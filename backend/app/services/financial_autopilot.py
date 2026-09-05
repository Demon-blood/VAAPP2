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
    BankStatementImport,
    BankTransaction,
    Bill,
    BudgetEnvelope,
    HistoricalFinancialTransaction,
    OAuthState,
    OwnAccountTransfer,
    Task,
)
from app.services.audit import write_audit
from app.services.cash_safety import committed_destination_balance, effective_available_balance
from app.services.financial_learning import learn_recurring_cashflows
from app.services.investment_service import investment_funding_forecast_by_scope, investment_history_summary
from app.services.runtime_config import get_runtime_value

TRANSFER_SUCCESS = {"ACSC", "ACCC", "BOOK"}
TRANSFER_FAILED = {"RJCT", "CANC", "CNCL", "FAIL"}
ACTIVE_TRANSFER_STATUSES = {"creating", "received", "pending", "authorization_required", "creation_uncertain", "acsp", "actc", "acpt"}

CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "housing": ("rent", "huur", "mortgage", "hypotheek", "syndic", "syndicus"),
    "groceries": (
        "delhaize", "colruyt", "carrefour", "aldi", "lidl", "okay", "supermarket", "supermarkt",
        "grocery", "bakkerij", "bakery", "market", "proxy ",
    ),
    "utilities": ("engie", "luminus", "water", "electric", "elektr", "gas", "proximus", "orange", "telenet", "telecom"),
    "transport": ("nmbs", "sncb", "de lijn", "mivb", "stib", "uber", "bolt", "fuel", "benzine", "diesel", "parking", "lukoil", " q8 ", "total nb"),
    # Google Play is intentionally digital spending, not automatically a subscription:
    # Beobank statements can contain many irregular Google Play purchases per day.
    "digital": ("google play", "google *google pla", "google*google play", "chamet", "powbot", "cleverbridge", "pgsharp", "xsolla", "1global.com"),
    "cash": ("geldafhaling", "cash withdrawal", " cash "),
    "money_transfer": ("remitly", "money transfer", "remittance service"),
    "subscriptions": ("subscription", "abonnement", "netflix", "spotify", "youtube premium", "apple.com/bill", "adobe", "openai *chatgpt", "premium plan fee", "metal plan fee", "uber *one membership"),
    "family_support": ("onderhoudsbijdrage", "maintenance contribution", "child support"),
    "dining": ("restaurant", "cafe", "café", "bar ", "takeaway", "deliveroo", "ubereats", "uber eats", "frit "),
    "health": ("pharmacy", "apotheek", "doctor", "arts", "hospital", "ziekenhuis", "mutualiteit"),
    "insurance": ("insurance", "verzekering", "assurance"),
    "tax": ("tax", "belasting", "fiscus", "finance.belgium", "fod financ"),
    "shopping": ("amazon", "bol.com", "zalando", "ikea", "mediamarkt", "shopping", "winkel", "temu.com", "coolblue"),
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


def categorize_transaction_text(value: str) -> str:
    text = f" {value.casefold()} "
    for category, terms in CATEGORY_TERMS.items():
        if any(term in text for term in terms):
            return category
    return "other"


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
    )
    return categorize_transaction_text(text), False


def _transaction_identity(account_id: int, item: dict[str, Any]) -> str:
    explicit = str(item.get("transaction_id") or "").strip()
    if explicit:
        return explicit[:255]
    material = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(f"{account_id}:{material}".encode()).hexdigest()


