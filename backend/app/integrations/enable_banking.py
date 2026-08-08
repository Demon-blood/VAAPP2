from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_config import get_runtime_value


class EnableBankingConfigurationError(RuntimeError):
    pass


async def _config(db: AsyncSession) -> tuple[str, str, str]:
    base_url = await get_runtime_value(db, "enable_banking_base_url", "https://api.enablebanking.com")
    application_id = await get_runtime_value(db, "enable_banking_application_id")
    private_key = await get_runtime_value(db, "enable_banking_private_key_pem")
    if not application_id or not private_key:
        raise EnableBankingConfigurationError("Enable Banking application ID and private key are required")
    return base_url, application_id, private_key


async def ensure_enable_banking_configured(db: AsyncSession) -> None:
    await _config(db)


async def _jwt_token(db: AsyncSession) -> str:
    _, application_id, private_key = await _config(db)
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": application_id},
    )


async def _request(db: AsyncSession, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url, _, _ = await _config(db)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.request(
            method,
            base_url.rstrip("/") + path,
            headers={"Authorization": f"Bearer {await _jwt_token(db)}", "Accept": "application/json"},
            json=json_body,
        )
        if response.status_code >= 400:
            detail = response.text.strip()
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(
                        payload.get("detail")
                        or payload.get("message")
                        or payload.get("error_description")
                        or payload.get("error")
                        or detail
                    )
            except Exception:
                pass
            raise EnableBankingConfigurationError(
                f"Enable Banking rejected {method} {path} ({response.status_code}): {detail or response.reason_phrase}"
            )
        return response.json() if response.content else {}


def _normalize_aspsp_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def resolve_aspsp(aspsps: list[dict[str, Any]], requested_name: str, psu_type: str) -> dict[str, Any]:
    requested = _normalize_aspsp_name(requested_name)
    if not requested:
        raise EnableBankingConfigurationError("A bank name is required")

    compatible: list[dict[str, Any]] = []
    for item in aspsps:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        supported = [str(value).casefold() for value in (item.get("psu_types") or [])]
        if supported and psu_type.casefold() not in supported:
            continue
        compatible.append(item)

    exact = [item for item in compatible if _normalize_aspsp_name(str(item["name"])) == requested]
    if exact:
        return exact[0]

    partial = [
        item
        for item in compatible
        if requested in _normalize_aspsp_name(str(item["name"]))
        or _normalize_aspsp_name(str(item["name"])) in requested
    ]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        partial.sort(key=lambda item: (len(_normalize_aspsp_name(str(item["name"]))), str(item["name"])))
        return partial[0]

    available = ", ".join(str(item.get("name")) for item in compatible[:20])
    raise EnableBankingConfigurationError(
        f'Enable Banking does not currently expose a compatible Belgian AIS institution matching "{requested_name}"'
        + (f". Available institutions include: {available}" if available else ".")
    )


async def list_aspsps(
    db: AsyncSession, *, country: str, psu_type: str | None = None, service: str | None = None
) -> list[dict[str, Any]]:
    params = {"country": country.upper()}
    if psu_type:
        params["psu_type"] = psu_type
    if service:
        params["service"] = service
    payload = await _request(db, "GET", f"/aspsps?{urlencode(params)}")
    values = payload.get("aspsps") if isinstance(payload, dict) else None
    return [item for item in (values or []) if isinstance(item, dict)]


async def start_account_authorization(
    db: AsyncSession,
    *,
    institution_country: str,
    institution_name: str,
    psu_type: str,
    state: str,
    redirect_url: str,
) -> dict[str, Any]:
    aspsps = await list_aspsps(
        db,
        country=institution_country,
        psu_type=psu_type,
        service="AIS",
    )
    aspsp = resolve_aspsp(aspsps, institution_name, psu_type)
    now = datetime.now(timezone.utc)
    requested_seconds = 180 * 24 * 60 * 60
    try:
        maximum_seconds = int(aspsp.get("maximum_consent_validity") or requested_seconds)
    except (TypeError, ValueError):
        maximum_seconds = requested_seconds
    consent_seconds = max(60, min(requested_seconds, maximum_seconds))
    valid_until = (now + timedelta(seconds=consent_seconds)).isoformat(timespec="seconds")
    response = await _request(
        db,
        "POST",
        "/auth",
        json_body={
            "access": {
                "valid_until": valid_until,
                "balances": True,
                "transactions": True,
            },
            "aspsp": {"country": institution_country.upper(), "name": str(aspsp["name"])},
            "psu_type": psu_type,
            "redirect_url": redirect_url,
            "state": state,
        },
    )
    response["_resolved_aspsp_name"] = str(aspsp["name"])
    return response


async def complete_account_authorization(db: AsyncSession, code: str) -> dict[str, Any]:
    return await _request(db, "POST", "/sessions", json_body={"code": code})


async def get_session(db: AsyncSession, session_id: str) -> dict[str, Any]:
    return await _request(db, "GET", f"/sessions/{session_id}")


async def get_account_details(db: AsyncSession, account_id: str) -> dict[str, Any]:
    return await _request(db, "GET", f"/accounts/{account_id}/details")


async def get_account_balances(db: AsyncSession, account_id: str) -> dict[str, Any]:
    return await _request(db, "GET", f"/accounts/{account_id}/balances")


async def get_account_transactions(db: AsyncSession, account_id: str, date_from: str | None = None) -> dict[str, Any]:
    suffix = f"?date_from={date_from}" if date_from else ""
    return await _request(db, "GET", f"/accounts/{account_id}/transactions{suffix}")


async def create_sepa_payment(
    db: AsyncSession,
    *,
    institution_country: str,
    institution_name: str,
    psu_type: str,
    creditor_name: str,
    creditor_iban: str,
    amount: str,
    currency: str,
    reference: str,
    state: str,
    redirect_url: str,
) -> dict[str, Any]:
    aspsps = await list_aspsps(
        db,
        country=institution_country,
        psu_type=psu_type,
        service="PIS",
    )
    aspsp = resolve_aspsp(aspsps, institution_name, psu_type)
    return await _request(
        db,
        "POST",
        "/payments",
        json_body={
            "aspsp": {"country": institution_country.upper(), "name": str(aspsp["name"])},
            "payment_request": {
                "credit_transfer_transaction": [
                    {
                        "beneficiary": {
                            "creditor": {"name": creditor_name},
                            "creditor_account": {"identification": creditor_iban, "scheme_name": "IBAN"},
                        },
                        "instructed_amount": {"amount": amount, "currency": currency},
                        "remittance_information": [reference] if reference else [],
                    }
                ]
            },
            "payment_type": "SEPA",
            "psu_type": psu_type,
            "redirect_url": redirect_url,
            "state": state,
        },
    )


async def get_payment(db: AsyncSession, payment_id: str) -> dict[str, Any]:
    return await _request(db, "GET", f"/payments/{payment_id}")


async def verify_connection(db: AsyncSession) -> dict[str, Any]:
    return await _request(db, "GET", "/aspsps?country=BE")
