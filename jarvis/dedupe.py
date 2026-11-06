"""Remembers which recordings have been handled, so repeats are ignored.

The Pebble app may deliver the same recording more than once: it retries when it
doesn't get a reply, and can resend after it restarts. Each recording keeps the
same delivery ID across repeats, so we record IDs here.

SQLite keeps the record in a single file, so it survives Jarvis restarts and Pi
reboots. It's built into Python, so there's nothing extra to install.
"""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Pebble never resends anything this old, so older IDs can be forgotten.
KEEP_SECONDS = 30 * 24 * 60 * 60


class DeliveryLog:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS deliveries ("
                " id TEXT PRIMARY KEY,"  # PRIMARY KEY: SQLite forbids duplicates
                " received_at REAL NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # A fresh connection per call keeps things simple and safe when several
        # requests arrive at once. Each runs in its own thread, and SQLite
        # connections shouldn't be shared between threads.
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:  # saves the changes if everything worked, undoes them if not
                yield db
        finally:
            db.close()  # "with db" alone doesn't close the connection

    def first_time(self, delivery_id: str, now: float | None = None) -> bool:
        """Record `delivery_id`. True if it's new, False if already handled.

        "INSERT OR IGNORE" checks and records in one indivisible step. If two
        copies arrive at the same instant, exactly one insert succeeds, so
        exactly one gets True.
        """
        now = time.time() if now is None else now
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO deliveries (id, received_at) VALUES (?, ?)",
                (delivery_id, now),  # "?" placeholders keep the ID from being read as SQL
            )
            db.execute("DELETE FROM deliveries WHERE received_at < ?", (now - KEEP_SECONDS,))
            return cursor.rowcount == 1  # 1 row inserted means new, 0 means a repeat
