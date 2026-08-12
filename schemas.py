"""Parsing of incoming Snappic webhook payloads.

IMPORTANT
---------
The exact JSON shape Snappic sends is not published, so this module is written
to be tolerant: it looks for the fields we need in several likely locations.
When you see a real payload (log one — see README "Confirming the payload"),
adjust the key lists below in ONE place and everything downstream keeps working.
"""

from __future__ import annotations

from typing import Any, Optional

# Candidate key paths for each field we care about. Each entry is a list of
# keys to try, in order; nested paths use dotted notation ("session.id").
_CAPTURE_FIELDS = {
    "session_id": ["session.id", "session_id", "id", "sessionId"],
    "media_type": ["session.type", "type", "media_type", "mediaType"],
    "media_url": [
        "session.direct_url",
        "direct_url",
        "media_url",
        "url",
        "session.media_url",
        "directUrl",
    ],
    "site_url": ["session.site_url", "site_url", "gallery_url", "siteUrl"],
}

_SHARE_FIELDS = {
    "session_id": ["session.id", "session_id", "id", "sessionId"],
    "share_method": ["method", "share_method", "share.method", "channel"],
    "phone": [
        "recipient",
        "phone",
        "phone_number",
        "to",
        "contact",
        "share.recipient",
        "recipient.phone",
        "phoneNumber",
    ],
    "email": ["email", "recipient_email", "share.email", "recipient.email"],
}


def _dig(data: dict[str, Any], dotted_key: str) -> Optional[Any]:
    """Follow a dotted key path into nested dicts. Returns None if absent."""
    cur: Any = data
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _first(data: dict[str, Any], candidates: list[str]) -> Optional[Any]:
    for key in candidates:
        val = _dig(data, key)
        if val is not None and val != "":
            return val
    return None


def _normalize_type(raw: Optional[Any]) -> Optional[str]:
    if raw is None:
        return None
    t = str(raw).strip().lower()
    if t in {"photo", "image", "picture", "still"}:
        return "photo"
    if t in {"gif", "boomerang", "burst"}:
        return "gif"
    if t in {"video", "mp4", "clip"}:
        return "video"
    if t in {"ai", "ai_image", "aiimage", "generated"}:
        return "ai"
    return t


class CaptureEvent:
    def __init__(self, payload: dict[str, Any]):
        self.session_id: Optional[str] = _coerce_str(
            _first(payload, _CAPTURE_FIELDS["session_id"])
        )
        self.media_type: Optional[str] = _normalize_type(
            _first(payload, _CAPTURE_FIELDS["media_type"])
        )
        self.media_url: Optional[str] = _coerce_str(
            _first(payload, _CAPTURE_FIELDS["media_url"])
        )
        self.site_url: Optional[str] = _coerce_str(
            _first(payload, _CAPTURE_FIELDS["site_url"])
        )

    @property
    def is_valid(self) -> bool:
        return bool(self.session_id and self.media_url)


class ShareEvent:
    def __init__(self, payload: dict[str, Any]):
        self.session_id: Optional[str] = _coerce_str(
            _first(payload, _SHARE_FIELDS["session_id"])
        )
        method = _first(payload, _SHARE_FIELDS["share_method"])
        self.share_method: Optional[str] = (
            str(method).strip().lower() if method is not None else None
        )
        self.phone: Optional[str] = _coerce_str(
            _first(payload, _SHARE_FIELDS["phone"])
        )
        self.email: Optional[str] = _coerce_str(
            _first(payload, _SHARE_FIELDS["email"])
        )
        # If method wasn't explicit, infer it from what contact info we got.
        if not self.share_method:
            if self.phone:
                self.share_method = "sms"
            elif self.email:
                self.share_method = "email"

    @property
    def is_sms(self) -> bool:
        return self.share_method == "sms" and bool(self.phone)

    @property
    def is_valid(self) -> bool:
        return bool(self.session_id)


def _coerce_str(val: Optional[Any]) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s or None
