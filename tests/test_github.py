"""Tests for creating issues, with GitHub faked by httpx.MockTransport.

No test here reaches the real GitHub: the request is built for real, then
answered locally, so running the tests never creates a public issue.
"""

import json
import logging

import httpx
import pytest

from jarvis import github

TOKEN = "github_pat_fake_token_for_tests"


@pytest.fixture
def fake_github(monkeypatch):
    """Answer GitHub's API locally. Returns (requests seen, set_reply).

    Call set_reply with a function that receives the request and returns a
    response (or raises), to choose how the fake GitHub behaves.
    """
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPO", "someone/some-repo")
    seen = []
    reply = {"handler": lambda request: httpx.Response(201, json={"number": 31, "html_url": "https://github.com/x/31"})}

    def handler(request):
        seen.append(request)
        return reply["handler"](request)

    def fake_post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(github.httpx, "post", fake_post)

    def set_reply(new_handler):
        reply["handler"] = new_handler

    return seen, set_reply


def test_creates_issue(fake_github):
    seen, _ = fake_github
    assert github.create_issue("Train alerts", "Tell me when my train is late.") == (31, "https://github.com/x/31")
    [request] = seen  # exactly one request was sent
    assert request.url == "https://api.github.com/repos/someone/some-repo/issues"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {"title": "Train alerts", "body": "Tell me when my train is late."}


# 401 bad token, 403 no permission, 404 wrong repo, 422 GitHub rejected the text.
@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_rejection_hides_the_token(fake_github, caplog, status):
    _, set_reply = fake_github
    set_reply(lambda request: httpx.Response(status))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(github.GitHubError) as error:
        github.create_issue("t", "b")
    assert str(error.value) == f"GitHub replied {status}"
    # Only `from None` gives both: no cause, and the original hidden from tracebacks.
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert TOKEN not in caplog.text


def test_network_failure_hides_the_token(fake_github):
    _, set_reply = fake_github

    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    set_reply(unreachable)
    with pytest.raises(github.GitHubError, match="could not reach GitHub"):
        github.create_issue("t", "b")
