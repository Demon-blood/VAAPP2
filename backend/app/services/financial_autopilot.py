from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import new_token
from app.integrations import enable_banking
from app.models.entities import (
    BankAccount,
    BankAutopilotPolicy,
    BankConnection,
    BankTransaction,
    Bill,
    BudgetEnvelope,
    OAuthState,
    OwnAccountTransfer,
    Task,
)
from app.services.audit import write_audit
from app.services.cash_safety import committed_destination_balance, effective_available_balance
from app.services.runtime_config import get_runtime_value

TRANSFER_SUCCESS = {"ACSC", "ACCC", "BOOK"}
TRANSFER_FAILED = {"RJCT", "CANC", "CNCL", "FAIL"}
ACTIVE_TRANSFER_STATUSES = {"creating", "received", "pending", "authorization_required", "creation_uncertain", "acsp", "actc", "acpt"}

CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "housing": ("rent", "huur", "mortgage", "hypotheek", "syndic", "syndicus"),
    "groceries": ("delhaize", "colruyt", "carrefour", "aldi", "lidl", "okay", "supermarket", "supermarkt", "grocery"),
    "utilities": ("engie", "luminus", "water", "electric", "elektr", "gas", "proximus", "orange", "telenet", "telecom"),
    "transport": ("nmbs", "sncb", "de lijn", "mivb", "stib", "uber", "bolt", "fuel", "benzine", "diesel", "parking"),
    "subscriptions": ("subscription", "abonnement", "netflix", "spotify", "youtube", "google play", "apple.com/bill", "adobe"),
    "dining": ("restaurant", "cafe", "café", "bar ", "takeaway", "deliveroo", "ubereats", "uber eats"),
    "health": ("pharmacy", "apotheek", "doctor", "arts", "hospital", "ziekenhuis", "mutualiteit"),
    "insurance": ("insurance", "verzekering", "assurance"),
    "tax": ("tax", "belasting", "fiscus", "finance.belgium", "fod financ"),
    "shopping": ("amazon", "bol.com", "zalando", "ikea", "mediamarkt", "shopping", "winkel"),
}
DEFAULT_BUDGET_CATEGORIES = tuple(CATEGORY_TERMS) + ("other",)


