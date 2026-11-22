"""Tests for flashcards by voice: the add_flashcard tool and the /flashcards/undo button.

Anki and the phone are faked (tests/test_anki_sync.py tests the Anki side).
The Undo codes are real, in a throwaway file.
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient

import jarvis.app as app_module
from jarvis import anki_sync
from jarvis.drafts import UndoCodes
from jarvis.tools import TOOLS, flashcards
from jarvis.tools.flashcards import add_flashcard

PUBLIC_URL = "https://jarvis.example.ts.net"
GEHEN = {
    "german": "gehen",
    "english": "to go",
    "forms": "geht · ist gegangen",
    "example": "Ich gehe nach Hause.",
    "example_english": "I'm going home.",
}


class FakeAnki:
    """Stands in for jarvis.anki_sync: records cards, and can be told to fail."""

    def __init__(self):
        self.cards: dict[int, tuple[str, ...]] = {}
        self.next_id = 1001
        self.synced = True
        self.problem: anki_sync.AnkiError | None = None

    def add_card(self, *fields):
        if self.problem:
            raise self.problem
        if any(existing[0] == fields[0] for existing in self.cards.values()):
            return anki_sync.Added(note_id=None, synced=True)
        note_id, self.next_id = self.next_id, self.next_id + 1
        self.cards[note_id] = fields
        return anki_sync.Added(note_id=note_id, synced=self.synced)

    def remove_card(self, note_id):
        if self.problem:
            raise self.problem
        return self.cards.pop(note_id, None) is not None


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Fake Anki and phone, real Undo codes. Returns (client, anki, notifications)."""
    monkeypatch.setenv("JARVIS_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setattr(flashcards, "undo_codes", UndoCodes(tmp_path / "undo.sqlite3"))

    anki = FakeAnki()
    monkeypatch.setattr(anki_sync, "add_card", anki.add_card)
    monkeypatch.setattr(anki_sync, "remove_card", anki.remove_card)

    notifications = []
    monkeypatch.setattr(flashcards.notify, "send", lambda message, **options: notifications.append((message, options)))
    return TestClient(app_module.app), anki, notifications


def code_in(notification):
    """The one-time code inside a notification's Undo button."""
    _, options = notification
    found = re.search(r"body=([^,]+),", options["actions"])
    assert found, "the notification has no Undo button"
    return found.group(1)


def undo(client, code):
    return client.post("/flashcards/undo", content=code)


# --- The tool ---


def test_tool_is_switched_on():
    assert add_flashcard in TOOLS


def test_tool_adds_the_card_and_shows_it_with_undo(world):
    _, anki, notifications = world
    result = add_flashcard.invoke(GEHEN)
    assert "Added gehen" in result
    assert list(anki.cards.values()) == [tuple(GEHEN.values())]
    [(message, options)] = notifications
    assert message == "gehen = to go\ngeht · ist gegangen\nIch gehe nach Hause.\nI'm going home."
    assert options["title"] == "Flashcard added"
    assert options["actions"].startswith(f"http, Undo, {PUBLIC_URL}/flashcards/undo, method=POST, body=")


def test_tool_saves_the_note_under_the_buttons_code(world):
    _, _, notifications = world
    add_flashcard.invoke(GEHEN)
    assert flashcards.undo_codes.take(code_in(notifications[0])) == (1001, "gehen")


def test_tool_trims_overlong_text(world):
    _, anki, _ = world
    add_flashcard.invoke({**GEHEN, "german": "  " + "g" * 999 + "  "})
    [fields] = anki.cards.values()
    assert fields[0] == "g" * flashcards.MAX_FIELD


def test_word_already_in_deck_sends_nothing(world):
    _, anki, notifications = world
    add_flashcard.invoke(GEHEN)
    result = add_flashcard.invoke(GEHEN)
    assert "already in the deck" in result
    assert len(anki.cards) == 1 and len(notifications) == 1


def test_not_synced_yet_is_mentioned(world):
    _, anki, notifications = world
    anki.synced = False
    add_flashcard.invoke(GEHEN)
    assert "next sync" in notifications[0][0]


def test_anki_failing_is_told_to_claude(world):
    _, anki, notifications = world
    anki.problem = anki_sync.AnkiError("couldn't reach AnkiWeb")
    result = add_flashcard.invoke(GEHEN)
    assert result == "The card was not added: couldn't reach AnkiWeb"
    assert notifications == []


# --- The Undo button ---


def test_tapping_undo_removes_the_card(world):
    client, anki, notifications = world
    add_flashcard.invoke(GEHEN)
    assert undo(client, code_in(notifications[0])).status_code == 200
    assert anki.cards == {}
    assert notifications[-1][0] == "Removed: gehen"


def test_second_tap_removes_nothing(world):
    client, anki, notifications = world
    add_flashcard.invoke(GEHEN)
    code = code_in(notifications[0])
    undo(client, code)
    assert undo(client, code).status_code == 404


def test_made_up_code_is_refused(world):
    client, anki, notifications = world
    add_flashcard.invoke(GEHEN)
    assert undo(client, "a-code-nobody-was-given").status_code == 404
    assert len(anki.cards) == 1


def test_card_already_deleted_in_anki(world):
    client, anki, notifications = world
    add_flashcard.invoke(GEHEN)
    anki.cards.clear()
    undo(client, code_in(notifications[0]))
    assert notifications[-1][0] == "gehen was already gone from Anki"


def test_anki_failing_on_undo_tells_you(world):
    client, anki, notifications = world
    add_flashcard.invoke(GEHEN)
    anki.problem = anki_sync.AnkiError("couldn't reach AnkiWeb")
    undo(client, code_in(notifications[0]))
    assert notifications[-1][0] == "Couldn't remove gehen: couldn't reach AnkiWeb"


def test_the_code_never_reaches_the_log(world, caplog):
    client, _, notifications = world
    caplog.set_level(logging.DEBUG)
    add_flashcard.invoke(GEHEN)
    code = code_in(notifications[0])
    undo(client, code)
    undo(client, code)  # the refused second tap logs too
    assert code not in caplog.text
    assert "flashcard removed" in caplog.text
    assert "unknown undo code" in caplog.text
