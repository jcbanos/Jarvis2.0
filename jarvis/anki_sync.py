"""Jarvis's link to Anki: adds flashcards to your German-Jarvis deck, and removes them.

Everything that uses the `anki` package lives here, so the rest of Jarvis only
calls add_card() and remove_card(), and tests can fake them.

How a card reaches your Mac and phone: the Pi keeps its own copy of your Anki
collection, in anki/collection.anki2, and syncs it with AnkiWeb exactly as
Anki on the Mac and AnkiDroid do. Each change is synced straight away, and
your other devices pick it up the next time they sync.

The one rule: NEVER a full upload. A full upload replaces everything on
AnkiWeb, review history included, with the Pi's copy. This file never asks for
one. If AnkiWeb says a full sync is needed, it stops and changes nothing.
Only scripts/anki_login.py does a full sync, and only a download, which
replaces the Pi's copy and nothing else.

Try it from the project folder (with ANKI_SYNC_KEY blank it only changes the
local copy, and doesn't sync):
    .venv/bin/python -m jarvis.anki_sync gehen "to go" "geht · ist gegangen"
"""

import html
import logging
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from anki.collection import Collection, SearchNode
from anki.errors import NetworkError, NotFoundError, SyncError, SyncErrorKind
from anki.models import NotetypeDict
from anki.notes import NoteId
from anki.sync import SyncAuth
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("jarvis.anki")

DEFAULT_COLLECTION = "anki/collection.anki2"
DEFAULT_DECK = "German-Jarvis"

# The note type Jarvis creates the first time. Each note makes two cards, one
# for each direction. The examples are only on the back, so they can't give
# the answer away. {{#Forms}}…{{/Forms}} shows that part only when it's filled in.
NOTETYPE = "Jarvis German"
FIELDS = ("German", "English", "Forms", "Example", "Example (English)")
_DETAILS = "{{#Forms}}<br><br>{{Forms}}{{/Forms}}<br><br><i>{{Example}}</i><br>{{Example (English)}}"
TEMPLATES = (
    ("German to English", "{{German}}", "{{FrontSide}}<hr id=answer>{{English}}" + _DETAILS),
    ("English to German", "{{English}}", "{{FrontSide}}<hr id=answer>{{German}}" + _DETAILS),
)

# Only one recording at a time may open the collection. Each request runs in
# its own thread, and Anki's database isn't built to be opened twice at once.
_lock = threading.Lock()


class AnkiError(Exception):
    """Something went wrong. The message is safe to log and to show: it never contains the sync key."""


@dataclass(frozen=True)
class Added:
    """What add_card() did.

    note_id: the new note, or None if the word was already in the deck.
    synced:  False if the card was saved on the Pi but AnkiWeb couldn't be
             reached afterwards. It's sent with the next successful sync.
    """

    note_id: int | None
    synced: bool


def add_card(german: str, english: str, forms: str, example: str, example_english: str) -> Added:
    """Add a two-way card for a German word to the deck, then sync.

    Raises AnkiError if it changed nothing: the sync before adding failed, or
    AnkiWeb wants a full sync.
    """
    values = [html.escape(text.strip()) for text in (german, english, forms, example, example_english)]
    with _open() as (col, auth):
        _sync(col, auth)  # first get changes from your other devices, such as a deleted card
        notetype = _notetype(col)
        deck = _deck_name()
        deck_id = col.decks.id(deck)
        assert deck_id is not None  # id() creates the deck when it's missing

        if _already_in_deck(col, deck, values[0]):
            return Added(note_id=None, synced=True)

        note = col.new_note(notetype)
        for field, value in zip(FIELDS, values, strict=True):
            note[field] = value
        col.add_note(note, deck_id)
        return Added(note_id=note.id, synced=_sync_after_change(col, auth))


def remove_card(note_id: int) -> bool:
    """Delete a note Jarvis added, with both its cards, then sync.

    Returns False if the note was already gone, for example deleted in Anki.
    Raises AnkiError if it changed nothing.
    """
    with _open() as (col, auth):
        _sync(col, auth)
        try:
            col.get_note(NoteId(note_id))
        except NotFoundError:
            return False
        col.remove_notes([NoteId(note_id)])
        _sync_after_change(col, auth)
        return True


