from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text, new_token
from app.integrations import enable_banking
from app.models.entities import BankAccount, BankConnection, Bill, Creditor, OAuthState, Payment, Task
from app.services.audit import write_audit
from app.services.runtime_config import get_runtime_value

SUCCESS_STATUSES = {"ACSC", "ACCC", "BOOK"}
FAILED_STATUSES = {"RJCT", "CANC", "FAIL"}


async def start_bank_connection(
    db: AsyncSession, *, institution_country: str, institution_name: str, psu_type: str, redirect_url: str
) -> str:
    state = new_token(24)
    state_row = OAuthState(
        state=state,
        provider="enable_banking",
        payload_json=json.dumps(
            {
                "institution_country": institution_country.upper(),
                "institution_name": institution_name,
                "psu_type": psu_type,
            }
        ),
        expires_at=datetime.utcnow() + timedelta(minutes=30),
    )
    db.add(state_row)
    await db.commit()
    response = await enable_banking.start_account_authorization(
        db,
        institution_country=institution_country,
        institution_name=institution_name,
        psu_type=psu_type,
        state=state,
        redirect_url=redirect_url,
    )
    resolved_name = str(response.pop("_resolved_aspsp_name", institution_name))
    state_row.payload_json = json.dumps(
        {
            "institution_country": institution_country.upper(),
            "institution_name": resolved_name,
            "psu_type": psu_type,
        }
    )
    await db.commit()
    url = str(response.get("url") or "").strip()
    if not url:
        raise enable_banking.EnableBankingConfigurationError(
            "Enable Banking did not return an authorization URL for this institution"
        )
    return url


async def complete_bank_connection(db: AsyncSession, *, code: str, state: str) -> BankConnection:
    state_row = await db.get(OAuthState, state)
    if state_row is None or state_row.provider != "enable_banking" or state_row.expires_at < datetime.utcnow():
        raise ValueError("Bank authorization state is invalid or expired")
    context = json.loads(state_row.payload_json)
    session = await enable_banking.complete_account_authorization(db, code)
    session_id = session.get("session_id") or session.get("id")
    if not session_id:
        raise RuntimeError("Enable Banking did not return a session ID")
    access = session.get("access") if isinstance(session.get("access"), dict) else {}
    valid_until_value = access.get("valid_until") or session.get("valid_until")
    connection = BankConnection(
        institution_country=context["institution_country"],
        institution_name=context["institution_name"],
        psu_type=context["psu_type"],
        session_id_encrypted=encrypt_text(session_id),
        valid_until=datetime.fromisoformat(str(valid_until_value).replace("Z", "+00:00")).replace(tzinfo=None)
        if valid_until_value
        else None,
    )
    db.add(connection)
    await db.delete(state_row)
    await db.flush()
    await sync_bank_connection(db, connection)
    await write_audit(
        db,
        "bank_connected",
        entity_type="bank_connection",
        entity_id=str(connection.id),
        details={"institution": connection.institution_name, "psu_type": connection.psu_type},
    )
    await db.commit()
    return connection


def _extract_balance(payload: dict[str, Any], preferred: tuple[str, ...]) -> Decimal | None:
    candidates = payload.get("balances") or payload.get("balance") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    for kind in preferred:
        for item in candidates:
            balance_type = str(item.get("balance_type") or item.get("type") or "").lower()
            if kind in balance_type:
                amount = item.get("balance_amount") or item.get("amount") or {}
                value = amount.get("amount") if isinstance(amount, dict) else amount
                try:
                    return Decimal(str(value))
                except Exception:
                    continue
    return None


async def sync_bank_connection(db: AsyncSession, connection: BankConnection) -> int:
    session_id = decrypt_text(connection.session_id_encrypted)
    session = await enable_banking.get_session(db, session_id)
    account_ids = session.get("accounts") if isinstance(session, dict) else []
    if not isinstance(account_ids, list):
        account_ids = []
    count = 0
    for raw_account_id in account_ids:
        external_id = str(raw_account_id or "").strip()
        if not external_id:
            continue
        item = await enable_banking.get_account_details(db, external_id)
        if not isinstance(item, dict):
            continue

        identification = item.get("account_id") or {}
        iban = ""
        if isinstance(identification, dict):
            iban = str(identification.get("iban") or identification.get("identification") or "")
        iban = iban.replace(" ", "")

        existing = (
            await db.execute(select(BankAccount).where(BankAccount.external_account_id == external_id))
        ).scalar_one_or_none()
        if existing is None and iban:
            existing = (
                await db.execute(select(BankAccount).where(BankAccount.iban == iban))
            ).scalar_one_or_none()

        account = existing or BankAccount(
            bank_connection_id=connection.id,
            external_account_id=external_id,
        )
        account.bank_connection_id = connection.id
        account.external_account_id = external_id
        account.name = str(item.get("name") or item.get("product") or item.get("details") or connection.institution_name)
        account.iban = iban
        account.currency = str(item.get("currency") or "EUR")
        account.account_scope = "pro" if connection.psu_type.lower() in {"business", "corporate"} else "personal"
        if existing is None:
            db.add(account)
        await db.flush()
        balances = await enable_banking.get_account_balances(db, external_id)
        account.current_balance = _extract_balance(balances, ("closing", "interim", "current", "clav", "clbd"))
        account.available_balance = _extract_balance(balances, ("available", "interim", "current", "itav", "xpcd"))
        account.last_synced_at = datetime.utcnow()
        count += 1
    await db.commit()
    return count


