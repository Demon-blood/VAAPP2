from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_config import get_runtime_value


class CloudflareConfigurationError(RuntimeError):
    pass


async def _credentials(db: AsyncSession) -> tuple[str, str]:
    token = await get_runtime_value(db, "cloudflare_api_token")
    account_id = await get_runtime_value(db, "cloudflare_account_id")
    if not token or not account_id:
        raise CloudflareConfigurationError("Cloudflare token and account ID are required")
    return token, account_id


async def cloudflare_get(db: AsyncSession, path: str, params: dict[str, Any] | None = None) -> Any:
    token, _ = await _credentials(db)
    async with httpx.AsyncClient(base_url="https://api.cloudflare.com/client/v4", timeout=45) as client:
        response = await client.get(path, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, params=params)
        response.raise_for_status()
        payload = response.json()
    if not payload.get("success", False):
        raise RuntimeError(str(payload.get("errors") or "Cloudflare rejected the request"))
    return payload.get("result")


async def verify_cloudflare_connection(db: AsyncSession) -> dict[str, Any]:
    result = await cloudflare_get(db, "/user/tokens/verify")
    return result or {}


async def resource_summary(db: AsyncSession) -> dict[str, Any]:
    _, account_id = await _credentials(db)
    workers = await cloudflare_get(db, f"/accounts/{account_id}/workers/scripts")
    d1 = await cloudflare_get(db, f"/accounts/{account_id}/d1/database", {"per_page": 100})
    r2 = await cloudflare_get(db, f"/accounts/{account_id}/r2/buckets")
    zones = await cloudflare_get(db, "/zones", {"account.id": account_id, "per_page": 50})
    if isinstance(workers, dict):
        workers = workers.get("workers") or workers.get("scripts") or []
    if isinstance(r2, dict):
        r2 = r2.get("buckets") or []
    return {"workers": list(workers or []), "d1_databases": list(d1 or []), "r2_buckets": list(r2 or []), "zones": list(zones or [])}
