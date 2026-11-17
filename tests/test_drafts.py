"""Tests for the idea drafts waiting for your confirmation."""

import threading

import pytest

from jarvis.drafts import KEEP_SECONDS, IdeaDrafts


@pytest.fixture
def drafts(tmp_path):
    """A draft store in a throwaway file that pytest deletes afterwards."""
    return IdeaDrafts(tmp_path / "drafts.sqlite3")


def test_take_returns_the_draft(drafts):
    code = drafts.add("Train alerts", "Tell me when my train is late.")
    assert drafts.take(code) == ("Train alerts", "Tell me when my train is late.")


def test_each_code_works_once(drafts):
    code = drafts.add("Train alerts", "…")
    drafts.take(code)
    assert drafts.take(code) is None


def test_unknown_code_finds_nothing(drafts):
    drafts.add("Train alerts", "…")
    assert drafts.take("a-code-nobody-was-given") is None


def test_expired_draft_is_gone(drafts):
    code = drafts.add("Train alerts", "…", now=1000)
    assert drafts.take(code, now=1000 + KEEP_SECONDS + 1) is None


def test_draft_just_inside_the_limit_still_works(drafts):
    code = drafts.add("Train alerts", "…", now=1000)
    assert drafts.take(code, now=1000 + KEEP_SECONDS - 1) is not None


def test_drafts_are_separate(drafts):
    first = drafts.add("First idea", "one")
    second = drafts.add("Second idea", "two")
    assert first != second
    assert drafts.take(second) == ("Second idea", "two")
    assert drafts.take(first) == ("First idea", "one")


def test_accents_survive(drafts):
    code = drafts.add("Recordatorio de cumpleaños", "Avísame el día antes, por favor.")
    assert drafts.take(code) == ("Recordatorio de cumpleaños", "Avísame el día antes, por favor.")


def test_code_is_never_stored(tmp_path):
    """The file holds only a fingerprint, so a copy of it can't confirm a draft."""
    path = tmp_path / "drafts.sqlite3"
    code = IdeaDrafts(path).add("Train alerts", "…")
    assert code.encode() not in path.read_bytes()


def test_survives_a_restart(tmp_path):
    """Drafts live in the file, so restarting Jarvis doesn't lose them."""
    path = tmp_path / "drafts.sqlite3"
    code = IdeaDrafts(path).add("Train alerts", "…")
    assert IdeaDrafts(path).take(code) == ("Train alerts", "…")


def test_simultaneous_taps_get_it_once(drafts):
    """Tapping the button twice quickly must never create two issues."""
    code = drafts.add("Train alerts", "…")
    results = []
    start = threading.Barrier(10)

    def tap():
        start.wait()  # all 10 threads take at the same instant
        results.append(drafts.take(code))

    threads = [threading.Thread(target=tap) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(("Train alerts", "…")) == 1
    assert results.count(None) == 9
