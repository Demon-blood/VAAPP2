from __future__ import annotations

import base64
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_config import get_runtime_value


class KrakenConfigurationError(RuntimeError):
    pass


async def _credentials(db: AsyncSession) -> tuple[str, str, str]:
    base_url = await get_runtime_value(db, "kraken_api_base_url", "https://api.kraken.com")
    api_key = await get_runtime_value(db, "kraken_api_key")
    api_secret = await get_runtime_value(db, "kraken_api_secret")
    if not api_key or not api_secret:
        raise KrakenConfigurationError("Kraken API key and secret are required")
    return base_url.rstrip("/"), api_key, api_secret


def _signature(path: str, params: dict[str, str], secret: str) -> str:
    encoded = urlencode(params)
    nonce = params["nonce"]
    digest = hashlib.sha256((nonce + encoded).encode("utf-8")).digest()
    try:
        secret_bytes = base64.b64decode(secret)
    except Exception as exc:
        raise KrakenConfigurationError("Kraken API secret is not valid base64") from exc
    mac = hmac.new(secret_bytes, path.encode("utf-8") + digest, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode("ascii")


async def _private(db: AsyncSession, path: str, payload: dict[str, Any] | None = None) -> Any:
    base_url, api_key, secret = await _credentials(db)
    params = {key: str(value) for key, value in (payload or {}).items() if value is not None}
    params["nonce"] = str(time.time_ns())
    headers = {
        "API-Key": api_key,
        "API-Sign": _signature(path, params, secret),
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(base_url + path, data=params, headers=headers)
    if response.status_code >= 400:
        raise KrakenConfigurationError(f"Kraken HTTP {response.status_code}: {response.text[:1000]}")
    body = response.json()
    errors = body.get("error") if isinstance(body, dict) else None
    if errors:
        raise KrakenConfigurationError("Kraken rejected the request: " + "; ".join(str(item) for item in errors))
    if isinstance(body, dict) and "result" in body:
        return body.get("result")
    return body


async def _public(base_url: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(base_url.rstrip("/") + path, params=params, headers={"Accept": "application/json"})
    response.raise_for_status()
    body = response.json()
    errors = body.get("error") if isinstance(body, dict) else None
    if errors:
        raise KrakenConfigurationError("Kraken rejected the request: " + "; ".join(str(item) for item in errors))
    return body.get("result") if isinstance(body, dict) and isinstance(body.get("result"), dict) else body


async def verify_connection(db: AsyncSession) -> dict[str, Any]:
    return await _private(db, "/0/private/GetApiKeyInfo")


async def get_api_key_permissions(db: AsyncSession) -> set[str]:
    info = await verify_connection(db)
    values = info.get("permissions") if isinstance(info, dict) else []
    return {str(value) for value in (values or [])}


async def get_balances(db: AsyncSession) -> dict[str, Decimal]:
    result = await _private(db, "/0/private/Balance")
    values: dict[str, Decimal] = {}
    if isinstance(result, dict):
        for asset, value in result.items():
            try:
                values[str(asset)] = Decimal(str(value))
            except Exception:
                continue
    return values


async def get_eur_balance(db: AsyncSession) -> Decimal:
    balances = await get_balances(db)
    for key in ("ZEUR", "EUR"):
        if key in balances:
            return balances[key]
    return Decimal("0")


def _display_asset_code(value: str) -> str:
    code = str(value or "").upper().split(".", 1)[0]
    aliases = {
        "XXBT": "XBT",
        "XBT": "XBT",
        "XETH": "ETH",
        "ZEUR": "EUR",
        "ZUSD": "USD",
        "ZGBP": "GBP",
    }
    if code in aliases:
        return aliases[code]
    if len(code) == 4 and code[0] in {"X", "Z"}:
        return code[1:]
    return code


def _ticker_last_price(result: Any) -> Decimal | None:
    if not isinstance(result, dict) or not result:
        return None
    row = next(iter(result.values()), None)
    if not isinstance(row, dict):
        return None
    close = row.get("c")
    if not isinstance(close, list) or not close:
        return None
    try:
        price = Decimal(str(close[0]))
    except Exception:
        return None
    return price if price > 0 else None


async def get_eur_valued_balances(db: AsyncSession) -> dict[str, Any]:
    """Return non-zero Kraken balances with a best-effort EUR valuation.

    Balance quantities come from Kraken's authenticated Balance endpoint. EUR values
    use the public last-trade ticker and are explicitly estimates; assets without a
    supported EUR market remain visible but unvalued rather than being guessed.
    """
    base_url, _, _ = await _credentials(db)
    balances = await get_balances(db)
    rows: list[dict[str, Any]] = []
    total = Decimal("0")
    unvalued = 0
    for raw_asset, quantity in sorted(balances.items()):
        if quantity <= 0:
            continue
        asset = _display_asset_code(raw_asset)
        price: Decimal | None = None
        value: Decimal | None = None
        if asset == "EUR":
            price = Decimal("1")
            value = quantity
        else:
            pair = "XBTEUR" if asset == "XBT" else f"{asset}EUR"
            try:
                ticker = await _public(base_url, "/0/public/Ticker", {"pair": pair})
                price = _ticker_last_price(ticker)
                if price is not None:
                    value = quantity * price
            except Exception:
                value = None
        if value is None:
            unvalued += 1
        else:
            total += value
        rows.append(
            {
                "asset": asset,
                "kraken_asset_code": raw_asset,
                "quantity": str(quantity),
                "price_eur": str(price.quantize(Decimal("0.00000001"))) if price is not None else None,
                "estimated_value_eur": str(value.quantize(Decimal("0.01"))) if value is not None else None,
            }
        )
    rows.sort(
        key=lambda row: Decimal(str(row.get("estimated_value_eur") or "0")),
        reverse=True,
    )
    return {
        "status": "connected",
        "estimated_total_eur": str(total.quantize(Decimal("0.01"))),
        "assets": rows,
        "asset_count": len(rows),
        "unvalued_asset_count": unvalued,
    }


async def get_deposit_status(db: AsyncSession, *, asset: str = "EUR") -> list[dict[str, Any]]:
    """Return Kraken deposit history for reconciliation; this never moves funds."""
    result = await _private(db, "/0/private/DepositStatus", {"asset": asset})
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


async def market_buy_eur(db: AsyncSession, *, pair: str, eur_amount: Decimal, client_order_id: str) -> dict[str, Any]:
    if eur_amount <= 0:
        raise KrakenConfigurationError("Kraken investment amount must be positive")
    base_url, _, _ = await _credentials(db)
    ticker = await _public(base_url, "/0/public/Ticker", {"pair": pair})
    pair_data = next(iter(ticker.values()), None) if isinstance(ticker, dict) else None
    ask = None
    if isinstance(pair_data, dict):
        asks = pair_data.get("a")
        if isinstance(asks, list) and asks:
            ask = Decimal(str(asks[0]))
    if ask is None or ask <= 0:
        raise KrakenConfigurationError(f"Kraken did not return an ask price for {pair}")
    # Leave a 1% execution cushion so a market buy never intentionally exceeds the
    # authorised EUR contribution after a rapid price move or fee.
    volume = (eur_amount * Decimal("0.99") / ask).quantize(Decimal("0.00000001"))
    if volume <= 0:
        raise KrakenConfigurationError("Calculated Kraken order size is below the supported precision")
    return await _private(
        db,
        "/0/private/AddOrder",
        {
            "pair": pair,
            "type": "buy",
            "ordertype": "market",
            "volume": format(volume, "f"),
            "cl_ord_id": client_order_id[:18],
        },
    )
