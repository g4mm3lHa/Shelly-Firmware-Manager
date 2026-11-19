"""Manager, layer 2 of 6: the firmware store, downgrade candidates, manual uploads and
the export/import of the whole store.

Stored firmware is never deleted automatically: the user deletes files in the
Firmware tab. Older stored versions allow a downgrade of Gen2+ devices, newer ones are
provided as update by a check (see manager_checks). Files can be uploaded manually in
two steps: the file is first staged (temporary name below the firmware folder, removed
at start like an incomplete download), then confirmed with type and version. Only one
upload is staged at a time.

The whole store can be exported as one sealed ZIP file and imported on another
instance (format and checks: store_transfer). The export password is generated here
and shown once; an import is staged (temporary file, PART_PREFIX), checked completely
and stored only after the user confirmed the summary. A fresh installation asks
whether to import before its first check (first_start_open, see ManagerCore).
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import time
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import data_paths
import shelly
import store_transfer
from changelog import marked
from shelly import ShellyError, clip

from manager_core import (
    DOWNLOAD_BASE_MESSAGES, Device, FirmwareMeta, ManagerCore, PART_PREFIX, SOURCE_DOWNLOAD,
    SOURCE_UPLOAD, STATUS_AVAILABLE, STATUS_INSTALLING, holds_file,
)

log = logging.getLogger("sfm.manager")

# Manual firmware upload: the file is first staged (temporary name below
# FW_DIR, removed at start like an incomplete download), then confirmed with type
# and version. Only one upload is staged at a time.
UPLOAD_PREFIX = PART_PREFIX + "upload-"

UPLOAD_TTL_S = 900  # a staged upload that is not confirmed within 15 min is dropped

MAX_MANIFEST = 64 * 1024  # upper limit for a manifest.json read from an uploaded ZIP

STABLE_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")  # stable Gen2+ versions only

DOWNGRADE_OFF = "Downgrades are off – enable “Allow downgrades” in Settings"  # option allow_downgrade is off

EXPORT_PREFIX = PART_PREFIX + "export-"  # temporary export file (removed at start)

IMPORT_PREFIX = PART_PREFIX + "import-"  # staged store import (removed at start)

EXPORT_TTL_S = 600  # a prepared export must be downloaded within 10 min

IMPORT_TTL_S = 900  # a staged import that is not confirmed within 15 min is dropped

SPACE_MARGIN = 20 * 1024 * 1024  # bytes kept free besides an export or import


class FirmwareStore(ManagerCore):
    """Firmware files of the store: housekeeping, lookup, deletion, downgrades, uploads."""

    # --------------------------------------------------------------------------------------- Store entries

    def _register_firmware(
        self,
        name: str,
        fw_type: str,
        gen: int,
        version: str,
        sha: str,
        size: int,
        https: bool | None = None,
        source: str = SOURCE_DOWNLOAD,
        sha_checked: bool | None = None,
    ) -> None:
        """Add a firmware to the store (basis for reuse and downgrades).
        https: whether the download was protected by TLS (Gen1: never; uploads: None).
        source: SOURCE_DOWNLOAD or SOURCE_UPLOAD; sha_checked (uploads only): whether
        the user supplied a SHA-256 that matched the file."""
        meta: FirmwareMeta = {
            "type": clip(fw_type, 64),
            "gen": gen,
            "version": clip(version, 64),
            "sha256": sha,
            "size": size,
            "downloaded": int(time.time()),  # time of download or upload
            "https": (bool(https) if https is not None else gen != 1) if source == SOURCE_DOWNLOAD else None,
            "source": source,
        }
        if source == SOURCE_UPLOAD:
            meta["sha_checked"] = bool(sha_checked)
        self.firmware[name] = meta

    @staticmethod
    def _source(meta: FirmwareMeta) -> str:
        """Origin of a store entry; entries without the field (3.0.0 candidates) were downloaded."""
        return SOURCE_UPLOAD if meta.get("source") == SOURCE_UPLOAD else SOURCE_DOWNLOAD

    def _in_use(self) -> set[str]:
        """Files a device currently holds as update, is installing (incl. downgrades) or
        may retry after a failed installation."""
        return {d["file"] for d in self.devices.values() if holds_file(d)}

    def cleanup_firmware(self) -> None:
        """Housekeeping of the store – never deletes a registered firmware.

        Store entries whose file is missing are dropped; files on disk that are not
        in the store are removed. Temporary files (running downloads and staged
        uploads, PART_PREFIX) are left alone: they are removed by their own code
        path or at the next start."""
        self.firmware = {n: m for n, m in self.firmware.items() if (data_paths.FW_DIR / n).is_file()}
        for path in data_paths.FW_DIR.iterdir():
            if path.name in self.firmware or path.name.startswith(PART_PREFIX):
                continue
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
                log.info("Removed unknown file %s from the firmware store", path.name)
        # Download diagnosis only for files currently provided
        in_use = self._in_use()
        self.downloads = {k: v for k, v in self.downloads.items() if k in in_use}

    @staticmethod
    def _file_matches(name: str, sha: object) -> bool:
        """True if the stored file exists and has this SHA-256 (runs in a worker thread)."""
        try:
            return shelly.file_sha256(data_paths.FW_DIR, name) == sha
        except (OSError, ShellyError):
            return False

    async def _intact(self, name: str, meta: FirmwareMeta) -> bool:
        """Checksum verification of a stored file before it is provided.

        Verified once per file and check: parallel devices of the same type share
        the result (single flight). A damaged or missing file is dropped from the
        store and counts as not stored."""
        task = self._verifying.get(name)
        if task is None:
            task = self._verifying[name] = asyncio.ensure_future(
                asyncio.to_thread(self._file_matches, name, meta.get("sha256"))
            )
        if await task:
            return True
        if self.firmware.pop(name, None) is not None:
            log.warning("Stored firmware %s is damaged and is not used anymore", name)
        return False

    async def _stored_firmware(
        self, fw_type: str, version: str, uploads: bool = False
    ) -> tuple[str, FirmwareMeta] | None:
        """Stored firmware for exactly this ID and version offered by the manufacturer's
        server – after checksum verification. Downloads only, with uploads=True an
        uploaded file too (a download of the same version is preferred)."""
        matches = [
            (name, meta)
            for name, meta in list(self.firmware.items())
            if meta.get("type") == fw_type
            and meta.get("version") == version
            and (uploads or self._source(meta) == SOURCE_DOWNLOAD)
        ]
        matches.sort(key=lambda item: self._source(item[1]) != SOURCE_DOWNLOAD)  # downloads first
        for name, meta in matches:
            if await self._intact(name, meta):
                return name, meta
        return None

    async def _store_offer(self, info: dict[str, Any]) -> tuple[str, FirmwareMeta] | None:
        """The stored firmware to provide for a device before the manufacturer's
        server is asked: the next higher stable version of its firmware ID.

        Downloads and uploads count; of two files with the same version the
        manufacturer download is preferred. Every candidate is verified by its
        checksum first – a damaged file is skipped and the next one is taken.
        Returns (file name, store entry) or None if nothing newer is stored."""
        try:
            installed = shelly.parse_version(info["version"])
        except ShellyError:
            return None  # unknown version format: left to the manufacturer's offer
        candidates = []
        for name, meta in self.firmware.items():
            if meta.get("type") != info["type"] or ((meta.get("gen") or 1) >= 2) != (info["gen"] >= 2):
                continue
            try:
                parsed = shelly.parse_version(meta.get("version"))
            except ShellyError:
                continue
            if parsed > installed and parsed[3] == 3:  # newer final release
                candidates.append((parsed, self._source(meta) == SOURCE_UPLOAD, name))
        for _, _, name in sorted(candidates):
            meta = self.firmware.get(name, {})
            if meta and await self._intact(name, meta):
                return name, meta
        return None

    def _delete_lock(self, name: str) -> str | None:
        """Reason why a stored firmware cannot be deleted now, else None. A file that
        is only provided to devices can be deleted (see delete_firmware). The full
        check locks all files, a single-device check the files of its device type."""
        if self.checking:
            return "Locked during a check"
        meta = self.firmware.get(name, {})
        for device_id in self.device_checks:
            dev = self.devices.get(device_id, {})
            if dev.get("file") == name or (meta.get("type") and dev.get("type") == meta.get("type")):
                return "Locked during the check of a device"
        holders = [d for d in self.devices.values() if d.get("file") == name]
        installing = {self.devices.get(i, {}).get("file") for i in self.installing}
        if name in installing or any(d.get("status") == STATUS_INSTALLING for d in holders):
            return "Locked: being installed"
        return None

    def _release_file(self, name: str) -> list[str]:
        """Devices that hold the file lose it: the update is no longer provided
        (status "Available" – the next check of the device decides again; markers of
        a retry, a store fallback or an intermediate version end). Returns their
        names."""
        released = []
        for dev in self.devices.values():
            if dev.get("file") != name:
                continue
            if holds_file(dev):
                released.append(str(dev.get("name") or "?"))
                dev.update(status=STATUS_AVAILABLE, message="Provided firmware deleted – check the device again")
                dev.pop("error_level", None)
            for key in ("install_failed", "store_fallback", "step_target"):
                dev.pop(key, None)
            dev.update(file=None, sha256=None, size=None, source_url=None)
        return sorted(released, key=str.lower)

    def firmware_list(self) -> list[dict[str, Any]]:
        """Stored firmwares for the "Firmware" tab."""
        users: dict[str, list[dict[str, str]]] = {}  # file name -> devices it is provided for
        for device_id, dev in self.devices.items():
            if holds_file(dev):
                users.setdefault(dev["file"], []).append({"id": device_id, "name": dev.get("name") or "?"})
        result = []
        for name, meta in self.firmware.items():
            lock = self._delete_lock(name)
            source = self._source(meta)
            # Devices this file is provided for, ordered by name ({"id", "name"}: the
            # ID lets the Firmware tab open the installation dialog of the device)
            holders = sorted(users.get(name, []), key=lambda d: (d["name"].lower(), d["id"]))
            result.append(
                {
                    "name": name,
                    **{k: meta.get(k) for k in ("type", "gen", "version", "sha256", "size", "downloaded")},
                    "https": meta.get("https", meta.get("gen") != 1) if source == SOURCE_DOWNLOAD else None,
                    "source": source,
                    "sha_checked": bool(meta.get("sha_checked")) if source == SOURCE_UPLOAD else None,
                    "devices": holders,
                    "changelog": "changelog" in meta,  # flag only; text: firmware_changelog
                    "intermediate": meta.get("intermediate") is True,  # once provided as an intermediate version
                    "deletable": lock is None,
                    "locked": lock,
                }
            )
        return result

    def firmware_changelog(self, name: str) -> list[dict[str, Any]] | None:
        """The changelog kept with a stored firmware, for the Firmware tab:
        one version in the form of changelog_for, or None if none is stored yet.
        Nothing is requested – the changelog is stored by a check that provides the
        file (see _store_changelogs)."""
        meta = self.firmware[name]
        stored = meta.get("changelog")
        if not isinstance(stored, dict):
            return None
        return [marked(str(meta.get("version") or ""), stored, meta.get("type"))]

    def store_size(self) -> int:
        """Total size of all stored firmware files in bytes (information only)."""
        return sum(int(meta.get("size") or 0) for meta in self.firmware.values())

    def delete_firmware(
        self, name: str, user: str = "unknown", confirm: object = False, acknowledged: object = False
    ) -> None:
        """Delete a stored firmware – only manually, with a second confirmation
        (confirm and acknowledged, as for the device password).

        Refused during a check and for a file that is being installed. A file that
        is only provided to devices can be deleted: they lose the provided update
        (see _release_file; named in the log entry). Devices that may later need the
        version download it again at a check."""
        if name not in self.firmware:
            raise KeyError(name)
        lock = self._delete_lock(name)
        if lock:
            raise ShellyError(lock.replace("Locked", "Firmware cannot be deleted", 1))
        if confirm is not True or acknowledged is not True:
            raise ValueError("Deletion not confirmed")
        meta = self.firmware.pop(name)
        (data_paths.FW_DIR / name).unlink(missing_ok=True)
        released = self._release_file(name)
        note = f" (no longer provided: {', '.join(released)})" if released else ""
        log.info("Firmware %s deleted by %s%s", name, user, note)
        extra: dict[str, Any] = {"released": released} if released else {}
        self.events.add(
            "delete",
            type=meta.get("type"),
            version=meta.get("version"),
            sha256=meta.get("sha256"),
            source=self._source(meta),
            user=user,
            **extra,
        )
        self.save()

    # ------------------------------------------------------------------------------------------- Downgrade

    def downgrade_versions(self, device_id: str) -> list[dict[str, Any]]:
        """Stored stable versions of the device's firmware ID that are older than the
        installed one, newest first. Gen1: never (empty list).

        One entry per version; a manufacturer download is preferred over an upload
        of the same version."""
        dev = self.devices[device_id]
        if (dev.get("gen") or 1) < 2 or not dev.get("type"):
            return []
        try:
            installed = shelly.parse_version(dev.get("version"))
        except ShellyError:
            return []
        best: dict[str, tuple[str, FirmwareMeta]] = {}  # version -> (name, meta)
        for name, meta in self.firmware.items():
            if meta.get("type") != dev["type"] or (meta.get("gen") or 1) < 2:
                continue
            version = str(meta.get("version") or "")
            try:
                parsed = shelly.parse_version(version)
            except ShellyError:
                continue
            if parsed >= installed or parsed[3] != 3 or not (data_paths.FW_DIR / name).is_file():
                continue  # not older, not a final release, or file missing
            current = best.get(version)
            if current is None or (self._source(current[1]) == SOURCE_UPLOAD and self._source(meta) == SOURCE_DOWNLOAD):
                best[version] = (name, meta)
        ordered = sorted(best.values(), key=lambda item: shelly.parse_version(item[1]["version"]), reverse=True)
        return [
            {
                "name": name,
                "version": meta.get("version"),
                "sha256": meta.get("sha256"),
                "size": meta.get("size"),
                "downloaded": meta.get("downloaded"),
                "source": self._source(meta),
                "sha_checked": bool(meta.get("sha_checked")) if self._source(meta) == SOURCE_UPLOAD else None,
            }
            for name, meta in ordered
        ]

    def _downgrade_candidates(self) -> dict[str, set[tuple[int, ...]]]:
        """Stored final Gen2+ versions per firmware ID whose file exists – computed
        once per state request, so the row menu knows whether a downgrade is possible
        without one request per device (same rules as downgrade_versions)."""
        stored: dict[str, set[tuple[int, ...]]] = {}
        for name, meta in self.firmware.items():
            if (meta.get("gen") or 1) < 2:
                continue
            try:
                parsed = shelly.parse_version(meta.get("version"))
            except ShellyError:
                continue
            if parsed[3] == 3 and (data_paths.FW_DIR / name).is_file():
                stored.setdefault(str(meta.get("type")), set()).add(parsed)
        return stored

    @staticmethod
    def _downgrade_count(dev: Device, stored: dict[str, set[tuple[int, ...]]]) -> int:
        """Number of stored versions older than the installed one (0 for Gen1)."""
        if (dev.get("gen") or 1) < 2 or not dev.get("type"):
            return 0
        try:
            installed = shelly.parse_version(dev.get("version"))
        except ShellyError:
            return 0
        return sum(1 for version in stored.get(dev["type"], ()) if version < installed)

    def downgrade_match(self, device_id: str, sha: object) -> dict[str, Any] | None:
        """The offered downgrade entry with exactly this SHA-256, else None."""
        if not isinstance(sha, str):
            return None
        return next(
            (v for v in self.downgrade_versions(device_id) if hmac.compare_digest(str(v["sha256"]), sha)), None
        )

    def downgrade_blocker(self, device_id: str) -> str | None:
        """Why no downgrade can be started for the device now, else None."""
        dev = self.devices[device_id]
        if (dev.get("gen") or 1) < 2:
            return "Downgrades are not offered for Gen1 devices"
        if not self.allow_downgrade:
            return DOWNGRADE_OFF
        if self.checking:
            return "Check in progress – please try again afterwards"
        if self.download_base_issue:
            return DOWNLOAD_BASE_MESSAGES[self.download_base_issue]
        if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
            return "Installation for this device is already running"
        if device_id in self.reloading:
            return "Device info is being reloaded – please try again in a moment"
        if device_id in self.device_checks:
            return "Device is being checked – please try again afterwards"
        if not dev.get("origin") or not dev.get("build") or not dev.get("mac"):
            return "Device has not been checked successfully yet"
        return None

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
        if not staged or not isinstance(token, str) or not hmac.compare_digest(staged["token"], token):
            raise ShellyError("No pending upload – please upload the file again")
        return staged

    def upload_types(self) -> dict[str, int]:
        """Firmware IDs an upload can be assigned to: those of known Gen2+ devices."""
        types: dict[str, int] = {}
        for dev in self.devices.values():
            if (dev.get("gen") or 1) >= 2 and dev.get("type"):
                types[dev["type"]] = max(types.get(dev["type"], 0), dev["gen"])
        return types

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, str | None]:
        """Type/version hints from a manifest.json in a ZIP (top level or one folder
        deep). Only pre-fills the form – the user confirms or corrects both values.
        Returns {"type": str|None, "version": str|None}; never raises."""
        found: dict[str, str | None] = {"type": None, "version": None}
        try:
            with zipfile.ZipFile(path) as archive:
                member = next(
                    (
                        i
                        for i in archive.infolist()
                        if i.filename.rsplit("/", 1)[-1] == "manifest.json" and i.filename.count("/") <= 1
                    ),
                    None,
                )
                if member is None or member.file_size > MAX_MANIFEST:
                    return found
                with archive.open(member) as handle:
                    data = json.loads(handle.read(MAX_MANIFEST + 1)[:MAX_MANIFEST])
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
            return found
        if not isinstance(data, dict):
            return found
        name = data.get("name") or data.get("app")
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
            found["type"] = name
        for key in ("version", "build_id", "fw_id"):
            match = shelly.VERSION_RE.search(str(data.get(key) or ""))
            if match:
                found["version"] = clip(match.group(0), 64)
                break
        return found

    async def stage_upload(
        self, chunks: AsyncIterator[bytes], filename: str, length: int | None, user: str = "unknown"
    ) -> dict[str, Any]:
        """Step 1 of a manual upload: store the file under a temporary name.

        chunks: async iterator of bytes (the request body, streamed – never held in
        memory as a whole); length: declared Content-Length. Checks extension, size
        limits and (for .zip) the archive format, computes the SHA-256 and reads
        type/version hints from a manifest. Replaces an earlier staged upload.
        Returns the data for the confirmation form (no file path)."""
        ext = Path(str(filename or "")).suffix.lower()
        if ext not in shelly.FW_EXTENSIONS:
            raise ValueError("Only .zip, .bin or .gbl firmware files can be uploaded")
        if not isinstance(length, int) or length < shelly.MIN_FW_SIZE:
            raise ValueError("Firmware file implausibly small")
        if length > shelly.MAX_FW_SIZE:
            raise ValueError("Firmware larger than 64 MiB")
        if self._upload_lock.locked():
            raise ShellyError("Another upload is in progress – please wait")
        async with self._upload_lock:
            self._drop_upload()
            path = data_paths.FW_DIR / f"{UPLOAD_PREFIX}{secrets.token_hex(8)}"
            digest, size = hashlib.sha256(), 0
            try:
                with path.open("wb") as handle:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > shelly.MAX_FW_SIZE or size > length:
                            raise ValueError("Upload larger than announced or than 64 MiB")
                        digest.update(chunk)
                        # Worker thread: a slow disk never blocks the event loop
                        await asyncio.to_thread(handle.write, chunk)
                if size != length:
                    raise ValueError("Upload incomplete")
                if ext == ".zip" and not await asyncio.to_thread(zipfile.is_zipfile, path):
                    raise ValueError("The .zip file is not a valid ZIP archive")
                detected: dict[str, str | None] = (
                    await asyncio.to_thread(self._read_manifest, path) if ext == ".zip" else {}
                )
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            sha = digest.hexdigest()
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

    def _by_sha(self, sha: str) -> FirmwareMeta | None:
        """Store entry with this SHA-256 (any file extension), else None."""
        return next((m for m in self.firmware.values() if m.get("sha256") == sha), None)

    def confirm_upload(
        self, token: object, fw_type: object, version: object, expected_sha: object = None, user: str = "unknown"
    ) -> dict[str, Any]:
        """Step 2: register the staged file with the confirmed type and version.

        type: firmware ID of a known Gen2+ device; version: stable X.Y.Z; an optional
        expected SHA-256 must match the file (otherwise refused, the upload stays
        staged for a correction). A file or a type/version that is already stored is
        refused. Returns the new store entry."""
        staged = self._staged(token)
        types = self.upload_types()
        if not isinstance(fw_type, str) or fw_type not in types:
            raise ValueError("Choose the firmware ID of a known Gen2+ device")
        if not isinstance(version, str) or not STABLE_VERSION_RE.match(version.strip()):
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

    def _free_space(self) -> int:
        """Free bytes on the volume of the firmware store."""
        return shutil.disk_usage(data_paths.FW_DIR).free

    def prepare_export(self, user: str = "unknown") -> dict[str, Any]:
        """Step 1 of an export: generate the export password and derive the seal key.

        The password is returned once (shown in the dialog) and kept nowhere – only
        the derived key stays in memory until the download (EXPORT_TTL_S). Returns
        {"token", "password", "files", "downloads", "uploads", "size", "filename"}."""
        if not self.firmware:
            raise ShellyError("The firmware store is empty – nothing to export")
        password = store_transfer.generate_password()
        salt = secrets.token_bytes(store_transfer.SALT_BYTES)
        uploads = sum(1 for m in self.firmware.values() if self._source(m) == SOURCE_UPLOAD)
        self._export = {
            "token": secrets.token_hex(16),
            "key": store_transfer.derive_key(password, salt),
            "salt": salt,
            "created": time.monotonic(),
            "user": user,
        }
        return {
            "token": self._export["token"],
            "password": password,
            "files": len(self.firmware),
            "downloads": len(self.firmware) - uploads,
            "uploads": uploads,
            "size": self.store_size(),
            "filename": time.strftime("shelly-firmware-store-%Y-%m-%d.zip"),
        }

    async def build_export(self, token: object, app_version: str) -> tuple[Path, str]:
        """Step 2: write the export prepared with token to a temporary file (single
        use). Returns (path, download file name); the caller sends and deletes it."""
        pending = self._export
        if (
            not pending
            or time.monotonic() - pending["created"] > EXPORT_TTL_S
            or not isinstance(token, str)
            or not hmac.compare_digest(pending["token"], token)
        ):
            self._export = None
            raise ShellyError("Export expired – please start it again")
        self._export = None
        if self._free_space() < self.store_size() + SPACE_MARGIN:
            raise ShellyError("Not enough free disk space for the export")
        target = data_paths.FW_DIR / f"{EXPORT_PREFIX}{secrets.token_hex(8)}"
        snapshot = {n: dict(m) for n, m in self.firmware.items()}
        try:
            counts = await asyncio.to_thread(
                store_transfer.build_export, data_paths.FW_DIR, snapshot, pending["key"], pending["salt"], app_version,
                target,
            )
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        user = pending["user"]
        log.info(
            "Firmware store exported by %s (%d files, %d damaged left out)", user, counts["files"], counts["damaged"]
        )
        extra: dict[str, Any] = dict(counts)
        self.events.add("export", severity="warning" if counts["damaged"] else None, user=user, **extra)
        return target, time.strftime("shelly-firmware-store-%Y-%m-%d.zip")

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
        if not staged or not isinstance(token, str) or not hmac.compare_digest(staged["token"], token):
            raise ShellyError("No pending import – please choose the file again")
        return staged

    async def stage_import(self, chunks: AsyncIterator[bytes], length: int | None) -> dict[str, Any]:
        """Step 1 of an import: store the export file under a temporary name.

        Streamed to disk (never held in memory); refused above the option
        import_max_mb or without about twice its size of free disk space (the file
        plus the firmware copied from it). Replaces an earlier staged import."""
        if not isinstance(length, int) or length < 1:
            raise ValueError("Empty file")
        if length > self.import_max:
            raise ValueError(f"File larger than the maximum import size ({self.import_max // 1048576} MB, Settings)")
        if self._import_lock.locked():
            raise ShellyError("Another import is in progress – please wait")
        async with self._import_lock:
            self._drop_import()
            if self._free_space() < 2 * length + SPACE_MARGIN:
                raise ShellyError("Not enough free disk space for this import")
            path = data_paths.FW_DIR / f"{IMPORT_PREFIX}{secrets.token_hex(8)}"
            size = 0
            try:
                with path.open("wb") as handle:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > length or size > self.import_max:
                            raise ValueError("File larger than announced or than the maximum import size")
                        await asyncio.to_thread(handle.write, chunk)
                if size != length:
                    raise ValueError("Transfer incomplete")
                if not await asyncio.to_thread(zipfile.is_zipfile, path):
                    raise ValueError("The file is no ZIP archive")
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            self._import = {"token": secrets.token_hex(16), "path": path, "size": size, "created": time.monotonic()}
        return {"token": self._import["token"], "size": size}

    def _classify(self, entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Compare checked entries with the store: new, already stored (same SHA-256;
        data that is missing here is added) and conflicts (same firmware ID and
        version, different file: the stored file is kept)."""
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
        notes = []
        if entry["source"] == SOURCE_DOWNLOAD and self._source(stored) == SOURCE_UPLOAD:
            notes.append("marked as downloaded")
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
        }

    async def confirm_import(self, token: object, user: str = "unknown") -> dict[str, int]:
        """Step 3: store the new files of the checked import, add missing data to
        already stored ones, keep conflicting stored files. Refused during a check
        (the store must not change then). Answers an open first-start question."""
        if self.checking or self.device_checks:
            raise ShellyError("Not possible during a check – please try again afterwards")
        if self._import_lock.locked():
            raise ShellyError("Another import is in progress – please wait")
        async with self._import_lock:
            staged = self._staged_import(token)
            checked = staged.get("checked")
            if not checked:
                raise ShellyError("The file has not been checked yet")
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
                    https=entry.get("https"), source=entry["source"], sha_checked=entry.get("sha_checked"),
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
                if entry["source"] == SOURCE_DOWNLOAD and self._source(meta) == SOURCE_UPLOAD:
                    meta.update(source=SOURCE_DOWNLOAD, https=entry.get("https"))
                    meta.pop("sha_checked", None)
                    if entry["downloaded"]:
                        meta["downloaded"] = entry["downloaded"]
                if "changelog" in entry and "changelog" not in meta:
                    meta["changelog"] = entry["changelog"]
                if entry.get("intermediate"):
                    meta["intermediate"] = True
                merged += 1
            self._drop_import()
            if self.first_start_open:
                self.first_start_open = False
                self.schedule_changed.set()
        counts: dict[str, Any] = {
            "stored": stored, "updated": merged, "same": len(groups["same"]), "conflicts": len(groups["conflict"]),
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
        """One HA notification while the first-start question is open (sent once)."""
        if not self.first_start_open or self.first_start_notified:
            return
        await self.notify(
            "Shelly Firmware Manager",
            "Import a firmware store or skip it in the app – no check runs until then.",
            "shelly_firmware_manager_first_start",
        )
        self.first_start_notified = True
        self.save()
