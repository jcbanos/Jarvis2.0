"""Tests for the delivery log. Each test gets its own throwaway database file."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis import dedupe
from jarvis.dedupe import KEEP_SECONDS, DeliveryLog


def test_new_delivery_is_first_time(tmp_path):
    log = DeliveryLog(tmp_path / "d.sqlite3")
    assert log.first_time("abc") is True


def test_repeat_is_not_first_time(tmp_path):
    log = DeliveryLog(tmp_path / "d.sqlite3")
    log.first_time("abc")
    assert log.first_time("abc") is False


def test_different_deliveries_are_independent(tmp_path):
    log = DeliveryLog(tmp_path / "d.sqlite3")
    assert log.first_time("abc") is True
    assert log.first_time("xyz") is True


def test_record_survives_a_restart(tmp_path):
    path = tmp_path / "d.sqlite3"
    DeliveryLog(path).first_time("abc")
    # A new DeliveryLog on the same file is like Jarvis after a reboot.
    assert DeliveryLog(path).first_time("abc") is False


def test_simultaneous_copies_get_through_exactly_once(tmp_path):
    log = DeliveryLog(tmp_path / "d.sqlite3")
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: log.first_time("same-id"), range(10)))
    assert results.count(True) == 1


def test_old_entries_are_forgotten(tmp_path):
    log = DeliveryLog(tmp_path / "d.sqlite3")
    log.first_time("old", now=1_000)
    # Any later call clears entries older than KEEP_SECONDS.
    log.first_time("new", now=1_000 + KEEP_SECONDS + 1)
    assert log.first_time("old", now=1_000 + KEEP_SECONDS + 2) is True


def test_every_connection_is_closed(tmp_path, monkeypatch):
    # "with sqlite3.connect(...)" saves changes but doesn't close the connection,
    # so each request would leave one open. Record every connection and check.
    opened = []
    real_connect = sqlite3.connect

    def recording_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(dedupe.sqlite3, "connect", recording_connect)
    log = DeliveryLog(tmp_path / "d.sqlite3")
    log.first_time("abc")
    log.first_time("abc")

    assert len(opened) == 3  # setup, then one per call
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_a_failed_call_saves_nothing_and_still_closes(tmp_path, monkeypatch):
    log = DeliveryLog(tmp_path / "d.sqlite3")

    class FailsOnDelete(sqlite3.Connection):
        """A connection where the cleanup step fails, after the INSERT has run."""

        def execute(self, sql, *args):
            if sql.startswith("DELETE"):
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, *args)

    opened = []
    real_connect = sqlite3.connect

    def failing_connect(*args, **kwargs):
        opened.append(real_connect(*args, factory=FailsOnDelete, **kwargs))
        return opened[-1]

    monkeypatch.setattr(dedupe.sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError):
        log.first_time("abc")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")

    monkeypatch.undo()
    assert log.first_time("abc") is True  # the half-done INSERT was undone