async def sync_all_banks(db: AsyncSession) -> int:
    result = await db.execute(select(BankConnection).where(BankConnection.status == "active"))
    total = 0
    for connection in result.scalars():
        total += await sync_bank_connection(db, connection)
    return total


async def create_payment_for_bill(db: AsyncSession, *, bill_id: int, bank_account_id: int, redirect_url: str) -> Payment:
    bill = await db.get(Bill, bill_id)
    account = await db.get(BankAccount, bank_account_id)
    if bill is None or account is None:
        raise ValueError("Bill or bank account does not exist")
    if not account.enabled_for_payments:
        raise ValueError("This bank account has not been approved for payment execution")
    if not bill.iban:
        raise ValueError("Bill has no IBAN")
    creditor = (
        await db.execute(select(Creditor).where(Creditor.iban == bill.iban))
    ).scalar_one_or_none()
    if creditor is None or not creditor.auto_pay_enabled:
        raise ValueError("Creditor is not approved for automatic payment")
    if bill.amount > creditor.max_auto_amount:
        raise ValueError("Bill exceeds the approved creditor limit")
    if bill.account_scope != creditor.account_scope:
        raise ValueError("Bill scope does not match the approved creditor policy")
    if account.account_scope != bill.account_scope:
        raise ValueError("Selected account does not match the bill scope")
    available = account.available_balance if account.available_balance is not None else account.current_balance
    if available is None:
        raise ValueError("Bank balance is not available")
    if available - bill.amount < account.safety_reserve:
        raise ValueError("Payment would breach the account safety reserve")
    duplicate = (
        await db.execute(
            select(Payment).where(
                Payment.bill_id == bill.id,
                Payment.status.not_in(["failed", "cancelled", "rejected"]),
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise ValueError("A payment already exists for this bill")
    connection = await db.get(BankConnection, account.bank_connection_id)
    if connection is None:
        raise ValueError("Bank connection is missing")

    state = new_token(24)
    state_row = OAuthState(
        state=state,
        provider="enable_banking_payment",
        payload_json=json.dumps({"bill_id": bill.id, "bank_account_id": account.id}),
        expires_at=datetime.utcnow() + timedelta(days=2),
    )
    db.add(state_row)
    await db.commit()
    try:
        response = await enable_banking.create_sepa_payment(
            db,
            institution_country=connection.institution_country,
            institution_name=connection.institution_name,
            psu_type=connection.psu_type,
            creditor_name=bill.creditor_name,
            creditor_iban=bill.iban,
            amount=f"{bill.amount:.2f}",
            currency=bill.currency,
            reference=bill.reference or bill.invoice_number,
            state=state,
            redirect_url=redirect_url,
        )
    except Exception:
        await db.delete(state_row)
        await db.commit()
        raise
    external_id = response.get("payment_id")
    payment = Payment(
        bill_id=bill.id,
        bank_account_id=account.id,
        external_payment_id=external_id,
        amount=bill.amount,
        currency=bill.currency,
        status=str(response.get("status") or "received").lower(),
        authorization_url=response.get("url"),
        requires_user_action=bool(response.get("url")),
    )
    db.add(payment)
    bill.status = "payment_initiated"
    await db.flush()
    state_row.payload_json = json.dumps(
        {
            "bill_id": bill.id,
            "bank_account_id": account.id,
            "payment_id": payment.id,
            "external_payment_id": external_id,
        }
    )
    await write_audit(
        db,
        "payment_initiated",
        entity_type="payment",
        entity_id=str(payment.id),
        details={"bill_id": bill.id, "external_payment_id": external_id},
    )
    await db.commit()
    await db.refresh(payment)
    return payment


async def refresh_payment(db: AsyncSession, payment: Payment) -> Payment:
    if not payment.external_payment_id:
        return payment
    response = await enable_banking.get_payment(db, payment.external_payment_id)
    status = str(response.get("status") or payment.status).upper()
    if status in SUCCESS_STATUSES:
        payment.status = "completed"
        payment.requires_user_action = False
        bill = await db.get(Bill, payment.bill_id)
        if bill:
            bill.status = "paid"
    elif status in FAILED_STATUSES:
        payment.status = "failed"
        payment.requires_user_action = False
        payment.failure_reason = str(response.get("status_reason_information") or response.get("reason") or status)
    else:
        payment.status = status.lower()
    await db.commit()
    return payment


async def refresh_all_payments(db: AsyncSession) -> int:
    result = await db.execute(
        select(Payment).where(Payment.external_payment_id.is_not(None), Payment.status != "completed")
    )
    count = 0
    for payment in result.scalars():
        await refresh_payment(db, payment)
        count += 1
    return count


async def complete_payment_authorization(
    db: AsyncSession,
    *,
    state: str,
    error: str | None = None,
    error_description: str | None = None,
) -> Payment | None:
    state_row = await db.get(OAuthState, state)
    if state_row is None or state_row.provider != "enable_banking_payment":
        raise ValueError("Payment authorization state is invalid or expired")
    context = json.loads(state_row.payload_json or "{}")
    payment = await db.get(Payment, context.get("payment_id")) if context.get("payment_id") else None
    if payment is not None:
        if error:
            payment.status = "cancelled" if error == "access_denied" else "failed"
            payment.requires_user_action = False
            payment.failure_reason = error_description or error
            await write_audit(
                db,
                "payment_authorization_failed",
                entity_type="payment",
                entity_id=str(payment.id),
                result="failed",
                details={"error": error, "description": error_description or ""},
            )
        else:
            await refresh_payment(db, payment)
            payment.requires_user_action = payment.status not in {"completed", "failed", "cancelled", "rejected"}
            await write_audit(
                db,
                "payment_authorization_returned",
                entity_type="payment",
                entity_id=str(payment.id),
                details={"status": payment.status},
            )
    await db.delete(state_row)
    await db.commit()
    return payment


async def auto_pay_eligible_bills(db: AsyncSession, *, redirect_url: str) -> dict[str, int]:
    enabled = (await get_runtime_value(db, "auto_pay_enabled", "true")).lower() == "true"
    if not enabled:
        return {"initiated": 0, "skipped": 0, "failed": 0}
    try:
        days_before_due = max(0, min(int(await get_runtime_value(db, "auto_pay_days_before_due", "3")), 60))
    except ValueError:
        days_before_due = 3
    cutoff = datetime.utcnow() + timedelta(days=days_before_due)
    bills = list(
        (
            await db.execute(
                select(Bill).where(
                    Bill.status == "validated",
                    (Bill.due_at.is_(None)) | (Bill.due_at <= cutoff),
                ).order_by(Bill.due_at.asc().nullsfirst(), Bill.id)
            )
        ).scalars()
    )
    result = {"initiated": 0, "skipped": 0, "failed": 0}
    for bill in bills:
        existing = (
            await db.execute(
                select(Payment).where(
                    Payment.bill_id == bill.id,
                    Payment.status.not_in(["failed", "cancelled", "rejected"]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            result["skipped"] += 1
            continue
        creditor = await db.get(Creditor, bill.creditor_id) if bill.creditor_id else None
        if creditor is None or not creditor.auto_pay_enabled or creditor.iban != bill.iban:
            result["skipped"] += 1
            continue
        accounts = list(
            (
                await db.execute(
                    select(BankAccount).where(
                        BankAccount.enabled_for_payments.is_(True),
                        BankAccount.account_scope == bill.account_scope,
                        BankAccount.currency == bill.currency,
                    ).order_by(BankAccount.available_balance.desc().nullslast(), BankAccount.current_balance.desc().nullslast())
                )
            ).scalars()
        )
        selected = None
        for account in accounts:
            available = account.available_balance if account.available_balance is not None else account.current_balance
            if available is not None and available - bill.amount >= account.safety_reserve:
                selected = account
                break
        if selected is None:
            result["failed"] += 1
            existing_task = (
                await db.execute(
                    select(Task).where(
                        Task.source_type == "bill_payment",
                        Task.source_id == str(bill.id),
                        Task.status.in_(["open", "waiting"]),
                    )
                )
            ).scalar_one_or_none()
            if existing_task is None:
                db.add(
                    Task(
                        title=f"Funding required for {bill.creditor_name}",
                        description=f"No approved {bill.account_scope} account can pay {bill.amount} {bill.currency} while preserving its safety reserve.",
                        source_type="bill_payment",
                        source_id=str(bill.id),
                        priority="high",
                        requires_approval=False,
                    )
                )
            await write_audit(
                db,
                "automatic_payment_blocked",
                entity_type="bill",
                entity_id=str(bill.id),
                result="blocked",
                details={"reason": "No eligible funded account"},
            )
            await db.commit()
            continue
        try:
            await create_payment_for_bill(
                db,
                bill_id=bill.id,
                bank_account_id=selected.id,
                redirect_url=redirect_url,
            )
            result["initiated"] += 1
        except Exception as exc:
            result["failed"] += 1
            await write_audit(
                db,
                "automatic_payment_failed",
                entity_type="bill",
                entity_id=str(bill.id),
                result="failed",
                details={"error": str(exc)},
            )
            await db.commit()
    return result
