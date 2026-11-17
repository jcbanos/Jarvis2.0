"""Creates GitHub issues, for ideas you've confirmed on your phone.

This is ordinary web code with no LangChain involved: one HTTP POST to GitHub's
API. Like notify.py, it never lets the token reach an error message or the log.

The token should be a fine-grained one, limited to this repo with
"Issues: read and write" only, so a leak can't touch the code. See the README.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

API = "https://api.github.com"


class GitHubError(Exception):
    """Creating the issue failed. The message is safe to log: it never holds the token."""


def create_issue(title: str, body: str) -> tuple[int, str]:
    """Open an issue in GITHUB_REPO. Returns (issue number, web address)."""
    repo = os.environ["GITHUB_REPO"]  # e.g. "jcbanos/Jarvis2.0"
    try:
        response = httpx.post(
            f"{API}/repos/{repo}/issues",
            json={"title": title, "body": body},
            headers={
                "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        response.raise_for_status()
    # httpx's messages name the URL but not the headers, so the token isn't in
    # them today. As in notify.py, replace them with a short description
    # anyway, and `from None` hides the original from tracebacks in the log.
    except httpx.HTTPStatusError as error:
        raise GitHubError(f"GitHub replied {error.response.status_code}") from None
    except httpx.HTTPError as error:
        raise GitHubError(f"could not reach GitHub ({type(error).__name__})") from None

    issue = response.json()
    return issue["number"], issue["html_url"]
