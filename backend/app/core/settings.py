from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Full-Time VA"
    environment: str = "production"
    public_base_url: HttpUrl
    database_url: str = "sqlite+aiosqlite:///./data/full_time_va.db"
    pairing_secret: str = Field(min_length=24)
    token_encryption_key: str = Field(min_length=32)

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_pubsub_verification_token: str = ""
    google_pubsub_topic: str = ""

    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 90

    enable_banking_base_url: str = "https://api.enablebanking.com"
    enable_banking_application_id: str = ""
    enable_banking_private_key_path: str = "/run/secrets/enable_banking_private_key.pem"
    enable_banking_redirect_uri: str = ""

    firebase_service_account_json_path: str = ""

    github_token: str = ""
    github_api_version: str = "2022-11-28"

    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""

    discord_bot_token: str = ""
    discord_default_channel_id: str = ""

    google_drive_archive_folder: str = "Full-Time VA"
    external_sync_minutes: int = 30
    default_timezone: str = "Europe/Brussels"
    gmail_sync_minutes: int = 5
    bank_sync_minutes: int = 30
    automation_enabled: bool = True

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgresql+asyncpg://"):
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        allowed = {"development", "test", "production"}
        if value not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}")
        return value

    @property
    def enable_banking_key_exists(self) -> bool:
        return Path(self.enable_banking_private_key_path).is_file()


@lru_cache
def get_settings() -> Settings:
    return Settings()
