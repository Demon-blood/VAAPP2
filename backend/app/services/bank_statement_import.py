from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    BankAccount,
    BankConnection,
    BankStatementImport,
    BankTransaction,
    HistoricalFinancialTransaction,
)
from app.services.audit import write_audit
from app.services.beobank_statement_parser import (
    ParsedBankStatement,
    ParsedStatementTransaction,
    StatementImportError,
    _normalize_iban,
    _normalize_party,
    parse_beobank_statement,
)
from app.services.revolut_statement_parser import parse_revolut_pdf, parse_revolut_xlsx

def _income_kind_from_text(direction: str, text: str, is_internal: bool) -> str:
    if direction != "credit":
        return ""
    if is_internal:
        return "internal_transfer"
    normalized = f" {text.casefold()} "
    if "vakantiegeld" in normalized or "vacances annuelles" in normalized:
        return "holiday_pay"
    if "jobbonus" in normalized or re.search(r"\bbonus\b", normalized):
        return "bonus"
    if any(term in normalized for term in ("werkloosheid", "unemployment", "uitkering")):
        return "benefit"
    if any(term in normalized for term in ("kohierartikel", "service public federal finances", "fod financ")):
        return "tax_refund"
    if any(term in normalized for term in ("refund", "terugbetaling", "restitutie")):
        return "refund"
    if any(term in normalized for term in ("/a/ loon", " loon ", "salary", "salaris", "wage")):
        return "salary"
    return "irregular_income"


def _income_kind(transaction: ParsedStatementTransaction, is_internal: bool) -> str:
    return _income_kind_from_text(
        transaction.direction,
        f"{transaction.counterparty_name} {transaction.remittance}",
        is_internal,
    )


def _is_internal(
    transaction: ParsedStatementTransaction,
    *,
    own_ibans: set[str],
    connected_account_names: set[str],
) -> bool:
    if transaction.counterparty_iban and transaction.counterparty_iban in own_ibans:
        return True
    merchant = _normalize_party(transaction.counterparty_name)
    type_name = transaction.transaction_type.casefold()
    # Revolut FX pockets and Robo portfolio are movements inside the user's Revolut
    # relationship, not consumption. They must never inflate spending.
    if type_name == "exchange" or "robportfolio" in merchant or "roboportfolio" in _normalize_party(transaction.remittance):
        return True
    # Beobank exposes Revolut card top-ups as ordinary Mastercard purchases and does
    # not include the receiving IBAN. Only treat them as internal from this rule when
    # a connected Revolut account exists. Historical cross-statement matching can
    # independently confirm them even before Enable Banking sees Revolut.
    if merchant.startswith("revolut") and any("revolut" in name for name in connected_account_names):
        return True
    return False


def _categorize(transaction: ParsedStatementTransaction, is_internal: bool) -> str:
    if is_internal:
        normalized = _normalize_party(f"{transaction.transaction_type} {transaction.counterparty_name} {transaction.remittance}")
        if "roboportfolio" in normalized:
            return "investment_contribution"
        if transaction.transaction_type.casefold() == "exchange":
            return "internal_fx"
        return "internal_transfer"
    from app.services.financial_autopilot import categorize_transaction_text

    return categorize_transaction_text(
        " ".join(
            (
                transaction.transaction_type,
                transaction.counterparty_name,
                transaction.remittance,
            )
        )
    )




