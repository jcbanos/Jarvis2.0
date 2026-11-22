"""One-time setup: connect the Pi's copy of Anki to your AnkiWeb account.

Run it on the Pi, from ~/jarvis, while you're not using the ring:
    .venv/bin/python scripts/anki_login.py

It asks for your AnkiWeb email and password (the password is never saved),
downloads your whole collection into anki/collection.anki2, and prints a sync
key to put in .env as ANKI_SYNC_KEY.

If Jarvis ever says "AnkiWeb wants a full sync", run it again with --download.
That downloads the collection again with the key already in .env, replacing
only the Pi's copy. Nothing is ever uploaded: AnkiWeb stays as it is.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from anki.collection import Collection
from anki.errors import NetworkError, SyncError, SyncErrorKind
from anki.sync import SyncAuth
from dotenv import load_dotenv

# Lets this script import from the jarvis package in the project folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.anki_sync import DEFAULT_COLLECTION  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="download again, using ANKI_SYNC_KEY from .env")
    args = parser.parse_args()

    load_dotenv()
    path = Path(os.environ.get("ANKI_COLLECTION") or DEFAULT_COLLECTION)
    path.parent.mkdir(parents=True, exist_ok=True)

    print(f"This replaces the Pi's copy, {path}, with your collection from AnkiWeb.")
    print("AnkiWeb itself isn't changed. Cards added on the Pi but never synced would be lost.")
    if input("Type yes to go on: ").strip().lower() != "yes":
        sys.exit("Stopped. Nothing changed.")

    col = Collection(str(path))
    try:
        if args.download:
            key = os.environ.get("ANKI_SYNC_KEY", "").strip()
            if not key:
                sys.exit("ANKI_SYNC_KEY is empty in .env. Run without --download first.")
            auth = SyncAuth(hkey=key, endpoint=os.environ.get("ANKI_SYNC_ENDPOINT") or None)
        else:
            email = input("AnkiWeb email: ").strip()
            auth = col.sync_login(email, getpass.getpass("AnkiWeb password: "), endpoint=None)

        # A full download: AnkiWeb's collection replaces the Pi's. upload=False
        # is the whole point: this must never send the Pi's copy to AnkiWeb.
        print("Downloading your collection. This can take a minute...")
        col.close_for_full_sync()
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        col.reopen(after_full_sync=True)
        print(f"Done: {col.note_count()} notes on the Pi.")
    except SyncError as error:
        if error.kind is SyncErrorKind.AUTH:
            sys.exit("AnkiWeb rejected the login. Check the email and password, or the key in .env.")
        sys.exit(f"AnkiWeb sync failed ({error.kind.name.lower()}).")
    except NetworkError:
        sys.exit("Couldn't reach AnkiWeb. Check the Pi's internet connection.")
    finally:
        col.close()

    if not args.download:
        # The key works like a password for your AnkiWeb account, so it's only
        # shown here, on your own screen, and never logged.
        print("\nAdd these to ~/jarvis/.env (nano ~/jarvis/.env), then restart Jarvis:\n")
        print(f"ANKI_SYNC_KEY={auth.hkey}")
        if auth.endpoint:
            print(f"ANKI_SYNC_ENDPOINT={auth.endpoint}")


if __name__ == "__main__":
    main()
