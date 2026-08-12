from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import new_token
from app.integrations import enable_banking
from app.integrations.kraken_api import (
    KrakenConfigurationError,
    get_api_key_permissions,
    get_deposit_status,
    get_eur_balance,
    market_buy_eur,
)
from app.models.entities import (
    BankAccount,
    BankAutopilotPolicy,
    BankConnection,
    InvestmentFundingTransfer,
    OAuthState,
    OwnAccountTransfer,
    Task,
)
from app.services.audit import write_audit
from app.services.cash_safety import effective_available_balance
from app.services.financial_autopilot import (
    ACTIVE_TRANSFER_STATUSES,
    budget_cash_plan_by_scope,
    current_month_income_by_scope,
    monthly_spend_by_scope,
    upcoming_bill_totals,
)
from app.services.financial_learning import learn_recurring_cashflows
from app.services.runtime_config import get_runtime_value

SUCCESS_STATUSES = {"ACSC", "ACCC", "BOOK"}
FAILED_STATUSES = {"RJCT", "CANC", "CNCL", "FAIL"}
ACTIVE_FUNDING_STATUSES = {
    "creating", "received", "pending", "authorization_required", "creation_uncertain",
    "acsp", "actc", "acpt", "awaiting_deposit", "deposit_observed", "trade_pending",
}


def _money(value: Any, default: Decimal = Decimal("0.00")) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _iban(value: Any) -> str:
    return "".join(str(value or "").upper().split())


async def _active_bank_transfer_exists(db: AsyncSession, source_id: int) -> bool:
    count = (
        await db.execute(
            select(func.count()).select_from(OwnAccountTransfer).where(
                OwnAccountTransfer.source_account_id == source_id,
                OwnAccountTransfer.status.in_(ACTIVE_TRANSFER_STATUSES),
            )
        )
    ).scalar_one()
    return bool(count)


