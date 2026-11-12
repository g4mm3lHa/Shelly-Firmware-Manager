"""Manager, layer 1 of 6: state, persistence, Home Assistant access, app options and the
check slot.

The manager is one object built from layers, each in its own module and each extending
the one before: ManagerCore (this file) -> FirmwareStore (manager_store) ->
DeviceSettings (manager_devices) -> Checks (manager_checks) -> Installs
(manager_installs) -> Manager (manager). A layer only uses what the layers below it
provide; the complete object is manager.Manager.

State lives in /data/state.json, firmware in /data/firmware, device passwords in
/data/credentials.json, changelogs in /data/changelog.json and the activity log in
/data/events.json (all private to the app, paths in data_paths).
Checks – the full check and the single-device check – run exclusively (asyncio.Lock)
and are reserved synchronously before they start
(reserve_check); checks and installations exclude each other.

Progress of a running check is kept in memory only (self.progress, self.activity):
the UI shows it while polling, nothing of it is persisted.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import aiohttp
import app_options
import data_paths
import schedule
from changelog import Changelogs
from credentials import Credentials
from events import EventLog
from shelly import AuthError, DeviceAuth, ShellyError, clip, error_text
from tls import CertificatePins


log = logging.getLogger("sfm.manager")

# A device entry of state.json and a store entry of the firmware store: plain JSON
# objects whose fields are listed in docs/CODEMAP.md (Stored data).
Device = dict[str, Any]

FirmwareMeta = dict[str, Any]

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


def holds_file(dev: Device) -> bool:
    """True while the device holds its provided firmware file: ready, installing, or
    failed installation that can be retried (marker install_failed, kept until the
    next check of the device)."""
    if not dev.get("file"):
        return False
    return dev.get("status") in PENDING or (dev.get("status") == STATUS_ERROR and bool(dev.get("install_failed")))

SUPERVISOR = "http://supervisor"  # Supervisor API (Core API proxy below /core)

SUPERVISOR_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Per-device activity during a check (memory only, shown in the device row)
ACTIVITY_WAITING = "waiting"  # part of the running check, not yet reached

ACTIVITY_CHECKING = "checking"  # device / manufacturer server is being queried

ACTIVITY_DOWNLOADING = "downloading"  # firmware for this device is being downloaded

ACTIVITY_RELOADING = "reloading"  # Reload Device Info is reading the device

PART_PREFIX = ".part-"  # temporary download files (see shelly._store)

SOURCE_DOWNLOAD = "download"  # downloaded from the manufacturer by a check

SOURCE_UPLOAD = "upload"  # uploaded manually in the Firmware tab

UI_TABLES = ("devices",)  # tables whose sort order/columns are stored

UI_VERSION = 3  # table settings format since 3.0 (older formats are dropped, not migrated)

COLUMN_RE = re.compile(r"^[a-z_]{1,32}$")

MAX_COLUMNS = 30

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
        # User shown in the log for the downloads of this check ("Schedule" for
        # scheduled checks)
        self._check_user: str | None = None
        self._upload: dict[str, Any] | None = None  # staged manual upload (see stage_upload), memory only
        self._upload_lock = asyncio.Lock()  # one upload transfer at a time
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
        """Load the state of the last run; missing or broken file = fresh start."""
        try:
            data = json.loads(data_paths.STATE_FILE.read_text())
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
        for path in data_paths.FW_DIR.glob(PART_PREFIX + "*"):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
                log.info("Removed incomplete download %s", path.name)

    def save(self) -> bool:
        """Write the state atomically (temporary file first, then rename).

        A write error (e.g. full disk) keeps the previous state file intact, is
        logged and shown in the UI – it must not abort a check or installation."""
        temp = data_paths.STATE_FILE.with_suffix(".tmp")
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
            temp.replace(data_paths.STATE_FILE)
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

    # -------------------------------------------------------------------------------------- Home Assistant

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

    # ------------------------------------- Check slot (shared by checks, installations and device actions)

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
        else:  # "all"
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

    def _progress(self) -> dict[str, Any]:
        """Progress of the running check; set by reserve_check before every check."""
        if self.progress is None:
            raise ShellyError("No check reserved")
        return self.progress

    def _end_check(self) -> None:
        """Clear the memory-only state of a finished or cancelled check."""
        self.activity.clear()
        self.progress = None
        self._verifying.clear()
        self._fresh.clear()
        self._check_user = None

    # ---------------------------------------------------------------------------------- Device credentials

    def _auth(self, device_id: str, dev: Device) -> DeviceAuth | None:
        """Stored credentials of a device, else None (there is no shared password)."""
        return self.credentials.auth_for(device_id, dev.get("gen") or 1)

    def _auth_for(self, device_id: str) -> Callable[[int], DeviceAuth | None]:
        """Credentials lookup by generation, for requests that detect the generation."""
        return lambda gen: self.credentials.auth_for(device_id, gen)
