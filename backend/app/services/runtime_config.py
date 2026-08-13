from __future__ import annotations

import json
from typing import Any

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
            {"key": "va_autonomous_core_enabled", "label": "Run the autonomous VA core continuously", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "auto_pay_enabled", "label": "Automatically initiate eligible bills", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "auto_pay_days_before_due", "label": "Pay this many days before due date", "type": "number", "required": True, "default": "3"},
            {"key": "connector_automation_enabled", "label": "Run scheduled service connector rules", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "autopilot_planner_enabled", "label": "Proactively plan routine work", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "autonomous_low_risk_replies", "label": "Send low-risk replies automatically", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "auto_recover_transient_failures", "label": "Automatically recover transient provider failures", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "daily_briefing_enabled", "label": "Send a daily VA briefing", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "daily_briefing_hour_local", "label": "Daily briefing hour (local 0-23)", "type": "number", "required": True, "default": "19"},
            {"key": "daily_briefing_window_hours", "label": "Daily briefing lookback hours", "type": "number", "required": True, "default": "24"},
            {"key": "communications_auto_reply_enabled", "label": "Auto-reply to safe phone/messages", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "communications_silence_unknown_calls", "label": "Silence unknown callers", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
            {"key": "gmail_auto_trash_low_value_enabled", "label": "Trash read low-value mail after grace period", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "gmail_low_value_trash_after_days", "label": "Low-value mail trash grace period (days)", "type": "number", "required": True, "default": "14"},
            {"key": "finance_auto_budget_enabled", "label": "Continuously manage budgets", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "finance_auto_transfer_enabled", "label": "Automatically rebalance own bank accounts", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "finance_cash_buffer_multiplier", "label": "Cash buffer multiplier", "type": "number", "required": True, "default": "1.10"},
            {"key": "finance_min_operating_cash_floor", "label": "Minimum cash kept in an operating account", "type": "number", "required": True, "default": "1000"},
            {"key": "finance_max_single_transfer", "label": "Maximum automatic own-account transfer", "type": "number", "required": True, "default": "1000"},
            {"key": "finance_daily_internal_transfer_limit", "label": "Daily automatic own-account transfer limit", "type": "number", "required": True, "default": "1000"},
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
        "description": "Automation-first OpenAI-compatible decision engine. Rules and cached decisions are used before AI, and daily budgets reserve capacity for urgent mail.",
        "fields": [
            {"key": "ai_base_url", "label": "Primary API base URL", "type": "url", "required": True, "default": "https://api.groq.com/openai/v1"},
            {"key": "ai_api_key", "label": "Primary API key", "type": "secret", "required": True},
            {"key": "ai_model", "label": "Primary model", "type": "text", "required": True, "default": "openai/gpt-oss-20b"},
            {"key": "ai_daily_request_budget", "label": "Daily AI request budget", "type": "number", "required": True, "default": "1000"},
            {"key": "ai_daily_token_budget", "label": "Daily AI token budget", "type": "number", "required": True, "default": "200000"},
            {"key": "ai_daily_request_reserve", "label": "Requests reserved for urgent mail", "type": "number", "required": True, "default": "100"},
            {"key": "ai_daily_token_reserve", "label": "Tokens reserved for urgent mail", "type": "number", "required": True, "default": "25000"},
            {"key": "ai_backfill_daily_limit", "label": "Historical emails allowed to use AI per day", "type": "number", "required": True, "default": "50"},
            {"key": "ai_timeout_seconds", "label": "AI request timeout seconds", "type": "number", "required": True, "default": "90"},
            {"key": "ai_fallback_base_url", "label": "Gemini fallback API base URL", "type": "url", "required": False, "default": "https://generativelanguage.googleapis.com/v1beta/openai/"},
            {"key": "ai_fallback_api_key", "label": "Gemini fallback API key", "type": "secret", "required": False},
            {"key": "ai_fallback_model", "label": "Gemini fallback model", "type": "text", "required": False, "default": "gemini-3.6-flash"},
            {"key": "ai_fallback_allow_sensitive", "label": "Allow fallback for sensitive mail", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
        ],
    },
    "twilio": {
        "title": "Twilio Programmable Voice",
        "description": "Real PSTN calling for the VA. Calls disclose that they are automated, recording is disabled, and provider completion never substitutes for objective verification.",
        "setup_url": "https://console.twilio.com/",
        "fields": [
            {"key": "telephony_enabled", "label": "Enable autonomous telephone calls", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "twilio_account_sid", "label": "Account SID", "type": "text", "required": True},
            {"key": "twilio_auth_token", "label": "Auth token", "type": "secret", "required": True},
            {"key": "twilio_from_number", "label": "Twilio caller number (E.164)", "type": "text", "required": True},
            {"key": "telephony_owner_display_name", "label": "Name the VA may say it represents", "type": "text", "required": False},
            {"key": "telephony_language", "label": "Voice / speech locale", "type": "text", "required": True, "default": "en-GB"},
            {"key": "telephony_max_turns", "label": "Maximum caller speech turns per call", "type": "number", "required": True, "default": "10"},
            {"key": "telephony_max_duration_seconds", "label": "Maximum planned call duration (seconds)", "type": "number", "required": True, "default": "600"},
            {"key": "telephony_max_attempts", "label": "Maximum bounded outbound attempts", "type": "number", "required": True, "default": "3"},
        ],
    },
    "fulfillment": {
        "title": "Purchasing, travel, logistics & customer service",
        "description": "Durable fulfillment ownership. Standing purchase/travel limits act as explicit preauthorization; unknown or over-limit monetary commitments still require the account holder before provider execution.",
        "fields": [
            {"key": "fulfillment_auto_purchase_enabled", "label": "Allow purchases within my standing limit", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
            {"key": "fulfillment_max_single_purchase_eur", "label": "Maximum one automatically authorized purchase (EUR)", "type": "number", "required": True, "default": "0"},
            {"key": "fulfillment_auto_travel_enabled", "label": "Allow travel bookings within my standing limit", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
            {"key": "fulfillment_max_single_travel_eur", "label": "Maximum one automatically authorized travel booking (EUR)", "type": "number", "required": True, "default": "0"},
            {"key": "fulfillment_monthly_purchase_limit_eur", "label": "Monthly purchasing/travel commitment limit (EUR)", "type": "number", "required": True, "default": "0"},
            {"key": "fulfillment_tracking_enabled", "label": "Own order and delivery tracking", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "fulfillment_auto_returns_enabled", "label": "Handle routine returns, refunds and cancellations", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
            {"key": "fulfillment_auto_service_enabled", "label": "Handle routine customer-service cases", "type": "choice", "choices": ["true", "false"], "required": True, "default": "true"},
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
    "kraken": {
        "title": "Kraken investments",
        "description": "Read portfolio balances and optionally execute a configured crypto contribution policy. Withdrawal permissions are not required or used.",
        "setup_url": "https://www.kraken.com/u/security/api",
        "fields": [
            {"key": "kraken_api_base_url", "label": "API base URL", "type": "url", "required": True, "default": "https://api.kraken.com"},
            {"key": "kraken_api_key", "label": "API key", "type": "secret", "required": True},
            {"key": "kraken_api_secret", "label": "API secret", "type": "secret", "required": True},
            {"key": "kraken_auto_trade_enabled", "label": "Allow policy-based crypto purchases", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
            {"key": "kraken_default_pair", "label": "Default investment pair", "type": "text", "required": True, "default": "XBTEUR"},
            {"key": "kraken_max_auto_trade_eur", "label": "Maximum automatic purchase (EUR)", "type": "number", "required": True, "default": "250"},
            {"key": "kraken_funding_recipient", "label": "Verified Kraken SEPA recipient", "type": "text", "required": False},
            {"key": "kraken_funding_iban", "label": "Verified Kraken SEPA IBAN", "type": "text", "required": False},
            {"key": "kraken_funding_reference", "label": "Kraken deposit reference (leave blank if Kraken shows none)", "type": "text", "required": False},
            {"key": "kraken_personal_owner_confirmed", "label": "Kraken recipient matches my Personal bank-account holder name", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
            {"key": "kraken_monthly_target_eur", "label": "Monthly Kraken contribution target", "type": "number", "required": True, "default": "0"},
            {"key": "kraken_auto_fund_enabled", "label": "Allow automatic SEPA funding when configured", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
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
    "ai_timeout_seconds": "ai_timeout_seconds",
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
