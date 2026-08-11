from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import Base, BankAccount, BankAutopilotPolicy, BankConnection, OwnAccountTransfer, Payment
from app.services import financial_autopilot
from app.services.cash_safety import committed_destination_balance, effective_available_balance
from app.services.financial_autopilot import categorize_transaction, create_own_account_transfer, run_budget_autopilot


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _accounts(db, *, operating_balance: str = "4000.00"):
    connection = BankConnection(
        institution_country="BE",
        institution_name="Test Bank",
        psu_type="personal",
        session_id_encrypted="opaque",
        status="active",
    )
    db.add(connection)
    await db.flush()
    operating = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="op-1",
        account_scope="personal",
        name="Current account",
        iban="BE68539007547034",
        currency="EUR",
        current_balance=Decimal(operating_balance),
        available_balance=Decimal(operating_balance),
        safety_reserve=Decimal("1000.00"),
        enabled_for_payments=True,
    )
    savings = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="save-1",
        account_scope="personal",
        name="Savings account",
        iban="BE71096123456769",
        currency="EUR",
        current_balance=Decimal("500.00"),
        available_balance=Decimal("500.00"),
        safety_reserve=Decimal("0.00"),
        enabled_for_payments=False,
    )
    db.add_all([operating, savings])
    await db.commit()
    return operating, savings


def test_transaction_categorization_detects_own_account_and_normal_spend() -> None:
    own = {"BE71096123456769"}
    category, internal = categorize_transaction(
        {
            "credit_debit_indicator": "DBIT",
            "creditor_account": {"identification": "BE71096123456769"},
            "remittance_information": ["savings"],
        },
        own,
    )
    assert (category, internal) == ("internal_transfer", True)
    # On an incoming transaction the connected/creditor side may be one of our own
    # IBANs. The debtor is the counterparty, so this must NOT be called internal.
    category, internal = categorize_transaction(
        {
            "credit_debit_indicator": "CRDT",
            "creditor_account": {"identification": "BE71096123456769"},
            "debtor_account": {"identification": "BE12000000000000"},
            "debtor": {"name": "Employer"},
        },
        own,
    )
    assert internal is False
    category, internal = categorize_transaction(
        {"creditor": {"name": "Colruyt"}, "remittance_information": ["groceries"]}, own
    )
    assert category == "groceries"
    assert internal is False


@pytest.mark.asyncio
async def test_own_transfer_uses_explicit_debtor_iban_and_is_idempotent(db, monkeypatch) -> None:
    source, destination = await _accounts(db)
    db.add(BankAutopilotPolicy(bank_account_id=source.id, role="operating", internal_transfers_enabled=True, target_floor=Decimal("1000")))
    await db.commit()
    calls = []

    async def fake_payment(_db, **kwargs):
        calls.append(kwargs)
        return {"payment_id": "pis-1", "url": "https://bank.example/authorize"}

    monkeypatch.setattr(financial_autopilot.enable_banking, "create_sepa_payment", fake_payment)
    first = await create_own_account_transfer(
        db, source_account_id=source.id, destination_account_id=destination.id, amount=Decimal("250"),
        reason="rebalance", redirect_url="https://va.example/api/banking/transfer-callback", idempotency_key="same-key",
    )
    second = await create_own_account_transfer(
        db, source_account_id=source.id, destination_account_id=destination.id, amount=Decimal("250"),
        reason="rebalance", redirect_url="https://va.example/api/banking/transfer-callback", idempotency_key="same-key",
    )
    assert first.id == second.id
    assert len(calls) == 1
    assert calls[0]["debtor_iban"] == source.iban
    assert calls[0]["creditor_iban"] == destination.iban
    assert first.status == "authorization_required"


@pytest.mark.asyncio
async def test_network_ambiguity_blocks_blind_transfer_retry(db, monkeypatch) -> None:
    source, destination = await _accounts(db)
    db.add(BankAutopilotPolicy(bank_account_id=source.id, role="operating", internal_transfers_enabled=True, target_floor=Decimal("1000")))
    await db.commit()
    attempts = 0

    async def timeout(_db, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("provider outcome unknown")

    monkeypatch.setattr(financial_autopilot.enable_banking, "create_sepa_payment", timeout)
    first = await create_own_account_transfer(
        db, source_account_id=source.id, destination_account_id=destination.id, amount=Decimal("200"),
        reason="rebalance", redirect_url="https://va.example/cb", idempotency_key="timeout-key",
    )
    second = await create_own_account_transfer(
        db, source_account_id=source.id, destination_account_id=destination.id, amount=Decimal("200"),
        reason="rebalance", redirect_url="https://va.example/cb", idempotency_key="timeout-key",
    )
    assert first.status == "creation_uncertain"
    assert second.id == first.id
    assert attempts == 1


@pytest.mark.asyncio
async def test_budget_autopilot_respects_reserve_and_per_transfer_cap(db, monkeypatch) -> None:
    source, destination = await _accounts(db, operating_balance="4000")
    calls = []

    async def fake_payment(_db, **kwargs):
        calls.append(kwargs)
        return {"payment_id": "budget-1", "url": "https://bank.example/auth"}

    monkeypatch.setattr(financial_autopilot.enable_banking, "create_sepa_payment", fake_payment)
    outcome = await run_budget_autopilot(db, redirect_url="https://va.example/cb")
    assert outcome["initiated"] == 1
    transfer = (await db.execute(select(OwnAccountTransfer))).scalar_one()
    assert transfer.amount <= Decimal("1000.00")
    assert source.available_balance - transfer.amount >= source.safety_reserve
    assert calls[0]["debtor_iban"] == source.iban


@pytest.mark.asyncio
async def test_effective_balance_reserves_pending_and_unreflected_completed_outflows(db) -> None:
    source, destination = await _accounts(db)
    synced = datetime.utcnow()
    source.last_synced_at = synced
    db.add_all(
        [
            Payment(
                bill_id=9001,
                bank_account_id=source.id,
                amount=Decimal("300.00"),
                currency="EUR",
                status="pending",
                updated_at=synced - timedelta(days=2),
            ),
            Payment(
                bill_id=9002,
                bank_account_id=source.id,
                amount=Decimal("50.00"),
                currency="EUR",
                status="completed",
                updated_at=synced - timedelta(minutes=1),
            ),
            OwnAccountTransfer(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("200.00"),
                currency="EUR",
                reason="pending reserve",
                idempotency_key="cash-reserve-pending",
                status="authorization_required",
                updated_at=synced - timedelta(days=1),
            ),
            OwnAccountTransfer(
                source_account_id=source.id,
                destination_account_id=destination.id,
                amount=Decimal("100.00"),
                currency="EUR",
                reason="completed after balance snapshot",
                idempotency_key="cash-reserve-completed",
                status="completed",
                updated_at=synced + timedelta(seconds=1),
            ),
        ]
    )
    await db.commit()

    effective = await effective_available_balance(db, source)
    destination_committed = await committed_destination_balance(db, destination)

    # The old completed payment was already captured by the balance sync. The
    # pending payment, pending transfer and transfer completed after the sync
    # remain reserved until a newer bank balance arrives.
    assert effective == Decimal("3400.00")
    assert destination_committed == Decimal("800.00")
