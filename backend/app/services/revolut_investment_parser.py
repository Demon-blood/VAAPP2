from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pymupdf as fitz

from app.services.beobank_statement_parser import StatementImportError
from app.services.revolut_statement_parser import _read_xlsx_rows


@dataclass(frozen=True)
class InvestmentTransactionRow:
    booked_at: datetime
    symbol: str
    transaction_type: str
    side: str
    quantity: Decimal | None
    price: Decimal | None
    amount: Decimal
    currency: str
    fx_rate: Decimal | None
    fee: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")


@dataclass(frozen=True)
class InvestmentPositionRow:
    symbol: str
    company: str
    isin: str
    quantity: Decimal
    price: Decimal
    market_value: Decimal
    allocation_percent: Decimal
    currency: str


@dataclass(frozen=True)
class InvestmentAccountStatement:
    provider: str
    portfolio_kind: str
    period_start: datetime
    period_end: datetime
    account_reference: str
    transactions: tuple[InvestmentTransactionRow, ...]
    positions: tuple[InvestmentPositionRow, ...]
    summary_by_currency: dict[str, dict[str, Decimal]]
    checksum_sha256: str
    source_format: str


@dataclass(frozen=True)
class InvestmentPnLRow:
    date_acquired: datetime
    date_sold: datetime
    symbol: str
    security_name: str
    isin: str
    country: str
    quantity: Decimal
    cost_basis: Decimal
    gross_proceeds: Decimal
    gross_pnl: Decimal
    currency: str


@dataclass(frozen=True)
class InvestmentIncomeRow:
    booked_at: datetime
    symbol: str
    security_name: str
    isin: str
    country: str
    gross_amount: Decimal
    withholding_tax: Decimal
    net_amount: Decimal
    currency: str


@dataclass(frozen=True)
class InvestmentPnLStatement:
    provider: str
    period_start: datetime | None
    period_end: datetime | None
    account_reference: str
    sells: tuple[InvestmentPnLRow, ...]
    income: tuple[InvestmentIncomeRow, ...]
    checksum_sha256: str
    source_format: str


def _decimal(value: object, default: Decimal | None = None) -> Decimal:
    if value in (None, ""):
        if default is not None:
            return default
        raise StatementImportError("Missing Revolut investment numeric value")
    text = str(value).replace("US$", "").replace("EUR", "").replace("USD", "")
    text = text.replace("€", "").replace(",", "").replace("%", "").strip()
    # Some Revolut XLSX readers surface the euro symbol as mojibake. Strip any
    # remaining non-numeric glyphs rather than rejecting an otherwise valid amount.
    text = re.sub(r"[^0-9.eE\-+]", "", text)
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise StatementImportError(f"Invalid Revolut investment number: {value}") from exc


def _amount_and_currency(value: object, fallback: str = "EUR") -> tuple[Decimal, str]:
    text = str(value or "").strip()
    currency = fallback
    if text.startswith("US$") or text.upper().startswith("USD "):
        currency = "USD"
    elif text.startswith("€") or text.upper().startswith("EUR "):
        currency = "EUR"
    return _decimal(text, Decimal("0")), currency


