"""FastAPI application: Snappic webhook endpoints + a small status API."""

from __future__ import annotations

import hmac
import json
import logging
from collections import deque

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from sqlmodel import Session, select

__version__ = "1.0.0"
from config import get_settings
from dashboard import render_dashboard
from db import engine, init_db
from models import MediaSession
from processing import Services, force_deliver, handle_capture, handle_share
from schemas import CaptureEvent, ShareEvent
from security import verify_webhook

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("snappic")

app = FastAPI(
    title="Snappic AI Alt-Text Service",
    version=__version__,
    description=(
        "Generates screen-reader-friendly descriptions of photo-booth media "
        "and texts them to guests for accessibility."
    ),
)

# One shared Services container for the process (clients are reused).
services = Services(settings=settings)

# Ring buffer of the most recent raw webhook payloads, newest first — used to
# inspect exactly what Snappic sends (see GET /admin/debug/payloads).
RECENT_PAYLOADS: deque = deque(maxlen=25)

# --------------------------------------------------------------------------
# Admin auth (HTTP Basic) — protects the dashboard and guest-data endpoints
# --------------------------------------------------------------------------
_basic = HTTPBasic(auto_error=True)


def require_admin(credentials: HTTPBasicCredentials = Depends(_basic)) -> bool:
    if not settings.admin_configured:
        raise HTTPException(
            status_code=503,
            detail="Admin is not configured. Set ADMIN_PASSWORD to enable it.",
        )
    user_ok = hmac.compare_digest(credentials.username, settings.admin_username)
    pass_ok = hmac.compare_digest(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


class ResendRequest(BaseModel):
    alt_text: str | None = None   # corrected description (optional)
    phone: str | None = None      # override phone number (optional)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("Snappic alt-text service v%s starting", __version__)
    if not settings.anthropic_configured:
        log.warning("ANTHROPIC_API_KEY not set — captioning will fail.")
    if not settings.twilio_configured:
        log.warning("Twilio not fully configured — SMS delivery will fail.")
    if settings.snappic_verify_mode == "none":
        log.warning("Webhook verification is OFF (mode=none). Do not use in production.")
    if not settings.admin_configured:
        log.warning("ADMIN_PASSWORD not set — the /admin dashboard is disabled.")


# --------------------------------------------------------------------------
# Health / info
# --------------------------------------------------------------------------

@app.get("/")
def root() -> dict:
    return {
        "service": "snappic-alt-text",
        "version": __version__,
        "status": "ok",
        "anthropic_configured": settings.anthropic_configured,
        "twilio_configured": settings.twilio_configured,
        "admin_configured": settings.admin_configured,
        "verify_mode": settings.snappic_verify_mode,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/admin/debug/payloads", dependencies=[Depends(require_admin)])
def debug_payloads() -> list:
    """The most recent raw webhook payloads (newest first). For diagnosing format."""
    return list(RECENT_PAYLOADS)


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------

async def _read_and_verify(request: Request, signature: str | None) -> dict:
    raw = await request.body()
    if not verify_webhook(settings, raw, signature):
        log.warning("Rejected webhook: signature verification failed")
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")
    try:
        RECENT_PAYLOADS.appendleft({"path": request.url.path, "payload": payload})
    except Exception:  # noqa: BLE001
        pass
    log.info("RAW webhook %s: %s", request.url.path, json.dumps(payload)[:2000])
    return payload


@app.post("/webhooks/snappic/session")
async def snappic_session(
    request: Request,
    background: BackgroundTasks,
) -> JSONResponse:
    """Fired when media is captured. We caption it here."""
    signature = request.headers.get(settings.snappic_signature_header)
    payload = await _read_and_verify(request, signature)

    event = CaptureEvent(payload)
    if not event.is_valid:
        log.warning("session webhook missing session_id or media_url: %s", payload)
        # 200 so Snappic doesn't retry a payload we simply can't use.
        return JSONResponse(
            {"status": "ignored", "reason": "missing session_id or media_url"},
            status_code=200,
        )

    background.add_task(handle_capture, event, services)
    return JSONResponse({"status": "accepted", "session_id": event.session_id})


@app.post("/webhooks/snappic/share")
async def snappic_share(
    request: Request,
    background: BackgroundTasks,
) -> JSONResponse:
    """Fired when the guest shares/receives the media. We deliver the text here."""
    signature = request.headers.get(settings.snappic_signature_header)
    payload = await _read_and_verify(request, signature)

    event = ShareEvent(payload)
    if not event.is_valid:
        log.warning("share webhook missing session_id: %s", payload)
        return JSONResponse(
            {"status": "ignored", "reason": "missing session_id"}, status_code=200
        )

    background.add_task(handle_share, event, services)
    return JSONResponse({"status": "accepted", "session_id": event.session_id})


# --------------------------------------------------------------------------
# Status API — protected (contains guest phone numbers)
# --------------------------------------------------------------------------

@app.get("/sessions/{session_id}", dependencies=[Depends(require_admin)])
def get_session(session_id: str) -> dict:
    with Session(engine) as db:
        row = db.get(MediaSession, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        return row.model_dump()


@app.get("/sessions", dependencies=[Depends(require_admin)])
def list_sessions(limit: int = 50) -> list[dict]:
    return _recent_sessions(limit)


def _recent_sessions(limit: int) -> list[dict]:
    with Session(engine) as db:
        rows = db.exec(
            select(MediaSession).order_by(MediaSession.created_at.desc()).limit(limit)
        ).all()
        return [r.model_dump() for r in rows]


# --------------------------------------------------------------------------
# Admin dashboard
# --------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_dashboard() -> HTMLResponse:
    return HTMLResponse(render_dashboard(settings))


@app.get("/admin/api/sessions", dependencies=[Depends(require_admin)])
def admin_sessions(limit: int = 200) -> list[dict]:
    return _recent_sessions(limit)


@app.post("/admin/api/sessions/{session_id}/resend",
          dependencies=[Depends(require_admin)])
def admin_resend(session_id: str, body: ResendRequest) -> JSONResponse:
    """Staff action: (optionally) correct the description and (re)send the text."""
    result = force_deliver(
        session_id,
        services,
        corrected_text=body.alt_text,
        phone_override=body.phone,
    )
    status_code = 200 if result.get("ok") else 400
    if result.get("error") == "session not found":
        status_code = 404
    return JSONResponse(result, status_code=status_code)
