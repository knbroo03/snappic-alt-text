"""Parsing of incoming Snappic webhook payloads.

Snappic's exact JSON shape is not published, so this module is resilient: it
first tries known candidate field names, then falls back to *sniffing* the whole
payload recursively for anything that looks like a media URL, a phone number, or
an email. That way it works regardless of the exact keys Snappic uses.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Optional

# Candidate key paths (fast path). Dotted notation walks nested dicts.
_CAPTURE_FIELDS = {
    "session_id": ["session.id", "session_id", "id", "sessionId", "session.session_id"],
    "media_type": ["session.type", "type", "media_type", "mediaType", "session.media_type"],
    "media_url": [
        "session.direct_url", "direct_url", "media_url", "url",
        "session.media_url", "directUrl", "session.url", "download_url",
        "file_url", "image_url", "fileUrl", "imageUrl", "downloadUrl",
    ],
    "site_url": ["session.site_url", "site_url", "gallery_url", "siteUrl"],
}

_SHARE_FIELDS = {
    "session_id": ["session.id", "session_id", "id", "sessionId", "session.session_id"],
    "share_method": ["method", "share_method", "share.method", "channel", "type", "share_type"],
    "phone": [
        "recipient", "phone", "phone_number", "to", "contact",
        "share.recipient", "recipient.phone", "phoneNumber", "mobile",
        "share.phone", "sms", "number",
    ],
    "email": ["email", "recipient_email", "share.email", "recipient.email"],
}

_URL_RE = re.compile(r"^https?://", re.I)
_MEDIA_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|heic|heif|mp4|mov|m4v|webm)(\?|#|$)", re.I)
_VIDEO_EXT_RE = re.compile(r"\.(mp4|mov|m4v|webm)(\?|#|$)", re.I)
_GIF_EXT_RE = re.compile(r"\.gif(\?|#|$)", re.I)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _dig(data: dict[str, Any], dotted_key: str) -> Optional[Any]:
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


def _walk_strings(obj: Any) -> Iterator[str]:
    """Yield every string value anywhere in a nested dict/list structure."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def _sniff_media_url(payload: dict[str, Any]) -> Optional[str]:
    """Find the most media-like URL anywhere in the payload."""
    urls = [s.strip() for s in _walk_strings(payload) if _URL_RE.match(s.strip())]
    if not urls:
        return None
    # Prefer URLs that end in a media file extension (the actual file, not a page).
    media = [u for u in urls if _MEDIA_EXT_RE.search(u)]
    if media:
        return media[0]
    # Next, prefer URLs that hint at a file/download rather than a gallery page.
    hinted = [u for u in urls if re.search(r"(media|download|file|image|photo|video|cdn|amazonaws|storage)", u, re.I)]
    if hinted:
        return hinted[0]
    return urls[0]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _sniff_phone(payload: dict[str, Any]) -> Optional[str]:
    """Find a phone-number-looking string anywhere in the payload."""
    # Prefer E.164 (+15025551234).
    for s in _walk_strings(payload):
        st = s.strip()
        if st.startswith("+") and 10 <= len(_digits(st)) <= 15:
            return st
    # Fallback: a string that is essentially just 10–11 digits (US style).
    for s in _walk_strings(payload):
        st = s.strip()
        cleaned = re.sub(r"[()\-\s.]", "", st).lstrip("+")
        if cleaned.isdigit() and 10 <= len(cleaned) <= 11:
            return st
    return None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _sniff_email(payload: dict[str, Any]) -> Optional[str]:
    for s in _walk_strings(payload):
        if _EMAIL_RE.match(s.strip()):
            return s.strip()
    return None


def _type_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if _VIDEO_EXT_RE.search(url):
        return "video"
    if _GIF_EXT_RE.search(url):
        return "gif"
    if _MEDIA_EXT_RE.search(url):
        return "photo"
    return None


def _normalize_type(raw: Optional[Any]) -> Optional[str]:
    if raw is None:
        return None
    t = str(raw).strip().lower()
    if t in {"photo", "image", "picture", "still", "jpg", "jpeg", "png"}:
        return "photo"
    if t in {"gif", "boomerang", "burst"}:
        return "gif"
    if t in {"video", "mp4", "clip", "mov"}:
        return "video"
    if t in {"ai", "ai_image", "aiimage", "generated"}:
        return "ai"
    return t


def _coerce_str(val: Optional[Any]) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

class CaptureEvent:
    def __init__(self, payload: dict[str, Any]):
        self.session_id = _coerce_str(_first(payload, _CAPTURE_FIELDS["session_id"]))
        # media URL: known keys first, then sniff for any media URL.
        self.media_url = _coerce_str(_first(payload, _CAPTURE_FIELDS["media_url"]))
        if not self.media_url:
            self.media_url = _sniff_media_url(payload)
        # media type: known keys, else infer from the URL.
        self.media_type = _normalize_type(_first(payload, _CAPTURE_FIELDS["media_type"]))
        if not self.media_type:
            self.media_type = _type_from_url(self.media_url) or "photo"
        self.site_url = _coerce_str(_first(payload, _CAPTURE_FIELDS["site_url"]))

    @property
    def is_valid(self) -> bool:
        return bool(self.session_id and self.media_url)


class ShareEvent:
    def __init__(self, payload: dict[str, Any]):
        self.session_id = _coerce_str(_first(payload, _SHARE_FIELDS["session_id"]))
        method = _first(payload, _SHARE_FIELDS["share_method"])
        self.share_method = str(method).strip().lower() if method is not None else None
        # phone: known keys first, then sniff.
        self.phone = _coerce_str(_first(payload, _SHARE_FIELDS["phone"]))
        if not self.phone:
            self.phone = _sniff_phone(payload)
        self.email = _coerce_str(_first(payload, _SHARE_FIELDS["email"]))
        if not self.email:
            self.email = _sniff_email(payload)
        # Normalize/repair method.
        if self.share_method in {"sms", "text", "phone", "mobile"}:
            self.share_method = "sms"
        elif self.share_method in {"email", "mail"}:
            self.share_method = "email"
        if not self.share_method or self.share_method not in {"sms", "email"}:
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
