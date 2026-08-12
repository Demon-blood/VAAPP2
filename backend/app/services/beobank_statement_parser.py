from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pymupdf as fitz

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_AMOUNT_RE = re.compile(r"^-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}$")
_INTEGER_RE = re.compile(r"^\d{1,4}$")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?\d){10,30}\b", re.I)
_CARD_TIME_RE = re.compile(r"\b(\d{2}/\d{2}/(?:\d{2}|\d{4}))\s+(\d{2}:\d{2})\b")
_FEE_RE = re.compile(r"Kosten\+BTW:\s*(-?[\d.,]+)\s+([A-Z]{3})", re.I)
_FX_RE = re.compile(
    r"(-?[\d.,]+)\s+([A-Z]{3})\s*\(1\s+([A-Z]{3})\s*=\s*([\d.,]+)\s+([A-Z]{3})",
    re.I,
)
_DUTCH_MONTHS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
_NOISE_PREFIXES = (
    "Blz ",
    "GO REKENING :",
    "REKENINGHOUDER(S):",
    "Uittreksel:",
    "Nr.",
    "Datum",
    "Valutadatum",
    "KB.",
    "<<Vervolg",
    "Detail van uw rekeningen",
    "Zichtrekeningen",
    "Actuariële",
    "Vragen / Klachten?",
    "Adreswijziging?",
)
_NOISE_EXACT = {
    "Storting EUR",
    "Afhaling EUR",
    "Beschrijving",
    "Storting",
    "Afhaling",
    "Eindsaldo",
    "Beginsaldo",
}


class StatementImportError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedStatementTransaction:
    sequence_number: int
    booking_date: datetime
    value_date: datetime
    transaction_type: str
    signed_amount: Decimal
    counterparty_name: str
    counterparty_iban: str
    description_lines: tuple[str, ...]
    merchant_occurred_at: datetime | None = None
    original_amount: Decimal | None = None
    original_currency: str = ""
    exchange_rate: Decimal | None = None
    fee_amount: Decimal | None = None
    external_transaction_id: str = ""
    balance_after: Decimal | None = None
    source_format: str = "pdf"
    source_state: str = "COMPLETED"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def direction(self) -> str:
        return "credit" if self.signed_amount >= 0 else "debit"

    @property
    def amount(self) -> Decimal:
        return abs(self.signed_amount).quantize(Decimal("0.01"))

    @property
    def remittance(self) -> str:
        return " | ".join(self.description_lines)


@dataclass(frozen=True)
class ParsedBankStatement:
    provider: str
    statement_number: int
    statement_date: datetime
    account_iban: str
    currency: str
    period_start: datetime
    period_end: datetime
    opening_balance: Decimal
    total_credits: Decimal
    total_debits: Decimal
    closing_balance: Decimal
    transactions: tuple[ParsedStatementTransaction, ...]
    source_checksum_sha256: str
    account_identity: str = ""
    account_display_name: str = ""
    source_format: str = "pdf"
    ignored_transaction_count: int = 0
    validation_details: dict[str, object] = field(default_factory=dict)

    @property
    def statement_key(self) -> str:
        if self.account_identity:
            material = ":".join(
                (
                    self.provider,
                    self.account_identity.casefold(),
                    self.period_start.date().isoformat(),
                    self.currency,
                    f"{self.opening_balance:.2f}",
                    f"{self.total_credits:.2f}",
                    f"{self.total_debits:.2f}",
                    f"{self.closing_balance:.2f}",
                )
            )
        else:
            # Preserve the v0.7.1 Beobank identity so previously imported statements
            # remain duplicates rather than being inserted a second time.
            material = ":".join(
                (
                    self.provider,
                    self.account_iban,
                    str(self.statement_number),
                    self.statement_date.date().isoformat(),
                    f"{self.opening_balance:.2f}",
                    f"{self.closing_balance:.2f}",
                )
            )
        return hashlib.sha256(material.encode()).hexdigest()