def _money(value: Any, default: Decimal = Decimal("0.00")) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _date_value(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _iban(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _counterparty(item: dict[str, Any]) -> tuple[str, str]:
    """Return the *other side* of a transaction, never the connected account itself."""
    indicator = str(item.get("credit_debit_indicator") or "DBIT").upper()
    creditor = item.get("creditor") if isinstance(item.get("creditor"), dict) else {}
    debtor = item.get("debtor") if isinstance(item.get("debtor"), dict) else {}
    creditor_account = item.get("creditor_account") if isinstance(item.get("creditor_account"), dict) else {}
    debtor_account = item.get("debtor_account") if isinstance(item.get("debtor_account"), dict) else {}
    if indicator.startswith("CR"):
        party = debtor
        account = debtor_account
    else:
        party = creditor
        account = creditor_account
    return (
        str(party.get("name") or "")[:255],
        _iban(account.get("iban") or account.get("identification"))[:34],
    )


def categorize_transaction(item: dict[str, Any], own_ibans: set[str]) -> tuple[str, bool]:
    counterparty_name, counterparty_iban = _counterparty(item)
    if counterparty_iban and counterparty_iban in own_ibans:
        return "internal_transfer", True
    creditor = item.get("creditor") if isinstance(item.get("creditor"), dict) else {}
    debtor = item.get("debtor") if isinstance(item.get("debtor"), dict) else {}
    text = " ".join(
        [
            counterparty_name,
            str(creditor.get("name") or ""),
            str(debtor.get("name") or ""),
            " ".join(str(value) for value in (item.get("remittance_information") or []) if value),
            str(item.get("entry_reference") or ""),
            str((item.get("bank_transaction_code") or {}).get("description") if isinstance(item.get("bank_transaction_code"), dict) else ""),
        ]
    ).casefold()
    for category, terms in CATEGORY_TERMS.items():
        if any(term in text for term in terms):
            return category, False
    return "other", False


def _transaction_identity(account_id: int, item: dict[str, Any]) -> str:
    explicit = str(item.get("transaction_id") or "").strip()
    if explicit:
        return explicit[:255]
    material = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(f"{account_id}:{material}".encode()).hexdigest()


async def sync_bank_transactions(db: AsyncSession, *, lookback_days: int = 90) -> dict[str, int]:
    accounts = list((await db.execute(select(BankAccount))).scalars())
    own_ibans = {_iban(account.iban) for account in accounts if account.iban}
    created = 0
    updated = 0
    pages = 0
    for account in accounts:
        latest = (
            await db.execute(
                select(func.max(BankTransaction.booking_date)).where(BankTransaction.bank_account_id == account.id)
            )
        ).scalar_one_or_none()
        start = (latest - timedelta(days=7) if latest else datetime.utcnow() - timedelta(days=lookback_days)).date()
        continuation: str | None = None
        for _ in range(20):
            payload = await enable_banking.get_account_transactions(
                db,
                account.external_account_id,
                start.isoformat(),
                continuation_key=continuation,
            )
            pages += 1
            values = payload.get("transactions") if isinstance(payload, dict) else []
            if not isinstance(values, list):
                values = []
            for item in values:
                if not isinstance(item, dict):
                    continue
                provider_id = _transaction_identity(account.id, item)
                row = (
                    await db.execute(
                        select(BankTransaction).where(
                            BankTransaction.bank_account_id == account.id,
                            BankTransaction.provider_transaction_id == provider_id,
                        )
                    )
                ).scalar_one_or_none()
                new_row = row is None
                if row is None:
                    row = BankTransaction(bank_account_id=account.id, provider_transaction_id=provider_id, amount=Decimal("0.00"))
                    db.add(row)
                amount_node = item.get("transaction_amount") or item.get("instructed_amount") or {}
                raw_amount = amount_node.get("amount") if isinstance(amount_node, dict) else amount_node
                row.amount = abs(_money(raw_amount))
                row.currency = str(amount_node.get("currency") if isinstance(amount_node, dict) else account.currency or "EUR")[:3].upper()
                indicator = str(item.get("credit_debit_indicator") or "DBIT").upper()
                row.direction = "credit" if indicator.startswith("CR") else "debit"
                row.booking_date = _date_value(item.get("booking_date") or item.get("booking_date_time"))
                row.value_date = _date_value(item.get("value_date") or item.get("value_date_time"))
                row.counterparty_name, row.counterparty_iban = _counterparty(item)
                remittance = item.get("remittance_information") or []
                if isinstance(remittance, str):
                    remittance = [remittance]
                row.remittance = " | ".join(str(value) for value in remittance if value)[:4000]
                row.category, row.is_internal_transfer = categorize_transaction(item, own_ibans)
                row.raw_json = json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))[:30000]
                if new_row:
                    created += 1
                else:
                    updated += 1
            continuation = str(payload.get("continuation_key") or "").strip() or None
            if not continuation:
                break
        await db.commit()
    return {"created": created, "updated": updated, "pages": pages}


async def ensure_default_budget_envelopes(db: AsyncSession, account_scope: str = "personal") -> None:
    existing = {
        row.category
        for row in (
            await db.execute(select(BudgetEnvelope).where(BudgetEnvelope.account_scope == account_scope))
        ).scalars()
    }
    for category in DEFAULT_BUDGET_CATEGORIES:
        if category not in existing:
            db.add(BudgetEnvelope(account_scope=account_scope, category=category))
    await db.commit()


def _derived_role(account: BankAccount) -> str:
    name = f"{account.name} {account.account_scope}".casefold()
    if any(term in name for term in ("saving", "savings", "spaar", "vault", "reserve")):
        return "savings"
    if any(term in name for term in ("tax", "belasting", "btw", "vat")):
        return "tax"
    return "operating"


async def ensure_account_autopilot_policies(db: AsyncSession) -> list[BankAutopilotPolicy]:
    accounts = list((await db.execute(select(BankAccount))).scalars())
    by_account = {
        row.bank_account_id: row
        for row in (await db.execute(select(BankAutopilotPolicy))).scalars()
    }
    changed = False
    for account in accounts:
        if account.id in by_account:
            continue
        role = _derived_role(account)
        policy = BankAutopilotPolicy(
            bank_account_id=account.id,
            role=role,
            internal_transfers_enabled=bool(account.enabled_for_payments and role == "operating"),
            target_floor=max(_money(account.safety_reserve), Decimal("0.00")),
            target_ceiling=Decimal("0.00"),
            accept_surplus=role in {"savings", "reserve", "tax"},
            monthly_outbound_limit=Decimal("5000.00"),
            min_transfer_amount=Decimal("50.00"),
        )
        db.add(policy)
        by_account[account.id] = policy
        changed = True
    if changed:
        await db.commit()
        return await ensure_account_autopilot_policies(db)
    return list(by_account.values())


