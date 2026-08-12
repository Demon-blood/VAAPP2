from __future__ import annotations

import hashlib
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

import pymupdf as fitz

from app.services.beobank_statement_parser import (
    ParsedBankStatement,
    ParsedStatementTransaction,
    StatementImportError,
    _normalize_iban,
)

_XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DATE_LINE_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$")
_EUR_RE = re.compile(r"^(?:-?€|-?EUR\s*)[\d,]+\.\d{2}$", re.I)
_TX_ID_RE = re.compile(r"Transaction Id:\s*([0-9a-f-]{20,})", re.I)
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_FOREIGN_AMOUNT_RE = re.compile(r"^([\d,]+\.\d{2})\s+([A-Z]{3})$")
_PERIOD_RE = re.compile(r"from ([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})", re.I)


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value).replace("€", "").replace("EUR", "").replace("eur", "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise StatementImportError(f"Invalid Revolut amount: {value}") from exc


def _excel_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        serial = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        raise StatementImportError(f"Invalid Revolut XLSX date: {value}")
    return datetime(1899, 12, 30) + timedelta(days=serial)


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference or "")
    if not letters:
        raise StatementImportError("Malformed Revolut XLSX cell reference")
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - 64
    return value - 1


def _read_xlsx_rows(content: bytes) -> list[list[object]]:
    if not content.startswith(b"PK"):
        raise StatementImportError("The selected XLSX file is not a valid Office workbook")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise StatementImportError("The selected XLSX file is corrupt") from exc
    with archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
            raise StatementImportError("The XLSX workbook structure is incomplete")

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("x:si", _XLSX_NS):
                shared_strings.append("".join(node.text or "" for node in item.findall(".//x:t", _XLSX_NS)))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        first_sheet = workbook.find("x:sheets/x:sheet", _XLSX_NS)
        if first_sheet is None:
            raise StatementImportError("The XLSX workbook contains no worksheets")
        relationship_id = first_sheet.attrib.get(f"{{{_OFFICE_REL_NS}}}id", "")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = ""
        for rel in relationships.findall("r:Relationship", _REL_NS):
            if rel.attrib.get("Id") == relationship_id:
                target = rel.attrib.get("Target", "")
                break
        if not target:
            raise StatementImportError("Could not locate the Revolut XLSX worksheet")
        normalized_target = str(PurePosixPath("xl") / target) if not target.startswith("/") else target.lstrip("/")
        normalized_target = str(PurePosixPath(normalized_target))
        if normalized_target not in names:
            # Relationship targets can contain ../ segments. Resolve them against xl/.
            parts: list[str] = []
            for part in normalized_target.split("/"):
                if part == "..":
                    if parts:
                        parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            normalized_target = "/".join(parts)
        if normalized_target not in names:
            raise StatementImportError("The Revolut XLSX worksheet is missing")

        sheet = ET.fromstring(archive.read(normalized_target))
        rows: list[list[object]] = []
        for row_node in sheet.findall(".//x:sheetData/x:row", _XLSX_NS):
            values: list[object] = []
            for cell in row_node.findall("x:c", _XLSX_NS):
                index = _column_index(cell.attrib.get("r", ""))
                while len(values) <= index:
                    values.append(None)
                cell_type = cell.attrib.get("t", "")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall(".//x:t", _XLSX_NS))
                else:
                    value_node = cell.find("x:v", _XLSX_NS)
                    raw = value_node.text if value_node is not None else None
                    if raw is None:
                        value = None
                    elif cell_type == "s":
                        try:
                            value = shared_strings[int(raw)]
                        except (IndexError, ValueError) as exc:
                            raise StatementImportError("The Revolut XLSX shared-string table is invalid") from exc
                    elif cell_type in {"str", "b"}:
                        value = raw
                    else:
                        try:
                            value = float(raw)
                        except ValueError:
                            value = raw
                values[index] = value
            rows.append(values)
        return rows