async def _active_kraken_funding(db: AsyncSession) -> InvestmentFundingTransfer | None:
    return (
        await db.execute(
            select(InvestmentFundingTransfer)
            .where(
                InvestmentFundingTransfer.provider == "kraken",
                InvestmentFundingTransfer.status.in_(ACTIVE_FUNDING_STATUSES),
            )
            .order_by(InvestmentFundingTransfer.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _monthly_committed(db: AsyncSession) -> Decimal:
    month_start = datetime.combine(date.today().replace(day=1), datetime.min.time())
    value = (
        await db.execute(
            select(func.coalesce(func.sum(InvestmentFundingTransfer.amount), 0)).where(
                InvestmentFundingTransfer.provider == "kraken",
                InvestmentFundingTransfer.created_at >= month_start,
                InvestmentFundingTransfer.status.not_in(["failed", "cancelled", "rejected"]),
            )
        )
    ).scalar_one()
    return _money(value)


async def _personal_source(db: AsyncSession) -> tuple[BankAccount, BankAutopilotPolicy, BankConnection] | None:
    rows = list(
        (
            await db.execute(
                select(BankAccount, BankAutopilotPolicy, BankConnection)
                .join(BankAutopilotPolicy, BankAutopilotPolicy.bank_account_id == BankAccount.id)
                .join(BankConnection, BankConnection.id == BankAccount.bank_connection_id)
                .where(
                    BankAccount.account_scope == "personal",
                    BankAccount.enabled_for_payments.is_(True),
                    BankAutopilotPolicy.role == "operating",
                    BankAutopilotPolicy.internal_transfers_enabled.is_(True),
                )
                .order_by(BankAccount.id)
            )
        ).all()
    )
    return rows[0] if rows else None


async def _protected_source_floor(db: AsyncSession, account: BankAccount, policy: BankAutopilotPolicy) -> Decimal:
    spend = await monthly_spend_by_scope(db)
    bills = await upcoming_bill_totals(db)
    recurring = await learn_recurring_cashflows(db)
    recurring_amount = _money(recurring.get("protected_next_30_days", {}).get("personal", "0"))
    monthly_budget, reserve_targets, allocation_percent = await budget_cash_plan_by_scope(db)
    income = await current_month_income_by_scope(db)
    has_tax_destination = bool((
        await db.execute(
            select(func.count()).select_from(BankAutopilotPolicy)
            .join(BankAccount, BankAccount.id == BankAutopilotPolicy.bank_account_id)
            .where(BankAccount.account_scope == "personal", BankAutopilotPolicy.role == "tax")
        )
    ).scalar_one())
    tax_virtual = Decimal("0.00")
    if not has_tax_destination:
        tax_virtual = (
            _money(income.get("personal", Decimal("0")))
            * _money(allocation_percent.get("personal", Decimal("0")))
            / Decimal("100")
        ).quantize(Decimal("0.01"))
    try:
        multiplier = Decimal(await get_runtime_value(db, "finance_cash_buffer_multiplier", "1.10"))
    except InvalidOperation:
        multiplier = Decimal("1.10")
    minimum = _money(await get_runtime_value(db, "finance_min_operating_cash_floor", "1000"), Decimal("1000"))
    normal_need = max(
        _money(spend.get("personal", Decimal("0"))),
        _money(bills.get("personal", Decimal("0"))) + recurring_amount,
        _money(monthly_budget.get("personal", Decimal("0"))),
        _money(reserve_targets.get("personal", Decimal("0"))),
    )
    dynamic = (normal_need * multiplier).quantize(Decimal("0.01")) + _money(account.safety_reserve) + tax_virtual
    return max(dynamic, _money(policy.target_floor), _money(policy.target_ceiling), minimum)


async def run_kraken_funding_autopilot(db: AsyncSession, *, redirect_url: str) -> dict[str, Any]:
    if (await get_runtime_value(db, "kraken_auto_fund_enabled", "false")).casefold() != "true":
        return {"enabled": False, "state": "disabled"}
    target = _money(await get_runtime_value(db, "kraken_monthly_target_eur", "0"))
    if target <= 0:
        return {"enabled": True, "state": "no_target"}
    recipient = (await get_runtime_value(db, "kraken_funding_recipient", "")).strip()
    iban = _iban(await get_runtime_value(db, "kraken_funding_iban", ""))
    reference = (await get_runtime_value(db, "kraken_funding_reference", "")).strip()
    if not recipient or len(iban) < 15 or not reference:
        return {"enabled": True, "state": "configuration_required", "missing": "recipient/IBAN/reference"}

    permissions = await get_api_key_permissions(db)
    if "query-funds" not in permissions:
        return {"enabled": True, "state": "configuration_required", "missing": "Kraken query-funds permission"}

    active = await _active_kraken_funding(db)
    if active is not None:
        return {"enabled": True, "state": "active_transfer", "transfer_id": active.id, "status": active.status}

    source_bundle = await _personal_source(db)
    if source_bundle is None:
        return {"enabled": True, "state": "no_personal_operating_source"}
    source, policy, connection = source_bundle
    if await _active_bank_transfer_exists(db, source.id):
        return {"enabled": True, "state": "bank_transfer_active", "source_account_id": source.id}

    committed = await _monthly_committed(db)
    gap = max(Decimal("0.00"), target - committed)
    if gap <= 0:
        return {"enabled": True, "state": "monthly_target_met", "target": str(target), "committed": str(committed)}

    available = await effective_available_balance(db, source)
    if available is None:
        return {"enabled": True, "state": "balance_unavailable"}
    retained = await _protected_source_floor(db, source, policy)
    excess = _money(available) - retained
    if excess <= 0:
        return {"enabled": True, "state": "no_safe_surplus", "retained": str(retained)}

    max_single = _money(await get_runtime_value(db, "finance_max_single_transfer", "1000"), Decimal("1000"))
    amount = min(gap, excess, max_single).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    minimum = max(Decimal("1.00"), _money(policy.min_transfer_amount))
    if amount < minimum:
        return {"enabled": True, "state": "below_minimum", "safe_surplus": str(max(excess, Decimal('0')))}

    today_key = f"kraken:{date.today().isoformat()}:{source.id}:{amount:.2f}:{reference}"
    existing = (
        await db.execute(select(InvestmentFundingTransfer).where(InvestmentFundingTransfer.idempotency_key == today_key))
    ).scalar_one_or_none()
    if existing is not None:
        return {"enabled": True, "state": "already_planned", "transfer_id": existing.id, "status": existing.status}

    pre_cash = await get_eur_balance(db)
    transfer = InvestmentFundingTransfer(
        provider="kraken",
        source_bank_account_id=source.id,
        amount=amount,
        currency="EUR",
        recipient_name=recipient[:255],
        creditor_iban=iban[:34],
        reference=reference[:2000],
        status="creating",
        pre_provider_cash=pre_cash,
        trade_pair=(await get_runtime_value(db, "kraken_default_pair", "XBTEUR"))[:40],
        idempotency_key=today_key,
    )
    db.add(transfer)
    await db.flush()
    state = new_token(24)
    state_row = OAuthState(
        state=state,
        provider="kraken_investment_funding",
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
            creditor_name=recipient,
            creditor_iban=iban,
            amount=f"{amount:.2f}",
            currency="EUR",
            reference=reference,
            state=state,
            redirect_url=redirect_url,
            debtor_iban=source.iban or None,
        )
    except enable_banking.EnableBankingConfigurationError as exc:
        transfer.status = "failed"
        transfer.failure_reason = str(exc)[:2000]
        await db.delete(state_row)
        await db.commit()
        return {"enabled": True, "state": "failed", "transfer_id": transfer.id, "error": str(exc)}
    except (httpx.RequestError, TimeoutError) as exc:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = f"Kraken funding payment outcome is uncertain; automatic retry is blocked: {exc}"[:2000]
        db.add(Task(
            title="Check bank before retrying Kraken funding",
            description=transfer.failure_reason,
            source_type="kraken_funding_uncertain",
            source_id=str(transfer.id),
            priority="urgent",
            requires_approval=True,
        ))
        await db.commit()
        return {"enabled": True, "state": "creation_uncertain", "transfer_id": transfer.id}

    transfer.external_payment_id = str(response.get("payment_id") or response.get("id") or "").strip() or None
    transfer.authorization_url = str(response.get("url") or "").strip() or None
    transfer.requires_user_action = bool(transfer.authorization_url)
    if transfer.external_payment_id is None:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = "Payment provider returned no payment identifier; automatic retry is blocked."
    else:
        transfer.status = "authorization_required" if transfer.requires_user_action else str(response.get("status") or "received").lower()
    state_row.payload_json = json.dumps({"transfer_id": transfer.id, "external_payment_id": transfer.external_payment_id})
    if transfer.authorization_url:
        db.add(Task(
            title="Authorize Kraken investment funding",
            description=f"The VA prepared {amount:.2f} EUR of safe surplus for Kraken. Bank SCA is required: {transfer.authorization_url}",
            source_type="kraken_funding_authorization",
            source_id=str(transfer.id),
            priority="high",
            requires_approval=True,
        ))
    await write_audit(
        db,
        "kraken_funding_initiated",
        entity_type="investment_funding_transfer",
        entity_id=str(transfer.id),
        details={"source_account_id": source.id, "amount": str(amount), "requires_user_action": transfer.requires_user_action},
    )
    await db.commit()
    return {"enabled": True, "state": transfer.status, "transfer_id": transfer.id, "amount": str(amount), "requires_user_action": transfer.requires_user_action}


async def refresh_kraken_funding_transfer(db: AsyncSession, transfer: InvestmentFundingTransfer) -> InvestmentFundingTransfer:
    if not transfer.external_payment_id or transfer.status in {"creation_uncertain", "failed", "cancelled", "rejected", "funded", "invested"}:
        return transfer
    if transfer.status not in {"awaiting_deposit", "deposit_observed", "trade_pending"}:
        response = await enable_banking.get_payment(db, transfer.external_payment_id)
        status = str(response.get("status") or transfer.status).upper()
        if status in SUCCESS_STATUSES:
            transfer.status = "awaiting_deposit"
            transfer.requires_user_action = False
            transfer.authorization_url = None
        elif status in FAILED_STATUSES:
            transfer.status = "failed"
            transfer.requires_user_action = False
            transfer.failure_reason = str(response.get("status_reason_information") or response.get("reason") or status)[:2000]
        else:
            transfer.status = status.lower()
        await db.commit()
    return transfer


async def reconcile_kraken_funding_and_trade(db: AsyncSession, transfer: InvestmentFundingTransfer) -> InvestmentFundingTransfer:
    if transfer.status != "awaiting_deposit":
        return transfer
    current = await get_eur_balance(db)
    transfer.observed_provider_cash = current
    baseline = transfer.pre_provider_cash or Decimal("0")
    required_delta = (transfer.amount * Decimal("0.98")).quantize(Decimal("0.01"))

    # Prefer Kraken's own deposit ledger over a balance delta. The balance fallback
    # remains useful when an account/API combination does not expose fiat deposit
    # status, but it is deliberately conservative and only one funding transfer can
    # be active at a time.
    deposit_observed = False
    try:
        deposits = await get_deposit_status(db, asset="EUR")
        earliest = transfer.created_at - timedelta(days=1)
        for item in deposits:
            status = str(item.get("status") or "").casefold()
            if status not in {"success", "settled", "completed"}:
                continue
            try:
                amount = _money(item.get("amount"))
                booked = datetime.utcfromtimestamp(float(item.get("time") or 0))
            except (TypeError, ValueError, OSError):
                continue
            if booked < earliest or amount < required_delta:
                continue
            transfer.provider_deposit_ref = str(item.get("refid") or item.get("txid") or "")[:255] or None
            deposit_observed = True
            break
    except (KrakenConfigurationError, httpx.RequestError):
        deposit_observed = False

    if not deposit_observed and current - baseline < required_delta:
        await db.commit()
        return transfer
    transfer.status = "deposit_observed"
    await db.commit()

    auto_trade = (await get_runtime_value(db, "kraken_auto_trade_enabled", "false")).casefold() == "true"
    if not auto_trade:
        transfer.status = "funded"
        await db.commit()
        return transfer
    permissions = await get_api_key_permissions(db)
    if "modify-trades" not in permissions:
        transfer.status = "funded"
        transfer.failure_reason = "Kraken deposit arrived, but the API key lacks modify-trades permission; cash was left uninvested."
        await db.commit()
        return transfer
    max_trade = _money(await get_runtime_value(db, "kraken_max_auto_trade_eur", "250"), Decimal("250"))
    trade_amount = min(transfer.amount, max_trade, _money(current))
    if trade_amount <= 0:
        transfer.status = "funded"
        await db.commit()
        return transfer
    transfer.status = "trade_pending"
    await db.commit()
    try:
        result = await market_buy_eur(
            db,
            pair=transfer.trade_pair or "XBTEUR",
            eur_amount=trade_amount,
            client_order_id=f"va{transfer.id}{int(transfer.created_at.timestamp())}",
        )
    except KrakenConfigurationError as exc:
        transfer.status = "funded"
        transfer.failure_reason = f"Kraken funding succeeded but automatic trade was not placed: {exc}"[:2000]
        await db.commit()
        return transfer
    order_ids = result.get("txid") if isinstance(result, dict) else None
    if isinstance(order_ids, list):
        order_id = str(order_ids[0]) if order_ids else ""
    else:
        order_id = str(order_ids or result.get("order_id") or "") if isinstance(result, dict) else ""
    transfer.trade_order_id = order_id[:255] or None
    transfer.status = "invested" if transfer.trade_order_id else "funded"
    if not transfer.trade_order_id:
        transfer.failure_reason = "Kraken accepted the trade call but did not return an order identifier; no automatic retry was attempted."
    await write_audit(
        db,
        "kraken_investment_executed" if transfer.trade_order_id else "kraken_investment_trade_uncertain",
        entity_type="investment_funding_transfer",
        entity_id=str(transfer.id),
        result="success" if transfer.trade_order_id else "blocked",
        details={"amount": str(trade_amount), "pair": transfer.trade_pair, "order_id": transfer.trade_order_id},
    )
    await db.commit()
    return transfer


async def refresh_all_kraken_funding(db: AsyncSession) -> dict[str, int]:
    rows = list(
        (
            await db.execute(
                select(InvestmentFundingTransfer)
                .where(
                    InvestmentFundingTransfer.provider == "kraken",
                    InvestmentFundingTransfer.status.not_in(["failed", "cancelled", "rejected", "funded", "invested"]),
                )
                .order_by(InvestmentFundingTransfer.id)
            )
        ).scalars()
    )
    refreshed = 0
    reconciled = 0
    for row in rows:
        before = row.status
        try:
            await refresh_kraken_funding_transfer(db, row)
            if row.status == "awaiting_deposit":
                await reconcile_kraken_funding_and_trade(db, row)
                reconciled += 1
            if row.status != before:
                refreshed += 1
        except (enable_banking.EnableBankingConfigurationError, KrakenConfigurationError, httpx.RequestError) as exc:
            row.failure_reason = f"Funding status check deferred: {exc}"[:2000]
            await db.commit()
    return {"reviewed": len(rows), "status_changes": refreshed, "provider_reconciliations": reconciled}


async def complete_kraken_funding_authorization(
    db: AsyncSession,
    *,
    state: str,
    error: str | None = None,
    error_description: str | None = None,
) -> InvestmentFundingTransfer | None:
    state_row = await db.get(OAuthState, state)
    if state_row is None or state_row.provider != "kraken_investment_funding" or state_row.expires_at < datetime.utcnow():
        raise ValueError("Kraken funding authorization state is invalid or expired")
    context = json.loads(state_row.payload_json or "{}")
    transfer = await db.get(InvestmentFundingTransfer, int(context.get("transfer_id") or 0))
    if transfer is not None:
        if error:
            transfer.status = "cancelled" if error == "access_denied" else "failed"
            transfer.requires_user_action = False
            transfer.failure_reason = (error_description or error)[:2000]
        else:
            await refresh_kraken_funding_transfer(db, transfer)
            transfer.authorization_url = None
            transfer.requires_user_action = False
        task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "kraken_funding_authorization",
                    Task.source_id == str(transfer.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if task is not None:
            task.status = "completed"
    await db.delete(state_row)
    await db.commit()
    return transfer


async def investment_funding_transfer_summary(db: AsyncSession) -> dict[str, Any]:
    rows = list(
        (
            await db.execute(
                select(InvestmentFundingTransfer).order_by(InvestmentFundingTransfer.id.desc()).limit(50)
            )
        ).scalars()
    )
    return {
        "kraken": [
            {
                "id": row.id,
                "amount": str(row.amount),
                "currency": row.currency,
                "status": row.status,
                "requires_user_action": row.requires_user_action,
                "authorization_url": row.authorization_url,
                "trade_pair": row.trade_pair,
                "trade_order_id": row.trade_order_id,
                "failure_reason": row.failure_reason,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows if row.provider == "kraken"
        ]
    }
