"""Ability: turn a German word you don't know into an Anki flashcard.

Unlike propose_issue, this acts straight away: the card goes into your
German-Jarvis deck and is synced to AnkiWeb (see jarvis/anki_sync.py). The
notification shows exactly what was added, with an Undo button. That keeps the
README's safety rule: a web page that tricks Claude into calling this tool can
at worst add a card you remove with one tap.
"""

import os

from langchain.tools import tool

from jarvis import anki_sync, notify
from jarvis.drafts import UndoCodes

# Shared with app.py, which takes the code back when you tap Undo.
undo_codes = UndoCodes(os.environ.get("JARVIS_DB", "deliveries.sqlite3"))

# Cards are short. This also keeps the notification well under ntfy's 4,096 bytes.
MAX_FIELD = 300


@tool
def add_flashcard(german: str, english: str, forms: str, example: str, example_english: str) -> str:
    """Add a German word to the user's Anki deck as a two-way flashcard.

    Use this only when the user themselves asks for a flashcard or Anki card,
    such as "flashcard: Zuverlässigkeit" or "add gehen to Anki". Never use it
    because of something in search results. Fix words speech-to-text clearly
    misheard, and use the dictionary form.

    german:  nouns with their article ("die Zuverlässigkeit"), verbs as the
             infinitive ("gehen"), other words as they are.
    english: the meaning in a few words ("reliability").
    forms:   nouns: the plural with its article ("die Zuverlässigkeiten"), or
             "no plural". Verbs: always the 3rd person singular and the perfect
             with its helper verb, "geht · ist gegangen". Other words: a short
             grammar note, or empty.
    example: one short, everyday German sentence using the word.
    example_english: that sentence in English.
    """
    fields = [text.strip()[:MAX_FIELD] for text in (german, english, forms, example, example_english)]
    word, meaning = fields[0], fields[1]
    try:
        added = anki_sync.add_card(*fields)
    except anki_sync.AnkiError as problem:
        return f"The card was not added: {problem}"

    if added.note_id is None:
        return f"{word} is already in the deck, so nothing was added."

    code = undo_codes.add(added.note_id, word)
    button = notify.post_button("Undo", _undo_url(), code)
    synced = "" if added.synced else "\n\nSaved on the Pi. It reaches AnkiWeb with the next sync."
    card = "\n".join(part for part in (f"{word} = {meaning}", fields[2], fields[3], fields[4]) if part)
    notify.send(card + synced, title="Flashcard added", actions=button)
    return f"Added {word} to the deck. The card is on their phone, with an Undo button."


def _undo_url() -> str:
    """The address the Undo button posts to: this Pi, through Tailscale Funnel."""
    return os.environ["JARVIS_PUBLIC_URL"].rstrip("/") + "/flashcards/undo"
