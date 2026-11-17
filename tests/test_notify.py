"""Tests for sending notifications, with ntfy faked by httpx.MockTransport.

Same approach as test_github.py: the request is built for real, then answered
locally, so running the tests never sends anything to your phone.
"""

import httpx
import pytest

from jarvis import notify

TOPIC = "jarvis-fake-topic-for-tests"


@pytest.fixture
def sent(monkeypatch):
    """Answer ntfy locally. Returns the list of requests it received."""
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    def fake_post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    return seen


def test_plain_message(sent):
    notify.send("Madrid is 21 degrees.")
    [request] = sent
    assert request.url == f"https://ntfy.sh/{TOPIC}"
    assert request.content.decode() == "Madrid is 21 degrees."
    assert request.headers["Title"] == "Jarvis"
    assert "Actions" not in request.headers
    assert "Click" not in request.headers


def test_accents_survive_in_the_message(sent):
    notify.send("Mañana llueve en Málaga.")
    assert sent[0].content.decode("utf-8") == "Mañana llueve en Málaga."


def test_button(sent):
    button = notify.post_button("Create issue", "https://example.com/ideas/confirm", "abc123")
    notify.send("Train alerts", title="New idea", actions=button)
    [request] = sent
    assert request.headers["Title"] == "New idea"
    assert request.headers["Actions"] == (
        "http, Create issue, https://example.com/ideas/confirm, method=POST, body=abc123, clear=true"
    )


def test_click_opens_a_page(sent):
    notify.send("Created #31: Train alerts", click="https://github.com/someone/some-repo/issues/31")
    assert sent[0].headers["Click"] == "https://github.com/someone/some-repo/issues/31"


@pytest.mark.parametrize(
    "label, url, body",
    [
        ("Create, now", "https://example.com/x", "abc"),
        ("Create", "https://example.com/x;y", "abc"),
        ("Create", "https://example.com/x", "a,b"),
    ],
)
def test_button_refuses_commas_and_semicolons(label, url, body):
    """ntfy splits buttons on these, so they'd silently break the button."""
    with pytest.raises(ValueError):
        notify.post_button(label, url, body)