async def recategorize_bank_transaction_history(db: AsyncSession) -> dict[str, int]:
    accounts = {account.id: account for account in (await db.execute(select(BankAccount))).scalars()}
    own_ibans = {_iban(account.iban) for account in accounts.values() if account.iban}
    own_by_iban = {_iban(account.iban): account for account in accounts.values() if account.iban}
    connections = {row.id: row for row in (await db.execute(select(BankConnection))).scalars()}
    revolut_account_ids = {
        account.id
        for account in accounts.values()
        if "revolut" in (account.name or "").casefold()
        or "revolut" in (connections.get(account.bank_connection_id).institution_name.casefold()
                            if connections.get(account.bank_connection_id) else "")
    }
    rows = list((await db.execute(select(BankTransaction))).scalars())
    changed = 0
    internal_marked = 0
    for row in rows:
        source = accounts.get(row.bank_account_id)
        exact_internal = bool(row.counterparty_iban and _iban(row.counterparty_iban) in own_ibans)
        revolut_alias = bool(
            source is not None
            and source.id not in revolut_account_ids
            and revolut_account_ids
            and "revolut" in (row.counterparty_name or "").casefold()
        )
        revolut_internal = bool(
            source is not None
            and source.id in revolut_account_ids
            and any(term in f"{row.counterparty_name} {row.remittance}".casefold() for term in ("robo portfolio", "exchanged to "))
        )
        is_internal = exact_internal or revolut_alias or revolut_internal
        normalized_text = f"{row.counterparty_name} {row.remittance}".casefold()
        counterparty_account = own_by_iban.get(_iban(row.counterparty_iban)) if row.counterparty_iban else None
        if exact_internal and source is not None and counterparty_account is not None and source.account_scope != counterparty_account.account_scope:
            if source.account_scope == "pro" and row.direction == "debit":
                category = "owner_draw"
            elif source.account_scope == "personal" and row.direction == "debit":
                category = "owner_contribution"
            else:
                category = "owner_transfer"
        elif is_internal and source is not None and source.id in revolut_account_ids and "robo portfolio" in normalized_text:
            category = "investment_contribution"
        elif is_internal and source is not None and source.id in revolut_account_ids and "exchanged to " in normalized_text:
            category = "internal_fx"
        else:
            category = "internal_transfer" if is_internal else categorize_transaction_text(normalized_text)
        if row.is_internal_transfer != is_internal or row.category != category:
            row.is_internal_transfer = is_internal
            row.category = category
            changed += 1
            if is_internal:
                internal_marked += 1
    if changed:
        await db.commit()
    return {"reviewed": len(rows), "changed": changed, "internal_marked": internal_marked}


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
    recategorized = await recategorize_bank_transaction_history(db)
    return {"created": created, "updated": updated, "pages": pages, "recategorized": recategorized["changed"]}


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


def _derived_role(account: BankAccount, institution_name: str = "") -> str:
    name = f"{account.name} {institution_name}".casefold()
    if any(term in name for term in ("emergency", "reserve")):
        return "reserve"
    if any(term in name for term in ("saving", "savings", "spaar", "vault")):
        return "savings"
    if any(term in name for term in ("tax", "belasting", "btw", "vat")):
        return "tax"
    # A personal Revolut current account is the controlled day-to-day spending
    # wallet in the learned cash architecture. Revolut Pro remains a business
    # operating account because its Uber income and business reserves live there.
    if "revolut" in name and account.account_scope == "personal":
        return "spending"
    return "operating"


async def repair_legacy_account_scopes(db: AsyncSession) -> int:
    """Repair the old UI's `reserve` account scope without altering account ownership semantics.

    Scope is Personal/Pro. Reserve is a Financial Autopilot role. Earlier Android builds
    accidentally exposed `reserve` as a scope, which could make safe own-account transfers
    impossible because the transfer engine correctly refuses cross-scope movement.
    """
    accounts = list((await db.execute(select(BankAccount).where(BankAccount.account_scope == "reserve"))).scalars())
    if not accounts:
        return 0
    policies = {
        row.bank_account_id: row
        for row in (await db.execute(select(BankAutopilotPolicy))).scalars()
    }
    for account in accounts:
        account.account_scope = "personal"
        policy = policies.get(account.id)
        if policy is None:
            policy = BankAutopilotPolicy(bank_account_id=account.id)
            db.add(policy)
            policies[account.id] = policy
        policy.role = "reserve"
        policy.internal_transfers_enabled = False
        policy.accept_surplus = True
        policy.target_floor = max(_money(policy.target_floor), _money(account.safety_reserve))
    await write_audit(
        db,
        "legacy_bank_scope_repaired",
        entity_type="bank_account",
        details={"count": len(accounts), "old_scope": "reserve", "new_scope": "personal", "role": "reserve"},
    )
    await db.commit()
    return len(accounts)


