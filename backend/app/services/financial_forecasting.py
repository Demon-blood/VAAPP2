from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.models.entities import (
    BankAccount,
    BankAutopilotPolicy,
    Bill,
    FinancialAllocationAction,
    FinancialAllocationPlan,
    FinancialForecastRun,
    OwnAccountTransfer,
)
from app.services.audit import write_audit
from app.services.cash_safety import committed_destination_balance, effective_available_balance
from app.services.financial_autopilot import (
    _spending_target_for_account,
    budget_cash_plan_by_scope,
    create_own_account_transfer,
    current_month_income_by_scope,
    ensure_account_autopilot_policies,
    monthly_spend_by_scope,
)
from app.services.financial_learning import learn_recurring_cashflows
from app.services.investment_service import investment_funding_forecast_by_scope
from app.services.runtime_config import get_runtime_value

_FINAL_TRANSFER_STATUSES = {"completed", "failed", "cancelled", "rejected"}
_ACTIVE_TRANSFER_STATUSES = {
    "creating",
    "received",
    "pending",
    "authorization_required",
    "creation_uncertain",
    "acsp",
    "actc",
    "acpt",
}


def _money(value: Any, default: Decimal = Decimal("0.00")) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _add_months(value: datetime, months: int = 1) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _scope_account_name(account: BankAccount | None) -> str:
    if account is None:
        return ""
    return account.name or account.iban or f"Account {account.id}"


async def _money_setting(
    db: AsyncSession,
    key: str,
    default: str,
) -> Decimal:
    return _money(await get_runtime_value(db, key, default), _money(default))


async def _reconcile_actions(db: AsyncSession) -> int:
    actions = list(
        (
            await db.execute(
                select(FinancialAllocationAction).where(
                    FinancialAllocationAction.status.in_(
                        ["dispatching", "initiated", "needs_user_auth", "creation_uncertain"]
                    )
                )
            )
        ).scalars()
    )
    changed = 0
    for action in actions:
        transfer = None
        if action.own_account_transfer_id:
            transfer = await db.get(OwnAccountTransfer, action.own_account_transfer_id)
        if transfer is None and action.idempotency_key:
            transfer = (
                await db.execute(
                    select(OwnAccountTransfer).where(
                        OwnAccountTransfer.idempotency_key == action.idempotency_key
                    )
                )
            ).scalar_one_or_none()
        if transfer is None:
            if action.status == "dispatching":
                # No provider POST can occur before create_own_account_transfer persists
                # its OwnAccountTransfer intent. Without that ledger row this is a
                # pre-provider crash window and is safe to resume.
                action.status = "planned"
                changed += 1
            continue
        if action.own_account_transfer_id != transfer.id:
            action.own_account_transfer_id = transfer.id
            changed += 1
        new_status = action.status
        normalized = str(transfer.status or "").lower()
        if normalized == "completed":
            new_status = "verified"
        elif normalized in {"failed", "cancelled", "rejected"}:
            new_status = "failed"
        elif normalized == "creation_uncertain":
            new_status = "creation_uncertain"
        elif transfer.requires_user_action:
            new_status = "needs_user_auth"
        else:
            new_status = "initiated"
        if new_status != action.status:
            action.status = new_status
            changed += 1
    touched_plan_ids = {action.allocation_plan_id for action in actions if action.allocation_plan_id}
    for plan_id in touched_plan_ids:
        plan = await db.get(FinancialAllocationPlan, plan_id)
        if plan is None:
            continue
        rows = list((await db.execute(select(FinancialAllocationAction).where(FinancialAllocationAction.allocation_plan_id == plan_id))).scalars())
        statuses = {row.status for row in rows}
        if not rows:
            continue
        if statuses <= {"verified"}:
            plan.status = "verified"
        elif "creation_uncertain" in statuses:
            plan.status = "blocked_system"
        elif "needs_user_auth" in statuses:
            plan.status = "needs_user"
        elif statuses & {"initiated", "dispatching"}:
            plan.status = "executing"
        elif statuses <= {"failed", "blocked_policy"}:
            plan.status = "blocked"
    if changed or touched_plan_ids:
        await db.commit()
    return changed


