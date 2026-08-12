"""Application configuration, loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Anthropic / Claude
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_messaging_service_sid: str = ""

    # Snappic webhook security
    snappic_webhook_secret: str = ""
    snappic_verify_mode: Literal["hmac", "token", "none"] = "token"
    snappic_signature_header: str = "X-Snappic-Signature"

    # Message wording
    sms_prefix: str = "Photo description:"
    event_name: str = ""

    # Admin dashboard (HTTP Basic auth)
    admin_username: str = "admin"
    admin_password: str = ""

    @property
    def admin_configured(self) -> bool:
        return bool(self.admin_password)

    # App / DB
    database_url: str = "sqlite:///./snappic_alt_text.db"
    media_tmp_dir: str = "./_media_tmp"
    log_level: str = "INFO"

    @property
    def twilio_configured(self) -> bool:
        has_sender = bool(self.twilio_from_number or self.twilio_messaging_service_sid)
        return bool(self.twilio_account_sid and self.twilio_auth_token and has_sender)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
