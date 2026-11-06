"""Tests for the signature check. They make no network calls and cost nothing.

Run from the project folder:
    .venv/bin/pytest
"""

import pytest

from jarvis.security import InvalidRequest, sign, signed_bytes, verify

SECRET = "test-secret-that-is-at-least-32-bytes-long"
NOW = 1_800_000_000  # a fixed "current time", so tests don't depend on the clock
BODY = b'--boundary\r\nContent-Disposition: form-data; name="transcription"\r\n\r\nhello\r\n--boundary--\r\n'


def make_headers(body=BODY, secret=SECRET, timestamp=NOW, is_test=False, **overrides):
    """Build headers the way the Pebble app would, with a genuine signature."""
    ts, delivery, trigger = str(timestamp), "delivery-123", "double-click-hold"
    headers = {
        "X-Index-Webhook-Version": "1",
        "X-Index-Timestamp": ts,
        "X-Index-Delivery": delivery,
        "X-Index-Trigger": trigger,
        "X-Index-Signature": sign(secret, signed_bytes(ts, delivery, trigger, is_test, body)),
    }
    if is_test:
        headers["X-Index-Test"] = "true"
    headers.update(overrides)
    return headers


def test_genuine_request_passes():
    verify(make_headers(), BODY, SECRET, now=NOW)  # no exception means it passed


def test_genuine_test_event_passes():
    verify(make_headers(is_test=True), BODY, SECRET, now=NOW)


def test_header_names_ignore_case():
    headers = {name.lower(): value for name, value in make_headers().items()}
    verify(headers, BODY, SECRET, now=NOW)


def test_tampered_body_is_rejected():
    tampered = BODY.replace(b"hello", b"HELLO")
    with pytest.raises(InvalidRequest, match="signature"):
        verify(make_headers(), tampered, SECRET, now=NOW)


def test_wrong_secret_is_rejected():
    headers = make_headers(secret="an-attacker-guessing-the-secret-here")
    with pytest.raises(InvalidRequest, match="signature"):
        verify(headers, BODY, SECRET, now=NOW)


def test_old_request_is_rejected():
    headers = make_headers(timestamp=NOW - 6 * 60)  # signed 6 minutes ago
    with pytest.raises(InvalidRequest, match="window"):
        verify(headers, BODY, SECRET, now=NOW)


def test_test_flag_cannot_be_flipped():
    # Adding X-Index-Test to a real request changes the signed bytes.
    headers = make_headers(**{"X-Index-Test": "true"})
    with pytest.raises(InvalidRequest, match="signature"):
        verify(headers, BODY, SECRET, now=NOW)


@pytest.mark.parametrize(
    "missing", ["X-Index-Webhook-Version", "X-Index-Timestamp", "X-Index-Signature", "X-Index-Delivery"]
)
def test_missing_header_is_rejected(missing):
    headers = make_headers()
    del headers[missing]
    with pytest.raises(InvalidRequest):
        verify(headers, BODY, SECRET, now=NOW)
