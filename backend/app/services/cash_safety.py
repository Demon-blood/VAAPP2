from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import BankAccount, OwnAccountTransfer, Payment, Task

_FAILED_STATUSES = {
    "failed",
    "cancelled",
    "canceled",
    "rejected",
    "rjct",
    "canc",
    "cncl",
    "fail",
}
_COMPLETED_STATUS = "completed"


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _is_locally_committed(status: str, updated_at, last_synced_at) -> bool:
    """Whether an outflow/inflow must still be reserved around a cached bank balance.

    Non-final provider states are always treated as committed. A completed movement
    remains reserved until a bank balance sync newer than that local completion has
    occurred. This deliberately prefers temporary under-spending over double-spending
    when a provider response and an AIS balance update arrive at different times.
    """

    normalized = str(status or "").strip().lower()
    if normalized in _FAILED_STATUSES:
        return False
    if normalized != _COMPLETED_STATUS:
        return True
    if last_synced_at is None:
        return True
    return updated_at is None or updated_at > last_synced_at


async def reserved_outflows(db: AsyncSession, account: BankAccount) -> Decimal:
    """Return VA-initiated money that must still be held against this source account."""

    total = Decimal("0.00")
    payment_rows = (
        await db.execute(
            select(Payment.amount, Payment.status, Payment.updated_at).where(
                Payment.bank_account_id == account.id
            )
        )
    ).all()
    for amount, status, updated_at in payment_rows:
        if _is_locally_committed(status, updated_at, account.last_synced_at):
            total += _money(amount)

    transfer_rows = (
        await db.execute(
            select(OwnAccountTransfer.amount, OwnAccountTransfer.status, OwnAccountTransfer.updated_at).where(
                OwnAccountTransfer.source_account_id == account.id
            )
        )
    ).all()
    for amount, status, updated_at in transfer_rows:
        if _is_locally_committed(status, updated_at, account.last_synced_at):
            total += _money(amount)

    return total.quantize(Decimal("0.01"))


async def effective_available_balance(db: AsyncSession, account: BankAccount) -> Decimal | None:
    """Cached bank balance minus locally committed, potentially unreflected outflows."""

    base = account.available_balance if account.available_balance is not None else account.current_balance
    if base is None:
        return None
    return (_money(base) - await reserved_outflows(db, account)).quantize(Decimal("0.01"))


async def committed_destination_balance(db: AsyncSession, account: BankAccount) -> Decimal | None:
    """Balance used for target planning, including own-account transfers already on the way.

    This helper is intentionally *not* used to authorize spending. It only prevents
    multiple source accounts from over-funding the same savings/reserve target before
    the destination bank balance has caught up with an initiated transfer.
    """

    base = account.available_balance if account.available_balance is not None else account.current_balance
    if base is None:
        return None
    incoming = Decimal("0.00")
    rows = (
        await db.execute(
            select(OwnAccountTransfer.amount, OwnAccountTransfer.status, OwnAccountTransfer.updated_at).where(
                OwnAccountTransfer.destination_account_id == account.id
            )
        )
    ).all()
    for amount, status, updated_at in rows:
        if _is_locally_committed(status, updated_at, account.last_synced_at):
            incoming += _money(amount)
    return (_money(base) + incoming).quantize(Decimal("0.01"))


async def quarantine_stale_creation_intents(
    db: AsyncSession, *, stale_minutes: int = 15
) -> dict[str, int]:
    """Turn crash-window `creating` intents into non-retryable reconciliation exceptions.

    The local intent is deliberately written before the provider POST. If a process
    dies around that POST, we cannot know whether the bank accepted it. After a
    bounded grace period, never retry: reserve the cash and ask for reconciliation.
    """

    cutoff = datetime.utcnow() - timedelta(minutes=max(5, stale_minutes))
    result = {"payments": 0, "transfers": 0}

    payments = list(
        (
            await db.execute(
                select(Payment).where(Payment.status == "creating", Payment.updated_at < cutoff)
            )
        ).scalars()
    )
    for payment in payments:
        payment.status = "creation_uncertain"
        payment.requires_user_action = True
        payment.failure_reason = (
            "Payment creation was interrupted before the provider outcome was recorded; "
            "automatic retry is blocked until the bank is reconciled."
        )
        existing = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "payment_creation_uncertain",
                    Task.source_id == str(payment.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Task(
                    title="Check bank before retrying interrupted payment",
                    description=payment.failure_reason,
                    source_type="payment_creation_uncertain",
                    source_id=str(payment.id),
                    priority="urgent",
                    requires_approval=True,
                )
            )
        result["payments"] += 1

    transfers = list(
        (
            await db.execute(
                select(OwnAccountTransfer).where(
                    OwnAccountTransfer.status == "creating",
                    OwnAccountTransfer.updated_at < cutoff,
                )
            )
        ).scalars()
    )
    for transfer in transfers:
        transfer.status = "creation_uncertain"
        transfer.requires_user_action = True
        transfer.failure_reason = (
            "Own-account transfer creation was interrupted before the provider outcome was recorded; "
            "automatic retry is blocked until the bank is reconciled."
        )
        existing = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bank_transfer_uncertain",
                    Task.source_id == str(transfer.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Task(
                    title="Check bank before retrying interrupted own-account transfer",
                    description=transfer.failure_reason,
                    source_type="bank_transfer_uncertain",
                    source_id=str(transfer.id),
                    priority="urgent",
                    requires_approval=True,
                )
            )
        result["transfers"] += 1

    if result["payments"] or result["transfers"]:
        await db.commit()
    return result