def _row_metadata(row: HistoricalFinancialTransaction) -> dict[str, Any]:
    try:
        value = json.loads(row.raw_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _row_is_intrinsically_internal(row: HistoricalFinancialTransaction) -> bool:
    metadata = _row_metadata(row)
    if metadata.get("cross_statement_internal") is True:
        return True
    normalized = _normalize_party(f"{row.transaction_type} {row.counterparty_name} {row.remittance}")
    return row.transaction_type.casefold() == "exchange" or "roboportfolio" in normalized


def _mark_cross_statement_internal(row: HistoricalFinancialTransaction, reason: str) -> None:
    metadata = _row_metadata(row)
    metadata["cross_statement_internal"] = True
    metadata["cross_statement_reason"] = reason
    row.raw_json = json.dumps(metadata, ensure_ascii=False, default=str, separators=(",", ":"))[:30000]
    row.is_internal_transfer = True
    row.category = "internal_transfer"
    row.income_kind = "internal_transfer" if row.direction == "credit" else ""


def _match_score(history: HistoricalFinancialTransaction, bank: BankTransaction) -> float:
    score = 0.0
    if history.booking_date.date() == (bank.booking_date.date() if bank.booking_date else None):
        score += 0.35
    elif bank.booking_date and abs((history.booking_date.date() - bank.booking_date.date()).days) <= 2:
        score += 0.20
    if history.counterparty_iban and history.counterparty_iban == _normalize_iban(bank.counterparty_iban):
        score += 0.45
    history_name = _normalize_party(history.counterparty_name)
    bank_name = _normalize_party(bank.counterparty_name)
    if history_name and bank_name:
        similarity = difflib.SequenceMatcher(a=history_name, b=bank_name).ratio()
        score += min(0.30, similarity * 0.30)
    if history.remittance and bank.remittance:
        history_reference = _normalize_party(history.remittance[-400:])
        bank_reference = _normalize_party(bank.remittance[-400:])
        if history_reference and bank_reference:
            similarity = difflib.SequenceMatcher(a=history_reference, b=bank_reference).ratio()
            score += min(0.15, similarity * 0.15)
    return min(score, 1.0)


async def recategorize_historical_statement_history(db: AsyncSession) -> dict[str, int]:
    """Apply current account aliases/category policy to imported historical evidence."""
    accounts = list((await db.execute(select(BankAccount))).scalars())
    own_ibans = {_normalize_iban(account.iban) for account in accounts if account.iban}
    own_by_iban = {_normalize_iban(account.iban): account for account in accounts if account.iban}
    accounts_by_id = {account.id: account for account in accounts}
    statements = {row.id: row for row in (await db.execute(select(BankStatementImport))).scalars()}
    connections = {row.id: row for row in (await db.execute(select(BankConnection))).scalars()}
    connected_names = {_normalize_party(account.name) for account in accounts if account.name}
    connected_names.update(
        _normalize_party(connection.institution_name)
        for connection in connections.values()
        if connection.institution_name
    )
    has_revolut = any("revolut" in name for name in connected_names)
    rows = list((await db.execute(select(HistoricalFinancialTransaction))).scalars())
    from app.services.financial_autopilot import categorize_transaction_text

    changed = 0
    internal_marked = 0
    for row in rows:
        exact_internal = bool(row.counterparty_iban and _normalize_iban(row.counterparty_iban) in own_ibans)
        revolut_alias = bool(has_revolut and _normalize_party(row.counterparty_name).startswith("revolut"))
        is_internal = exact_internal or revolut_alias or _row_is_intrinsically_internal(row)
        normalized = _normalize_party(f"{row.transaction_type} {row.counterparty_name} {row.remittance}")
        statement = statements.get(row.statement_import_id)
        source_account = accounts_by_id.get(statement.matched_bank_account_id) if statement and statement.matched_bank_account_id else own_by_iban.get(_normalize_iban(row.account_iban))
        counterparty_account = own_by_iban.get(_normalize_iban(row.counterparty_iban)) if row.counterparty_iban else None
        if exact_internal and source_account is not None and counterparty_account is not None and source_account.account_scope != counterparty_account.account_scope:
            if source_account.account_scope == "pro" and row.direction == "debit":
                category = "owner_draw"
            elif source_account.account_scope == "personal" and row.direction == "debit":
                category = "owner_contribution"
            else:
                category = "owner_transfer"
        elif is_internal and "roboportfolio" in normalized:
            category = "investment_contribution"
        elif is_internal and row.transaction_type.casefold() == "exchange":
            category = "internal_fx"
        else:
            category = "internal_transfer" if is_internal else categorize_transaction_text(
                f"{row.transaction_type} {row.counterparty_name} {row.remittance}"
            )
        income_kind = _income_kind_from_text(
            row.direction,
            f"{row.counterparty_name} {row.remittance}",
            is_internal,
        )
        if (
            row.is_internal_transfer != is_internal
            or row.category != category
            or row.income_kind != income_kind
        ):
            row.is_internal_transfer = is_internal
            row.category = category
            row.income_kind = income_kind
            changed += 1
            if is_internal:
                internal_marked += 1
    if changed:
        await db.commit()
    return {"reviewed": len(rows), "changed": changed, "internal_marked": internal_marked}


async def reconcile_cross_statement_internal_transfers(db: AsyncSession) -> dict[str, int]:
    """Confirm transfers that appear on both imported account histories.

    This is intentionally narrow. It currently confirms Beobank -> Revolut card top-ups
    and transfers between two Revolut account sections. A coincidental equal amount is
    never enough by itself.
    """
    statements = {row.id: row for row in (await db.execute(select(BankStatementImport))).scalars()}
    rows = list(
        (
            await db.execute(
                select(HistoricalFinancialTransaction).order_by(
                    HistoricalFinancialTransaction.booking_date,
                    HistoricalFinancialTransaction.id,
                )
            )
        ).scalars()
    )
    changed_ids: set[int] = set()

    beobank_revolut_debits = [
        row
        for row in rows
        if statements.get(row.statement_import_id) is not None
        and statements[row.statement_import_id].provider == "beobank"
        and row.direction == "debit"
        and _normalize_party(row.counterparty_name).startswith("revolut")
    ]
    revolut_topups = [
        row
        for row in rows
        if statements.get(row.statement_import_id) is not None
        and statements[row.statement_import_id].provider == "revolut"
        and row.direction == "credit"
        and (row.transaction_type.casefold() == "topup" or _normalize_party(row.counterparty_name).startswith("topupby"))
    ]
    used_topups: set[int] = set()
    for debit in beobank_revolut_debits:
        candidates = [
            credit
            for credit in revolut_topups
            if credit.id not in used_topups
            and credit.account_scope == debit.account_scope
            and credit.currency == debit.currency
            and credit.amount == debit.amount
            and abs((credit.booking_date.date() - debit.booking_date.date()).days) <= 3
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda credit: (
                abs((credit.booking_date - debit.booking_date).total_seconds()),
                credit.id,
            )
        )
        credit = candidates[0]
        used_topups.add(credit.id)
        _mark_cross_statement_internal(debit, "beobank_revolut_topup_pair")
        _mark_cross_statement_internal(credit, "beobank_revolut_topup_pair")
        changed_ids.update((debit.id, credit.id))

    def statement_display(statement: BankStatementImport) -> str:
        try:
            payload = json.loads(statement.validation_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        return str(payload.get("account_display_name") or "")

    revolut_transfers = [
        row
        for row in rows
        if statements.get(row.statement_import_id) is not None
        and statements[row.statement_import_id].provider == "revolut"
        and row.transaction_type.casefold() == "transfer"
        and "roboportfolio" not in _normalize_party(f"{row.counterparty_name} {row.remittance}")
    ]
    used_pairs: set[int] = set()
    for left in revolut_transfers:
        if left.id in used_pairs:
            continue
        left_statement = statements[left.statement_import_id]
        candidates: list[tuple[float, HistoricalFinancialTransaction]] = []
        for right in revolut_transfers:
            if right.id == left.id or right.id in used_pairs or right.statement_import_id == left.statement_import_id:
                continue
            if right.direction == left.direction or right.amount != left.amount or right.currency != left.currency:
                continue
            if abs((right.booking_date.date() - left.booking_date.date()).days) > 1:
                continue
            right_statement = statements[right.statement_import_id]
            right_display = _normalize_party(statement_display(right_statement))
            left_display = _normalize_party(statement_display(left_statement))
            left_party = _normalize_party(f"{left.counterparty_name} {left.remittance}")
            right_party = _normalize_party(f"{right.counterparty_name} {right.remittance}")
            evidence = 0.0
            if right_display and right_display in left_party:
                evidence += 0.55
            elif right_display and difflib.SequenceMatcher(a=right_display, b=left_party).ratio() >= 0.45:
                evidence += 0.35
            if left_display and left_display in right_party:
                evidence += 0.55
            elif left_display and difflib.SequenceMatcher(a=left_display, b=right_party).ratio() >= 0.45:
                evidence += 0.35
            if evidence >= 0.55:
                candidates.append((evidence, right))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (-item[0], item[1].id))
        right = candidates[0][1]
        _mark_cross_statement_internal(left, "revolut_account_pair")
        _mark_cross_statement_internal(right, "revolut_account_pair")
        used_pairs.update((left.id, right.id))
        changed_ids.update((left.id, right.id))

    if changed_ids:
        await db.commit()
    return {"marked_internal": len(changed_ids)}


async def reconcile_statement_transactions_with_bank(db: AsyncSession) -> dict[str, int]:
    # Statements may be imported before their live account is connected. Re-attach
    # them by exact normalized IBAN on every reconciliation so import order is irrelevant.
    accounts = list((await db.execute(select(BankAccount))).scalars())
    accounts_by_iban = {_normalize_iban(account.iban): account for account in accounts if account.iban}
    unattached = list(
        (
            await db.execute(
                select(BankStatementImport).where(BankStatementImport.matched_bank_account_id.is_(None))
            )
        ).scalars()
    )
    attached = 0
    for statement in unattached:
        account = accounts_by_iban.get(_normalize_iban(statement.account_iban))
        if account is None:
            continue
        statement.matched_bank_account_id = account.id
        statement.account_scope = account.account_scope
        historical_rows = list(
            (
                await db.execute(
                    select(HistoricalFinancialTransaction).where(
                        HistoricalFinancialTransaction.statement_import_id == statement.id
                    )
                )
            ).scalars()
        )
        for history in historical_rows:
            history.account_scope = account.account_scope
        attached += 1
    if attached:
        await db.commit()

    recategorized = await recategorize_historical_statement_history(db)
    cross_statement = await reconcile_cross_statement_internal_transfers(db)
    propagated_internal = 0
    linked_internal = list(
        (
            await db.execute(
                select(HistoricalFinancialTransaction).where(
                    HistoricalFinancialTransaction.is_internal_transfer.is_(True),
                    HistoricalFinancialTransaction.matched_bank_transaction_id.is_not(None),
                )
            )
        ).scalars()
    )
    for history in linked_internal:
        bank_row = await db.get(BankTransaction, history.matched_bank_transaction_id)
        if bank_row is not None and (not bank_row.is_internal_transfer or bank_row.category != "internal_transfer"):
            bank_row.is_internal_transfer = True
            bank_row.category = "internal_transfer"
            propagated_internal += 1
    if propagated_internal:
        await db.commit()
    rows = list(
        (
            await db.execute(
                select(HistoricalFinancialTransaction)
                .where(HistoricalFinancialTransaction.matched_bank_transaction_id.is_(None))
                .order_by(HistoricalFinancialTransaction.booking_date, HistoricalFinancialTransaction.id)
            )
        ).scalars()
    )
    matched = 0
    reviewed = 0
    for row in rows:
        statement = await db.get(BankStatementImport, row.statement_import_id)
        if statement is None or statement.matched_bank_account_id is None:
            continue
        candidates = list(
            (
                await db.execute(
                    select(BankTransaction).where(
                        BankTransaction.bank_account_id == statement.matched_bank_account_id,
                        BankTransaction.direction == row.direction,
                        BankTransaction.amount == row.amount,
                        BankTransaction.booking_date >= row.booking_date - timedelta(days=2),
                        BankTransaction.booking_date <= row.booking_date + timedelta(days=2, hours=23, minutes=59),
                        ~BankTransaction.id.in_(
                            select(HistoricalFinancialTransaction.matched_bank_transaction_id).where(
                                HistoricalFinancialTransaction.matched_bank_transaction_id.is_not(None)
                            )
                        ),
                    )
                )
            ).scalars()
        )
        reviewed += 1
        if not candidates:
            continue
        ranked = sorted(((_match_score(row, candidate), candidate) for candidate in candidates), key=lambda item: item[0], reverse=True)
        score, candidate = ranked[0]
        # Date + exact amount is necessary but not sufficient when a merchant has many
        # equal-value transactions on the same day. Require merchant/reference evidence
        # unless there is only one possible bank transaction.
        threshold = 0.55 if len(candidates) == 1 else 0.64
        if score < threshold:
            continue
        row.matched_bank_transaction_id = candidate.id
        row.match_confidence = Decimal(f"{score:.4f}")
        if row.is_internal_transfer:
            candidate.is_internal_transfer = True
            candidate.category = "internal_transfer"
            propagated_internal += 1
        matched += 1
    if matched:
        await db.commit()
    return {
        "reviewed": reviewed,
        "matched": matched,
        "attached_accounts": attached,
        "recategorized": recategorized["changed"],
        "cross_statement_internal": cross_statement["marked_internal"],
        "propagated_internal_to_bank": propagated_internal,
    }


def _validation_payload(parsed: ParsedBankStatement, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(existing or {})
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    source_entry = {"format": parsed.source_format, "sha256": parsed.source_checksum_sha256}
    if source_entry not in sources:
        sources.append(source_entry)
    payload.update(
        {
            "sequence_complete": True,
            "credits_reconciled": True,
            "debits_reconciled": True,
            "closing_balance_reconciled": True,
            "account_identity": parsed.account_identity,
            "account_display_name": parsed.account_display_name,
            "sources": sources,
            "ignored_transactions": max(int(payload.get("ignored_transactions") or 0), parsed.ignored_transaction_count),
        }
    )
    payload.update(parsed.validation_details)
    if any(item.get("format") == "xlsx" for item in sources):
        payload["authoritative_source"] = "xlsx"
    elif sources:
        payload["authoritative_source"] = sources[0].get("format")
    return payload


def _history_raw_payload(item: ParsedStatementTransaction) -> dict[str, Any]:
    return {
        "sequence_number": item.sequence_number,
        "transaction_type": item.transaction_type,
        "description_lines": item.description_lines,
        "signed_amount": str(item.signed_amount),
        "external_transaction_id": item.external_transaction_id,
        "balance_after": str(item.balance_after) if item.balance_after is not None else None,
        "source_state": item.source_state,
        "source_format": item.source_format,
        "metadata": item.metadata,
        "sources": {
            item.source_format: {
                "transaction_type": item.transaction_type,
                "signed_amount": str(item.signed_amount),
                "external_transaction_id": item.external_transaction_id,
                "balance_after": str(item.balance_after) if item.balance_after is not None else None,
                "description_lines": item.description_lines,
                "metadata": item.metadata,
            }
        },
    }


def _merge_history_raw(row: HistoricalFinancialTransaction, item: ParsedStatementTransaction) -> None:
    payload = _row_metadata(row)
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    sources[item.source_format] = {
        "transaction_type": item.transaction_type,
        "signed_amount": str(item.signed_amount),
        "external_transaction_id": item.external_transaction_id,
        "balance_after": str(item.balance_after) if item.balance_after is not None else None,
        "description_lines": item.description_lines,
        "metadata": item.metadata,
    }
    payload["sources"] = sources
    if item.external_transaction_id:
        payload["external_transaction_id"] = item.external_transaction_id
    if item.balance_after is not None:
        payload["balance_after"] = str(item.balance_after)
    row.raw_json = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))[:30000]


