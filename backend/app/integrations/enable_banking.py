from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

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
        response.raise_for_status()
        return response.json() if response.content else {}


async def start_account_authorization(
    db: AsyncSession,
    *,
    institution_country: str,
    institution_name: str,
    psu_type: str,
    state: str,
    redirect_url: str,
) -> dict[str, Any]:
    return await _request(
        db,
        "POST",
        "/auth",
        json_body={
            "access": {"valid_until": (datetime.now(timezone.utc) + timedelta(days=180)).date().isoformat()},
            "aspsp": {"country": institution_country.upper(), "name": institution_name},
            "psu_type": psu_type,
            "redirect_url": redirect_url,
            "state": state,
        },
    )


async def complete_account_authorization(db: AsyncSession, code: str) -> dict[str, Any]:
    return await _request(db, "POST", "/sessions", json_body={"code": code})


async def get_session(db: AsyncSession, session_id: str) -> dict[str, Any]:
    return await _request(db, "GET", f"/sessions/{session_id}")


async def get_accounts(db: AsyncSession, session_id: str) -> dict[str, Any]:
    return await _request(db, "GET", f"/accounts?session_id={session_id}")


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
    return await _request(
        db,
        "POST",
        "/payments",
        json_body={
            "aspsp": {"country": institution_country.upper(), "name": institution_name},
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
