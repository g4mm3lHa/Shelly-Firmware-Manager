"""Backups of device settings: the encrypted files below /data/backups and their list.

One file per backup, `<id>.sfmb`, plus `index.json` (what the UI lists) and `key` (the
key of this app, 32 random bytes, created at the first start; file mode 0600). A backup
belongs to a device by its MAC address, so it survives a new device ID in Home
Assistant and moves with a store export.

File format (all backups, also downloaded and exported ones):

    "SFMB1\\n" | header length (4 bytes, big endian) | header (JSON) | ciphertext | tag

The payload is the JSON object {"meta", "data"}, compressed with zlib and encrypted
with a key stream of SHAKE-256 over a per-file key (HMAC-SHA256 of the key and the
random nonce of the header); the tag is an HMAC-SHA256 over everything before it
(encrypt-then-MAC, standard library only). Stored backups use the key of this app. A
downloaded or exported backup uses a key derived from its password instead (scrypt,
parameters and salt in the header) – the key of this app never leaves /data.

A file is only decrypted after its tag was verified; the unpacked size is limited.
The index holds no settings, only what the lists show; a lost or damaged index is
built again from the files.

No device or network access here (see shelly_backup and manager_backups). Scrypt blocks
for a moment: callers derive password keys in a worker thread.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import zlib
from pathlib import Path
from typing import Any

import data_paths
from shelly import clip, parse_json

log = logging.getLogger("sfm.backups")

MAGIC = b"SFMB1\n"  # start of every backup file

FORMAT = "shelly-firmware-manager/backup"  # header "format"

FORMAT_VERSION = 1  # header "format_version"; other versions are refused

EXTENSION = ".sfmb"  # stored, downloaded and exported backup files

INDEX = "index.json"

KEY_FILE = "key"

KEY_BYTES = 32

NONCE_BYTES = 16

TAG_BYTES = 32

MAX_HEADER = 4096  # bytes; upper limit for the header of a file

MAX_FILE = 4 * 1024 * 1024  # bytes; largest backup file read or accepted

MAX_PAYLOAD = 16 * 1024 * 1024  # bytes; largest unpacked payload

ID_RE = re.compile(r"^[0-9a-f]{16}$")  # ID of a backup (also checked for IDs from the URL)

MAC_RE = re.compile(r"^[0-9A-F]{12}$")  # MAC address as a backup names its device

# Why a backup was made (meta "reason"): by hand, before an update or downgrade, by a
# schedule, or before another backup was restored
REASONS = ("manual", "update", "downgrade", "schedule", "restore")

SCRYPT = {"n": 2**15, "r": 8, "p": 1}  # scrypt cost of a password key; only exactly these values are accepted

SCRYPT_MAXMEM = 64 * 1024 * 1024  # bytes; memory limit of the key derivation

SALT_BYTES = 16

# Fields of an index entry (the meta data of the file plus size and the two markers)
ENTRY_TEXT = {"device": 64, "model": 64, "type": 64, "fw": 64, "note": 160}


class BackupError(ValueError):
    """A backup that cannot be read, stored or imported (message is shown to the user)."""


def normalize_mac(value: object) -> str | None:
    """MAC address in the form of the backups (12 upper-case hex digits), None if it is none."""
    text = re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).upper()
    return text if MAC_RE.fullmatch(text) else None


def derive_key(password: str, salt: bytes) -> bytes:
    """Key of a downloaded or exported backup: scrypt with the fixed cost SCRYPT (about 0.1 s)."""
    return hashlib.scrypt(password.encode(), salt=salt, maxmem=SCRYPT_MAXMEM, dklen=KEY_BYTES, **SCRYPT)


def password_header(salt: bytes) -> dict[str, Any]:
    """Header field of a file sealed with a password key."""
    return {"name": "scrypt", **SCRYPT, "salt": salt.hex()}


def _subkeys(key: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    """Encryption and authentication key of one file."""
    return (
        hmac.new(key, b"enc" + nonce, hashlib.sha256).digest(),
        hmac.new(key, b"mac" + nonce, hashlib.sha256).digest(),
    )


def _crypt(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    """XOR of data with the key stream of this file (the same call encrypts and decrypts)."""
    stream = hashlib.shake_256(enc_key + nonce).digest(len(data))
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def seal(key: bytes, meta: dict[str, Any], data: dict[str, Any], kdf: dict[str, Any] | None = None) -> bytes:
    """One backup as file content, encrypted and authenticated with key (kdf: header
    field of a password key, see password_header)."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    header: dict[str, Any] = {"format": FORMAT, "format_version": FORMAT_VERSION, "nonce": nonce.hex()}
    if kdf is not None:
        header["kdf"] = kdf
    head = json.dumps(header, separators=(",", ":")).encode()
    payload = zlib.compress(json.dumps({"meta": meta, "data": data}, separators=(",", ":")).encode(), 6)
    enc_key, mac_key = _subkeys(key, nonce)
    body = MAGIC + len(head).to_bytes(4, "big") + head + _crypt(enc_key, nonce, payload)
    return body + hmac.new(mac_key, body, hashlib.sha256).digest()


