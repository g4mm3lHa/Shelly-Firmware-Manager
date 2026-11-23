"""Manager, layer 3 of 9: files into and out of the store – manual uploads and the
export/import of the whole store, received in parts.

Files arrive in parts of at most PART_MAX (Home Assistant Ingress refuses request bodies
above 16 MiB); one incomplete transfer per kind is kept (_receiving) and dropped after
PART_TTL_S without a part. A manual upload is first staged (temporary name below the
firmware folder, removed at start like an incomplete download), then confirmed with
type and version; only one upload is staged at a time.

The whole store can be exported as one sealed ZIP file and imported on another
instance (format and checks: store_transfer); the backups of device settings travel
with it, encrypted with the key of the export password. The export password is generated here
and shown once; an import is staged (temporary file, PART_PREFIX), checked completely
and stored only after the user confirmed the summary. A fresh installation asks
whether to import before its first check (first_start_open, see ManagerCore).
"""

import asyncio
import contextlib
import hmac
import logging
import re
import secrets
import time
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import backup_store
import data_paths
import shelly
import shelly_cloud
import store_transfer
from shelly import ShellyError, clip

from manager_core import PART_PREFIX, SOURCE_DOWNLOAD, SOURCE_IMPORT, SOURCE_UPLOAD, FirmwareMeta
from manager_store import STABLE_VERSION_RE, FirmwareStore

log = logging.getLogger("sfm.manager")

# Manual firmware upload: the file is first staged (temporary name below
# FW_DIR, removed at start like an incomplete download), then confirmed with type
# and version. Only one upload is staged at a time.
UPLOAD_PREFIX = PART_PREFIX + "upload-"

UPLOAD_TTL_S = 900  # a staged upload that is not confirmed within 15 min is dropped

# Files arrive in parts: Home Assistant Ingress refuses request bodies above 16 MiB, so
# the UI sends larger files as consecutive parts (offset, total size, transfer ID).
# A transfer that receives no part for PART_TTL_S is dropped.
PART_MAX = 8 * 1024 * 1024  # largest part per request (bytes); the UI sends exactly this

PART_TTL_S = 300  # seconds without a part before an incomplete transfer is dropped

TRANSFER_ID_RE = re.compile(r"^[0-9a-f]{32}$")  # ID of a transfer in parts

# Gen2+ beta versions as Shelly names them ("1.5.0-beta2", also release candidates):
# accepted for an upload only while the option beta_firmware is on
BETA_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}-(beta|rc)\d{1,3}$")

EXPORT_PREFIX = PART_PREFIX + "export-"  # temporary export file (removed at start)

IMPORT_PREFIX = PART_PREFIX + "import-"  # staged store import (removed at start)

EXPORT_TTL_S = 600  # a prepared export must be downloaded within 10 min

IMPORT_TTL_S = 900  # a staged import that is not confirmed within 15 min is dropped

SPACE_MARGIN = 20 * 1024 * 1024  # bytes kept free besides an export or import


