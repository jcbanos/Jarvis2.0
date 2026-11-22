"""Keeps idea drafts until you confirm them, and Undo codes for flashcards.

When Jarvis drafts a GitHub issue from an idea you spoke, nothing is published
yet. The draft waits here under a random one-time code, which travels in the
"Create issue" button of the notification. Tapping it sends the code back, and
only then is the issue created. A draft you never confirm expires.

Same SQLite file and approach as dedupe.py, in a table of its own.
"""

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# A draft you haven't confirmed within a day is treated as a "no".
KEEP_SECONDS = 24 * 60 * 60


def _fingerprint(code: str) -> str:
    """The SHA-256 of a code. Only this is stored, never the code itself.

    So even someone who copied the database file couldn't confirm a draft:
    a fingerprint can't be turned back into its code.
    """
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class _SQLiteStore:
    """What IdeaDrafts and UndoCodes share: the file, and how to connect to it."""

    def __init__(self, path: str | Path):
        self.path = str(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # As in dedupe.py: a fresh connection per call, since each request runs
        # in its own thread, and "with db" saves everything or nothing.
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()


class IdeaDrafts(_SQLiteStore):
    def __init__(self, path: str | Path):
        super().__init__(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS idea_drafts ("
                " fingerprint TEXT PRIMARY KEY,"
                " title TEXT NOT NULL,"
                " body TEXT NOT NULL,"
                " created_at REAL NOT NULL)"
            )

    def add(self, title: str, body: str, now: float | None = None) -> str:
        """Save a draft. Returns its one-time code, for the confirm button."""
        now = time.time() if now is None else now
        code = secrets.token_urlsafe(32)  # 32 random bytes: impossible to guess
        with self._connect() as db:
            db.execute(
                "INSERT INTO idea_drafts (fingerprint, title, body, created_at) VALUES (?, ?, ?, ?)",
                (_fingerprint(code), title, body, now),
            )
            db.execute("DELETE FROM idea_drafts WHERE created_at < ?", (now - KEEP_SECONDS,))
        return code

    def take(self, code: str, now: float | None = None) -> tuple[str, str] | None:
        """Remove the draft for `code` and return (title, body).

        Returns None if the code is unknown, already used or expired. Taking
        removes the draft, so each code works once, even if the button is
        tapped twice.
        """
        now = time.time() if now is None else now
        with self._connect() as db:
            # This first write locks the database until "with" ends. A second
            # tap arriving at the same instant waits here, then finds the
            # draft already gone, so exactly one tap gets it.
            db.execute("DELETE FROM idea_drafts WHERE created_at < ?", (now - KEEP_SECONDS,))
            fingerprint = _fingerprint(code)
            row = db.execute("SELECT title, body FROM idea_drafts WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM idea_drafts WHERE fingerprint = ?", (fingerprint,))
            return row[0], row[1]


class UndoCodes(_SQLiteStore):
    """One-time codes for the Undo button on a "flashcard added" notification.

    Works like IdeaDrafts, the other way round: the card is added at once, and
    the code can remove it. Same SQLite file, in a table of its own, with the
    same 24-hour expiry. Only the code's fingerprint is stored.
    """

    def __init__(self, path: str | Path):
        super().__init__(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS flashcard_undo ("
                " fingerprint TEXT PRIMARY KEY,"
                " note_id INTEGER NOT NULL,"
                " word TEXT NOT NULL,"
                " created_at REAL NOT NULL)"
            )

    def add(self, note_id: int, word: str, now: float | None = None) -> str:
        """Remember an added note. Returns its one-time code, for the Undo button."""
        now = time.time() if now is None else now
        code = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute(
                "INSERT INTO flashcard_undo (fingerprint, note_id, word, created_at) VALUES (?, ?, ?, ?)",
                (_fingerprint(code), note_id, word, now),
            )
            db.execute("DELETE FROM flashcard_undo WHERE created_at < ?", (now - KEEP_SECONDS,))
        return code

    def take(self, code: str, now: float | None = None) -> tuple[int, str] | None:
        """Remove the entry for `code` and return (note_id, word), or None.

        None means unknown, already used or expired. As with IdeaDrafts.take,
        the first write locks the database, so a double tap undoes once.
        """
        now = time.time() if now is None else now
        with self._connect() as db:
            db.execute("DELETE FROM flashcard_undo WHERE created_at < ?", (now - KEEP_SECONDS,))
            fingerprint = _fingerprint(code)
            row = db.execute(
                "SELECT note_id, word FROM flashcard_undo WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM flashcard_undo WHERE fingerprint = ?", (fingerprint,))
            return row[0], row[1]
