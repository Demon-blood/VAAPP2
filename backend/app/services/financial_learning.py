from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import BankAccount, BankStatementImport, BankTransaction, HistoricalFinancialTransaction


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _semantic_kind(direction: str, category: str, income_kind: str, text: str, amount: Decimal) -> str:
    normalized = f" {text.casefold()} "
    if direction == "credit":
        if income_kind == "salary" or any(term in normalized for term in (" loon ", "salary", "salaris", "wage")):
            return "salary"
        if income_kind:
            return income_kind
        return "income"
    if category == "family_support" or any(term in normalized for term in ("onderhoudsbijdrage", "child support", "maintenance contribution")):
        return "child_support"
    if category == "housing":
        return "housing_contribution"
    if "bijdrage" in normalized and amount >= Decimal("150"):
        return "housing_contribution"
    if category == "subscriptions":
        return "subscription"
    if category == "utilities":
        return "utility"
    if category == "insurance":
        return "insurance"
    return category or "other"


def _next_monthly_date(days: list[int], last_seen: datetime) -> datetime:
    typical_day = max(1, min(28, int(round(median(days))) if days else last_seen.day))
    today = date.today()
    candidate = datetime(today.year, today.month, typical_day)
    if candidate.date() <= today:
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        candidate = datetime(year, month, typical_day)
    return candidate


async def learn_recurring_cashflows(db: AsyncSession, *, days: int = 260) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=max(120, days))
    evidence: list[dict[str, Any]] = []
    live = list(
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
    for tx, account in live:
        if tx.booking_date is None:
            continue
        text = f"{tx.counterparty_name} {tx.remittance}"
        kind = _semantic_kind(tx.direction, tx.category, "", text, _money(tx.amount))
        evidence.append(
            {
                "scope": account.account_scope,
                "date": tx.booking_date,
                "direction": tx.direction,
                "amount": _money(tx.amount),
                "counterparty": tx.counterparty_name,
                "iban": tx.counterparty_iban,
                "text": text,
                "kind": kind,
            }
        )
    historical = list(
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
    for tx in historical:
        text = f"{tx.counterparty_name} {tx.remittance}"
        kind = _semantic_kind(tx.direction, tx.category, tx.income_kind, text, _money(tx.amount))
        evidence.append(
            {
                "scope": tx.account_scope,
                "date": tx.booking_date,
                "direction": tx.direction,
                "amount": _money(tx.amount),
                "counterparty": tx.counterparty_name,
                "iban": tx.counterparty_iban,
                "text": text,
                "kind": kind,
            }
        )

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        party_key = _norm(row["iban"]) or _norm(row["counterparty"]) or _norm(row["text"])[:80]
        if not party_key:
            continue
        grouped[(row["scope"], row["direction"], row["kind"], party_key)].append(row)

    recurring: list[dict[str, Any]] = []
    protected_by_scope: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for (scope, direction, kind, _), rows in grouped.items():
        rows.sort(key=lambda item: item["date"])
        month_totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
        days_of_month: list[int] = []
        for row in rows:
            booked = row["date"]
            month_totals[(booked.year, booked.month)] += row["amount"]
            days_of_month.append(booked.day)
        month_count = len(month_totals)
        if month_count < 3:
            continue
        recent_months = sorted(month_totals)[-6:]
        amounts = [month_totals[key] for key in recent_months]
        average = (sum(amounts, Decimal("0")) / Decimal(len(amounts))).quantize(Decimal("0.01"))
        if average <= 0:
            continue
        last_seen = rows[-1]["date"]
        if last_seen < datetime.utcnow() - timedelta(days=75):
            continue
        next_due = _next_monthly_date(days_of_month[-6:], last_seen)
        confidence = min(Decimal("0.99"), Decimal("0.70") + Decimal(min(month_count, 6)) * Decimal("0.05"))
        if kind in {"salary", "child_support"}:
            confidence = max(confidence, Decimal("0.95"))
        protected = direction == "debit" and kind in {
            "child_support",
            "housing_contribution",
            "subscription",
            "utility",
            "insurance",
        }
        if protected and next_due <= datetime.utcnow() + timedelta(days=30):
            protected_by_scope[scope] += average
        recurring.append(
            {
                "scope": scope,
                "direction": direction,
                "kind": kind,
                "counterparty": rows[-1]["counterparty"],
                "amount": str(average),
                "months_seen": month_count,
                "last_seen": last_seen.isoformat(),
                "next_expected": next_due.isoformat(),
                "confidence": str(confidence.quantize(Decimal('0.01'))),
                "protected_obligation": protected,
            }
        )
    recurring.sort(key=lambda item: (item["scope"], item["direction"], item["next_expected"], item["kind"]))
    return {
        "items": recurring,
        "protected_next_30_days": {scope: str(value.quantize(Decimal('0.01'))) for scope, value in protected_by_scope.items()},
    }
