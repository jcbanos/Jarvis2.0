"""Deploy the latest main to the Pi, but only if CI passed on it.

The jarvis-deploy timer runs this once a day at 13:00 on the Pi
(deploy/jarvis-deploy.timer). Each run:

1. Fetches main from GitHub. If there's nothing new, it stops.
2. Asks GitHub whether CI passed on the new commit. If CI is still running,
   it waits up to 15 minutes for it. If CI failed, it never deploys it.
3. Updates the code with git, installs libraries if requirements.txt changed,
   and runs the tests on the Pi.
4. Restarts Jarvis and checks /health.
5. If anything fails, it goes back to the previous commit, restarts if needed,
   and sends ❌ to your phone. On success it sends ✅.

Only the standard library is used, so the Pi's system Python can run it even
if .venv is broken. To run it by hand on the Pi:

    sudo systemctl start jarvis-deploy      # deploy now, without waiting for 13:00
    journalctl -u jarvis-deploy -n 50       # what it did

The repo is public, so this only reads from GitHub and needs no token. It
never prints .env values: the deploy log is private, but keep it clean anyway.
"""

import fcntl
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = "jcbanos/Jarvis2.0"
CI_WORKFLOW = "CI"  # the "name:" in .github/workflows/ci.yml
ROOT = Path(__file__).resolve().parent.parent
HEALTH_URL = "http://127.0.0.1:8000/health"
HEALTH_WAIT_SECONDS = 60  # Jarvis takes a few seconds to load LangChain
CI_WAIT_SECONDS = 15 * 60  # CI takes about a minute; this leaves room for a slow GitHub
STATE_FILE = ".deploy-state"  # remembers a commit that failed, so it isn't retried
LOCK_FILE = ".deploy.lock"

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Getter = Callable[[str], str]  # returns the response body; raises OSError on failure
Poster = Callable[[str, bytes], None]  # sends a JSON body; raises OSError on failure


class DeployError(Exception):
    """A command that must succeed, such as git fetch, failed."""


# The errors to catch, named here rather than written as "except (A, B):".
# Ruff, set for Python 3.14, rewrites that to "except A, B:", which the Pi's
# system Python (3.13) can't read. CI checks this file parses on older Python.
READ_ERRORS = (OSError, ValueError)  # a missing or garbled file or answer
CHECK_ERRORS = (DeployError, OSError, ValueError, KeyError)  # no network, or GitHub answered oddly


def log(message: str) -> None:
    # flush, so lines reach the journal at once, not when the script ends
    print(message, flush=True)


