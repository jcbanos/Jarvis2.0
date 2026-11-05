# Jarvis

A voice agent you talk to through a **Pebble Index 01** ring. Double-click and hold the ring, ask a question, and the answer arrives on your phone and Garmin watch a few seconds later.

It's built to grow: web search is the first ability, and new ones are Python functions added to a list.

## How it works

```
Ring ──Bluetooth──▶ Pebble app (phone) ──HTTPS──▶ jarvis.tail867051.ts.net
                    transcribes speech            │ Tailscale Funnel
                                                  ▼
                                    Raspberry Pi: Jarvis service
                                      1. security.verify   genuine request?
                                      2. dedupe            seen it before?
                                      3. reply "OK" at once
                                      4. agent.answer      Claude + web search  (background)
                                      5. notify.send       ──▶ ntfy ──▶ phone ──▶ Garmin
```

- **The ring** only records. Your phone's Pebble app turns the audio into text and posts it to Jarvis.
- **Tailscale Funnel** gives the Pi a public HTTPS address without opening anything on your router. Only Jarvis's port is public.
- **Jarvis replies "OK" straight away** and answers in the background, so the Pebble app never waits and retries.
- **Claude does the thinking and searching** on Anthropic's servers. The Pi mostly waits on the network, which is why a small computer is plenty.
- **ntfy** delivers the answer as a phone notification, which Garmin Connect forwards to the watch.

## The code

| File | Job |
|---|---|
| `jarvis/app.py` | The front door. Receives requests and coordinates the other modules |
| `jarvis/security.py` | Checks each request was signed by your Pebble app |
| `jarvis/dedupe.py` | Remembers handled recordings in `deliveries.sqlite3`, so repeats are ignored |
| `jarvis/agent.py` | **The only LangChain part.** `answer(question)` takes text in and returns text out |
| `jarvis/tools/__init__.py` | Jarvis's abilities: the `TOOLS` list |
| `jarvis/notify.py` | Sends text to your phone through ntfy |
| `tests/` | 31 automated tests. They fake Claude and ntfy, so they cost nothing |
| `scripts/send_test_request.py` | Acts as the Pebble app, for testing without the ring |
| `deploy/jarvis.service` | Keeps Jarvis running on the Pi |

### LangChain in one minute

- **Model** (`ChatAnthropic`): the connection to Claude.
- **Tool**: something Claude can ask to use. Claude only sees its name, arguments and docstring, so the docstring is effectively an instruction.
- **Agent** (`create_agent`): the loop. Ask Claude, run any tool it requests, give it the result, repeat until it answers without requesting a tool.
- **Messages**: the conversation so far. Each pass through the loop resends all of it, which is why each tool use adds cost.
- **Middleware**: plug-ins that wrap every model call. Jarvis uses `ModelRefusalMiddleware` to turn "Claude declined" into an error it can handle.

Tools run in one of two places. **Server tools**, like web search, run on Anthropic's servers within a single Claude call. **Your tools** are Python functions that run on the Pi, each one costing an extra call to Claude.

## Adding an ability

1. Write a function in `jarvis/tools/` and decorate it with `@tool`. Make the docstring say clearly when to use it.
2. Add it to `TOOLS` in `jarvis/tools/__init__.py`.
3. Test it on your Mac with `.venv/bin/python -m jarvis.agent "a question that needs it"`.
4. Update the Pi (see below).

```python
from langchain.tools import tool

@tool
def get_room_temperature(room: str) -> str:
    """Get the current temperature of a room in the user's home."""
    return read_sensor(room)  # your code here
```

**Safety rule:** Claude reads web pages, and a page could contain text written to trick it into using your tools. **Any tool that does something** (sends, buys, deletes, switches on) should ask you to confirm before acting.

## Day-to-day commands

Run these in Terminal on your Mac. If `jarvis.local` doesn't work, use the Pi's IP address instead, which was `192.168.1.170`.

