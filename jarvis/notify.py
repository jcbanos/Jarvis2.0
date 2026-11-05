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


def send(message: str, title: str = "Jarvis") -> None:
    """Push `message` to your phone. Raises an exception if ntfy rejects it.

    The message goes in the body, which can hold any text, accents included.
    The title is kept plain ASCII because it travels in an HTTP header, and
    headers can't safely carry characters like "á".
    """
    topic = os.environ["NTFY_TOPIC"]
    response = httpx.post(
        f"{NTFY_SERVER}/{topic}",
        content=message.encode("utf-8"),
        headers={"Title": title},
        timeout=10,  # give up after 10 seconds rather than hang
    )
    # Turns an error reply from ntfy (such as 429, too many messages) into an
    # exception, so failures aren't silent.
    response.raise_for_status()


if __name__ == "__main__":
    send(" ".join(sys.argv[1:]) or "Hello from Jarvis. Notifications are working.")
    print("Sent.")