async def repair_v080_default_account_roles(db: AsyncSession) -> int:
    """One-time-safe migration between Personal Revolut spending and Revolut Pro operating.

    Only policies that still look auto-seeded are changed. Explicit user-edited
    policies are left alone. Revolut Pro is a professional account inside the
    personal Revolut app and must remain Pro/operating rather than spending.
    """
    accounts = {account.id: account for account in (await db.execute(select(BankAccount))).scalars()}
    connections = {row.id: row for row in (await db.execute(select(BankConnection))).scalars()}
    policies = list((await db.execute(select(BankAutopilotPolicy))).scalars())
    changed = 0
    for policy in policies:
        account = accounts.get(policy.bank_account_id)
        if account is None:
            continue
        connection = connections.get(account.bank_connection_id)
        name = f"{account.name} {connection.institution_name if connection else ''}".casefold()
        if "revolut" not in name:
            continue

        # If an account was originally synced under the old blanket Personal rule
        # and later identified as Revolut Pro, undo only the default-like spending
        # policy. User-customised policies are intentionally preserved.
        if account.account_scope == "pro" and policy.role == "spending":
            old_spending_default = (
                policy.accept_surplus
                and not policy.internal_transfers_enabled
                and _money(policy.target_floor) == Decimal("0.00")
                and _money(policy.target_ceiling) == Decimal("0.00")
                and _money(policy.monthly_outbound_limit) == Decimal("5000.00")
                and _money(policy.min_transfer_amount) == Decimal("50.00")
            )
            if old_spending_default:
                policy.role = "operating"
                policy.internal_transfers_enabled = bool(account.enabled_for_payments)
                policy.accept_surplus = False
                policy.target_floor = max(_money(account.safety_reserve), Decimal("0.00"))
                changed += 1
            continue

        if account.account_scope != "personal" or policy.role != "operating":
            continue
        default_like = (
            not policy.accept_surplus
            and _money(policy.target_ceiling) == Decimal("0.00")
            and _money(policy.monthly_outbound_limit) == Decimal("5000.00")
            and _money(policy.min_transfer_amount) == Decimal("50.00")
            and _money(policy.target_floor) in {Decimal("0.00"), _money(account.safety_reserve)}
        )
        if not default_like:
            continue
        policy.role = "spending"
        policy.internal_transfers_enabled = False
        policy.accept_surplus = True
        policy.target_floor = Decimal("0.00")
        changed += 1
    if changed:
        await write_audit(
            db,
            "v080_revolut_spending_role_migrated",
            entity_type="bank_autopilot_policy",
            details={"count": changed},
        )
        await db.commit()
    return changed


