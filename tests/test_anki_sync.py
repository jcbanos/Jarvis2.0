"""Tests for jarvis/anki_sync.py, with a real Anki collection and a fake AnkiWeb.

The collection is a throwaway file, so these never touch your real cards.
AnkiWeb is faked by replacing Collection.sync_collection, and a full upload
fails the test outright: it must never happen.
"""

import logging

import pytest
from anki.collection import Collection
from anki.errors import NetworkError, SyncError, SyncErrorKind
from anki.sync import SyncOutput

from jarvis import anki_sync

SYNC_KEY = "fake-anki-sync-key-for-tests"
GEHEN = ("gehen", "to go", "geht · ist gegangen", "Ich gehe nach Hause.", "I'm going home.")


@pytest.fixture
def ankiweb(monkeypatch, tmp_path):
    """A throwaway collection and a fake AnkiWeb. Returns the list of sync results to hand out.

    Each sync takes the next result from the list, or "no changes" when it's empty.
    Every sync is recorded in .syncs.
    """
    path = tmp_path / "anki" / "collection.anki2"
    monkeypatch.setenv("ANKI_COLLECTION", str(path))
    monkeypatch.setenv("ANKI_SYNC_KEY", SYNC_KEY)
    monkeypatch.delenv("ANKI_DECK", raising=False)
    monkeypatch.delenv("ANKI_SYNC_ENDPOINT", raising=False)

    class FakeAnkiWeb(list):
        syncs: list[str] = []

    replies = FakeAnkiWeb()
    replies.syncs = []

    def fake_sync(col, auth, sync_media):
        assert sync_media is False
        replies.syncs.append(auth.hkey)
        result = replies.pop(0) if replies else SyncOutput(required=SyncOutput.NO_CHANGES)
        if isinstance(result, Exception):
            raise result
        return result

    def never(*args, **kwargs):
        pytest.fail("a full upload or download was attempted")

    monkeypatch.setattr(Collection, "sync_collection", fake_sync)
    monkeypatch.setattr(Collection, "full_upload_or_download", never)
    replies.path = path  # type: ignore[attr-defined]
    return replies


def open_collection(ankiweb):
    return Collection(str(ankiweb.path))


def test_adding_makes_two_cards_in_the_deck(ankiweb):
    added = anki_sync.add_card(*GEHEN)
    assert added.note_id is not None and added.synced
    col = open_collection(ankiweb)
    try:
        note = col.get_note(added.note_id)
        assert note["German"] == "gehen"
        assert note["Forms"] == "geht · ist gegangen"
        assert note.note_type()["name"] == anki_sync.NOTETYPE  # type: ignore[index]
        cards = note.cards()
        assert len(cards) == 2  # German to English, and English to German
        assert {col.decks.name(card.did) for card in cards} == {"German-Jarvis"}
    finally:
        col.close()


def test_syncs_before_and_after_adding(ankiweb):
    anki_sync.add_card(*GEHEN)
    assert ankiweb.syncs == [SYNC_KEY, SYNC_KEY]


def test_note_type_and_deck_are_created_once(ankiweb):
    anki_sync.add_card(*GEHEN)
    anki_sync.add_card("die Zuverlässigkeit", "reliability", "die Zuverlässigkeiten", "", "")
    col = open_collection(ankiweb)
    try:
        assert [m["name"] for m in col.models.all()].count(anki_sync.NOTETYPE) == 1
        assert [d.name for d in col.decks.all_names_and_ids()].count("German-Jarvis") == 1
        assert col.note_count() == 2
    finally:
        col.close()


def test_deck_name_comes_from_the_setting(ankiweb, monkeypatch):
    monkeypatch.setenv("ANKI_DECK", "Deutsch")
    added = anki_sync.add_card(*GEHEN)
    col = open_collection(ankiweb)
    try:
        assert col.decks.name(col.get_note(added.note_id).cards()[0].did) == "Deutsch"  # type: ignore[arg-type]
    finally:
        col.close()


def test_the_same_word_isnt_added_twice(ankiweb):
    anki_sync.add_card(*GEHEN)
    again = anki_sync.add_card("Gehen", "to walk", "", "", "")  # different case, same word
    assert again.note_id is None
    col = open_collection(ankiweb)
    try:
        assert col.note_count() == 1
    finally:
        col.close()