async def _match_account_for_statement(
    db: AsyncSession,
    parsed: ParsedBankStatement,
) -> BankAccount | None:
    accounts = list((await db.execute(select(BankAccount))).scalars())
    if parsed.account_iban:
        exact = next((item for item in accounts if _normalize_iban(item.iban) == parsed.account_iban), None)
        if exact is not None:
            return exact
    # Do not guess among multiple Revolut accounts. Once the PDF supplies an exact
    # Belgian IBAN the canonical XLSX import is attached automatically by enrichment.
    if parsed.provider == "revolut" and parsed.account_identity == "current":
        connections = {row.id: row for row in (await db.execute(select(BankConnection))).scalars()}
        candidates = [
            account
            for account in accounts
            if "revolut" in (connections.get(account.bank_connection_id).institution_name.casefold()
                              if connections.get(account.bank_connection_id) else "")
        ]
        if len(candidates) == 1:
            return candidates[0]
    return None


async def _merge_existing_statement(
    db: AsyncSession,
    existing: BankStatementImport,
    parsed: ParsedBankStatement,
    *,
    filename: str,
) -> dict[str, Any]:
    try:
        validation = json.loads(existing.validation_json or "{}")
        if not isinstance(validation, dict):
            validation = {}
    except (TypeError, ValueError, json.JSONDecodeError):
        validation = {}
    existing.validation_json = json.dumps(_validation_payload(parsed, validation), separators=(",", ":"))
    existing.period_start = min(existing.period_start or parsed.period_start, parsed.period_start)
    existing.period_end = max(existing.period_end or parsed.period_end, parsed.period_end)
    existing.statement_date = max(existing.statement_date or parsed.statement_date, parsed.statement_date)
    if parsed.account_iban and not existing.account_iban:
        existing.account_iban = parsed.account_iban
    account = await _match_account_for_statement(db, parsed)
    if account is not None:
        existing.matched_bank_account_id = account.id
        existing.account_scope = account.account_scope
    rows = list(
        (
            await db.execute(
                select(HistoricalFinancialTransaction)
                .where(HistoricalFinancialTransaction.statement_import_id == existing.id)
                .order_by(HistoricalFinancialTransaction.sequence_number)
            )
        ).scalars()
    )
    if len(rows) != len(parsed.transactions):
        raise StatementImportError(
            "The paired Revolut PDF/XLSX transaction counts differ; merge blocked instead of risking duplicates"
        )
    existing_formats = {item.get("format") for item in validation.get("sources", []) if isinstance(item, dict)}
    xlsx_is_existing = "xlsx" in existing_formats
    for row, item in zip(rows, parsed.transactions):
        if row.sequence_number != item.sequence_number:
            raise StatementImportError("The paired Revolut PDF/XLSX transaction sequence differs; merge blocked")
        # XLSX is authoritative for cash movement/status. PDF remains authoritative
        # for transaction IDs, counterparties, IBANs, references and FX detail.
        if parsed.source_format == "xlsx":
            row.booking_date = item.booking_date
            row.value_date = item.value_date
            row.amount = item.amount
            row.direction = item.direction
            row.transaction_type = item.transaction_type
            row.merchant_occurred_at = item.merchant_occurred_at
            row.fee_amount = item.fee_amount
            if not xlsx_is_existing:
                existing.source_checksum_sha256 = parsed.source_checksum_sha256
                existing.original_filename = filename[:1000]
        else:
            if item.counterparty_name:
                row.counterparty_name = item.counterparty_name
            if item.counterparty_iban:
                row.counterparty_iban = item.counterparty_iban
            if item.remittance:
                row.remittance = item.remittance
            if item.original_amount is not None:
                row.original_amount = item.original_amount
                row.original_currency = item.original_currency
            if item.exchange_rate is not None:
                row.exchange_rate = item.exchange_rate
            if row.fee_amount is None and item.fee_amount is not None:
                row.fee_amount = item.fee_amount
        if parsed.account_iban:
            row.account_iban = parsed.account_iban
        if account is not None:
            row.account_scope = account.account_scope
        _merge_history_raw(row, item)
    await db.commit()
    reconciliation = await reconcile_statement_transactions_with_bank(db)
    return {
        "import_id": existing.id,
        "filename": existing.original_filename,
        "statement_number": existing.statement_number,
        "transactions": existing.transaction_count,
        "duplicate": True,
        "enriched": True,
        "provider": existing.provider,
        "source_format": parsed.source_format,
        "validation": existing.validation_status,
        "bank_matches": reconciliation["matched"],
    }