def _date(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise StatementImportError("Missing Revolut investment date")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        serial = float(value)
        from datetime import timedelta

        return datetime(1899, 12, 30) + timedelta(days=serial)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%d %b %Y %H:%M:%S %Z", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise StatementImportError(f"Invalid Revolut investment date: {value}")


def _row_kind(transaction_type: str) -> str:
    return transaction_type.strip().upper().replace("_", " ")


def parse_revolut_investment_account_xlsx(content: bytes) -> InvestmentAccountStatement:
    rows = _read_xlsx_rows(content)
    if not rows:
        raise StatementImportError("The Revolut investment workbook is empty")
    expected = ["Date", "Ticker", "Type", "Quantity", "Price per share", "Total Amount", "Currency", "FX Rate"]
    headers = [str(v or "").strip() for v in rows[0][:8]]
    if headers != expected:
        raise StatementImportError("This XLSX is not a Revolut investment account statement")
    transactions: list[InvestmentTransactionRow] = []
    dates: list[datetime] = []
    robo = False
    for raw in rows[1:]:
        row = list(raw) + [None] * max(0, 8 - len(raw))
        if not any(v not in (None, "") for v in row[:8]):
            continue
        booked = _date(row[0])
        tx_type = str(row[2] or "").strip().upper()
        if "ROBO MANAGEMENT FEE" in tx_type:
            robo = True
        amount, currency = _amount_and_currency(row[5], str(row[6] or "EUR").upper())
        price = None
        if row[4] not in (None, ""):
            price, _ = _amount_and_currency(row[4], currency)
        quantity = _decimal(row[3]) if row[3] not in (None, "") else None
        fx = _decimal(row[7]) if row[7] not in (None, "") else None
        side = ""
        if tx_type.startswith("BUY"):
            side = "buy"
        elif tx_type.startswith("SELL"):
            side = "sell"
        transactions.append(
            InvestmentTransactionRow(
                booked_at=booked,
                symbol=str(row[1] or "").strip().upper(),
                transaction_type=tx_type.lower().replace(" - ", "_").replace(" ", "_"),
                side=side,
                quantity=quantity,
                price=price,
                amount=amount,
                currency=currency,
                fx_rate=fx,
            )
        )
        dates.append(booked)
    if not transactions:
        raise StatementImportError("The Revolut investment workbook contains no transactions")
    return InvestmentAccountStatement(
        provider="revolut_securities",
        portfolio_kind="robo" if robo else "brokerage",
        period_start=min(dates),
        period_end=max(dates),
        account_reference="",
        transactions=tuple(transactions),
        positions=(),
        summary_by_currency={},
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        source_format="xlsx",
    )


def parse_revolut_investment_pnl_xlsx(content: bytes) -> InvestmentPnLStatement:
    rows = _read_xlsx_rows(content)
    if len(rows) < 2 or str(rows[0][0] or "").strip() != "Income from Sells":
        raise StatementImportError("This XLSX is not a Revolut investment profit and loss statement")
    sells: list[InvestmentPnLRow] = []
    income: list[InvestmentIncomeRow] = []
    mode = ""
    for idx, raw in enumerate(rows):
        first = str((raw[0] if raw else None) or "").strip()
        if first == "Date acquired":
            mode = "sells"
            continue
        if first == "Other income & fees":
            mode = ""
            continue
        if first == "Date" and idx > 0:
            mode = "income"
            continue
        row = list(raw) + [None] * max(0, 11 - len(raw))
        if mode == "sells" and row[0] not in (None, "") and row[1] not in (None, ""):
            sells.append(
                InvestmentPnLRow(
                    date_acquired=_date(row[0]),
                    date_sold=_date(row[1]),
                    symbol=str(row[2] or "").strip().upper(),
                    security_name=str(row[3] or "").replace("&amp;", "&").strip(),
                    isin=str(row[4] or "").strip().upper(),
                    country=str(row[5] or "").strip().upper(),
                    quantity=_decimal(row[6], Decimal("0")),
                    cost_basis=_decimal(row[7], Decimal("0")),
                    gross_proceeds=_decimal(row[8], Decimal("0")),
                    gross_pnl=_decimal(row[9], Decimal("0")),
                    currency=str(row[10] or "EUR").strip().upper(),
                )
            )
        elif mode == "income" and row[0] not in (None, ""):
            gross = _decimal(row[5], Decimal("0"))
            withholding = _decimal(row[6], Decimal("0"))
            net = _decimal(row[7], gross - withholding)
            income.append(
                InvestmentIncomeRow(
                    booked_at=_date(row[0]),
                    symbol=str(row[1] or "").strip().upper(),
                    security_name=str(row[2] or "").replace("&amp;", "&").strip(),
                    isin=str(row[3] or "").strip().upper(),
                    country=str(row[4] or "").strip().upper(),
                    gross_amount=gross,
                    withholding_tax=withholding,
                    net_amount=net,
                    currency=str(row[8] or "EUR").strip().upper(),
                )
            )
    if not sells and not income:
        raise StatementImportError("The Revolut P&L workbook contains no realised activity")
    dates = [row.date_sold for row in sells] + [row.booked_at for row in income]
    return InvestmentPnLStatement(
        provider="revolut_securities",
        period_start=min(dates) if dates else None,
        period_end=max(dates) if dates else None,
        account_reference="",
        sells=tuple(sells),
        income=tuple(income),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        source_format="xlsx",
    )


_MONEY_TOKEN = re.compile(r"^(?:US\$|€)-?\d[\d,]*(?:\.\d+)?$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,6}$")
_PERIOD = re.compile(r"(\d{1,2} [A-Z][a-z]{2} \d{4}) - (\d{1,2} [A-Z][a-z]{2} \d{4})")


def _currency_from_money(value: str) -> str:
    return "USD" if value.startswith("US$") else "EUR"


def _parse_position_section(lines: list[str], currency: str) -> list[InvestmentPositionRow]:
    try:
        start = lines.index(f"{currency} Portfolio breakdown")
    except ValueError:
        return []
    try:
        end = lines.index("Positions Value", start + 1)
    except ValueError:
        return []
    body = lines[start + 1 : end]
    positions: list[InvestmentPositionRow] = []
    for index, line in enumerate(body):
        if not _ISIN.match(line):
            continue
        symbol_index = index - 1
        while symbol_index >= 0 and not _SYMBOL.match(body[symbol_index]):
            symbol_index -= 1
        if symbol_index < 0:
            continue
        symbol = body[symbol_index]
        company = " ".join(body[symbol_index + 1 : index]).strip()
        numeric_tokens: list[str] = []
        look = index + 1
        while look < len(body) and len(numeric_tokens) < 4:
            numeric_tokens.extend(body[look].split())
            if any(token.endswith("%") for token in numeric_tokens):
                break
            look += 1
        quantity_token = next((t for t in numeric_tokens if not t.startswith(("€", "US$")) and not t.endswith("%")), None)
        money_tokens = [t for t in numeric_tokens if t.startswith(("€", "US$"))]
        percent_token = next((t for t in numeric_tokens if t.endswith("%")), None)
        if quantity_token is None or len(money_tokens) < 2 or percent_token is None:
            continue
        positions.append(
            InvestmentPositionRow(
                symbol=symbol,
                company=company,
                isin=line,
                quantity=_decimal(quantity_token),
                price=_decimal(money_tokens[0]),
                market_value=_decimal(money_tokens[1]),
                allocation_percent=_decimal(percent_token),
                currency=currency,
            )
        )
    return positions


def parse_revolut_investment_account_pdf(content: bytes) -> InvestmentAccountStatement:
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise StatementImportError(f"Could not read this Revolut investment PDF: {exc}") from exc
    try:
        pages = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    text = "\n".join(pages)
    if "Account Statement" not in text or "Revolut Securities Europe UAB" not in text:
        raise StatementImportError("This PDF is not a Revolut Securities account statement")
    period_match = _PERIOD.search(text)
    if not period_match:
        raise StatementImportError("The Revolut investment statement period is missing")
    period_start = datetime.strptime(period_match.group(1), "%d %b %Y")
    period_end = datetime.strptime(period_match.group(2), "%d %b %Y")
    account_match = re.search(r"Account number\s*\n([^\n]+)", text)
    account_reference = account_match.group(1).strip() if account_match else ""
    portfolio_kind = "robo" if "Robo management fee" in text else "brokerage"
    positions: list[InvestmentPositionRow] = []
    summary: dict[str, dict[str, Decimal]] = {}
    for page_text in pages:
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        for currency in ("EUR", "USD"):
            if f"{currency} Account summary" in lines:
                try:
                    s = lines.index(f"{currency} Account summary")
                    p = lines.index("Positions Value", s)
                    cash = lines.index("Cash value*", p)
                    total = lines.index("Total", cash)
                    summary[currency] = {
                        "positions_start": _decimal(lines[p + 1]),
                        "positions_end": _decimal(lines[p + 2]),
                        "cash_start": _decimal(lines[cash + 1]),
                        "cash_end": _decimal(lines[cash + 2]),
                        "total_start": _decimal(lines[total + 1]),
                        "total_end": _decimal(lines[total + 2]),
                    }
                except (ValueError, IndexError, StatementImportError):
                    pass
            positions.extend(_parse_position_section(lines, currency))
    return InvestmentAccountStatement(
        provider="revolut_securities",
        portfolio_kind=portfolio_kind,
        period_start=period_start,
        period_end=period_end,
        account_reference=account_reference,
        transactions=(),
        positions=tuple(positions),
        summary_by_currency=summary,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        source_format="pdf",
    )


def validate_revolut_investment_pnl_pdf(content: bytes) -> dict[str, object]:
    """Validate Revolut Securities P&L PDF identity and extract audit metadata.

    XLSX remains authoritative for row-level FIFO data, while this verifies that the
    paired PDF is genuinely a Revolut Securities P&L export for a readable period.
    """
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise StatementImportError(f"Could not read this Revolut investment P&L PDF: {exc}") from exc
    try:
        text = "\n".join(page.get_text("text") for page in doc)
        page_count = len(doc)
    finally:
        doc.close()
    if "Profit and Loss Statement" not in text or "Revolut Securities Europe UAB" not in text:
        raise StatementImportError("This PDF is not a Revolut Securities profit and loss statement")
    period_match = _PERIOD.search(text)
    period_start = datetime.strptime(period_match.group(1), "%d %b %Y") if period_match else None
    period_end = datetime.strptime(period_match.group(2), "%d %b %Y") if period_match else None
    currencies = [currency for currency in ("EUR", "USD") if f"{currency} Profit and Loss Statement" in text]
    return {
        "validated": True,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "currencies": currencies,
        "pages": page_count,
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
    }


def looks_like_revolut_investment(filename: str, content: bytes) -> bool:
    lower = filename.casefold()
    if lower.startswith("trading-") or "trading-account-statement" in lower or "trading-pnl-statement" in lower:
        return True
    if lower.endswith(".xlsx"):
        try:
            rows = _read_xlsx_rows(content)
        except StatementImportError:
            return False
        if not rows:
            return False
        first = [str(v or "").strip() for v in rows[0][:8]]
        return first[:3] == ["Date", "Ticker", "Type"] or first[0] == "Income from Sells"
    if lower.endswith(".pdf"):
        try:
            doc = fitz.open(stream=content, filetype="pdf")
            first = "\n".join(page.get_text("text") for page in list(doc)[:2])
            doc.close()
            return "Revolut Securities Europe UAB" in first or "Profit and Loss Statement" in first
        except Exception:
            return False
    return False
