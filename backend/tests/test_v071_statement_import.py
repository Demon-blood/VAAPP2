from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pymupdf as fitz
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import Base, BankAccount, BankConnection, HistoricalFinancialTransaction
from app.services.bank_statement_import import import_statement_bytes, statement_history_summary
from app.services.beobank_statement_parser import StatementImportError, parse_beobank_statement


def _synthetic_beobank_pdf(*, closing_balance: str = "2.842,02") -> bytes:
    page1 = """FAKE CUSTOMER
TEST STREET 1
1000 TEST
Beobank NV/SA, Koning Albert II-laan 2, 1000 Brussel
Blz 1
Datum van het uittreksel: 30 juni 2026
Overzicht van uw rekeningen
Zichtrekeningen
GO REKENING
BE12 3456 7890 1234 (EUR)
1.000,00
2.000,00
-157,98
{closing}
Depositorekeningen (1)
Beginsaldo
Storting
Afhaling
Eindsaldo
TOTAAL DEPOSITO'S
1.000,00
2.000,00
-157,98
2.842,02
"""
    page1 = page1.format(closing=closing_balance)
    page2 = """Blz 2
Detail van uw rekeningen
Zichtrekeningen
GO REKENING : BE12 3456 7890 1234 (EUR)
REKENINGHOUDER(S): FAKE CUSTOMER
Uittreksel: 006
31/05/2026
BEGINSALDO
1.000,00
02/06/2026
02/06/2026
Overschrijving van
2.000,00
1
TEST EMPLOYER
BE98 7654 3210 9876
/A/ Loon 05/2026
02/06/2026
03/06/2026
Betaling Debit Mastercard
-100,00
2
Revolut**9999* - Dublin
01/06/2026 09:19
05/06/2026
06/06/2026
Betaling Debit Mastercard
-21,99
3
NETFLIX.COM - AMSTERDAM
05/06/2026 00:00
"""
    page3 = """Blz 3
GO REKENING : BE12 3456 7890 1234 (EUR)
REKENINGHOUDER(S): FAKE CUSTOMER
Uittreksel: 006
29/06/2026
30/06/2026
Betaling Debit Mastercard
-35,99
4
Google Play Apps - Dublin
28/06/2026 12:23
Totaal Stortingen/Afhalingen
2.000,00
-157,98
30/06/2026
EINDSALDO
{closing}
Datum
Valutadatum Beschrijving
Storting EUR
Afhaling EUR
Nr.
"""
    page3 = page3.format(closing=closing_balance)
    document = fitz.open()
    for content in (page1, page2, page3):
        page = document.new_page()
        page.insert_text((36, 36), content, fontsize=8)
    data = document.tobytes()
    document.close()
    return data


def test_beobank_parser_reconciles_statement_to_the_cent() -> None:
    parsed = parse_beobank_statement(_synthetic_beobank_pdf())
    assert parsed.statement_number == 6
    assert parsed.account_iban == "BE12345678901234"
    assert len(parsed.transactions) == 4
    assert [item.sequence_number for item in parsed.transactions] == [1, 2, 3, 4]
    assert parsed.total_credits == Decimal("2000.00")
    assert parsed.total_debits == Decimal("157.98")
    assert parsed.opening_balance + parsed.total_credits - parsed.total_debits == parsed.closing_balance


def test_beobank_parser_blocks_unreconciled_statement() -> None:
    with pytest.raises(StatementImportError, match="balance validation failed"):
        parse_beobank_statement(_synthetic_beobank_pdf(closing_balance="2.842,03"))


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
async def test_import_separates_statement_evidence_and_excludes_confirmed_revolut_topup(db) -> None:
    connection = BankConnection(
        institution_country="BE",
        institution_name="Beobank",
        psu_type="personal",
        session_id_encrypted="opaque",
        status="active",
    )
    db.add(connection)
    await db.flush()
    db.add_all(
        [
            BankAccount(
                bank_connection_id=connection.id,
                external_account_id="beobank-current",
                account_scope="personal",
                name="GO REKENING",
                iban="BE12345678901234",
                currency="EUR",
            ),
            BankAccount(
                bank_connection_id=connection.id,
                external_account_id="revolut-own",
                account_scope="personal",
                name="Revolut Personal",
                iban="BE00111122223333",
                currency="EUR",
            ),
        ]
    )
    await db.commit()

    outcome = await import_statement_bytes(
        db,
        filename="statement-june.pdf",
        content=_synthetic_beobank_pdf(),
        fallback_scope="personal",
    )
    assert outcome["transactions"] == 4
    rows = list((await db.execute(select(HistoricalFinancialTransaction).order_by(HistoricalFinancialTransaction.sequence_number))).scalars())
    assert len(rows) == 4
    assert rows[1].counterparty_name.startswith("Revolut")
    assert rows[1].is_internal_transfer is True
    assert rows[1].category == "internal_transfer"
    assert rows[2].category == "subscriptions"
    assert rows[3].category == "digital"
    assert rows[0].income_kind == "salary"

    summary = await statement_history_summary(db)
    assert summary["statements"] == 1
    assert summary["transactions"] == 4
    assert summary["internal_transfers"] == 1
    assert "digital" in summary["learned_budget"]["personal"]


def test_statement_import_regression_fixture_is_synthetic_only() -> None:
    root = Path(__file__).parents[2]
    service = (root / "backend" / "app" / "services" / "bank_statement_import.py").read_text()
    parser = (root / "backend" / "app" / "services" / "beobank_statement_parser.py").read_text()
    fixture_source = Path(__file__).read_text()
    assert "FAKE CUSTOMER" in fixture_source
    assert "BE12 3456 7890 1234" in fixture_source
    assert "/mnt/data/" not in service
    assert "/mnt/data/" not in parser
