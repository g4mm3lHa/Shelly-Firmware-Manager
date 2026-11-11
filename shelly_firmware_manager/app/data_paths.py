"""Files of the app's private storage (/data).

One place for all paths: the other modules read them as attributes of this module
(data_paths.FW_DIR), so tests and the demo server can point them to a temporary folder.
"""

from pathlib import Path

DATA_DIR = Path("/data")
STATE_FILE = DATA_DIR / "state.json"
FW_DIR = DATA_DIR / "firmware"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
CHANGELOG_FILE = DATA_DIR / "changelog.json"
EVENTS_FILE = DATA_DIR / "events.json"
