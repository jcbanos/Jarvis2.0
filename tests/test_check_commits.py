"""Tests for scripts/check_commits.py, the commit message check that CI runs."""

import subprocess

import pytest

from scripts.check_commits import check_message, main

GOOD = """\
docs: Exclude git history from the Pi update command

Why: Since the project became a git repository, the README's rsync
command would copy the whole .git folder to the Pi.
What: Add --exclude '.git/' to the update command in the README.
"""


def with_title(title: str) -> str:
    """GOOD with a different title."""
    return GOOD.replace(GOOD.splitlines()[0], title)


@pytest.mark.parametrize(
    "message",
    [
        GOOD,
        # Real messages from this repo's history
        "chore: Add a pull request template for Jarvis\n\n"
        "Why: PRs should cover the same checks each time: testing, safety, deploy, cost\n"
        "What: Add .github/pull_request_template.md, adapted from the scrap-services\n"
        "template without the Truora-specific sections\n",
        "style: Add Ruff and format the code with it\n\n"
        "Why: Code style and common mistakes should be checked by a tool.\n"
        "What: Add ruff.toml and apply its formatting.\n",
        # A scope, and an "ed" verb that is already imperative
        with_title("feat(agent): Embed the weather tool in Jarvis"),
    ],
)
def test_good_messages_pass(message):
    assert check_message(message) == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (with_title("Exclude git history from the Pi update command"), 'look like "type: Subject"'),
        (with_title("Docs: Exclude git history from the Pi update"), 'look like "type: Subject"'),
        (with_title("docs:Exclude git history from the Pi update"), 'look like "type: Subject"'),
        (with_title("docs: exclude git history from the Pi update"), "capital letter"),
        (with_title("docs: Excluded git history from the Pi update"), "imperative"),
        (with_title("docs: Fix typo"), "20-80 characters"),
        (with_title("docs: " + "Exclude " * 10 + "history"), "20-80 characters"),
        (GOOD.replace("Why:", "Because:"), '"Why:"'),
        (GOOD.replace("What:", "Change:"), '"What:"'),
        ("docs: Exclude git history from the Pi update\n\nWhy: and What: in one line\n", "at least 3 lines"),
        (GOOD + "x" * 81 + "\n", "is 81 characters, over 80"),
        (GOOD + "x" * 80 + "\n", None),  # exactly 80 is allowed
        (GOOD + "\nCo-Authored-By: Someone <someone@example.com>\n", "Co-Authored-By"),
        ("", "empty"),
    ],
)
def test_each_rule_catches_its_problem(message, expected):
    problems = check_message(message)
    if expected is None:
        assert problems == []
    else:
        assert any(expected in problem for problem in problems), problems


def test_one_problem_does_not_hide_others():
    message = "Fixed stuff\n\nno sections here\n"
    assert len(check_message(message)) >= 4  # shape, length, Why, What


# --- The whole script, on a small throwaway git repository ---


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo with one commit on main and a branch called "work" checked out."""
    git("init", "-q", "-b", "main", cwd=tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "Initial commit, which isn't checked", cwd=tmp_path)
    git("switch", "-q", "-c", "work", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return tmp_path


def test_script_passes_good_branch(repo, capsys):
    git("commit", "-q", "--allow-empty", "-m", GOOD, cwd=repo)
    assert main(["check_commits.py", "main"]) == 0
    assert "All 1 commits" in capsys.readouterr().out


def test_script_fails_bad_commit_and_repeated_titles(repo, capsys):
    git("commit", "-q", "--allow-empty", "-m", GOOD, cwd=repo)
    git("commit", "-q", "--allow-empty", "-m", GOOD, cwd=repo)
    git("commit", "-q", "--allow-empty", "-m", "Fixed stuff", cwd=repo)
    assert main(["check_commits.py", "main"]) == 1
    output = capsys.readouterr().out
    assert "same title" in output
    assert "Fixed stuff" in output
    assert "2 of 3 commits need fixing" in output