async def _resume_safe_actions(db: AsyncSession, *, redirect_url: str) -> int:
    actions = list(
        (
            await db.execute(
                select(FinancialAllocationAction).where(
                    FinancialAllocationAction.status == "planned",
                    FinancialAllocationAction.own_account_transfer_id.is_(None),
                )
            )
        ).scalars()
    )
    resumed = 0
    for action in actions:
        if not action.source_account_id or not action.destination_account_id:
            action.status = "blocked_policy"
            action.rationale = f"{action.rationale} Missing source/destination account."[:2000]
            continue
        action.status = "dispatching"
        await db.commit()
        try:
            transfer = await create_own_account_transfer(
                db,
                source_account_id=action.source_account_id,
                destination_account_id=action.destination_account_id,
                amount=_money(action.amount),
                reason=action.rationale,
                redirect_url=redirect_url,
                idempotency_key=action.idempotency_key,
            )
        except ValueError as exc:
            action.status = "blocked_policy"
            action.rationale = f"{action.rationale} Execution blocked: {exc}"[:2000]
            await db.commit()
            resumed += 1
            continue
        action.own_account_transfer_id = transfer.id
        if transfer.status == "creation_uncertain":
            action.status = "creation_uncertain"
        elif transfer.status in {"failed", "cancelled", "rejected"}:
            action.status = "failed"
        elif transfer.requires_user_action:
            action.status = "needs_user_auth"
        elif transfer.status == "completed":
            action.status = "verified"
        else:
            action.status = "initiated"
        await db.commit()
        resumed += 1
    return resumed


async def _forecast_inputs(db: AsyncSession, horizon_days: int) -> dict[str, Any]:
    policies = await ensure_account_autopilot_policies(db)
    accounts = list((await db.execute(select(BankAccount).order_by(BankAccount.id))).scalars())
    policy_by_account = {row.bank_account_id: row for row in policies}
    recurring = await learn_recurring_cashflows(db)
    monthly_spend = await monthly_spend_by_scope(db)
    monthly_budget, reserve_targets, allocation_percent = await budget_cash_plan_by_scope(db)
    investment_funding = await investment_funding_forecast_by_scope(db)
    month_income = await current_month_income_by_scope(db)
    cutoff = datetime.utcnow() + timedelta(days=horizon_days)
    bills = list(
        (
            await db.execute(
                select(Bill).where(
                    Bill.status.not_in(["paid", "cancelled", "reclassified_nonpayable"]),
                    (Bill.due_at.is_(None)) | (Bill.due_at <= cutoff),
                )
            )
        ).scalars()
    )
    minimum_operating_floor = await _money_setting(db, "finance_min_operating_cash_floor", "1000")
    max_single = await _money_setting(db, "finance_max_single_transfer", "1000")
    return {
        "policies": policies,
        "accounts": accounts,
        "policy_by_account": policy_by_account,
        "recurring": recurring,
        "monthly_spend": monthly_spend,
        "monthly_budget": monthly_budget,
        "reserve_targets": reserve_targets,
        "allocation_percent": allocation_percent,
        "investment_funding": investment_funding,
        "month_income": month_income,
        "bills": bills,
        "minimum_operating_floor": minimum_operating_floor,
        "max_single": max_single,
    }


