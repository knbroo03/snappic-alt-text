"""Database models.

A single table keyed on Snappic's session id tracks both the captioning
state and the delivery state, which lets the two webhook events (capture and
share) arrive in either order and still produce exactly one delivered text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CaptionStatus(str, Enum):
    PENDING = "pending"      # capture received, description not yet written
    READY = "ready"          # description written and stored
    FAILED = "failed"        # captioning failed after retries


class DeliveryStatus(str, Enum):
    NONE = "none"            # no share yet / not an SMS share
    PENDING = "pending"      # guest shared by SMS, waiting on the description
    SENT = "sent"            # text delivered to the guest
    FAILED = "failed"        # sending failed after retries
    SKIPPED = "skipped"      # e.g. share was by email, or no phone number


class MediaSession(SQLModel, table=True):
    """One row per Snappic session (one captured photo/gif/video/ai image)."""

    # Snappic's session id is the primary key and our idempotency key.
    session_id: str = Field(primary_key=True)

    media_type: Optional[str] = None      # photo | gif | video | ai
    media_url: Optional[str] = None       # direct link to the media file
    site_url: Optional[str] = None        # online gallery/microsite page

    alt_text: Optional[str] = None
    caption_status: CaptionStatus = Field(default=CaptionStatus.PENDING)
    caption_error: Optional[str] = None

    share_phone: Optional[str] = None
    share_method: Optional[str] = None    # sms | email
    delivery_status: DeliveryStatus = Field(default=DeliveryStatus.NONE)
    delivery_error: Optional[str] = None
    twilio_message_sid: Optional[str] = None
    resend_count: int = Field(default=0)  # times staff manually (re)sent

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
