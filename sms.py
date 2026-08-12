"""Deliver the description to the guest by SMS via Twilio."""

from __future__ import annotations

from twilio.rest import Client

from config import Settings


class SmsError(Exception):
    pass


class SmsSender:
    """Thin wrapper around the Twilio client (easy to mock in tests)."""

    def __init__(self, settings: Settings, client: Client | None = None):
        self.settings = settings
        self._client = client or Client(
            settings.twilio_account_sid, settings.twilio_auth_token
        )

    def send(self, to_number: str, body: str) -> str:
        kwargs: dict[str, str] = {"to": to_number, "body": body}
        if self.settings.twilio_messaging_service_sid:
            kwargs["messaging_service_sid"] = self.settings.twilio_messaging_service_sid
        else:
            kwargs["from_"] = self.settings.twilio_from_number
        try:
            msg = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise SmsError(str(exc)) from exc
        return msg.sid


def compose_message(settings: Settings, alt_text: str) -> str:
    """Build the SMS body. Kept short and screen-reader friendly."""
    parts = []
    if settings.sms_prefix:
        parts.append(settings.sms_prefix.rstrip())
    parts.append(alt_text)
    body = " ".join(parts).strip()
    if settings.event_name:
        body = f"{body} ({settings.event_name})"
    return body
