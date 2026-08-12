from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import Base, BankStatementImport, HistoricalFinancialTransaction
from app.services.bank_statement_import import import_statement_file_bytes, reconcile_cross_statement_internal_transfers
from revolut_test_fixtures import _synthetic_revolut_pdf, _synthetic_revolut_xlsx

@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_paired_revolut_xlsx_pdf_merges_primary_instead_of_double_counting(db) -> None:
    xlsx_results = await import_statement_file_bytes(
        db, filename="revolut.xlsx", content=_synthetic_revolut_xlsx(), fallback_scope="personal"
    )
    assert len(xlsx_results) == 1
    assert xlsx_results[0]["duplicate"] is False

    pdf_results = await import_statement_file_bytes(
        db, filename="revolut.pdf", content=_synthetic_revolut_pdf(), fallback_scope="personal"
    )
    assert len(pdf_results) == 2
    assert pdf_results[0]["duplicate"] is True
    assert pdf_results[0]["enriched"] is True
    assert pdf_results[1]["duplicate"] is False

    imports = list((await db.execute(select(BankStatementImport).order_by(BankStatementImport.id))).scalars())
    assert len(imports) == 2
    main = imports[0]
    assert main.transaction_count == 5
    assert main.account_iban == "BE00123456789012"
    validation = json.loads(main.validation_json)
    assert validation["authoritative_source"] == "xlsx"
    assert {item["format"] for item in validation["sources"]} == {"xlsx", "pdf"}

    main_rows = list(
        (
            await db.execute(
                select(HistoricalFinancialTransaction)
                .where(HistoricalFinancialTransaction.statement_import_id == main.id)
                .order_by(HistoricalFinancialTransaction.sequence_number)
            )
        ).scalars()
    )
    assert len(main_rows) == 5
    assert "22222222-2222-2222-2222-222222222222" in main_rows[1].raw_json
    assert main_rows[2].is_internal_transfer is True  # Robo portfolio
    assert main_rows[3].category == "subscriptions"
    assert main_rows[4].income_kind == "refund"


@pytest.mark.asyncio
async def test_beobank_revolut_statement_pair_is_confirmed_internal_without_live_connection(db) -> None:
    beobank = BankStatementImport(
        provider="beobank", statement_key="b" * 64, source_checksum_sha256="1" * 64,
        original_filename="b.pdf", account_iban="BE001", account_scope="personal",
        statement_number=1, statement_date=datetime(2026, 1, 3), period_start=datetime(2026, 1, 1),
        period_end=datetime(2026, 1, 31), opening_balance=Decimal("100"), total_credits=Decimal("0"),
        total_debits=Decimal("30"), closing_balance=Decimal("70"), currency="EUR", transaction_count=1,
        validation_status="verified", validation_json="{}",
    )
    revolut = BankStatementImport(
        provider="revolut", statement_key="r" * 64, source_checksum_sha256="2" * 64,
        original_filename="r.xlsx", account_iban="", account_scope="personal",
        statement_number=0, statement_date=datetime(2026, 1, 3), period_start=datetime(2026, 1, 1),
        period_end=datetime(2026, 1, 31), opening_balance=Decimal("0"), total_credits=Decimal("30"),
        total_debits=Decimal("0"), closing_balance=Decimal("30"), currency="EUR", transaction_count=1,
        validation_status="verified", validation_json='{"account_identity":"current","account_display_name":"Owner"}',
    )
    db.add_all([beobank, revolut])
    await db.flush()
    debit = HistoricalFinancialTransaction(
        statement_import_id=beobank.id, transaction_fingerprint="d" * 64, sequence_number=1,
        account_iban="BE001", account_scope="personal", booking_date=datetime(2026, 1, 3),
        value_date=datetime(2026, 1, 3), amount=Decimal("30"), currency="EUR", direction="debit",
        transaction_type="Betaling Debit Mastercard", counterparty_name="Revolut**9999*", raw_json="{}",
    )
    credit = HistoricalFinancialTransaction(
        statement_import_id=revolut.id, transaction_fingerprint="c" * 64, sequence_number=1,
        account_iban="", account_scope="personal", booking_date=datetime(2026, 1, 2),
        value_date=datetime(2026, 1, 2), amount=Decimal("30"), currency="EUR", direction="credit",
        transaction_type="Topup", counterparty_name="Top-up by *9999", raw_json="{}",
    )
    db.add_all([debit, credit])
    await db.commit()
    outcome = await reconcile_cross_statement_internal_transfers(db)
    assert outcome["marked_internal"] == 2
    assert debit.is_internal_transfer is True
    assert credit.is_internal_transfer is True
    assert debit.category == credit.category == "internal_transfer"

