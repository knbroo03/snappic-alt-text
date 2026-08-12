"""End-to-end and unit tests. All external services are mocked."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from tests.conftest import make_jpeg_bytes


# --------------------------------------------------------------------------
# Payload parsing
# --------------------------------------------------------------------------

def test_capture_parsing_nested():
    from app.schemas import CaptureEvent

    ev = CaptureEvent(
        {"session": {"id": "abc", "type": "Photo", "direct_url": "http://x/p.jpg",
                     "site_url": "http://x/g"}}
    )
    assert ev.is_valid
    assert ev.session_id == "abc"
    assert ev.media_type == "photo"
    assert ev.media_url == "http://x/p.jpg"
    assert ev.site_url == "http://x/g"


def test_capture_parsing_flat():
    from app.schemas import CaptureEvent

    ev = CaptureEvent({"id": "z9", "type": "video", "url": "http://x/v.mp4"})
    assert ev.is_valid
    assert ev.session_id == "z9"
    assert ev.media_type == "video"


def test_capture_invalid_when_no_url():
    from app.schemas import CaptureEvent

    ev = CaptureEvent({"session": {"id": "abc"}})
    assert not ev.is_valid


def test_share_parsing_and_method_inference():
    from app.schemas import ShareEvent

    ev = ShareEvent({"session": {"id": "abc"}, "recipient": "+15555551212"})
    assert ev.is_valid
    assert ev.is_sms
    assert ev.phone == "+15555551212"
    assert ev.share_method == "sms"

    email_ev = ShareEvent({"id": "abc", "email": "g@example.com"})
    assert email_ev.share_method == "email"
    assert not email_ev.is_sms


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------

def test_token_verification():
    from app.config import Settings
    from app.security import verify_webhook

    s = Settings(snappic_verify_mode="token", snappic_webhook_secret="topsecret")
    assert verify_webhook(s, b"{}", "topsecret")
    assert not verify_webhook(s, b"{}", "wrong")
    assert not verify_webhook(s, b"{}", None)


def test_hmac_verification():
    import hashlib
    import hmac

    from app.config import Settings
    from app.security import verify_webhook

    secret = "topsecret"
    body = b'{"session":{"id":"abc"}}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    s = Settings(snappic_verify_mode="hmac", snappic_webhook_secret=secret)
    assert verify_webhook(s, body, sig)
    assert verify_webhook(s, body, f"sha256={sig}")
    assert not verify_webhook(s, body, "deadbeef")


# --------------------------------------------------------------------------
# Media handling
# --------------------------------------------------------------------------

def test_prepare_still_photo():
    from app.media import prepare_still

    out = prepare_still(make_jpeg_bytes(), "photo")
    assert out.media_type == "image/jpeg"
    assert len(out.data) > 0


def test_prepare_still_gif_picks_frame():
    from app.media import prepare_still

    frames = [Image.new("RGB", (32, 32), c) for c in
              [(255, 0, 0), (0, 255, 0), (0, 0, 255)]]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    out = prepare_still(buf.getvalue(), "gif")
    assert out.media_type == "image/jpeg"
    assert len(out.data) > 0


# --------------------------------------------------------------------------
# Captioner + SMS wrappers (mocked SDK clients)
# --------------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResp:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeAnthropic:
    def __init__(self, text="Two people smiling and giving a thumbs up."):
        self._text = text
        self.messages = self
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._text)


def test_captioner_strips_preamble():
    from app.captioner import Captioner
    from app.config import Settings
    from app.media import PreparedImage

    fake = _FakeAnthropic(text='"Image of two friends laughing."')
    cap = Captioner(Settings(anthropic_api_key="x"), client=fake)
    out = cap.describe(PreparedImage(data=make_jpeg_bytes(), media_type="image/jpeg"),
                       "photo")
    assert not out.lower().startswith("image of")
    assert "two friends laughing" in out.lower()
    assert fake.calls  # the client was actually called


class _FakeMessage:
    def __init__(self, sid="SM123"):
        self.sid = sid


class _FakeTwilio:
    def __init__(self):
        self.messages = self
        self.sent = []

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return _FakeMessage()


def test_sms_send_and_compose():
    from app.config import Settings
    from app.sms import SmsSender, compose_message

    s = Settings(twilio_account_sid="AC", twilio_auth_token="tok",
                 twilio_from_number="+15555550123", sms_prefix="Photo description:",
                 event_name="Gala")
    fake = _FakeTwilio()
    sender = SmsSender(s, client=fake)
    sid = sender.send("+15555551212", "hello")
    assert sid == "SM123"
    assert fake.sent[0]["to"] == "+15555551212"
    assert fake.sent[0]["from_"] == "+15555550123"

    body = compose_message(s, "Two people waving.")
    assert body.startswith("Photo description: Two people waving.")
    assert "(Gala)" in body


# --------------------------------------------------------------------------
# End-to-end orchestration (the important one)
# --------------------------------------------------------------------------

def _mock_services(caption_text="Two guests laughing at a photo booth."):
    from app.config import get_settings
    from app.processing import Services

    class MockCaptioner:
        def __init__(self):
            self.calls = 0

        def describe(self, image, media_type=None):
            self.calls += 1
            return caption_text

    class MockSms:
        def __init__(self):
            self.sent = []

        def send(self, to_number, body):
            self.sent.append((to_number, body))
            return "SM_TEST"

    cap, sms = MockCaptioner(), MockSms()
    svc = Services(settings=get_settings(), captioner=cap, sms_sender=sms)
    return svc, cap, sms


def test_capture_then_share_delivers(monkeypatch):
    import app.media as media_mod
    import app.processing as processing
    from app.models import CaptionStatus, DeliveryStatus, MediaSession
    from app.schemas import CaptureEvent, ShareEvent
    from app.db import engine
    from sqlmodel import Session

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()

    processing.handle_capture(
        CaptureEvent({"session": {"id": "S1", "type": "photo",
                                  "direct_url": "http://x/p.jpg"}}), svc)

    with Session(engine) as db:
        row = db.get(MediaSession, "S1")
        assert row.caption_status == CaptionStatus.READY
        assert row.alt_text == "Two guests laughing at a photo booth."

    processing.handle_share(
        ShareEvent({"session": {"id": "S1"}, "method": "sms",
                    "recipient": "+15555551212"}), svc)

    assert sms.sent, "expected an SMS to be sent"
    to, body = sms.sent[0]
    assert to == "+15555551212"
    assert "Two guests laughing" in body

    with Session(engine) as db:
        row = db.get(MediaSession, "S1")
        assert row.delivery_status == DeliveryStatus.SENT
        assert row.twilio_message_sid == "SM_TEST"


def test_share_before_capture_still_delivers(monkeypatch):
    import app.media as media_mod
    import app.processing as processing
    from app.models import DeliveryStatus, MediaSession
    from app.schemas import CaptureEvent, ShareEvent
    from app.db import engine
    from sqlmodel import Session

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()

    # Share arrives first — no description yet.
    processing.handle_share(
        ShareEvent({"id": "S2", "method": "sms", "recipient": "+15555559999"}), svc)
    assert not sms.sent
    with Session(engine) as db:
        row = db.get(MediaSession, "S2")
        assert row.delivery_status == DeliveryStatus.PENDING

    # Capture arrives — captioning completes and delivery fires.
    processing.handle_capture(
        CaptureEvent({"id": "S2", "type": "photo", "url": "http://x/p.jpg"}), svc)

    assert sms.sent, "expected delivery after late capture"
    assert sms.sent[0][0] == "+15555559999"
    with Session(engine) as db:
        row = db.get(MediaSession, "S2")
        assert row.delivery_status == DeliveryStatus.SENT


def test_no_double_send_on_repeated_events(monkeypatch):
    import app.media as media_mod
    import app.processing as processing
    from app.schemas import CaptureEvent, ShareEvent

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()

    cap_ev = CaptureEvent({"id": "S3", "type": "photo", "url": "http://x/p.jpg"})
    share_ev = ShareEvent({"id": "S3", "method": "sms", "recipient": "+15555550000"})

    processing.handle_capture(cap_ev, svc)
    processing.handle_share(share_ev, svc)
    # Duplicate deliveries of both events (Snappic may retry).
    processing.handle_share(share_ev, svc)
    processing.handle_capture(cap_ev, svc)

    assert len(sms.sent) == 1, "guest must receive exactly one text"


def test_email_share_is_skipped(monkeypatch):
    import app.media as media_mod
    import app.processing as processing
    from app.models import DeliveryStatus, MediaSession
    from app.schemas import CaptureEvent, ShareEvent
    from app.db import engine
    from sqlmodel import Session

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()

    processing.handle_capture(
        CaptureEvent({"id": "S4", "type": "photo", "url": "http://x/p.jpg"}), svc)
    processing.handle_share(
        ShareEvent({"id": "S4", "method": "email", "email": "g@example.com"}), svc)

    assert not sms.sent
    with Session(engine) as db:
        row = db.get(MediaSession, "S4")
        assert row.delivery_status == DeliveryStatus.SKIPPED


def test_admin_requires_auth(monkeypatch):
    import app.main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ADMIN_PASSWORD", "staffpw")
    main_mod.settings.admin_password = "staffpw"  # reflect env into loaded settings
    client = TestClient(main_mod.app)

    # No credentials -> 401.
    assert client.get("/admin").status_code == 401
    assert client.get("/admin/api/sessions").status_code == 401
    assert client.get("/sessions").status_code == 401

    # Correct credentials -> 200.
    ok = ("admin", "staffpw")
    assert client.get("/admin", auth=ok).status_code == 200
    assert client.get("/admin/api/sessions", auth=ok).status_code == 200

    # Wrong password -> 401.
    assert client.get("/admin", auth=("admin", "nope")).status_code == 401


def test_admin_resend_corrects_and_sends(monkeypatch):
    import app.main as main_mod
    import app.media as media_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()
    main_mod.services = svc
    main_mod.settings.admin_password = "staffpw"

    client = TestClient(main_mod.app)
    auth = ("admin", "staffpw")

    # Create a delivered session first.
    client.post("/webhooks/snappic/session",
                json={"id": "R1", "type": "photo", "url": "http://x/p.jpg"})
    client.post("/webhooks/snappic/share",
                json={"id": "R1", "method": "sms", "recipient": "+15555551212"})
    assert len(sms.sent) == 1

    # Staff corrects the description and resends.
    r = client.post("/admin/api/sessions/R1/resend", auth=auth,
                    json={"alt_text": "A corrected, better description."})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # A second text went out, with the corrected wording.
    assert len(sms.sent) == 2
    assert "corrected, better description" in sms.sent[1][1]

    status = client.get("/sessions/R1", auth=auth).json()
    assert status["alt_text"] == "A corrected, better description."
    assert status["resend_count"] == 1


def test_admin_resend_without_phone_reports_error(monkeypatch):
    import app.main as main_mod
    import app.media as media_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()
    main_mod.services = svc
    main_mod.settings.admin_password = "staffpw"

    client = TestClient(main_mod.app)
    auth = ("admin", "staffpw")

    # Captured but never shared -> no phone on file.
    client.post("/webhooks/snappic/session",
                json={"id": "R2", "type": "photo", "url": "http://x/p.jpg"})
    r = client.post("/admin/api/sessions/R2/resend", auth=auth, json={})
    assert r.status_code == 400
    assert "phone" in r.json()["error"].lower()
    assert not sms.sent


def test_webhook_endpoints_via_testclient(monkeypatch):
    """Exercise the actual FastAPI routes with mocked services."""
    import app.main as main_mod
    import app.media as media_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(media_mod, "download", lambda url, timeout=30.0: make_jpeg_bytes())
    svc, cap, sms = _mock_services()
    # Inject mock services into the running app.
    main_mod.services = svc
    main_mod.settings.admin_password = "staffpw"

    client = TestClient(main_mod.app)

    r = client.post("/webhooks/snappic/session",
                    json={"session": {"id": "W1", "type": "photo",
                                      "direct_url": "http://x/p.jpg"}})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"

    r2 = client.post("/webhooks/snappic/share",
                     json={"session": {"id": "W1"}, "method": "sms",
                           "recipient": "+15555551234"})
    assert r2.status_code == 200

    # With TestClient, background tasks run synchronously after the response,
    # so by now the SMS should have been sent.
    assert sms.sent, "expected SMS after share webhook"

    status = client.get("/sessions/W1", auth=("admin", "staffpw")).json()
    assert status["delivery_status"] == "sent"
