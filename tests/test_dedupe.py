"""Tests for the delivery log. Each test gets its own throwaway database file."""

from concurrent.futures import ThreadPoolExecutor

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
