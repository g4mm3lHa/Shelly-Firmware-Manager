"""Activity log of the app (Logs tab), stored only in /data/events.json.

Recorded: checks, firmware downloads and uploads, installations and downgrades that
were actually started, deleted firmware, setting changes on Shellys, per-device password
changes (set / removed / test failed) and app option changes. Not recorded: approvals
that never started, restarts, anything secret (passwords and hashes never reach this
module; for secret options only the key is logged).
Retention: entries older than retention_days and beyond max_entries are dropped.

Storage (3.0.1): JSON Lines – one entry per line, a new entry is appended, so adding
an entry no longer rewrites the whole log. The file is rewritten (atomically) only
when the dropped entries on disk exceed COMPACT_SLACK lines, at start after a trim,
and once to convert the JSON array written by 3.0.0. A damaged line (e.g. a write cut
off by a power loss) is skipped.

Severity (v3): every new entry carries "severity" = success | warning | error.
Historic entries without it are never rewritten; readers derive it from "ok".
"""

import json
import logging
import time
from pathlib import Path

from shelly import clip

log = logging.getLogger("sfm.events")

KINDS = ("check", "download", "upload", "install", "delete", "setting", "password", "options")
SEVERITIES = ("success", "warning", "error")  # ordered from lowest to highest
COMPACT_SLACK = 200  # dropped lines tolerated in the file before it is rewritten


def worst(*severities):
    """Highest of the given severities (aggregation for one log entry)."""
    return max(severities, key=SEVERITIES.index, default="success")


def _line(entry):
    """One entry as a JSON line (compact, ASCII-safe)."""
    return json.dumps(entry, separators=(",", ":")) + "\n"


class EventLog:
    """Append-only list of log entries with time and size based retention."""

    def __init__(self, path: Path, retention_days=90, max_entries=1000):
        """Load stored entries; missing or broken file = empty log."""
        self.path = path
        self.retention_s = int(retention_days) * 86400
        self.max_entries = int(max_entries)
        self.entries = []
        self._lines = 0  # lines in the file (kept and already dropped entries)
        rewrite = False
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            text = ""
        except OSError as err:
            log.warning("Log file unreadable, starting a new one: %s", err)
            text, rewrite = "", True
        if text.lstrip().startswith("["):  # JSON array of 3.0.0: convert once
            try:
                data = json.loads(text)
            except ValueError as err:
                log.warning("Log file unreadable, starting a new one: %s", err)
                data = []
            rows = data if isinstance(data, list) else []
            rewrite = True
        else:
            rows = []
            for raw in text.splitlines():
                self._lines += 1
                try:
                    rows.append(json.loads(raw))
                except ValueError:
                    rewrite = True  # damaged line: dropped by the next rewrite
        self.entries = [e for e in rows if isinstance(e, dict) and e.get("kind") in KINDS]
        if self._prune() or rewrite or len(self.entries) != self._lines:
            self._compact()

    def add(self, kind, ok=True, severity=None, **fields):
        """Append an entry (kind from KINDS) and persist it at once (one line).

        severity defaults to "success"/"error" from ok; "ok" is kept for the
        legacy readers and is False only for errors."""
        severity = severity if severity in SEVERITIES else ("success" if ok else "error")
        entry = {"ts": int(time.time()), "kind": kind, "ok": severity != "error", "severity": severity}
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

    def list(self):
        """All entries, newest first (the UI groups them by day)."""
        return self.entries[::-1]

    def _prune(self):
        """Apply the retention rules; True if something was removed."""
        before = len(self.entries)
        limit = time.time() - self.retention_s
        self.entries = [e for e in self.entries if e.get("ts", 0) >= limit][-self.max_entries :]
        return len(self.entries) != before

    def _compact(self):
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


def user_from(headers):
    """Name of the logged-in HA user from the Ingress headers (display name first).
    Control characters are removed; the value is limited to 64 characters."""
    name = headers.get("X-Remote-User-Display-Name") or headers.get("X-Remote-User-Name") or "unknown"
    return clip("".join(c for c in name if c.isprintable()), 64) or "unknown"
