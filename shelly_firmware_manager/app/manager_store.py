"""Manager, layer 2 of 8: the firmware store, downgrade candidates, manual uploads and
the export/import of the whole store.

Stored firmware is never deleted automatically: the user deletes files in the
Firmware tab. Older stored versions allow a downgrade of Gen2+ devices, newer ones are
provided as update by a check (see manager_checks). Files can be uploaded manually in
two steps: the file is first staged (temporary name below the firmware folder, removed
at start like an incomplete download), then confirmed with type and version. Only one
upload is staged at a time.

The whole store can be exported as one sealed ZIP file and imported on another
instance (format and checks: store_transfer); the backups of device settings travel
with it, encrypted with the key of the export password. The export password is generated here
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

import backup_store
import data_paths
import shelly
import store_transfer
from changelog import marked
from shelly import ShellyError, clip

from manager_core import (
    DOWNLOAD_BASE_MESSAGES, Device, FirmwareMeta, ManagerCore, PART_PREFIX, SOURCE_DOWNLOAD,
    SOURCE_IMPORT, SOURCE_UPLOAD, STATUS_AVAILABLE, STATUS_CURRENT, STATUS_INSTALLING, holds_file,
)

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

MAX_MANIFEST = 64 * 1024  # upper limit for a manifest.json read from an uploaded ZIP

STABLE_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")  # stable Gen2+ versions only
# Gen2+ beta versions as Shelly names them ("1.5.0-beta2", also release candidates):
# accepted for an upload only while the option beta_firmware is on
BETA_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}-(beta|rc)\d{1,3}$")

DOWNGRADE_OFF = "Downgrades are off – enable “Allow downgrades” in Settings"  # option allow_downgrade is off
# A scheduled installation ("Install at") blocks a downgrade of its device, like "Install"
DOWNGRADE_PLANNED = "Cancel the scheduled installation first"

EXPORT_PREFIX = PART_PREFIX + "export-"  # temporary export file (removed at start)

IMPORT_PREFIX = PART_PREFIX + "import-"  # staged store import (removed at start)

# Status message of a device whose provided Gen1 file was marked as imported (mark_gen1_imported)
GEN1_MARKED_MESSAGE = "Stored Gen1 firmware is now treated as imported – check the device again"

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
        https: whether the download was protected by TLS (Gen1: never; uploads and
        imports: None). source: SOURCE_DOWNLOAD, SOURCE_UPLOAD or SOURCE_IMPORT;
        sha_checked (uploads only): whether the user supplied a SHA-256 that matched
        the file."""
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
    def _adopted_unconfirmed(meta: FirmwareMeta) -> bool:
        """True for a Gen2+ file read back from the disk (marker adopted, see
        adopt_files) that no manufacturer offer has confirmed yet: it is provided only
        for an offer whose URL names exactly its SHA-256, never as a store fallback or
        downgrade."""
        return meta.get("adopted") is True and (meta.get("gen") or 1) >= 2

    @staticmethod
    def _source(meta: FirmwareMeta) -> str:
        """Origin of a store entry; entries without the field (3.0.0 candidates) were downloaded."""
        source = meta.get("source")
        return source if source in (SOURCE_UPLOAD, SOURCE_IMPORT) else SOURCE_DOWNLOAD

    def _confirm_by_offer(self, name: str, meta: FirmwareMeta) -> None:
        """A manufacturer offer named the SHA-256 of this stored file (over TLS): an
        imported or read-back file now counts as a verified download. Uploads keep
        their source (the user's own file, labelled as such)."""
        if self._adopted_unconfirmed(meta):
            meta.pop("adopted", None)
            meta["https"] = True
        if self._source(meta) == SOURCE_IMPORT:
            meta.update(source=SOURCE_DOWNLOAD, https=True)
            # The changelog came with the import (unverified text): the one of the
            # Shelly documentation is requested instead
            meta.pop("changelog", None)
            log.info("Imported firmware %s %s confirmed by the update server (%s)", meta.get("type"),
                     meta.get("version"), name)

    def _in_use(self) -> set[str]:
        """Files a device currently holds as update, is installing (incl. downgrades) or
        may retry after a failed installation."""
        return {d["file"] for d in self.devices.values() if holds_file(d)}

    def cleanup_firmware(self) -> None:
        """Housekeeping of the store – never deletes a file.

        Store entries whose file is missing are dropped; firmware files on disk that
        the store does not list are kept and listed as unknown files (see
        _unknown_on_disk) for a manual deletion in the Firmware tab."""
        self.firmware = {n: m for n, m in self.firmware.items() if (data_paths.FW_DIR / n).is_file()}
        # While a check downloads, a file may already carry its final name without
        # being registered yet: the unknown files are only listed anew when no download runs
        if not self._downloads_running:
            self.unknown_files = self._unknown_on_disk()
        # Download diagnosis only for files currently provided
        in_use = self._in_use()
        self.downloads = {k: v for k, v in self.downloads.items() if k in in_use}

    def _unknown_on_disk(self) -> dict[str, int]:
        """Firmware files (<sha256>.<ext>, regular files) in the firmware folder that
        the store does not list: name -> size. Temporary files (PART_PREFIX) and
        other names are left alone – the app never wrote them under such a name."""
        unknown: dict[str, int] = {}
        for path in data_paths.FW_DIR.iterdir():
            if path.name in self.firmware or not shelly.FW_NAME_RE.fullmatch(path.name):
                continue
            with contextlib.suppress(OSError):
                if path.is_file() and not path.is_symlink():
                    unknown[path.name] = path.stat().st_size
        return unknown

    def mark_gen1_imported(self) -> None:
        """Once for a state written by 3.8.0 or older: every Gen1 store entry becomes an
        imported file. Those versions stored imported files as downloads, and Gen1
        offers name no SHA-256, so no later check could tell them apart; a file Shelly
        still offers is downloaded again by the next check and is a download again.
        Devices holding such a file (provided, or a failed installation that could be
        retried) lose it until their next check."""
        if not self.gen1_marking_due:
            return
        marked = [
            name for name, meta in self.firmware.items()
            if (meta.get("gen") or 1) < 2 and self._source(meta) != SOURCE_IMPORT
        ]
        for name in marked:
            self.firmware[name].update(source=SOURCE_IMPORT, https=None)
            self.firmware[name].pop("sha_checked", None)
            for dev in self.devices.values():
                # Every holder: provided, or a failed installation that could be retried
                if dev.get("file") == name and holds_file(dev):
                    dev.update(
                        status=STATUS_AVAILABLE, message=GEN1_MARKED_MESSAGE, file=None, sha256=None, size=None,
                        source_url=None,
                    )
                    dev.pop("install_failed", None)
                    dev.pop("store_fallback", None)
        if marked:
            log.warning("%d stored Gen1 firmware file(s) marked as imported (origin cannot be verified)", len(marked))
        self.gen1_marking_due = False
        self.save()

    async def adopt_files(self) -> None:
        """Read firmware files back from the disk that the store does not list (after
        a lost or older state, e.g. the backup was loaded); runs once at the start.

        A file is taken over only if its content matches: the SHA-256 of the file
        must equal its name, and the firmware ID and version come from a device that
        holds exactly this file (state) or from the manifest.json in the archive
        (Gen2+, stable version, not stored yet). Taken over files carry the marker
        adopted: a Gen2+ file is provided only once a manufacturer offer names its
        SHA-256 (see _stored_firmware), never as store fallback or downgrade. All
        other files stay on disk as unknown files (listed, deleted only manually).
        One warning in the activity log names the files read from the disk – they
        could have been changed – and the new unknown files."""
        self.mark_gen1_imported()
        reported = set(self.unknown_files)  # unknown at the last run (persisted names)
        unknown = self._unknown_on_disk()
        adopted: list[dict[str, Any]] = []
        for name, size in sorted(unknown.items()):
            meta = await asyncio.to_thread(self._identify_file, name)
            if meta is None:
                continue
            self._register_firmware(name, meta["type"], meta["gen"], meta["version"], meta["sha256"], size)
            self.firmware[name].update(adopted=True, https=None)
            unknown.pop(name)
            adopted.append({"type": meta["type"], "version": meta["version"], "sha256": meta["sha256"]})
        self.unknown_files = unknown
        new_unknown = sorted(set(unknown) - reported)
        if adopted or new_unknown:
            log.warning(
                "Firmware files read from the disk: %d taken over, %d unknown – they could have been changed",
                len(adopted),
                len(new_unknown),
            )
            self.events.add(
                "store",
                severity="warning",
                action="adopted",
                adopted=adopted,
                unknown=len(new_unknown),
                user="App start",
            )
        if adopted or set(unknown) != reported:
            self.save()

    def _identify_file(self, name: str) -> dict[str, Any] | None:
        """Firmware ID, generation, version and SHA-256 of an unlisted file, or None
        if its content does not prove them (runs in a worker thread, see adopt_files)."""
        path = data_paths.FW_DIR / name
        try:
            sha = self._sha256_of(path)
        except OSError:
            return None
        if sha != name.split(".", 1)[0] or self._by_sha(sha):
            return None  # content does not match the name, or already stored under another name
        for dev in self.devices.values():
            version = dev.get("pending_version")
            if dev.get("file") == name and dev.get("sha256") == sha and dev.get("type") and isinstance(version, str):
                return {"type": dev["type"], "gen": int(dev.get("gen") or 1), "version": version, "sha256": sha}
        if not name.endswith(".zip"):
            return None
        found = self._read_manifest(path)
        fw_type, version = found.get("type"), found.get("version")
        if not fw_type or not version or not STABLE_VERSION_RE.fullmatch(version):
            return None
        if any(m.get("type") == fw_type and m.get("version") == version for m in self.firmware.values()):
            return None
        gens = [int(d.get("gen") or 2) for d in self.devices.values() if d.get("type") == fw_type]
        return {"type": fw_type, "gen": max(max(gens, default=2), 2), "version": version, "sha256": sha}

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

    def _replaces_download(self, meta: FirmwareMeta, gen: int, offered_sha: str | None) -> bool:
        """The rule of _stored_firmware for one store entry of the offered version: Gen2+
        the file the offer URL names (offered_sha) or the user's own upload; Gen1 a
        stored download only. Also decides whether a file provided before stays
        provided (see manager_checks._provide)."""
        if gen < 2:
            return self._source(meta) == SOURCE_DOWNLOAD
        if offered_sha is not None and meta.get("sha256") == offered_sha:
            return True
        return self._source(meta) == SOURCE_UPLOAD and not self._adopted_unconfirmed(meta)

    async def _stored_firmware(
        self, fw_type: str, version: str, gen: int, offered_sha: str | None = None
    ) -> tuple[str, FirmwareMeta] | None:
        """Stored firmware to provide instead of downloading exactly this ID and version
        offered by the manufacturer's server – after checksum verification.

        Gen2+: a stored file (any source) only if its SHA-256 is the one the offer URL
        names (offered_sha); an imported or read-back file then counts as a verified
        download (_confirm_by_offer). The deliberate exception is an upload of exactly
        this version: the user's own file, provided as upload. Otherwise – also when
        the URL names no SHA-256 – the file is downloaded.
        Gen1 (the URL names no SHA-256): a stored download only, never an imported file."""

        def named(meta: FirmwareMeta) -> bool:
            """The offer URL names exactly this file."""
            return offered_sha is not None and meta.get("sha256") == offered_sha

        def usable(meta: FirmwareMeta) -> bool:
            """May replace the download of the offered version."""
            if meta.get("type") != fw_type or meta.get("version") != version:
                return False
            return self._replaces_download(meta, gen, offered_sha)

        matches = [(name, meta) for name, meta in list(self.firmware.items()) if usable(meta)]
        matches.sort(key=lambda item: not named(item[1]))  # the file the offer names first
        for name, meta in matches:
            if await self._intact(name, meta):
                if named(meta):
                    self._confirm_by_offer(name, meta)
                return name, meta
        return None

    async def _store_offer(self, info: dict[str, Any], imports: bool = False) -> tuple[str, FirmwareMeta] | None:
        """The next higher stable stored version of the device's firmware ID: Gen1
        store first (provided before the manufacturer's list is asked) and the store
        fallback (offered for a manual installation at the user's risk while the
        manufacturer's server cannot be asked).

        Downloads and uploads count, imported files only with imports=True (store
        fallback: the user acknowledges the risk before installing); of two files with
        the same version the manufacturer download is preferred. Every candidate is
        verified by its checksum first – a damaged file is skipped and the next one is
        taken. Returns (file name, store entry) or None if nothing newer is stored."""
        try:
            installed = shelly.parse_version(info["version"])
        except ShellyError:
            return None  # unknown version format: left to the manufacturer's offer
        candidates = []
        for name, meta in self.firmware.items():
            if meta.get("type") != info["type"] or ((meta.get("gen") or 1) >= 2) != (info["gen"] >= 2):
                continue
            if self._adopted_unconfirmed(meta):
                continue  # Gen2+ file read back from the disk: only for a matching offer
            if self._source(meta) == SOURCE_IMPORT and not imports:
                continue  # unverified import: never provided without the user's risk acknowledgment
            try:
                parsed = shelly.parse_version(meta.get("version"))
            except ShellyError:
                continue
            if parsed > installed and parsed[3] == 3:  # newer final release
                candidates.append((parsed, self._source(meta) != SOURCE_DOWNLOAD, name))
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

    @staticmethod
    def _installed_key(dev: Device) -> tuple[str, tuple[int, ...]] | None:
        """(firmware ID, parsed version) a listed Gen2+ device runs, else None (Gen1,
        removed, not identified yet or a version of unknown format)."""
        if (dev.get("gen") or 1) < 2 or dev.get("removed") or not dev.get("type"):
            return None
        try:
            return str(dev["type"]), shelly.parse_version(dev.get("version"))
        except ShellyError:
            return None

    def _stored_versions(self) -> dict[tuple[str, tuple[int, ...]], FirmwareMeta]:
        """Stored Gen2+ files whose file exists, by (firmware ID, parsed version) – the
        files a downgrade could use (read-back files only once an offer confirmed
        them; imported files count, a downgrade to them is allowed with a warning)."""
        found: dict[tuple[str, tuple[int, ...]], FirmwareMeta] = {}
        for name, meta in self.firmware.items():
            if (meta.get("gen") or 1) < 2 or self._adopted_unconfirmed(meta):
                continue
            try:
                parsed = shelly.parse_version(meta.get("version"))
            except ShellyError:
                continue
            if (data_paths.FW_DIR / name).is_file():
                found[(str(meta.get("type")), parsed)] = meta
        return found

    def way_back(self, dev: Device, stored: dict[tuple[str, tuple[int, ...]], FirmwareMeta]) -> dict[str, Any] | None:
        """Way back of a Gen2+ device: {"version": installed version, "stored": whether
        a file of exactly that version is stored} (a downgrade to it stays possible after
        the next update); None for Gen1 and devices of unknown version. stored: see
        _stored_versions (computed once per state request)."""
        key = self._installed_key(dev)
        return None if key is None else {"version": dev.get("version"), "stored": key in stored}

    def installed_versions(self) -> dict[str, Any]:
        """Storage needed by the option keep_installed (Settings tab), over the versions
        the listed Gen2+ devices run: stored (number, bytes) and those the next check
        downloads (the device is up to date, so Shelly still offers its version), with an
        estimated size each (average of the stored Gen2+ files of these firmware IDs,
        else of all; None while nothing is stored); free bytes of the store volume."""
        stored = self._stored_versions()
        running: dict[tuple[str, tuple[int, ...]], list[Device]] = {}
        for dev in self.devices.values():
            key = self._installed_key(dev)
            if key is not None:
                running.setdefault(key, []).append(dev)
        kept = [key for key in running if key in stored]

        def offered(dev: Device) -> bool:
            """Up to date: the server's last offer was exactly the installed version."""
            try:
                same = shelly.parse_version(dev.get("offer_version")) == shelly.parse_version(dev.get("version"))
            except ShellyError:
                return False
            return dev.get("status") == STATUS_CURRENT and same

        pending = [key for key, devs in running.items() if key not in stored and any(offered(d) for d in devs)]
        types = {key[0] for key in pending}
        sizes = [int(m.get("size") or 0) for k, m in stored.items() if k[0] in types] or [
            int(m.get("size") or 0) for m in stored.values()
        ]
        return {
            "stored": len(kept),
            "stored_size": sum(int(stored[key].get("size") or 0) for key in kept),
            "pending": len(pending),
            "pending_size": sum(sizes) // len(sizes) if sizes else None,
            "free": self.free_space(),
        }

    def firmware_list(self) -> list[dict[str, Any]]:
        """Stored firmwares for the "Firmware" tab, followed by the unknown files
        (marker unknown: only name, size and whether it can be deleted now). installed:
        names of the listed Gen2+ devices that run exactly this version (their way
        back after the next update)."""
        users: dict[str, list[dict[str, str]]] = {}  # file name -> devices it is provided for
        running: dict[tuple[str, tuple[int, ...]], list[str]] = {}  # version -> devices that run it
        for device_id, dev in self.devices.items():
            if holds_file(dev):
                users.setdefault(dev["file"], []).append({"id": device_id, "name": dev.get("name") or "?"})
            key = self._installed_key(dev)
            if key is not None:
                running.setdefault(key, []).append(str(dev.get("name") or "?"))
        result = []
        for name, meta in self.firmware.items():
            lock = self._delete_lock(name)
            source = self._source(meta)
            # Devices this file is provided for, ordered by name ({"id", "name"}: the
            # ID lets the Firmware tab open the installation dialog of the device)
            holders = sorted(users.get(name, []), key=lambda d: (d["name"].lower(), d["id"]))
            installed: list[str] = []
            if (meta.get("gen") or 1) >= 2:
                with contextlib.suppress(ShellyError):
                    installed = running.get((str(meta.get("type")), shelly.parse_version(meta.get("version"))), [])
            result.append(
                {
                    "name": name,
                    **{k: meta.get(k) for k in ("type", "gen", "version", "sha256", "size", "downloaded")},
                    "https": meta.get("https", meta.get("gen") != 1) if source == SOURCE_DOWNLOAD else None,
                    "source": source,
                    "sha_checked": bool(meta.get("sha_checked")) if source == SOURCE_UPLOAD else None,
                    "devices": holders,
                    "installed": sorted(installed, key=str.lower),
                    "changelog": "changelog" in meta,  # flag only; text: firmware_changelog
                    "intermediate": meta.get("intermediate") is True,  # once provided as an intermediate version
                    "adopted": meta.get("adopted") is True,  # read back from the disk, see adopt_files
                    "deletable": lock is None,
                    "locked": lock,
                }
            )
        unknown_lock = "Locked during a check" if self.checking else None
        for name, size in sorted(self.unknown_files.items()):
            result.append(
                {"name": name, "unknown": True, "size": size, "deletable": unknown_lock is None, "locked": unknown_lock}
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
        version download it again at a check. An unknown file (see unknown_files)
        is deleted the same way, refused only during the full check."""
        if name in self.unknown_files and name not in self.firmware:
            self._delete_unknown(name, user, confirm, acknowledged)
            return
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

    def _delete_unknown(self, name: str, user: str, confirm: object, acknowledged: object) -> None:
        """Delete an unknown file of the firmware folder (see delete_firmware)."""
        if self.checking:
            raise ShellyError("Firmware cannot be deleted during a check")
        if confirm is not True or acknowledged is not True:
            raise ValueError("Deletion not confirmed")
        (data_paths.FW_DIR / name).unlink(missing_ok=True)
        size = self.unknown_files.pop(name, 0)
        log.info("Unknown file %s deleted by %s", name, user)
        self.events.add("delete", unknown=True, sha256=name.split(".", 1)[0], size=size, user=user)
        self.save()

    # ------------------------------------------------------------------------------------------- Downgrade

    def downgrade_versions(self, device_id: str) -> list[dict[str, Any]]:
        """Stored stable versions of the device's firmware ID that are older than the
        installed one, newest first. Gen1: never (empty list).

        One entry per version; a manufacturer download is preferred over an upload
        or an imported file of the same version."""
        dev = self.devices[device_id]
        if (dev.get("gen") or 1) < 2 or not dev.get("type"):
            return []
        try:
            installed = shelly.parse_version(dev.get("version"))
        except ShellyError:
            return []
        best: dict[str, tuple[str, FirmwareMeta]] = {}  # version -> (name, meta)
        for name, meta in self.firmware.items():
            if meta.get("type") != dev["type"] or (meta.get("gen") or 1) < 2 or self._adopted_unconfirmed(meta):
                continue
            version = str(meta.get("version") or "")
            try:
                parsed = shelly.parse_version(version)
            except ShellyError:
                continue
            if parsed >= installed or parsed[3] != 3 or not (data_paths.FW_DIR / name).is_file():
                continue  # not older, not a final release, or file missing
            current = best.get(version)
            if current is None or (
                self._source(current[1]) != SOURCE_DOWNLOAD and self._source(meta) == SOURCE_DOWNLOAD
            ):
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
            if (meta.get("gen") or 1) < 2 or self._adopted_unconfirmed(meta):
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
        if dev.get("planned"):
            return DOWNGRADE_PLANNED
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

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, str | None]:
        """Type/version hints from a manifest.json in a ZIP (top level or one folder
        deep). Only pre-fills the form – the user confirms or corrects both values.
        Returns {"type": str|None, "version": str|None}; never raises."""
        found: dict[str, str | None] = {"type": None, "version": None}
        try:
            store_transfer.check_directory(path)  # no hints from an archive with an oversized file list
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
        if ext not in shelly.FW_EXTENSIONS:
            raise ValueError("Only .zip, .bin or .gbl firmware files can be uploaded")
        if not isinstance(total, int) or isinstance(total, bool) or total < shelly.MIN_FW_SIZE:
            raise ValueError("Firmware file implausibly small")
        if total > shelly.MAX_FW_SIZE:
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
                sha = await asyncio.to_thread(self._sha256_of, path)
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

    @staticmethod
    def _sha256_of(path: Path) -> str:
        """SHA-256 of a file (read in blocks; run in a worker thread)."""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    def _by_sha(self, sha: str) -> FirmwareMeta | None:
        """Store entry with this SHA-256 (any file extension), else None."""
        return next((m for m in self.firmware.values() if m.get("sha256") == sha), None)

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

    def _free_space(self) -> int:
        """Free bytes on the volume of the firmware store."""
        return shutil.disk_usage(data_paths.FW_DIR).free

    def free_space(self) -> int | None:
        """Free bytes on the volume of the firmware store for the Firmware tab; None
        if the volume cannot be read (information only)."""
        try:
            return self._free_space()
        except OSError:
            return None

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
        salt = secrets.token_bytes(store_transfer.SALT_BYTES)
        key = await asyncio.to_thread(store_transfer.derive_key, password, salt)
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
                clean = backup_store.clean_meta(meta)
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
