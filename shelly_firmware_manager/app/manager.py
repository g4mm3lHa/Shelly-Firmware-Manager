"""Workflow: check devices, provide firmware, install updates, edit device settings.

State lives in /data/state.json, firmware in /data/firmware, device passwords in
/data/credentials.json, changelogs in /data/changelog.json and the activity log in
/data/events.json (all private to the app).
Checks – the full check and the single-device check – run exclusively
(asyncio.Lock); within the full check up to CHECK_PARALLEL devices are queried at
the same time, and offers and firmware downloads are shared (single flight) so
parallelism never duplicates a request or a file. Installations run in parallel
up to a limit (asyncio.Semaphore); checks and installations exclude each other.
Installations are always started manually. Ignored devices are left out of the
full check (an explicit single-device check is still possible).
Stored firmware is never deleted automatically: the user deletes files in
the Firmware tab. Older stored versions allow a downgrade of Gen2+ devices, and
older files can be uploaded manually (two steps: stage, then confirm).
The firmware store comes first: a check provides the next higher stored
version of the device's firmware ID (downloads and uploads); the manufacturer's
server is only asked for devices without such a file, once per firmware ID (Gen1:
once for all types). A confirmed installation is followed by a check of the
installed devices, so the next stored version is provided without waiting.

Progress of a running check is kept in memory only (self.progress, self.activity):
the UI shows it while polling, nothing of it is persisted.
"""

import asyncio
import contextlib
import functools
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import zipfile
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any

import aiohttp
import app_options
import schedule
import shelly
from changelog import Changelogs, marked
from credentials import Credentials
from events import EventLog, worst
from shelly import UNSUPPORTED, AuthError, DeviceAuth, ShellyError, clip, error_text
from tls import CertificatePins

log = logging.getLogger("sfm.manager")

# A device entry of state.json and a store entry of the firmware store: plain JSON
# objects whose fields are listed in docs/CODEMAP.md (Stored data).
Device = dict[str, Any]
FirmwareMeta = dict[str, Any]
# Shared manufacturer requests of one check (single flight): (gen, firmware ID) -> offer
# of a Gen2+ firmware ID, GEN1_OFFERS -> firmware list of all Gen1 types
OfferTasks = dict[tuple[int, str], "asyncio.Future[Any]"]
GEN1_OFFERS = (1, "")

DATA_DIR = Path("/data")
STATE_FILE = DATA_DIR / "state.json"
FW_DIR = DATA_DIR / "firmware"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
CHANGELOG_FILE = DATA_DIR / "changelog.json"
EVENTS_FILE = DATA_DIR / "events.json"

# Device status – shown like this in the UI
STATUS_CURRENT = "up-to-date"
STATUS_AVAILABLE = "available"  # newer version known, provided at the next check (after a downgrade)
STATUS_READY = "ready"  # firmware downloaded, waiting for manual installation
STATUS_INSTALLING = "installing"
STATUS_UNCLEAR = "unconfirmed"  # installation without confirmed target version
STATUS_ERROR = "error"
STATUS_UNREGISTERED = "unregistered"
STATUS_SKIPPED = "skipped"  # Gen1 device while Gen1 updates are disabled
PENDING = (STATUS_READY, STATUS_INSTALLING)  # device holds a firmware file

INSTALL_FIRST_CHECK_S = 30  # wait after the update command
INSTALL_POLL_S = 10  # then check every 10 s
INSTALL_NO_DOWNLOAD_S = 120  # no download request: give up after 2 min (a Shelly may
# still answer with the old version for a while after accepting the update)
INSTALL_WAIT_S = 180  # upper limit (3 min) in case the Shelly gets stuck
SUPERVISOR = "http://supervisor"  # Supervisor API (Core API proxy below /core)
SUPERVISOR_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Per-device activity during a check (memory only, shown in the device row)
ACTIVITY_WAITING = "waiting"  # part of the running check, not yet reached
ACTIVITY_CHECKING = "checking"  # device / manufacturer server is being queried
ACTIVITY_DOWNLOADING = "downloading"  # firmware for this device is being downloaded
ACTIVITY_RELOADING = "reloading"  # Reload Device Info is reading the device
PART_PREFIX = ".part-"  # temporary download files (see shelly._store)

# Manual firmware upload: the file is first staged (temporary name below
# FW_DIR, removed at start like an incomplete download), then confirmed with type
# and version. Only one upload is staged at a time.
UPLOAD_PREFIX = PART_PREFIX + "upload-"
UPLOAD_TTL_S = 900  # a staged upload that is not confirmed within 15 min is dropped
MAX_MANIFEST = 64 * 1024  # upper limit for a manifest.json read from an uploaded ZIP
STABLE_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")  # stable Gen2+ versions only
SOURCE_DOWNLOAD = "download"  # downloaded from the manufacturer by a check
SOURCE_UPLOAD = "upload"  # uploaded manually in the Firmware tab

# Where the update provided for a device comes from (device field offer_source)
OFFER_MANUFACTURER = "manufacturer"  # offered by the manufacturer's server in this check
OFFER_STORE = "store"  # stored download of an earlier check, server not asked
OFFER_UPLOAD = "upload"  # manually uploaded file from the store, server not asked
OFFER_MESSAGES = {
    OFFER_MANUFACTURER: "Provided, not installed",
    OFFER_STORE: "Provided from the firmware store, not installed",
    OFFER_UPLOAD: "Provided from the firmware store (uploaded file), not installed",
}

UI_TABLES = ("devices",)  # tables whose sort order/columns are stored
UI_VERSION = 3  # table settings format since 3.0 (older formats are dropped, not migrated)
COLUMN_RE = re.compile(r"^[a-z_]{1,32}$")
MAX_COLUMNS = 30

# Parallel device queries of one full check. Each Shelly is a small embedded HTTP
# server that is only asked by one request at a time; 4 devices in parallel cut
# the check time of larger installations by roughly a factor of 4 while keeping
# Wi-Fi and Home Assistant load low. Internal constant, not a setting.
CHECK_PARALLEL = 4
# Check after confirmed installations: it needs the check slot, which a
# Reload Device Info or a check started in the same moment may hold for a few seconds
FOLLOWUP_ATTEMPTS = 6
FOLLOWUP_RETRY_S = 5
PASSWORD_MODES = ("store", "enable", "change", "remove", "forget")
MAX_SERVED_IPS = 16  # download diagnosis: client addresses recorded per file

# download_base_url problems that block installations
DOWNLOAD_BASE_MESSAGES = {
    "missing": "Installations are disabled: set the download base URL (LAN IP address of Home "
    "Assistant and port 8098) in the Settings tab.",
    "unconfirmed": "Installations are disabled: the download base URL is still the former example "
    f"value {app_options.LEGACY_DOWNLOAD_BASE}. Change it in the Settings tab or confirm that it "
    "really is the address of Home Assistant.",
}


def severity_of(err: BaseException) -> str:
    """Log severity of a device error: authentication and internal errors are
    errors; unreachable devices, timeouts and temporary/missing data warnings."""
    if isinstance(err, AuthError) or not isinstance(err, (TimeoutError, ShellyError, aiohttp.ClientError, OSError)):
        return "error"
    return "warning"


