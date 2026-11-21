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
The full check ("check all") runs exclusively (asyncio.Lock): it starts only while no
single-device check, installation or Reload Device Info runs, and blocks all of them
while it runs. Single-device checks and installations of different devices run in
parallel; one device is checked, installed or reloaded at most once at a time. Every
check is reserved synchronously before it starts (reserve_check).

Progress is kept in memory only (self.progress of the full check, self.activity per
device): the UI shows it while polling, nothing of it is persisted.
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

STATUS_UNREGISTERED = "unregistered"

STATUS_SKIPPED = "skipped"  # Gen1 device while Gen1 updates are disabled

PENDING = (STATUS_READY, STATUS_INSTALLING)  # device holds a firmware file


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

SOURCE_DOWNLOAD = "download"  # downloaded from the manufacturer by a check

SOURCE_UPLOAD = "upload"  # uploaded manually in the Firmware tab

# download_base_url problems that block installations
DOWNLOAD_BASE_MESSAGES = {
    "missing": "Installations are disabled: set the download base URL (LAN IP address of Home "
    "Assistant and port 8098) in the Settings tab.",
    "unconfirmed": "Installations are disabled: the download base URL is still the former example "
    f"value {app_options.LEGACY_DOWNLOAD_BASE}. Change it in the Settings tab or confirm that it "
    "really is the address of Home Assistant.",
}

# No check before the first-start question is answered (fresh installation)
FIRST_START_BLOCK = "Not before the first-start question is answered: import a firmware store or skip it"

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
        self.allow_downgrade = options.get("allow_downgrade") is True  # optional, missing = off
        self.import_max = int(options["import_max_mb"]) * 1024 * 1024  # bytes; largest store import
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
        """Load the state of the last run; missing or broken file = fresh start."""
        try:
            data = json.loads(data_paths.STATE_FILE.read_text())
            if not isinstance(data, dict):
                raise ValueError("state is not an object")
            self.devices = data.get("devices") or {}
            self.last_scan = data.get("last_scan")
            self.firmware = data["firmware"] if isinstance(data.get("firmware"), dict) else {}
            self.schedule = data["schedule"] if isinstance(data.get("schedule"), dict) else {}
            confirmed = data.get("download_base_confirmed")
            self.download_base_confirmed = confirmed if isinstance(confirmed, str) else None
            self.first_start_open = data.get("first_start_open") is True
            self.first_start_notified = data.get("first_start_notified") is True
        except FileNotFoundError:
            self.first_start_open = True  # fresh installation (also after an uninstall)
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
            "schedule": self.schedule,
            "download_base_confirmed": self.download_base_confirmed,
            "first_start_open": self.first_start_open,
            "first_start_notified": self.first_start_notified,
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
        checked or installed at the same time. Raises ShellyError otherwise."""
        if self.first_start_open:
            raise ShellyError(FIRST_START_BLOCK)
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
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