| What | Command |
|---|---|
| Is Jarvis running? | `ssh jcbanos@jarvis.local 'systemctl status jarvis'` |
| Watch the log live (Ctrl+C to stop) | `ssh jcbanos@jarvis.local 'journalctl -u jarvis -f'` |
| Token use per question | `ssh jcbanos@jarvis.local 'journalctl -u jarvis \| grep "tokens used"'` |
| Is it reachable from the internet? | Open `https://jarvis.tail867051.ts.net/health` and look for `{"status":"ok"}` |
| Run the tests on your Mac | `.venv/bin/pytest` |
| Ask a question without the ring | `.venv/bin/python scripts/send_test_request.py "your question" --url https://jarvis.tail867051.ts.net/webhook` |

### Updating the Pi after changing code

From the `Jarvis2.0` folder on your Mac:

```bash
# 1. Copy changes across (only what changed is sent)
rsync -a --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
      --exclude '*.sqlite3' --exclude '.DS_Store' ./ jcbanos@jarvis.local:~/jarvis/

# 2. Only if requirements.txt changed: install the new libraries
ssh jcbanos@jarvis.local 'cd ~/jarvis && .venv/bin/pip install -r requirements.txt'

# 3. Restart Jarvis (asks for your Pi password)
ssh -t jcbanos@jarvis.local 'sudo systemctl restart jarvis'
```

## Settings

`.env` holds these. It's never copied into git, and on the Pi only your user can read it.

| Setting | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Pays for Claude. Manage it at [console.anthropic.com](https://console.anthropic.com) |
| `JARVIS_MODEL` | Which Claude model to use. Currently `claude-sonnet-5-5` |
| `NTFY_TOPIC` | Your private ntfy topic. Anyone who knows it can read your answers |
| `WEBHOOK_SECRET` | Shared with the Pebble app, which signs each request with it |

`.env.example` lists the same settings with blank values.

### Pebble app webhook (Double click & hold)

| Setting | Value |
|---|---|
| URL | `https://jarvis.tail867051.ts.net/webhook` |
| Payload | **Transcription only** |
| Signing | On, with the `WEBHOOK_SECRET` value |

## Costs

Measured on real questions with Sonnet 5.5:

| Question | Input tokens | Approximate cost |
|---|---|---|
| Needs a web search | about 20,000 | 5 cents |
| Answered without searching | about 6,000 | 1 cent |

Even a question with no search sends about 6,000 tokens, because describing the search tool to Claude takes that much. Check real spending on the Console's **Usage** and **Cost** pages, and keep a monthly spend limit set there.

## Security

- **Signatures:** anything not signed with your secret gets `401 unauthorized`. Requests older than 5 minutes are rejected too, so a recorded request can't be replayed.
- **Only two addresses exist,** `/health` and `/webhook`. Automated scanners find every new public address within minutes and try paths like `/.env`. They all get `404`, and you'll see them in the log. That's normal.
- **The service runs as your user,** can't gain admin rights, and can only write inside `~/jarvis`.
- **If a secret leaks:** for the webhook secret, generate a new one, put it in `.env` on the Pi and in the Pebble app, and restart Jarvis. For the API key, delete it in the Console and create a new one. For the ntfy topic, pick a new random name, update `.env`, and resubscribe on your phone.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Log says `empty transcription ignored` | The webhook payload isn't set to **Transcription only** |
| Log says `rejected request: signature does not match` | The secret in the Pebble app differs from `WEBHOOK_SECRET` |
| Log says `timestamp outside the 5-minute window` | The phone's or Pi's clock is wrong |
| You get two answers | Pebble's built-in assistant is also handling the gesture. Check **Ring Button** in the Pebble app |
| "Couldn't answer: …" | Claude or the network failed. The log has the details (`journalctl -u jarvis`) |
| Nothing arrives at all | Check `/health` in a browser, then the log, then that the ntfy app is subscribed to the right topic |
| Answer on phone but not watch | Garmin Connect needs **Notification access**, and ntfy must be on in its app list |
| `jarvis.local` not found | Use the Pi's IP address, or install Tailscale on your Mac |
