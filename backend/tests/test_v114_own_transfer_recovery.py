from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.entities import (
    BankAccount,
    BankAutopilotPolicy,
    BankConnection,
    OwnAccountTransfer,
    OwnAccountTransferRecoveryEvidence,
    Task,
)
from app.services import financial_autopilot, own_transfer_recovery
from app.services.financial_autopilot import create_own_account_transfer
from app.services.own_transfer_recovery import reconcile_uncertain_own_account_transfer


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _accounts(db):
    connection = BankConnection(
        institution_country="BE",
        institution_name="Recovery Test Bank",
        psu_type="personal",
        session_id_encrypted="opaque",
        status="active",
    )
    db.add(connection)
    await db.flush()
    source = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="source-ext",
        account_scope="personal",
        name="Operating",
        iban="BE68539007547034",
        currency="EUR",
        available_balance=Decimal("5000.00"),
        current_balance=Decimal("5000.00"),
        safety_reserve=Decimal("1000.00"),
        enabled_for_payments=True,
    )
    destination = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="destination-ext",
        account_scope="personal",
        name="Savings",
        iban="BE71096123456769",
        currency="EUR",
        available_balance=Decimal("500.00"),
        current_balance=Decimal("500.00"),
        enabled_for_payments=False,
    )
    db.add_all([source, destination])
    await db.flush()
    db.add(
        BankAutopilotPolicy(
            bank_account_id=source.id,
            role="operating",
            internal_transfers_enabled=True,
            target_floor=Decimal("1000.00"),
        )
    )
    await db.commit()
    return source, destination


def _booked_transaction(*, transaction_id: str, amount: str = "200.00") -> dict:
    return {
        "transaction_id": transaction_id,
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "booking_date": datetime.now(UTC).date().isoformat(),
        "creditor_account": {"iban": "BE71096123456769"},
        "remittance_information": ["Full-Time VA budget transfer"],
    }


@pytest.mark.asyncio
async def test_network_creation_uncertainty_stays_va_owned_without_fake_task(db, monkeypatch) -> None:
    source, destination = await _accounts(db)
    attempts = 0

    async def timeout(_db, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("provider outcome unknown")

    monkeypatch.setattr(financial_autopilot.enable_banking, "create_sepa_payment", timeout)
    first = await create_own_account_transfer(
        db,
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("200.00"),
        reason="rebalance",
        redirect_url="https://va.example/cb",
        idempotency_key="v114-timeout",
    )
    second = await create_own_account_transfer(
        db,
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("200.00"),
        reason="rebalance",
        redirect_url="https://va.example/cb",
        idempotency_key="v114-timeout",
    )

    tasks = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source_type == "bank_transfer_uncertain",
                    Task.source_id == str(first.id),
                )
            )
        ).scalars()
    )
    assert first.status == "creation_uncertain"
    assert first.requires_user_action is False
    assert first.authorization_url is None
    assert second.id == first.id
    assert attempts == 1
    assert tasks == []


@pytest.mark.asyncio
async def test_missing_payment_identifier_suppresses_unbound_authorization_url(db, monkeypatch) -> None:
    source, destination = await _accounts(db)

    async def missing_id(_db, **_kwargs):
        return {"url": "https://bank.example/authorize-unbound"}

    monkeypatch.setattr(financial_autopilot.enable_banking, "create_sepa_payment", missing_id)
    transfer = await create_own_account_transfer(
        db,
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("175.00"),
        reason="rebalance",
        redirect_url="https://va.example/cb",
        idempotency_key="v114-missing-id",
    )
    tasks = list(
        (
            await db.execute(
                select(Task).where(Task.source_type == "bank_transfer_uncertain")
            )
        ).scalars()
    )

    assert transfer.status == "creation_uncertain"
    assert transfer.external_payment_id is None
    assert transfer.authorization_url is None
    assert transfer.requires_user_action is False
    assert tasks == []


