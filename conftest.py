"""Shared test fixtures.

Everything external is mocked, so the suite runs with no API keys and no
network access. We use a throwaway SQLite file per test session.
"""

from __future__ import annotations

import io
import os

import pytest

# Point the app at a temp DB and a dummy config BEFORE importing app modules.
os.environ.setdefault("DATABASE_URL", "sqlite:///./_test_snappic.db")
os.environ.setdefault("SNAPPIC_VERIFY_MODE", "none")
os.environ.setdefault("SMS_PREFIX", "Photo description:")
os.environ.setdefault("EVENT_NAME", "")

from PIL import Image  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Give each test its own SQLite file and fresh engine/tables."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Rebuild the engine bound to this test's DB.
    import importlib

    from app import config as config_mod

    config_mod.get_settings.cache_clear()

    import app.db as db_mod
    importlib.reload(db_mod)

    # Rebind modules that captured `engine` at import time.
    import app.processing as processing_mod
    importlib.reload(processing_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    db_mod.init_db()
    yield


def make_jpeg_bytes(color=(120, 180, 240), size=(64, 64)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()