async def ensure_account_autopilot_policies(db: AsyncSession) -> list[BankAutopilotPolicy]:
    await repair_legacy_account_scopes(db)
    await repair_v080_default_account_roles(db)
    accounts = list((await db.execute(select(BankAccount))).scalars())
    connections = {row.id: row for row in (await db.execute(select(BankConnection))).scalars()}
    by_account = {
        row.bank_account_id: row
        for row in (await db.execute(select(BankAutopilotPolicy))).scalars()
    }
    changed = False
    for account in accounts:
        if account.id in by_account:
            continue
        institution = connections.get(account.bank_connection_id)
        role = _derived_role(account, institution.institution_name if institution else "")
        policy = BankAutopilotPolicy(
            bank_account_id=account.id,
            role=role,
            internal_transfers_enabled=bool(account.enabled_for_payments and role == "operating"),
            target_floor=max(_money(account.safety_reserve), Decimal("0.00")) if role == "operating" else Decimal("0.00"),
            target_ceiling=Decimal("0.00"),
            accept_surplus=role in {"spending", "savings", "reserve", "tax"},
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


async def _learning_transactions(
    db: AsyncSession,
    *,
    since: datetime,
) -> list[tuple[str, datetime, str, Decimal, str, bool]]:
    """Return deduplicated live + statement evidence for budgeting.

    Statement rows that have matched an Enable Banking transaction are excluded so one
    monetary event is never counted twice. Historical rows remain useful when the live
    provider no longer exposes the older transaction window.
    """
    result: list[tuple[str, datetime, str, Decimal, str, bool]] = []
    live_rows = list(
        (
            await db.execute(
                select(BankTransaction, BankAccount)
                .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
                .where(
                    BankTransaction.booking_date >= since,
                    BankTransaction.is_internal_transfer.is_(False),
                )
            )
        ).all()
    )
    for tx, account in live_rows:
        if tx.booking_date is None:
            continue
        refund_text = f"{tx.counterparty_name} {tx.remittance}".casefold()
        result.append((
            account.account_scope,
            tx.booking_date,
            tx.category,
            _money(tx.amount),
            tx.direction,
            tx.direction == "credit" and any(term in refund_text for term in ("refund", "reversal", "reverted")),
        ))

    historical_rows = list(
        (
            await db.execute(
                select(HistoricalFinancialTransaction).where(
                    HistoricalFinancialTransaction.booking_date >= since,
                    HistoricalFinancialTransaction.matched_bank_transaction_id.is_(None),
                    HistoricalFinancialTransaction.is_internal_transfer.is_(False),
                )
            )
        ).scalars()
    )
    for tx in historical_rows:
        result.append((
            tx.account_scope,
            tx.booking_date,
            tx.category,
            _money(tx.amount),
            tx.direction,
            tx.income_kind == "refund" or "refund" in tx.transaction_type.casefold(),
        ))
    return result


async def _learned_monthly_spend(
    db: AsyncSession,
    *,
    days: int = 180,
) -> tuple[dict[str, Decimal], dict[tuple[str, str], Decimal]]:
    since = datetime.utcnow() - timedelta(days=max(30, days))
    rows = await _learning_transactions(db, since=since)
    scope_months: dict[str, set[tuple[int, int]]] = {}
    scope_totals: dict[str, Decimal] = {}
    category_totals: dict[tuple[str, str], Decimal] = {}
    for scope, booked, category, amount, direction, is_refund in rows:
        scope_months.setdefault(scope, set()).add((booked.year, booked.month))
        key = (scope, category)
        if direction == "debit":
            scope_totals[scope] = scope_totals.get(scope, Decimal("0.00")) + amount
            category_totals[key] = category_totals.get(key, Decimal("0.00")) + amount
        elif is_refund:
            scope_totals[scope] = max(Decimal("0.00"), scope_totals.get(scope, Decimal("0.00")) - amount)
            category_totals[key] = max(Decimal("0.00"), category_totals.get(key, Decimal("0.00")) - amount)

    monthly_scope: dict[str, Decimal] = {}
    monthly_category: dict[tuple[str, str], Decimal] = {}
    for scope, total in scope_totals.items():
        months = Decimal(max(1, len(scope_months.get(scope, set()))))
        monthly_scope[scope] = (total / months).quantize(Decimal("0.01"))
    for key, total in category_totals.items():
        months = Decimal(max(1, len(scope_months.get(key[0], set()))))
        monthly_category[key] = (total / months * Decimal("1.05")).quantize(Decimal("0.01"))
    return monthly_scope, monthly_category


async def monthly_spend_by_scope(db: AsyncSession, *, days: int = 180) -> dict[str, Decimal]:
    monthly_scope, _ = await _learned_monthly_spend(db, days=days)
    return monthly_scope


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
    rows = await _learning_transactions(db, since=month_start)
    totals: dict[str, Decimal] = {}
    for scope, _, _, amount, direction, is_refund in rows:
        if direction == "credit" and not is_refund:
            totals[scope] = totals.get(scope, Decimal("0.00")) + amount
    return totals


async def budget_cash_plan_by_scope(
    db: AsyncSession,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal]]:
    """Return monthly budget plan, reserve targets, and income-allocation percentages.

    Explicit monthly limits win. Categories left at zero learn a conservative 105%
    allowance from up to six months of deduplicated Enable Banking + imported statement
    history. Reserve targets remain a separate cash-safety floor.
    """
    await ensure_default_budget_envelopes(db, "personal")
    await ensure_default_budget_envelopes(db, "pro")
    envelopes = list(
        (await db.execute(select(BudgetEnvelope).where(BudgetEnvelope.enabled.is_(True)))).scalars()
    )
    _, learned_categories = await _learned_monthly_spend(db, days=180)
    monthly_plan: dict[str, Decimal] = {}
    reserve_targets: dict[str, Decimal] = {}
    allocation_percent: dict[str, Decimal] = {}
    for envelope in envelopes:
        configured = _money(envelope.monthly_limit)
        effective = configured if configured > 0 else learned_categories.get(
            (envelope.account_scope, envelope.category), Decimal("0.00")
        )
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
        transfer.requires_user_action = False
        transfer.failure_reason = f"Provider creation outcome is uncertain; automatic retry blocked: {exc}"[:2000]
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
        # An authorization URL without a provider payment ID cannot be bound to
        # a specific transfer safely. Keep the uncertainty VA-owned and reconcile
        # independent booked-bank evidence instead of exposing a fake user boundary.
        transfer.authorization_url = None
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = False
        transfer.failure_reason = "Provider returned success without a payment identifier; automatic retry is blocked."
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
    if transfer.status == "creation_uncertain" and not transfer.external_payment_id:
        from app.services.own_transfer_recovery import reconcile_uncertain_own_account_transfer

        await reconcile_uncertain_own_account_transfer(db, transfer)
        return transfer
    if not transfer.external_payment_id:
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


