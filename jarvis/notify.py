"""Sends text to your phone through ntfy.

This is ordinary web code with no LangChain involved: one HTTP POST to ntfy,
which passes the message on to every phone subscribed to the topic.

Try it from the project folder:
    .venv/bin/python -m jarvis.notify "Hello from Jarvis"
"""

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")


class NotifyError(Exception):
    """Sending failed. The message is safe to log: it never contains the topic."""


def post_button(label: str, url: str, body: str) -> str:
    """A notification button that, when tapped, makes your phone POST `body` to `url`.

    ntfy describes buttons as comma-separated text, e.g.
        http, Create issue, https://example.com/ideas/confirm, method=POST, body=abc
    so a comma or semicolon inside a part would split it in the wrong place.
    `clear=true` removes the notification once the button has worked.
    """
    for part in (label, url, body):
        if "," in part or ";" in part:
            raise ValueError("button parts can't contain commas or semicolons")
    return f"http, {label}, {url}, method=POST, body={body}, clear=true"


def send(message: str, title: str = "Jarvis", actions: str | None = None, click: str | None = None) -> None:
    """Push `message` to your phone. Raises NotifyError if that fails.

    actions: buttons on the notification, made with post_button().
    click:   a web address the notification opens when you tap it.

    The message goes in the body, which can hold any text, accents included.
    The title, actions and click travel in HTTP headers, which can't safely
    carry characters like "á", so keep those plain ASCII.
    """
    topic = os.environ["NTFY_TOPIC"]
    headers = {"Title": title}
    if actions:
        headers["Actions"] = actions
    if click:
        headers["Click"] = click
    try:
        response = httpx.post(
            f"{NTFY_SERVER}/{topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=10,  # give up after 10 seconds rather than hang
        )
        # Turns an error reply from ntfy (such as 429, too many messages) into
        # an exception, so failures aren't silent.
        response.raise_for_status()
    # httpx's own errors include the full URL, and so the topic, which works
    # like a password. Replace them with a short description. `from None`
    # hides the original from tracebacks, so it can't reach the log either.
    except httpx.HTTPStatusError as error:
        raise NotifyError(f"ntfy replied {error.response.status_code}") from None
    except httpx.HTTPError as error:
        raise NotifyError(f"could not reach ntfy ({type(error).__name__})") from None


if __name__ == "__main__":
    send(" ".join(sys.argv[1:]) or "Hello from Jarvis. Notifications are working.")
    print("Sent.")
