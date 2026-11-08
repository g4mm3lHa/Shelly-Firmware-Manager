"""Shelly passwords: per-device credentials entered in the UI (v3 removed the
shared password option and its fallback completely).

Storage rules:
- Per-device passwords live only in /data/credentials.json (mode 0600), never in
  the state file, logs or API answers.
- Gen2+ devices: only HA1 = SHA-256("admin:<device ID>:<password>") is stored; the
  plain password is discarded right after the hash has been computed.
- Gen1 devices (Basic Auth) inevitably need the plain text; the file is part of
  Home Assistant backups (see README).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from shelly import DeviceAuth, gen2_ha1

log = logging.getLogger("sfm.credentials")

MAX_PASSWORD = 128  # longer than any password a Shelly accepts


def validate_password(password: object) -> str:
    """The password if it is a printable string of sensible length, else ValueError."""
    if not isinstance(password, str) or not 0 < len(password) <= MAX_PASSWORD:
        raise ValueError(f"Password must have 1–{MAX_PASSWORD} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise ValueError("Password must not contain control characters")
    return password


class Credentials:
    """Resolves the credentials to use for a device and stores device passwords."""

    def __init__(self, path: Path) -> None:
        """Load stored device passwords; a missing or broken file means none."""
        self.path = path
        # HA device ID -> {"gen": 1, "password"} | {"gen": 2, "realm", "ha1"}
        self.entries: dict[str, dict[str, Any]] = {}
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                self.entries = {k: v for k, v in data.items() if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as err:
            log.warning("Credentials file unreadable, ignoring it: %s", type(err).__name__)

    def __repr__(self) -> str:
        """Never reveal secrets, not even in a debug log."""
        return f"Credentials({len(self.entries)} device password(s))"

    def has_device_password(self, device_id: str) -> bool:
        """True if a device-specific password is stored (for the UI: set/not set)."""
        return device_id in self.entries

    def auth_for(self, device_id: str, gen: int) -> DeviceAuth | None:
        """DeviceAuth for a device from its stored credentials, else None."""
        entry = self.entries.get(device_id)
        if entry and entry.get("gen") == (1 if gen == 1 else 2):
            if gen == 1:
                return DeviceAuth(gen=1, password=entry.get("password"))
            return DeviceAuth(gen=gen, ha1=entry.get("ha1"), realm=entry.get("realm"))
        return None

    @staticmethod
    def candidate(gen: int, realm: str | None, password: object) -> DeviceAuth:
        """DeviceAuth for a password that is about to be stored (used to test it)."""
        password = validate_password(password)
        if gen == 1:
            return DeviceAuth(gen=1, password=password)
        if not realm:
            raise ValueError("Device ID unknown – run a check first")
        return DeviceAuth(gen=gen, ha1=gen2_ha1(realm, password), realm=realm)

    def store(self, device_id: str, auth: DeviceAuth) -> None:
        """Store a tested device password (Gen2+: only HA1)."""
        if auth.gen == 1:
            self.entries[device_id] = {"gen": 1, "password": auth.password}
        else:
            self.entries[device_id] = {"gen": 2, "realm": auth.realm, "ha1": auth.ha1}
        self._save()

    def remove(self, device_id: str) -> None:
        """Forget a device password (local only, the device is not touched)."""
        if self.entries.pop(device_id, None) is not None:
            self._save()

    def _save(self) -> None:
        """Write atomically with mode 0600 (created with that mode, never wider)."""
        temp = self.path.with_suffix(".tmp")
        temp.unlink(missing_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(self.entries, handle)
        temp.replace(self.path)