async def _import_parsed_statement(
    db: AsyncSession,
    *,
    parsed: ParsedBankStatement,
    filename: str,
    fallback_scope: str,
) -> dict[str, Any]:
    existing = (
        await db.execute(select(BankStatementImport).where(BankStatementImport.statement_key == parsed.statement_key))
    ).scalar_one_or_none()
    if existing is not None:
        if parsed.provider == "revolut":
            return await _merge_existing_statement(db, existing, parsed, filename=filename)
        return {
            "import_id": existing.id,
            "filename": existing.original_filename,
            "statement_number": existing.statement_number,
            "transactions": existing.transaction_count,
            "duplicate": True,
            "enriched": False,
            "provider": existing.provider,
            "source_format": parsed.source_format,
            "validation": existing.validation_status,
        }

    accounts = list((await db.execute(select(BankAccount))).scalars())
    account = await _match_account_for_statement(db, parsed)
    account_scope = account.account_scope if account is not None else fallback_scope
    own_ibans = {_normalize_iban(item.iban) for item in accounts if item.iban}
    connections = {row.id: row for row in (await db.execute(select(BankConnection))).scalars()}
    connected_names = {_normalize_party(item.name) for item in accounts if item.name}
    connected_names.update(
        _normalize_party(connection.institution_name)
        for connection in connections.values()
        if connection.institution_name
    )

    statement = BankStatementImport(
        provider=parsed.provider,
        statement_key=parsed.statement_key,
        source_checksum_sha256=parsed.source_checksum_sha256,
        original_filename=filename[:1000],
        account_iban=parsed.account_iban,
        account_scope=account_scope,
        matched_bank_account_id=account.id if account is not None else None,
        statement_number=parsed.statement_number,
        statement_date=parsed.statement_date,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        opening_balance=parsed.opening_balance,
        total_credits=parsed.total_credits,
        total_debits=parsed.total_debits,
        closing_balance=parsed.closing_balance,
        currency=parsed.currency,
        transaction_count=len(parsed.transactions),
        validation_status="verified",
        validation_json=json.dumps(_validation_payload(parsed), separators=(",", ":")),
    )
    db.add(statement)
    await db.flush()

    for item in parsed.transactions:
        internal = _is_internal(item, own_ibans=own_ibans, connected_account_names=connected_names)
        fingerprint = hashlib.sha256(f"{parsed.statement_key}:{item.sequence_number}".encode()).hexdigest()
        row = HistoricalFinancialTransaction(
            statement_import_id=statement.id,
            transaction_fingerprint=fingerprint,
            sequence_number=item.sequence_number,
            account_iban=parsed.account_iban,
            account_scope=account_scope,
            booking_date=item.booking_date,
            value_date=item.value_date,
            amount=item.amount,
            currency=parsed.currency,
            direction=item.direction,
            transaction_type=item.transaction_type,
            counterparty_name=item.counterparty_name,
            counterparty_iban=item.counterparty_iban,
            remittance=item.remittance,
            category=_categorize(item, internal),
            income_kind=_income_kind(item, internal),
            is_internal_transfer=internal,
            merchant_occurred_at=item.merchant_occurred_at,
            original_amount=item.original_amount,
            original_currency=item.original_currency,
            exchange_rate=item.exchange_rate,
            fee_amount=item.fee_amount,
            raw_json=json.dumps(_history_raw_payload(item), ensure_ascii=False, default=str, separators=(",", ":")),
        )
        db.add(row)
    await db.commit()
    reconciliation = await reconcile_statement_transactions_with_bank(db)
    await write_audit(
        db,
        "bank_statement_imported",
        entity_type="bank_statement_import",
        entity_id=str(statement.id),
        details={
            "provider": parsed.provider,
            "source_format": parsed.source_format,
            "statement_number": parsed.statement_number,
            "transaction_count": len(parsed.transactions),
            "ignored_transactions": parsed.ignored_transaction_count,
            "matched_bank_account_id": statement.matched_bank_account_id,
            "bank_matches": reconciliation["matched"],
            "cross_statement_internal": reconciliation.get("cross_statement_internal", 0),
        },
    )
    await db.commit()
    return {
        "import_id": statement.id,
        "filename": filename,
        "statement_number": parsed.statement_number,
        "transactions": len(parsed.transactions),
        "duplicate": False,
        "enriched": False,
        "provider": parsed.provider,
        "source_format": parsed.source_format,
        "ignored_transactions": parsed.ignored_transaction_count,
        "validation": "verified",
        "bank_matches": reconciliation["matched"],
    }