def http_get(url: str) -> str:
    # GitHub's API refuses requests without a User-Agent
    request = urllib.request.Request(url, headers={"User-Agent": "jarvis-deploy"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode()


def http_post(url: str, body: bytes) -> None:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15):
        pass


def read_env(path: Path) -> dict[str, str]:
    """The KEY=value lines of a .env file. Comments and blank lines are skipped."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def ci_result(sha: str, get: Getter) -> str:
    """Whether CI "passed", "failed" or is "pending" on this commit of main."""
    url = f"https://api.github.com/repos/{REPO}/actions/runs?head_sha={sha}&branch=main&event=push"
    runs = [run for run in json.loads(get(url))["workflow_runs"] if run["name"] == CI_WORKFLOW]
    if not runs:
        return "pending"  # GitHub hasn't started it yet
    latest = runs[0]  # GitHub lists the newest first
    if latest["status"] != "completed":
        return "pending"
    # "skipped" jobs, like Commits on main, still give the run a "success"
    return "passed" if latest["conclusion"] == "success" else "failed"


class Deploy:
    """One deploy attempt. The commands and web requests are passed in, so tests can fake them."""

    def __init__(
        self,
        root: Path = ROOT,
        run: Runner | None = None,
        get: Getter = http_get,
        post: Poster = http_post,
        sleep: Callable[[float], None] = time.sleep,
        health_wait: float = HEALTH_WAIT_SECONDS,
        ci_wait: float = CI_WAIT_SECONDS,
    ):
        self.root = root
        self.run = run or self._run
        self.get = get
        self.post = post
        self.sleep = sleep
        self.health_wait = health_wait
        self.ci_wait = ci_wait

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=900)

    def git(self, *args: str) -> str:
        """Run git and return its output. Raises DeployError if it fails."""
        result = self.run(["git", *args])
        if result.returncode != 0:
            raise DeployError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    # --- remembering a commit that failed -----------------------------------

    def rejected(self) -> str:
        try:
            return json.loads((self.root / STATE_FILE).read_text()).get("rejected", "")
        except READ_ERRORS:
            return ""

    def reject(self, sha: str) -> None:
        (self.root / STATE_FILE).write_text(json.dumps({"rejected": sha}) + "\n")

    # --- the steps ----------------------------------------------------------

    def requirements_changed(self, old: str, new: str) -> bool:
        # --quiet makes git diff exit with 1 when there's a difference
        return self.run(["git", "diff", "--quiet", old, new, "--", "requirements.txt"]).returncode != 0

    def install_libraries(self) -> bool:
        return self.succeeds([str(self.root / ".venv/bin/pip"), "install", "--quiet", "-r", "requirements.txt"])

    def restart(self) -> bool:
        # Allowed without a password by deploy/sudoers-jarvis-deploy, and nothing else is.
        # -n: fail instead of waiting for a password nobody will type.
        return self.succeeds(["sudo", "-n", "/usr/bin/systemctl", "restart", "jarvis"])

    def succeeds(self, cmd: list[str]) -> bool:
        result = self.run(cmd)
        if result.returncode != 0:
            # The last lines say why, for example which test failed
            tail = (result.stdout + result.stderr).strip().splitlines()[-15:]
            log(f"{Path(cmd[0]).name} failed:\n" + "\n".join(tail))
        return result.returncode == 0

    def healthy(self) -> bool:
        """Wait up to health_wait seconds for /health to answer ok."""
        deadline = time.monotonic() + self.health_wait
        while True:
            try:
                if json.loads(self.get(HEALTH_URL)).get("status") == "ok":
                    return True
            except READ_ERRORS:
                pass  # not up yet
            if time.monotonic() >= deadline:
                return False
            self.sleep(2)

    def roll_back(self, old: str, libraries_changed: bool) -> None:
        log(f"Going back to {old[:7]}")
        self.git("reset", "--hard", old)
        if libraries_changed:
            self.install_libraries()

    def notify(self, title: str, message: str, tag: str, click: str) -> None:
        """Send to the phone through the same ntfy topic Jarvis uses. Never fails the deploy."""
        env = read_env(self.root / ".env")
        topic = env.get("NTFY_TOPIC")
        if not topic:
            log("NTFY_TOPIC isn't in .env, so no message was sent")
            return
        server = env.get("NTFY_SERVER", "https://ntfy.sh")
        body = {"topic": topic, "title": title, "message": message, "tags": [tag], "click": click}
        try:
            self.post(server, json.dumps(body).encode())
        except OSError:
            log("Couldn't reach ntfy, so no message was sent")

    # --- the whole deploy ---------------------------------------------------

    def __call__(self) -> str:
        """Deploy if there's something new. Returns a one-line summary."""
        self.git("fetch", "--quiet", "origin", "main")
        old = self.git("rev-parse", "HEAD")
        new = self.git("rev-parse", "origin/main")
        if old == new:
            return "Up to date"
        if new == self.rejected():
            return f"Skipping {new[:7]}, which already failed"

        title = self.git("log", "-1", "--format=%s", new)
        commit_url = f"https://github.com/{REPO}/commit/{new}"
        # CI on main takes about a minute, so right after a merge it may still be running
        deadline = time.monotonic() + self.ci_wait
        while (result := ci_result(new, self.get)) == "pending" and time.monotonic() < deadline:
            log(f"Waiting for CI on {new[:7]}")
            self.sleep(30)
        if result == "pending":
            return f"CI still running on {new[:7]}, trying again at the next run"
        if result == "failed":
            self.reject(new)
            self.notify("⏭️ Not deploying: CI failed", title, "fast_forward", commit_url)
            return f"CI failed on {new[:7]}, not deploying"

        log(f"Deploying {new[:7]}: {title}")
        self.git("reset", "--hard", new)
        libraries_changed = self.requirements_changed(old, new)

        # Each step that can fail, and whether Jarvis is already running the new code by then
        problem = None
        restarted = False
        if libraries_changed and not self.install_libraries():
            problem = "installing libraries failed"
        elif not self.succeeds([str(self.root / ".venv/bin/pytest"), "-q", "-x", "-p", "no:cacheprovider"]):
            problem = "the tests failed on the Pi"
        else:
            # From here Jarvis may be stopped, so a rollback must restart it.
            # This script keeps running: it's jarvis-deploy.service, not jarvis.service.
            restarted = True
            restart_worked = self.restart()
            if not restart_worked:
                problem = "the restart failed"
            elif not self.healthy():
                problem = "/health didn't answer"

        if problem is None:
            self.notify("✅ Deployed to the Pi", title, "white_check_mark", commit_url)
            return f"Deployed {new[:7]}"

        self.reject(new)
        self.roll_back(old, libraries_changed)
        message = f"{title}\n{problem.capitalize()}. Back on {old[:7]}."
        if restarted and not (self.restart() and self.healthy()):
            message += " Jarvis is still down after going back!"
        self.notify("❌ Deploy failed", message, "x", commit_url)
        return f"Deploy of {new[:7]} failed: {problem}"


def main(deploy: Deploy | None = None) -> int:
    deploy = deploy or Deploy()
    # Only one deploy at a time, for example if you start one by hand while the timer's is running
    with open(deploy.root / LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another deploy is running")
            return 0
        try:
            log(deploy())
        except CHECK_ERRORS as error:
            # Usually no network. Logged only: the timer tries again tomorrow.
            log(f"Couldn't check for updates: {error}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