@contextmanager
def _open() -> Iterator[tuple[Collection, SyncAuth | None]]:
    """Open the Pi's copy of the collection, and always close it afterwards."""
    with _lock:
        path = Path(os.environ.get("ANKI_COLLECTION") or DEFAULT_COLLECTION)
        path.parent.mkdir(parents=True, exist_ok=True)
        col = Collection(str(path))
        try:
            yield col, _auth()
        finally:
            col.close()


def _auth() -> SyncAuth | None:
    """The AnkiWeb login from .env, or None to work only on the local copy."""
    key = os.environ.get("ANKI_SYNC_KEY", "").strip()
    if not key:
        return None
    return SyncAuth(hkey=key, endpoint=os.environ.get("ANKI_SYNC_ENDPOINT") or None)


def _sync(col: Collection, auth: SyncAuth | None) -> None:
    """A normal sync with AnkiWeb. Raises AnkiError rather than ever doing a full one."""
    if auth is None:
        return
    try:
        out = col.sync_collection(auth, sync_media=False)
    # Anki's own messages could, in principle, include request details, so
    # replace them with a short description. `from None` keeps them out of
    # tracebacks, and so out of the log.
    except SyncError as error:
        if error.kind is SyncErrorKind.AUTH:
            raise AnkiError("AnkiWeb rejected the sync key. Run scripts/anki_login.py again") from None
        raise AnkiError(f"AnkiWeb sync failed ({error.kind.name.lower()})") from None
    except NetworkError:
        raise AnkiError("couldn't reach AnkiWeb") from None

    if out.server_message:
        log.warning("AnkiWeb says: %s", out.server_message)
    if out.HasField("new_endpoint"):
        # AnkiWeb moved the account to another of its servers. Use it now, and
        # say so, since .env should be updated for next time.
        log.warning("AnkiWeb asked to use %s: set ANKI_SYNC_ENDPOINT in .env", out.new_endpoint)
        auth.endpoint = out.new_endpoint
    # sync_collection only ever does a normal sync. Any other answer means it
    # stopped without syncing, because only a full sync would work. Which full
    # sync is right needs a person to decide, so stop here.
    if out.required not in (out.NO_CHANGES, out.NORMAL_SYNC):
        raise AnkiError("AnkiWeb wants a full sync. Run scripts/anki_login.py --download on the Pi")


def _sync_after_change(col: Collection, auth: SyncAuth | None) -> bool:
    """Sync once the change is saved on the Pi. Returns False if that failed.

    The change is already saved by then, so a failure doesn't lose it: it
    goes to AnkiWeb with the next successful sync.
    """
    try:
        _sync(col, auth)
    except AnkiError as problem:
        log.warning("saved on the Pi but not synced: %s", problem)
        return False
    return auth is not None


def _notetype(col: Collection) -> NotetypeDict:
    """The Jarvis German note type, created the first time it's needed."""
    existing = col.models.by_name(NOTETYPE)
    if existing is not None:
        if [field["name"] for field in existing["flds"]] != list(FIELDS):
            raise AnkiError(f"the {NOTETYPE} note type's fields were changed in Anki. Put them back as {FIELDS}")
        return existing

    log.info("creating the %s note type", NOTETYPE)
    notetype = col.models.new(NOTETYPE)
    for name in FIELDS:
        col.models.add_field(notetype, col.models.new_field(name))
    for name, front, back in TEMPLATES:
        template = col.models.new_template(name)
        template["qfmt"] = front
        template["afmt"] = back
        col.models.add_template(notetype, template)
    col.models.add(notetype)
    created = col.models.by_name(NOTETYPE)
    assert created is not None
    return created


def _deck_name() -> str:
    return os.environ.get("ANKI_DECK") or DEFAULT_DECK


def _already_in_deck(col: Collection, deck: str, german: str) -> bool:
    """True if a Jarvis note in the deck already has this German word, ignoring case.

    The comparison is done here in Python rather than in Anki's search,
    because words can contain characters that mean something in its search.
    """
    search = col.build_search_string(SearchNode(deck=deck), SearchNode(note=NOTETYPE))
    return any(col.get_note(note_id)["German"].casefold() == german.casefold() for note_id in col.find_notes(search))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    word, meaning, forms = (sys.argv[1:] + ["", "", ""])[:3]
    print(add_card(word or "gehen", meaning or "to go", forms, "", ""))
