"""Pretend to be the Pebble app: send Jarvis a correctly signed question.

Useful for testing without the ring. Signs with the WEBHOOK_SECRET in .env,
exactly as the Pebble app would.

    .venv/bin/python scripts/send_test_request.py "What's the weather in Madrid?"
    .venv/bin/python scripts/send_test_request.py "..." --url https://your-pi-address/webhook

Each real question costs a few cents, because Jarvis really asks Claude.
"""

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Lets this script import from the jarvis package in the project folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.security import sign, signed_bytes  # noqa: E402

BOUNDARY = "jarvis-script-boundary"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--url", default="http://127.0.0.1:8000/webhook")
    parser.add_argument("--delivery", help="reuse a delivery ID to test duplicate handling")
    args = parser.parse_args()

    load_dotenv()
    fields = {"transcription": args.question, "recordedAt": str(int(time.time() * 1000)), "client": "ring"}
    body = "".join(
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()
    ) + f"--{BOUNDARY}--\r\n"
    body = body.encode("utf-8")

    ts, delivery, trigger = str(int(time.time())), args.delivery or str(uuid.uuid4()), "double-click-hold"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        "X-Index-Webhook-Version": "1",
        "X-Index-Timestamp": ts,
        "X-Index-Delivery": delivery,
        "X-Index-Trigger": trigger,
        "X-Index-Signature": sign(os.environ["WEBHOOK_SECRET"], signed_bytes(ts, delivery, trigger, False, body)),
    }
    response = httpx.post(args.url, content=body, headers=headers, timeout=15)
    print(f"delivery {delivery}: HTTP {response.status_code} {response.text}")


if __name__ == "__main__":
    main()