class Manager:
    """Holds the state of all Shellys and controls checks, storage and installation."""

    def __init__(
        self,
        options: dict[str, Any],
        session: aiohttp.ClientSession,
        token: str,
        pins: CertificatePins | None = None,
    ) -> None:
        """Apply the options and load the stored state, passwords, changelogs and log."""
        options = app_options.with_defaults(options)
        self.options = options  # complete option set, needed to save changes
        self.check_days = int(options["check_interval_days"])
        self.check_time = options["check_time"]
        self.tz, self.tz_name = schedule.local_zone()
        # Optional, no default: installations are refused while it is unset
        self.download_base = (options.get("download_base_url") or "").rstrip("/") or None
        self.download_base_confirmed: str | None = None  # confirmed legacy example value (persisted)
        self.restrict_downloads = bool(options["restrict_downloads_to_devices"])
        self.gen1_allowed = frozenset(options.get("gen1_allowed_types", []))
        self.gen1_enabled = bool(options["gen1_updates_enabled"])
        self.session = session
        self.token = token
        self.pins = pins  # certificate pins for shelly.cloud
        self.lock = asyncio.Lock()  # check (exclusive)
        self.install_slots = asyncio.Semaphore(int(options.get("max_parallel_installs", 5)))
        self.installing: set[str] = set()  # device IDs: running or waiting
        self.last_scan: int | None = None
        self.devices: dict[str, Device] = {}  # HA device ID -> state
        self.downloads: dict[str, dict[str, Any]] = {}  # file name -> {"served": {ip: n}, "rejected": {ip, ...}}
        self.firmware: dict[str, FirmwareMeta] = {}  # file name -> metadata of the stored firmware
        self.ui: dict[str, Any] = {}  # table settings (sort order, hidden columns) for all users
        self.activity: dict[str, str] = {}  # HA device ID -> ACTIVITY_* while a check runs
        self.progress: dict[str, Any] | None = None  # running check: {"kind", "total", "done", "errors", "phase"}
        self.storage_error: str | None = None  # last failure writing state.json (shown in the UI)
        self.schedule: dict[str, Any] = {}  # automatic check schedule (see schedule.py), persisted
        self.reloading: set[str] = set()  # device IDs whose Reload Device Info is running
        # (firmware ID, version) -> running provide task (single flight)
        self._provisions: dict[tuple[str, str], asyncio.Future[tuple[str, str, int]]] = {}
        self._downloads_running = 0  # for the progress phase of a parallel check
        # Running check only (cleared when it ends, see _end_check):
        # file name -> shared checksum verification of a stored file (single flight)
        self._verifying: dict[str, asyncio.Future[bool]] = {}
        # file name -> manufacturer URL of a firmware downloaded by this check
        self._fresh: dict[str, str] = {}
        # Devices with a confirmed installation, checked once no installation runs
        self._followup: set[str] = set()
        self._upload: dict[str, Any] | None = None  # staged manual upload (see stage_upload), memory only
        self._upload_lock = asyncio.Lock()  # one upload transfer at a time
        # Set whenever the schedule changes (check finished): wakes the scheduler in
        # main.py instead of letting it poll every minute.
        self.schedule_changed = asyncio.Event()
        FW_DIR.mkdir(parents=True, exist_ok=True)
        self._remove_partial_downloads()
        self.credentials = Credentials(CREDENTIALS_FILE)
        self.changelogs = Changelogs(CHANGELOG_FILE)
        self.events = EventLog(EVENTS_FILE, options["log_retention_days"], options["log_entry_limit"])
        self._load()
        self.schedule, changed = schedule.configure(
            self.schedule, self.check_days, self.check_time, time.time(), self.tz
        )
        if changed:  # new install or changed interval/time: anchor anew
            self.save()

    # ------------------------------------------------------------ Persistence

    def _load(self) -> None:
        """Load the state of the last run; missing or broken file = fresh start."""
        try:
            data = json.loads(STATE_FILE.read_text())
            if not isinstance(data, dict):
                raise ValueError("state is not an object")
            self.devices = data.get("devices") or {}
            self.last_scan = data.get("last_scan")
            self.firmware = data["firmware"] if isinstance(data.get("firmware"), dict) else {}
            if isinstance(data.get("ui"), dict) and data.get("ui_version") == UI_VERSION:
                self.ui = data["ui"]
            self.schedule = data["schedule"] if isinstance(data.get("schedule"), dict) else {}
            confirmed = data.get("download_base_confirmed")
            self.download_base_confirmed = confirmed if isinstance(confirmed, str) else None
        except FileNotFoundError:
            return
        except (ValueError, OSError) as err:
            log.warning("State file unreadable, starting fresh: %s", err)
            return
        for dev in self.devices.values():
            # A restart during an installation leaves an unclear state
            if dev.get("status") == STATUS_INSTALLING:
                dev.update(status=STATUS_UNCLEAR, message="App was restarted during the installation")
            dev.pop("downgrade", None)  # marker of a running downgrade, memory semantics

    @staticmethod
    def _remove_partial_downloads() -> None:
        """Delete temporary download files left behind by a crash or restart.
        They are never offered for installation (only <sha256>.<ext> names are)."""
        for path in FW_DIR.glob(PART_PREFIX + "*"):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
                log.info("Removed incomplete download %s", path.name)

    def save(self) -> bool:
        """Write the state atomically (temporary file first, then rename).

        A write error (e.g. full disk) keeps the previous state file intact, is
        logged and shown in the UI – it must not abort a check or installation."""
        temp = STATE_FILE.with_suffix(".tmp")
        state = {
            "devices": self.devices,
            "last_scan": self.last_scan,
            "firmware": self.firmware,
            "ui": self.ui,
            "ui_version": UI_VERSION,
            "schedule": self.schedule,
            "download_base_confirmed": self.download_base_confirmed,
        }
        try:
            temp.write_text(json.dumps(state, separators=(",", ":")))
            temp.replace(STATE_FILE)
        except OSError as err:
            temp.unlink(missing_ok=True)
            self.storage_error = clip(f"State could not be saved: {err}", 300)
            log.error("%s", self.storage_error)
            return False
        self.storage_error = None
        return True

    def set_ui(self, prefs: dict[str, Any]) -> None:
        """Store table settings shared by all users. Raises ValueError on bad input."""
        clean: dict[str, Any] = {}
        for table in UI_TABLES:
            entry = prefs.get(table)
            if entry is None:
                continue
            sort, hidden, order = (
                (entry.get("sort"), entry.get("hidden"), entry.get("order", []))
                if isinstance(entry, dict)
                else (None, None, None)
            )
            valid_sort = (
                isinstance(sort, list)
                and len(sort) == 2
                and isinstance(sort[0], str)
                and COLUMN_RE.match(sort[0])
                and sort[1] in ("asc", "desc")
            )
            valid_lists = all(isinstance(x, list) and len(x) <= MAX_COLUMNS for x in (hidden, order))
            if not valid_sort or not valid_lists or hidden is None or order is None:
                raise ValueError(f"Invalid table settings for {table}")
            if not all(isinstance(c, str) and COLUMN_RE.match(c) for c in [*hidden, *order]):
                raise ValueError(f"Invalid column name for {table}")
            clean[table] = {"sort": sort, "hidden": sorted(set(hidden)), "order": list(dict.fromkeys(order))}
        self.ui = clean
        self.save()

    # ------------------------------------------------------------ Firmware store

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
        """Files a device currently holds as update or is installing (incl. downgrades)."""
        return {d["file"] for d in self.devices.values() if d.get("status") in PENDING and d.get("file")}

    def cleanup_firmware(self) -> None:
        """Housekeeping of the store – never deletes a registered firmware.

        Store entries whose file is missing are dropped; files on disk that are not
        in the store are removed. Temporary files (running downloads and staged
        uploads, PART_PREFIX) are left alone: they are removed by their own code
        path or at the next start."""
        self.firmware = {n: m for n, m in self.firmware.items() if (FW_DIR / n).is_file()}
        for path in FW_DIR.iterdir():
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
            return shelly.file_sha256(FW_DIR, name) == sha
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

    async def _stored_firmware(self, fw_type: str, version: str) -> tuple[str, FirmwareMeta] | None:
        """Already downloaded firmware for exactly this ID and version – after checksum
        verification. Used for a version offered by the manufacturer's server, so
        only files downloaded from the manufacturer count here."""
        for name, meta in list(self.firmware.items()):
            if (
                meta.get("type") == fw_type
                and meta.get("version") == version
                and self._source(meta) == SOURCE_DOWNLOAD
                and await self._intact(name, meta)
            ):
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
        """Reason why a stored firmware cannot be deleted now, else None."""
        if self.checking:
            return "Locked during a check"
        holders = [d for d in self.devices.values() if d.get("file") == name]
        installing = {self.devices.get(i, {}).get("file") for i in self.installing}
        if name in installing or any(d.get("status") == STATUS_INSTALLING for d in holders):
            return "Locked: being installed"
        if any(d.get("status") == STATUS_READY for d in holders):
            return "Locked: provided as pending update"
        return None

    def firmware_list(self) -> list[dict[str, Any]]:
        """Stored firmwares for the "Firmware" tab."""
        users: dict[str, list[dict[str, str]]] = {}  # file name -> devices it is provided for
        for device_id, dev in self.devices.items():
            if dev.get("status") in PENDING and dev.get("file"):
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

        Refused during a check and for a file that is installed right now or held
        as pending update for a device (the device must be checked or installed
        first). Devices that may later need it download it again at a check."""
        if name not in self.firmware:
            raise KeyError(name)
        lock = self._delete_lock(name)
        if lock:
            raise ShellyError(lock.replace("Locked", "Firmware cannot be deleted", 1))
        if confirm is not True or acknowledged is not True:
            raise ValueError("Deletion not confirmed")
        meta = self.firmware.pop(name)
        (FW_DIR / name).unlink(missing_ok=True)
        log.info("Firmware %s deleted by %s", name, user)
        self.events.add(
            "delete",
            type=meta.get("type"),
            version=meta.get("version"),
            sha256=meta.get("sha256"),
            source=self._source(meta),
            user=user,
        )
        self.save()

    # ------------------------------------------------------------ Downgrade

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
            if parsed >= installed or parsed[3] != 3 or not (FW_DIR / name).is_file():
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
            if parsed[3] == 3 and (FW_DIR / name).is_file():
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
        if self.checking:
            return "Check in progress – please try again afterwards"
        if self.download_base_issue:
            return DOWNLOAD_BASE_MESSAGES[self.download_base_issue]
        if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
            return "Installation for this device is already running"
        if device_id in self.reloading:
            return "Device info is being reloaded – please try again in a moment"
        if not dev.get("origin") or not dev.get("build") or not dev.get("mac"):
            return "Device has not been checked successfully yet"
        return None

    # ------------------------------------------------------------ Manual upload

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
            path = FW_DIR / f"{UPLOAD_PREFIX}{secrets.token_hex(8)}"
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
        staged["path"].replace(FW_DIR / name)  # same directory: atomic
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

    # ------------------------------------------------------------ Home Assistant

    async def _registry(self) -> list[dict[str, Any]]:
        """Device registry via the Core WebSocket API (through the Supervisor proxy)."""
        ws_url = SUPERVISOR.replace("http", "ws", 1) + "/core/websocket"
        async with self.session.ws_connect(ws_url, timeout=aiohttp.ClientWSTimeout(ws_close=30)) as ws:
            if (await ws.receive_json(timeout=30)).get("type") != "auth_required":
                raise ShellyError("Unexpected WebSocket greeting")
            await ws.send_json({"type": "auth", "access_token": self.token})
            if (await ws.receive_json(timeout=30)).get("type") != "auth_ok":
                raise ShellyError("Authentication with Home Assistant failed")
            await ws.send_json({"id": 1, "type": "config/device_registry/list"})
            while True:
                msg = await ws.receive_json(timeout=30)
                if msg.get("id") == 1:
                    if not msg.get("success"):
                        raise ShellyError(clip(msg.get("error")))
                    result = msg["result"]
                    return result if isinstance(result, list) else []

    async def notify(self, title: str, message: str, notification_id: str) -> None:
        """Persistent notification in Home Assistant (errors are only logged)."""
        try:
            async with self.session.post(
                f"{SUPERVISOR}/core/api/services/persistent_notification/create",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"title": title, "message": message, "notification_id": notification_id},
                timeout=SUPERVISOR_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
        except (TimeoutError, aiohttp.ClientError) as err:
            log.warning("Notification failed: %s", err)

    async def _supervisor_post(self, path: str, body: dict[str, Any] | None) -> None:
        """POST to the Supervisor API (only /addons/self/* is used); errors -> ShellyError."""
        try:
            async with self.session.post(
                f"{SUPERVISOR}{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=body,
                timeout=SUPERVISOR_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    try:
                        detail = (await resp.json(content_type=None)).get("message")
                    except (ValueError, AttributeError):
                        detail = None
                    raise ShellyError(f"Supervisor rejected the request: {clip(detail or resp.status, 300)}")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise ShellyError(f"Supervisor not reachable: {err or type(err).__name__}") from None

    # ------------------------------------------------------------ App options

    def options_view(self) -> dict[str, Any]:
        """App options for the Settings tab (secret only as set/not set)."""
        return app_options.public_view(self.options)

    async def save_options(self, changes: object, user: str | None = None) -> list[str]:
        """Validate and save via the Supervisor; the caller restarts the app.

        The attempt is written to the activity log with the changed keys – values
        only for non-secret options. Returns the list of changed keys (empty:
        nothing saved, no restart)."""
        new, changed = app_options.merge(self.options, changes)
        if not changed:
            return []
        if self.busy:
            raise ShellyError(f"Not possible – {self.busy}")
        diff = app_options.change_list(self.options, new, changed)
        try:
            await self._supervisor_post("/addons/self/options", {"options": new})
        except ShellyError as err:
            self.events.add("options", ok=False, changes=diff, user=user, error=error_text(err, 300))
            raise
        log.info("App options changed by %s (%s) – restarting", user, ", ".join(changed))
        self.events.add("options", changes=diff, user=user)
        self.options = new
        if "download_base_url" in changed and self.download_base_confirmed is not None:
            self.download_base_confirmed = None  # a confirmation never outlives a change
            self.save()
        return changed

    # ------------------------------------------------------------ Download base URL

    @property
    def download_base_issue(self) -> str | None:
        """Why installations are blocked by download_base_url, else None.

        "missing": not set (optional option without default). "unconfirmed": still the
        former example value, which is only accepted after an explicit confirmation –
        it may really be the address of Home Assistant (FritzBox default network)."""
        if not self.download_base:
            return "missing"
        legacy = app_options.LEGACY_DOWNLOAD_BASE.rstrip("/")
        if self.download_base == legacy and self.download_base_confirmed != legacy:
            return "unconfirmed"
        return None

    def confirm_download_base(self, url: object, user: str | None = None) -> None:
        """Confirm that the former example value really is the address of Home
        Assistant. url must be the current value (no confirmation of a stale view).
        Stored in state.json; no option change, no restart. Logged."""
        current = isinstance(url, str) and url.rstrip("/") == self.download_base
        if self.download_base_issue != "unconfirmed" or not current:
            raise ShellyError("Nothing to confirm – the download base URL has changed, reload the page")
        self.download_base_confirmed = self.download_base
        self.save()
        log.info("Download base URL %s confirmed by %s", self.download_base, user)
        self.events.add(
            "options",
            confirmed=True,
            changes=[{"option": "download_base_url", "old": "not confirmed", "new": "confirmed"}],
            user=user,
        )

    async def restart_app(self) -> None:
        """Ask the Supervisor to restart this app so new options take effect."""
        await self._supervisor_post("/addons/self/restart", None)

    # ------------------------------------------------------------ Check

    @property
    def checking(self) -> bool:
        """True while a check runs or has been reserved by a request (see reserve_check)."""
        return self.lock.locked() or self.progress is not None

    def reserve_check(self, kind: str = "all", device_id: str | None = None) -> None:
        """Reserve the check slot synchronously, before the background task starts.

        Two quick requests (double tap, two browsers) can therefore never start two
        checks, and an approval arriving in between is refused. Raises ShellyError
        if a check or an installation is running."""
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        if self.installing:
            raise ShellyError(f"Not possible – installations running: {len(self.installing)}")
        if self.reloading:
            raise ShellyError("Device info is being reloaded – please try again in a moment")
        if kind == "device":
            if device_id is None:
                raise ValueError("device_id required for a single-device check")
            self.progress = {
                "kind": "device",
                "device": self.devices[device_id].get("name"),
                "total": 1,
                "done": 0,
                "errors": 0,
                "phase": "queued",
            }
            self.activity[device_id] = ACTIVITY_WAITING
        else:  # "all" or "followup" (check after installations)
            self.progress = {"kind": kind, "total": 0, "done": 0, "errors": 0, "phase": "queued"}

    def _reserved(self, kind: str, device_id: str | None = None) -> bool:
        """True if the slot was reserved for exactly this check and it has not started."""
        p = self.progress
        return (
            not self.lock.locked()
            and p is not None
            and p.get("kind") == kind
            and p.get("phase") == "queued"
            and (device_id is None or self.activity.get(device_id) == ACTIVITY_WAITING)
        )

    @property
    def busy(self) -> str | None:
        """Text for the UI while a check or installations are running."""
        if self.checking:
            if self.progress and self.progress.get("kind") == "device":
                return f"Checking {self.progress.get('device') or 'device'}"
            return "Check in progress"
        if self.installing:
            return f"Installations running: {len(self.installing)}"
        return None

    def scan_due_in(self, now: float) -> float:
        """Seconds until the next automatic check; 0 = due now (see schedule.py).

        Only the schedule counts: neither an app update, a manual check nor a
        Reload Device Info shifts it."""
        return schedule.seconds_until(self.schedule, now, self.tz)

    async def scan(self, automatic: bool = False, user: str | None = None) -> bool:
        """Check all Shellys. Returns False if a check or installation is running.
        Automatic checks report newly found updates as a notification in HA and
        record their scheduled date when they start (one attempt per date).
        Every check is written to the activity log (user only for manual checks)."""
        if not self._reserved("all"):
            try:
                self.reserve_check("all")
            except ShellyError:
                return False
        if automatic:
            due, _ = schedule.status(self.schedule, time.time(), self.tz)
            if due:
                self.schedule = {**self.schedule, "last_date": due.isoformat()}
        async with self.lock:
            started = time.monotonic()
            failure: str | None = None
            checked: list[str] = []
            self._progress()["phase"] = "registry"
            try:
                checked = await self._scan_all()
                self._progress()["phase"] = "changelog"
                await self._store_changelogs(self.devices.values())
            except Exception as err:  # report overall errors (e.g. registry), do not crash
                log.exception("Check failed")
                failure = error_text(err, 500) or type(err).__name__
                await self.notify("Shelly firmware check failed", failure, "sfm_summary")
            finally:
                # Also runs on cancellation (app stop): no device stays "checking"
                self._end_check()
                self.last_scan = int(time.time())
                self.cleanup_firmware()
                self.save()
                self.schedule_changed.set()
            self._log_check(checked, automatic, user, time.monotonic() - started, failure)
        if automatic:
            await self._notify_new_updates()
        return True

    def _log_check(
        self,
        checked: list[str],
        automatic: bool,
        user: str | None,
        duration: float,
        failure: str | None,
        trigger: str | None = None,
    ) -> None:
        """One log entry per check; findings = devices with an update, a warning or
        an error. trigger overrides the derived value ("device" for single-device
        checks, "install" for the check after installations, which has no user).
        An update finding names its source (OFFER_*: manufacturer, store or upload).
        Every finding carries a severity; "error" holds the message of warnings
        and errors (key kept for historic entries). The entry gets the highest
        severity of its findings, an overall failure is an error."""
        findings: list[dict[str, Any]] = []
        for device_id in checked:
            dev = self.devices.get(device_id, {})
            if dev.get("status") == STATUS_ERROR:
                level = dev.get("error_level") or "error"
                findings.append({"device": dev.get("name"), "error": dev.get("message"), "severity": level})
                continue
            if dev.get("status") in (STATUS_READY, STATUS_AVAILABLE):
                findings.append(
                    {
                        "device": dev.get("name"),
                        "version": dev.get("version"),
                        "offer": dev.get("offer_version"),
                        "source": dev.get("offer_source") or OFFER_MANUFACTURER,
                        "severity": "success",
                    }
                )
            problem = dev.get("settings_error")
            if problem:  # firmware checked, device settings/status not readable
                findings.append(
                    {
                        "device": dev.get("name"),
                        "error": f"Device status: {problem['message']}",
                        "severity": problem["severity"],
                    }
                )
        counts = {level: sum(1 for f in findings if f["severity"] == level) for level in ("warning", "error")}
        updates = sum(1 for f in findings if "offer" in f)
        self.events.add(
            "check",
            severity=worst("error" if failure else "success", *(f["severity"] for f in findings)),
            trigger=trigger or ("scheduled" if automatic else "manual"),
            user=None if automatic or trigger == "install" else user or "unknown",
            devices=len(checked),
            duration=round(duration, 1),
            result=failure or f"{updates} update(s), {counts['warning']} warning(s), {counts['error']} error(s)",
            updates=updates,
            warnings=counts["warning"],
            errors=counts["error"],
            findings=findings,
        )

    async def _store_changelogs(self, devices: Iterable[Device]) -> None:
        """Keep the changelog of every offered firmware with its store entry.

        A changelog page is requested only if a firmware provided for one of the
        devices has no changelog yet – once per check and generation, never with
        device data, and not again within an hour if the page does not list the
        version (see Changelogs.refresh). A stored changelog is never requested
        again."""
        self._attach_changelogs()  # from the page fetched last: no request
        missing = set()
        for dev in devices:
            meta = self.firmware.get(str(dev.get("file"))) if dev.get("status") == STATUS_READY else None
            if meta is not None and "changelog" not in meta:
                missing.add(1 if meta.get("gen") == 1 else 2)
        if missing:
            await self.changelogs.refresh(self.session, missing, self.pins)
            self._attach_changelogs()

    def _attach_changelogs(self) -> None:
        """Copy the changelog of every stored firmware that has none yet from the
        page fetched last, as far as that page lists the version (no request)."""
        for meta in self.firmware.values():
            if "changelog" not in meta:
                entry = self.changelogs.version_entry(meta.get("gen"), meta.get("version"))
                if entry is not None:
                    meta["changelog"] = entry

    def changelog_for(self, device_id: str) -> list[dict[str, Any]] | None:
        """Changelog entries between the installed and the offered version (None:
        unknown). The offered version is taken from the changelog kept with the
        provided firmware, versions in between from the page fetched last."""
        dev = self.devices[device_id]
        if not dev.get("offer_version") or not dev.get("version"):
            return None
        meta = self.firmware.get(str(dev.get("file"))) if dev.get("status") in PENDING else None
        return self.changelogs.between(
            dev.get("gen"), dev["version"], dev["offer_version"], dev.get("type"), (meta or {}).get("changelog")
        )

    def _end_check(self) -> None:
        """Clear the memory-only state of a finished or cancelled check."""
        self.activity.clear()
        self.progress = None
        self._verifying.clear()
        self._fresh.clear()

    async def _scan_all(self) -> list[str]:
        """Check all Shelly main devices from the HA device registry.
        Returns the IDs of the checked devices."""
        offers: OfferTasks = {}  # shared manufacturer requests: one per firmware ID
        entries = self._shelly_entries(await self._registry())
        for dev in self.devices.values():  # A full check ends the "just updated" display
            dev.pop("updated", None)
        # Ignored devices count as registered (seen) but are not contacted
        seen = set(entries)
        for device_id in seen & set(self.devices):
            if self.devices[device_id].get("ignored"):
                entry = entries.pop(device_id)
                self.devices[device_id]["name"] = clip(entry.get("name_by_user") or entry.get("name") or device_id, 100)
        # Every device of this check is "waiting" until it is reached
        progress = self._progress()
        progress.update(total=len(entries), phase="devices")
        self.activity.update(dict.fromkeys(entries, ACTIVITY_WAITING))
        await self._check_entries(entries, offers)
        for device_id, dev in self.devices.items():
            if device_id not in seen:
                dev.update(status=STATUS_UNREGISTERED, message="")
        return sorted(entries)

    async def _check_entries(self, entries: dict[str, dict[str, Any]], offers: OfferTasks) -> None:
        """Check the given registry entries, up to CHECK_PARALLEL at the same time,
        and count the results in the progress of the running check."""
        progress = self._progress()
        slots = asyncio.Semaphore(CHECK_PARALLEL)

        async def check(device_id: str, entry: dict[str, Any]) -> None:
            """Check one device within the parallel limit and count the result."""
            async with slots:
                ok = await self._check_entry(device_id, entry, offers)
            if not ok:
                progress["errors"] += 1
            progress["done"] += 1

        # TaskGroup: a cancelled check (app stop) cancels every device query
        async with asyncio.TaskGroup() as group:
            for device_id, entry in entries.items():
                group.create_task(check(device_id, entry))

    def _shelly_entries(self, registry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Shelly main devices from the registry, ordered by name: {device ID: entry}.

        Sub-devices (channels of e.g. Pro 3EM or Strip) carry via_device_id; only the
        main device has firmware of its own – stale sub-device entries are removed."""
        entries: dict[str, dict[str, Any]] = {}
        for entry in registry:
            is_shelly = any(i and i[0] == "shelly" for i in entry.get("identifiers", []))
            if not is_shelly or entry.get("disabled_by"):
                continue
            if entry.get("via_device_id"):
                self.devices.pop(entry["id"], None)
                self.credentials.remove(entry["id"])
                continue
            entries[entry["id"]] = entry

        def label(i: str) -> str:
            """Sort key: the name shown in HA (user-given name first)."""
            return str(entries[i].get("name_by_user") or entries[i].get("name") or i).lower()

        order = sorted(entries, key=label)
        return {i: entries[i] for i in order}

    async def _check_entry(self, device_id: str, entry: dict[str, Any], offers: OfferTasks) -> bool:
        """Check one registry entry, keep its result on the device. True on success.

        last_check = last attempt, last_success = last successful check; a failed
        attempt keeps the last known firmware so it is never shown as freshly checked."""
        dev = self.devices.setdefault(device_id, {})
        dev["name"] = clip(entry.get("name_by_user") or entry.get("name") or device_id, 100)
        dev["last_check"] = int(time.time())
        self.activity[device_id] = ACTIVITY_CHECKING
        try:
            await self._scan_device(device_id, dev, entry.get("configuration_url"), offers)
        except (TimeoutError, ShellyError, aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            log.warning("%s: %s", dev["name"], err)
            dev.update(
                status=STATUS_ERROR, message=error_text(err, 500) or type(err).__name__, error_level=severity_of(err)
            )
            return False
        else:
            dev["last_success"] = dev["last_check"]
            return True
        finally:
            self.activity.pop(device_id, None)

    async def check_device(self, device_id: str, user: str | None = None) -> dict[str, Any]:
        """Check a single device without starting the full check.

        Same steps as the full check for this one device (device info, firmware
        store first, else offer and download of missing firmware) – never an
        installation, a restart or a settings change. The time of the last full
        check (last_scan) and the schedule stay unchanged. Raises ShellyError if a
        check or an installation is running."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        if not self._reserved("device", device_id):
            self.reserve_check("device", device_id)
        async with self.lock:
            dev = self.devices[device_id]
            dev.pop("updated", None)  # Checked again by the user, no longer "just updated"
            started = time.monotonic()
            failure = None
            progress = self._progress()
            try:
                progress["phase"] = "registry"
                entry = self._shelly_entries(await self._registry()).get(device_id)
                if entry is None:
                    dev.update(status=STATUS_UNREGISTERED, message="")
                    failure = "Device is no longer in the Home Assistant device registry"
                else:
                    progress["phase"] = "devices"
                    if not await self._check_entry(device_id, entry, {}):
                        progress["errors"] = 1
                    progress["done"] = 1
                    if dev.get("status") == STATUS_READY:
                        progress["phase"] = "changelog"
                        await self._store_changelogs([dev])
            except Exception as err:  # registry failure etc.: report, do not crash
                log.exception("Device check failed")
                failure = error_text(err, 500) or type(err).__name__
            finally:
                self._end_check()
                self.cleanup_firmware()
                self.save()
            self._log_check(
                [device_id], False, user, time.monotonic() - started, failure, trigger="device"
            )
        return {"status": dev.get("status"), "error": failure}

    async def _check_after_installs(self) -> None:
        """Check the devices whose installation was confirmed, so that the
        next stored version – or the manufacturer's offer – is provided at once.

        Runs when the last running installation has ended (a check cannot start
        earlier): one check for all devices installed in the meantime, with the same
        steps as any other check and its own log entry. Never after a downgrade or
        an unconfirmed installation. The time of the last full check (last_scan)
        and the schedule stay unchanged."""
        for _ in range(FOLLOWUP_ATTEMPTS):
            if self.installing or not self._followup:
                return  # the installation ending last starts the check / nothing to do
            try:
                self.reserve_check("followup")
                break
            except ShellyError:  # the slot is held for a moment (e.g. Reload Device Info)
                await asyncio.sleep(FOLLOWUP_RETRY_S)
        else:
            log.info("Check after installation skipped: the check slot stayed busy")
            self._followup.clear()
            return
        wanted, self._followup = sorted(self._followup), set()
        async with self.lock:
            started = time.monotonic()
            failure: str | None = None
            checked: list[str] = []
            progress = self._progress()
            try:
                progress["phase"] = "registry"
                registry = self._shelly_entries(await self._registry())
                entries = {i: registry[i] for i in wanted if i in registry and i in self.devices}
                for device_id in wanted:
                    if device_id in self.devices and device_id not in registry:
                        self.devices[device_id].update(status=STATUS_UNREGISTERED, message="")
                progress.update(total=len(entries), phase="devices")
                self.activity.update(dict.fromkeys(entries, ACTIVITY_WAITING))
                await self._check_entries(entries, {})
                checked = sorted(entries)
                progress["phase"] = "changelog"
                await self._store_changelogs([self.devices[i] for i in checked])
            except Exception as err:  # registry failure etc.: report, do not crash
                log.exception("Check after installation failed")
                failure = error_text(err, 500) or type(err).__name__
            finally:
                self._end_check()
                self.cleanup_firmware()
                self.save()
            self._log_check(checked, False, None, time.monotonic() - started, failure, trigger="install")

    async def _scan_device(
        self, device_id: str, dev: Device, configuration_url: str | None, offers: OfferTasks
    ) -> None:
        """Check one device and provide the new firmware if there is one."""
        origin = await shelly.resolve_device_origin(configuration_url)
        dev.update(origin=origin)
        info = await shelly.device_info(
            self.session,
            origin,
            self._auth_for(device_id),
            lambda gen, sid: dev.update(gen=gen, shelly_id=sid),
        )
        self._apply_info(dev, info)
        await self._read_settings(device_id, dev, extended=True)

        if info["gen"] == 1 and not self.gen1_enabled:
            dev.update(
                status=STATUS_SKIPPED,
                offer_version=None,
                offer_source=None,
                message="Gen1 updates are disabled in the settings",
                file=None,
                sha256=None,
            )
            return
        if info["gen"] == 1 and info["type"] not in self.gen1_allowed:
            raise ShellyError(f"Gen1 type {info['type']} is not in the list of allowed types")

        # The firmware store comes first. The next higher stored version is
        # provided – also instead of an update provided before – and the
        # manufacturer's server is not asked for this device.
        stored = await self._store_offer(info)
        if stored:
            name, meta = stored
            # A file downloaded by this very check is the manufacturer's current offer
            fresh_url = self._fresh.get(name)
            if fresh_url:
                source = OFFER_MANUFACTURER
            else:
                source = OFFER_UPLOAD if self._source(meta) == SOURCE_UPLOAD else OFFER_STORE
            dev.update(
                status=STATUS_READY,
                offer_version=meta["version"],
                offer_source=source,
                file=name,
                sha256=meta["sha256"],
                size=meta["size"],
                pending_version=meta["version"],
                pending_build=info["build"],
                source_url=fresh_url,
                message=OFFER_MESSAGES[source],
            )
            return

        offer = await self._manufacturer_offer(info, offers)
        dev["offer_version"] = offer["version"] if offer else None
        if not offer or shelly.parse_version(offer["version"]) <= shelly.parse_version(info["version"]):
            dev.update(
                status=STATUS_CURRENT,
                offer_source=None,
                message="" if offer else "No stable offer",
                file=None,
                sha256=None,
            )
            return

        # Already provided and device unchanged -> download nothing again
        already = (
            dev.get("status") == STATUS_READY
            and dev.get("pending_version") == offer["version"]
            and dev.get("pending_build") == info["build"]
            and (FW_DIR / str(dev.get("file"))).is_file()
        )
        if already:
            dev.update(offer_source=OFFER_MANUFACTURER, message=OFFER_MESSAGES[OFFER_MANUFACTURER])
            return

        # One provide task per firmware ID and version: a parallel device of the same
        # type waits for it instead of downloading the file a second time
        fw_key = (info["type"], offer["version"])
        task = self._provisions.get(fw_key)
        if task is None:
            task = self._provisions[fw_key] = asyncio.ensure_future(self._provide(device_id, dev, info, offer))
            task.add_done_callback(functools.partial(self._provision_done, fw_key))
        name, sha, size = await task
        dev.update(
            status=STATUS_READY,
            offer_source=OFFER_MANUFACTURER,
            file=name,
            sha256=sha,
            size=size,
            pending_version=offer["version"],
            pending_build=info["build"],
            source_url=clip(offer["url"], 500),
            message=OFFER_MESSAGES[OFFER_MANUFACTURER],
        )

    async def _manufacturer_offer(self, info: dict[str, Any], offers: OfferTasks) -> dict[str, str] | None:
        """Stable offer of the manufacturer's server for the device's firmware ID.

        One request per firmware ID and check: parallel devices of the same type
        await the same request (single flight). Gen1: the manufacturer serves one
        list for all types, so it is fetched once per check for all of them."""
        if info["gen"] >= 2:
            key = (int(info["gen"]), str(info["type"]))
            if key not in offers:
                offers[key] = asyncio.ensure_future(shelly.fetch_offer(self.session, info["type"], self.pins))
            offer: dict[str, str] | None = await offers[key]
            return offer
        if GEN1_OFFERS not in offers:
            offers[GEN1_OFFERS] = asyncio.ensure_future(shelly.fetch_gen1_offers(self.session, self.pins))
        return shelly.gen1_offer(await offers[GEN1_OFFERS], info["type"])

    def _provision_done(self, fw_key: tuple[str, str], _task: "asyncio.Future[Any]") -> None:
        """Forget a finished provide task (single flight per firmware ID and version)."""
        self._provisions.pop(fw_key, None)

    async def _provide(
        self, device_id: str, dev: Device, info: dict[str, Any], offer: dict[str, str]
    ) -> tuple[str, str, int]:
        """The firmware offered by the manufacturer's server: an already downloaded
        file of exactly this version (after checksum verification) or a new download.
        Returns (file name, sha256, size)."""
        stored = await self._stored_firmware(info["type"], offer["version"])
        if stored:
            name, meta = stored
            log.info("%s: using stored firmware %s %s", dev["name"], info["type"], offer["version"])
            return name, str(meta["sha256"]), int(meta["size"])
        self.activity[device_id] = ACTIVITY_DOWNLOADING
        self._downloads_running += 1
        if self.progress:
            self.progress["phase"] = "download"
        try:
            name, sha, size, https = await shelly.download_firmware(
                self.session, offer["url"], FW_DIR, self.pins, https_only=info["gen"] >= 2
            )
        finally:
            self._downloads_running -= 1
            if self.progress and not self._downloads_running:
                self.progress["phase"] = "devices"
            if self.activity.get(device_id) == ACTIVITY_DOWNLOADING:
                self.activity[device_id] = ACTIVITY_CHECKING
        self._register_firmware(name, info["type"], info["gen"], offer["version"], sha, size, https)
        self._fresh[name] = clip(offer["url"], 500)  # later devices of this check: same offer
        self.events.add("download", type=info["type"], version=offer["version"], size=size, sha256=sha, https=https)
        return name, sha, size

    @staticmethod
    def _apply_info(dev: Device, info: dict[str, Any]) -> None:
        """Take over the identity read from the device; a changed version (e.g.
        update via the Shelly app) sets last_update."""
        if dev.get("version") and dev["version"] != info["version"]:
            dev["last_update"] = int(time.time())
        dev.update(**info)

    async def _notify_new_updates(self) -> None:
        """One HA notification if an automatic check found versions not reported before.
        Lists all open updates so that a replaced notification loses nothing."""
        open_updates = [
            d for d in self.devices.values() if d.get("status") in (STATUS_READY, STATUS_AVAILABLE)
        ]
        new = [d for d in open_updates if d.get("notified_version") != d.get("offer_version")]
        if not new:
            return
        for dev in new:
            dev["notified_version"] = dev.get("offer_version")
        self.save()
        lines = "\n".join(
            f"- {d.get('name')}: {d.get('version')} → {d.get('offer_version')}"
            for d in sorted(open_updates, key=lambda d: (d.get("name") or "").lower())
        )
        await self.notify(
            f"Shelly firmware: {len(new)} new update(s) found",
            f"{lines}\n\nReview and install in the “Shelly Firmware” panel.",
            "sfm_updates",
        )

    # ------------------------------------------------------------ Device settings

    def _auth(self, device_id: str, dev: Device) -> DeviceAuth | None:
        """Stored credentials of a device, else None (there is no shared password)."""
        return self.credentials.auth_for(device_id, dev.get("gen") or 1)

    def _auth_for(self, device_id: str) -> Callable[[int], DeviceAuth | None]:
        """Credentials lookup by generation, for requests that detect the generation."""
        return lambda gen: self.credentials.auth_for(device_id, gen)

    async def _read_settings(self, device_id: str, dev: Device, extended: bool = False) -> bool:
        """Read settings and feature states (see shelly.read_settings). True on success.

        A failure must not stop the firmware check: the old values stay, but
        settings_error marks them as unknown (shown as such, logged as warning or
        error) – a failed read-back is never reported as confirmed."""
        try:
            dev.update(
                await shelly.read_settings(
                    self.session, dev["gen"], dev["origin"], dev["type"], self._auth(device_id, dev), extended
                )
            )
        except (TimeoutError, ShellyError, aiohttp.ClientError, KeyError, ValueError) as err:
            log.warning("%s: reading settings failed: %s", dev.get("name"), err)
            message = error_text(err, 300) or type(err).__name__
            dev["settings_error"] = {"message": message, "severity": severity_of(err)}
            return False
        dev.pop("settings_error", None)
        return True

    def _settings_device(self, device_id: str) -> Device:
        """Device whose settings may be read/changed now (reachable, not installing)."""
        dev = self.devices[device_id]
        if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
            raise ShellyError("Installation in progress – please try again afterwards")
        if self.activity.get(device_id) in (ACTIVITY_CHECKING, ACTIVITY_DOWNLOADING):
            raise ShellyError("Device is being checked – please try again afterwards")
        if device_id in self.reloading:
            raise ShellyError("Device info is being reloaded – please try again in a moment")
        if not dev.get("origin") or not dev.get("gen"):
            raise ShellyError("Device has not been checked successfully yet")
        return dev

    async def refresh_settings(self, device_id: str) -> dict[str, Any]:
        """Read the current settings directly from the Shelly (before editing)."""
        dev = self._settings_device(device_id)
        settings = await shelly.read_settings(
            self.session, dev["gen"], dev["origin"], dev["type"], self._auth(device_id, dev)
        )
        dev.update(settings)
        dev.pop("settings_error", None)
        self.save()
        return settings

    def set_ignored(self, device_id: str, ignored: object) -> None:
        """Ignore or resume a device. Purely local: nothing is sent to the device and
        no check is started (an ignored device keeps its last known state)."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        if not isinstance(ignored, bool):
            raise ValueError("ignored: true or false expected")
        dev = self.devices[device_id]
        if ignored:
            dev["ignored"] = True
        else:
            dev.pop("ignored", None)
        self.save()

    async def reload_device(self, device_id: str) -> dict[str, str | None]:
        """Reload Device Info: identity, installed firmware and the settings/feature
        states the app shows – read from the device only.

        No offer, changelog or download request and no update detection: known
        update information and its check time stay; only info_refreshed is set.
        If the installed version reached the known offer, the device is shown as
        up to date (derived locally, the offer is not re-checked). Returns
        {"warning": text or None}; raises ShellyError if the device is unavailable."""
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        dev = self._settings_device(device_id)
        self.reloading.add(device_id)
        self.activity[device_id] = ACTIVITY_RELOADING
        try:
            info = await shelly.device_info(
                self.session,
                dev["origin"],
                self._auth_for(device_id),
                lambda gen, sid: dev.update(gen=gen, shelly_id=sid),
            )
            self._apply_info(dev, info)
            ok = await self._read_settings(device_id, dev, extended=True)
            if dev.get("status") in (STATUS_READY, STATUS_AVAILABLE, STATUS_UNCLEAR) and dev.get("offer_version"):
                try:
                    reached = shelly.parse_version(info["version"]) >= shelly.parse_version(dev["offer_version"])
                except ShellyError:
                    reached = False
                if reached:
                    dev.update(status=STATUS_CURRENT, file=None, sha256=None, message="Known offer installed")
            dev["info_refreshed"] = int(time.time())
        except (TimeoutError, aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            raise ShellyError(f"Device not reachable: {error_text(err, 200) or type(err).__name__}") from None
        finally:
            self.reloading.discard(device_id)
            self.activity.pop(device_id, None)
            self.cleanup_firmware()
            self.save()
        return {"warning": None if ok else f"Device status not readable: {dev['settings_error']['message']}"}

    async def change_settings(
        self, device_id: str, changes: dict[str, Any], user: str = "unknown"
    ) -> tuple[dict[str, Any], bool, bool]:
        """Validate, send to the Shelly, read back. Returns (settings, restart_required, verified).
        verified is False if the read-back failed (the change is then unconfirmed).
        Every attempt that reaches the device is written to the activity log."""
        dev = self._settings_device(device_id)
        clean = shelly.validate_settings(changes, dev.get("outputs") or [])
        if "ap" in clean and dev.get("gen") == 1:
            raise ValueError(shelly.GEN1_AP_NOTE)
        for key, label in (("ap", "an access point switch"), ("ble", "a global Bluetooth switch")):
            if key in clean and dev.get(key) == UNSUPPORTED:
                raise ValueError(f"This device/firmware has no {label}")
        diff = self._settings_diff(dev, clean)
        try:
            restart = await shelly.write_settings(
                self.session, dev["gen"], dev["origin"], clean, self._auth(device_id, dev)
            )
        except (TimeoutError, ShellyError, aiohttp.ClientError, ValueError) as err:
            error = error_text(err, 300) or type(err).__name__
            self.events.add("setting", ok=False, device=dev.get("name"), changes=diff, user=user, error=error)
            raise
        log.info("%s: settings changed by %s (%s)", dev.get("name"), user, ", ".join(sorted(clean)))
        self.events.add("setting", device=dev.get("name"), changes=diff, user=user)
        if "eco" in clean:  # the device may restart and miss the read-back
            dev["eco"] = clean["eco"]
        verified = await self._read_settings(device_id, dev)  # show what the device really stored
        self.save()
        return {k: dev.get(k) for k in ("shelly_name", "ntp", "eco", "ap", "ble", "outputs")}, restart, verified

    @staticmethod
    def _settings_diff(dev: Device, clean: dict[str, Any]) -> list[dict[str, Any]]:
        """Old -> new values of a settings change, readable for the log."""
        onoff: dict[object, str] = {True: "on", False: "off"}
        diff: list[dict[str, Any]] = []
        if "name" in clean:
            diff.append({"setting": "Shelly name", "old": dev.get("shelly_name"), "new": clean["name"]})
        if "ntp" in clean:
            diff.append({"setting": "NTP server", "old": dev.get("ntp"), "new": clean["ntp"]})
        for key, label in (("eco", "Eco mode"), ("ap", "Access point"), ("ble", "Bluetooth")):
            if key in clean:
                diff.append({"setting": label, "old": onoff.get(dev.get(key)), "new": onoff[clean[key]]})
        outputs = {o["key"]: o for o in dev.get("outputs") or []}
        for key, value in clean.get("aop", {}).items():
            old = outputs.get(key, {})
            label = old.get("name") if old.get("name") not in (None, key) else key
            diff.append(
                {"setting": f"Power-on action {clip(label, 64)}", "old": old.get("aop"), "new": value}
            )
        return diff

    async def restart_device(self, device_id: str) -> None:
        """Restart the Shelly (same restrictions as changing settings). Not logged."""
        dev = self._settings_device(device_id)
        await shelly.restart(self.session, dev["gen"], dev["origin"], self._auth(device_id, dev))
        log.info("%s: restart requested", dev.get("name"))

    async def set_device_password(
        self,
        device_id: str,
        mode: str,
        password: str | None = None,
        confirm: object = False,
        acknowledged: object = False,
        user: str | None = None,
    ) -> dict[str, Any]:
        """Password dialog. Modes:

        - store:  the device is protected; test the entered password, then store it
        - enable: the device is unprotected; set a new password on the device
        - change: the device is protected and its password is stored; set a new one
        - remove: the device is protected and its password is stored; disable it
        - forget: delete the locally stored password only (the device is untouched)

        Every mode needs confirm=True, enable/change also acknowledged=True (the
        user stored the new password). The protection status is read live from
        the device (/shelly) – stored credentials never count as protection.
        Device writes are verified afterwards; local credentials change only after
        a verified success, so an ambiguous answer never loses the recovery
        credential. Results are logged – never the password or hash."""
        if mode not in PASSWORD_MODES:
            raise ValueError("Unknown password action")
        if confirm is not True:
            raise ValueError("Confirmation required")
        if mode in ("enable", "change") and acknowledged is not True:
            raise ValueError("Confirm that the new password is stored securely")
        dev = self._settings_device(device_id)
        name = dev.get("name")
        if mode == "forget":
            if self.credentials.has_device_password(device_id):
                self.credentials.remove(device_id)
                log.info("%s: stored device password deleted by %s", name, user)
                self.events.add("password", device=name, action="forgotten", user=user)
            return {"protected": dev.get("auth_enabled")}
        gen, origin, realm = dev["gen"], dev["origin"], dev.get("shelly_id")
        try:
            result = await self._password_action(device_id, dev, mode, password, gen, origin, realm)
        except (TimeoutError, ShellyError, aiohttp.ClientError, ValueError) as err:
            error = error_text(err, 300) or type(err).__name__
            if not isinstance(err, ValueError):  # input errors never reached the device
                self.events.add("password", ok=False, device=name, action=mode, user=user, error=error)
            raise
        finally:
            self.save()
        action = {"store": "stored", "enable": "enabled", "change": "changed", "remove": "removed"}[mode]
        log.info("%s: password %s by %s", name, action, user)
        self.events.add("password", device=name, action=action, user=user)
        return result

    async def _password_action(
        self,
        device_id: str,
        dev: Device,
        mode: str,
        password: str | None,
        gen: int,
        origin: str,
        realm: str | None,
    ) -> dict[str, Any]:
        """Device part of set_device_password (see there); raises on any failure."""
        protected = await shelly.protection_status(self.session, origin)
        dev["auth_enabled"] = protected
        current = self._auth(device_id, dev)
        candidate = None
        if mode in ("store", "enable", "change"):
            candidate = self.credentials.candidate(gen, realm, password)
        if mode == "store" and candidate is not None:
            if protected is False:
                raise ShellyError("The device is not password protected – use 'Enable password protection' instead")
            if not await self._auth_works(dev, candidate):
                raise AuthError("The Shelly rejected the password")
            self.credentials.store(device_id, candidate)
            return {"protected": protected}
        if mode == "enable" and protected is not False:
            raise ShellyError(
                "The device is already password protected – store or change its password"
                if protected
                else "The device does not report its protection status"
            )
        if mode in ("change", "remove"):
            if protected is not True:
                raise ShellyError("The device is not password protected")
            if current is None:
                raise ShellyError("Store the current device password first")
        new = None if mode == "remove" else candidate
        try:
            await shelly.set_protection(self.session, gen, origin, realm, new, current)
            answer: BaseException | None = None
        except (TimeoutError, ShellyError, aiohttp.ClientError) as err:
            answer = err  # the change may still have been applied: verify below
        # Verification against the device itself
        try:
            protected = await shelly.protection_status(self.session, origin)
        except (TimeoutError, ShellyError, aiohttp.ClientError):
            protected = None
        dev["auth_enabled"] = protected
        if mode == "remove":
            if protected is False:
                self.credentials.remove(device_id)
                return {"protected": False}
            raise ShellyError(self._unverified("Password protection was not confirmed as removed", answer))
        if protected is True and new is not None and await self._auth_works(dev, new):
            self.credentials.store(device_id, new)  # replaces the old credential only now
            return {"protected": True}
        if mode == "change" and await self._auth_works(dev, current):
            raise ShellyError(
                self._unverified("The password was not changed – the previous password still works", answer)
            )
        raise ShellyError(
            self._unverified(
                "The new password could not be confirmed – the previously stored password was kept", answer
            )
        )

    async def _auth_works(self, dev: Device, auth: DeviceAuth | None) -> bool:
        """True if an authenticated read succeeds with auth. Only meaningful while
        the device is protected – an unprotected device accepts anything."""
        try:
            await shelly.read_settings(self.session, dev["gen"], dev["origin"], dev.get("type"), auth)
        except (TimeoutError, ShellyError, aiohttp.ClientError):  # AuthError included
            return False
        return True

    @staticmethod
    def _unverified(text: str, answer: BaseException | None) -> str:
        """Message for an unconfirmed password change, with the device's answer."""
        return f"{text} ({clip(answer, 120)})" if answer else text

    # ------------------------------------------------------------ Installation

    def approval_matches(self, device_id: str, sha: str) -> bool:
        """An approval is only valid while exactly this file (SHA-256) is provided."""
        dev = self.devices.get(device_id) or {}
        if dev.get("gen") == 1 and not self.gen1_enabled:
            return False  # Gen1 updates disabled in the settings
        return dev.get("status") == STATUS_READY and hmac.compare_digest(str(dev.get("sha256")), sha)

    async def install(self, device_id: str, sha: str, approved_by: str, downgrade: bool = False) -> bool:
        """Install an approved update or downgrade (see _install_approved), then check
        the installed devices once no installation is running anymore (see
        _check_after_installs). True only if the target version is confirmed."""
        ok = await self._install_approved(device_id, sha, approved_by, downgrade)
        await self._check_after_installs()
        return ok

    async def _install_approved(
        self, device_id: str, sha: str, approved_by: str, downgrade: bool = False
    ) -> bool:
        """Install an approved update. True only if the target version is confirmed.

        Several installations run in parallel, at most max_parallel_installs at a
        time; others wait for a free slot. Nothing starts during a check, and each
        device is in progress at most once. approved_by is only written to the log.

        downgrade (Gen2+ only): install the stored older version with this
        SHA-256 instead of the provided update. The confirmation flags are checked by
        the web UI; the same install path (pre-check, download server, OTA events,
        verification of the exact target version) is used."""
        if (
            self.checking
            or self.download_base_issue
            or device_id in self.installing
            or device_id in self.reloading
        ):
            return False
        target = None
        if downgrade:
            target = self.downgrade_match(device_id, sha) if device_id in self.devices else None
            if target is None or self.downgrade_blocker(device_id):
                return False
        elif not self.approval_matches(device_id, sha):
            return False
        dev = self.devices[device_id]
        self.installing.add(device_id)  # no await before this: no double starts possible
        dev.pop("updated", None)  # a new installation: the former "just updated" mark is obsolete
        if target is not None:
            # The device now holds the older file: the download server serves it to
            # this device only while the installation runs (status installing). The
            # update provided before (if any) is provided again at the next check.
            dev.update(
                status=STATUS_INSTALLING,
                file=target["name"],
                sha256=target["sha256"],
                size=target["size"],
                pending_version=target["version"],
                pending_build=dev.get("build"),
                source_url=None,
                offer_source=None,
                downgrade=True,
            )
            self.save()
        dev["message"] = "Approved, waiting for a free installation slot"
        log.info(
            "%s %s → %s approved by %s",
            "Downgrade" if downgrade else "Installation", dev["name"], dev["pending_version"], approved_by,
        )
        entry: dict[str, Any] | None = None  # log context, set once the installation actually starts
        try:
            async with self.install_slots:
                dev["message"] = "Downgrade started" if downgrade else "Installation started"
                entry = {
                    "device": dev.get("name"),
                    "type": dev.get("type"),
                    "old": dev.get("version"),
                    "new": dev.get("pending_version"),
                    "user": approved_by,
                }
                if downgrade:
                    entry["downgrade"] = True
                self.events.add("install", event="started", **entry)
                ok, diagnosis = await self._install(device_id, dev, sha)
                if ok and downgrade:
                    self._after_downgrade(dev)
                elif ok:
                    self._followup.add(device_id)  # checked when the installations ended
                    # Stays in the "Updates available" group until the next full
                    # check or a single-device check of this device
                    dev["updated"] = True
                event = "confirmed" if ok else "unconfirmed"
                self.events.add("install", ok=ok, event=event, detail=diagnosis, **entry)
                return ok
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            dev.update(status=STATUS_ERROR, message=error_text(err, 500) or type(err).__name__)
            if entry:
                self.events.add("install", ok=False, event="failed", detail=dev["message"], **entry)
            return False
        finally:
            self.installing.discard(device_id)
            dev.pop("downgrade", None)
            self.cleanup_firmware()  # housekeeping only, stored firmware stays
            self.save()

    @staticmethod
    def _after_downgrade(dev: Device) -> None:
        """After a confirmed downgrade: a newer version known from the last check is
        shown as available again (it is provided at the next check)."""
        offer = dev.get("offer_version")
        try:
            newer = bool(offer) and shelly.parse_version(offer) > shelly.parse_version(dev.get("version"))
        except ShellyError:
            newer = False
        if newer:
            dev.update(
                status=STATUS_AVAILABLE,
                message=f"Downgraded to {dev.get('version')} – a newer version is provided again at the next check",
            )
        else:
            dev["message"] = f"Downgraded to {dev.get('version')}"

    async def _install(self, device_id: str, dev: Device, sha: str) -> tuple[bool, str | None]:
        """Actual installation: pre-check, update command, wait for the target version.
        Returns (confirmed, diagnosis or None)."""
        # 1. The approval is valid only for exactly this file and this device state
        if await asyncio.to_thread(shelly.file_sha256, FW_DIR, dev["file"]) != sha:
            raise ShellyError("Firmware file was modified")
        auth = self._auth(device_id, dev)
        current = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
        if current["mac"] != dev.get("mac") or current["build"] != dev.get("pending_build"):
            raise ShellyError("Device or firmware changed since the check – please check again")

        # 2. Trigger the update; the Shelly downloads from the app's download server.
        # Gen2+: over the device WebSocket, so the device's own OTA events
        # (progress, error) arrive; plain HTTP as fallback if the WebSocket is unusable.
        url = f"{self.download_base}/{dev['file']}"
        self._reset_download(dev)
        ota: dict[str, Any] = {"error": None, "failed": asyncio.Event()}
        reader = await self._update_over_websocket(dev, url, auth, ota) if dev["gen"] >= 2 else None
        if reader is None:
            await shelly.trigger_update(self.session, dev["gen"], dev["origin"], url, auth)
        dev.update(status=STATUS_INSTALLING, message="Update accepted, waiting for restart")
        self.save()
        try:
            return await self._await_target_version(device_id, dev, ota)
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            dev.pop("ota_progress", None)

    async def _await_target_version(self, device_id: str, dev: Device, ota: dict[str, Any]) -> tuple[bool, str | None]:
        """3. Report success only when the device reports exactly the target version
        (parsed version number; Gen1 the vX.Y.Z part): wait 30 s, then check every
        10 s until the Shelly is reachable again. A different version (also a newer
        one) is never reported as success. If it has not even requested the file
        after 2 min, stop waiting – the update will never arrive. A Gen2+ ota_error
        event ends the wait at once (raises OtaError).
        Returns (confirmed, diagnosis or None)."""
        target = shelly.parse_version(dev["pending_version"])
        old = shelly.parse_version(dev["version"]) if dev.get("version") else None
        unexpected = None  # last reported version that is neither old nor target
        started = time.monotonic()
        await self._pause(ota, INSTALL_FIRST_CHECK_S)
        while (elapsed := time.monotonic() - started) < INSTALL_WAIT_S:
            downloaded = self._downloaded(dev)
            try:
                info = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
                reported = shelly.parse_version(info["version"])
                if reported == target:
                    dev.update(
                        info,
                        status=STATUS_CURRENT,
                        file=None,
                        sha256=None,
                        last_update=int(time.time()),
                        message="Updated",
                    )
                    title = "Shelly downgraded" if dev.get("downgrade") else "Shelly updated"
                    await self.notify(f"{title}: {dev['name']}", info["version"], f"sfm_{device_id}")
                    return True, None
                # Neither old nor target: keep waiting (polling is unchanged), but the
                # result can only be "unconfirmed" with this version as hint
                unexpected = info["version"] if reported != old else None
                if not unexpected and not downloaded and elapsed >= INSTALL_NO_DOWNLOAD_S:
                    break  # reachable, old version, nothing downloaded
            except (TimeoutError, aiohttp.ClientError, ShellyError) as err:
                log.debug("Waiting for %s: %s", dev["name"], err)  # restart – expected
            if dev.get("ota_progress") is None:  # the device's own progress is more precise
                dev["message"] = (
                    "Firmware transferred, waiting for restart"
                    if downloaded
                    else "Waiting for the Shelly to download the firmware"
                )
            await self._pause(ota, INSTALL_POLL_S)

        diagnosis = (
            f"The Shelly reports {unexpected} instead of the target version {dev['pending_version']}"
            if unexpected
            else self._install_diagnosis(dev)
        )
        log.warning("Installation %s not confirmed: %s", dev["name"], diagnosis)
        dev.update(status=STATUS_UNCLEAR, message=f"Target version not confirmed. {diagnosis}")
        return False, diagnosis

    async def _update_over_websocket(
        self, dev: Device, url: str, auth: DeviceAuth | None, ota: dict[str, Any]
    ) -> asyncio.Task[None] | None:
        """Gen2+: open the device WebSocket and send Shelly.Update over it.

        Returns the task that reads the device's OTA events, or None if the
        WebSocket is not usable before anything was sent (the caller then sends the
        command over HTTP). A refusal by the device raises as with HTTP. If the
        command was sent but its answer is lost, the update may run: it is never
        sent a second time, the events and the version polling continue."""
        headers = getattr(self.session, "headers", None)
        channel = shelly.OtaChannel(dev["origin"], auth, headers)
        try:
            await channel.__aenter__()
        except (TimeoutError, aiohttp.ClientError, OSError, ShellyError) as err:
            log.info("%s: device WebSocket not available (%s) – update command via HTTP",
                     dev["name"], error_text(err, 120) or type(err).__name__)
            return None
        try:
            accepted = await channel.update(url)
        except shelly.ChannelUnavailable as err:
            await channel.__aexit__(None, None, None)
            log.info("%s: device WebSocket closed (%s) – update command via HTTP", dev["name"], err)
            return None
        except BaseException:
            await channel.__aexit__(None, None, None)
            raise
        if accepted is None:
            log.warning("%s: no answer to the update command – waiting for the device", dev["name"])
        return asyncio.create_task(self._read_ota_events(dev, channel, ota))

    async def _read_ota_events(self, dev: Device, channel: shelly.OtaChannel, ota: dict[str, Any]) -> None:
        """Follow the OTA events of one installation until the device restarts:
        progress and state go to the device row, ota_error ends the installation."""
        try:
            async for event in channel.events():
                kind, msg = event["event"], event["msg"]
                if kind == "ota_progress" and event["progress"] is not None:
                    dev["ota_progress"] = event["progress"]
                    dev["message"] = f"The Shelly is downloading the firmware: {event['progress']} %"
                elif kind == "ota_begin":
                    dev["ota_progress"] = dev.get("ota_progress") or 0
                    dev["message"] = "The Shelly started the update"
                elif kind == "ota_success":
                    dev["ota_progress"] = 100
                    dev["message"] = "Update applied, the Shelly restarts"
                elif kind == "ota_error":
                    ota["error"] = msg or "no details"
                    ota["failed"].set()
                    return
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            log.debug("%s: device WebSocket ended: %s", dev["name"], err)  # restart – expected
        finally:
            await channel.__aexit__(None, None, None)

    async def _pause(self, ota: dict[str, Any], seconds: float) -> None:
        """Sleep while waiting for the device; ends at once on a Gen2+ ota_error."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ota["failed"].wait(), seconds)
        if ota["failed"].is_set():
            raise shelly.OtaError(
                f"The Shelly reports an update error: {ota['error']}. Check that "
                f"{self.download_base} is reachable from the Shelly (LAN IP address of Home "
                "Assistant, port 8098)"
            )

    # ------------------------------------------------------------ Download server

    def may_download(self, name: str, client_ip: str) -> bool:
        """May client_ip download the file name? Only provided files, optionally only to Shellys.
        Rejected attempts on provided files are recorded for the installation diagnosis;
        served downloads are counted by record_download (GET only)."""
        owners = [d for d in self.devices.values() if d.get("status") in PENDING and d.get("file") == name]
        if not owners:
            return False  # unknown file: record nothing
        allowed = not self.restrict_downloads or any(
            shelly.origin_ip(d.get("origin", "")) == client_ip for d in owners
        )
        record = self.downloads.setdefault(name, {"served": {}, "rejected": set()})
        if not allowed and len(record["rejected"]) < 8:  # limited in case someone floods requests
            record["rejected"].add(client_ip)
        return allowed

    def record_download(self, name: str, client_ip: str) -> None:
        """Count a GET that starts the transfer of a provided file, per client
        address (parallel installations of one firmware share the file)."""
        record = self.downloads.get(name)
        if record is None:
            return
        served = record["served"]
        if client_ip in served or len(served) < MAX_SERVED_IPS:
            served[client_ip] = served.get(client_ip, 0) + 1

    def _reset_download(self, dev: Device) -> None:
        """Forget earlier downloads of this device only (installation start)."""
        record = self.downloads.get(str(dev.get("file")))
        if record is not None:
            record["served"].pop(shelly.origin_ip(dev.get("origin", "")), None)

    def _downloaded(self, dev: Device) -> bool:
        """True if this device has started downloading its file. A request from an
        address that belongs to no known device (Docker without the sender IP, see
        restrict_downloads_to_devices) cannot be attributed and counts for every
        device of that file."""
        served = (self.downloads.get(str(dev.get("file"))) or {}).get("served") or {}
        if served.get(shelly.origin_ip(dev.get("origin", ""))):
            return True
        known = {shelly.origin_ip(d["origin"]) for d in self.devices.values() if d.get("origin")}
        return any(count and address not in known for address, count in served.items())

    def _install_diagnosis(self, dev: Device) -> str:
        """Explains why the target version was not reached."""
        record = self.downloads.get(str(dev.get("file")), {})
        if self._downloaded(dev):
            return (
                "The Shelly downloaded the firmware but still reports "
                f"{dev.get('version')} – check the device log or the Shelly's web interface "
                "and try again with enabled websocket debugging"
            )
        if record.get("rejected"):
            ips = ", ".join(sorted(record["rejected"]))
            return (
                f"Download rejected: request came from {ips}, not from the device IP "
                f"{shelly.origin_ip(dev['origin'])}. Disable the option "
                "restrict_downloads_to_devices"
            )
        return (
            f"The Shelly never requested the firmware – is {self.download_base} reachable "
            "from the Shelly (IP of Home Assistant, port from the Network section)?"
        )

    # ------------------------------------------------------------ UI

    def _progress(self) -> dict[str, Any]:
        """Progress of the running check; set by reserve_check before every check."""
        if self.progress is None:
            raise ShellyError("No check reserved")
        return self.progress

    def public_state(self) -> dict[str, Any]:
        """Only fields the UI needs: no credentials, builds or download diagnosis.

        The device's private IP address and its web interface URL are included on
        purpose (IP column, link to the device); both come from the validated origin."""
        fields = (
            "name",
            "shelly_name",
            "gen",
            "type",
            "model",
            "version",
            "offer_version",
            "status",
            "message",
            "sha256",
            "size",
            "last_check",
            "last_success",
            "last_update",
            "ntp",
            "eco",
            "ap",
            "ble",
            "actions",
            "schedules",
            "auth_enabled",
            "outputs",
            "source_url",
            "offer_source",
            "updated",
            "info_refreshed",
            "settings_error",
            "error_level",
            "ota_progress",
            "downgrade",
        )
        devices: list[dict[str, Any]] = []
        stored = self._downgrade_candidates()
        for key, dev in self.devices.items():
            origin = dev.get("origin")  # checked by resolve_device_origin: private IP
            devices.append(
                {
                    "id": key,
                    **{f: dev.get(f) for f in fields},
                    # Number of stored older versions (row menu: Downgrade enabled or not)
                    "downgrades": self._downgrade_count(dev, stored),
                    "installing": key in self.installing,
                    "activity": self.activity.get(key),
                    "ignored": bool(dev.get("ignored")),
                    "password_set": self.credentials.has_device_password(key),  # flag only
                    "ip": shelly.origin_ip(origin) if origin else None,
                    "web_url": f"{origin}/" if origin else None,
                }
            )
        return {
            "devices": devices,
            "last_scan": self.last_scan,
            "busy": self.busy,
            "schedule": {
                "days": self.check_days,
                "time": self.check_time,
                "timezone": self.tz_name,
                "next": int(schedule.next_run(self.schedule, time.time(), self.tz)),
            },
            "scanning": self.checking,
            "progress": dict(self.progress) if self.progress else None,
            "storage_error": self.storage_error,
            "gen1_enabled": self.gen1_enabled,
            "download_base": {
                "url": self.download_base,
                "issue": self.download_base_issue,
                "message": DOWNLOAD_BASE_MESSAGES.get(self.download_base_issue or ""),
            },
            "ui": self.ui,
        }
