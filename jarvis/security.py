"""Checks that a webhook request really came from your Pebble app.

Follows Pebble's "Index webhook protocol version 1":
https://developer.repebble.com/index-01/webhooks/

The Pebble app and Jarvis share a secret. For each request, the app computes a
signature from the request's contents using that secret. We repeat the
calculation here: if the results match, the request is genuine and untampered.
The secret itself is never sent.
"""

import hashlib
import hmac
import time
from collections.abc import Mapping

SUPPORTED_VERSION = "1"

# Reject requests signed more than 5 minutes ago (or in the future), so a
# recorded genuine request can't be replayed later. Pebble suggests 5 minutes.
MAX_AGE_SECONDS = 5 * 60


class InvalidRequest(Exception):
    """The request failed a check. The message says which one, for the log."""


def signed_bytes(timestamp: str, delivery: str, trigger: str, is_test: bool, body: bytes) -> bytes:
    """Build the exact bytes Pebble signs: five header lines, then the raw body.

    Shared with the tests, which use it to create genuinely signed requests.
    """
    prefix = f"v1\n{timestamp}\n{delivery}\n{trigger}\n{'1' if is_test else '0'}\n"
    return prefix.encode("utf-8") + body


def sign(secret: str, data: bytes) -> str:
    """HMAC-SHA256 of `data` keyed with `secret`, as lowercase hex."""
    return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


def verify(headers: Mapping[str, str], body: bytes, secret: str, now: float | None = None) -> None:
    """Raise InvalidRequest unless the request is fresh and correctly signed.

    `body` must be the raw bytes exactly as received, before any form parsing.
    Parsing can change the bytes slightly, which would break the signature.
    """
    # HTTP header names ignore case, so compare them in lowercase.
    h = {name.lower(): value for name, value in headers.items()}

    # 1. Version
    version = h.get("x-index-webhook-version")
    if version != SUPPORTED_VERSION:
        raise InvalidRequest(f"unsupported webhook version: {version!r}")

    # 2. Freshness
    timestamp = h.get("x-index-timestamp", "")
    if not timestamp.isdigit():
        raise InvalidRequest("missing or malformed timestamp")
    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > MAX_AGE_SECONDS:
        raise InvalidRequest("timestamp outside the 5-minute window")

    delivery = h.get("x-index-delivery")
    trigger = h.get("x-index-trigger")
    received = h.get("x-index-signature")
    if not (delivery and trigger and received):
        raise InvalidRequest("missing delivery, trigger or signature header")

    # 3. Recompute the signature ourselves
    is_test = h.get("x-index-test") == "true"
    expected = sign(secret, signed_bytes(timestamp, delivery, trigger, is_test, body))

    # 4. Compare in constant time, so response timing reveals nothing
    if not hmac.compare_digest(expected, received.lower()):
        raise InvalidRequest("signature does not match")
