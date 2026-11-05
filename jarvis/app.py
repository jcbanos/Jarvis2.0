"""Jarvis's front door: receives recordings from the Pebble app.

This file only coordinates. Each job lives in its own module:
    security.verify   is the request genuine?
    dedupe            have we handled this recording already?
    agent.answer      ask Claude (the only LangChain part)
    notify.send       push the answer to your phone

Run it on your Mac from the project folder:
    .venv/bin/uvicorn jarvis.app:app --port 8000
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from jarvis import notify
from jarvis.agent import answer
from jarvis.dedupe import DeliveryLog
from jarvis.security import InvalidRequest, verify

load_dotenv()

# Send our own log lines (questions, token use, errors) to the terminal.
# On the Pi they end up in the system journal.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jarvis.app")

# A transcript is a few kilobytes. Refusing anything over 1 MB stops a stranger
# from making the Pi read a huge upload before the signature check rejects it.
MAX_BODY_BYTES = 1_000_000

app = FastAPI(title="Jarvis")
deliveries = DeliveryLog(os.environ.get("JARVIS_DB", "deliveries.sqlite3"))


@app.get("/health")
def health():
    """Lets you check from a browser that Jarvis is running and reachable."""
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    # 1. Size check, before reading the whole body.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="too large")

    # 2. Is it genuinely from your Pebble app? Uses the raw bytes, unparsed.
    try:
        verify(request.headers, body, os.environ["WEBHOOK_SECRET"])
    except InvalidRequest as problem:
        # Logged so you can diagnose setup mistakes, but the sender only learns
        # "unauthorized", which gives an attacker nothing to work with.
        log.warning("rejected request: %s", problem)
        raise HTTPException(status_code=401, detail="unauthorized")

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
    log.info("question: %s", question)
    background.add_task(handle_question, question)
    return {"status": "accepted"}


def handle_question(question: str) -> None:
    """Runs in the background: answer, then notify. Never fails silently."""
    try:
        reply = answer(question)
    except Exception:
        # log.exception records the full error details for later diagnosis.
        log.exception("could not answer %r", question)
        reply = f"Couldn't answer: {question}"
    _send_safely(reply)


def _send_safely(message: str) -> None:
    try:
        notify.send(message)
    except Exception:
        log.exception("could not send notification")