class Transfers(FirmwareStore):
    """Files into and out of the store: uploads, store export and import (in parts)."""

    # --------------------------------------------------------------------------------------- Transfers in parts

    def _drop_receiving(self, kind: str) -> None:
        """Forget an incomplete transfer of this kind and remove its temporary file."""
        part = self._receiving.pop(kind, None)
        if part:
            part["path"].unlink(missing_ok=True)

    async def _receive_part(
        self,
        kind: str,
        prefix: str,
        chunks: AsyncIterator[bytes],
        length: int,
        total: int,
        offset: int,
        transfer: object,
        space: int = 0,
    ) -> dict[str, Any] | None:
        """Append one part of a file transfer (kind "upload" or "import") to its
        temporary file. The first part (offset 0, no transfer ID) starts a new
        transfer and replaces an incomplete one of the same kind; every further part
        must name the transfer and continue exactly at the received size. space: free
        bytes the transfer needs (checked at the start).

        Returns None while parts are missing, else {"path", "size"} of the complete
        file (no longer tracked here). Raises ValueError for invalid parts and
        ShellyError for an unknown or expired transfer; the transfer is dropped on
        any error inside a part."""
        if not 0 < length <= PART_MAX or offset < 0 or offset + length > total:
            self._drop_receiving(kind)
            raise ValueError("Invalid part of the file transfer")
        part = self._receiving.get(kind)
        if part and time.monotonic() - part["active"] > PART_TTL_S:
            self._drop_receiving(kind)
            part = None
        if offset == 0 and transfer is None:
            self._drop_receiving(kind)
            if space and self._free_space() < space + SPACE_MARGIN:
                raise ShellyError("Not enough free disk space for this file")
            part = {
                "id": secrets.token_hex(16),
                "path": data_paths.FW_DIR / f"{prefix}{secrets.token_hex(8)}",
                "total": total,
                "size": 0,
            }
            self._receiving[kind] = part
        elif (
            not part
            or not isinstance(transfer, str)
            or not shelly.same_secret(transfer, part["id"])
            or part["total"] != total
        ):
            raise ShellyError("Transfer interrupted – please choose the file again")
        elif offset != part["size"]:
            self._drop_receiving(kind)
            raise ShellyError("Transfer interrupted (part out of order) – please choose the file again")
        part["active"] = time.monotonic()
        received = 0
        try:
            with part["path"].open("ab") as handle:
                async for chunk in chunks:
                    received += len(chunk)
                    if received > length:
                        raise ValueError("Part larger than announced")
                    # Worker thread: a slow disk never blocks the event loop
                    await asyncio.to_thread(handle.write, chunk)
            if received != length:
                raise ValueError("Transfer incomplete")
        except BaseException:
            self._drop_receiving(kind)
            raise
        part["size"] += received
        part["active"] = time.monotonic()
        if part["size"] < total:
            return None
        del self._receiving[kind]
        return {"path": part["path"], "size": part["size"]}

    # --------------------------------------------------------------------------------------- Manual upload

    def _drop_upload(self) -> None:
        """Forget the staged upload and remove its temporary file."""
        staged, self._upload = self._upload, None
        if staged:
            staged["path"].unlink(missing_ok=True)

    def _staged(self, token: object) -> dict[str, Any]:
        """The staged upload for token (expired ones are dropped), else ShellyError."""
        staged = self._upload
        if staged and time.monotonic() - staged["created"] > UPLOAD_TTL_S:
            self._drop_upload()
            staged = None
        if not staged or not shelly.same_secret(token, staged["token"]):
            raise ShellyError("No pending upload – please upload the file again")
        return staged

    def upload_types(self) -> dict[str, int]:
        """Firmware IDs an upload can be assigned to: those of known Gen2+ devices."""
        types: dict[str, int] = {}
        for dev in self.devices.values():
            if (dev.get("gen") or 1) >= 2 and dev.get("type"):
                types[dev["type"]] = max(types.get(dev["type"], 0), dev["gen"])
        return types

    async def stage_upload(
        self, chunks: AsyncIterator[bytes], filename: str, length: int | None, user: str = "unknown"
    ) -> dict[str, Any]:
        """Step 1 of a manual upload in one request (the whole file as one part)."""
        if not isinstance(length, int) or length < 1:
            raise ValueError("Firmware file implausibly small")
        result = await self.stage_upload_part(chunks, filename, length, length, 0, None, user)
        if "token" not in result:
            raise ValueError("Upload incomplete")
        return result

    async def stage_upload_part(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        length: int | None,
        total: object,
        offset: object,
        transfer: object,
        user: str = "unknown",
    ) -> dict[str, Any]:
        """Step 1 of a manual upload: receive one part of the file (streamed to a
        temporary name, never held in memory as a whole; see _receive_part).

        total: size of the whole file, offset: position of this part, transfer: ID
        returned by the first part. Checks extension and size limits; after the last
        part the archive format (.zip), the SHA-256 and type/version hints from a
        manifest. Replaces an earlier staged upload. Returns {"transfer", "received"}
        while parts are missing, then the data for the confirmation form (no path)."""
        ext = Path(str(filename or "")).suffix.lower()
        if ext not in shelly_cloud.FW_EXTENSIONS:
            raise ValueError("Only .zip, .bin or .gbl firmware files can be uploaded")
        if not isinstance(total, int) or isinstance(total, bool) or total < shelly_cloud.MIN_FW_SIZE:
            raise ValueError("Firmware file implausibly small")
        if total > shelly_cloud.MAX_FW_SIZE:
            raise ValueError("Firmware larger than 64 MB")
        if not isinstance(length, int) or not isinstance(offset, int) or isinstance(offset, bool):
            raise ValueError("Invalid part of the file transfer")
        if self._upload_lock.locked():
            raise ShellyError("Another upload is in progress – please wait")
        async with self._upload_lock:
            if offset == 0:
                self._drop_upload()
            done = await self._receive_part("upload", UPLOAD_PREFIX, chunks, length, total, offset, transfer)
            if done is None:
                part = self._receiving["upload"]
                return {"transfer": part["id"], "received": part["size"]}
            path, size = done["path"], done["size"]
            try:
                if ext == ".zip" and not await asyncio.to_thread(zipfile.is_zipfile, path):
                    raise ValueError("The .zip file is not a valid ZIP archive")
                detected: dict[str, str | None] = (
                    await asyncio.to_thread(self._read_manifest, path) if ext == ".zip" else {}
                )
                sha = await asyncio.to_thread(shelly_cloud.sha256_of, path)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            self._upload = {
                "token": secrets.token_hex(16),
                "path": path,
                "ext": ext,
                "sha256": sha,
                "size": size,
                "created": time.monotonic(),
                "filename": clip(Path(str(filename)).name, 120),
            }
        log.info("Firmware upload staged by %s (%s, %d bytes, SHA-256 %s)", user, self._upload["filename"], size, sha)
        stored = self._by_sha(sha)
        return {
            "token": self._upload["token"],
            "filename": self._upload["filename"],
            "sha256": sha,
            "size": size,
            "detected": {"type": detected.get("type"), "version": detected.get("version")},
            "types": sorted(self.upload_types()),
            "duplicate": f"{stored.get('type')} {stored.get('version')}" if stored else None,
        }

    def confirm_upload(
        self, token: object, fw_type: object, version: object, expected_sha: object = None, user: str = "unknown"
    ) -> dict[str, Any]:
        """Step 2: register the staged file with the confirmed type and version.

        type: firmware ID of a known Gen2+ device; version: stable X.Y.Z, or a beta version
        X.Y.Z-betaN while the option beta_firmware is on (it is provided only when the
        Shelly update server offers exactly that version); an optional expected SHA-256
        must match the file (otherwise refused, the upload stays staged for a correction).
        A file or a type/version that is already stored is refused. Returns the new store entry."""
        staged = self._staged(token)
        types = self.upload_types()
        if not isinstance(fw_type, str) or fw_type not in types:
            raise ValueError("Choose the firmware ID of a known Gen2+ device")
        if not isinstance(version, str) or not (
            STABLE_VERSION_RE.fullmatch(version.strip())
            or (self.beta_firmware and BETA_VERSION_RE.fullmatch(version.strip()))
        ):
            if self.beta_firmware:
                raise ValueError("Version must be a release number such as 1.4.4 or a beta version such as 1.5.0-beta2")
            raise ValueError("Version must be a stable release number such as 1.4.4")
        version = version.strip()
        checked = False
        if expected_sha not in (None, ""):
            expected = str(expected_sha).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("The SHA-256 must have 64 hexadecimal characters")
            if not hmac.compare_digest(expected, staged["sha256"]):
                raise ShellyError("The SHA-256 of the uploaded file does not match the entered value")
            checked = True
        name = f"{staged['sha256']}{staged['ext']}"
        known = self._by_sha(staged["sha256"])
        if known:
            raise ShellyError(f"This file is already stored as {known.get('type')} {known.get('version')}")
        if any(m.get("type") == fw_type and m.get("version") == version for m in self.firmware.values()):
            raise ShellyError(f"{fw_type} {version} is already stored")
        staged["path"].replace(data_paths.FW_DIR / name)  # same directory: atomic
        self._upload = None
        self._register_firmware(
            name, fw_type, types[fw_type], version, staged["sha256"], staged["size"],
            source=SOURCE_UPLOAD, sha_checked=checked,
        )
        log.info("Firmware %s %s uploaded by %s (SHA-256 %s)", fw_type, version, user, staged["sha256"])
        self.events.add(
            "upload", type=fw_type, version=version, size=staged["size"], sha256=staged["sha256"],
            sha_checked=checked, user=user,
        )
        self.save()
        return {"name": name, **self.firmware[name]}

    def cancel_upload(self, token: object) -> None:
        """Discard the staged upload (Cancel in the confirmation form)."""
        with contextlib.suppress(ShellyError):
            self._staged(token)
            self._drop_upload()

    # -------------------------------------------------------------------------------- Store export / import

    async def prepare_export(self, user: str = "unknown") -> dict[str, Any]:
        """Step 1 of an export: generate the export password and derive the seal key
        (in a worker thread: scrypt takes a moment and much memory).

        The password is returned once (shown in the dialog) and kept nowhere – only
        the derived key stays in memory until the download (EXPORT_TTL_S). What can
        be refused is refused here, where the dialog shows it: the download itself
        runs through a browser link that cannot show an error. The backups of the
        device settings are part of the export, encrypted with the key of the export
        password. Returns {"token", "password", "files", "downloads", "uploads",
        "imports", "size", "backups", "filename", "expires_in"}."""
        if not self.firmware and not self.backups.entries:
            raise ShellyError("The firmware store is empty – nothing to export")
        if self._free_space() < self.store_size() + SPACE_MARGIN:
            raise ShellyError("Not enough free disk space for the export")
        password = store_transfer.generate_password()
        salt = secrets.token_bytes(backup_store.SALT_BYTES)
        key = await asyncio.to_thread(backup_store.derive_key, password, salt)
        sources = [self._source(m) for m in self.firmware.values()]
        self._export = {
            "token": secrets.token_hex(16),
            "key": key,
            "salt": salt,
            "created": time.monotonic(),
            "user": user,
        }
        return {
            "token": self._export["token"],
            "password": password,
            "files": len(self.firmware),
            "downloads": sources.count(SOURCE_DOWNLOAD),
            "uploads": sources.count(SOURCE_UPLOAD),
            "imports": sources.count(SOURCE_IMPORT),
            "size": self.store_size(),
            "backups": len(self.backups.entries),
            "filename": time.strftime("shelly-firmware-store-%Y-%m-%d.zip"),
            "expires_in": EXPORT_TTL_S,  # seconds the download stays possible
        }

    async def build_export(self, token: object, app_version: str) -> tuple[Path, str]:
        """Step 2: write the export prepared with token to a temporary file (single
        use). Returns (path, download file name); the caller sends and deletes it."""
        pending = self._export
        if (
            not pending
            or time.monotonic() - pending["created"] > EXPORT_TTL_S
            or not shelly.same_secret(token, pending["token"])
        ):
            self._export = None
            raise ShellyError("Export expired – please start it again")
        self._export = None
        if self._free_space() < self.store_size() + SPACE_MARGIN:
            raise ShellyError("Not enough free disk space for the export")
        target = data_paths.FW_DIR / f"{EXPORT_PREFIX}{secrets.token_hex(8)}"
        snapshot = {n: dict(m) for n, m in self.firmware.items()}
        backups, unreadable = self._export_backups(pending["key"], pending["salt"])
        try:
            counts = await asyncio.to_thread(
                store_transfer.build_export, data_paths.FW_DIR, snapshot, pending["key"], pending["salt"], app_version,
                target, backups,
            )
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        counts["damaged"] += unreadable
        user = pending["user"]
        log.info(
            "Firmware store exported by %s (%d files, %d backups, %d damaged left out)",
            user, counts["files"], counts["backups"], counts["damaged"],
        )
        extra: dict[str, Any] = dict(counts)
        self.events.add("export", severity="warning" if counts["damaged"] else None, user=user, **extra)
        return target, time.strftime("shelly-firmware-store-%Y-%m-%d.zip")

    def _export_backups(self, key: bytes, salt: bytes) -> tuple[list[tuple[str, bytes]], int]:
        """The stored backups of device settings as files of an export, each encrypted
        with the key of the export password: ([(path, content)], number of backups
        that could not be read and are left out)."""
        files: list[tuple[str, bytes]] = []
        unreadable = 0
        for entry in list(self.backups.entries):
            try:
                content = self.backups.sealed_copy(entry["id"], key, salt)
            except backup_store.BackupError as err:
                log.warning("Backup %s left out of the export: %s", entry["id"], err)
                unreadable += 1
                continue
            path = f"{store_transfer.BACKUP_FOLDER}/{entry['mac']}/{entry['id']}{backup_store.EXTENSION}"
            files.append((path, content))
        return files, unreadable

    def _import_backups(self, staged: dict[str, Any]) -> int:
        """Store the backups of device settings of a checked import that are not
        stored yet, encrypted with the key of this app and marked as imported; a
        "Kept" mark comes with the backup. A file that cannot be read is skipped
        (logged). Returns the number of stored backups."""
        checked = staged["checked"]
        stored = 0
        for entry in checked["backups"]:
            if self.backups.get(entry["id"]) is not None:
                continue
            try:
                content = store_transfer.read_backup(staged["path"], checked["prefix"], entry)
                meta, data = backup_store.open_sealed(checked["key"], content, "not readable with the export password")
                clean = backup_store.clean_imported(meta, data)
                if self.backups.known(clean):
                    continue
                self.backups.add(clean, data, kept=meta.get("kept") is True, imported=True)
                stored += 1
            except (ValueError, OSError, KeyError, zipfile.BadZipFile) as err:
                log.warning("Backup %s of the import skipped: %s", entry["path"], err)
        return stored

    def _drop_import(self) -> None:
        """Forget the staged import and remove its temporary file."""
        staged, self._import = self._import, None
        if staged:
            staged["path"].unlink(missing_ok=True)

    def _staged_import(self, token: object) -> dict[str, Any]:
        """The staged import for token (expired ones are dropped), else ShellyError."""
        staged = self._import
        if staged and time.monotonic() - staged["created"] > IMPORT_TTL_S:
            self._drop_import()
            staged = None
        if not staged or not shelly.same_secret(token, staged["token"]):
            raise ShellyError("No pending import – please choose the file again")
        return staged

    async def stage_import(self, chunks: AsyncIterator[bytes], length: int | None) -> dict[str, Any]:
        """Step 1 of an import in one request (the whole file as one part)."""
        if not isinstance(length, int) or length < 1:
            raise ValueError("Empty file")
        result = await self.stage_import_part(chunks, length, length, 0, None)
        if "token" not in result:
            raise ValueError("Transfer incomplete")
        return result

    async def stage_import_part(
        self, chunks: AsyncIterator[bytes], length: int | None, total: object, offset: object, transfer: object
    ) -> dict[str, Any]:
        """Step 1 of an import: receive one part of the export file (streamed to a
        temporary name, never held in memory; see _receive_part). Refused above the
        option import_max_mb or without about twice its size of free disk space (the
        file plus the firmware copied from it). Replaces an earlier staged import.
        Returns {"transfer", "received"} while parts are missing, then {"token",
        "size"}."""
        if not isinstance(total, int) or isinstance(total, bool) or total < 1:
            raise ValueError("Empty file")
        if total > self.import_max:
            raise ValueError(f"File larger than the maximum import size ({self.import_max // 1048576} MB, Settings)")
        if not isinstance(length, int) or not isinstance(offset, int) or isinstance(offset, bool):
            raise ValueError("Invalid part of the file transfer")
        if self._import_lock.locked():
            raise ShellyError("Another import is in progress – please wait")
        async with self._import_lock:
            if offset == 0:
                self._drop_import()
            done = await self._receive_part(
                "import", IMPORT_PREFIX, chunks, length, total, offset, transfer, space=2 * total
            )
            if done is None:
                part = self._receiving["import"]
                return {"transfer": part["id"], "received": part["size"]}
            path, size = done["path"], done["size"]
            if not await asyncio.to_thread(zipfile.is_zipfile, path):
                path.unlink(missing_ok=True)
                raise ValueError("The file is no ZIP archive")
            self._import = {"token": secrets.token_hex(16), "path": path, "size": size, "created": time.monotonic()}
        return {"token": self._import["token"], "size": size}

    @staticmethod
    def _same_firmware(entry: dict[str, Any], stored: FirmwareMeta) -> bool:
        """The import entry names the same firmware ID and version as the stored file
        of the same SHA-256 – only then may it add data to it (changelog, marker)."""
        return bool(stored.get("type") == entry["type"] and stored.get("version") == entry["version"])

    def _classify(self, entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Compare checked entries with the store: new, already stored (same SHA-256;
        data that is missing here is added if ID and version match too, the source
        never changes) and conflicts (same firmware ID and version, different file:
        the stored file is kept)."""
        result: dict[str, list[dict[str, Any]]] = {"new": [], "same": [], "conflict": []}
        for entry in entries:
            stored = self._by_sha(entry["sha256"])
            if stored is not None:
                result["same"].append(entry)
            elif any(
                m.get("type") == entry["type"] and m.get("version") == entry["version"] for m in self.firmware.values()
            ):
                result["conflict"].append(entry)
            else:
                result["new"].append(entry)
        return result

    def _merge_note(self, entry: dict[str, Any]) -> str:
        """What an import adds to an already stored file (summary text)."""
        stored = self._by_sha(entry["sha256"]) or {}
        if not self._same_firmware(entry, stored):
            return ""  # another ID or version than the stored file: nothing is taken over
        notes = []
        if "changelog" in entry and "changelog" not in stored:
            notes.append("changelog added")
        if entry.get("intermediate") and stored.get("intermediate") is not True:
            notes.append("intermediate marker added")
        return ", ".join(notes)

    async def check_import(self, token: object, password: object) -> dict[str, Any]:
        """Step 2: check the staged export completely (seal, every entry and file) and
        compare it with the store. Nothing is stored yet. Returns the summary."""
        if self._import_lock.locked():
            raise ShellyError("Another import is in progress – please wait")
        async with self._import_lock:
            staged = self._staged_import(token)
            text = store_transfer.normalize_password(password)
            checked = await asyncio.to_thread(store_transfer.read_export, staged["path"], text)
            staged["checked"] = checked
            staged["created"] = time.monotonic()  # the confirmation gets the full time
        groups = self._classify(checked["entries"])

        def brief(e: dict[str, Any], note: str = "") -> dict[str, Any]:
            """Summary row of an entry."""
            return {"type": e["type"], "version": e["version"], "gen": e["gen"], "source": e["source"], "note": note}

        return {
            "exported": checked["exported"],
            "files": len(checked["entries"]),
            "size": sum(e["size"] for e in checked["entries"]),
            "new": [brief(e) for e in groups["new"]],
            "same": [brief(e, self._merge_note(e)) for e in groups["same"]],
            "conflict": [brief(e) for e in groups["conflict"]],
            # Backups of device settings in the export: how many are new, how many stored already
            "backups": len(checked["backups"]),
            "backups_new": sum(1 for b in checked["backups"] if self.backups.get(b["id"]) is None),
        }

    async def confirm_import(self, token: object, user: str = "unknown", acknowledged: object = True) -> dict[str, int]:
        """Step 3: store the new files of the checked import, add missing data to
        already stored ones, keep conflicting stored files. Refused during a check
        (the store must not change then); while it runs no check can start. Answers
        an open first-start question.

        The seal only proves that the creator of the file knew its password, not who
        made it: every new file gets the source SOURCE_IMPORT, whatever the manifest
        says (see _stored_firmware and _store_offer), and an import never changes the
        source of a stored file. acknowledged: the user stated to have created the export
        or to trust its source (the web UI passes the checkbox; False is refused)."""
        if self.checking or self.device_checks:
            raise ShellyError("Not possible during a check – please try again afterwards")
        if self._import_lock.locked():
            raise ShellyError("Another import is in progress – please wait")
        async with self._import_lock:
            self._import_storing = True  # no check may start while the store changes (reserve_check)
            try:
                staged = self._staged_import(token)
                checked = staged.get("checked")
                if not checked:
                    raise ShellyError("The file has not been checked yet")
                if acknowledged is not True:
                    raise ValueError("Confirmation incomplete")
                groups = self._classify(checked["entries"])
                # The sizes are sealed; a compressed archive can unpack to more than its size
                if self._free_space() < sum(e["size"] for e in groups["new"]) + SPACE_MARGIN:
                    raise ShellyError("Not enough free disk space for the files of this import")
                stored = 0
                for entry in groups["new"]:
                    name = f"{entry['sha256']}{entry['ext']}"
                    part = data_paths.FW_DIR / f"{PART_PREFIX}import-file-{secrets.token_hex(6)}"
                    try:
                        await asyncio.to_thread(
                            store_transfer.extract_entry, staged["path"], checked["prefix"], entry, part
                        )
                        part.replace(data_paths.FW_DIR / name)  # same directory: atomic
                    except BaseException:
                        part.unlink(missing_ok=True)
                        self.save()
                        raise
                    self._register_firmware(
                        name, entry["type"], entry["gen"], entry["version"], entry["sha256"], entry["size"],
                        source=SOURCE_IMPORT,
                    )
                    meta = self.firmware[name]
                    if entry["downloaded"]:
                        meta["downloaded"] = entry["downloaded"]  # original date of the download / upload
                    if entry.get("intermediate"):
                        meta["intermediate"] = True
                    if "changelog" in entry:
                        meta["changelog"] = entry["changelog"]
                    stored += 1
                merged = 0
                for entry in groups["same"]:
                    known = self._by_sha(entry["sha256"])
                    if known is None or not self._merge_note(entry):
                        continue
                    meta = known
                    if "changelog" in entry and "changelog" not in meta:
                        meta["changelog"] = entry["changelog"]
                    if entry.get("intermediate"):
                        meta["intermediate"] = True
                    merged += 1
                backups = self._import_backups(staged)
                self._drop_import()
                if self.first_start_open:
                    self.first_start_open = False
                    self.schedule_changed.set()
            finally:
                self._import_storing = False
        counts: dict[str, Any] = {
            "stored": stored, "updated": merged, "same": len(groups["same"]), "conflicts": len(groups["conflict"]),
            "backups": backups,
        }
        log.info("Firmware store imported by %s: %s", user, counts)
        self.events.add(
            "import", severity="warning" if counts["conflicts"] else None, user=user,
            conflicts_list=[f"{e['type']} {e['version']}" for e in groups["conflict"]][:50], **counts,
        )
        self.save()
        return counts

    def cancel_import(self, token: object) -> None:
        """Discard the staged import (Cancel in one of the import dialogs)."""
        with contextlib.suppress(ShellyError):
            self._staged_import(token)
            self._drop_import()

    def skip_first_start(self, user: str = "unknown") -> None:
        """First-start question answered with "Skip": checks may run from now on."""
        if not self.first_start_open:
            return
        self.first_start_open = False
        log.info("First-start import question skipped by %s", user)
        self.events.add("setup", action="skipped", user=user)
        self.save()
        self.schedule_changed.set()

    async def announce_first_start(self) -> None:
        """One HA notification while the first-start question is open (sent once; a
        notification Home Assistant did not accept is tried again at the next start)."""
        if not self.first_start_open or self.first_start_notified:
            return
        sent = await self.notify(
            "Firmware Manager for Shelly",
            "Import a firmware store or skip it in the app – no check runs until then.",
            "shelly_firmware_manager_first_start",
        )
        if sent:
            self.first_start_notified = True
            self.save()