def parse_revolut_xlsx(content: bytes) -> ParsedBankStatement:
    checksum = hashlib.sha256(content).hexdigest()
    rows = _read_xlsx_rows(content)
    if not rows:
        raise StatementImportError("The Revolut XLSX workbook is empty")
    required = [
        "Type",
        "Product",
        "Started Date",
        "Completed Date",
        "Description",
        "Amount",
        "Fee",
        "Currency",
        "State",
        "Balance",
    ]
    headers = [str(value or "").strip() for value in rows[0][: len(required)]]
    if headers != required:
        raise StatementImportError("This XLSX is not a recognized Revolut account statement export")

    completed: list[ParsedStatementTransaction] = []
    ignored = 0
    previous_balance: Decimal | None = None
    opening_balance: Decimal | None = None
    credits = Decimal("0.00")
    debits = Decimal("0.00")
    product = "Current"
    currency = "EUR"
    completed_dates: list[datetime] = []

    for raw_row in rows[1:]:
        row = list(raw_row) + [None] * max(0, 10 - len(raw_row))
        state = str(row[8] or "").strip().upper()
        if not any(value not in (None, "") for value in row[:10]):
            continue
        if state != "COMPLETED":
            ignored += 1
            continue
        started = _excel_datetime(row[2])
        finished = _excel_datetime(row[3])
        if started is None or finished is None:
            raise StatementImportError("A completed Revolut row is missing its transaction date")
        amount_component = _money(row[5] or 0)
        fee_component = _money(row[6] or 0)
        cash_delta = (amount_component - fee_component).quantize(Decimal("0.01"))
        balance = _money(row[9])
        if opening_balance is None:
            opening_balance = (balance - cash_delta).quantize(Decimal("0.01"))
            previous_balance = opening_balance
        expected = (previous_balance + cash_delta).quantize(Decimal("0.01")) if previous_balance is not None else balance
        if expected != balance:
            raise StatementImportError(
                "Revolut XLSX balance progression does not reconcile; import blocked"
            )
        previous_balance = balance
        if cash_delta >= 0:
            credits += cash_delta
        else:
            debits += -cash_delta
        product = str(row[1] or product).strip() or product
        currency = str(row[7] or currency).strip().upper() or currency
        description = str(row[4] or row[0] or "").strip()
        completed_dates.append(finished)
        completed.append(
            ParsedStatementTransaction(
                sequence_number=len(completed) + 1,
                booking_date=finished,
                value_date=started,
                transaction_type=str(row[0] or "Transaction")[:120],
                signed_amount=cash_delta,
                counterparty_name=description[:255],
                counterparty_iban="",
                description_lines=(description,),
                merchant_occurred_at=started,
                fee_amount=fee_component if fee_component else None,
                balance_after=balance,
                source_format="xlsx",
                source_state=state,
                metadata={
                    "product": product,
                    "xlsx_amount": str(amount_component),
                    "xlsx_fee": str(fee_component),
                },
            )
        )

    if not completed or opening_balance is None or previous_balance is None:
        raise StatementImportError("The Revolut XLSX contains no completed transactions")
    calculated = (opening_balance + credits - debits).quantize(Decimal("0.01"))
    if calculated != previous_balance:
        raise StatementImportError("Revolut XLSX totals do not reconcile to the closing balance")
    period_start = min(completed_dates).replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = max(completed_dates).replace(hour=0, minute=0, second=0, microsecond=0)
    return ParsedBankStatement(
        provider="revolut",
        statement_number=0,
        statement_date=period_end,
        account_iban="",
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening_balance,
        total_credits=credits.quantize(Decimal("0.01")),
        total_debits=debits.quantize(Decimal("0.01")),
        closing_balance=previous_balance,
        transactions=tuple(completed),
        source_checksum_sha256=checksum,
        account_identity=product.casefold(),
        account_display_name=product,
        source_format="xlsx",
        ignored_transaction_count=ignored,
        validation_details={
            "balance_progression_reconciled": True,
            "completed_transactions": len(completed),
            "ignored_reverted_transactions": ignored,
            "cash_delta_uses_amount_minus_fee": True,
        },
    )


def _parse_english_date(value: str) -> datetime:
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise StatementImportError(f"Invalid Revolut PDF date: {value}")


def _pdf_amount(value: str) -> Decimal:
    negative = value.startswith("-")
    amount = _money(value.lstrip("-"))
    return -amount if negative else amount


def _transaction_type(description: str, direction: str, details: list[str]) -> str:
    lowered = description.casefold()
    if lowered.startswith("top-up") or lowered.startswith("top up"):
        return "Topup"
    if "robo portfolio" in lowered or lowered.startswith("transfer ") or lowered.startswith("international transfer"):
        return "Transfer"
    if lowered.startswith("exchanged to"):
        return "Exchange"
    if "plan fee" in lowered:
        return "Charge"
    if "refund" in lowered:
        return "Charge Refund"
    if any(line.startswith("Card:") for line in details):
        return "Card Refund" if direction == "credit" else "Card Payment"
    return "Transfer" if any(line.startswith(("To:", "From:", "Reference:")) for line in details) else "Transaction"


def _counterparty(description: str, details: list[str]) -> tuple[str, str]:
    name = description
    iban = ""
    for line in details:
        if line.startswith(("To:", "From:")):
            value = line.split(":", 1)[1].strip()
            name = value.split(",", 1)[0].strip() or name
            matches = _IBAN_RE.findall(value.upper())
            if matches:
                iban = _normalize_iban(matches[-1])[:34]
            break
    return name[:255], iban


