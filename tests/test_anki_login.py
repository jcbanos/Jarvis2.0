"""Tests for scripts/anki_login.py, with AnkiWeb faked.

The most important check: the script only ever downloads (upload=False).
"""

import sys

import pytest
from anki.collection import Collection
from anki.errors import NetworkError, SyncError, SyncErrorKind
from anki.sync import SyncAuth

from scripts import anki_login


@pytest.fixture
def ankiweb(monkeypatch, tmp_path):
    """A throwaway collection path and a fake AnkiWeb. Returns what it was asked to do."""
    monkeypatch.setenv("ANKI_COLLECTION", str(tmp_path / "anki" / "collection.anki2"))
    monkeypatch.delenv("ANKI_SYNC_KEY", raising=False)
    monkeypatch.delenv("ANKI_SYNC_ENDPOINT", raising=False)
    monkeypatch.setattr(anki_login, "load_dotenv", lambda: None)  # never read the real .env
    calls: dict = {"logins": [], "transfers": [], "fail": None}

    def fake_login(col, username, password, endpoint):
        calls["logins"].append((username, password))
        if calls["fail"]:
            raise calls["fail"]
        return SyncAuth(hkey="fresh-key-from-ankiweb", endpoint="https://sync9.example/")

    def fake_transfer(col, *, auth, server_usn, upload):
        if calls["fail"]:
            raise calls["fail"]
        calls["transfers"].append((auth.hkey, upload))

    monkeypatch.setattr(Collection, "sync_login", fake_login)
    monkeypatch.setattr(Collection, "full_upload_or_download", fake_transfer)
    return calls


def run(monkeypatch, *args, answers=("yes", "me@example.com"), password="secret"):
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    monkeypatch.setattr(anki_login.getpass, "getpass", lambda prompt="": password)
    monkeypatch.setattr(sys, "argv", ["anki_login.py", *args])
    anki_login.main()


def test_login_downloads_and_prints_the_key(ankiweb, monkeypatch, capsys):
    run(monkeypatch)
    assert ankiweb["logins"] == [("me@example.com", "secret")]
    assert ankiweb["transfers"] == [("fresh-key-from-ankiweb", False)]  # a download, never an upload
    out = capsys.readouterr().out
    assert "ANKI_SYNC_KEY=fresh-key-from-ankiweb" in out
    assert "ANKI_SYNC_ENDPOINT=https://sync9.example/" in out
    assert "secret" not in out


def test_not_typing_yes_changes_nothing(ankiweb, monkeypatch):
    with pytest.raises(SystemExit, match="Nothing changed"):
        run(monkeypatch, answers=("no",))
    assert ankiweb["logins"] == [] and ankiweb["transfers"] == []


def test_download_again_uses_the_key_from_env(ankiweb, monkeypatch, capsys):
    monkeypatch.setenv("ANKI_SYNC_KEY", "key-in-env")
    run(monkeypatch, "--download", answers=("yes",))
    assert ankiweb["logins"] == []
    assert ankiweb["transfers"] == [("key-in-env", False)]
    assert "ANKI_SYNC_KEY=" not in capsys.readouterr().out


def test_download_again_needs_a_key(ankiweb, monkeypatch):
    with pytest.raises(SystemExit, match="ANKI_SYNC_KEY is empty"):
        run(monkeypatch, "--download", answers=("yes",))


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        (SyncError("x", None, None, None, SyncErrorKind.AUTH), "rejected the login"),
        (SyncError("x", None, None, None, SyncErrorKind.OTHER), "sync failed"),
        (NetworkError("x", None, None, None), "Couldn't reach AnkiWeb"),
    ],
)
def test_failures_explain_themselves(ankiweb, monkeypatch, problem, message):
    ankiweb["fail"] = problem
    with pytest.raises(SystemExit, match=message):
        run(monkeypatch)
