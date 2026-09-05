from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.integrations.kraken_api import KrakenOrderCreationUncertainError
from app.models.entities import (
    BankAccount,
    BankConnection,
    InvestmentFundingRecoveryEvidence,
    InvestmentFundingTransfer,
    InvestmentTradeIntent,
    Task,
)
from app.services import investment_autopilot, investment_recovery
from app.services.investment_recovery import (
    prepare_kraken_trade_intent,
    reconcile_kraken_trade_intent,
    reconcile_uncertain_kraken_funding,
    stable_trade_client_order_id,
)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _source(db) -> BankAccount:
    connection = BankConnection(
        institution_country="BE",
        institution_name="Investment Recovery Bank",
        psu_type="personal",
        session_id_encrypted="opaque",
        status="active",
    )
    db.add(connection)
    await db.flush()
    source = BankAccount(
        bank_connection_id=connection.id,
        external_account_id="kraken-source-ext",
        account_scope="personal",
        name="Operating",
        iban="BE68539007547034",
        currency="EUR",
        available_balance=Decimal("5000.00"),
        current_balance=Decimal("5000.00"),
        safety_reserve=Decimal("1000.00"),
        enabled_for_payments=True,
    )
    db.add(source)
    await db.commit()
    return source


async def _transfer(
    db,
    source: BankAccount,
    *,
    key: str = "v117-transfer",
    status: str = "creation_uncertain",
) -> InvestmentFundingTransfer:
    transfer = InvestmentFundingTransfer(
        provider="kraken",
        source_bank_account_id=source.id,
        amount=Decimal("200.00"),
        currency="EUR",
        recipient_name="Kraken",
        creditor_iban="BE71096123456769",
        reference="KRAKEN-VA-REF-123",
        status=status,
        requires_user_action=True,
        authorization_url="https://bank.example/unbound",
        trade_pair="XBTEUR",
        idempotency_key=key,
    )
    db.add(transfer)
    await db.commit()
    return transfer


def _booked(transaction_id: str, *, amount: str = "200.00") -> dict:
    return {
        "transaction_id": transaction_id,
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "booking_date": datetime.now(UTC).date().isoformat(),
        "creditor_account": {"iban": "BE71096123456769"},
        "remittance_information": ["KRAKEN-VA-REF-123"],
    }


@pytest.mark.asyncio
async def test_unique_booked_bank_evidence_recovers_funding_without_needs_you(db, monkeypatch) -> None:
    source = await _source(db)
    transfer = await _transfer(db, source)
    task = Task(
        title="Check bank before retrying Kraken funding",
        description="legacy fake boundary",
        source_type="kraken_funding_uncertain",
        source_id=str(transfer.id),
        priority="urgent",
        requires_approval=True,
    )
    db.add(task)
    await db.commit()

    async def transactions(_db, _account_id, **_kwargs):
        return {"transactions": [_booked("kraken-bank-tx-1")]}

    monkeypatch.setattr(investment_recovery.enable_banking, "get_account_transactions", transactions)
    result = await reconcile_uncertain_kraken_funding(db, transfer)

    assert result["state"] == "recovered"
    assert transfer.status == "awaiting_deposit"
    assert transfer.requires_user_action is False
    assert transfer.authorization_url is None
    assert task.status == "completed"
    evidence = (
        await db.execute(
            select(InvestmentFundingRecoveryEvidence).where(
                InvestmentFundingRecoveryEvidence.transfer_id == transfer.id
            )
        )
    ).scalar_one()
    assert evidence.transaction_id == "kraken-bank-tx-1"


@pytest.mark.asyncio
async def test_multiple_bank_candidates_remain_va_owned_without_guessing(db, monkeypatch) -> None:
    source = await _source(db)
    transfer = await _transfer(db, source, key="v117-ambiguous")

    async def transactions(_db, _account_id, **_kwargs):
        return {"transactions": [_booked("tx-a"), _booked("tx-b")]}

    monkeypatch.setattr(investment_recovery.enable_banking, "get_account_transactions", transactions)
    result = await reconcile_uncertain_kraken_funding(db, transfer)

    assert result["state"] == "ambiguous"
    assert result["candidate_count"] == 2
    assert transfer.status == "creation_uncertain"
    assert transfer.requires_user_action is False
    assert transfer.authorization_url is None


@pytest.mark.asyncio
async def test_booked_bank_transaction_cannot_recover_two_funding_intents(db, monkeypatch) -> None:
    source = await _source(db)
    first = await _transfer(db, source, key="v117-bind-1")
    second = await _transfer(db, source, key="v117-bind-2")

    async def transactions(_db, _account_id, **_kwargs):
        return {"transactions": [_booked("tx-one-use")]}

    monkeypatch.setattr(investment_recovery.enable_banking, "get_account_transactions", transactions)
    first_result = await reconcile_uncertain_kraken_funding(db, first)
    second_result = await reconcile_uncertain_kraken_funding(db, second)

    assert first_result["state"] == "recovered"
    assert second_result["state"] == "waiting_for_evidence"
    assert first.status == "awaiting_deposit"
    assert second.status == "creation_uncertain"