def read_header(blob: bytes) -> tuple[dict[str, Any], int]:
    """Header of a backup file and the offset of its ciphertext; BackupError if the
    content is no backup file of this app."""
    if len(blob) > MAX_FILE:
        raise BackupError("The file is too large for a backup")
    start = len(MAGIC) + 4
    if len(blob) < start + TAG_BYTES or not blob.startswith(MAGIC):
        raise BackupError("The file is no backup of this app")
    length = int.from_bytes(blob[len(MAGIC) : start], "big")
    if not 2 <= length <= MAX_HEADER or len(blob) < start + length + TAG_BYTES:
        raise BackupError("The file is no backup of this app")
    try:
        header = json.loads(blob[start : start + length])
    except ValueError:
        raise BackupError("The file is no backup of this app") from None
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise BackupError("The file is no backup of this app")
    if header.get("format_version") != FORMAT_VERSION:
        raise BackupError("Backup format not supported by this version of the app")
    nonce = header.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(rf"[0-9a-f]{{{NONCE_BYTES * 2}}}", nonce):
        raise BackupError("Damaged backup file: header incomplete")
    return header, start + length


def password_salt(header: dict[str, Any]) -> bytes:
    """Salt of a file sealed with a password; BackupError if the file was not sealed
    with one (a stored file of an app) or names other parameters."""
    kdf = header.get("kdf")
    salt = kdf.get("salt") if isinstance(kdf, dict) else None
    if (
        not isinstance(kdf, dict)
        or kdf.get("name") != "scrypt"
        or any(kdf.get(k) != v for k, v in SCRYPT.items())
        or not isinstance(salt, str)
        or not re.fullmatch(rf"[0-9a-f]{{{SALT_BYTES * 2}}}", salt)
    ):
        raise BackupError("This file was not downloaded with a password – it cannot be imported")
    return bytes.fromhex(salt)


