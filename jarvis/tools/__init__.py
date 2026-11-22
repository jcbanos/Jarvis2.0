"""Jarvis's abilities.

To add an ability:
  1. Write a Python function decorated with @tool (there's an example in the
     README). Its docstring tells Claude when to use it, so make it clear.
  2. Add it to TOOLS below.

The agent picks up everything in TOOLS automatically.

Safety rule: Claude reads web pages, and a page could contain text written to
trick it. If a tool can *do* something (send, buy, delete, switch on), make it
ask you to confirm first rather than acting straight away.
"""

from typing import Any

from langchain_core.tools import BaseTool

from jarvis.tools.flashcards import add_flashcard
from jarvis.tools.ideas import propose_issue

# Claude's own web search, which runs on Anthropic's servers. It's a dict rather
# than a function because there's no code for the Pi to run.
#   max_uses: at most 3 searches per question, which caps the per-search fee.
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}

# propose_issue only drafts. Creating the issue needs a tap on your phone.
# add_flashcard acts at once, but its notification has an Undo button.
# The type says the list may mix both kinds: dicts and @tool functions.
TOOLS: list[BaseTool | dict[str, Any]] = [WEB_SEARCH, propose_issue, add_flashcard]