@pytest.mark.asyncio
async def test_trade_intent_has_stable_unique_client_order_id_before_provider_call(db) -> None:
    source = await _source(db)
    transfer = await _transfer(db, source, key="v117-trade-intent", status="deposit_observed")
    client_id = stable_trade_client_order_id(transfer)
    intent = await prepare_kraken_trade_intent(
        db,
        transfer,
        pair="XBTEUR",
        eur_amount=Decimal("200.00"),
    )
    again = await prepare_kraken_trade_intent(
        db,
        transfer,
        pair="XBTEUR",
        eur_amount=Decimal("200.00"),
    )

    assert intent.id == again.id
    assert intent.client_order_id == client_id
    assert len(intent.client_order_id) == 18
    assert intent.status == "prepared"


@pytest.mark.asyncio
async def test_unique_kraken_client_order_evidence_recovers_trade_without_replay(db, monkeypatch) -> None:
    source = await _source(db)
    transfer = await _transfer(db, source, key="v117-trade-recover", status="trade_pending")
    intent = await prepare_kraken_trade_intent(
        db,
        transfer,
        pair="XBTEUR",
        eur_amount=Decimal("200.00"),
    )
    intent.status = "creation_uncertain"
    await db.commit()

    async def orders(_db, client_order_id):
        assert client_order_id == intent.client_order_id
        return [{"order_id": "O-V117-RECOVERED", "status": "closed", "source": "closed"}]

    monkeypatch.setattr(investment_recovery, "get_orders_by_client_order_id", orders)
    result = await reconcile_kraken_trade_intent(db, transfer, intent)

    assert result["state"] == "recovered"
    assert transfer.status == "invested"
    assert transfer.trade_order_id == "O-V117-RECOVERED"
    assert intent.provider_order_id == "O-V117-RECOVERED"
    assert intent.status == "verified"


@pytest.mark.asyncio
async def test_ambiguous_add_order_is_never_blindly_replayed(db, monkeypatch) -> None:
    source = await _source(db)
    transfer = await _transfer(db, source, key="v117-order-uncertain", status="deposit_observed")
    provider_calls = 0

    async def runtime(_db, key, default=""):
        values = {
            "kraken_auto_trade_enabled": "true",
            "kraken_max_auto_trade_eur": "250",
        }
        return values.get(key, default)

    async def permissions(_db):
        return {"query-funds", "modify-trades", "query-open-trades", "query-closed-trades"}

    async def balance(_db):
        return Decimal("200.00")

    async def ambiguous_order(_db, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise KrakenOrderCreationUncertainError("AddOrder response was lost")

    monkeypatch.setattr(investment_autopilot, "get_runtime_value", runtime)
    monkeypatch.setattr(investment_autopilot, "get_api_key_permissions", permissions)
    monkeypatch.setattr(investment_autopilot, "get_eur_balance", balance)
    monkeypatch.setattr(investment_autopilot, "market_buy_eur", ambiguous_order)

    await investment_autopilot.reconcile_kraken_funding_and_trade(db, transfer)
    intent = (
        await db.execute(
            select(InvestmentTradeIntent).where(InvestmentTradeIntent.transfer_id == transfer.id)
        )
    ).scalar_one()
    assert transfer.status == "trade_pending"
    assert intent.status == "creation_uncertain"
    assert provider_calls == 1

    async def recovered_orders(_db, client_order_id):
        assert client_order_id == intent.client_order_id
        return [{"order_id": "O-V117-LATE", "status": "closed", "source": "closed"}]

    monkeypatch.setattr(investment_recovery, "get_orders_by_client_order_id", recovered_orders)
    await investment_autopilot.reconcile_kraken_funding_and_trade(db, transfer)

    assert provider_calls == 1
    assert transfer.status == "invested"
    assert transfer.trade_order_id == "O-V117-LATE"


@pytest.mark.asyncio
async def test_auto_trade_requires_query_permissions_needed_for_safe_recovery(db, monkeypatch) -> None:
    source = await _source(db)
    transfer = await _transfer(db, source, key="v117-query-permissions", status="deposit_observed")
    provider_calls = 0

    async def runtime(_db, key, default=""):
        return "true" if key == "kraken_auto_trade_enabled" else default

    async def permissions(_db):
        return {"query-funds", "modify-trades"}

    async def balance(_db):
        return Decimal("200.00")

    async def market(_db, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {"txid": ["SHOULD-NOT-HAPPEN"]}

    monkeypatch.setattr(investment_autopilot, "get_runtime_value", runtime)
    monkeypatch.setattr(investment_autopilot, "get_api_key_permissions", permissions)
    monkeypatch.setattr(investment_autopilot, "get_eur_balance", balance)
    monkeypatch.setattr(investment_autopilot, "market_buy_eur", market)

    await investment_autopilot.reconcile_kraken_funding_and_trade(db, transfer)

    assert provider_calls == 0
    assert transfer.status == "funded"
    assert "query-open-trades" in (transfer.failure_reason or "")
