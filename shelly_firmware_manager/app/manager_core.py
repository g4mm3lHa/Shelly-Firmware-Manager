"""Manager, layer 1 of 6: state, persistence, Home Assistant access, app options and the
check slot.

The manager is one object built from layers, each in its own module and each extending
the one before: ManagerCore (this file) -> FirmwareStore (manager_store) ->
DeviceSettings (manager_devices) -> Checks (manager_checks) -> Installs
(manager_installs) -> Manager (manager). A layer only uses what the layers below it
provide; the complete object is manager.Manager.

State lives in /data/state.json (with the previous save as backup state.bak, see
ManagerCore.save and _load), firmware in /data/firmware, device passwords in
/data/credentials.json, changelogs in /data/changelog.json and the activity log in
/data/events.json (all private to the app, paths in data_paths).
The full check ("check all") runs exclusively (asyncio.Lock): it starts only while no
single-device check, installation or Reload Device Info runs, and blocks all of them
while it runs. Single-device checks and installations of different devices run in
parallel; one device is checked, installed or reloaded at most once at a time. Every
check is reserved synchronously before it starts (reserve_check).

Progress is kept in memory only (self.progress of the full check, self.activity per
device): the UI shows it while polling, nothing of it is persisted.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
import app_options
import data_paths
import schedule
from changelog import Changelogs
from credentials import Credentials
from events import EventLog
from shelly import AuthError, DeviceAuth, ShellyError, clip, error_text, plain
from tls import CertificatePins


log = logging.getLogger("sfm.manager")

# A device entry of state.json and a store entry of the firmware store: plain JSON
# objects whose fields are listed in docs/codemap/data.md.
Device = dict[str, Any]

FirmwareMeta = dict[str, Any]

# Device status – shown like this in the UI
STATUS_CURRENT = "up-to-date"

STATUS_AVAILABLE = "available"  # newer version known, provided at the next check (after a downgrade)

STATUS_READY = "ready"  # firmware downloaded, waiting for manual installation

STATUS_INSTALLING = "installing"

STATUS_UNCLEAR = "unconfirmed"  # installation without confirmed target version

STATUS_ERROR = "error"

# A battery device (marker battery, detected from the device while awake) that was
# not reachable: it sleeps between wake-ups, so this is no error
STATUS_ASLEEP = "asleep"

# Not in the device registry at its last check (state of versions before the "removed"
# marker); shown as "Not checked" until the device is checked again
STATUS_UNREGISTERED = "unregistered"

STATUS_SKIPPED = "skipped"  # Gen1 device while Gen1 updates are disabled

PENDING = (STATUS_READY, STATUS_INSTALLING)  # device holds a firmware file

# Device list sections for the UI (see section_of), in the order they are shown; the
# read-only list of other Shellys (Manager.others) follows them
SECTION_UPDATES = "updates"
SECTION_ATTENTION = "attention"
SECTION_NOT_CHECKED = "not_checked"
SECTION_UP_TO_DATE = "up_to_date"
SECTION_ASLEEP = "asleep"
SECTION_IGNORED = "ignored"
SECTION_REMOVED = "removed"

# Where the marker battery (sleeping battery device) comes from (device field battery_source)
BATTERY_FROM_DEVICE = "device"  # reported by the device itself while awake (always wins)

BATTERY_FROM_HA = "home_assistant"  # Shelly battery sensor in Home Assistant, device never reported it


def section_of(dev: Device) -> str:
    """Section of the device list a device belongs to (SECTION_*): removed and ignored
    devices first by their marker, then provided updates (also a recent or failed
    installation that stays there until the next check), devices that need attention
    (error, unconfirmed installation, no usable address), never checked devices
    (also Gen1 devices skipped while Gen1 updates are disabled), up to date devices
    and sleeping battery devices."""
    status = dev.get("status")
    if dev.get("removed"):
        return SECTION_REMOVED
    if dev.get("ignored"):
        return SECTION_IGNORED
    recent = dev.get("updated") and status in (STATUS_CURRENT, STATUS_UNCLEAR)
    if status in (STATUS_AVAILABLE, *PENDING) or recent or holds_file(dev):
        return SECTION_UPDATES
    if status in (STATUS_ERROR, STATUS_UNCLEAR) or dev.get("address_issue"):
        return SECTION_ATTENTION
    if status == STATUS_ASLEEP:
        return SECTION_ASLEEP
    if status == STATUS_CURRENT:
        return SECTION_UP_TO_DATE
    return SECTION_NOT_CHECKED  # skipped, unregistered (older versions) or no result


def holds_file(dev: Device) -> bool:
    """True while the device holds its provided firmware file: ready, installing, a
    failed installation that can be retried (marker install_failed), or a stored
    version offered for a manual installation because the manufacturer's server
    could not be asked (marker store_fallback) – both kept until the next check of
    the device."""
    if not dev.get("file"):
        return False
    if dev.get("status") in PENDING:
        return True
    failed = dev.get("status") in (STATUS_ERROR, STATUS_ASLEEP)
    return failed and bool(dev.get("install_failed") or dev.get("store_fallback"))


SUPERVISOR = "http://supervisor"  # Supervisor API (Core API proxy below /core)

SUPERVISOR_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Largest WebSocket message accepted from Home Assistant: the device registry lists the
# devices of all integrations in one message (aiohttp's default of 4 MiB is too small
# for very large installations)
REGISTRY_MAX = 32 * 1024 * 1024  # bytes

# Pages of the Home Assistant frontend: the page of this app (its info card has the
# "Changelog" link next to the version) and, if the slug is unknown, the list of apps
APP_PAGE = "/config/app/{slug}/info"
APP_LIST_PAGE = "/config/apps"
APP_SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")  # slug as the Supervisor reports it

# Per-device activity during a check (memory only, shown in the device row)
ACTIVITY_WAITING = "waiting"  # part of the running check, not yet reached

ACTIVITY_CHECKING = "checking"  # device / manufacturer server is being queried

ACTIVITY_DOWNLOADING = "downloading"  # firmware for this device is being downloaded

ACTIVITY_RELOADING = "reloading"  # Reload Device Info is reading the device

PART_PREFIX = ".part-"  # temporary download files (see shelly._store)

# ASCII punctuation: escaped with a backslash in texts of the Home Assistant notification,
# which renders Markdown (links, images, emphasis, HTML, autolinks); shown unchanged
MARKDOWN_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")


def markdown_text(value: object, limit: int = 64) -> str:
    """A device name or version as literal text in Markdown: control and format
    characters removed (see shelly.plain), every ASCII punctuation character escaped."""
    return MARKDOWN_PUNCTUATION.sub(r"\\\1", plain(value, limit))

SOURCE_DOWNLOAD = "download"  # downloaded from the manufacturer by a check

SOURCE_UPLOAD = "upload"  # uploaded manually in the Firmware tab

# Taken over from a store import: unverified like an upload (the seal of an export only
# proves knowledge of its password, not who made it) until a manufacturer offer names
# its SHA-256; it never replaces a manufacturer download otherwise
SOURCE_IMPORT = "import"

# download_base_url problems that block installations
DOWNLOAD_BASE_MESSAGES = {
    "missing": "Installations are disabled: set the download base URL (LAN IP address of Home "
    "Assistant and port 8098) in the Settings tab.",
    "unconfirmed": "Installations are disabled: the download base URL is still the former example "
    f"value {app_options.LEGACY_DOWNLOAD_BASE}. Change it in the Settings tab or confirm that it "
    "really is the address of Home Assistant.",
    "not_private": "Installations are disabled: the IP address of the download base URL is not in a "
    "private network (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7). Correct it in the "
    "Settings tab.",
    "invalid": "Installations are disabled: the download base URL is not a valid address "
    "(http(s)://host:port, letters, digits, dots and hyphens in the host). Correct it in the Settings tab.",
}

# No check before the first-start question is answered (fresh installation)
FIRST_START_BLOCK = "Not before the first-start question is answered: import a firmware store or skip it"


def severity_of(err: BaseException) -> str:
    """Log severity of a device error: authentication and internal errors are
    errors; unreachable devices, timeouts and temporary/missing data warnings."""
    if isinstance(err, AuthError) or not isinstance(err, (TimeoutError, ShellyError, aiohttp.ClientError, OSError)):
        return "error"
    return "warning"


def unexpected_text(err: BaseException) -> str:
    """Status message for an error no code path expects (e.g. a device answer of an
    unexpected shape): names the error type; the traceback is in the app log."""
    return clip(f"Unexpected error ({type(err).__name__}: {error_text(err, 300)}) – see the app log", 500)


class ManagerCore:
    """State of all Shellys, its persistence and the services every layer needs."""

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
        self.gen1_enabled = bool(options["gen1_updates_enabled"])
        self.allow_downgrade = options.get("allow_downgrade") is True  # optional, missing = off
        # Host name column: look up host names at every check (off: no lookup, no column)
        self.resolve_host_names = options["resolve_host_names"] is True
        self.import_max = int(options["import_max_mb"]) * 1024 * 1024  # bytes; largest store import
        # Gen2+: also store the version a device runs while Shelly still offers it (way back)
        self.keep_installed = options["keep_installed"] is True
        # A new version is offered only once it is this many days old (build date); 0 = at once
        self.offer_after_days = int(options["offer_after_days"])
        self.session = session
        self.token = token
        self.pins = pins  # certificate pins for shelly.cloud
        self.app_slug: str | None = None  # slug of this app (Supervisor), read on first use
        self.lock = asyncio.Lock()  # check (exclusive)
        self.install_slots = asyncio.Semaphore(int(options.get("max_parallel_installs", 5)))
        self.installing: set[str] = set()  # device IDs: running or waiting
        self.last_scan: int | None = None
        self.devices: dict[str, Device] = {}  # HA device ID -> state
        self.downloads: dict[str, dict[str, Any]] = {}  # file name -> {"served": {ip: n}, "rejected": {ip, ...}}
        self.firmware: dict[str, FirmwareMeta] = {}  # file name -> metadata of the stored firmware
        self.activity: dict[str, str] = {}  # HA device ID -> ACTIVITY_* while a check runs
        self.progress: dict[str, Any] | None = None  # running full check: {"kind", "total", "done", "errors", "phase"}
        self.device_checks: dict[str, str] = {}  # HA device ID -> "queued"/"running" (single-device checks)
        self.storage_error: str | None = None  # last failure writing state.json (shown in the UI)
        # Set at start if the state file was missing or unreadable although the app
        # was installed before (see _load); shown in the UI until the next restart
        self.state_warning: str | None = None
        # Files in the firmware folder the store does not list (name -> size): never
        # deleted automatically, listed in the Firmware tab for a manual deletion
        # (see FirmwareStore.adopt_files and cleanup_firmware); the names are
        # persisted so that a file is reported in the log only once
        self.unknown_files: dict[str, int] = {}
        # Shellys of other integrations (Bluetooth, Zigbee, Matter, MQTT) and BLU devices
        # behind a Shelly gateway: read-only list for the UI, taken from the registry at
        # every check (see Checks._other_shellys); persisted
        self.others: list[dict[str, Any]] = []
        # Shellys Home Assistant knows but the app does not list, with the reason:
        # {"listed": n, "missing": [{"name", "model", "reason"}]} (MISSING_*), taken
        # at every full check (see Checks._missing_shellys); persisted
        self.listing: dict[str, Any] = {"listed": 0, "missing": []}
        self._state_file_valid = False  # state.json holds a state this run read or wrote
        # Gen1 store entries of a state written by 3.8.0 or older are marked as imported
        # once at the start (FirmwareStore.mark_gen1_imported): imports of these versions
        # were stored as downloads and cannot be told apart
        self.gen1_marking_due = False
        self.schedule: dict[str, Any] = {}  # automatic check schedule (see schedule.py), persisted
        self.reloading: set[str] = set()  # device IDs whose Reload Device Info is running
        # (firmware ID, version) -> running provide task (single flight)
        self._provisions: dict[tuple[str, str], asyncio.Future[tuple[str, str, int]]] = {}
        # (firmware ID, version) -> devices waiting for that provide task (full and
        # single-device checks alike); all of them show "downloading" while it downloads
        self._provision_waiters: dict[tuple[str, str], set[str]] = {}
        self._fetching: set[tuple[str, str]] = set()  # provide tasks that are downloading right now
        self._downloads_running = 0  # for the progress phase of a parallel check
        # Running checks only (cleared when no check runs anymore, see _release_check_state):
        # file name -> shared checksum verification of a stored file (single flight)
        self._verifying: dict[str, asyncio.Future[bool]] = {}
        # file name -> manufacturer URL of a firmware downloaded by this check
        self._fresh: dict[str, str] = {}
        # User shown in the log for the downloads: of the full check ("Schedule" for
        # scheduled checks) and per device of a single-device check
        self._scan_user: str | None = None
        self._check_users: dict[str, str] = {}
        # Reference request of the running checks (shared by parallel single-device
        # checks): does the manufacturer's server still answer with the next step for
        # an installed version (see Checks)?
        self._header_check: asyncio.Future[bool | None] | None = None
        self._header_got = ""  # version the reference request was answered with
        # Devices that installed an intermediate version: checked again at once
        self._step_checks: set[str] = set()
        self._upload: dict[str, Any] | None = None  # staged manual upload (see stage_upload), memory only
        self._upload_lock = asyncio.Lock()  # one upload transfer at a time
        self._export: dict[str, Any] | None = None  # prepared store export (see prepare_export), memory only
        self._import: dict[str, Any] | None = None  # staged store import (see stage_import), memory only
        self._import_lock = asyncio.Lock()  # one import transfer / check / confirmation at a time
        self._import_storing = False  # confirm_import is writing its files to the store
        # File transfers in parts (upload, import) that are not complete yet, by kind;
        # memory only (see FirmwareStore._receive_part)
        self._receiving: dict[str, dict[str, Any]] = {}
        # First start of a fresh installation (no state.json): no check runs until the
        # user answered the import question (import a store or skip); persisted
        self.first_start_open = False
        self.first_start_notified = False  # the HA notification about the question was sent
        # Set whenever the schedule changes (check finished): wakes the scheduler in
        # main.py instead of letting it poll every minute.
        self.schedule_changed = asyncio.Event()
        data_paths.FW_DIR.mkdir(parents=True, exist_ok=True)
        self._remove_partial_downloads()
        self.credentials = Credentials(data_paths.CREDENTIALS_FILE)
        self.changelogs = Changelogs(data_paths.CHANGELOG_FILE)
        self.events = EventLog(data_paths.EVENTS_FILE, options["log_retention_days"], options["log_entry_limit"])
        self._load()
        self.schedule, changed = schedule.configure(
            self.schedule, self.check_days, self.check_time, time.time(), self.tz
        )
        if changed:  # new install or changed interval/time: anchor anew
            self.save()

    # ----------------------------------------------------------------------------------------- Persistence

    def _load(self) -> None:
        """Load the state of the last run.

        Neither a state file nor a backup = fresh installation. An unreadable state
        file is kept as state.damaged and the backup (state.bak: the save before the
        last one, see save) is loaded instead; so is the backup if only the state
        file is missing (the app was stopped between the two renames of save).
        Without any readable state the app starts with an empty device list and an
        empty store registry; the firmware files are read again from the disk at the
        start (see FirmwareStore.adopt_files) – no file is ever removed for that."""
        state_file = data_paths.STATE_FILE
        backup = state_file.with_suffix(".bak")
        try:
            self._apply_state(self._read_state(state_file))
            self._state_file_valid = True
        except FileNotFoundError:
            if not backup.is_file():
                self.first_start_open = True  # fresh installation (also after an uninstall)
                return
            log.error("State file missing, loading the backup")
            self._load_backup(backup)
        except (ValueError, OSError) as err:
            log.error("State file unreadable, loading the backup: %s", err)
            with contextlib.suppress(OSError):
                state_file.replace(state_file.with_suffix(".damaged"))  # kept for a manual look
            self._load_backup(backup)
        for dev in self.devices.values():
            # A restart during an installation leaves an unclear state
            if dev.get("status") == STATUS_INSTALLING:
                dev.update(status=STATUS_UNCLEAR, message="App was restarted during the installation")
            dev.pop("downgrade", None)  # marker of a running downgrade, memory semantics

    def _load_backup(self, backup: Path) -> None:
        """Load the backup because the state file is missing or unreadable (see _load)."""
        try:
            self._apply_state(self._read_state(backup))
        except (ValueError, OSError) as err:
            log.error("No readable backup of the state (%s): starting without devices, firmware files are kept", err)
            self.state_warning = (
                "The state file was unreadable and no backup could be loaded. The devices are found again by "
                "the next check; the files of the firmware store were read again from the disk (see the log)."
            )
            return
        self.state_warning = (
            "The state file was unreadable – the backup of the previous save was loaded. Check the devices again."
        )

    @staticmethod
    def _read_state(path: Path) -> dict[str, Any]:
        """Content of a state file; ValueError if it is not a usable state (so a
        damaged file is never taken for an empty device list or store registry)."""
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        for key in ("devices", "firmware"):
            value = data.get(key)
            if value is not None and not (isinstance(value, dict) and all(isinstance(v, dict) for v in value.values())):
                raise ValueError(f"state: {key} is not a map of objects")
        return data

    def _apply_state(self, data: dict[str, Any]) -> None:
        """Take over a state read by _read_state."""
        self.devices = data.get("devices") or {}
        if not self.resolve_host_names:  # switched off: no host name is kept or shown
            for dev in self.devices.values():
                if isinstance(dev, dict):
                    dev.pop("host_name", None)
        self.last_scan = data.get("last_scan")
        self.firmware = data.get("firmware") or {}
        self.schedule = data["schedule"] if isinstance(data.get("schedule"), dict) else {}
        confirmed = data.get("download_base_confirmed")
        self.download_base_confirmed = confirmed if isinstance(confirmed, str) else None
        self.first_start_open = data.get("first_start_open") is True
        self.first_start_notified = data.get("first_start_notified") is True
        self.gen1_marking_due = data.get("gen1_imports_marked") is not True
        unknown = data.get("unknown_files")
        if isinstance(unknown, list):
            self.unknown_files = {str(n): 0 for n in unknown if isinstance(n, str)}
        others = data.get("others")
        self.others = [o for o in others if isinstance(o, dict)] if isinstance(others, list) else []
        listing = data.get("listing")
        if isinstance(listing, dict) and isinstance(listing.get("missing"), list):
            self.listing = {
                "listed": int(listing.get("listed") or 0),
                "missing": [m for m in listing["missing"] if isinstance(m, dict)],
            }

    @staticmethod
    def _remove_partial_downloads() -> None:
        """Delete temporary download files left behind by a crash or restart.
        They are never offered for installation (only <sha256>.<ext> names are)."""
        for path in data_paths.FW_DIR.glob(PART_PREFIX + "*"):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
                log.info("Removed incomplete download %s", path.name)

    def save(self) -> bool:
        """Write the state atomically and durably: temporary file, fsync, then the
        current state file becomes the backup (state.bak) and the temporary file the
        state file. A power failure therefore leaves a complete state file or, at
        worst, the backup of the save before (see _load).

        A write error (e.g. full disk) keeps the previous state file intact, is
        logged and shown in the UI – it must not abort a check or installation."""
        state_file = data_paths.STATE_FILE
        temp = state_file.with_suffix(".tmp")
        state = {
            "devices": self.devices,
            "last_scan": self.last_scan,
            "firmware": self.firmware,
            "schedule": self.schedule,
            "download_base_confirmed": self.download_base_confirmed,
            "first_start_open": self.first_start_open,
            "first_start_notified": self.first_start_notified,
            "gen1_imports_marked": not self.gen1_marking_due,
            "unknown_files": sorted(self.unknown_files),
            "others": self.others,
            "listing": self.listing,
        }
        try:
            with temp.open("w") as handle:
                handle.write(json.dumps(state, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            # Only a state file this run read or wrote becomes the backup – never a
            # file that could not be read at start
            if self._state_file_valid and state_file.is_file():
                state_file.replace(state_file.with_suffix(".bak"))
            temp.replace(state_file)
            self._state_file_valid = True
            self._sync_directory(state_file.parent)
        except OSError as err:
            temp.unlink(missing_ok=True)
            self.storage_error = clip(f"State could not be saved: {err}", 300)
            log.error("%s", self.storage_error)
            return False
        self.storage_error = None
        return True

    @staticmethod
    def _sync_directory(path: Path) -> None:
        """Make the renames of save durable (best effort: not every file system
        supports syncing a directory)."""
        with contextlib.suppress(OSError):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    # -------------------------------------------------------------------------------------- Home Assistant

    async def _registry(self) -> list[dict[str, Any]]:
        """Device registry via the Core WebSocket API (through the Supervisor proxy).
        The registry of all integrations arrives as one message (REGISTRY_MAX)."""
        return await self._ha_list("config/device_registry/list")

    async def _battery_devices(self) -> set[str]:
        """HA device IDs with a battery sensor of the Shelly integration (entity
        registry: platform shelly, unique ID "<mac>-<component>-battery" – Gen1
        device_0, Gen2+ devicepower:0; Home Assistant removes it for externally
        powered devices). Home Assistant does not hand out the sleep period of its
        config entry over the API, so this sensor is the only hint of a battery
        device that never answered the app. Empty if the request fails (no
        conclusion, logged)."""
        try:
            entities = await self._ha_list("config/entity_registry/list")
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.warning("Entity registry of Home Assistant not available: %s", err or type(err).__name__)
            return set()
        found = set()
        for entity in entities:
            if not isinstance(entity, dict) or entity.get("platform") != "shelly":
                continue
            unique_id, device_id = entity.get("unique_id"), entity.get("device_id")
            if (
                isinstance(unique_id, str)
                and unique_id.endswith("-battery")
                and str(entity.get("entity_id") or "").startswith("sensor.")
                and isinstance(device_id, str)
            ):
                found.add(device_id)
        return found

    async def _ha_setup_state(self) -> tuple[list[Any], list[Any]]:
        """Config entries of the Shelly integration (config_entries/get; ignored
        discoveries have source "ignore") and the flows in progress (discoveries
        not set up yet, config_entries/flow/progress) – for the Shellys Home
        Assistant knows but the app does not list. Raises like _ha_list."""
        entries = await self._ha_list("config_entries/get", domain="shelly")
        flows = await self._ha_list("config_entries/flow/progress")
        return entries, flows

    async def _ha_list(self, request: str, **params: str) -> list[Any]:
        """Answer of one list request of the Core WebSocket API (device or entity
        registry, config entries, flows); params are added to the request; anything
        but a successful list answer is a ShellyError."""
        ws_url = SUPERVISOR.replace("http", "ws", 1) + "/core/websocket"

        async def receive(ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
            """Next JSON object from Home Assistant; anything else is a ShellyError."""
            msg = await ws.receive(timeout=30)
            if msg.type is not aiohttp.WSMsgType.TEXT:
                # e.g. closed by aiohttp because the message exceeds REGISTRY_MAX
                raise ShellyError(f"Home Assistant did not answer the device registry request ({msg.type.name})")
            try:
                data = json.loads(msg.data)
            except ValueError:
                raise ShellyError("Unreadable answer of Home Assistant") from None
            if not isinstance(data, dict):
                raise ShellyError("Unexpected answer of Home Assistant")
            return data

        async with self.session.ws_connect(
            ws_url, timeout=aiohttp.ClientWSTimeout(ws_close=30), max_msg_size=REGISTRY_MAX
        ) as ws:
            if (await receive(ws)).get("type") != "auth_required":
                raise ShellyError("Unexpected WebSocket greeting")
            await ws.send_json({"type": "auth", "access_token": self.token})
            if (await receive(ws)).get("type") != "auth_ok":
                raise ShellyError("Authentication with Home Assistant failed")
            await ws.send_json({"id": 1, "type": request, **params})
            while True:
                msg = await receive(ws)
                if msg.get("id") == 1:
                    if not msg.get("success"):
                        raise ShellyError(clip(msg.get("error")))
                    result = msg.get("result")
                    return result if isinstance(result, list) else []

    async def notify(self, title: str, message: str, notification_id: str) -> bool:
        """Persistent notification in Home Assistant. True if Home Assistant accepted
        it; errors are only logged."""
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
            return False
        return True

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

    async def app_page(self) -> str:
        """Path of this app's page in the Home Assistant frontend (Settings tab: link to
        the changelog). The slug is read once from the Supervisor (/addons/self/info,
        open to every app); while it is not available the list of apps is returned."""
        if self.app_slug is None:
            try:
                async with self.session.get(
                    f"{SUPERVISOR}/addons/self/info",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=SUPERVISOR_TIMEOUT,
                ) as resp:
                    resp.raise_for_status()
                    slug = ((await resp.json(content_type=None)).get("data") or {}).get("slug")
            except (TimeoutError, aiohttp.ClientError, ValueError, AttributeError) as err:
                log.warning("App info not available from the Supervisor: %s", err or type(err).__name__)
                return APP_LIST_PAGE
            if not isinstance(slug, str) or not APP_SLUG_RE.fullmatch(slug):
                log.warning("App info from the Supervisor has no valid slug")
                return APP_LIST_PAGE
            self.app_slug = slug
        return APP_PAGE.format(slug=self.app_slug)

    # ----------------------------------------------------------------------------------------- App options

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

    @property
    def download_base_issue(self) -> str | None:
        """Why installations are blocked by download_base_url, else None.

        "missing": not set (optional option without default). "unconfirmed": still the
        former example value, which is only accepted after an explicit confirmation –
        it may really be the address of Home Assistant (FritzBox default network).
        "not_private": an IP address outside the private networks, saved before the
        option refused it. "invalid": not a usable address (odd characters, bad host name).
        Neither can be confirmed, only corrected."""
        if not self.download_base:
            return "missing"
        problem = app_options.download_base_problem(self.download_base)
        if problem:
            return "not_private" if problem == app_options.DOWNLOAD_BASE_NOT_PRIVATE else "invalid"
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

    # ------------------------------------- Check slots (full check exclusive, single checks per device)

    @property
    def checking(self) -> bool:
        """True while the full check runs or has been reserved by a request (see
        reserve_check). Single-device checks: device_checks."""
        return self.lock.locked() or self.progress is not None

    def reserve_check(self, kind: str = "all", device_id: str | None = None) -> None:
        """Reserve a check synchronously, before the background task starts.

        Two quick requests (double tap, two browsers) can therefore never start the
        same check twice, and an approval arriving in between is refused.
        kind "all": only while nothing else runs (no single-device check, installation
        or Reload Device Info). kind "device": not during the full check, and not for
        a device that is being checked, installed or reloaded – other devices may be
        checked or installed at the same time. No check starts while a store import
        writes its files. Raises ShellyError otherwise."""
        if self.first_start_open:
            raise ShellyError(FIRST_START_BLOCK)
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        if self._import_storing:
            raise ShellyError("Store import in progress – please try again in a moment")
        if self.reloading and (kind != "device" or device_id in self.reloading):
            raise ShellyError("Device info is being reloaded – please try again in a moment")
        if kind == "device":
            if device_id is None:
                raise ValueError("device_id required for a single-device check")
            if device_id in self.device_checks:
                raise ShellyError("This device is already being checked")
            dev = self.devices.get(device_id, {})
            if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
                raise ShellyError("Installation of this device in progress – please try again afterwards")
            self.device_checks[device_id] = "queued"
            self.activity[device_id] = ACTIVITY_WAITING
            return
        if self.device_checks:
            raise ShellyError(f"Not possible – single-device checks running: {len(self.device_checks)}")
        if self.installing:
            raise ShellyError(f"Not possible – installations running: {len(self.installing)}")
        self.progress = {"kind": kind, "total": 0, "done": 0, "errors": 0, "phase": "queued"}

    def _reserved(self, kind: str, device_id: str | None = None) -> bool:
        """True if exactly this check was reserved and has not started yet."""
        if kind == "device":
            return device_id is not None and self.device_checks.get(device_id) == "queued"
        p = self.progress
        return not self.lock.locked() and p is not None and p.get("kind") == kind and p.get("phase") == "queued"

    @property
    def busy(self) -> str | None:
        """Text for the UI while a check or installations are running (the full
        check button is blocked while this is set)."""
        if self.checking:
            return "Check in progress"
        parts = []
        if self.device_checks:
            names = [str(self.devices.get(i, {}).get("name") or "device") for i in self.device_checks]
            parts.append(f"Checking {names[0]}" if len(names) == 1 else f"Checking {len(names)} devices")
        if self.installing:
            parts.append(f"Installations running: {len(self.installing)}")
        if self.reloading:
            parts.append("Reloading device info")
        return " · ".join(parts) or None

    def _progress(self) -> dict[str, Any]:
        """Progress of the running full check; set by reserve_check before it starts."""
        if self.progress is None:
            raise ShellyError("No check reserved")
        return self.progress

    def _end_check(self) -> None:
        """Clear the memory-only state of a finished or cancelled full check."""
        self.activity.clear()
        self.progress = None
        self._scan_user = None
        self._release_check_state()

    def _end_device_check(self, device_id: str) -> None:
        """Clear the state of a finished or cancelled single-device check."""
        self.device_checks.pop(device_id, None)
        self.activity.pop(device_id, None)
        self._check_users.pop(device_id, None)
        self._release_check_state()

    def _release_check_state(self) -> None:
        """Shared caches of the running checks end with the last of them (called
        after progress was cleared, so a full check ending inside its lock counts as
        finished)."""
        if self.progress is not None or self.device_checks:
            return
        self._verifying.clear()
        self._fresh.clear()
        if self._header_check is not None and not self._header_check.done():
            self._header_check.cancel()
        self._header_check = None

    # ---------------------------------------------------------------------------------- Device credentials

    def _auth(self, device_id: str, dev: Device) -> DeviceAuth | None:
        """Stored credentials of a device, else None (there is no shared password)."""
        return self.credentials.auth_for(device_id, dev.get("gen") or 1)

    def _auth_for(self, device_id: str) -> Callable[[int], DeviceAuth | None]:
        """Credentials lookup by generation, for requests that detect the generation."""
        return lambda gen: self.credentials.auth_for(device_id, gen)