def parse_statement_file(filename: str, content: bytes) -> tuple[ParsedBankStatement, ...]:
    extension = filename.casefold().rsplit(".", 1)[-1] if "." in filename else ""
    if extension == "xlsx":
        return (parse_revolut_xlsx(content),)
    if extension != "pdf":
        raise StatementImportError("Supported financial-history files are PDF and XLSX")
    try:
        return (parse_beobank_statement(content),)
    except StatementImportError as beobank_error:
        try:
            return parse_revolut_pdf(content)
        except StatementImportError as revolut_error:
            raise StatementImportError(
                f"Unrecognized bank statement PDF (Beobank: {beobank_error}; Revolut: {revolut_error})"
            ) from revolut_error


async def import_statement_file_bytes(
    db: AsyncSession,
    *,
    filename: str,
    content: bytes,
    fallback_scope: str = "personal",
) -> list[dict[str, Any]]:
    parsed_statements = parse_statement_file(filename, content)
    results: list[dict[str, Any]] = []
    for parsed in parsed_statements:
        results.append(
            await _import_parsed_statement(
                db,
                parsed=parsed,
                filename=filename,
                fallback_scope=fallback_scope,
            )
        )
    return results


async def import_statement_bytes(
    db: AsyncSession,
    *,
    filename: str,
    content: bytes,
    fallback_scope: str = "personal",
) -> dict[str, Any]:
    """Backward-compatible single-statement entry point used by existing tests/callers."""
    parsed = parse_beobank_statement(content)
    return await _import_parsed_statement(
        db,
        parsed=parsed,
        filename=filename,
        fallback_scope=fallback_scope,
    )


