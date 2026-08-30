from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.integrations import enable_banking
from app.models.entities import (
    BankAccount,
    BankConnection,
    Bill,
    Creditor,
    Payment,
    PaymentRecoveryEvidence,
    Task,
)
from app.services import banking_service
from app.services.banking_service import create_payment_for_bill
from app.services.payment_recovery import reconcile_uncertain_payment


def _dt(hour: int = 12) -> datetime:
    return datetime(2026, 8, 30, hour, 0, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _payment_fixture(db, *, status: str = "creation_uncertain", requires_user: bool = True):
    connection = BankConnection(
        institution_country="BE",
        institution_name="Example Bank",
        psu_type="personal",
        session_id_encrypted="encrypted",
        status="active",
    )
    db.add(connection)
    await db.flush()
    account = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="account-1",
        account_scope="personal",
        name="Current",
        iban="BE00000000000000",
        currency="EUR",
        current_balance=Decimal("1000.00"),
        available_balance=Decimal("1000.00"),
        safety_reserve=Decimal("100.00"),
        enabled_for_payments=True,
    )
    db.add(account)
    creditor = Creditor(
        name="Proximus Belgium",
        iban="BE11111111111111",
        account_scope="personal",
        auto_pay_enabled=True,
        max_auto_amount=Decimal("500.00"),
    )
    db.add(creditor)
    await db.flush()
    bill = Bill(
        creditor_id=creditor.id,
        creditor_name=creditor.name,
        iban=creditor.iban,
        amount=Decimal("89.99"),
        currency="EUR",
        reference="INV-2026-44321",
        invoice_number="44321",
        account_scope="personal",
        status="payment_initiated" if status == "creation_uncertain" else "validated",
    )
    db.add(bill)
    await db.flush()
    payment = Payment(
        bill_id=bill.id,
        bank_account_id=account.id,
        amount=bill.amount,
        currency=bill.currency,
        status=status,
        requires_user_action=requires_user,
        failure_reason="uncertain",
        created_at=_dt(),
    )
    db.add(payment)
    await db.commit()
    return bill, account, creditor, payment


@pytest.mark.asyncio
async def test_unique_booked_transaction_recovers_uncertain_payment_and_closes_legacy_task(db, monkeypatch):
    bill, _, _, payment = await _payment_fixture(db)
    db.add(
        Task(
            title="Check bank",
            source_type="payment_creation_uncertain",
            source_id=str(payment.id),
            priority="urgent",
            requires_approval=True,
        )
    )
    await db.commit()

    async def fake_transactions(*_args, **_kwargs):
        return {
            "booked": [
                {
                    "transaction_id": "tx-44321",
                    "booking_date": "2026-08-30",
                    "transaction_amount": {"amount": "-89.99", "currency": "EUR"},
                    "creditor_name": "Proximus Belgium",
                    "remittance_information": "INV-2026-44321",
                }
            ]
        }

    monkeypatch.setattr(enable_banking, "get_account_transactions", fake_transactions)
    outcome = await reconcile_uncertain_payment(db, payment)

    assert outcome["state"] == "recovered"
    assert payment.status == "completed"
    assert payment.requires_user_action is False
    assert bill.status == "paid"
    evidence_count = int(
        (
            await db.execute(
                select(func.count(PaymentRecoveryEvidence.id)).where(
                    PaymentRecoveryEvidence.payment_id == payment.id
                )
            )
        ).scalar_one()
    )
    assert evidence_count == 1
    task = (
        await db.execute(
            select(Task).where(
                Task.source_type == "payment_creation_uncertain",
                Task.source_id == str(payment.id),
            )
        )
    ).scalar_one()
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_ambiguous_matches_remain_va_owned_and_never_complete(db, monkeypatch):
    bill, _, _, payment = await _payment_fixture(db)

    async def fake_transactions(*_args, **_kwargs):
        return {
            "booked": [
                {
                    "transaction_id": "tx-a",
                    "booking_date": "2026-08-30",
                    "amount": {"amount": "89.99", "currency": "EUR"},
                    "description": "Proximus INV-2026-44321",
                },
                {
                    "transaction_id": "tx-b",
                    "booking_date": "2026-08-30",
                    "amount": {"amount": "89.99", "currency": "EUR"},
                    "description": "Proximus INV-2026-44321",
                },
            ]
        }

    monkeypatch.setattr(enable_banking, "get_account_transactions", fake_transactions)
    outcome = await reconcile_uncertain_payment(db, payment)

    assert outcome["state"] == "ambiguous"
    assert payment.status == "creation_uncertain"
    assert payment.requires_user_action is False
    assert bill.status == "payment_initiated"
    evidence_count = int(
        (
            await db.execute(
                select(func.count(PaymentRecoveryEvidence.id)).where(
                    PaymentRecoveryEvidence.payment_id == payment.id
                )
            )
        ).scalar_one()
    )
    assert evidence_count == 0