def _foreign_metadata(details: list[str]) -> tuple[Decimal | None, str, Decimal | None]:
    original_amount: Decimal | None = None
    original_currency = ""
    fee: Decimal | None = None
    for line in details:
        if line.startswith("Fee:"):
            match = re.search(r"€([\d,]+\.\d{2})", line)
            if match:
                fee = _money(match.group(1))
        match = _FOREIGN_AMOUNT_RE.match(line)
        if match and match.group(2) != "EUR":
            original_amount = _money(match.group(1))
            original_currency = match.group(2)
    return original_amount, original_currency, fee


def _parse_pdf_section(
    section_text: str,
    *,
    opening_balance: Decimal,
    expected_credits: Decimal,
    expected_debits: Decimal,
    expected_closing: Decimal,
) -> tuple[ParsedStatementTransaction, ...]:
    lines = [line.strip() for line in section_text.splitlines() if line.strip()]
    starts: list[int] = []
    for index in range(len(lines) - 3):
        if _DATE_LINE_RE.fullmatch(lines[index]) and _DATE_LINE_RE.fullmatch(lines[index + 1]):
            starts.append(index)
    transactions: list[ParsedStatementTransaction] = []
    previous_balance = opening_balance
    credits = Decimal("0.00")
    debits = Decimal("0.00")
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        money_index = next((idx for idx in range(2, len(block)) if _EUR_RE.fullmatch(block[idx])), None)
        if money_index is None or money_index + 1 >= len(block) or not _EUR_RE.fullmatch(block[money_index + 1]):
            continue
        description = " ".join(block[2:money_index]).strip()
        movement = abs(_pdf_amount(block[money_index]))
        balance_after = _pdf_amount(block[money_index + 1])
        signed_delta = (balance_after - previous_balance).quantize(Decimal("0.01"))
        if abs(abs(signed_delta) - movement) > Decimal("0.01"):
            raise StatementImportError(
                f"Revolut PDF balance progression failed near {description or block[0]}"
            )
        direction = "credit" if signed_delta >= 0 else "debit"
        details = block[money_index + 2 :]
        external_id = ""
        for line in details:
            match = _TX_ID_RE.search(line)
            if match:
                external_id = match.group(1)
                break
        counterparty_name, counterparty_iban = _counterparty(description, details)
        original_amount, original_currency, fee = _foreign_metadata(details)
        transaction_type = _transaction_type(description, direction, details)
        if direction == "credit":
            credits += signed_delta
        else:
            debits += -signed_delta
        transactions.append(
            ParsedStatementTransaction(
                sequence_number=len(transactions) + 1,
                booking_date=_parse_english_date(block[1]),
                value_date=_parse_english_date(block[0]),
                transaction_type=transaction_type,
                signed_amount=signed_delta,
                counterparty_name=counterparty_name,
                counterparty_iban=counterparty_iban,
                description_lines=tuple([description, *details]),
                merchant_occurred_at=_parse_english_date(block[0]),
                original_amount=original_amount,
                original_currency=original_currency,
                fee_amount=fee,
                external_transaction_id=external_id,
                balance_after=balance_after,
                source_format="pdf",
                source_state="COMPLETED",
            )
        )
        previous_balance = balance_after
    if not transactions:
        raise StatementImportError("No completed Revolut PDF transactions were found")
    if credits.quantize(Decimal("0.01")) != expected_credits or debits.quantize(Decimal("0.01")) != expected_debits:
        raise StatementImportError("Revolut PDF money-in/money-out totals do not reconcile")
    if previous_balance.quantize(Decimal("0.01")) != expected_closing:
        raise StatementImportError("Revolut PDF closing balance does not reconcile")
    return tuple(transactions)


def _balance_summary(page_text: str) -> list[tuple[str, Decimal, Decimal, Decimal, Decimal]]:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    summaries: list[tuple[str, Decimal, Decimal, Decimal, Decimal]] = []
    try:
        index = lines.index("Balance summary") + 1
    except ValueError as exc:
        raise StatementImportError("The Revolut PDF balance summary is missing") from exc
    while index < len(lines):
        if lines[index] == "Total":
            break
        if lines[index] != "Account (Current Account)":
            index += 1
            continue
        index += 1
        label = "Current"
        if index < len(lines) and not _EUR_RE.fullmatch(lines[index]):
            label = lines[index]
            index += 1
        amounts: list[Decimal] = []
        while index < len(lines) and len(amounts) < 4:
            if _EUR_RE.fullmatch(lines[index]):
                amounts.append(abs(_pdf_amount(lines[index])))
            index += 1
        if len(amounts) != 4:
            raise StatementImportError("A Revolut PDF account summary row is incomplete")
        summaries.append((label, amounts[0], amounts[1], amounts[2], amounts[3]))
    if not summaries:
        raise StatementImportError("The Revolut PDF contains no account summary rows")
    return summaries


