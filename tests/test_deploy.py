"""Tests for scripts/deploy.py, the automatic deploy that runs on the Pi.

Nothing real runs: git, pip, pytest and sudo are replaced by FakeShell, and
GitHub, /health and ntfy by FakeWeb. Each test says what the Pi and GitHub
would answer, then checks what the deploy did.
"""

import fcntl
import json
import subprocess
import sys

import pytest

from scripts import deploy as deploy_module
from scripts.deploy import LOCK_FILE, Deploy, ci_result, main, read_env

OLD, NEW = "a" * 40, "b" * 40
TITLE = "feat: Add the weather ability"


class FakeShell:
    """Stands in for the commands. Starts on OLD, with NEW waiting on origin/main."""

    def __init__(self, head=OLD, origin=NEW, requirements_changed=False, failing=()):
        self.head, self.origin = head, origin
        self.requirements_changed = requirements_changed
        self.failing = set(failing)  # "pip", "pytest", "restart" or "fetch"
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self._fails(cmd):
            return subprocess.CompletedProcess(cmd, 1, "some output\n", "it broke\n")
        out = ""
        if cmd[:2] == ["git", "rev-parse"]:
            out = self.head if cmd[2] == "HEAD" else self.origin
        elif cmd[:3] == ["git", "reset", "--hard"]:
            self.head = cmd[3]
        elif cmd[:2] == ["git", "log"]:
            out = TITLE
        elif cmd[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, int(self.requirements_changed), "", "")
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    def _fails(self, cmd):
        name = cmd[0].rsplit("/", 1)[-1]  # ".venv/bin/pytest" -> "pytest"
        return (
            name in self.failing
            or ("restart" in cmd and "restart" in self.failing)
            or (cmd[:2] == ["git", "fetch"] and "fetch" in self.failing)
        )

    def ran(self, word):
        """How many commands had this word, such as "restart" or "pip", as a whole argument or program name."""
        return sum(any(part == word or part.endswith("/" + word) for part in cmd) for cmd in self.calls)


class FakeWeb:
    """Stands in for GitHub's API, Jarvis's /health and ntfy."""

    def __init__(self, ci=(("completed", "success"),), health=(True,)):
        # One answer per request; the last one repeats.
        self.ci = list(ci)  # (status, conclusion), or None for "no run yet"
        self.health = list(health)  # True if /health answers ok
        self.sent: list[dict] = []

    def get(self, url):
        if "api.github.com" in url:
            state = self.ci.pop(0) if len(self.ci) > 1 else self.ci[0]
            runs = [] if state is None else [{"name": "CI", "status": state[0], "conclusion": state[1]}]
            return json.dumps({"workflow_runs": runs})
        up = self.health.pop(0) if len(self.health) > 1 else self.health[0]
        if not up:
            raise ConnectionRefusedError("not up")
        return '{"status":"ok"}'

    def post(self, url, body):
        self.sent.append(json.loads(body))

    @property
    def titles(self):
        return [message["title"] for message in self.sent]


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".env").write_text("# settings\nNTFY_TOPIC=test-topic\nOTHER='x'\n")
    return tmp_path


def make(root, shell, web, **changes):
    """A Deploy using the fakes. Waits default to 0, so a test never loops."""
    settings = {"get": web.get, "post": web.post, "health_wait": 0, "ci_wait": 0, **changes}
    return Deploy(root=root, run=shell, sleep=lambda _: None, **settings)


# --- when nothing should happen ----------------------------------------------


def test_up_to_date_does_nothing(root):
    shell, web = FakeShell(origin=OLD), FakeWeb()
    assert make(root, shell, web)() == "Up to date"
    assert shell.ran("reset") == 0
    assert web.sent == []


@pytest.mark.parametrize("ci", [None, ("in_progress", None), ("queued", None)])
def test_gives_up_when_ci_doesnt_finish_in_time(root, ci):
    shell, web = FakeShell(), FakeWeb(ci=[ci])
    assert "trying again at the next run" in make(root, shell, web)()
    assert shell.head == OLD
    assert web.sent == []


def test_waits_for_ci_to_finish(root):
    # Merged a minute ago: no run yet, then running, then passed
    shell, web = FakeShell(), FakeWeb(ci=[None, ("in_progress", None), ("completed", "success")])
    assert make(root, shell, web, ci_wait=900)() == f"Deployed {NEW[:7]}"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_ci_failed_never_deploys_and_tells_once(root, conclusion):
    shell, web = FakeShell(), FakeWeb(ci=[("completed", conclusion)])
    assert "not deploying" in make(root, shell, web)()
    assert shell.head == OLD
    assert web.titles == ["⏭️ Not deploying: CI failed"]

    # The next run skips it without asking GitHub or sending again
    assert "already failed" in make(root, shell, web)()
    assert len(web.sent) == 1


# --- deploying --------------------------------------------------------------


