from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.core.settings import get_settings
from app.models.entities import RuntimeSetting

settings = get_settings()

# Fields that can be configured from Android. The app never returns stored secret values.
CONFIG_SECTIONS: dict[str, dict[str, Any]] = {
    "automation": {
        "title": "VA automation policy",
        "description": "Controls unattended execution. Safety checks and provider-required authorization remain enforced.",
        "fields": [
            {"key": "auto_pay_enabled", "label": "Automatically initiate eligible bills", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "auto_pay_days_before_due", "label": "Pay this many days before due date", "type": "number", "required": True, "default": "3"},
            {"key": "connector_automation_enabled", "label": "Run scheduled service connector rules", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
        ],
    },
    "google": {
        "title": "Google Gmail, Calendar, Drive and Contacts",
        "description": "Official Google OAuth connection for email, calendar, files and contacts.",
        "setup_url": "https://console.cloud.google.com/apis/credentials",
        "fields": [
            {"key": "google_client_id", "label": "OAuth client ID", "type": "text", "required": True},
            {"key": "google_client_secret", "label": "OAuth client secret", "type": "secret", "required": True},
            {"key": "google_pubsub_topic", "label": "Pub/Sub topic", "type": "text", "required": False},
            {"key": "google_pubsub_verification_token", "label": "Pub/Sub verification token", "type": "secret", "required": False},
        ],
    },
    "ai": {
        "title": "AI decision engine",
        "description": "OpenAI-compatible model used for classification and action decisions.",
        "fields": [
            {"key": "ai_base_url", "label": "API base URL", "type": "url", "required": True, "default": "https://api.openai.com/v1"},
            {"key": "ai_api_key", "label": "API key", "type": "secret", "required": True},
            {"key": "ai_model", "label": "Model", "type": "text", "required": True},
        ],
    },
    "enable_banking": {
        "title": "Open Banking - Beobank and Revolut",
        "description": "Enable Banking application credentials for account information and payment initiation.",
        "setup_url": "https://enablebanking.com/",
        "fields": [
            {"key": "enable_banking_base_url", "label": "API base URL", "type": "url", "required": True, "default": "https://api.enablebanking.com"},
            {"key": "enable_banking_application_id", "label": "Application ID", "type": "text", "required": True},
            {"key": "enable_banking_private_key_pem", "label": "Private key PEM", "type": "multiline_secret", "required": True},
        ],
    },
    "github": {
        "title": "GitHub",
        "description": "Repository, issues, workflow, release and persistent Android signing-secret access. Uses a fine-grained token with Actions read/write and repository Secrets read/write. GitHub personal notification polling is intentionally skipped because that REST endpoint only accepts classic PATs.",
        "setup_url": "https://github.com/settings/personal-access-tokens",
        "fields": [
            {"key": "github_token", "label": "Fine-grained token", "type": "secret", "required": True},
            {"key": "github_default_repository", "label": "Default repository owner/name", "type": "text", "required": False},
        ],
    },
    "cloudflare": {
        "title": "Cloudflare",
        "description": "Workers, D1, R2, DNS and deployment administration.",
        "setup_url": "https://dash.cloudflare.com/profile/api-tokens",
        "fields": [
            {"key": "cloudflare_api_token", "label": "API token", "type": "secret", "required": True},
            {"key": "cloudflare_account_id", "label": "Account ID", "type": "text", "required": True},
        ],
    },
    "discord": {
        "title": "Discord",
        "description": "Bot-based messages and notifications.",
        "setup_url": "https://discord.com/developers/applications",
        "fields": [
            {"key": "discord_bot_token", "label": "Bot token", "type": "secret", "required": True},
            {"key": "discord_default_channel_id", "label": "Default channel ID", "type": "text", "required": True},
        ],
    },
}

ENV_FALLBACKS = {
    "google_client_id": "google_client_id",
    "google_client_secret": "google_client_secret",
    "google_pubsub_topic": "google_pubsub_topic",
    "google_pubsub_verification_token": "google_pubsub_verification_token",
    "ai_base_url": "ai_base_url",
    "ai_api_key": "ai_api_key",
    "ai_model": "ai_model",
    "enable_banking_base_url": "enable_banking_base_url",
    "enable_banking_application_id": "enable_banking_application_id",
    "github_token": "github_token",
    "cloudflare_api_token": "cloudflare_api_token",
    "cloudflare_account_id": "cloudflare_account_id",
    "discord_bot_token": "discord_bot_token",
    "discord_default_channel_id": "discord_default_channel_id",
}


async def get_runtime_value(db: AsyncSession, key: str, default: str = "") -> str:
    row = await db.get(RuntimeSetting, key)
    if row is not None:
        return decrypt_text(row.value_encrypted)
    attr = ENV_FALLBACKS.get(key)
    if attr:
        value = getattr(settings, attr, "")
        if value not in (None, ""):
            return str(value)
    for section in CONFIG_SECTIONS.values():
        for field in section["fields"]:
            if field["key"] == key and field.get("default") is not None:
                return str(field["default"])
    return default


async def set_runtime_values(db: AsyncSession, values: dict[str, Any]) -> None:
    valid_keys = {field["key"] for section in CONFIG_SECTIONS.values() for field in section["fields"]}
    for key, raw in values.items():
        if key not in valid_keys:
            raise ValueError(f"Unsupported configuration field: {key}")
        value = "" if raw is None else str(raw).strip()
        row = await db.get(RuntimeSetting, key)
        if not value and row is not None:
            # Empty secret fields mean keep the current stored value.
            continue
        field = next(
            field
            for section in CONFIG_SECTIONS.values()
            for field in section["fields"]
            if field["key"] == key
        )
        is_secret = "secret" in str(field.get("type") or "")
        if row is None:
            row = RuntimeSetting(key=key, value_encrypted=encrypt_text(value), is_secret=is_secret)
            db.add(row)
        else:
            row.value_encrypted = encrypt_text(value)
            row.is_secret = is_secret
    await db.commit()


async def section_status(db: AsyncSession) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for slug, section in CONFIG_SECTIONS.items():
        configured_fields: list[str] = []
        missing: list[str] = []
        current_values: dict[str, str] = {}
        for field in section["fields"]:
            value = await get_runtime_value(db, field["key"])
            if value:
                configured_fields.append(field["key"])
                if "secret" not in str(field.get("type") or ""):
                    current_values[field["key"]] = value
            elif field.get("required"):
                missing.append(field["key"])
        result.append(
            {
                "slug": slug,
                **section,
                "configured": not missing,
                "configured_fields": configured_fields,
                "missing_fields": missing,
                "current_values": current_values,
            }
        )
    return result


def encode_public_config(config: dict[str, Any]) -> str:
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))