def parse_revolut_pdf(content: bytes) -> tuple[ParsedBankStatement, ...]:
    if not content.startswith(b"%PDF"):
        raise StatementImportError("The selected file is not a PDF")
    checksum = hashlib.sha256(content).hexdigest()
    try:
        document = fitz.open(stream=content, filetype="pdf")
        page_one = document[0].get_text("text")
        text = "\n".join(page.get_text("text") for page in document)
    except Exception as exc:
        raise StatementImportError(f"Could not read this Revolut PDF: {exc}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass
    if "Revolut Bank UAB" not in text or "Balance summary" not in page_one:
        raise StatementImportError("This PDF is not a recognized Revolut account statement")
    generated = re.search(r"Generated on the ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    statement_date = _parse_english_date(generated.group(1)) if generated else datetime.utcnow()
    main_heading = re.search(r"Account transactions from ([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if not main_heading:
        raise StatementImportError("The Revolut PDF statement period is missing")
    period_start = _parse_english_date(main_heading.group(1))
    period_end = _parse_english_date(main_heading.group(2))
    summaries = _balance_summary(page_one)

    ibans = [_normalize_iban(value) for value in _IBAN_RE.findall(page_one.upper())]
    main_iban = next((value for value in ibans if value.startswith("BE")), ibans[0] if ibans else "")
    owner_lines = [line.strip() for line in page_one.splitlines() if line.strip()]
    owner_name = owner_lines[owner_lines.index("Balance summary") - 1] if "Balance summary" in owner_lines else "Current"

    main_start = main_heading.start()
    reverted_match = re.search(r"Reverted from [A-Z][a-z]+ \d{1,2}, \d{4} to [A-Z][a-z]+ \d{1,2}, \d{4}", text[main_start:])
    secondary_match = re.search(r"([^\n]+)'s account transactions from [A-Z][a-z]+ \d{1,2}, \d{4} to [A-Z][a-z]+ \d{1,2}, \d{4}", text)
    main_end_candidates = [len(text)]
    if reverted_match:
        main_end_candidates.append(main_start + reverted_match.start())
    if secondary_match:
        main_end_candidates.append(secondary_match.start())
    main_end = min(main_end_candidates)
    main_summary = summaries[0]
    main_transactions = _parse_pdf_section(
        text[main_start:main_end],
        opening_balance=main_summary[1],
        expected_debits=main_summary[2],
        expected_credits=main_summary[3],
        expected_closing=main_summary[4],
    )
    reverted_count = 0
    if reverted_match:
        reverted_start = main_start + reverted_match.start()
        reverted_end = secondary_match.start() if secondary_match else len(text)
        reverted_count = len(_TX_ID_RE.findall(text[reverted_start:reverted_end]))

    statements = [
        ParsedBankStatement(
            provider="revolut",
            statement_number=0,
            statement_date=statement_date,
            account_iban=main_iban,
            currency="EUR",
            period_start=period_start,
            period_end=period_end,
            opening_balance=main_summary[1],
            total_credits=main_summary[3],
            total_debits=main_summary[2],
            closing_balance=main_summary[4],
            transactions=main_transactions,
            source_checksum_sha256=checksum,
            account_identity="current",
            account_display_name=owner_name,
            source_format="pdf",
            ignored_transaction_count=reverted_count,
            validation_details={
                "balance_progression_reconciled": True,
                "summary_reconciled": True,
                "ignored_reverted_transactions": reverted_count,
            },
        )
    ]

    if secondary_match and len(summaries) > 1:
        secondary_name = secondary_match.group(1).strip()
        secondary_summary = summaries[1]
        secondary_start = secondary_match.start()
        secondary_transactions = _parse_pdf_section(
            text[secondary_start:],
            opening_balance=secondary_summary[1],
            expected_debits=secondary_summary[2],
            expected_credits=secondary_summary[3],
            expected_closing=secondary_summary[4],
        )
        statements.append(
            ParsedBankStatement(
                provider="revolut",
                statement_number=0,
                statement_date=statement_date,
                account_iban="",
                currency="EUR",
                period_start=period_start,
                period_end=period_end,
                opening_balance=secondary_summary[1],
                total_credits=secondary_summary[3],
                total_debits=secondary_summary[2],
                closing_balance=secondary_summary[4],
                transactions=secondary_transactions,
                source_checksum_sha256=checksum,
                account_identity=f"current:{re.sub(r'[^a-z0-9]+', '', secondary_name.casefold())}",
                account_display_name=secondary_name,
                source_format="pdf",
                validation_details={"balance_progression_reconciled": True, "summary_reconciled": True},
            )
        )
    return tuple(statements)
