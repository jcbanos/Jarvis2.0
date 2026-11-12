## Why
<!-- What problem does this solve, or why is it worth doing? -->

## What
<!-- What changed? -->

## Bug fix?
- **Is this a bug fix?** YES_OR_NO
- **What went wrong:**
- **How we prevent it next time** (a test?):

## Testing
- [ ] `pytest` passes on the Mac
- [ ] Tried on the Mac (`python -m jarvis.agent` or `scripts/send_test_request.py`)
- [ ] Deployed to the Pi and asked a question with the ring
- **Non happy paths tested:**

## New ability?
- **Does this add or change a tool?** YES_OR_NO
- **Does the tool do something** (send, buy, delete, switch on)? If yes, it asks for confirmation: YES_OR_NO

## Security & correctness
- [ ] No secrets (API key, webhook secret, ntfy topic, tokens) in the diff or the logs
- [ ] Nothing private in the diff, PR text or CI logs: this repo is public
- [ ] `/webhook` still rejects unsigned requests
- [ ] Text from web results can't trigger actions
- **Could this break something?** YES_OR_NO_AND_EXPLAIN
- **README updated?** YES_OR_NO_AND_EXPLAIN

## Deploy
- **New libraries** (needs `pip install`)? YES_OR_NO
- **Pi restart needed?** YES_OR_NO
- **New `.env` setting?** YES_OR_NO (also add it to `.env.example`)

## Cost
- **Changes the model, tools or prompt size?** YES_OR_NO_AND_EXPLAIN

## Notification sample (optional)
<!-- Paste what arrived on the phone or watch -->
