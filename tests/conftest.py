"""Settings that apply to every test. pytest loads this file before any test.

Loading jarvis.app creates its delivery record file. Pointing it at a throwaway
folder here means running the tests never leaves a deliveries.sqlite3 in the
project, and never touches the Pi's real record if you run them there.
"""

import os
import tempfile

os.environ["JARVIS_DB"] = os.path.join(tempfile.mkdtemp(prefix="jarvis-tests-"), "deliveries.sqlite3")