def test_words_with_search_characters_are_fine(ankiweb):
    anki_sync.add_card('die "Kita" (Kindergarten)*', "nursery", "", "", "")
    assert anki_sync.add_card('die "Kita" (Kindergarten)*', "nursery", "", "", "").note_id is None


def test_text_is_shown_as_text_not_html(ankiweb):
    added = anki_sync.add_card("<script>alert(1)</script>", "x", "", "", "")
    col = open_collection(ankiweb)
    try:
        assert col.get_note(added.note_id)["German"] == "&lt;script&gt;alert(1)&lt;/script&gt;"  # type: ignore[arg-type]
    finally:
        col.close()


def test_removing_deletes_the_note_and_both_cards(ankiweb):
    added = anki_sync.add_card(*GEHEN)
    assert anki_sync.remove_card(added.note_id) is True  # type: ignore[arg-type]
    col = open_collection(ankiweb)
    try:
        assert col.note_count() == 0 and col.card_count() == 0
    finally:
        col.close()


def test_removing_a_note_thats_gone_says_so(ankiweb):
    added = anki_sync.add_card(*GEHEN)
    anki_sync.remove_card(added.note_id)  # type: ignore[arg-type]
    assert anki_sync.remove_card(added.note_id) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("required", [SyncOutput.FULL_SYNC, SyncOutput.FULL_UPLOAD, SyncOutput.FULL_DOWNLOAD])
def test_full_sync_needed_changes_nothing(ankiweb, required):
    ankiweb.append(SyncOutput(required=required))
    with pytest.raises(anki_sync.AnkiError, match="full sync"):
        anki_sync.add_card(*GEHEN)
    col = open_collection(ankiweb)
    try:
        assert col.note_count() == 0
    finally:
        col.close()


def test_wrong_key_says_to_log_in_again(ankiweb):
    ankiweb.append(SyncError("bad key", None, None, None, SyncErrorKind.AUTH))
    with pytest.raises(anki_sync.AnkiError, match="anki_login.py"):
        anki_sync.add_card(*GEHEN)


def test_no_network_adds_nothing(ankiweb):
    ankiweb.append(NetworkError("down", None, None, None))
    with pytest.raises(anki_sync.AnkiError, match="couldn't reach AnkiWeb"):
        anki_sync.add_card(*GEHEN)


def test_sync_failing_after_adding_keeps_the_card(ankiweb):
    ankiweb.extend([SyncOutput(required=SyncOutput.NO_CHANGES), NetworkError("down", None, None, None)])
    added = anki_sync.add_card(*GEHEN)
    assert added.note_id is not None and added.synced is False


def test_without_a_key_it_works_locally(ankiweb, monkeypatch):
    monkeypatch.setenv("ANKI_SYNC_KEY", "")
    added = anki_sync.add_card(*GEHEN)
    assert added.note_id is not None and added.synced is False
    assert ankiweb.syncs == []


def test_changed_note_type_is_refused(ankiweb):
    anki_sync.add_card(*GEHEN)
    col = open_collection(ankiweb)
    try:
        notetype = col.models.by_name(anki_sync.NOTETYPE)
        col.models.rename_field(notetype, notetype["flds"][1], "Meaning")  # type: ignore[index]
        col.models.save(notetype)
    finally:
        col.close()
    with pytest.raises(anki_sync.AnkiError, match="fields were changed"):
        anki_sync.add_card("die Katze", "cat", "die Katzen", "", "")


def test_new_server_is_used_and_logged(ankiweb, caplog):
    caplog.set_level(logging.INFO)
    ankiweb.append(SyncOutput(required=SyncOutput.NO_CHANGES, new_endpoint="https://sync9.example/"))
    anki_sync.add_card(*GEHEN)
    assert "ANKI_SYNC_ENDPOINT" in caplog.text


def test_the_sync_key_never_reaches_the_log(ankiweb, caplog):
    caplog.set_level(logging.DEBUG)
    ankiweb.extend([SyncOutput(required=SyncOutput.NO_CHANGES), NetworkError(SYNC_KEY, None, None, None)])
    anki_sync.add_card(*GEHEN)
    ankiweb.append(SyncError(SYNC_KEY, None, None, None, SyncErrorKind.AUTH))
    with pytest.raises(anki_sync.AnkiError) as raised:
        anki_sync.add_card("die Katze", "cat", "", "", "")
    assert SYNC_KEY not in caplog.text
    assert SYNC_KEY not in str(raised.value)
