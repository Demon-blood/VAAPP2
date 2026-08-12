from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    InvestmentIncomeEvent,
    InvestmentPnLEvent,
    InvestmentPortfolio,
    InvestmentPosition,
    InvestmentTransaction,
)
from app.services.beobank_statement_parser import StatementImportError
from app.services.revolut_investment_parser import (
    InvestmentAccountStatement,
    InvestmentPnLStatement,
    looks_like_revolut_investment,
    parse_revolut_investment_account_pdf,
    parse_revolut_investment_account_xlsx,
    parse_revolut_investment_pnl_xlsx,
    validate_revolut_investment_pnl_pdf,
)


class InvestmentImportError(ValueError):
    pass


def _json_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError


def _fingerprint(*values: object) -> str:
    material = "|".join(str(value or "") for value in values)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _portfolio(
    db: AsyncSession,
    *,
    provider: str,
    account_scope: str,
    portfolio_kind: str,
) -> InvestmentPortfolio:
    row = (
        await db.execute(
            select(InvestmentPortfolio).where(
                InvestmentPortfolio.provider == provider,
                InvestmentPortfolio.account_scope == account_scope,
                InvestmentPortfolio.portfolio_kind == portfolio_kind,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        label = "Revolut Robo portfolio" if portfolio_kind == "robo" else "Revolut brokerage"
        row = InvestmentPortfolio(
            provider=provider,
            account_scope=account_scope,
            portfolio_kind=portfolio_kind,
            display_name=label,
        )
        db.add(row)
        await db.flush()
    return row


async def import_account_statement(
    db: AsyncSession,
    statement: InvestmentAccountStatement,
    *,
    account_scope: str,
    filename: str,
) -> dict[str, Any]:
    portfolio = await _portfolio(
        db,
        provider=statement.provider,
        account_scope=account_scope,
        portfolio_kind=statement.portfolio_kind,
    )
    duplicate_source = portfolio.source_checksum_sha256 == statement.checksum_sha256
    previous_period_end = portfolio.period_end
    is_latest_snapshot = previous_period_end is None or statement.period_end >= previous_period_end
    portfolio.period_start = statement.period_start if portfolio.period_start is None else min(portfolio.period_start, statement.period_start)
    portfolio.period_end = statement.period_end if portfolio.period_end is None else max(portfolio.period_end, statement.period_end)
    if statement.account_reference:
        portfolio.external_account_ref = statement.account_reference
    if statement.summary_by_currency and is_latest_snapshot:
        portfolio.summary_json = json.dumps(statement.summary_by_currency, default=_json_decimal, sort_keys=True)
    if is_latest_snapshot:
        portfolio.source_checksum_sha256 = statement.checksum_sha256
    portfolio.imported_at = datetime.utcnow()

    created_transactions = 0
    for tx in statement.transactions:
        fingerprint = _fingerprint(
            statement.provider,
            statement.portfolio_kind,
            tx.booked_at.isoformat(),
            tx.symbol,
            tx.transaction_type,
            tx.side,
            tx.quantity,
            tx.amount,
            tx.currency,
        )
        existing = (
            await db.execute(select(InvestmentTransaction.id).where(InvestmentTransaction.fingerprint == fingerprint))
        ).scalar_one_or_none()
        if existing is not None:
            continue
        db.add(
            InvestmentTransaction(
                portfolio_id=portfolio.id,
                fingerprint=fingerprint,
                booked_at=tx.booked_at,
                symbol=tx.symbol,
                transaction_type=tx.transaction_type,
                side=tx.side,
                quantity=tx.quantity,
                price=tx.price,
                amount=tx.amount,
                currency=tx.currency,
                fx_rate=tx.fx_rate,
                fee=tx.fee,
                commission=tx.commission,
                raw_json=json.dumps(
                    {
                        "source_filename": filename,
                        "source_format": statement.source_format,
                    },
                    separators=(",", ":"),
                ),
            )
        )
        created_transactions += 1

    positions_updated = 0
    if statement.positions and is_latest_snapshot:
        await db.execute(delete(InvestmentPosition).where(InvestmentPosition.portfolio_id == portfolio.id))
        for position in statement.positions:
            db.add(
                InvestmentPosition(
                    portfolio_id=portfolio.id,
                    symbol=position.symbol,
                    company=position.company,
                    isin=position.isin,
                    currency=position.currency,
                    quantity=position.quantity,
                    price=position.price,
                    market_value=position.market_value,
                    allocation_percent=position.allocation_percent,
                    as_of=statement.period_end,
                )
            )
            positions_updated += 1
    await db.commit()
    return {
        "kind": "investment_account",
        "provider": statement.provider,
        "portfolio_kind": statement.portfolio_kind,
        "filename": filename,
        "duplicate": duplicate_source and created_transactions == 0 and positions_updated == 0,
        "transactions_created": created_transactions,
        "positions_updated": positions_updated,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
    }


async def _infer_pnl_portfolio(db: AsyncSession, statement: InvestmentPnLStatement, account_scope: str) -> InvestmentPortfolio:
    candidates = list(
        (
            await db.execute(
                select(InvestmentPortfolio).where(
                    InvestmentPortfolio.provider == statement.provider,
                    InvestmentPortfolio.account_scope == account_scope,
                )
            )
        ).scalars()
    )
    if not candidates:
        # A small P&L export with no preceding account statement is most likely the
        # self-directed brokerage ledger; the account statement can enrich it later.
        return await _portfolio(
            db,
            provider=statement.provider,
            account_scope=account_scope,
            portfolio_kind="brokerage",
        )
    symbols = {row.symbol for row in statement.sells} | {row.symbol for row in statement.income}
    best: tuple[int, InvestmentPortfolio] | None = None
    for candidate in candidates:
        known = {
            value
            for value in (
                await db.execute(
                    select(InvestmentTransaction.symbol).where(
                        InvestmentTransaction.portfolio_id == candidate.id,
                        InvestmentTransaction.symbol != "",
                    )
                )
            ).scalars()
        }
        known |= {
            value
            for value in (
                await db.execute(
                    select(InvestmentPosition.symbol).where(InvestmentPosition.portfolio_id == candidate.id)
                )
            ).scalars()
        }
        score = len(symbols & known)
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is not None and (best[0] > 0 or len(candidates) == 1):
        return best[1]
    raise InvestmentImportError("Could not safely match this Revolut P&L statement to a brokerage/Robo portfolio")


async def import_pnl_statement(
    db: AsyncSession,
    statement: InvestmentPnLStatement,
    *,
    account_scope: str,
    filename: str,
) -> dict[str, Any]:
    portfolio = await _infer_pnl_portfolio(db, statement, account_scope)
    sells_created = 0
    income_created = 0
    for row in statement.sells:
        fingerprint = _fingerprint(
            "pnl",
            portfolio.id,
            row.date_acquired.isoformat(),
            row.date_sold.isoformat(),
            row.symbol,
            row.quantity,
            row.cost_basis,
            row.gross_proceeds,
            row.currency,
        )
        exists = (
            await db.execute(select(InvestmentPnLEvent.id).where(InvestmentPnLEvent.fingerprint == fingerprint))
        ).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(
            InvestmentPnLEvent(
                portfolio_id=portfolio.id,
                fingerprint=fingerprint,
                date_acquired=row.date_acquired,
                date_sold=row.date_sold,
                symbol=row.symbol,
                security_name=row.security_name,
                isin=row.isin,
                country=row.country,
                quantity=row.quantity,
                cost_basis=row.cost_basis,
                gross_proceeds=row.gross_proceeds,
                gross_pnl=row.gross_pnl,
                currency=row.currency,
            )
        )
        sells_created += 1
    for row in statement.income:
        fingerprint = _fingerprint(
            "income",
            portfolio.id,
            row.booked_at.isoformat(),
            row.symbol,
            row.gross_amount,
            row.withholding_tax,
            row.currency,
        )
        exists = (
            await db.execute(select(InvestmentIncomeEvent.id).where(InvestmentIncomeEvent.fingerprint == fingerprint))
        ).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(
            InvestmentIncomeEvent(
                portfolio_id=portfolio.id,
                fingerprint=fingerprint,
                booked_at=row.booked_at,
                symbol=row.symbol,
                security_name=row.security_name,
                isin=row.isin,
                country=row.country,
                gross_amount=row.gross_amount,
                withholding_tax=row.withholding_tax,
                net_amount=row.net_amount,
                currency=row.currency,
            )
        )
        income_created += 1
    await db.commit()
    return {
        "kind": "investment_pnl",
        "provider": statement.provider,
        "portfolio_kind": portfolio.portfolio_kind,
        "filename": filename,
        "duplicate": sells_created == 0 and income_created == 0,
        "realised_events_created": sells_created,
        "income_events_created": income_created,
    }


async def import_revolut_investment_file_bytes(
    db: AsyncSession,
    *,
    filename: str,
    content: bytes,
    account_scope: str,
) -> list[dict[str, Any]]:
    if account_scope not in {"personal", "pro"}:
        raise InvestmentImportError("Investment account scope must be Personal or Pro")
    lower = filename.casefold()
    try:
        if lower.endswith(".xlsx"):
            rows_hint = None
            if "pnl" in lower:
                statement = parse_revolut_investment_pnl_xlsx(content)
                return [await import_pnl_statement(db, statement, account_scope=account_scope, filename=filename)]
            statement = parse_revolut_investment_account_xlsx(content)
            return [await import_account_statement(db, statement, account_scope=account_scope, filename=filename)]
        if lower.endswith(".pdf"):
            if "pnl" in lower:
                # P&L XLSX is authoritative for FIFO rows. The PDF independently
                # validates provider identity, statement period and covered currencies.
                validation = validate_revolut_investment_pnl_pdf(content)
                return [{
                    "kind": "investment_pnl_pdf",
                    "filename": filename,
                    "duplicate": True,
                    "validation_only": True,
                    "validation": validation,
                }]
            statement = parse_revolut_investment_account_pdf(content)
            return [await import_account_statement(db, statement, account_scope=account_scope, filename=filename)]
    except StatementImportError as exc:
        raise InvestmentImportError(str(exc)) from exc
    raise InvestmentImportError("Unsupported Revolut investment file type")


async def investment_history_summary(db: AsyncSession) -> dict[str, Any]:
    portfolios = list((await db.execute(select(InvestmentPortfolio).order_by(InvestmentPortfolio.id))).scalars())
    result: list[dict[str, Any]] = []
    total_positions = 0
    for portfolio in portfolios:
        positions = list(
            (await db.execute(select(InvestmentPosition).where(InvestmentPosition.portfolio_id == portfolio.id))).scalars()
        )
        txs = list(
            (await db.execute(select(InvestmentTransaction).where(InvestmentTransaction.portfolio_id == portfolio.id))).scalars()
        )
        pnl = list(
            (await db.execute(select(InvestmentPnLEvent).where(InvestmentPnLEvent.portfolio_id == portfolio.id))).scalars()
        )
        income = list(
            (await db.execute(select(InvestmentIncomeEvent).where(InvestmentIncomeEvent.portfolio_id == portfolio.id))).scalars()
        )
        values: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for position in positions:
            values[position.currency] += Decimal(position.market_value)
        topups_by_month: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
        recent_cutoff = datetime.utcnow() - timedelta(days=210)
        last_topup: datetime | None = None
        for tx in txs:
            if tx.transaction_type == "cash_top-up" or tx.transaction_type == "cash_top_up":
                if tx.booked_at >= recent_cutoff:
                    topups_by_month[(tx.booked_at.year, tx.booked_at.month)] += Decimal(tx.amount)
                last_topup = tx.booked_at if last_topup is None else max(last_topup, tx.booked_at)
        monthly_topup = Decimal("0")
        if len(topups_by_month) >= 3 and last_topup is not None and last_topup >= datetime.utcnow() - timedelta(days=60):
            values_month = list(topups_by_month.values())[-6:]
            monthly_topup = (sum(values_month, Decimal("0")) / Decimal(len(values_month))).quantize(Decimal("0.01"))
        realised_by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        dividend_by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        tax_by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for event in pnl:
            realised_by_currency[event.currency] += Decimal(event.gross_pnl)
        for event in income:
            dividend_by_currency[event.currency] += Decimal(event.net_amount)
            tax_by_currency[event.currency] += Decimal(event.withholding_tax)
        total_positions += len(positions)
        summary_by_currency = json.loads(portfolio.summary_json or "{}")
        total_value_by_currency: dict[str, Decimal] = {}
        cash_value_by_currency: dict[str, Decimal] = {}
        for currency, summary_row in summary_by_currency.items():
            if not isinstance(summary_row, dict):
                continue
            try:
                total_value_by_currency[str(currency)] = Decimal(str(summary_row.get("total_end") or "0"))
                cash_value_by_currency[str(currency)] = Decimal(str(summary_row.get("cash_end") or "0"))
            except Exception:
                continue
        for currency, market_value in values.items():
            total_value_by_currency.setdefault(currency, market_value)
            cash_value_by_currency.setdefault(currency, Decimal("0"))
        result.append(
            {
                "id": portfolio.id,
                "provider": portfolio.provider,
                "scope": portfolio.account_scope,
                "portfolio_kind": portfolio.portfolio_kind,
                "display_name": portfolio.display_name,
                "period_start": portfolio.period_start.isoformat() if portfolio.period_start else None,
                "period_end": portfolio.period_end.isoformat() if portfolio.period_end else None,
                "positions": len(positions),
                "transactions": len(txs),
                "market_value_by_currency": {key: str(value.quantize(Decimal('0.01'))) for key, value in values.items()},
                "total_value_by_currency": {key: str(value.quantize(Decimal('0.01'))) for key, value in total_value_by_currency.items()},
                "cash_value_by_currency": {key: str(value.quantize(Decimal('0.01'))) for key, value in cash_value_by_currency.items()},
                "realised_pnl_by_currency": {key: str(value.quantize(Decimal('0.01'))) for key, value in realised_by_currency.items()},
                "net_investment_income_by_currency": {key: str(value.quantize(Decimal('0.01'))) for key, value in dividend_by_currency.items()},
                "withholding_tax_by_currency": {key: str(value.quantize(Decimal('0.01'))) for key, value in tax_by_currency.items()},
                "learned_monthly_cash_topup": str(monthly_topup),
                "last_cash_topup_at": last_topup.isoformat() if last_topup else None,
                "topup_months_observed": len(topups_by_month),
                "summary_by_currency": summary_by_currency,
                "top_positions": [
                    {
                        "symbol": position.symbol,
                        "company": position.company,
                        "isin": position.isin,
                        "currency": position.currency,
                        "quantity": str(position.quantity),
                        "price": str(position.price),
                        "market_value": str(position.market_value),
                        "allocation_percent": str(position.allocation_percent),
                    }
                    for position in sorted(positions, key=lambda item: Decimal(item.market_value), reverse=True)[:20]
                ],
            }
        )

    total_value: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    realised: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    income_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    tax_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    monthly_by_scope: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for portfolio in result:
        for currency, value in portfolio.get("total_value_by_currency", {}).items():
            total_value[currency] += Decimal(str(value))
        for currency, value in portfolio.get("realised_pnl_by_currency", {}).items():
            realised[currency] += Decimal(str(value))
        for currency, value in portfolio.get("net_investment_income_by_currency", {}).items():
            income_total[currency] += Decimal(str(value))
        for currency, value in portfolio.get("withholding_tax_by_currency", {}).items():
            tax_total[currency] += Decimal(str(value))
        monthly_by_scope[str(portfolio.get("scope") or "personal")] += Decimal(
            str(portfolio.get("learned_monthly_cash_topup") or "0")
        )
    return {
        "portfolios": result,
        "portfolio_count": len(result),
        "position_count": total_positions,
        "total_value_by_currency": {key: str(value.quantize(Decimal("0.01"))) for key, value in total_value.items()},
        "realised_pnl_by_currency": {key: str(value.quantize(Decimal("0.01"))) for key, value in realised.items()},
        "net_investment_income_by_currency": {key: str(value.quantize(Decimal("0.01"))) for key, value in income_total.items()},
        "withholding_tax_by_currency": {key: str(value.quantize(Decimal("0.01"))) for key, value in tax_total.items()},
        "learned_monthly_cash_topup_by_scope": {key: str(value.quantize(Decimal("0.01"))) for key, value in monthly_by_scope.items()},
    }


async def investment_funding_forecast_by_scope(db: AsyncSession) -> dict[str, Decimal]:
    summary = await investment_history_summary(db)
    result: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for portfolio in summary["portfolios"]:
        monthly = Decimal(str(portfolio.get("learned_monthly_cash_topup") or "0"))
        if monthly > 0:
            result[str(portfolio["scope"])] += monthly
    return dict(result)