async def statement_history_summary(db: AsyncSession) -> dict[str, Any]:
    imports = list((await db.execute(select(BankStatementImport).order_by(BankStatementImport.period_end))).scalars())
    transactions = list((await db.execute(select(HistoricalFinancialTransaction))).scalars())
    if not imports:
        return {
            "statements": 0,
            "transactions": 0,
            "matched_to_bank": 0,
            "historical_only": 0,
            "internal_transfers": 0,
            "period_start": None,
            "period_end": None,
            "balance_chain_verified": False,
            "recurring_expenses": [],
            "recurring_income": [],
            "learned_budget": {},
        }

    matched = sum(1 for item in transactions if item.matched_bank_transaction_id is not None)
    internal = sum(1 for item in transactions if item.is_internal_transfer)
    period_start = min((item.period_start for item in imports if item.period_start), default=None)
    period_end = max((item.period_end for item in imports if item.period_end), default=None)

    chain_verified = True
    by_account_identity: dict[str, list[BankStatementImport]] = defaultdict(list)
    for item in imports:
        identity = item.account_iban
        if not identity:
            try:
                payload = json.loads(item.validation_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            identity = f"{item.provider}:{payload.get('account_identity') or item.id}"
        by_account_identity[identity].append(item)
    for account_imports in by_account_identity.values():
        account_imports.sort(key=lambda item: (item.period_end or datetime.min, item.statement_number))
        for previous, current in zip(account_imports, account_imports[1:]):
            if current.opening_balance != previous.closing_balance:
                chain_verified = False
                break

    debit_groups: dict[tuple[str, Decimal], list[HistoricalFinancialTransaction]] = defaultdict(list)
    salary_groups: dict[str, list[HistoricalFinancialTransaction]] = defaultdict(list)
    scope_months: dict[str, set[tuple[int, int]]] = defaultdict(set)
    category_totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for item in transactions:
        if item.matched_bank_transaction_id is not None or item.is_internal_transfer:
            continue
        scope_months[item.account_scope].add((item.booking_date.year, item.booking_date.month))
        if item.direction == "debit":
            category_totals[(item.account_scope, item.category)] += item.amount
            merchant = _normalize_party(item.counterparty_name)
            if merchant:
                debit_groups[(merchant, item.amount)].append(item)
        elif item.income_kind == "refund" or "refund" in item.transaction_type.casefold():
            # Refunds reverse prior consumption; they are not ordinary income.
            key = (item.account_scope, item.category)
            category_totals[key] = max(Decimal("0.00"), category_totals[key] - item.amount)
        elif item.income_kind == "salary":
            salary_groups[_normalize_party(item.counterparty_name)].append(item)

    recurring_expenses: list[dict[str, Any]] = []
    for (_, amount), rows in debit_groups.items():
        months = {(row.booking_date.year, row.booking_date.month) for row in rows}
        counts = Counter((row.booking_date.year, row.booking_date.month) for row in rows)
        if len(months) < 3 or max(counts.values(), default=0) > 2:
            continue
        recurring_expenses.append(
            {
                "merchant": rows[0].counterparty_name,
                "amount": str(amount),
                "currency": rows[0].currency,
                "months": len(months),
                "category": rows[0].category,
            }
        )
    recurring_expenses.sort(key=lambda item: (-item["months"], item["merchant"]))

    recurring_income: list[dict[str, Any]] = []
    for rows in salary_groups.values():
        monthly_totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0.00"))
        for row in rows:
            monthly_totals[(row.booking_date.year, row.booking_date.month)] += row.amount
        if len(monthly_totals) < 2:
            continue
        average = (sum(monthly_totals.values(), Decimal("0.00")) / Decimal(len(monthly_totals))).quantize(Decimal("0.01"))
        recurring_income.append(
            {
                "source": rows[0].counterparty_name,
                "average": str(average),
                "currency": rows[0].currency,
                "months": len(monthly_totals),
            }
        )

    learned_budget: dict[str, dict[str, str]] = defaultdict(dict)
    for (scope, category), total in category_totals.items():
        month_count = max(1, len(scope_months[scope]))
        learned_budget[scope][category] = str(
            (total / Decimal(month_count) * Decimal("1.05")).quantize(Decimal("0.01"))
        )

    return {
        "statements": len(imports),
        "transactions": len(transactions),
        "matched_to_bank": matched,
        "historical_only": len(transactions) - matched,
        "internal_transfers": internal,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "balance_chain_verified": chain_verified,
        "recurring_expenses": recurring_expenses[:30],
        "recurring_income": recurring_income[:20],
        "learned_budget": dict(learned_budget),
    }


async def list_statement_imports(db: AsyncSession, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(
                select(BankStatementImport)
                .order_by(BankStatementImport.statement_date.desc(), BankStatementImport.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            validation = json.loads(row.validation_json or "{}")
            if not isinstance(validation, dict):
                validation = {}
        except (TypeError, ValueError, json.JSONDecodeError):
            validation = {}
        result.append(
            {
                "id": row.id,
                "provider": row.provider,
                "filename": row.original_filename,
                "account_iban_last4": row.account_iban[-4:],
                "account_display_name": validation.get("account_display_name") or "",
                "source_formats": [
                    item.get("format") for item in validation.get("sources", []) if isinstance(item, dict)
                ],
                "account_scope": row.account_scope,
                "statement_number": row.statement_number,
                "statement_date": row.statement_date,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "opening_balance": row.opening_balance,
                "total_credits": row.total_credits,
                "total_debits": row.total_debits,
                "closing_balance": row.closing_balance,
                "currency": row.currency,
                "transaction_count": row.transaction_count,
                "validation_status": row.validation_status,
                "matched_bank_account_id": row.matched_bank_account_id,
                "imported_at": row.imported_at,
            }
        )
    return result
