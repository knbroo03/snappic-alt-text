"""Webhook authenticity verification.

Snappic's exact signing method is not published, so this supports three modes
(set SNAPPIC_VERIFY_MODE):

  * "hmac"  - Snappic sends HMAC-SHA256(secret, raw_body) in a header. We
              recompute it over the raw request body and compare. Most secure.
  * "token" - Snappic sends the shared secret verbatim in a header. We compare
              it in constant time. Simple; fine over HTTPS.
  * "none"  - No verification. LOCAL TESTING ONLY.

Once you confirm Snappic's real mechanism (see README), keep the matching mode
and delete the others if you like.
"""

from __future__ import annotations

import hashlib
import hmac

from config import Settings


def verify_webhook(settings: Settings, raw_body: bytes, header_value: str | None) -> bool:
    mode = settings.snappic_verify_mode
    secret = settings.snappic_webhook_secret

    if mode == "none":
        return True

    if not secret:
        # Misconfiguration: a verify mode is on but no secret is set.
        return False

    if header_value is None:
        return False

    if mode == "token":
        return hmac.compare_digest(header_value.strip(), secret.strip())

    if mode == "hmac":
        computed = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        supplied = header_value.strip()
        # Accept both "sha256=<hex>" and bare "<hex>" formats.
        if supplied.lower().startswith("sha256="):
            supplied = supplied.split("=", 1)[1]
        return hmac.compare_digest(computed, supplied)

    return False