async def _spending_target_for_account(
    db: AsyncSession,
    account: BankAccount,
    policy: BankAutopilotPolicy,
    *,
    monthly_investment_funding: Decimal,
) -> Decimal:
    """Return a one-week cash target for a spending wallet.

    Revolut Personal is funded as a controlled wallet, not as a savings sink. The
    target learns recent non-internal debits and adds one week of the investment
    cash that Revolut's own scheduler is expected to move into its portfolios.
    Explicit floor/ceiling values remain authoritative minimum targets.
    """
    since = datetime.utcnow() - timedelta(days=56)
    live_total = (
        await db.execute(
            select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
                BankTransaction.bank_account_id == account.id,
                BankTransaction.booking_date >= since,
                BankTransaction.direction == "debit",
                BankTransaction.is_internal_transfer.is_(False),
            )
        )
    ).scalar_one()
    historical_total = (
        await db.execute(
            select(func.coalesce(func.sum(HistoricalFinancialTransaction.amount), 0))
            .join(BankStatementImport, HistoricalFinancialTransaction.statement_import_id == BankStatementImport.id)
            .where(
                BankStatementImport.matched_bank_account_id == account.id,
                HistoricalFinancialTransaction.booking_date >= since,
                HistoricalFinancialTransaction.direction == "debit",
                HistoricalFinancialTransaction.is_internal_transfer.is_(False),
                HistoricalFinancialTransaction.matched_bank_transaction_id.is_(None),
            )
        )
    ).scalar_one()
    weekly_spend = ((_money(live_total) + _money(historical_total)) / Decimal("8")).quantize(Decimal("0.01"))
    weekly_investment = (_money(monthly_investment_funding) / Decimal("4.345")).quantize(Decimal("0.01"))
    learned = (weekly_spend * Decimal("1.20") + weekly_investment * Decimal("1.10")).quantize(Decimal("0.01"))
    configured = max(_money(policy.target_floor), _money(policy.target_ceiling))
    return max(configured, learned)


