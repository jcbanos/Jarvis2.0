"""Tests for ideas by voice: the propose_issue tool and the /ideas/confirm button.

Everything outside the Pi is faked: no notification reaches a phone and no
issue reaches GitHub. The draft store is real, in a throwaway file.
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient

import jarvis.app as app_module
from jarvis import github
from jarvis.drafts import IdeaDrafts
from jarvis.tools import TOOLS, ideas
from jarvis.tools.ideas import propose_issue

PUBLIC_URL = "https://jarvis.example.ts.net"


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Fake phone and GitHub, real drafts. Returns (client, notifications, issues).

    notifications: (message, options) for each notification sent.
    issues:        (title, body) for each issue created.
    """
    monkeypatch.setenv("JARVIS_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setattr(ideas, "drafts", IdeaDrafts(tmp_path / "drafts.sqlite3"))

    notifications = []
    monkeypatch.setattr(ideas.notify, "send", lambda message, **options: notifications.append((message, options)))

    issues = []

    def fake_create_issue(title, body):
        issues.append((title, body))
        return 31, "https://github.com/someone/some-repo/issues/31"

    monkeypatch.setattr(app_module.github, "create_issue", fake_create_issue)
    return TestClient(app_module.app), notifications, issues


def code_in(notification):
    """The one-time code inside a notification's Create issue button."""
    _, options = notification
    found = re.search(r"body=([^,]+),", options["actions"])
    assert found, "the notification has no Create issue button"
    return found.group(1)


def confirm(client, code):
    return client.post("/ideas/confirm", content=code)


# --- The tool ---


def test_tool_is_switched_on():
    assert propose_issue in TOOLS


def test_tool_sends_a_preview_with_a_button(world):
    _, notifications, issues = world
    result = propose_issue.invoke({"title": "Train alerts", "description": "Tell me when my train is late."})
    assert result == ideas.DRAFTED
    [(message, options)] = notifications
    assert message == "Train alerts\n\nTell me when my train is late."
    assert options["title"] == "New idea - tap to publish"
    assert options["actions"].startswith(f"http, Create issue, {PUBLIC_URL}/ideas/confirm, method=POST, body=")
    assert issues == []  # drafting never publishes


def test_tool_saves_the_draft_under_the_buttons_code(world):
    _, notifications, _ = world
    propose_issue.invoke({"title": "Train alerts", "description": "Tell me when my train is late."})
    assert ideas.drafts.take(code_in(notifications[0])) == ("Train alerts", "Tell me when my train is late.")


def test_tool_trims_overlong_text(world):
    _, notifications, _ = world
    propose_issue.invoke({"title": "  " + "T" * 500 + "  ", "description": "D" * 9000})
    draft = ideas.drafts.take(code_in(notifications[0]))
    assert draft, "the draft wasn't saved"
    title, description = draft
    assert title == "T" * ideas.MAX_TITLE
    assert description == "D" * ideas.MAX_DESCRIPTION


def test_trailing_slash_in_the_setting_is_fine(world, monkeypatch):
    _, notifications, _ = world
    monkeypatch.setenv("JARVIS_PUBLIC_URL", PUBLIC_URL + "/")
    propose_issue.invoke({"title": "t", "description": "d"})
    assert f"{PUBLIC_URL}/ideas/confirm," in notifications[0][1]["actions"]


# --- The button ---


def test_tapping_creates_the_issue(world):
    client, notifications, issues = world
    code = ideas.drafts.add("Train alerts", "Tell me when my train is late.")
    response = confirm(client, code)
    assert response.status_code == 200
    [(title, body)] = issues
    assert title == "Train alerts"
    assert body == "Tell me when my train is late." + app_module.IDEA_FOOTER
    [(message, options)] = notifications
    assert message == "Created #31: Train alerts"
    assert options["click"] == "https://github.com/someone/some-repo/issues/31"


def test_second_tap_creates_nothing(world):
    client, _, issues = world
    code = ideas.drafts.add("Train alerts", "…")
    confirm(client, code)
    assert confirm(client, code).status_code == 404
    assert len(issues) == 1


def test_made_up_code_is_refused(world):
    client, notifications, issues = world
    ideas.drafts.add("Train alerts", "…")
    assert confirm(client, "a-code-nobody-was-given").status_code == 404
    assert issues == [] and notifications == []


def test_empty_tap_is_refused(world):
    client, _, issues = world
    assert confirm(client, "").status_code == 404
    assert issues == []


def test_oversized_tap_is_refused(world):
    client, _, issues = world
    assert confirm(client, "x" * (app_module.MAX_BODY_BYTES + 1)).status_code == 413
    assert issues == []


def test_github_failing_tells_you(world, monkeypatch):
    client, notifications, _ = world

    def refused(title, body):
        raise github.GitHubError("GitHub replied 403")

    monkeypatch.setattr(app_module.github, "create_issue", refused)
    confirm(client, ideas.drafts.add("Train alerts", "…"))
    assert [message for message, _ in notifications] == ["Couldn't create the issue: Train alerts"]


def test_the_code_never_reaches_the_log(world, caplog):
    client, _, _ = world
    caplog.set_level(logging.DEBUG)
    code = ideas.drafts.add("Train alerts", "…")
    confirm(client, code)
    confirm(client, code)  # the refused second tap logs too
    assert code not in caplog.text
    assert "idea confirmed" in caplog.text
    assert "idea published as issue #31" in caplog.text
    assert "unknown idea code" in caplog.text


# --- The whole journey ---


def test_from_idea_to_issue(world):
    """Claude drafts, you tap the button the phone received, the issue appears."""
    client, notifications, issues = world
    propose_issue.invoke({"title": "Train alerts", "description": "Tell me when my train is late."})
    assert issues == []
    confirm(client, code_in(notifications[0]))
    assert issues == [("Train alerts", "Tell me when my train is late." + app_module.IDEA_FOOTER)]
    assert notifications[-1][0] == "Created #31: Train alerts"
