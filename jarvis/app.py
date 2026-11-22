"""Jarvis's front door: receives recordings from the Pebble app.

This file only coordinates. Each job lives in its own module:
    security.verify   is the request genuine?
    dedupe            have we handled this recording already?
    agent.answer      ask Claude (the only LangChain part)
    notify.send       push the answer to your phone
    github            create an idea's issue, once you've confirmed it
    anki_sync         remove a flashcard, when you tap Undo

Run it on your Mac from the project folder:
    .venv/bin/uvicorn jarvis.app:app --port 8000
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from jarvis import anki_sync, github, notify
from jarvis.agent import answer
from jarvis.dedupe import DeliveryLog
from jarvis.security import InvalidRequest, verify
from jarvis.tools import flashcards, ideas

load_dotenv()

# Send our own log lines (questions, token use, errors) to the terminal.
# On the Pi they end up in the system journal.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jarvis.app")

# The HTTP libraries log every request with its full URL. For ntfy, the URL
# contains your topic, which works like a password, so only let them log
# warnings. Jarvis logs its own "sent" line instead. httpx2 is a newer copy of
# httpx that the Anthropic library uses.
for http_logger in ("httpx", "httpx2"):
    logging.getLogger(http_logger).setLevel(logging.WARNING)

# Questions can hold private details, so the log keeps only their beginning.
PREVIEW_CHARS = 40

# A transcript is a few kilobytes. Refusing anything over 1 MB stops a stranger
# from making the Pi read a huge upload before the signature check rejects it.
MAX_BODY_BYTES = 1_000_000

app = FastAPI(title="Jarvis")
deliveries = DeliveryLog(os.environ.get("JARVIS_DB", "deliveries.sqlite3"))


@app.get("/health")
def health():
    """Lets you check from a browser that Jarvis is running and reachable."""
    return {"status": "ok"}


async def _read_body(request: Request) -> bytes:
    """The request body, refusing anything over MAX_BODY_BYTES.

    The declared size is checked before reading, and the real size after,
    since a sender can lie about the first.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="too large")
    return body


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    # 1. Size check, before reading the whole body.
    body = await _read_body(request)

    # 2. Is it genuinely from your Pebble app? Uses the raw bytes, unparsed.
    try:
        verify(request.headers, body, os.environ["WEBHOOK_SECRET"])
    except InvalidRequest as problem:
        # Logged so you can diagnose setup mistakes, but the sender only learns
        # "unauthorized", which gives an attacker nothing to work with.
        log.warning("rejected request: %s", problem)
        raise HTTPException(status_code=401, detail="unauthorized") from None

    # From here on the request is genuine, so it's safe to parse.
    form = await request.form()
    question = str(form.get("transcription") or "").strip()

    # 3. The "Send test event" button in the Pebble app.
    if request.headers.get("x-index-test") == "true":
        log.info("test event received")
        background.add_task(_send_safely, "Webhook OK. Your Pebble app can reach Jarvis.")
        return {"status": "test received"}

    # 4. Already handled? Reply OK (so the app stops retrying) and do nothing.
    delivery = request.headers["x-index-delivery"]
    if not deliveries.first_time(delivery):
        log.info("duplicate delivery %s ignored", delivery)
        return {"status": "duplicate"}

    # 5. Nothing to answer.
    if not question:
        log.info("empty transcription ignored")
        return {"status": "empty"}

    # 6. Reply straight away. FastAPI runs the background task after replying.
    log.info("question: %s", _preview(question))
    background.add_task(handle_question, question)
    return {"status": "accepted"}


@app.post("/ideas/confirm")
async def confirm_idea(request: Request, background: BackgroundTasks):
    """Your phone calls this when you tap "Create issue" on an idea's draft.

    The body is the draft's one-time code, which only that notification holds.
    There's no signature check: the code itself is the proof, since it's
    impossible to guess and works once.
    """
    code = (await _read_body(request)).decode("utf-8", errors="replace").strip()

    # Unknown, already used or expired all look the same from outside.
    draft = ideas.drafts.take(code)
    if draft is None:
        log.warning("unknown idea code")  # never log the code itself
        raise HTTPException(status_code=404, detail="not found")

    log.info("idea confirmed")
    background.add_task(publish_idea, *draft)
    return {"status": "creating"}


@app.post("/flashcards/undo")
async def undo_flashcard(request: Request, background: BackgroundTasks):
    """Your phone calls this when you tap Undo on a "Flashcard added" notification.

    Works like /ideas/confirm: the body is a one-time code, and the code is the proof.
    """
    code = (await _read_body(request)).decode("utf-8", errors="replace").strip()

    entry = flashcards.undo_codes.take(code)
    if entry is None:
        log.warning("unknown undo code")  # never log the code itself
        raise HTTPException(status_code=404, detail="not found")

    log.info("flashcard undo requested")
    background.add_task(remove_flashcard, *entry)
    return {"status": "removing"}


def remove_flashcard(note_id: int, word: str) -> None:
    """Runs in the background: delete the card, then tell you how it went."""
    try:
        removed = anki_sync.remove_card(note_id)
    except anki_sync.AnkiError as problem:
        log.error("could not remove the flashcard: %s", problem)
        _send_safely(f"Couldn't remove {word}: {problem}")
        return
    log.info("flashcard removed" if removed else "flashcard was already gone")
    _send_safely(f"Removed: {word}" if removed else f"{word} was already gone from Anki")


# Added under each issue, so it's clear where it came from.
IDEA_FOOTER = "\n\n---\n_Spoken to Jarvis and confirmed on the phone._"


def publish_idea(title: str, description: str) -> None:
    """Runs in the background: create the issue, then tell you how it went."""
    try:
        number, url = github.create_issue(title, description + IDEA_FOOTER)
    except github.GitHubError as problem:
        log.error("could not create the issue: %s", problem)
        _send_safely(f"Couldn't create the issue: {title}")
        return
    log.info("idea published as issue #%d", number)
    _send_safely(f"Created #{number}: {title}", click=url)


def handle_question(question: str) -> None:
    """Runs in the background: answer, then notify. Never fails silently."""
    try:
        reply = answer(question)
    except Exception:
        # log.exception records the full error details for later diagnosis.
        log.exception("could not answer %s", _preview(question))
        reply = f"Couldn't answer: {question}"
    _send_safely(reply)


def _preview(question: str) -> str:
    """The question's first few words for the log, e.g. 'What time is…' (52 chars)."""
    shown = question if len(question) <= PREVIEW_CHARS else question[:PREVIEW_CHARS] + "…"
    return f"{shown!r} ({len(question)} chars)"


def _send_safely(message: str, click: str | None = None) -> None:
    try:
        notify.send(message, click=click)
    except notify.NotifyError as problem:
        log.error("could not send notification: %s", problem)
    except Exception as problem:
        # Something unexpected. Log only its type: its message or traceback
        # could contain the ntfy URL.
        log.error("could not send notification: unexpected %s", type(problem).__name__)
    else:
        log.info("notification sent to ntfy")