def test_deploys_and_restarts(root):
    shell, web = FakeShell(), FakeWeb()
    assert make(root, shell, web)() == f"Deployed {NEW[:7]}"
    assert shell.head == NEW
    assert shell.ran("pytest") == 1
    assert shell.ran("restart") == 1
    assert shell.ran("pip") == 0  # requirements.txt didn't change
    assert web.sent == [
        {
            "topic": "test-topic",
            "title": "✅ Deployed to the Pi",
            "message": TITLE,
            "tags": ["white_check_mark"],
            "click": f"https://github.com/jcbanos/Jarvis2.0/commit/{NEW}",
        }
    ]


def test_installs_libraries_only_when_requirements_changed(root):
    shell, web = FakeShell(requirements_changed=True), FakeWeb()
    assert make(root, shell, web)() == f"Deployed {NEW[:7]}"
    assert shell.ran("pip") == 1


def test_waits_for_jarvis_to_come_up(root):
    shell, web = FakeShell(), FakeWeb(health=(False, False, True))
    assert make(root, shell, web, health_wait=60)() == f"Deployed {NEW[:7]}"


# --- going back when something fails ----------------------------------------


def test_failing_tests_go_back_without_restarting(root):
    shell, web = FakeShell(failing={"pytest"}), FakeWeb()
    assert "the tests failed" in make(root, shell, web)()
    assert shell.head == OLD
    assert shell.ran("restart") == 0  # the old code was never stopped
    assert web.titles == ["❌ Deploy failed"]
    assert "Back on aaaaaaa." in web.sent[0]["message"]


def test_failing_install_goes_back_and_reinstalls_old_libraries(root):
    shell, web = FakeShell(requirements_changed=True, failing={"pip"}), FakeWeb()
    assert "installing libraries failed" in make(root, shell, web)()
    assert shell.head == OLD
    assert shell.ran("pip") == 2  # the new ones, then the old ones again
    assert shell.ran("pytest") == 0


def test_unhealthy_goes_back_and_restarts_old_code(root):
    shell, web = FakeShell(), FakeWeb(health=(False, True))  # new code down, old code fine
    assert "/health didn't answer" in make(root, shell, web)()
    assert shell.head == OLD
    assert shell.ran("restart") == 2
    assert "still down" not in web.sent[0]["message"]


def test_says_so_when_jarvis_stays_down(root):
    shell, web = FakeShell(failing={"restart"}), FakeWeb(health=(False,))
    assert "the restart failed" in make(root, shell, web)()
    assert shell.head == OLD
    assert "Jarvis is still down after going back!" in web.sent[0]["message"]


def test_a_failed_commit_isnt_retried(root):
    shell, web = FakeShell(failing={"pytest"}), FakeWeb()
    make(root, shell, web)()
    assert "already failed" in make(root, shell, web)()
    assert len(web.sent) == 1


def test_a_newer_commit_is_tried_after_a_failed_one(root):
    shell, web = FakeShell(failing={"pytest"}), FakeWeb()
    make(root, shell, web)()
    shell.failing.clear()
    shell.origin = "c" * 40
    assert make(root, shell, web)() == "Deployed ccccccc"


# --- notifications ----------------------------------------------------------


def test_ntfy_down_doesnt_stop_the_deploy(root):
    def post(url, body):
        raise OSError("no network")

    shell, web = FakeShell(), FakeWeb()
    assert make(root, shell, web, post=post)() == f"Deployed {NEW[:7]}"


def test_no_topic_means_no_message(root):
    (root / ".env").write_text("WEBHOOK_SECRET=x\n")
    shell, web = FakeShell(), FakeWeb()
    assert make(root, shell, web)() == f"Deployed {NEW[:7]}"
    assert web.sent == []


def test_read_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nA=1\nB = "two"\nnot a setting\nC=x=y\n')
    assert read_env(env) == {"A": "1", "B": "two", "C": "x=y"}
    assert read_env(tmp_path / "missing") == {}


def test_ci_result_ignores_other_workflows():
    body = {"workflow_runs": [{"name": "CodeQL", "status": "completed", "conclusion": "failure"}]}
    assert ci_result(NEW, lambda url: json.dumps(body)) == "pending"


# --- main: the lock and errors ----------------------------------------------


def test_main_deploys(root, capsys):
    shell, web = FakeShell(), FakeWeb()
    assert main(make(root, shell, web)) == 0
    assert "Deployed" in capsys.readouterr().out


def test_main_logs_when_github_cant_be_reached(root, capsys):
    shell, web = FakeShell(failing={"fetch"}), FakeWeb()
    assert main(make(root, shell, web)) == 1
    assert "Couldn't check for updates: git fetch failed" in capsys.readouterr().out
    assert web.sent == []  # logged only; the timer tries again tomorrow


def test_main_skips_when_another_deploy_is_running(root, capsys):
    shell, web = FakeShell(), FakeWeb()
    with open(root / LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(make(root, shell, web)) == 0
    assert "Another deploy is running" in capsys.readouterr().out
    assert shell.calls == []


def test_real_commands_run_in_the_project_folder(tmp_path):
    result = Deploy(root=tmp_path).run([sys.executable, "-c", "import os; print(os.getcwd())"])
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_state_file_that_isnt_json_is_ignored(root):
    (root / deploy_module.STATE_FILE).write_text("garbage")
    assert Deploy(root=root).rejected() == ""
