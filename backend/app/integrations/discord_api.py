from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_config import get_runtime_value


class DiscordConfigurationError(RuntimeError):
    pass


async def _headers(db: AsyncSession) -> dict[str, str]:
    token = await get_runtime_value(db, "discord_bot_token")
    if not token:
        raise DiscordConfigurationError("Discord bot token is not configured")
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json", "User-Agent": "DiscordBot (Full-Time-VA, 0.4.16)"}


async def discord_get(db: AsyncSession, path: str) -> Any:
    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", timeout=30) as client:
        response = await client.get(path, headers=await _headers(db))
        response.raise_for_status()
        return response.json()


async def discord_post(db: AsyncSession, path: str, payload: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", timeout=30) as client:
        response = await client.post(path, headers=await _headers(db), json=payload)
        response.raise_for_status()
        return response.json()


async def verify_discord_connection(db: AsyncSession) -> dict[str, Any]:
    return await discord_get(db, "/users/@me")


async def send_discord_message(db: AsyncSession, content: str, channel_id: str | None = None) -> dict[str, Any]:
    target = channel_id or await get_runtime_value(db, "discord_default_channel_id")
    if not target:
        raise DiscordConfigurationError("Discord default channel ID is not configured")
    if not content.strip() or len(content) > 2000:
        raise ValueError("Discord content must contain 1 to 2000 characters")
    return await discord_post(db, f"/channels/{target}/messages", {"content": content})
