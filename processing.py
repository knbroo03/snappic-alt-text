"""Orchestration: turn webhook events into stored descriptions and sent texts.

The two webhook events can arrive in either order:

  capture then share  -> description is usually ready; deliver immediately.
  share then capture   -> mark delivery PENDING; deliver when captioning finishes.

Delivery is guarded on delivery_status so a guest never gets two texts even if
both code paths race.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from . import media
from .captioner import CaptionError, Captioner
from .config import Settings, get_settings
from .db import engine
from .models import CaptionStatus, DeliveryStatus, MediaSession
from .schemas import CaptureEvent, ShareEvent
from .sms import SmsError, SmsSender, compose_message

log = logging.getLogger("snappic")

_CAPTION_RETRIES = 3
_SEND_RETRIES = 3
_RETRY_BACKOFF_SEC = 2.0


class Services:
    """Holds the external-service clients. Lazily built; injectable for tests."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        captioner: Optional[Captioner] = None,
        sms_sender: Optional[SmsSender] = None,
    ):
        self.settings = settings or get_settings()
        self._captioner = captioner
        self._sms_sender = sms_sender

    @property
    def captioner(self) -> Captioner:
        if self._captioner is None:
            self._captioner = Captioner(self.settings)
        return self._captioner

    @property
    def sms_sender(self) -> SmsSender:
        if self._sms_sender is None:
            self._sms_sender = SmsSender(self.settings)
        return self._sms_sender


def _touch(row: MediaSession) -> None:
    row.updated_at = datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Event entry points (called from the webhook routes as background tasks)
# --------------------------------------------------------------------------

def handle_capture(event: CaptureEvent, services: Services) -> None:
    """Upsert the session, then generate and store its description."""
    with Session(engine) as db:
        row = db.get(MediaSession, event.session_id)
        if row is None:
            row = MediaSession(session_id=event.session_id)
            db.add(row)
        row.media_type = event.media_type or row.media_type
        row.media_url = event.media_url or row.media_url
        row.site_url = event.site_url or row.site_url
        # If we already have a good description, don't redo the work.
        already_done = row.caption_status == CaptionStatus.READY and row.alt_text
        _touch(row)
        db.commit()
        if already_done:
            log.info("capture: %s already captioned; skipping", event.session_id)
            _try_deliver(event.session_id, services, db=None)
            return

    _caption_session(event.session_id, services)
    # Captioning done (or failed) -> attempt any waiting delivery.
    _try_deliver(event.session_id, services, db=None)


def handle_share(event: ShareEvent, services: Services) -> None:
    """Record the guest's contact info and deliver if we can."""
    with Session(engine) as db:
        row = db.get(MediaSession, event.session_id)
        if row is None:
            # Share arrived before capture — create a stub to hold the phone.
            row = MediaSession(session_id=event.session_id)
            db.add(row)
        row.share_method = event.share_method or row.share_method
        if event.phone:
            row.share_phone = event.phone
        if not event.is_sms:
            # Email shares (or shares without a phone) aren't delivered by us.
            if row.delivery_status == DeliveryStatus.NONE:
                row.delivery_status = DeliveryStatus.SKIPPED
        _touch(row)
        db.commit()

    _try_deliver(event.session_id, services, db=None)


# --------------------------------------------------------------------------
# Captioning
# --------------------------------------------------------------------------

def _caption_session(session_id: str, services: Services) -> None:
    with Session(engine) as db:
        row = db.get(MediaSession, session_id)
        if row is None or not row.media_url:
            log.warning("caption: no media_url for %s", session_id)
            return
        media_url = row.media_url
        media_type = row.media_type
        row.caption_status = CaptionStatus.PENDING
        _touch(row)
        db.commit()

    last_err = ""
    for attempt in range(1, _CAPTION_RETRIES + 1):
        try:
            raw = media.download(media_url)
            still = media.prepare_still(raw, media_type)
            alt_text = services.captioner.describe(still, media_type)
            with Session(engine) as db:
                row = db.get(MediaSession, session_id)
                row.alt_text = alt_text
                row.caption_status = CaptionStatus.READY
                row.caption_error = None
                _touch(row)
                db.commit()
            log.info("caption: ready for %s: %r", session_id, alt_text)
            return
        except (CaptionError, media.MediaError, Exception) as exc:  # noqa: BLE001
            last_err = str(exc)
            log.warning(
                "caption attempt %d/%d failed for %s: %s",
                attempt, _CAPTION_RETRIES, session_id, last_err,
            )
            if attempt < _CAPTION_RETRIES:
                time.sleep(_RETRY_BACKOFF_SEC * attempt)

    with Session(engine) as db:
        row = db.get(MediaSession, session_id)
        if row is not None:
            row.caption_status = CaptionStatus.FAILED
            row.caption_error = last_err[:500]
            _touch(row)
            db.commit()
    log.error("caption: gave up on %s: %s", session_id, last_err)


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------

