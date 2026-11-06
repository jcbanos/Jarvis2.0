"""Tests for the webhook, end to end inside the app, with Claude and ntfy faked.

TestClient sends requests straight into the FastAPI app with no real network.
It also runs background tasks before returning, so we can check what was sent.
"""

import time

import pytest
from fastapi.testclient import TestClient

import jarvis.app as app_module
from jarvis.dedupe import DeliveryLog
from jarvis.security import sign, signed_bytes

SECRET = "test-secret-that-is-at-least-32-bytes-long"
BOUNDARY = "jarvis-test-boundary"


def multipart(fields: dict[str, str]) -> bytes:
    """Encode form fields as multipart/form-data, the format Pebble uses."""
    parts = [
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    ]
    return ("".join(parts) + f"--{BOUNDARY}--\r\n").encode("utf-8")


def pebble_request(transcription="What time is it in Tokyo?", delivery="delivery-1", is_test=False, secret=SECRET):
    """Build (body, headers) the way the Pebble app should, signature included."""
    fields = {"transcription": transcription, "recordedAt": "1800000000000", "client": "ring"}
    if is_test:
        fields["test"] = "true"
    body = multipart(fields)
    ts = str(int(time.time()))
    trigger = "test-event" if is_test else "double-click-hold"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        "X-Index-Webhook-Version": "1",
        "X-Index-Timestamp": ts,
        "X-Index-Delivery": delivery,
        "X-Index-Trigger": trigger,
        "X-Index-Signature": sign(secret, signed_bytes(ts, delivery, trigger, is_test, body)),
    }
    if is_test:
        headers["X-Index-Test"] = "true"
    return body, headers


@pytest.fixture
def jarvis(monkeypatch, tmp_path):
    """The app with fakes swapped in. Returns (client, questions asked, messages sent)."""
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(app_module, "deliveries", DeliveryLog(tmp_path / "d.sqlite3"))

    asked, sent = [], []

    def fake_answer(question):
        asked.append(question)
        return f"Answer to: {question}"

    monkeypatch.setattr(app_module, "answer", fake_answer)
    monkeypatch.setattr(app_module.notify, "send", lambda message, title="Jarvis": sent.append(message))
    return TestClient(app_module.app), asked, sent


def post(client, body, headers):
    return client.post("/webhook", content=body, headers=headers)


def test_question_is_answered_and_sent(jarvis):
    client, asked, sent = jarvis
    response = post(client, *pebble_request("What time is it in Tokyo?"))
    assert response.status_code == 200
    assert asked == ["What time is it in Tokyo?"]
    assert sent == ["Answer to: What time is it in Tokyo?"]


def test_accents_survive(jarvis):
    client, asked, _ = jarvis
    post(client, *pebble_request("¿Qué tiempo hace en Málaga?"))
    assert asked == ["¿Qué tiempo hace en Málaga?"]


def test_duplicate_is_answered_once(jarvis):
    client, asked, sent = jarvis
    body, headers = pebble_request(delivery="same-recording")
    post(client, body, headers)
    second = post(client, body, headers)
    assert second.status_code == 200  # OK, so Pebble stops retrying
    assert second.json() == {"status": "duplicate"}
    assert len(asked) == 1 and len(sent) == 1


def test_wrong_signature_is_rejected_and_nothing_happens(jarvis):
    client, asked, sent = jarvis
    response = post(client, *pebble_request(secret="a-stranger-who-does-not-know-the-secret"))
    assert response.status_code == 401
    assert asked == [] and sent == []


def test_test_event_confirms_without_calling_claude(jarvis):
    client, asked, sent = jarvis
    response = post(client, *pebble_request(is_test=True))
    assert response.json() == {"status": "test received"}
    assert asked == []
    assert len(sent) == 1 and sent[0].startswith("Webhook OK")


def test_empty_transcription_is_ignored(jarvis):
    client, asked, sent = jarvis
    response = post(client, *pebble_request(transcription="   "))
    assert response.json() == {"status": "empty"}
    assert asked == [] and sent == []


def test_failure_sends_couldnt_answer(jarvis, monkeypatch):
    client, _, sent = jarvis

    def broken_answer(question):
        raise RuntimeError("Anthropic is unreachable")

    monkeypatch.setattr(app_module, "answer", broken_answer)
    response = post(client, *pebble_request("Who won the match?"))
    assert response.status_code == 200
    assert sent == ["Couldn't answer: Who won the match?"]


def test_oversized_request_is_refused(jarvis):
    client, asked, _ = jarvis
    body, headers = pebble_request()
    huge = body + b"x" * (app_module.MAX_BODY_BYTES + 1)
    assert post(client, huge, headers).status_code == 413
    assert asked == []


def test_health(jarvis):
    client, _, _ = jarvis
    assert client.get("/health").json() == {"status": "ok"}
