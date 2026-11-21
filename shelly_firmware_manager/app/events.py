"""Activity log of the app (Logs tab), stored only in /data/events.json.

Recorded: checks, firmware downloads and uploads, installations and downgrades that
were actually started, deleted firmware, setting changes on Shellys, per-device password
changes (set / removed / test failed) and app option changes. Not recorded: approvals
that never started, restarts, anything secret (passwords and hashes never reach this
module; for secret options only the key is logged).
Retention: entries older than retention_days and beyond max_entries are dropped.

Storage: JSON Lines – one entry per line, a new entry is appended, so adding
an entry does not rewrite the whole log. The file is rewritten (atomically) only
when the dropped entries on disk exceed COMPACT_SLACK lines, at start after a trim,
and once to convert the JSON array written by 3.0.0. A damaged line (e.g. a write cut
off by a power loss) and an entry without a numeric time are skipped.

Severity: every new entry carries "severity" = success | warning | error.
Historic entries without it are never rewritten; readers derive it from "ok".

Deleting: every entry carries a random "id" (entries of older versions get one
at the first start, the file is rewritten once). An administrator can delete a single entry
or the whole log in the Logs tab; clearing the log leaves one entry that names who
cleared it.
"""

import json
import logging
import re
import secrets
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shelly import clip

log = logging.getLogger("sfm.events")

# "log": the log itself was cleared (see EventLog.clear); "store": firmware files read
# back from the disk at the start (FirmwareStore.adopt_files); "device": a removed
# device was removed from the list (DeviceSettings.forget_device)
KINDS = (
    "check", "download", "upload", "install", "delete", "setting", "password", "options", "log", "export", "import",
    "setup", "store", "device",
)
ENTRY_ID_RE = re.compile(r"^[0-9a-f]{12}$")  # "id" of an entry, also checked for IDs from the URL
SEVERITIES = ("success", "warning", "error")  # ordered from lowest to highest
COMPACT_SLACK = 200  # dropped lines tolerated in the file before it is rewritten


def worst(*severities: str) -> str:
    """Highest of the given severities (aggregation for one log entry)."""
    return max(severities, key=SEVERITIES.index, default="success")


def _line(entry: dict[str, Any]) -> str:
    """One entry as a JSON line (compact, ASCII-safe)."""
    return json.dumps(entry, separators=(",", ":")) + "\n"


def _has_time(entry: dict[str, Any]) -> bool:
    """True if the entry carries a numeric time "ts" (the retention compares it)."""
    ts = entry.get("ts")
    return isinstance(ts, (int, float)) and not isinstance(ts, bool)


class EventLog:
    """Log entries with time and size based retention; new entries are appended, single
    entries or the whole log can be deleted by an administrator."""

    def __init__(self, path: Path, retention_days: int = 90, max_entries: int = 1000) -> None:
        """Load stored entries; missing or broken file = empty log."""
        self.path = path
        self.retention_s = int(retention_days) * 86400
        self.max_entries = int(max_entries)
        self.entries: list[dict[str, Any]] = []
        self._lines = 0  # lines in the file (kept and already dropped entries)
        rewrite = False
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            text = ""
        except (OSError, UnicodeDecodeError) as err:
            log.warning("Log file unreadable, starting a new one: %s", err)
            text, rewrite = "", True
        if text.lstrip().startswith("["):  # JSON array of 3.0.0: convert once
            try:
                data: Any = json.loads(text)
            except ValueError as err:
                log.warning("Log file unreadable, starting a new one: %s", err)
                data = []
            rows: list[Any] = data if isinstance(data, list) else []
            rewrite = True
        else:
            rows = []
            for raw in text.splitlines():
                self._lines += 1
                try:
                    rows.append(json.loads(raw))
                except ValueError:
                    rewrite = True  # damaged line: dropped by the next rewrite
        self.entries = [e for e in rows if isinstance(e, dict) and e.get("kind") in KINDS and _has_time(e)]
        for entry in self.entries:  # entries written before 3.1.1 (or with a damaged ID)
            if not ENTRY_ID_RE.fullmatch(str(entry.get("id") or "")):
                entry["id"] = secrets.token_hex(6)
                rewrite = True
        if self._prune() or rewrite or len(self.entries) != self._lines:
            self._compact()

    def add(self, kind: str, ok: bool = True, severity: str | None = None, **fields: Any) -> dict[str, Any]:
        """Append an entry (kind from KINDS) and persist it at once (one line).

        severity defaults to "success"/"error" from ok; "ok" is kept for the
        legacy readers and is False only for errors."""
        severity = severity if severity in SEVERITIES else ("success" if ok else "error")
        entry: dict[str, Any] = {
            "id": secrets.token_hex(6),
            "ts": int(time.time()),
            "kind": kind,
            "ok": severity != "error",
            "severity": severity,
        }
        entry.update({k: v for k, v in fields.items() if v is not None})
        self.entries.append(entry)
        self._prune()
        if self._lines + 1 - len(self.entries) > COMPACT_SLACK:
            self._compact()  # contains the new entry
            return entry
        try:  # a full disk must not break a check or installation
            with self.path.open("a") as handle:
                handle.write(_line(entry))
            self._lines += 1
        except OSError as err:
            log.warning("Log could not be written: %s", err)
        return entry

    def list(self) -> list[dict[str, Any]]:
        """All entries, newest first (the UI groups them by day)."""
        return self.entries[::-1]

    def remove(self, entry_id: str) -> bool:
        """Delete the entry with this ID. False if there is none (anymore)."""
        kept = [e for e in self.entries if e.get("id") != entry_id]
        if len(kept) == len(self.entries):
            return False
        self.entries = kept
        self._compact()
        return True

    def clear(self, user: str) -> int:
        """Delete all entries and record who did it – this one entry stays,
        so an emptied log is never silent. Returns the number of deleted entries."""
        count = len(self.entries)
        self.entries = []
        self._compact()
        self.add("log", action="cleared", entries=count, user=user)
        return count

    def _prune(self) -> bool:
        """Apply the retention rules; True if something was removed."""
        before = len(self.entries)
        limit = time.time() - self.retention_s
        self.entries = [e for e in self.entries if e.get("ts", 0) >= limit][-self.max_entries :]
        return len(self.entries) != before

    def _compact(self) -> None:
        """Rewrite the file with the kept entries (temporary file, then rename)."""
        temp = self.path.with_suffix(".tmp")
        try:
            temp.write_text("".join(_line(e) for e in self.entries))
            temp.replace(self.path)
        except OSError as err:
            temp.unlink(missing_ok=True)
            log.warning("Log could not be written: %s", err)
            return
        self._lines = len(self.entries)


def user_from(headers: Mapping[str, str]) -> str:
    """Name of the logged-in HA user from the Ingress headers (display name first).
    Control characters are removed; the value is limited to 64 characters."""
    name = headers.get("X-Remote-User-Display-Name") or headers.get("X-Remote-User-Name") or "unknown"
    return clip("".join(c for c in name if c.isprintable()), 64) or "unknown"