async def monthly_spend_by_scope(db: AsyncSession, *, days: int = 90) -> dict[str, Decimal]:
    since = datetime.utcnow() - timedelta(days=days)
    rows = list(
        (
            await db.execute(
                select(BankTransaction, BankAccount)
                .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
                .where(
                    BankTransaction.booking_date >= since,
                    BankTransaction.direction == "debit",
                    BankTransaction.is_internal_transfer.is_(False),
                )
            )
        ).all()
    )
    totals: dict[str, Decimal] = {}
    for tx, account in rows:
        totals[account.account_scope] = totals.get(account.account_scope, Decimal("0.00")) + _money(tx.amount)
    multiplier = Decimal("30") / Decimal(str(max(1, days)))
    return {scope: (value * multiplier).quantize(Decimal("0.01")) for scope, value in totals.items()}


async def upcoming_bill_totals(db: AsyncSession, *, days: int = 30) -> dict[str, Decimal]:
    cutoff = datetime.utcnow() + timedelta(days=days)
    rows = list(
        (
            await db.execute(
                select(Bill).where(
                    Bill.status.not_in(["paid", "cancelled", "reclassified_nonpayable"]),
                    (Bill.due_at.is_(None)) | (Bill.due_at <= cutoff),
                )
            )
        ).scalars()
    )
    totals: dict[str, Decimal] = {}
    for bill in rows:
        totals[bill.account_scope] = totals.get(bill.account_scope, Decimal("0.00")) + _money(bill.amount)
    return totals


async def current_month_income_by_scope(db: AsyncSession) -> dict[str, Decimal]:
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = list(
        (
            await db.execute(
                select(BankAccount.account_scope, func.coalesce(func.sum(BankTransaction.amount), 0))
                .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
                .where(
                    BankTransaction.booking_date >= month_start,
                    BankTransaction.direction == "credit",
                    BankTransaction.is_internal_transfer.is_(False),
                )
                .group_by(BankAccount.account_scope)
            )
        ).all()
    )
    return {str(scope): _money(value) for scope, value in rows}


async def budget_cash_plan_by_scope(
    db: AsyncSession,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal]]:
    """Return monthly budget plan, reserve targets, and income-allocation percentages.

    Explicit monthly limits win. Categories without a configured limit learn a
    conservative 105% monthly allowance from the last 90 days. Reserve targets are
    treated as a separate cash-safety floor. Income allocation percentages are used
    for destination funding (for example, reserving 15% of income for tax).
    """
    await ensure_default_budget_envelopes(db, "personal")
    await ensure_default_budget_envelopes(db, "pro")
    envelopes = list(
        (await db.execute(select(BudgetEnvelope).where(BudgetEnvelope.enabled.is_(True)))).scalars()
    )
    since = datetime.utcnow() - timedelta(days=90)
    history_rows = list(
        (
            await db.execute(
                select(
                    BankAccount.account_scope,
                    BankTransaction.category,
                    func.coalesce(func.sum(BankTransaction.amount), 0),
                )
                .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
                .where(
                    BankTransaction.booking_date >= since,
                    BankTransaction.direction == "debit",
                    BankTransaction.is_internal_transfer.is_(False),
                )
                .group_by(BankAccount.account_scope, BankTransaction.category)
            )
        ).all()
    )
    history = {(str(scope), str(category)): _money(value) for scope, category, value in history_rows}
    monthly_plan: dict[str, Decimal] = {}
    reserve_targets: dict[str, Decimal] = {}
    allocation_percent: dict[str, Decimal] = {}
    for envelope in envelopes:
        configured = _money(envelope.monthly_limit)
        if configured > 0:
            effective = configured
        else:
            effective = (
                history.get((envelope.account_scope, envelope.category), Decimal("0.00"))
                / Decimal("3")
                * Decimal("1.05")
            ).quantize(Decimal("0.01"))
        monthly_plan[envelope.account_scope] = monthly_plan.get(envelope.account_scope, Decimal("0.00")) + effective
        reserve_targets[envelope.account_scope] = (
            reserve_targets.get(envelope.account_scope, Decimal("0.00")) + _money(envelope.reserve_target)
        )
        if envelope.category == "tax":
            allocation_percent[envelope.account_scope] = min(
                Decimal("100.00"),
                allocation_percent.get(envelope.account_scope, Decimal("0.00"))
                + _money(envelope.income_allocation_percent),
            )
    return monthly_plan, reserve_targets, allocation_percent


async def _outbound_transfer_total(db: AsyncSession, source_id: int, since: datetime) -> Decimal:
    value = (
        await db.execute(
            select(func.coalesce(func.sum(OwnAccountTransfer.amount), 0)).where(
                OwnAccountTransfer.source_account_id == source_id,
                OwnAccountTransfer.created_at >= since,
                OwnAccountTransfer.status.not_in(["failed", "cancelled", "rejected"]),
            )
        )
    ).scalar_one()
    return _money(value)


async def _active_transfer_exists(db: AsyncSession, source_id: int) -> bool:
    count = (
        await db.execute(
            select(func.count()).select_from(OwnAccountTransfer).where(
                OwnAccountTransfer.source_account_id == source_id,
                OwnAccountTransfer.status.in_(ACTIVE_TRANSFER_STATUSES),
            )
        )
    ).scalar_one()
    return bool(count)


async def create_own_account_transfer(
    db: AsyncSession,
    *,
    source_account_id: int,
    destination_account_id: int,
    amount: Decimal,
    reason: str,
    redirect_url: str,
    idempotency_key: str,
) -> OwnAccountTransfer:
    existing = (
        await db.execute(select(OwnAccountTransfer).where(OwnAccountTransfer.idempotency_key == idempotency_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    # Serialize money movement per source account. Recheck idempotency after the
    # row lock because another worker may have created the intent while we waited.
    source = (
        await db.execute(select(BankAccount).where(BankAccount.id == source_account_id).with_for_update())
    ).scalar_one_or_none()
    existing = (
        await db.execute(select(OwnAccountTransfer).where(OwnAccountTransfer.idempotency_key == idempotency_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    destination = await db.get(BankAccount, destination_account_id)
    if source is None or destination is None:
        raise ValueError("Source or destination bank account does not exist")
    if source.id == destination.id:
        raise ValueError("Source and destination accounts must be different")
    if not source.iban or not destination.iban:
        raise ValueError("Both connected accounts must have an IBAN")
    if source.currency != destination.currency:
        raise ValueError("Automatic own-account transfers require matching currencies")
    if source.account_scope != destination.account_scope:
        raise ValueError("Automatic own-account transfers cannot cross personal/business scopes")
    source_policy = (
        await db.execute(select(BankAutopilotPolicy).where(BankAutopilotPolicy.bank_account_id == source.id))
    ).scalar_one_or_none()
    if source_policy is None or not source_policy.internal_transfers_enabled:
        raise ValueError("Source account is not enabled for automatic internal transfers")
    if not source.enabled_for_payments:
        raise ValueError("Source account is not approved for payment execution")
    if await _active_transfer_exists(db, source.id):
        raise ValueError("Another own-account transfer from this source is still active")
    amount = _money(amount)
    if amount <= 0:
        raise ValueError("Transfer amount must be positive")
    max_single = _money(await get_runtime_value(db, "finance_max_single_transfer", "1000"), Decimal("1000.00"))
    daily_limit = _money(
        await get_runtime_value(db, "finance_daily_internal_transfer_limit", "1000"),
        Decimal("1000.00"),
    )
    if amount > max_single:
        raise ValueError("Transfer exceeds the global per-transfer safety limit")
    today_start = datetime.combine(date.today(), datetime.min.time())
    month_start = today_start.replace(day=1)
    day_used = await _outbound_transfer_total(db, source.id, today_start)
    month_used = await _outbound_transfer_total(db, source.id, month_start)
    if day_used + amount > daily_limit:
        raise ValueError("Transfer would exceed the daily own-account transfer limit")
    if month_used + amount > _money(source_policy.monthly_outbound_limit):
        raise ValueError("Transfer would exceed the account monthly transfer limit")
    minimum_operating_floor = _money(
        await get_runtime_value(db, "finance_min_operating_cash_floor", "1000"),
        Decimal("1000.00"),
    )
    available = await effective_available_balance(db, source)
    required_floor = max(
        _money(source.safety_reserve),
        _money(source_policy.target_floor),
        minimum_operating_floor,
    )
    if available is None or _money(available) - amount < required_floor:
        raise ValueError("Transfer would breach the source account reserve")
    connection = await db.get(BankConnection, source.bank_connection_id)
    if connection is None:
        raise ValueError("Source bank connection is missing")

    transfer = OwnAccountTransfer(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=amount,
        currency=source.currency,
        reason=reason[:2000],
        idempotency_key=idempotency_key,
        status="creating",
    )
    db.add(transfer)
    await db.flush()
    state = new_token(24)
    state_row = OAuthState(
        state=state,
        provider="enable_banking_internal_transfer",
        payload_json=json.dumps({"transfer_id": transfer.id}),
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
            creditor_name=destination.name or "Own account",
            creditor_iban=destination.iban,
            amount=f"{amount:.2f}",
            currency=source.currency,
            reference=f"Full-Time VA budget transfer {date.today().isoformat()}",
            state=state,
            redirect_url=redirect_url,
            debtor_iban=source.iban,
        )
    except enable_banking.EnableBankingConfigurationError as exc:
        transfer.status = "failed"
        transfer.failure_reason = str(exc)[:2000]
        await db.delete(state_row)
        await write_audit(
            db,
            "own_account_transfer_creation_failed",
            entity_type="own_account_transfer",
            entity_id=str(transfer.id),
            result="failed",
            details={"error": str(exc)},
        )
        await db.commit()
        return transfer
    except (httpx.RequestError, TimeoutError) as exc:
        # The request may have reached the provider. Never retry creation blindly.
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = f"Provider creation outcome is uncertain; automatic retry blocked: {exc}"[:2000]
        db.add(
            Task(
                title="Check bank before retrying own-account transfer",
                description=transfer.failure_reason,
                source_type="bank_transfer_uncertain",
                source_id=str(transfer.id),
                priority="urgent",
                requires_approval=True,
            )
        )
        await write_audit(
            db,
            "own_account_transfer_creation_uncertain",
            entity_type="own_account_transfer",
            entity_id=str(transfer.id),
            result="blocked",
            details={"error": str(exc), "retry_suppressed": True},
        )
        await db.commit()
        return transfer

    transfer.external_payment_id = str(response.get("payment_id") or response.get("id") or "").strip() or None
    transfer.authorization_url = str(response.get("url") or "").strip() or None
    transfer.requires_user_action = bool(transfer.authorization_url)
    if transfer.external_payment_id is None:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = "Provider returned success without a payment identifier; automatic retry is blocked."
        db.add(
            Task(
                title="Check bank before retrying own-account transfer",
                description=transfer.failure_reason,
                source_type="bank_transfer_uncertain",
                source_id=str(transfer.id),
                priority="urgent",
                requires_approval=True,
            )
        )
    else:
        transfer.status = "authorization_required" if transfer.requires_user_action else str(response.get("status") or "received").lower()
    state_row.payload_json = json.dumps({"transfer_id": transfer.id, "external_payment_id": transfer.external_payment_id})
    if transfer.authorization_url:
        db.add(
            Task(
                title=f"Authorize transfer to {destination.name or destination.iban}",
                description=f"The VA planned {amount:.2f} {source.currency} from {source.name} to {destination.name}. Bank SCA is required: {transfer.authorization_url}",
                source_type="bank_transfer_authorization",
                source_id=str(transfer.id),
                priority="high",
                requires_approval=True,
            )
        )
    await write_audit(
        db,
        "own_account_transfer_initiated",
        entity_type="own_account_transfer",
        entity_id=str(transfer.id),
        details={
            "source_account_id": source.id,
            "destination_account_id": destination.id,
            "amount": str(amount),
            "requires_user_action": transfer.requires_user_action,
        },
    )
    await db.commit()
    return transfer


async def refresh_own_account_transfer(db: AsyncSession, transfer: OwnAccountTransfer) -> OwnAccountTransfer:
    if not transfer.external_payment_id or transfer.status == "creation_uncertain":
        return transfer
    response = await enable_banking.get_payment(db, transfer.external_payment_id)
    status = str(response.get("status") or transfer.status).upper()
    if status in TRANSFER_SUCCESS:
        transfer.status = "completed"
        transfer.requires_user_action = False
    elif status in TRANSFER_FAILED:
        transfer.status = "failed"
        transfer.requires_user_action = False
        transfer.failure_reason = str(response.get("status_reason_information") or response.get("reason") or status)[:2000]
    else:
        transfer.status = status.lower()
    if transfer.status == "completed":
        task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bank_transfer_authorization",
                    Task.source_id == str(transfer.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if task is not None:
            task.status = "completed"
    await db.commit()
    return transfer


async def refresh_all_own_account_transfers(db: AsyncSession) -> int:
    transfers = list(
        (
            await db.execute(
                select(OwnAccountTransfer).where(
                    OwnAccountTransfer.external_payment_id.is_not(None),
                    OwnAccountTransfer.status.not_in(["completed", "failed", "cancelled", "rejected"]),
                )
            )
        ).scalars()
    )
    for transfer in transfers:
        await refresh_own_account_transfer(db, transfer)
    return len(transfers)


async def complete_own_transfer_authorization(
    db: AsyncSession,
    *,
    state: str,
    error: str | None = None,
    error_description: str | None = None,
) -> OwnAccountTransfer | None:
    state_row = await db.get(OAuthState, state)
    if state_row is None or state_row.provider != "enable_banking_internal_transfer" or state_row.expires_at < datetime.utcnow():
        raise ValueError("Transfer authorization state is invalid or expired")
    context = json.loads(state_row.payload_json or "{}")
    transfer = await db.get(OwnAccountTransfer, int(context.get("transfer_id") or 0))
    if transfer is not None:
        if error:
            transfer.status = "cancelled" if error == "access_denied" else "failed"
            transfer.requires_user_action = False
            transfer.failure_reason = (error_description or error)[:2000]
        else:
            await refresh_own_account_transfer(db, transfer)
            transfer.authorization_url = None
            transfer.requires_user_action = False
        authorization_task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bank_transfer_authorization",
                    Task.source_id == str(transfer.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if authorization_task is not None:
            authorization_task.status = "completed"
    await db.delete(state_row)
    await db.commit()
    return transfer


async def run_budget_autopilot(db: AsyncSession, *, redirect_url: str) -> dict[str, Any]:
    await ensure_default_budget_envelopes(db, "personal")
    await ensure_default_budget_envelopes(db, "pro")
    policies = await ensure_account_autopilot_policies(db)
    if (await get_runtime_value(db, "finance_auto_budget_enabled", "true")).lower() != "true":
        return {"enabled": False, "planned": 0, "initiated": 0, "blocked": 0}
    if (await get_runtime_value(db, "finance_auto_transfer_enabled", "true")).lower() != "true":
        return {"enabled": True, "transfers_enabled": False, "planned": 0, "initiated": 0, "blocked": 0}

    accounts = {account.id: account for account in (await db.execute(select(BankAccount))).scalars()}
    policy_by_account = {policy.bank_account_id: policy for policy in policies}
    spend = await monthly_spend_by_scope(db)
    obligations = await upcoming_bill_totals(db)
    monthly_budget, reserve_targets, allocation_percent = await budget_cash_plan_by_scope(db)
    month_income = await current_month_income_by_scope(db)
    try:
        buffer_multiplier = Decimal(await get_runtime_value(db, "finance_cash_buffer_multiplier", "1.10"))
    except InvalidOperation:
        buffer_multiplier = Decimal("1.10")
    max_single = _money(await get_runtime_value(db, "finance_max_single_transfer", "1000"), Decimal("1000.00"))
    daily_limit = _money(await get_runtime_value(db, "finance_daily_internal_transfer_limit", "1000"), Decimal("1000.00"))
    minimum_operating_floor = _money(
        await get_runtime_value(db, "finance_min_operating_cash_floor", "1000"),
        Decimal("1000.00"),
    )

    destination_candidates = [
        accounts[policy.bank_account_id]
        for policy in policies
        if policy.accept_surplus
        and policy.role in {"savings", "reserve", "tax"}
        and policy.bank_account_id in accounts
        and accounts[policy.bank_account_id].iban
    ]
    outcome: dict[str, Any] = {"enabled": True, "transfers_enabled": True, "planned": 0, "initiated": 0, "blocked": 0, "details": []}
    today_start = datetime.combine(date.today(), datetime.min.time())
    month_start = today_start.replace(day=1)
    tax_account_ids_by_scope: dict[str, set[int]] = {}
    for candidate_policy in policies:
        if candidate_policy.role != "tax" or candidate_policy.bank_account_id not in accounts:
            continue
        account = accounts[candidate_policy.bank_account_id]
        tax_account_ids_by_scope.setdefault(account.account_scope, set()).add(account.id)
    tax_gap_remaining: dict[str, Decimal] = {}
    for scope, account_ids in tax_account_ids_by_scope.items():
        required = (
            _money(month_income.get(scope, Decimal("0.00")))
            * _money(allocation_percent.get(scope, Decimal("0.00")))
            / Decimal("100")
        ).quantize(Decimal("0.01"))
        if required <= 0:
            tax_gap_remaining[scope] = Decimal("0.00")
            continue
        already_allocated = (
            await db.execute(
                select(func.coalesce(func.sum(OwnAccountTransfer.amount), 0)).where(
                    OwnAccountTransfer.destination_account_id.in_(account_ids),
                    OwnAccountTransfer.created_at >= month_start,
                    OwnAccountTransfer.status.not_in(["failed", "cancelled", "rejected"]),
                )
            )
        ).scalar_one()
        tax_gap_remaining[scope] = max(Decimal("0.00"), required - _money(already_allocated))

    for policy in sorted(policies, key=lambda row: row.id):
        source = accounts.get(policy.bank_account_id)
        if source is None or policy.role != "operating" or not policy.internal_transfers_enabled:
            continue
        if await _active_transfer_exists(db, source.id):
            outcome["blocked"] += 1
            outcome["details"].append({"source_account_id": source.id, "reason": "active_transfer_exists"})
            continue
        available = await effective_available_balance(db, source)
        if available is None:
            continue
        monthly_need = max(
            spend.get(source.account_scope, Decimal("0.00")),
            obligations.get(source.account_scope, Decimal("0.00")),
            monthly_budget.get(source.account_scope, Decimal("0.00")),
            reserve_targets.get(source.account_scope, Decimal("0.00")),
        )
        dynamic_floor = (_money(monthly_need) * buffer_multiplier).quantize(Decimal("0.01")) + _money(source.safety_reserve)
        retained = max(dynamic_floor, _money(policy.target_floor), _money(policy.target_ceiling), minimum_operating_floor)
        excess = _money(available) - retained
        if excess < _money(policy.min_transfer_amount):
            continue

        destinations = [
            account
            for account in destination_candidates
            if account.id != source.id and account.currency == source.currency and account.account_scope == source.account_scope
        ]
        if not destinations:
            continue
        destination_balances: dict[int, Decimal] = {}
        for candidate in destinations:
            committed = await committed_destination_balance(db, candidate)
            destination_balances[candidate.id] = _money(committed)

        def destination_rank(account: BankAccount) -> tuple[int, int]:
            dest_policy = policy_by_account[account.id]
            balance = destination_balances[account.id]
            explicit_gap = _money(dest_policy.target_floor) - balance
            tax_gap = tax_gap_remaining.get(account.account_scope, Decimal("0.00"))
            if dest_policy.role == "tax" and tax_gap > 0:
                return (0, account.id)
            if dest_policy.role in {"reserve", "tax"} and explicit_gap > 0:
                return (1, account.id)
            if dest_policy.role == "savings":
                return (2, account.id)
            return (3, account.id)

        destinations.sort(key=destination_rank)
        destination = destinations[0]
        dest_policy = policy_by_account[destination.id]
        destination_balance = destination_balances[destination.id]
        explicit_gap = _money(dest_policy.target_floor) - destination_balance
        tax_gap = tax_gap_remaining.get(destination.account_scope, Decimal("0.00")) if dest_policy.role == "tax" else Decimal("0.00")
        gap = max(explicit_gap, tax_gap)
        # Reserve/tax accounts only receive money for an explicit target or a
        # configured income-allocation gap. Ordinary surplus belongs in savings.
        if dest_policy.role in {"reserve", "tax"} and gap <= 0:
            savings = [account for account in destinations if policy_by_account[account.id].role == "savings"]
            if not savings:
                continue
            destination = savings[0]
            dest_policy = policy_by_account[destination.id]
            destination_balance = destination_balances[destination.id]
            gap = _money(dest_policy.target_floor) - destination_balance
            tax_gap = Decimal("0.00")
        desired = min(excess, max_single)
        if gap > 0:
            desired = min(desired, gap)
        desired = desired.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        month_used = await _outbound_transfer_total(db, source.id, month_start)
        day_used = await _outbound_transfer_total(db, source.id, today_start)
        remaining_month = max(Decimal("0.00"), _money(policy.monthly_outbound_limit) - month_used)
        remaining_day = max(Decimal("0.00"), daily_limit - day_used)
        desired = min(desired, remaining_month, remaining_day)
        if desired < _money(policy.min_transfer_amount):
            continue

        outcome["planned"] += 1
        key = f"budget:{date.today().isoformat()}:{source.id}:{destination.id}:{desired:.2f}"
        try:
            transfer = await create_own_account_transfer(
                db,
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=desired,
                reason=(
                    f"Budget rebalance: keep {retained:.2f} {source.currency} available; "
                    f"monthly spend forecast {spend.get(source.account_scope, Decimal('0.00')):.2f}; "
                    f"budget plan {monthly_budget.get(source.account_scope, Decimal('0.00')):.2f}; "
                    f"30-day obligations {obligations.get(source.account_scope, Decimal('0.00')):.2f}."
                ),
                redirect_url=redirect_url,
                idempotency_key=key,
            )
        except ValueError as exc:
            # A concurrent worker may have created an active transfer after this
            # planner's initial check, or an execution-boundary limit may have
            # tightened. Treat that as a blocked plan, never as a reason to retry
            # an irreversible payment request.
            outcome["blocked"] += 1
            outcome["details"].append(
                {
                    "source_account_id": source.id,
                    "destination_account_id": destination.id,
                    "amount": str(desired),
                    "status": "blocked",
                    "reason": str(exc),
                }
            )
            continue
        if transfer.status in {"failed", "creation_uncertain"}:
            outcome["blocked"] += 1
        else:
            outcome["initiated"] += 1
        if dest_policy.role == "tax" and transfer.status not in {"failed", "cancelled", "rejected"}:
            tax_gap_remaining[destination.account_scope] = max(
                Decimal("0.00"), tax_gap_remaining.get(destination.account_scope, Decimal("0.00")) - desired
            )
        outcome["details"].append(
            {
                "transfer_id": transfer.id,
                "source_account_id": source.id,
                "destination_account_id": destination.id,
                "amount": str(desired),
                "status": transfer.status,
                "requires_user_action": transfer.requires_user_action,
            }
        )
    return outcome


async def finance_overview(db: AsyncSession) -> dict[str, Any]:
    await ensure_default_budget_envelopes(db, "personal")
    await ensure_default_budget_envelopes(db, "pro")
    policies = await ensure_account_autopilot_policies(db)
    accounts = list((await db.execute(select(BankAccount).order_by(BankAccount.id))).scalars())
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = list(
        (
            await db.execute(
                select(BankTransaction, BankAccount)
                .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
                .where(BankTransaction.booking_date >= month_start, BankTransaction.is_internal_transfer.is_(False))
            )
        ).all()
    )
    spent: dict[tuple[str, str], Decimal] = {}
    income: dict[str, Decimal] = {}
    for tx, account in rows:
        if tx.direction == "debit":
            key = (account.account_scope, tx.category)
            spent[key] = spent.get(key, Decimal("0.00")) + _money(tx.amount)
        else:
            income[account.account_scope] = income.get(account.account_scope, Decimal("0.00")) + _money(tx.amount)

    history_spend = await monthly_spend_by_scope(db)
    envelopes = list((await db.execute(select(BudgetEnvelope).where(BudgetEnvelope.enabled.is_(True)))).scalars())
    envelope_rows = []
    for envelope in envelopes:
        actual = spent.get((envelope.account_scope, envelope.category), Decimal("0.00"))
        configured = _money(envelope.monthly_limit)
        suggested = configured
        if configured <= 0:
            category_90 = (
                await db.execute(
                    select(func.coalesce(func.sum(BankTransaction.amount), 0))
                    .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
                    .where(
                        BankAccount.account_scope == envelope.account_scope,
                        BankTransaction.category == envelope.category,
                        BankTransaction.direction == "debit",
                        BankTransaction.is_internal_transfer.is_(False),
                        BankTransaction.booking_date >= datetime.utcnow() - timedelta(days=90),
                    )
                )
            ).scalar_one()
            suggested = (_money(category_90) / Decimal("3") * Decimal("1.05")).quantize(Decimal("0.01"))
        limit = configured if configured > 0 else suggested
        envelope_rows.append(
            {
                "id": envelope.id,
                "scope": envelope.account_scope,
                "category": envelope.category,
                "spent": str(actual),
                "monthly_limit": str(configured),
                "effective_limit": str(limit),
                "remaining": str(max(Decimal("0.00"), limit - actual)),
                "overspent": bool(limit > 0 and actual > limit),
                "reserve_target": str(envelope.reserve_target),
                "income_allocation_percent": str(envelope.income_allocation_percent),
            }
        )

    effective_balances = [await effective_available_balance(db, account) for account in accounts]
    total_available = sum(
        (_money(balance) for balance in effective_balances if balance is not None),
        Decimal("0.00"),
    )
    obligations = await upcoming_bill_totals(db)
    pending_transfers = (
        await db.execute(
            select(func.count()).select_from(OwnAccountTransfer).where(
                OwnAccountTransfer.status.in_(ACTIVE_TRANSFER_STATUSES)
            )
        )
    ).scalar_one()
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_available": str(total_available),
        "currency": "EUR",
        "month_income": {scope: str(value) for scope, value in income.items()},
        "monthly_spend_forecast": {scope: str(value) for scope, value in history_spend.items()},
        "upcoming_30_day_obligations": {scope: str(value) for scope, value in obligations.items()},
        "pending_internal_transfers": int(pending_transfers),
        "envelopes": envelope_rows,
        "account_policies": [
            {
                "id": policy.id,
                "bank_account_id": policy.bank_account_id,
                "role": policy.role,
                "internal_transfers_enabled": policy.internal_transfers_enabled,
                "target_floor": str(policy.target_floor),
                "target_ceiling": str(policy.target_ceiling),
                "accept_surplus": policy.accept_surplus,
                "monthly_outbound_limit": str(policy.monthly_outbound_limit),
                "min_transfer_amount": str(policy.min_transfer_amount),
            }
            for policy in policies
        ],
    }