@pytest.mark.asyncio
async def test_unique_booked_transaction_recovers_transfer_and_closes_legacy_task(db, monkeypatch) -> None:
    source, destination = await _accounts(db)
    transfer = OwnAccountTransfer(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("200.00"),
        currency="EUR",
        reason="rebalance",
        idempotency_key="v114-recover",
        status="creation_uncertain",
        requires_user_action=True,
        authorization_url="https://bank.example/unbound",
    )
    db.add(transfer)
    await db.flush()
    task = Task(
        title="Check bank before retrying own-account transfer",
        description="legacy fake boundary",
        source_type="bank_transfer_uncertain",
        source_id=str(transfer.id),
        priority="urgent",
        requires_approval=True,
    )
    db.add(task)
    await db.commit()

    async def transactions(_db, _account_id, **_kwargs):
        return {"transactions": [_booked_transaction(transaction_id="tx-v114-1")]}

    monkeypatch.setattr(own_transfer_recovery.enable_banking, "get_account_transactions", transactions)
    outcome = await reconcile_uncertain_own_account_transfer(db, transfer)

    assert outcome["state"] == "recovered"
    assert outcome["transaction_id"] == "tx-v114-1"
    assert transfer.status == "completed"
    assert transfer.requires_user_action is False
    assert transfer.authorization_url is None
    assert transfer.failure_reason == ""
    assert task.status == "completed"
    evidence = (
        await db.execute(
            select(OwnAccountTransferRecoveryEvidence).where(
                OwnAccountTransferRecoveryEvidence.transfer_id == transfer.id
            )
        )
    ).scalar_one()
    assert evidence.transaction_id == "tx-v114-1"


@pytest.mark.asyncio
async def test_multiple_exact_candidates_remain_va_owned_without_guessing(db, monkeypatch) -> None:
    source, destination = await _accounts(db)
    transfer = OwnAccountTransfer(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("200.00"),
        currency="EUR",
        reason="rebalance",
        idempotency_key="v114-ambiguous",
        status="creation_uncertain",
        requires_user_action=True,
    )
    db.add(transfer)
    await db.commit()

    async def transactions(_db, _account_id, **_kwargs):
        return {
            "transactions": [
                _booked_transaction(transaction_id="tx-v114-a"),
                _booked_transaction(transaction_id="tx-v114-b"),
            ]
        }

    monkeypatch.setattr(own_transfer_recovery.enable_banking, "get_account_transactions", transactions)
    outcome = await reconcile_uncertain_own_account_transfer(db, transfer)

    assert outcome["state"] == "ambiguous"
    assert outcome["candidate_count"] == 2
    assert transfer.status == "creation_uncertain"
    assert transfer.requires_user_action is False
    assert transfer.external_payment_id is None


@pytest.mark.asyncio
async def test_booked_transaction_cannot_recover_two_transfer_intents(db, monkeypatch) -> None:
    source, destination = await _accounts(db)
    first = OwnAccountTransfer(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("200.00"),
        currency="EUR",
        reason="rebalance one",
        idempotency_key="v114-binding-one",
        status="creation_uncertain",
    )
    second = OwnAccountTransfer(
        source_account_id=source.id,
        destination_account_id=destination.id,
        amount=Decimal("200.00"),
        currency="EUR",
        reason="rebalance two",
        idempotency_key="v114-binding-two",
        status="creation_uncertain",
    )
    db.add_all([first, second])
    await db.commit()

    async def transactions(_db, _account_id, **_kwargs):
        return {"transactions": [_booked_transaction(transaction_id="tx-v114-single")]}

    monkeypatch.setattr(own_transfer_recovery.enable_banking, "get_account_transactions", transactions)
    first_outcome = await reconcile_uncertain_own_account_transfer(db, first)
    second_outcome = await reconcile_uncertain_own_account_transfer(db, second)

    assert first_outcome["state"] == "recovered"
    assert second_outcome["state"] == "waiting_for_evidence"
    assert first.status == "completed"
    assert second.status == "creation_uncertain"
    assert second.requires_user_action is False