async def run_budget_autopilot(db: AsyncSession, *, redirect_url: str) -> dict[str, Any]:
    # Recovery of an already-dispatched transfer is independent of whether new
    # automatic budgeting is currently enabled. It must never require a redial/recreate.
    from app.services.own_transfer_recovery import reconcile_all_uncertain_own_account_transfers

    await reconcile_all_uncertain_own_account_transfers(db)
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
    recurring = await learn_recurring_cashflows(db)
    protected_recurring = {
        scope: _money(value) for scope, value in recurring.get("protected_next_30_days", {}).items()
    }
    investment_funding = await investment_funding_forecast_by_scope(db)
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
        and policy.role in {"spending", "savings", "reserve", "tax"}
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
            obligations.get(source.account_scope, Decimal("0.00"))
            + protected_recurring.get(source.account_scope, Decimal("0.00")),
            monthly_budget.get(source.account_scope, Decimal("0.00")),
            reserve_targets.get(source.account_scope, Decimal("0.00")),
        )
        tax_virtual_reserve = Decimal("0.00")
        if not tax_account_ids_by_scope.get(source.account_scope):
            tax_virtual_reserve = (
                _money(month_income.get(source.account_scope, Decimal("0.00")))
                * _money(allocation_percent.get(source.account_scope, Decimal("0.00")))
                / Decimal("100")
            ).quantize(Decimal("0.01"))
        dynamic_floor = (
            (_money(monthly_need) * buffer_multiplier).quantize(Decimal("0.01"))
            + _money(source.safety_reserve)
            + tax_virtual_reserve
        )
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
        spending_targets: dict[int, Decimal] = {}
        for candidate in destinations:
            committed = await committed_destination_balance(db, candidate)
            destination_balances[candidate.id] = _money(committed)
            candidate_policy = policy_by_account[candidate.id]
            if candidate_policy.role == "spending":
                spending_targets[candidate.id] = await _spending_target_for_account(
                    db,
                    candidate,
                    candidate_policy,
                    monthly_investment_funding=investment_funding.get(candidate.account_scope, Decimal("0.00")),
                )

        def destination_rank(account: BankAccount) -> tuple[int, int]:
            dest_policy = policy_by_account[account.id]
            balance = destination_balances[account.id]
            explicit_gap = _money(dest_policy.target_floor) - balance
            tax_gap = tax_gap_remaining.get(account.account_scope, Decimal("0.00"))
            spending_gap = spending_targets.get(account.id, Decimal("0.00")) - balance
            if dest_policy.role == "spending" and spending_gap > 0:
                return (0, account.id)
            if dest_policy.role == "tax" and tax_gap > 0:
                return (1, account.id)
            if dest_policy.role in {"reserve", "tax"} and explicit_gap > 0:
                return (2, account.id)
            if dest_policy.role == "savings":
                return (3, account.id)
            return (4, account.id)

        destinations.sort(key=destination_rank)
        destination = destinations[0]
        dest_policy = policy_by_account[destination.id]
        destination_balance = destination_balances[destination.id]
        explicit_gap = _money(dest_policy.target_floor) - destination_balance
        tax_gap = tax_gap_remaining.get(destination.account_scope, Decimal("0.00")) if dest_policy.role == "tax" else Decimal("0.00")
        spending_gap = (
            spending_targets.get(destination.id, Decimal("0.00")) - destination_balance
            if dest_policy.role == "spending" else Decimal("0.00")
        )
        gap = max(explicit_gap, tax_gap, spending_gap)
        if dest_policy.role == "spending" and gap <= 0:
            alternatives = [
                account for account in destinations
                if account.id != destination.id and policy_by_account[account.id].role != "spending"
            ]
            if not alternatives:
                continue
            alternatives.sort(key=destination_rank)
            destination = alternatives[0]
            dest_policy = policy_by_account[destination.id]
            destination_balance = destination_balances[destination.id]
            explicit_gap = _money(dest_policy.target_floor) - destination_balance
            tax_gap = tax_gap_remaining.get(destination.account_scope, Decimal("0.00")) if dest_policy.role == "tax" else Decimal("0.00")
            spending_gap = Decimal("0.00")
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
                    f"30-day bills {obligations.get(source.account_scope, Decimal('0.00')):.2f}; "
                    f"learned protected obligations {protected_recurring.get(source.account_scope, Decimal('0.00')):.2f}; "
                    f"virtual tax reserve {tax_virtual_reserve:.2f}; "
                    f"learned monthly investment funding {investment_funding.get(source.account_scope, Decimal('0.00')):.2f}."
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