async def generate_financial_forecast(
    db: AsyncSession,
    *,
    horizon_days: int = 90,
    force: bool = False,
) -> FinancialForecastRun:
    horizon_days = max(30, min(int(horizon_days), 180))
    now = datetime.utcnow()
    today = datetime.combine(date.today(), datetime.min.time())
    data = await _forecast_inputs(db, horizon_days)
    accounts: list[BankAccount] = data["accounts"]
    policy_by_account: dict[int, BankAutopilotPolicy] = data["policy_by_account"]

    effective_by_account: dict[int, Decimal] = {}
    for account in accounts:
        effective = await effective_available_balance(db, account)
        if effective is not None:
            effective_by_account[account.id] = _money(effective)

    material = {
        "date": date.today().isoformat(),
        "horizon_days": horizon_days,
        "accounts": [
            {
                "id": account.id,
                "scope": account.account_scope,
                "currency": account.currency,
                "effective": str(effective_by_account.get(account.id, Decimal("0.00"))),
                "reserve": str(_money(account.safety_reserve)),
                "role": policy_by_account.get(account.id).role if policy_by_account.get(account.id) else "",
                "floor": str(_money(policy_by_account.get(account.id).target_floor)) if policy_by_account.get(account.id) else "0.00",
            }
            for account in accounts
        ],
        "recurring": data["recurring"],
        "monthly_spend": {key: str(value) for key, value in data["monthly_spend"].items()},
        "monthly_budget": {key: str(value) for key, value in data["monthly_budget"].items()},
        "reserve_targets": {key: str(value) for key, value in data["reserve_targets"].items()},
        "allocation_percent": {key: str(value) for key, value in data["allocation_percent"].items()},
        "investment_funding": {key: str(value) for key, value in data["investment_funding"].items()},
        "month_income": {key: str(value) for key, value in data["month_income"].items()},
        "bills": [
            {
                "id": bill.id,
                "scope": bill.account_scope,
                "amount": str(_money(bill.amount)),
                "currency": bill.currency,
                "due_at": _iso(bill.due_at),
                "status": bill.status,
            }
            for bill in data["bills"]
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    if not force:
        existing = (
            await db.execute(
                select(FinancialForecastRun)
                .where(
                    FinancialForecastRun.input_fingerprint == fingerprint,
                    FinancialForecastRun.horizon_days == horizon_days,
                    FinancialForecastRun.created_at >= now - timedelta(hours=6),
                )
                .order_by(FinancialForecastRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    scopes = sorted({account.account_scope for account in accounts} | {"personal", "pro"})
    events_by_scope: dict[str, dict[int, dict[str, Decimal]]] = {
        scope: {day: {"base": Decimal("0.00"), "conservative": Decimal("0.00")} for day in range(horizon_days + 1)}
        for scope in scopes
    }
    protected_recurring_monthly: dict[str, Decimal] = {scope: Decimal("0.00") for scope in scopes}

    for raw in data["recurring"].get("items", []):
        scope = str(raw.get("scope") or "personal")
        if scope not in events_by_scope:
            continue
        amount = _money(raw.get("amount"))
        if amount <= 0:
            continue
        direction = str(raw.get("direction") or "debit")
        confidence = _money(raw.get("confidence"), Decimal("0.00"))
        kind = str(raw.get("kind") or "other")
        try:
            next_expected = datetime.fromisoformat(str(raw.get("next_expected") or "").replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            continue
        if raw.get("protected_obligation") and direction == "debit":
            protected_recurring_monthly[scope] = protected_recurring_monthly.get(scope, Decimal("0.00")) + amount
        occurrence = next_expected
        while occurrence < today:
            occurrence = _add_months(occurrence)
        while occurrence <= today + timedelta(days=horizon_days):
            day = max(0, min(horizon_days, (occurrence.date() - today.date()).days))
            if direction == "credit":
                base_factor = min(Decimal("1.00"), max(Decimal("0.00"), confidence))
                if kind in {"salary", "child_support"} and confidence >= Decimal("0.95"):
                    conservative_factor = Decimal("0.90")
                elif confidence >= Decimal("0.90"):
                    conservative_factor = Decimal("0.75")
                else:
                    conservative_factor = Decimal("0.00")
                events_by_scope[scope][day]["base"] += amount * base_factor
                events_by_scope[scope][day]["conservative"] += amount * conservative_factor
            else:
                events_by_scope[scope][day]["base"] -= amount
                events_by_scope[scope][day]["conservative"] -= amount * Decimal("1.10")
            occurrence = _add_months(occurrence)

    known_bill_30: dict[str, Decimal] = {scope: Decimal("0.00") for scope in scopes}
    for bill in data["bills"]:
        scope = bill.account_scope
        if scope not in events_by_scope:
            continue
        amount = _money(bill.amount)
        if amount <= 0:
            continue
        if bill.due_at is None:
            day = 0
        else:
            day = max(0, min(horizon_days, (bill.due_at.date() - today.date()).days))
        events_by_scope[scope][day]["base"] -= amount
        events_by_scope[scope][day]["conservative"] -= amount
        if day <= 30:
            known_bill_30[scope] = known_bill_30.get(scope, Decimal("0.00")) + amount

    scope_rows: list[dict[str, Any]] = []
    overall_status = "healthy"
    overall_base_min = Decimal("0.00")
    overall_conservative_min = Decimal("0.00")
    for scope in scopes:
        managed_accounts = [
            account
            for account in accounts
            if account.account_scope == scope
            and (policy_by_account.get(account.id) is None or policy_by_account[account.id].role != "disabled")
        ]
        starting_cash = sum((effective_by_account.get(account.id, Decimal("0.00")) for account in managed_accounts), Decimal("0.00"))
        protected_floor = Decimal("0.00")
        for account in managed_accounts:
            policy = policy_by_account.get(account.id)
            floor = max(_money(account.safety_reserve), _money(policy.target_floor) if policy else Decimal("0.00"))
            if policy and policy.role == "operating":
                floor = max(floor, data["minimum_operating_floor"])
            protected_floor += floor

        monthly_baseline = max(
            _money(data["monthly_spend"].get(scope)),
            _money(data["monthly_budget"].get(scope)),
        )
        explicit_monthly = protected_recurring_monthly.get(scope, Decimal("0.00")) + known_bill_30.get(scope, Decimal("0.00"))
        variable_monthly = max(Decimal("0.00"), monthly_baseline - explicit_monthly)
        investment_monthly = _money(data["investment_funding"].get(scope))
        daily_variable = (variable_monthly / Decimal("30.4375")).quantize(Decimal("0.0001"))
        daily_investment = (investment_monthly / Decimal("30.4375")).quantize(Decimal("0.0001"))

        base_cash = starting_cash
        conservative_cash = starting_cash
        base_min = starting_cash
        conservative_min = starting_cash
        days_until_floor: int | None = None
        series: list[dict[str, Any]] = []
        for day in range(horizon_days + 1):
            if day > 0:
                base_cash -= daily_variable + daily_investment
                conservative_cash -= daily_variable * Decimal("1.10") + daily_investment
            base_cash += events_by_scope[scope][day]["base"]
            conservative_cash += events_by_scope[scope][day]["conservative"]
            base_cash = base_cash.quantize(Decimal("0.01"))
            conservative_cash = conservative_cash.quantize(Decimal("0.01"))
            base_min = min(base_min, base_cash)
            conservative_min = min(conservative_min, conservative_cash)
            if days_until_floor is None and conservative_cash < protected_floor:
                days_until_floor = day
            if day in {0, 7, 14, 30, 60, 90, 120, 180} or day == horizon_days:
                series.append(
                    {
                        "day": day,
                        "date": (today + timedelta(days=day)).date().isoformat(),
                        "base_cash": str(base_cash),
                        "conservative_cash": str(conservative_cash),
                    }
                )

        allocatable = max(Decimal("0.00"), conservative_min - protected_floor).quantize(Decimal("0.01"))
        if conservative_min < protected_floor:
            status = "at_risk"
            overall_status = "at_risk"
        elif allocatable <= Decimal("0.00"):
            status = "protected"
            if overall_status == "healthy":
                overall_status = "protected"
        else:
            status = "surplus"
        row = {
            "scope": scope,
            "status": status,
            "starting_cash": str(starting_cash.quantize(Decimal("0.01"))),
            "protected_floor": str(protected_floor.quantize(Decimal("0.01"))),
            "base_min_cash": str(base_min.quantize(Decimal("0.01"))),
            "conservative_min_cash": str(conservative_min.quantize(Decimal("0.01"))),
            "allocatable_surplus": str(allocatable),
            "days_until_floor": days_until_floor,
            "monthly_baseline_spend": str(monthly_baseline.quantize(Decimal("0.01"))),
            "monthly_variable_spend": str(variable_monthly.quantize(Decimal("0.01"))),
            "monthly_protected_recurring": str(protected_recurring_monthly.get(scope, Decimal("0.00")).quantize(Decimal("0.01"))),
            "known_bills_next_30_days": str(known_bill_30.get(scope, Decimal("0.00")).quantize(Decimal("0.01"))),
            "monthly_investment_funding": str(investment_monthly.quantize(Decimal("0.01"))),
            "reserve_target": str(_money(data["reserve_targets"].get(scope))),
            "tax_income_allocation_percent": str(_money(data["allocation_percent"].get(scope))),
            "series": series,
        }
        scope_rows.append(row)
        overall_base_min += base_min
        overall_conservative_min += conservative_min

    snapshot = {
        "generated_at": now.isoformat(),
        "horizon_days": horizon_days,
        "status": overall_status,
        "method": "source-backed recurring cashflows + exact open bills + budget/learned variable spend + protected investment funding; conservative scenario discounts uncertain income and increases variable/recurring debits",
        "scopes": scope_rows,
    }
    run = FinancialForecastRun(
        horizon_days=horizon_days,
        input_fingerprint=fingerprint,
        status=overall_status,
        base_min_cash=overall_base_min.quantize(Decimal("0.01")),
        conservative_min_cash=overall_conservative_min.quantize(Decimal("0.01")),
        snapshot_encrypted=encrypt_text(json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)),
        created_at=now,
    )
    db.add(run)
    await db.flush()
    await write_audit(
        db,
        "financial_forecast_generated",
        entity_type="financial_forecast_run",
        entity_id=str(run.id),
        details={
            "horizon_days": horizon_days,
            "status": overall_status,
            "input_fingerprint": fingerprint,
        },
    )
    await db.commit()
    await db.refresh(run)
    return run


def forecast_snapshot(run: FinancialForecastRun) -> dict[str, Any]:
    try:
        return json.loads(decrypt_text(run.snapshot_encrypted) or "{}")
    except Exception:
        return {}


async def latest_financial_forecast(db: AsyncSession) -> FinancialForecastRun | None:
    return (
        await db.execute(
            select(FinancialForecastRun).order_by(FinancialForecastRun.id.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _tax_gap(
    db: AsyncSession,
    *,
    scope: str,
    destination_ids: list[int],
    monthly_income: Decimal,
    allocation_percent: Decimal,
) -> Decimal:
    if not destination_ids or monthly_income <= 0 or allocation_percent <= 0:
        return Decimal("0.00")
    month_start = datetime.combine(date.today().replace(day=1), datetime.min.time())
    required = (monthly_income * allocation_percent / Decimal("100")).quantize(Decimal("0.01"))
    allocated = (
        await db.execute(
            select(func.coalesce(func.sum(OwnAccountTransfer.amount), 0)).where(
                OwnAccountTransfer.destination_account_id.in_(destination_ids),
                OwnAccountTransfer.created_at >= month_start,
                OwnAccountTransfer.status.not_in(["failed", "cancelled", "rejected"]),
            )
        )
    ).scalar_one()
    return max(Decimal("0.00"), required - _money(allocated))


async def _plan_scope_allocations(
    db: AsyncSession,
    *,
    run: FinancialForecastRun,
    scope_snapshot: dict[str, Any],
    data: dict[str, Any],
    redirect_url: str,
) -> FinancialAllocationPlan:
    scope = str(scope_snapshot.get("scope") or "personal")
    existing_plan = (
        await db.execute(
            select(FinancialAllocationPlan).where(
                FinancialAllocationPlan.forecast_run_id == run.id,
                FinancialAllocationPlan.account_scope == scope,
            )
        )
    ).scalar_one_or_none()
    if existing_plan is not None:
        return existing_plan

    plan = FinancialAllocationPlan(
        forecast_run_id=run.id,
        account_scope=scope,
        status="planning",
        starting_cash=_money(scope_snapshot.get("starting_cash")),
        protected_floor=_money(scope_snapshot.get("protected_floor")),
        base_min_cash=_money(scope_snapshot.get("base_min_cash")),
        conservative_min_cash=_money(scope_snapshot.get("conservative_min_cash")),
        allocatable_surplus=_money(scope_snapshot.get("allocatable_surplus")),
        details_encrypted=encrypt_text("{}"),
    )
    db.add(plan)
    await db.flush()

    accounts: list[BankAccount] = data["accounts"]
    policies: list[BankAutopilotPolicy] = data["policies"]
    policy_by_account: dict[int, BankAutopilotPolicy] = data["policy_by_account"]
    scope_accounts = {row.id: row for row in accounts if row.account_scope == scope}
    source_pairs = [
        (scope_accounts.get(policy.bank_account_id), policy)
        for policy in policies
        if policy.role == "operating"
        and policy.internal_transfers_enabled
        and policy.bank_account_id in scope_accounts
    ]
    source_pairs = [pair for pair in source_pairs if pair[0] is not None and pair[0].enabled_for_payments]
    destination_pairs = [
        (scope_accounts.get(policy.bank_account_id), policy)
        for policy in policies
        if policy.accept_surplus
        and policy.role in {"spending", "tax", "reserve", "savings"}
        and policy.bank_account_id in scope_accounts
    ]
    destination_pairs = [
        pair for pair in destination_pairs if pair[0] is not None and pair[0].iban
    ]

    remaining = _money(scope_snapshot.get("allocatable_surplus"))
    reasons: list[str] = []
    actions_created = 0
    if remaining <= 0:
        plan.status = "protected" if str(scope_snapshot.get("status")) != "at_risk" else "at_risk"
        reasons.append("Conservative forecast leaves no cash above protected floors.")
    elif not source_pairs:
        plan.status = "blocked_capability"
        reasons.append("No payment-enabled operating account in this scope may send own-account transfers.")
    elif not destination_pairs:
        plan.status = "blocked_capability"
        reasons.append("No same-scope spending/tax/reserve/savings account is configured to receive surplus.")
    else:
        tax_ids = [account.id for account, policy in destination_pairs if policy.role == "tax"]
        tax_gap = await _tax_gap(
            db,
            scope=scope,
            destination_ids=tax_ids,
            monthly_income=_money(data["month_income"].get(scope)),
            allocation_percent=_money(data["allocation_percent"].get(scope)),
        )
        reserve_accounts = [account for account, policy in destination_pairs if policy.role == "reserve"]
        reserve_committed_total = Decimal("0.00")
        for account in reserve_accounts:
            reserve_committed_total += _money(await committed_destination_balance(db, account))
        envelope_reserve_gap = max(
            Decimal("0.00"),
            _money(data["reserve_targets"].get(scope)) - reserve_committed_total,
        )
        spending_targets: dict[int, Decimal] = {}
        destination_balances: dict[int, Decimal] = {}
        for account, policy in destination_pairs:
            destination_balances[account.id] = _money(await committed_destination_balance(db, account))
            if policy.role == "spending":
                spending_targets[account.id] = await _spending_target_for_account(
                    db,
                    account,
                    policy,
                    monthly_investment_funding=_money(data["investment_funding"].get(scope)),
                )

        def destination_gap(account: BankAccount, policy: BankAutopilotPolicy) -> tuple[int, Decimal, str]:
            balance = destination_balances.get(account.id, Decimal("0.00"))
            explicit_gap = max(Decimal("0.00"), _money(policy.target_floor) - balance)
            if policy.role == "spending":
                gap = max(explicit_gap, spending_targets.get(account.id, Decimal("0.00")) - balance)
                return (0, max(Decimal("0.00"), gap), "spending_prefund")
            if policy.role == "tax":
                return (1, max(explicit_gap, tax_gap), "tax_reserve")
            if policy.role == "reserve":
                return (2, max(explicit_gap, envelope_reserve_gap), "cash_reserve")
            if policy.role == "savings":
                return (3, Decimal("0.00"), "ordinary_surplus")
            return (9, Decimal("0.00"), policy.role)

        for source, source_policy in sorted(source_pairs, key=lambda pair: pair[0].id):
            if remaining <= 0:
                break
            active = (
                await db.execute(
                    select(func.count()).select_from(OwnAccountTransfer).where(
                        OwnAccountTransfer.source_account_id == source.id,
                        OwnAccountTransfer.status.in_(_ACTIVE_TRANSFER_STATUSES),
                    )
                )
            ).scalar_one()
            if active:
                reasons.append(f"{_scope_account_name(source)} already has an active own-account transfer.")
                continue
            available = _money(await effective_available_balance(db, source))
            source_floor = max(
                _money(source.safety_reserve),
                _money(source_policy.target_floor),
                data["minimum_operating_floor"],
            )
            source_surplus = max(Decimal("0.00"), available - source_floor)
            source_budget = min(remaining, source_surplus, data["max_single"])
            if source_budget < _money(source_policy.min_transfer_amount):
                continue

            candidates = [
                (account, policy, *destination_gap(account, policy))
                for account, policy in destination_pairs
                if account.id != source.id and account.currency == source.currency
            ]
            candidates.sort(key=lambda item: (item[2], item[0].id))
            selected = None
            for item in candidates:
                account, policy, rank, gap, purpose = item
                if policy.role == "savings" or gap > 0:
                    selected = item
                    break
            if selected is None:
                continue
            destination, dest_policy, _, gap, purpose = selected
            desired = source_budget if dest_policy.role == "savings" or gap <= 0 else min(source_budget, gap)
            desired = desired.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            if desired < _money(source_policy.min_transfer_amount):
                continue
            key = f"forecast:{run.id}:{scope}:{purpose}:{source.id}:{destination.id}:{desired:.2f}"
            action = (
                await db.execute(
                    select(FinancialAllocationAction).where(
                        FinancialAllocationAction.idempotency_key == key
                    )
                )
            ).scalar_one_or_none()
            if action is None:
                action = FinancialAllocationAction(
                    allocation_plan_id=plan.id,
                    source_account_id=source.id,
                    destination_account_id=destination.id,
                    destination_role=dest_policy.role,
                    amount=desired,
                    currency=source.currency,
                    status="dispatching",
                    idempotency_key=key,
                    rationale=(
                        f"Forecast-safe {purpose}: conservative scope minimum {_money(scope_snapshot.get('conservative_min_cash')):.2f}; "
                        f"protected floor {_money(scope_snapshot.get('protected_floor')):.2f}; "
                        f"allocatable surplus {remaining:.2f}."
                    ),
                )
                db.add(action)
                await db.commit()
                await db.refresh(action)
            try:
                transfer = await create_own_account_transfer(
                    db,
                    source_account_id=source.id,
                    destination_account_id=destination.id,
                    amount=desired,
                    reason=action.rationale,
                    redirect_url=redirect_url,
                    idempotency_key=key,
                )
            except ValueError as exc:
                action.status = "blocked_policy"
                action.rationale = f"{action.rationale} Execution blocked: {exc}"[:2000]
                await db.commit()
                actions_created += 1
                continue
            action.own_account_transfer_id = transfer.id
            if transfer.status == "creation_uncertain":
                action.status = "creation_uncertain"
            elif transfer.status in {"failed", "cancelled", "rejected"}:
                action.status = "failed"
            elif transfer.requires_user_action:
                action.status = "needs_user_auth"
            elif transfer.status == "completed":
                action.status = "verified"
            else:
                action.status = "initiated"
            await db.commit()
            actions_created += 1
            if transfer.status not in {"failed", "cancelled", "rejected"}:
                remaining = max(Decimal("0.00"), remaining - desired)
                if dest_policy.role == "tax":
                    tax_gap = max(Decimal("0.00"), tax_gap - desired)
                if dest_policy.role == "reserve":
                    envelope_reserve_gap = max(Decimal("0.00"), envelope_reserve_gap - desired)

        if actions_created:
            action_statuses = {
                row.status
                for row in (
                    await db.execute(
                        select(FinancialAllocationAction).where(
                            FinancialAllocationAction.allocation_plan_id == plan.id
                        )
                    )
                ).scalars()
            }
            if "creation_uncertain" in action_statuses:
                plan.status = "blocked_system"
            elif "needs_user_auth" in action_statuses:
                plan.status = "needs_user"
            elif action_statuses & {"initiated", "dispatching", "verified"}:
                plan.status = "executing" if "verified" not in action_statuses or len(action_statuses) > 1 else "verified"
            else:
                plan.status = "blocked"
        else:
            plan.status = "no_action"

    details = {
        "scope": scope,
        "forecast_status": scope_snapshot.get("status"),
        "forecast_allocatable_surplus": scope_snapshot.get("allocatable_surplus"),
        "remaining_unallocated": str(remaining.quantize(Decimal("0.01"))),
        "reasons": reasons,
    }
    plan.details_encrypted = encrypt_text(json.dumps(details, separators=(",", ":"), ensure_ascii=False))
    await db.commit()
    await db.refresh(plan)
    return plan


async def run_financial_allocation_cycle(
    db: AsyncSession,
    *,
    redirect_url: str,
    horizon_days: int = 90,
    force_forecast: bool = True,
) -> dict[str, Any]:
    reconciled = await _reconcile_actions(db)
    resumed = await _resume_safe_actions(db, redirect_url=redirect_url)
    reconciled += await _reconcile_actions(db)
    run = await generate_financial_forecast(
        db,
        horizon_days=horizon_days,
        force=force_forecast,
    )
    snapshot = forecast_snapshot(run)
    data = await _forecast_inputs(db, run.horizon_days)
    plans: list[FinancialAllocationPlan] = []
    for scope_snapshot in snapshot.get("scopes", []):
        plans.append(
            await _plan_scope_allocations(
                db,
                run=run,
                scope_snapshot=scope_snapshot,
                data=data,
                redirect_url=redirect_url,
            )
        )
    await write_audit(
        db,
        "financial_allocation_cycle_completed",
        entity_type="financial_forecast_run",
        entity_id=str(run.id),
        details={
            "forecast_status": run.status,
            "plans": len(plans),
            "reconciled_actions": reconciled,
            "resumed_pre_provider_actions": resumed,
        },
    )
    await db.commit()
    return await forecast_public(db, run)


def allocation_compatibility_summary(payload: dict[str, Any]) -> dict[str, Any]:
    actions = [
        action
        for plan in payload.get("allocation_plans", [])
        for action in plan.get("actions", [])
    ]
    initiated_states = {"initiated", "needs_user_auth", "verified"}
    blocked_states = {"failed", "creation_uncertain", "blocked_policy"}
    return {
        "enabled": True,
        "transfers_enabled": True,
        "planned": len(actions),
        "initiated": sum(1 for action in actions if action.get("status") in initiated_states),
        "blocked": sum(1 for action in actions if action.get("status") in blocked_states),
        "superseded_by": "forecast_allocation",
        "forecast_status": payload.get("status"),
    }


async def forecast_public(db: AsyncSession, run: FinancialForecastRun | None) -> dict[str, Any]:
    if run is None:
        return {
            "status": "not_generated",
            "generated_at": None,
            "horizon_days": 90,
            "scopes": [],
            "allocation_plans": [],
        }
    snapshot = forecast_snapshot(run)
    plans = list(
        (
            await db.execute(
                select(FinancialAllocationPlan)
                .where(FinancialAllocationPlan.forecast_run_id == run.id)
                .order_by(FinancialAllocationPlan.account_scope, FinancialAllocationPlan.id)
            )
        ).scalars()
    )
    account_ids: set[int] = set()
    actions_by_plan: dict[int, list[FinancialAllocationAction]] = {}
    for plan in plans:
        actions = list(
            (
                await db.execute(
                    select(FinancialAllocationAction)
                    .where(FinancialAllocationAction.allocation_plan_id == plan.id)
                    .order_by(FinancialAllocationAction.id)
                )
            ).scalars()
        )
        actions_by_plan[plan.id] = actions
        for action in actions:
            if action.source_account_id:
                account_ids.add(action.source_account_id)
            if action.destination_account_id:
                account_ids.add(action.destination_account_id)
    accounts = {
        account_id: await db.get(BankAccount, account_id)
        for account_id in account_ids
    }
    plan_rows: list[dict[str, Any]] = []
    for plan in plans:
        try:
            details = json.loads(decrypt_text(plan.details_encrypted) or "{}")
        except Exception:
            details = {}
        action_rows: list[dict[str, Any]] = []
        for action in actions_by_plan.get(plan.id, []):
            transfer = await db.get(OwnAccountTransfer, action.own_account_transfer_id) if action.own_account_transfer_id else None
            action_rows.append(
                {
                    "id": action.id,
                    "source_account_id": action.source_account_id,
                    "source_account": _scope_account_name(accounts.get(action.source_account_id)),
                    "destination_account_id": action.destination_account_id,
                    "destination_account": _scope_account_name(accounts.get(action.destination_account_id)),
                    "destination_role": action.destination_role,
                    "amount": str(action.amount),
                    "currency": action.currency,
                    "status": action.status,
                    "rationale": action.rationale,
                    "own_account_transfer_id": action.own_account_transfer_id,
                    "authorization_url": transfer.authorization_url if transfer else None,
                    "requires_user_action": bool(transfer.requires_user_action) if transfer else False,
                }
            )
        plan_rows.append(
            {
                "id": plan.id,
                "scope": plan.account_scope,
                "status": plan.status,
                "starting_cash": str(plan.starting_cash),
                "protected_floor": str(plan.protected_floor),
                "base_min_cash": str(plan.base_min_cash),
                "conservative_min_cash": str(plan.conservative_min_cash),
                "allocatable_surplus": str(plan.allocatable_surplus),
                "details": details,
                "actions": action_rows,
                "created_at": plan.created_at.isoformat(),
                "updated_at": plan.updated_at.isoformat(),
            }
        )
    snapshot.update(
        {
            "id": run.id,
            "input_fingerprint": run.input_fingerprint,
            "allocation_plans": plan_rows,
        }
    )
    return snapshot