def _money(value: str | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(value.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError) as exc:
        raise StatementImportError(f"Invalid statement amount: {value}") from exc


def _date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%d/%m/%Y")
    except ValueError as exc:
        raise StatementImportError(f"Invalid statement date: {value}") from exc


def _statement_date(text: str, fallback: datetime) -> datetime:
    match = re.search(r"Datum van het uittreksel:\s*(\d{1,2})\s+([A-Za-zé]+)\s+(\d{4})", text, re.I)
    if not match:
        return fallback
    month = _DUTCH_MONTHS.get(match.group(2).casefold())
    if not month:
        return fallback
    return datetime(int(match.group(3)), month, int(match.group(1)))


def _normalize_iban(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _normalize_party(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _description(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        value = line.strip()
        if not value:
            continue
        if value == "Totaal Stortingen/Afhalingen":
            break
        if value in _NOISE_EXACT or any(value.startswith(prefix) for prefix in _NOISE_PREFIXES):
            continue
        if value.startswith("Als dit rekeninguittreksel") or value.startswith("Gelieve met uw agentschap"):
            continue
        cleaned.append(value)
    return cleaned


def _merchant_time(lines: list[str]) -> datetime | None:
    for line in lines:
        match = _CARD_TIME_RE.search(line)
        if not match:
            continue
        date_value = match.group(1)
        fmt = "%d/%m/%Y %H:%M" if len(date_value.split("/")[-1]) == 4 else "%d/%m/%y %H:%M"
        try:
            return datetime.strptime(f"{date_value} {match.group(2)}", fmt)
        except ValueError:
            continue
    return None


def _counterparty(lines: list[str], transaction_type: str) -> tuple[str, str]:
    iban = ""
    for line in lines:
        match = _IBAN_RE.search(line.upper())
        if match:
            iban = _normalize_iban(match.group(0))[:34]
            break

    name = lines[0] if lines else transaction_type
    match = _CARD_TIME_RE.search(name)
    if match and match.start() == 0:
        name = name[match.end() :].strip(" -")
    return name[:255], iban


def _foreign_details(lines: list[str], account_currency: str) -> tuple[Decimal | None, str, Decimal | None, Decimal | None]:
    original_amount: Decimal | None = None
    original_currency = ""
    exchange_rate: Decimal | None = None
    fee_amount: Decimal | None = None
    for line in lines:
        fee_match = _FEE_RE.search(line)
        if fee_match and fee_match.group(2).upper() == account_currency:
            fee_amount = _money(fee_match.group(1))
        fx_match = _FX_RE.search(line)
        if fx_match:
            original_amount = _money(fx_match.group(1))
            original_currency = fx_match.group(2).upper()
            try:
                exchange_rate = Decimal(fx_match.group(4).replace(".", "").replace(",", "."))
            except InvalidOperation:
                exchange_rate = None
    return original_amount, original_currency, exchange_rate, fee_amount


def parse_beobank_statement(pdf_bytes: bytes) -> ParsedBankStatement:
    if not pdf_bytes.startswith(b"%PDF"):
        raise StatementImportError("The selected file is not a PDF")
    checksum = hashlib.sha256(pdf_bytes).hexdigest()
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text("text") for page in document)
    except Exception as exc:
        raise StatementImportError(f"Could not read this PDF: {exc}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass

    if "Beobank NV/SA" not in text or "GO REKENING" not in text or "Totaal Stortingen/Afhalingen" not in text:
        raise StatementImportError("This PDF is not a recognized Beobank account statement")

    statement_match = re.search(r"Uittreksel:\s*(\d+)", text)
    account_match = re.search(r"GO REKENING\s*:?\s*\n?(BE\d{2}(?:\s*\d){12,30})\s*\(([A-Z]{3})\)", text)
    opening_match = re.search(
        r"(\d{2}/\d{2}/\d{4})\nBEGINSALDO\n(-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})",
        text,
    )
    totals_match = re.search(
        r"Totaal Stortingen/Afhalingen\n(-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})"
        r"\n(-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})",
        text,
    )
    closing_match = re.search(
        r"(\d{2}/\d{2}/\d{4})\nEINDSALDO\n(-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})",
        text,
    )
    if not all((statement_match, account_match, opening_match, totals_match, closing_match)):
        raise StatementImportError("The Beobank statement header/totals could not be parsed safely")

    period_start = _date(opening_match.group(1))
    period_end = _date(closing_match.group(1))
    statement_date = _statement_date(text, period_end)
    currency = account_match.group(2).upper()
    account_iban = _normalize_iban(account_match.group(1))

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts: list[int] = []
    for index in range(len(lines) - 4):
        if not (_DATE_RE.fullmatch(lines[index]) and _DATE_RE.fullmatch(lines[index + 1])):
            continue
        if not (_AMOUNT_RE.fullmatch(lines[index + 3]) and _INTEGER_RE.fullmatch(lines[index + 4])):
            continue
        if lines[index + 2] in {"BEGINSALDO", "EINDSALDO", "Totaal Stortingen/Afhalingen"}:
            continue
        starts.append(index)

    transactions: list[ParsedStatementTransaction] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        description = _description(lines[start + 5 : end])
        counterparty_name, counterparty_iban = _counterparty(description, lines[start + 2])
        original_amount, original_currency, exchange_rate, fee_amount = _foreign_details(description, currency)
        transactions.append(
            ParsedStatementTransaction(
                sequence_number=int(lines[start + 4]),
                booking_date=_date(lines[start]),
                value_date=_date(lines[start + 1]),
                transaction_type=lines[start + 2][:120],
                signed_amount=_money(lines[start + 3]),
                counterparty_name=counterparty_name,
                counterparty_iban=counterparty_iban,
                description_lines=tuple(description),
                merchant_occurred_at=_merchant_time(description),
                original_amount=original_amount,
                original_currency=original_currency,
                exchange_rate=exchange_rate,
                fee_amount=fee_amount,
            )
        )

    if not transactions:
        raise StatementImportError("No Beobank transactions were found")
    sequence = [item.sequence_number for item in transactions]
    if sequence != list(range(1, max(sequence) + 1)):
        raise StatementImportError("Beobank transaction numbering is incomplete; import blocked")

    opening_balance = _money(opening_match.group(2))
    total_credits = _money(totals_match.group(1)).copy_abs()
    total_debits = _money(totals_match.group(2)).copy_abs()
    closing_balance = _money(closing_match.group(2))
    extracted_credits = sum((item.amount for item in transactions if item.direction == "credit"), Decimal("0.00"))
    extracted_debits = sum((item.amount for item in transactions if item.direction == "debit"), Decimal("0.00"))
    calculated_closing = (opening_balance + extracted_credits - extracted_debits).quantize(Decimal("0.01"))
    if extracted_credits != total_credits or extracted_debits != total_debits or calculated_closing != closing_balance:
        raise StatementImportError(
            "Statement balance validation failed; the VA will not learn from an unreconciled statement"
        )

    return ParsedBankStatement(
        provider="beobank",
        statement_number=int(statement_match.group(1)),
        statement_date=statement_date,
        account_iban=account_iban,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        closing_balance=closing_balance,
        transactions=tuple(transactions),
        source_checksum_sha256=checksum,
    )


