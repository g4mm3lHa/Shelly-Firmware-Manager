"""Files of the app's private storage (/data) and the one way to replace a file in it.

One place for all paths: the other modules read them as attributes of this module
(data_paths.FW_DIR), so tests and the demo server can point them to a temporary folder.
write_atomic replaces a file without a reader ever seeing a half-written one; the state
file has its own save (ManagerCore.save) because it also keeps the previous file as
state.bak between the fsync and the rename.
"""

import contextlib
import os
from pathlib import Path

DATA_DIR = Path("/data")
STATE_FILE = DATA_DIR / "state.json"
FW_DIR = DATA_DIR / "firmware"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
CHANGELOG_FILE = DATA_DIR / "changelog.json"
EVENTS_FILE = DATA_DIR / "events.json"
BACKUP_DIR = DATA_DIR / "backups"  # backups of device settings (see backup_store)


def write_atomic(path: Path, content: bytes, temp: Path | None = None, *, durable: bool, private: bool = False) -> None:
    """Replace path with content: write a temporary file next to it, then rename it.

    durable: flush and fsync before the rename, so a power failure cannot leave an
    empty or half-written file (passwords, backups); without it a crash may lose the
    last write, never the previous file (log, changelog cache).
    private: the temporary file is created with mode 0600, never wider (a leftover of
    an earlier attempt is removed first, it is never reused).
    temp: name of the temporary file (default: path with the suffix .tmp).
    An OSError removes the temporary file and is raised to the caller."""
    temp = temp or path.with_suffix(".tmp")
    try:
        if private:
            temp.unlink(missing_ok=True)
            handle = os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        else:
            handle = temp.open("wb")
        with handle:
            handle.write(content)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        temp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise
