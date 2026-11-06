"""Check that commit messages follow templates/git/commit.txt.

Checks every commit on the current branch that isn't on the base branch
(origin/main unless you name another). Merge commits are skipped, since
GitHub writes those. CI runs this on every push. Run it on your Mac before
pushing:

    .venv/bin/python scripts/check_commits.py          # compares with origin/main
    .venv/bin/python scripts/check_commits.py main     # or name another base

Exits with 1 and lists the problems if any commit breaks a rule.
"""

import os
import re
import subprocess
import sys

MAX_LINE = 80
TITLE_MIN, TITLE_MAX = 20, 80

# "type: Subject" or "type(scope): Subject", with a lowercase type such as
# feat, fix, docs, chore, style or test.
TITLE_SHAPE = re.compile(r"^[a-z]+(\([a-z0-9-]+\))?: (?P<subject>.+)$")

# Verbs that end in "ed" but are already in imperative form.
IMPERATIVE_ED = {"embed", "exceed", "feed", "need", "proceed", "seed", "shed", "speed", "succeed"}


def check_message(message: str) -> list[str]:
    """Return what's wrong with one commit message. An empty list means it's fine."""
    lines = [line for line in message.strip().splitlines() if line.strip()]
    if not lines:
        return ["message is empty"]

    problems = []
    title = lines[0]

    match = TITLE_SHAPE.match(title)
    if not match:
        problems.append('title should look like "type: Subject" or "type(scope): Subject", with a lowercase type')
    else:
        subject = match["subject"]
        if not subject[0].isupper():
            problems.append("subject after the type should start with a capital letter")
        first_word = subject.split()[0].lower()
        if first_word.endswith("ed") and first_word not in IMPERATIVE_ED:
            problems.append(f'use the imperative, like "Fix" not "Fixed" (found "{subject.split()[0]}")')

    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        problems.append(f"title should be {TITLE_MIN}-{TITLE_MAX} characters (it's {len(title)})")

    if not any(line.startswith("Why:") for line in lines[1:]):
        problems.append('missing a "Why:" line explaining why it changed')
    if not any(line.startswith("What:") for line in lines[1:]):
        problems.append('missing a "What:" line explaining what changed')

    if len(lines) < 3:
        problems.append("needs at least 3 lines: title, Why and What")

    for number, line in enumerate(message.strip().splitlines(), start=1):
        if len(line) > MAX_LINE:
            problems.append(f"line {number} is {len(line)} characters, over {MAX_LINE}. Wrap it onto the next line")

    if any(line.lower().startswith("co-authored-by:") for line in lines):
        problems.append("remove the Co-Authored-By line")

    return problems


def branch_commits(base: str) -> list[tuple[str, str]]:
    """(sha, message) for each non-merge commit on HEAD that isn't on `base`, oldest first."""
    # %x00 and %x1e are separator characters that can't appear in a message.
    log = subprocess.run(
        ["git", "log", "--no-merges", "--reverse", "--format=%H%x00%B%x1e", f"{base}..HEAD"],
        capture_output=True,
        text=True,
    )
    if log.returncode != 0:
        sys.exit(f"Couldn't read commits since {base}: {log.stderr.strip()}\nTry `git fetch origin` first.")
    commits = []
    for record in log.stdout.split("\x1e"):
        if record.strip():
            sha, _, message = record.strip().partition("\x00")
            commits.append((sha, message))
    return commits


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "origin/main"
    commits = branch_commits(base)
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"

    failed = 0
    seen_titles = set()
    for sha, message in commits:
        title = message.strip().splitlines()[0] if message.strip() else ""
        problems = check_message(message)
        if title in seen_titles:
            problems.append("another commit on this branch has the same title")
        seen_titles.add(title)

        if problems:
            failed += 1
            print(f"✗ {sha[:7]} {title}")
            for problem in problems:
                print(f"    - {problem}")
                if in_ci:  # shows the problem on the run's summary page
                    print(f"::error title=Commit {sha[:7]}::{problem}")

    if failed:
        print(f"\n{failed} of {len(commits)} commits need fixing. See templates/git/commit.txt.")
        print("To reword commits, run `git rebase -i` and mark them as 'reword', then push with --force-with-lease.")
        return 1
    print(f"✓ All {len(commits)} commits since {base} follow templates/git/commit.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