@pytest.mark.asyncio
async def test_no_match_remains_active_without_fake_human_boundary(db, monkeypatch):
    _, _, _, payment = await _payment_fixture(db)

    async def fake_transactions(*_args, **_kwargs):
        return {"booked": []}

    monkeypatch.setattr(enable_banking, "get_account_transactions", fake_transactions)
    outcome = await reconcile_uncertain_payment(db, payment)

    assert outcome["state"] == "waiting_for_evidence"
    assert payment.status == "creation_uncertain"
    assert payment.requires_user_action is False


@pytest.mark.asyncio
async def test_network_drop_during_creation_is_va_owned_and_creates_no_approval_task(db, monkeypatch):
    bill, account, creditor, _ = await _payment_fixture(db, status="failed", requires_user=False)
    await db.delete((await db.execute(select(Payment).where(Payment.bill_id == bill.id))).scalar_one())
    bill.status = "validated"
    await db.commit()

    async def safe_balance(*_args, **_kwargs):
        return Decimal("1000.00")

    async def fail_create(*_args, **_kwargs):
        request = httpx.Request("POST", "https://bank.example/payments")
        raise httpx.ConnectError("network dropped", request=request)

    monkeypatch.setattr(banking_service, "effective_available_balance", safe_balance)
    monkeypatch.setattr(enable_banking, "create_sepa_payment", fail_create)
    payment = await create_payment_for_bill(
        db,
        bill_id=bill.id,
        bank_account_id=account.id,
        redirect_url="https://example.invalid/callback",
    )

    assert creditor.auto_pay_enabled is True
    assert payment.status == "creation_uncertain"
    assert payment.requires_user_action is False
    tasks = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source_type == "payment_creation_uncertain",
                    Task.source_id == str(payment.id),
                )
            )
        ).scalars()
    )
    assert tasks == []


@pytest.mark.asyncio
async def test_missing_provider_payment_id_does_not_expose_authorization_url(db, monkeypatch):
    bill, account, _, _ = await _payment_fixture(db, status="failed", requires_user=False)
    await db.delete((await db.execute(select(Payment).where(Payment.bill_id == bill.id))).scalar_one())
    bill.status = "validated"
    await db.commit()

    async def safe_balance(*_args, **_kwargs):
        return Decimal("1000.00")

    async def missing_id(*_args, **_kwargs):
        return {"url": "https://bank.example/authorize", "status": "received"}

    monkeypatch.setattr(banking_service, "effective_available_balance", safe_balance)
    monkeypatch.setattr(enable_banking, "create_sepa_payment", missing_id)
    payment = await create_payment_for_bill(
        db,
        bill_id=bill.id,
        bank_account_id=account.id,
        redirect_url="https://example.invalid/callback",
    )

    assert payment.status == "creation_uncertain"
    assert payment.external_payment_id is None
    assert payment.authorization_url is None
    assert payment.requires_user_action is False