def _aggregate_current_month(
    rows: list[tuple[str, datetime, str, Decimal, str, bool]],
) -> tuple[dict[tuple[str, str], Decimal], dict[str, Decimal]]:
    """Aggregate current-month budget totals without counting refunds as income.

    `_learning_transactions` returns six fields, including `is_refund`. Keeping the
    tuple contract in one pure helper prevents API overview regressions when the
    evidence model evolves.
    """
    spent: dict[tuple[str, str], Decimal] = {}
    income: dict[str, Decimal] = {}
    for scope, _, category, amount, direction, is_refund in rows:
        key = (scope, category)
        if direction == "debit":
            spent[key] = spent.get(key, Decimal("0.00")) + _money(amount)
        elif is_refund:
            spent[key] = max(
                Decimal("0.00"),
                spent.get(key, Decimal("0.00")) - _money(amount),
            )
        else:
            income[scope] = income.get(scope, Decimal("0.00")) + _money(amount)
    return spent, income


async def finance_overview(db: AsyncSession) -> dict[str, Any]:
    await ensure_default_budget_envelopes(db, "personal")
    await ensure_default_budget_envelopes(db, "pro")
    policies = await ensure_account_autopilot_policies(db)
    accounts = list((await db.execute(select(BankAccount).order_by(BankAccount.id))).scalars())
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_rows = await _learning_transactions(db, since=month_start)
    spent, income = _aggregate_current_month(current_rows)

    history_spend, learned_categories = await _learned_monthly_spend(db, days=180)
    envelopes = list((await db.execute(select(BudgetEnvelope).where(BudgetEnvelope.enabled.is_(True)))).scalars())
    envelope_rows = []
    for envelope in envelopes:
        actual = spent.get((envelope.account_scope, envelope.category), Decimal("0.00"))
        configured = _money(envelope.monthly_limit)
        suggested = learned_categories.get((envelope.account_scope, envelope.category), Decimal("0.00"))
        limit = configured if configured > 0 else suggested
        envelope_rows.append(
            {
                "id": envelope.id,
                "scope": envelope.account_scope,
                "category": envelope.category,
                "spent": str(actual),
                "monthly_limit": str(configured),
                "effective_limit": str(limit),
                "learned_limit": str(suggested),
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
    recurring_cashflows = await learn_recurring_cashflows(db)
    investments = await investment_history_summary(db)
    from app.services.investment_autopilot import investment_funding_transfer_summary
    investments["funding_transfers"] = await investment_funding_transfer_summary(db)
    investment_funding = await investment_funding_forecast_by_scope(db)
    pending_transfers = (
        await db.execute(
            select(func.count()).select_from(OwnAccountTransfer).where(
                OwnAccountTransfer.status.in_(ACTIVE_TRANSFER_STATUSES)
            )
        )
    ).scalar_one()
    from app.services.bank_statement_import import statement_history_summary

    statement_history = await statement_history_summary(db)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_available": str(total_available),
        "currency": "EUR",
        "month_income": {scope: str(value) for scope, value in income.items()},
        "monthly_spend_forecast": {scope: str(value) for scope, value in history_spend.items()},
        "upcoming_30_day_obligations": {scope: str(value) for scope, value in obligations.items()},
        "learned_recurring_cashflows": recurring_cashflows,
        "investment_funding_forecast": {scope: str(value) for scope, value in investment_funding.items()},
        "investments": investments,
        "pending_internal_transfers": int(pending_transfers),
        "statement_history": statement_history,
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