def open_sealed(key: bytes, blob: bytes, wrong_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(meta, data) of a backup file after its tag was verified with key; BackupError
    with the text wrong_key if the tag does not fit (wrong key or changed file)."""
    header, offset = read_header(blob)
    nonce = bytes.fromhex(header["nonce"])
    enc_key, mac_key = _subkeys(key, nonce)
    body, tag = blob[:-TAG_BYTES], blob[-TAG_BYTES:]
    if not hmac.compare_digest(hmac.new(mac_key, body, hashlib.sha256).digest(), tag):
        raise BackupError(wrong_key)
    unpacker = zlib.decompressobj()
    try:
        raw = unpacker.decompress(_crypt(enc_key, nonce, body[offset:]), MAX_PAYLOAD + 1)
    except zlib.error:
        raise BackupError("Damaged backup file: content unreadable") from None
    if len(raw) > MAX_PAYLOAD or unpacker.unconsumed_tail:
        raise BackupError("Damaged backup file: content too large")
    try:
        payload = parse_json(raw)
    except ValueError:
        raise BackupError("Damaged backup file: content unreadable") from None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or not isinstance(data, dict):
        raise BackupError("Damaged backup file: content incomplete")
    return meta, data


def _count(value: object) -> int:
    """A count of the parts of a backup (0 for anything that is no small whole number)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10000 else 0


def clean_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """The meta data of a backup reduced to the known fields and limits (a file from
    outside can name anything); BackupError if it does not name a device and a time."""
    mac = normalize_mac(meta.get("mac"))
    at, gen = meta.get("at"), meta.get("gen")
    if mac is None or not isinstance(at, int) or isinstance(at, bool) or at <= 0:
        raise BackupError("Damaged backup file: device or time missing")
    if not isinstance(gen, int) or isinstance(gen, bool) or not 1 <= gen <= 9:
        raise BackupError("Damaged backup file: generation missing")
    backup_id = meta.get("id")
    stated = meta.get("parts")
    parts: dict[str, Any] = stated if isinstance(stated, dict) else {}
    counted = ("actions",) if gen == 1 else ("scripts", "schedules", "webhooks")
    clean: dict[str, Any] = {
        "id": backup_id if isinstance(backup_id, str) and ID_RE.fullmatch(backup_id) else secrets.token_hex(8),
        "mac": mac,
        "gen": gen,
        "at": at,
        "reason": meta.get("reason") if meta.get("reason") in REASONS else "manual",
        "parts": {"settings": True, **{k: _count(parts.get(k)) for k in counted}},
        "digest": meta["digest"] if isinstance(meta.get("digest"), str) and len(meta["digest"]) == 64 else None,
    }
    if gen == 1 and parts.get("partial") is True:
        clean["parts"]["partial"] = True  # the actions of the device could not be read
    for key, limit in ENTRY_TEXT.items():
        clean[key] = clip(meta.get(key), limit) or None if isinstance(meta.get(key), str) else None
    return clean


class BackupStore:
    """The stored backups: files, key and index. Entries are plain JSON objects
    {"id", "mac", "gen", "at", "reason", "note", "device", "model", "type", "fw",
    "parts", "digest", "size", "kept", "imported"}."""

    def __init__(self, folder: Path) -> None:
        """Load the key (created at the first start) and the index; a missing or
        damaged index is built again from the files."""
        self.folder = folder
        self.entries: list[dict[str, Any]] = []
        # Set if the files could not be read with the key of this app (key lost): shown in the tab
        self.warning: str | None = None
        self.rev = 0  # grows with every change of the list (the UI reloads the tab)
        folder.mkdir(parents=True, exist_ok=True)
        for path in folder.glob(".part-*"):  # left behind by a crash while a file was written
            path.unlink(missing_ok=True)
        self.key = self._load_key()
        self._load()

    # ------------------------------------------------------------------------------------------ Storage

    def _load_key(self) -> bytes:
        """The key of this app; created (and synced to the disk) if there is none."""
        path = self.folder / KEY_FILE
        try:
            key = path.read_bytes()
            if len(key) == KEY_BYTES:
                return key
            log.error("Backup key has the wrong size – stored backups cannot be read, a new key is used")
        except FileNotFoundError:
            if any(self.folder.glob("*" + EXTENSION)):
                log.error("Backup key missing – stored backups cannot be read, a new key is used")
        except OSError as err:
            log.error("Backup key unreadable (%s) – a new key is used", err)
        key = secrets.token_bytes(KEY_BYTES)
        self._write(path, key)
        return key

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        """Write a file atomically and durably, mode 0600; the temporary name .part-<random>
        is removed at the start if a crash left it behind."""
        temp = path.with_name(f".part-{secrets.token_hex(6)}")
        data_paths.write_atomic(path, content, temp, durable=True, private=True)

    def _path(self, backup_id: str) -> Path:
        """File of a backup (the ID is checked: never a path from outside)."""
        if not ID_RE.fullmatch(backup_id):
            raise BackupError("Unknown backup")
        return self.folder / f"{backup_id}{EXTENSION}"

    def _load(self) -> None:
        """Read the index; entries without a file are dropped, files without an entry
        (or all files, if the index is unreadable) are read again."""
        listed: dict[str, dict[str, Any]] = {}
        try:
            stored = json.loads((self.folder / INDEX).read_text())
            rows = stored.get("backups") if isinstance(stored, dict) else None
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and isinstance(row.get("id"), str) and ID_RE.fullmatch(row["id"]):
                    listed[row["id"]] = row
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            log.error("Backup list unreadable, reading the files again: %s", err)
        files = {
            path.stem: path
            for path in sorted(self.folder.glob("*" + EXTENSION))
            if ID_RE.fullmatch(path.stem) and not path.is_symlink() and path.is_file()
        }
        kept: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = []
        unreadable = 0
        for backup_id, path in files.items():
            row = listed.get(backup_id)
            try:
                if row is None:
                    meta, _data = open_sealed(self.key, path.read_bytes(), "not readable with the key of this app")
                    row = {**clean_meta({**meta, "id": backup_id}), "kept": False, "imported": False}
                    log.warning("Backup %s was not listed – read again from its file", backup_id)
                (found if backup_id not in listed else kept).append(self._entry({**row, "size": path.stat().st_size}))
            except (OSError, BackupError) as err:
                unreadable += 1
                log.error("Backup file %s unreadable: %s", path.name, err)
        if unreadable:
            self.warning = (
                f"{unreadable} backup file(s) cannot be read with the key of this app and are not listed. "
                "The files are kept in the app data."
            )
        # The listed backups keep their order (newest first, the one stored last first
        # within a second); the stable sort only repairs a list that is out of order
        order = {backup_id: place for place, backup_id in enumerate(listed)}
        kept.sort(key=lambda e: order[e["id"]])
        self.entries = sorted(kept, key=lambda e: -e["at"])
        for entry in found:
            self._insert(entry)
        if [e["id"] for e in self.entries] != list(listed):  # files were added or are gone
            self._save()

    def _insert(self, entry: dict[str, Any]) -> None:
        """Put an entry at its place in the list: newest first, and before the
        entries of the same second (the one stored last is the newest)."""
        place = next((i for i, e in enumerate(self.entries) if e["at"] <= entry["at"]), len(self.entries))
        self.entries.insert(place, entry)

    @staticmethod
    def _entry(row: dict[str, Any]) -> dict[str, Any]:
        """Index entry with exactly the known fields."""
        clean = clean_meta(row)
        size = row.get("size")
        return {
            **clean,
            "size": size if isinstance(size, int) and not isinstance(size, bool) else 0,
            "kept": row.get("kept") is True,
            "imported": row.get("imported") is True,
        }

    def _save(self) -> None:
        """Write the index; a write error is logged (the files themselves stay complete
        and are read again at the next start)."""
        self.rev += 1
        try:
            self._write(self.folder / INDEX, json.dumps({"backups": self.entries}, separators=(",", ":")).encode())
        except OSError as err:
            log.error("Backup list could not be written: %s", err)

    # ------------------------------------------------------------------------------------------- Access

    def of(self, mac: object) -> list[dict[str, Any]]:
        """Backups of the device with this MAC address, newest first."""
        wanted = normalize_mac(mac)
        return [e for e in self.entries if e["mac"] == wanted] if wanted else []

    def get(self, backup_id: object) -> dict[str, Any] | None:
        """Index entry of a backup, None if there is none."""
        return next((e for e in self.entries if e["id"] == backup_id), None)

    def totals(self) -> dict[str, int]:
        """Number of backups, of devices that have one and the size of all files."""
        return {
            "count": len(self.entries),
            "devices": len({e["mac"] for e in self.entries}),
            "size": sum(e["size"] for e in self.entries),
        }

    def add(
        self, meta: dict[str, Any], data: dict[str, Any], kept: bool = False, imported: bool = False
    ) -> dict[str, Any]:
        """Store a backup (encrypted with the key of this app) and list it. Returns its
        index entry; BackupError if the file cannot be written."""
        clean = clean_meta(meta)
        if self.get(clean["id"]) is not None:
            raise BackupError("This backup is stored already")
        blob = seal(self.key, clean, data)
        if len(blob) > MAX_FILE:
            raise BackupError("The settings of this device are too large for a backup")
        try:
            self._write(self._path(clean["id"]), blob)
        except OSError as err:
            raise BackupError(f"The backup could not be written: {err}") from None
        entry = {**clean, "size": len(blob), "kept": kept, "imported": imported}
        self._insert(entry)
        self._save()
        return entry

    def load(self, backup_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """(meta, data) of a stored backup; BackupError if it is gone or unreadable."""
        if self.get(backup_id) is None:
            raise BackupError("Unknown backup")
        try:
            blob = self._path(backup_id).read_bytes()
        except OSError as err:
            raise BackupError(f"The backup file cannot be read: {err}") from None
        return open_sealed(self.key, blob, "The backup cannot be read with the key of this app")

    def delete(self, backup_id: str) -> dict[str, Any]:
        """Delete a backup and its file. Returns the removed entry."""
        entry = self.get(backup_id)
        if entry is None:
            raise BackupError("Unknown backup")
        with contextlib.suppress(OSError):
            self._path(backup_id).unlink(missing_ok=True)
        self.entries.remove(entry)
        self._save()
        return entry

    def set_kept(self, backup_id: str, kept: bool) -> dict[str, Any]:
        """Mark a backup as kept (never removed automatically) or take the mark off."""
        entry = self.get(backup_id)
        if entry is None:
            raise BackupError("Unknown backup")
        entry["kept"] = kept
        self._save()
        return entry

    def prune(self, mac: object, keep: int) -> list[dict[str, Any]]:
        """Apply the retention to one device: of its backups that are not marked as
        kept only the newest `keep` stay. Returns the removed entries (oldest last)."""
        counted = [e for e in self.of(mac) if not e["kept"]]
        removed = counted[max(keep, 1) :]
        for entry in removed:
            with contextlib.suppress(OSError):
                self._path(entry["id"]).unlink(missing_ok=True)
            self.entries.remove(entry)
        if removed:
            self._save()
        return removed

    def sealed_copy(self, backup_id: str, key: bytes, salt: bytes) -> bytes:
        """A stored backup as file content sealed with a password key (download and
        store export); the key of this app is not part of it."""
        meta, data = self.load(backup_id)
        entry = self.get(backup_id) or {}
        # "kept" travels with the file (a store import takes it over); "imported" is set by the importing app
        clean = {**clean_meta({**meta, "id": backup_id}), "exported": int(time.time()), "kept": bool(entry.get("kept"))}
        return seal(key, clean, data, password_header(salt))

    def known(self, meta: dict[str, Any]) -> bool:
        """True if this backup is stored already: same ID, or the same device, time and content."""
        return any(
            e["id"] == meta.get("id")
            or (e["mac"], e["at"], e["digest"]) == (meta.get("mac"), meta.get("at"), meta.get("digest"))
            for e in self.entries
        )
