"""Activity log of the app (Logs tab), stored only in /data/events.json.

Recorded: checks, firmware downloads and uploads, installations and downgrades that
were actually started, deleted firmware, setting changes on Shellys, per-device password changes (set /
removed / test failed) and app option changes. Not recorded: approvals that never
started, restarts, anything secret (passwords and hashes never reach this module;
for secret options only the key is logged).
Retention: entries older than retention_days and beyond max_entries are dropped.

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


def worst(*severities):
    """Highest of the given severities (aggregation for one log entry)."""
    return max(severities, key=SEVERITIES.index, default="success")


class EventLog:
    """Append-only list of log entries with time and size based retention."""

    def __init__(self, path: Path, retention_days=90, max_entries=1000):
        """Load stored entries; missing or broken file = empty log."""
        self.path = path
        self.retention_s = int(retention_days) * 86400
        self.max_entries = int(max_entries)
        self.entries = []
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                self.entries = [e for e in data if isinstance(e, dict) and e.get("kind") in KINDS]
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as err:
            log.warning("Log file unreadable, starting a new one: %s", err)
        if self._prune():
            self._save()

    def add(self, kind, ok=True, severity=None, **fields):
        """Append an entry (kind from KINDS) and persist the log at once.

        severity defaults to "success"/"error" from ok; "ok" is kept for the
        legacy readers and is False only for errors."""
        severity = severity if severity in SEVERITIES else ("success" if ok else "error")
        entry = {"ts": int(time.time()), "kind": kind, "ok": severity != "error", "severity": severity}
        entry.update({k: v for k, v in fields.items() if v is not None})
        self.entries.append(entry)
        self._prune()
        try:
            self._save()
        except OSError as err:  # a full disk must not break a check or installation
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

    def _save(self):
        """Write atomically (temporary file first, then rename)."""
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.entries, separators=(",", ":")))
        temp.replace(self.path)


def user_from(headers):
    """Name of the logged-in HA user from the Ingress headers (display name first).
    Control characters are removed; the value is limited to 64 characters."""
    name = headers.get("X-Remote-User-Display-Name") or headers.get("X-Remote-User-Name") or "unknown"
    return clip("".join(c for c in name if c.isprintable()), 64) or "unknown"