def _try_deliver(session_id: str, services: Services, db: Optional[Session]) -> None:
    """Send the SMS if — and only if — everything is ready and not already sent.

    Idempotent: guarded on delivery_status so racing callers don't double-send.
    """
    with Session(engine) as session_db:
        row = session_db.get(MediaSession, session_id)
        if row is None:
            return

        # Already handled?
        if row.delivery_status in (DeliveryStatus.SENT, DeliveryStatus.SKIPPED):
            return

        # Not an SMS share (or no phone yet) -> nothing to send.
        if row.share_method != "sms" or not row.share_phone:
            return

        # Description not ready yet -> remember to deliver once it is.
        if row.caption_status != CaptionStatus.READY or not row.alt_text:
            if row.caption_status == CaptionStatus.FAILED:
                # No description will ever come; leave delivery unsent + logged.
                log.error("deliver: caption failed for %s; not sending", session_id)
                return
            row.delivery_status = DeliveryStatus.PENDING
            _touch(row)
            session_db.commit()
            log.info("deliver: %s waiting on description", session_id)
            return

        phone = row.share_phone
        body = compose_message(services.settings, row.alt_text)

    # Send outside the DB block (network call).
    last_err = ""
    for attempt in range(1, _SEND_RETRIES + 1):
        try:
            sid = services.sms_sender.send(phone, body)
            with Session(engine) as session_db:
                row = session_db.get(MediaSession, session_id)
                if row.delivery_status == DeliveryStatus.SENT:
                    return  # someone beat us to it
                row.delivery_status = DeliveryStatus.SENT
                row.twilio_message_sid = sid
                row.delivery_error = None
                _touch(row)
                session_db.commit()
            log.info("deliver: sent to %s for %s (sid=%s)", phone, session_id, sid)
            return
        except (SmsError, Exception) as exc:  # noqa: BLE001
            last_err = str(exc)
            log.warning(
                "deliver attempt %d/%d failed for %s: %s",
                attempt, _SEND_RETRIES, session_id, last_err,
            )
            if attempt < _SEND_RETRIES:
                time.sleep(_RETRY_BACKOFF_SEC * attempt)

    with Session(engine) as session_db:
        row = session_db.get(MediaSession, session_id)
        if row is not None and row.delivery_status != DeliveryStatus.SENT:
            row.delivery_status = DeliveryStatus.FAILED
            row.delivery_error = last_err[:500]
            _touch(row)
            session_db.commit()
    log.error("deliver: gave up on %s: %s", session_id, last_err)


# --------------------------------------------------------------------------
# Staff-initiated resend (admin dashboard)
# --------------------------------------------------------------------------

def force_deliver(
    session_id: str,
    services: Services,
    corrected_text: Optional[str] = None,
    phone_override: Optional[str] = None,
) -> dict:
    """Send (or re-send) the description immediately, on a staff member's action.

    Unlike ``_try_deliver`` this ignores the current delivery_status, so staff
    can correct a description and re-send even after one was already delivered.
    Runs synchronously so the dashboard gets an immediate result. Returns a
    dict: {"ok": bool, "error"?: str, "message_sid"?: str, "session"?: dict}.
    """
    with Session(engine) as db:
        row = db.get(MediaSession, session_id)
        if row is None:
            return {"ok": False, "error": "session not found"}

        if corrected_text is not None and corrected_text.strip():
            row.alt_text = corrected_text.strip()
            row.caption_status = CaptionStatus.READY
            row.caption_error = None
        if phone_override and phone_override.strip():
            row.share_phone = phone_override.strip()
            row.share_method = "sms"

        if not row.alt_text:
            return {"ok": False, "error": "no description to send"}
        if not row.share_phone:
            return {"ok": False, "error": "no phone number on file for this guest"}

        phone = row.share_phone
        body = compose_message(services.settings, row.alt_text)
        _touch(row)
        db.commit()

    try:
        sid = services.sms_sender.send(phone, body)
    except Exception as exc:  # noqa: BLE001
        with Session(engine) as db:
            row = db.get(MediaSession, session_id)
            if row is not None:
                row.delivery_status = DeliveryStatus.FAILED
                row.delivery_error = str(exc)[:500]
                _touch(row)
                db.commit()
        log.error("force_deliver failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}

    with Session(engine) as db:
        row = db.get(MediaSession, session_id)
        row.delivery_status = DeliveryStatus.SENT
        row.twilio_message_sid = sid
        row.delivery_error = None
        row.resend_count = (row.resend_count or 0) + 1
        _touch(row)
        db.commit()
        result = row.model_dump()
    log.info("force_deliver: sent to %s for %s (sid=%s)", phone, session_id, sid)
    return {"ok": True, "message_sid": sid, "session": result}
