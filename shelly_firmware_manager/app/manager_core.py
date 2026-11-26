"""Manager, layer 1 of 9: state, persistence, Home Assistant access, app options and the
check slot.

The manager is one object built from layers, each in its own module and each extending
the one before: ManagerCore (this file) -> FirmwareStore (manager_store) ->
Transfers (manager_transfer) -> DeviceSettings (manager_devices) -> Backups
(manager_backups) -> Checks (manager_checks) -> Installs (manager_installs) -> Schedules
(manager_schedules) -> Manager (manager). A layer only uses what the layers below it
provide; the complete object is manager.Manager.

State lives in /data/state.json (with the previous save as backup state.bak, see
ManagerCore.save and _load), firmware in /data/firmware, device passwords in
/data/credentials.json, changelogs in /data/changelog.json, the activity log in
/data/events.json and the backups of device settings in /data/backups (all private to
the app, paths in data_paths).
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
import contextvars
import json
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import aiohttp
import app_options
import data_paths
import schedule
import timetable
from backup_store import BackupStore
from changelog import Changelogs
from credentials import Credentials
from events import EventLog
from shelly import (
    AuthError,
    DeviceAuth,
    HttpsRequiredError,
    ShellyError,
    clip,
    error_text,
    plain,
    port_open,
    same_secret,
)
from shelly_net import resolve_lan_address
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

# Gen1 device while Gen1 updates are disabled; also a Gen2+ device whose beta offer was
# withdrawn at the start because beta firmware is switched off (until its next check)
STATUS_SKIPPED = "skipped"

PENDING = (STATUS_READY, STATUS_INSTALLING)  # device holds a firmware file

# Device list sections for the UI (see section_of), in the order they are shown; the
# read-only list of other Shellys (Manager.others) follows them
SECTION_UPDATES = "updates"
SECTION_BETA = "beta"  # provided beta versions: own section after the stable updates
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
    devices first by their marker; a running downgrade (marker downgrade) is no offered
    update: the device stays in the section it had before (downgrade_from); then provided
    updates (also a recent or failed installation that stays there until the next
    check) – beta versions (marker beta) in their own section –, devices that need attention
    (error, unconfirmed installation, no usable address), never checked devices
    (also Gen1 devices skipped while Gen1 updates are disabled), up to date devices
    and sleeping battery devices."""
    status = dev.get("status")
    if dev.get("removed"):
        return SECTION_REMOVED
    if dev.get("ignored"):
        return SECTION_IGNORED
    if dev.get("downgrade") and status in PENDING:
        return str(dev.get("downgrade_from") or SECTION_UP_TO_DATE)
    recent = dev.get("updated") and status in (STATUS_CURRENT, STATUS_UNCLEAR)
    if status in (STATUS_AVAILABLE, *PENDING) or recent or holds_file(dev):
        return SECTION_BETA if dev.get("beta") else SECTION_UPDATES
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


def supervisor_headers(token: str) -> dict[str, str]:
    """Headers of a request to the Supervisor API (and the Core API proxy below /core)
    with the Supervisor token of this app; never logged."""
    return {"Authorization": f"Bearer {token}"}


async def supervisor_get(session: aiohttp.ClientSession, token: str, path: str) -> Any:
    """JSON answer of a GET to the Supervisor API at path (for example /addons/self/info),
    whatever its content type. An HTTP error status raises aiohttp.ClientResponseError, a
    body that is no JSON a ValueError; the caller logs and handles both."""
    headers = supervisor_headers(token)
    async with session.get(f"{SUPERVISOR}{path}", headers=headers, timeout=SUPERVISOR_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)

# Largest WebSocket message accepted from Home Assistant: the device registry lists the
# devices of all integrations in one message (aiohttp's default of 4 MiB is too small
# for very large installations)
REGISTRY_MAX = 32 * 1024 * 1024  # bytes
# Readable name of a Core WebSocket list request in error texts (unknown ones: the request)
HA_REQUEST_NAMES = {
    "config/device_registry/list": "device registry",
    "config/entity_registry/list": "entity registry",
    "config/area_registry/list": "area registry",
    "config_entries/get": "config entries",
    "config_entries/flow/progress": "discovery flows",
}
# Seconds a Home Assistant answer (device registry, battery sensors, areas) is reused by
# single checks; a full check and "Try again" always ask anew (see ManagerCore._ha_cached)
HA_CACHE_S = 60

# Pages of the Home Assistant frontend: the page of this app (its info card has the
# "Changelog" link next to the version) and, if the slug is unknown, the list of apps
APP_PAGE = "/config/app/{slug}/info"
APP_LIST_PAGE = "/config/apps"
APP_SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")  # slug as the Supervisor reports it
DOWNLOAD_PORT_KEY = "8098/tcp"  # container port of the download server (config.yaml "ports")
MAX_PORT = 65535  # highest TCP port

# Per-device activity during a check (memory only, shown in the device row)
ACTIVITY_WAITING = "waiting"  # part of the running check, not yet reached

ACTIVITY_CHECKING = "checking"  # device / manufacturer server is being queried

ACTIVITY_DOWNLOADING = "downloading"  # firmware for this device is being downloaded

ACTIVITY_RELOADING = "reloading"  # Reload Device Info is reading the device

ACTIVITY_SERVER = "server"  # the check waits for the answer of the Shelly update server

ACTIVITY_RESTARTING = "restarting"  # restarted from the app, waiting for its answer

PART_PREFIX = ".part-"  # temporary download files (see shelly_cloud._store)

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
    "not_private": "Installations are disabled: the IP address of the download base URL is not in a "
    "private network (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7). Correct it in the "
    "Settings tab.",
    "invalid": "Installations are disabled: the download base URL is not a valid address "
    "(http(s)://host:port, letters, digits, dots and hyphens in the host). Correct it in the Settings tab.",
}

# No check before the first-start question is answered (fresh installation)
FIRST_START_BLOCK = "Not before the first-start question is answered: import a firmware store or skip it"


def severity_of(err: BaseException) -> str:
    """Log severity of a device error: authentication errors, a device that requires
    HTTPS (both stay until the user acts) and internal errors are errors; unreachable
    devices, timeouts and temporary/missing data warnings."""
    if isinstance(err, (AuthError, HttpsRequiredError)):
        return "error"
    if not isinstance(err, (TimeoutError, ShellyError, aiohttp.ClientError, OSError)):
        return "error"
    return "warning"


def unreachable(err: BaseException) -> bool:
    """True if the device did not answer at all (timeout, connection failure) – not
    for an answer the device gave, e.g. a refused password."""
    return isinstance(err, (TimeoutError, aiohttp.ClientConnectionError, OSError))


def unexpected_text(err: BaseException) -> str:
    """Status message for an error no code path expects (e.g. a device answer of an
    unexpected shape): names the error type; the traceback is in the app log."""
    return clip(f"Unexpected error ({type(err).__name__}: {error_text(err, 300)}) – see the app log", 500)


class Pending:
    """What a dialog holds for a short time under a random token: a staged file, a
    prepared download or export (memory only; a file it names lies below /data).

    One rule for all of them: a wrong token is refused and drops nothing (it can only
    come from an outdated tab – 128 bit are not guessed); the payload is dropped
    exactly when its time is up, and on_drop removes what it left on disk; take uses
    it up (a download, an export, a confirmed import). Creating a new one drops the
    one held before."""

    def __init__(self) -> None:
        """Nothing held."""
        self.payload: dict[str, Any] | None = None  # None while nothing is held; use get/take
        self._token = ""
        self._ttl = 0.0
        self._deadline = 0.0
        self._on_drop: Callable[[dict[str, Any]], None] | None = None
        self._timer: asyncio.TimerHandle | None = None

    def create(
        self, payload: dict[str, Any], ttl: float, on_drop: Callable[[dict[str, Any]], None] | None = None
    ) -> str:
        """Hold payload for ttl seconds; returns its new token."""
        self.drop()
        self.payload, self._token, self._ttl, self._on_drop = payload, secrets.token_hex(16), ttl, on_drop
        self.renew()
        return self._token

    def renew(self) -> None:
        """The full time again from now (the next step of a dialog gets all of it)."""
        if self.payload is None:
            return
        self._deadline = time.monotonic() + self._ttl
        if self._timer is not None:
            self._timer.cancel()
        try:
            self._timer = asyncio.get_running_loop().call_later(max(self._ttl, 0), self.drop)
        except RuntimeError:  # no event loop (direct calls): get and take check the time
            self._timer = None

    def get(self, token: object) -> dict[str, Any] | None:
        """The payload held under token; None if nothing is held, its time is up or the
        token is another one (nothing is dropped then)."""
        if self.payload is not None and time.monotonic() >= self._deadline:
            self.drop()
        if self.payload is None or not same_secret(token, self._token):
            return None
        return self.payload

    def take(self, token: object) -> dict[str, Any] | None:
        """As get, and nothing is held afterwards. on_drop is not called: the caller
        uses what the payload names (or removes it)."""
        payload = self.get(token)
        if payload is not None:
            self._clear()
        return payload

    def drop(self) -> None:
        """Forget the payload and remove what it left behind (on_drop)."""
        payload, on_drop = self.payload, self._on_drop
        self._clear()
        if payload is not None and on_drop is not None:
            on_drop(payload)

    def _clear(self) -> None:
        """Nothing held any more; the timer is stopped."""
        if self._timer is not None:
            self._timer.cancel()
        self.payload, self._token, self._on_drop, self._timer = None, "", None, None


class HaConnection:
    """One Core WebSocket connection to Home Assistant (through the Supervisor proxy) for
    the list requests of one check (see ManagerCore._ha_reads).

    The socket opens and authenticates on the first request (greeting auth_required, then
    the token, then auth_ok); the requests follow one after another on it with IDs counted
    from 1, as Home Assistant requires per connection, and messages with other IDs are
    skipped. A refused request (success false) is a ShellyError and keeps the socket; any
    other failure (timeout, a socket closed by the other side, a frame that is not a JSON
    object, a failed sign-in) closes it, and the next request opens a new one. A request
    that was sent is never repeated. close ends the connection for good: a request made
    afterwards (a task that inherited the scope) uses a socket of its own that is closed
    right after its answer, so no socket outlives its scope."""

    def __init__(self, session: aiohttp.ClientSession, token: str, since: float | None) -> None:
        """Nothing is opened yet. since: start (time.monotonic) of a scope that asks
        anew – answers asked for before it are not reused; None: answers of up to
        HA_CACHE_S are reused (see ManagerCore._ha_cached)."""
        self.since = since
        self.finished = False  # set by close: this connection opens no socket any more
        self._session = session
        self._token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._last_id = 0  # ID of the last request sent on the open socket
        self._lock = asyncio.Lock()  # one request at a time on the socket

    async def request(self, request: str, **params: str) -> list[Any]:
        """Answer of one list request (params are added to it); anything but a
        successful list answer is a ShellyError (or the timeout / connection error)."""
        async with self._lock:
            if self.finished:  # the scope has ended: one request on a socket of its own
                single = HaConnection(self._session, self._token, None)
                try:
                    return await single.request(request, **params)
                finally:
                    await single.close()
            try:
                answer = await self._exchange(request, params)
            except BaseException:  # the state of the socket is unknown: never used again
                await self._drop()
                raise
        if not answer.get("success"):
            raise ShellyError(clip(answer.get("error")))
        result = answer.get("result")
        return result if isinstance(result, list) else []

    async def close(self) -> None:
        """End of the scope: the socket is closed (after the request that is running)
        and never opened again."""
        async with self._lock:
            self.finished = True
            await self._drop()

    async def _exchange(self, request: str, params: dict[str, str]) -> dict[str, Any]:
        """Send one request (opening the socket if none is open) and return Home
        Assistant's answer to its ID."""
        ws = self._ws
        if ws is None or ws.closed:
            ws = await self._open(request)
        self._last_id += 1
        await ws.send_json({"id": self._last_id, "type": request, **params})
        while True:
            msg = await self._receive(ws, request)
            if msg.get("id") == self._last_id:
                return msg

    async def _open(self, request: str) -> aiohttp.ClientWebSocketResponse:
        """A new authenticated socket; the token is sent only after the greeting
        auth_required. request: the list request that needs it (for error texts)."""
        await self._drop()  # e.g. closed by the other side since the last request
        ws_url = SUPERVISOR.replace("http", "ws", 1) + "/core/websocket"
        ws = await self._session.ws_connect(
            ws_url, timeout=aiohttp.ClientWSTimeout(ws_close=30), max_msg_size=REGISTRY_MAX
        )
        self._ws, self._last_id = ws, 0
        if (await self._receive(ws, request)).get("type") != "auth_required":
            raise ShellyError("Unexpected WebSocket greeting")
        await ws.send_json({"type": "auth", "access_token": self._token})
        if (await self._receive(ws, request)).get("type") != "auth_ok":
            raise ShellyError("Authentication with Home Assistant failed")
        return ws

    @staticmethod
    async def _receive(ws: aiohttp.ClientWebSocketResponse, request: str) -> dict[str, Any]:
        """Next JSON object from Home Assistant (at most 30 s); anything else is a
        ShellyError. request: the list request it belongs to (for error texts)."""
        msg = await ws.receive(timeout=30)
        if msg.type is not aiohttp.WSMsgType.TEXT:
            # e.g. closed by aiohttp because the message exceeds REGISTRY_MAX
            name = HA_REQUEST_NAMES.get(request, request)
            raise ShellyError(f"Home Assistant did not answer the {name} request ({msg.type.name})")
        try:
            data = json.loads(msg.data)
        except ValueError:
            raise ShellyError("Unreadable answer of Home Assistant") from None
        if not isinstance(data, dict):
            raise ShellyError("Unexpected answer of Home Assistant")
        return data

    async def _drop(self) -> None:
        """Close the open socket, if any (the next request opens a new one)."""
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()


# HaConnection of the running check's scope (ManagerCore._ha_reads). A context variable,
# not an attribute: parallel single checks and "Try again" run as separate tasks, each
# with a scope of its own.
_HA_READS: contextvars.ContextVar[HaConnection | None] = contextvars.ContextVar("ha_reads", default=None)


def _ha_usable(started: float, since: float | None) -> bool:
    """True if a Home Assistant answer whose request started at started (time.monotonic)
    may be reused: younger than HA_CACHE_S and, in a scope that asks anew (since), asked
    for at or after that scope started."""
    return time.monotonic() - started < HA_CACHE_S and (since is None or started >= since)


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
        # Random mark of this app start: the Settings tab reloads once it changes (restart done)
        self.start_id = secrets.token_hex(8)
        self.check_days = int(options["check_interval_days"])
        self.check_time = options["check_time"]
        self.tz, self.tz_name = schedule.local_zone()
        # Optional, no default: installations are refused while it is unset
        self.download_base = (options.get("download_base_url") or "").rstrip("/") or None
        self.restrict_downloads = bool(options["restrict_downloads_to_devices"])
        self.gen1_enabled = bool(options["gen1_updates_enabled"])
        self.allow_downgrade = options.get("allow_downgrade") is True  # optional, missing = off
        # Gen2+: also offer the beta versions of the Shelly update server (optional, missing = off)
        self.beta_firmware = options.get("beta_firmware") is True
        # Host name column: look up host names at every check (off: no lookup, no column)
        self.resolve_host_names = options["resolve_host_names"] is True
        self.import_max = int(options["import_max_mb"]) * 1024 * 1024  # bytes; largest store import
        # Gen2+: also store the version a device runs while Shelly still offers it (way back)
        self.keep_installed = options["keep_installed"] is True
        # A new version is offered only once it is this many days old (build date); 0 = at once
        self.offer_after_days = int(options["offer_after_days"])
        # Backups of the device settings: one before every installation, and how many
        # of the newest are kept per device (see manager_backups)
        self.backup_before_install = options["backup_before_install"] is True
        self.backup_keep = int(options["backup_keep"])
        self.session = session
        self.token = token
        self.pins = pins  # certificate pins for shelly.cloud
        self.app_slug: str | None = None  # slug of this app (Supervisor), read on first use
        # Host port of the download server as the Supervisor maps it (None: switched off in
        # Home Assistant); host_port_known is False while the Supervisor could not be read
        self.host_port: int | None = None
        self.host_port_known = False
        self.lock = asyncio.Lock()  # check (exclusive)
        self.install_slots = asyncio.Semaphore(int(options.get("max_parallel_installs", 5)))
        self.installing: set[str] = set()  # device IDs: running or waiting
        # Device ID -> running step of its installation (see Installs.install_step_view; memory only)
        self.install_steps: dict[str, dict[str, Any]] = {}
        self.last_scan: int | None = None
        self.devices: dict[str, Device] = {}  # HA device ID -> state
        # file name -> {"served": {ip: n}, "rejected": {ip, ...}, "attributed": {device id: n}}
        # ("attributed": GETs from an address of no known device while only that device installed the file)
        self.downloads: dict[str, dict[str, Any]] = {}
        self.firmware: dict[str, FirmwareMeta] = {}  # file name -> metadata of the stored firmware
        self.activity: dict[str, str] = {}  # HA device ID -> ACTIVITY_* while a check or restart runs
        self.progress: dict[str, Any] | None = None  # running full check: {"kind", "total", "done", "errors", "phase"}
        self.device_checks: dict[str, str] = {}  # HA device ID -> "queued"/"running" (single-device checks)
        self.storage_error: str | None = None  # last failure writing state.json (shown in the UI)
        # Set at start if the state file was missing or unreadable although the app
        # was installed before (see _load); shown in the UI until the next restart
        self.state_warning: str | None = None
        self.settings_warning: str | None = None  # settings file damaged or with invalid values (set by main)
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
        # Schedules of the Schedules tab (see timetable.py), persisted; the name of the
        # schedule whose run is going on (memory only, see manager_schedules)
        self.schedules: list[dict[str, Any]] = []
        self.schedule_running: str | None = None
        # Devices whose installation result is notified by the caller (a scheduled
        # installation sends one notification of its own), memory only
        self.notify_muted: set[str] = set()
        self.reloading: set[str] = set()  # device IDs whose Reload Device Info is running
        # Device whose backup is being restored (one at a time): nothing else touches it meanwhile
        self.restoring: set[str] = set()
        # "Back up now" for several devices and the running or last restore, for the
        # dialogs of the UI (memory only, see manager_backups)
        self.backup_job: dict[str, Any] | None = None
        self.restore_job: dict[str, Any] | None = None
        self._backup_download = Pending()  # prepared download of one backup
        self._backup_import = Pending()  # staged backup file of an import
        # (firmware ID, version) -> running provide task (single flight)
        self._provisions: dict[tuple[str, str], asyncio.Future[tuple[str, str, int]]] = {}
        # (firmware ID, version) -> devices waiting for that provide task (full and
        # single-device checks alike); all of them show "downloading" while it downloads
        self._provision_waiters: dict[tuple[str, str], set[str]] = {}
        self._fetching: set[tuple[str, str]] = set()  # provide tasks that are downloading right now
        # Answers of Home Assistant reused by single checks (see _ha_cached; memory only):
        # key ("devices", "batteries", "areas") -> (time.monotonic when its request
        # started, answer); the timer that drops it after HA_CACHE_S; the read running
        # for a key (start time, result shared by every caller waiting for it)
        self._ha_cache: dict[str, tuple[float, object]] = {}
        self._ha_drop: dict[str, asyncio.TimerHandle] = {}
        self._ha_reading: dict[str, tuple[float, asyncio.Future[object]]] = {}
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
        # Backup made right before the running installation of a device (ID), for the
        # comparison after the update (see Backups.compare_after_update)
        self._before_backups: dict[str, str] = {}
        self._upload = Pending()  # staged manual upload (see stage_upload_part)
        self._upload_lock = asyncio.Lock()  # one upload transfer at a time
        self._export = Pending()  # prepared store export (see prepare_export)
        self._import = Pending()  # staged store import (see stage_import_part)
        self._import_lock = asyncio.Lock()  # one import transfer / check / confirmation at a time
        self._import_storing = False  # confirm_import is writing its files to the store
        # File transfers in parts (upload, import) that are not complete yet, by kind;
        # memory only (see Transfers._receive_part)
        self._receiving: dict[str, dict[str, Any]] = {}
        # First start of a fresh installation (no state.json): no check runs until the
        # user answered the import question (import a store or skip); persisted
        self.first_start_open = False
        self.first_start_notified = False  # the HA notification about the question was sent
        # Card "Getting started" of the Devices tab: shown on a fresh installation until
        # the user hides it; persisted (missing in older states = not shown)
        self.getting_started = False
        # Result of the last connection test of the download server (see
        # Installs.run_connection_test): {"at", "ok", "url", "device", "ip", "seconds", "error"};
        # persisted, valid only while "url" is the current download base URL
        self.connection_test: dict[str, Any] | None = None
        # Running connection test (memory only): {"token", "ip", "fetched", "from"}
        self._test: dict[str, Any] | None = None
        # Set whenever the schedule changes (check finished): wakes the scheduler in
        # main.py instead of letting it poll every minute.
        self.schedule_changed = asyncio.Event()
        # Set whenever a schedule or a scheduled installation changes: wakes the loop of
        # the Schedules tab in main.py (run_timetable)
        self.timetable_changed = asyncio.Event()
        data_paths.FW_DIR.mkdir(parents=True, exist_ok=True)
        self._remove_partial_downloads()
        self.credentials = Credentials(data_paths.CREDENTIALS_FILE)
        self.changelogs = Changelogs(data_paths.CHANGELOG_FILE)
        self.events = EventLog(data_paths.EVENTS_FILE, options["log_retention_days"], options["log_entry_limit"])
        self.backups = BackupStore(data_paths.BACKUP_DIR)
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
                self.getting_started = True
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
            dev.pop("downgrade_from", None)  # and the section the device had before it
            if "planned" in dev and not timetable.check_plan(dev["planned"]):
                log.warning("%s: damaged scheduled installation dropped", plain(dev.get("name"), 64))
                dev.pop("planned")

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
        self.schedules = self._stored_schedules(data.get("schedules"))
        self.first_start_open = data.get("first_start_open") is True
        self.first_start_notified = data.get("first_start_notified") is True
        self.gen1_marking_due = data.get("gen1_imports_marked") is not True
        self.getting_started = data.get("getting_started") is True
        test = data.get("connection_test")
        self.connection_test = test if isinstance(test, dict) and isinstance(test.get("url"), str) else None
        unknown = data.get("unknown_files")
        if isinstance(unknown, list):
            self.unknown_files = {str(n): 0 for n in unknown if isinstance(n, str)}
        others = data.get("others")
        self.others = [o for o in others if isinstance(o, dict)] if isinstance(others, list) else []
        listing = data.get("listing")
        if isinstance(listing, dict) and isinstance(listing.get("missing"), list):
            listed = listing.get("listed")
            self.listing = {
                # Only a whole number is taken over; anything else counts as 0
                "listed": listed if isinstance(listed, int) and not isinstance(listed, bool) and listed >= 0 else 0,
                "missing": [m for m in listing["missing"] if isinstance(m, dict)],
            }

    @staticmethod
    def _stored_schedules(stored: object) -> list[dict[str, Any]]:
        """Schedules of the state file; a damaged entry (e.g. edited by hand) is
        dropped with a warning instead of stopping the app at every start."""
        schedules: list[dict[str, Any]] = []
        for item in stored if isinstance(stored, list) else []:
            try:
                schedules.append(timetable.check_stored(item))
            except (ValueError, TypeError, KeyError) as err:
                name = item.get("name") if isinstance(item, dict) else None
                log.warning("Damaged schedule %s dropped from the state file: %s", plain(name, 64) or "?", err)
        return schedules[: timetable.MAX_SCHEDULES]

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
            "schedules": self.schedules,
            "first_start_open": self.first_start_open,
            "first_start_notified": self.first_start_notified,
            "gen1_imports_marked": not self.gen1_marking_due,
            "getting_started": self.getting_started,
            "connection_test": self.connection_test,
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
        The registry of all integrations arrives as one message (REGISTRY_MAX). The
        answer is reused for up to HA_CACHE_S (see _ha_cached): the list is a copy, but
        its entries are shared – never change them."""
        return list(await self._ha_cached("devices", lambda: self._ha_list("config/device_registry/list")))

    async def _area_registry(self) -> list[Any]:
        """Area registry via the Core WebSocket API: [{"area_id", "name", …}]; reused
        like the device registry (see _registry)."""
        return list(await self._ha_cached("areas", lambda: self._ha_list("config/area_registry/list")))

    async def _battery_devices(self) -> set[str]:
        """HA device IDs with a battery sensor of the Shelly integration (see
        _read_battery_devices), reused for up to HA_CACHE_S (see _ha_cached). Empty if
        the request fails (no conclusion, logged; a failure is never reused)."""
        try:
            return set(await self._ha_cached("batteries", self._read_battery_devices))
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.warning("Entity registry of Home Assistant not available: %s", err or type(err).__name__)
            return set()

    async def _read_battery_devices(self) -> frozenset[str]:
        """HA device IDs with a battery sensor of the Shelly integration (entity
        registry: platform shelly, unique ID "<mac>-<component>-battery" – Gen1
        device_0, Gen2+ devicepower:0; Home Assistant removes it for externally
        powered devices). Home Assistant does not hand out the sleep period of its
        config entry over the API, so this sensor is the only hint of a battery
        device that never answered the app. Only this set is kept, not the entity
        registry. Raises like _ha_list."""
        entities = await self._ha_list("config/entity_registry/list")
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
        return frozenset(found)

    async def _ha_setup_state(self) -> tuple[list[Any], list[Any]]:
        """Config entries of the Shelly integration (config_entries/get; ignored
        discoveries have source "ignore") and the flows in progress (discoveries
        not set up yet, config_entries/flow/progress) – for the Shellys Home
        Assistant knows but the app does not list. Only the full check asks for them,
        so they are never reused; inside its scope they use its connection. Raises
        like _ha_list."""
        entries = await self._ha_list("config_entries/get", domain="shelly")
        flows = await self._ha_list("config_entries/flow/progress")
        return entries, flows

    @contextlib.asynccontextmanager
    async def _ha_reads(self, fresh: bool = False) -> AsyncIterator[None]:
        """Scope of the Home Assistant part of one check: every list request inside it
        (_ha_list) uses one HaConnection, opened on the first request that is not
        answered from the cache – a scope whose answers are all reused opens none. At
        the end the socket is closed and the connection is finished. fresh: answers
        asked for before the scope started are not reused (full check, "Try again");
        what it reads refreshes the cache for the next single check. Ends before any
        device is contacted, so no socket is held during device access."""
        scope = HaConnection(self.session, self.token, time.monotonic() if fresh else None)
        reset = _HA_READS.set(scope)
        try:
            yield
        finally:
            _HA_READS.reset(reset)
            await scope.close()

    async def _ha_cached[T](self, key: str, read: Callable[[], Awaitable[T]]) -> T:
        """Answer of Home Assistant under key ("devices", "batteries", "areas"): the
        stored one while _ha_usable allows it, else read() (single flight: callers
        that ask while a read of the key runs wait for it and share its answer or its
        failure; a fresh scope does not take a read that started before it, and waits
        for it to end and then reads itself). A success is stored with the time its
        request started and dropped by a timer after HA_CACHE_S; a failure is never
        stored – it reaches only the callers waiting at that moment, and the answer
        stored before is dropped. A cancelled reader hands over to a waiting caller,
        which then reads itself; a cancelled waiter leaves the read running."""
        scope = _HA_READS.get()
        since = scope.since if scope is not None else None
        while True:
            cached = self._ha_cache.get(key)
            if cached is not None and _ha_usable(cached[0], since):
                return cast(T, cached[1])
            running = self._ha_reading.get(key)
            if running is None:
                break
            started, shared = running
            await asyncio.wait({shared})  # not cancelled with this caller
            if shared.cancelled() or (since is not None and started < since):
                continue  # its reader was cancelled, or it was asked for before this fresh scope
            return cast(T, shared.result())  # the answer, or the failure raised again
        started = time.monotonic()
        shared = asyncio.get_running_loop().create_future()
        self._ha_reading[key] = (started, shared)
        try:
            value = await read()
            self._ha_store(key, started, value)
            shared.set_result(value)
            return value
        except Exception as err:
            self._ha_forget(key)
            shared.set_exception(err)
            shared.exception()  # marked as retrieved: without waiters nothing is logged for it
            raise
        except BaseException:  # cancelled: a waiting caller reads itself
            shared.cancel()
            raise
        finally:
            # Dropped with the read: the failure (and its traceback) is held only while
            # callers wait for it
            del self._ha_reading[key]

    def _ha_store(self, key: str, started: float, value: object) -> None:
        """Keep an answer of Home Assistant (see _ha_cached) until HA_CACHE_S after its
        request started; the timer then frees it (the device registry may be large)."""
        self._ha_forget(key)
        self._ha_cache[key] = (started, value)
        delay = max(started + HA_CACHE_S - time.monotonic(), 0)
        self._ha_drop[key] = asyncio.get_running_loop().call_later(delay, self._ha_forget, key)

    def _ha_forget(self, key: str) -> None:
        """Drop the stored answer of key and stop its timer."""
        timer = self._ha_drop.pop(key, None)
        if timer is not None:
            timer.cancel()
        self._ha_cache.pop(key, None)

    async def _ha_list(self, request: str, **params: str) -> list[Any]:
        """Answer of one list request of the Core WebSocket API (device, entity or area
        registry, config entries, flows); params are added to the request; anything
        but a successful list answer is a ShellyError (see HaConnection). Inside a
        check's scope (_ha_reads) all requests share its connection; outside one the
        request gets a connection of its own."""
        scope = _HA_READS.get()
        if scope is not None:
            return await scope.request(request, **params)
        async with self._ha_reads():
            return await self._ha_list(request, **params)

    async def notify(self, title: str, message: str, notification_id: str) -> bool:
        """Persistent notification in Home Assistant. True if Home Assistant accepted
        it; errors are only logged."""
        try:
            async with self.session.post(
                f"{SUPERVISOR}/core/api/services/persistent_notification/create",
                headers=supervisor_headers(self.token),
                json={"title": title, "message": message, "notification_id": notification_id},
                timeout=SUPERVISOR_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
        except (TimeoutError, aiohttp.ClientError) as err:
            log.warning("Notification failed: %s", err)
            return False
        return True

    async def ha_version(self) -> str | None:
        """Version of Home Assistant (Core API /config), None if it cannot be read
        (only for "Details for a bug ticket")."""
        try:
            version = (await supervisor_get(self.session, self.token, "/core/api/config")).get("version")
        except (TimeoutError, aiohttp.ClientError, ValueError, AttributeError) as err:
            log.warning("Version of Home Assistant not available: %s", err or type(err).__name__)
            return None
        return clip(version, 40) if isinstance(version, str) else None

    async def _supervisor_post(self, path: str, body: dict[str, Any] | None) -> None:
        """POST to the Supervisor API (only /addons/self/* is used); errors -> ShellyError."""
        try:
            async with self.session.post(
                f"{SUPERVISOR}{path}",
                headers=supervisor_headers(self.token),
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
                info = await supervisor_get(self.session, self.token, "/addons/self/info")
                slug = (info.get("data") or {}).get("slug")
            except (TimeoutError, aiohttp.ClientError, ValueError, AttributeError) as err:
                log.warning("App info not available from the Supervisor: %s", err or type(err).__name__)
                return APP_LIST_PAGE
            if not isinstance(slug, str) or not APP_SLUG_RE.fullmatch(slug):
                log.warning("App info from the Supervisor has no valid slug")
                return APP_LIST_PAGE
            self.app_slug = slug
        return APP_PAGE.format(slug=self.app_slug)

    async def read_host_port(self) -> None:
        """Read the host port of the download server from the Supervisor (/addons/self/info,
        "network": {"8098/tcp": port or null}, open to every app). Sets host_port and
        host_port_known; a failure is logged and leaves the port unknown."""
        try:
            info = await supervisor_get(self.session, self.token, "/addons/self/info")
            network = (info.get("data") or {}).get("network")
        except (TimeoutError, aiohttp.ClientError, ValueError, AttributeError) as err:
            log.warning("Host port not readable from the Supervisor: %s", err or type(err).__name__)
            self.host_port_known = False
            return
        port = network.get(DOWNLOAD_PORT_KEY, False) if isinstance(network, dict) else False
        if port is None or (isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= MAX_PORT):
            self.host_port, self.host_port_known = port, True
        else:
            log.warning("Supervisor reports no valid host port for %s", DOWNLOAD_PORT_KEY)
            self.host_port_known = False

    def _host_port_change(self, value: object) -> int | None:
        """Validated new host port of the Settings tab; None if it equals the current one.
        ValueError if it is no port or the current port is unknown (Supervisor not read)."""
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PORT:
            raise ValueError(f"host_port: whole number between 1 and {MAX_PORT} expected")
        if not self.host_port_known:
            raise ValueError(
                "host_port: the current port could not be read from Home Assistant – change it in the "
                "Configuration tab of the app (Network)"
            )
        return None if value == self.host_port else value

    @staticmethod
    async def _check_port_free(port: int, url: str) -> None:
        """Refuse a new host port that is already in use on the Home Assistant host: Docker
        maps the port before the app starts, so a port in use would stop the app from
        starting. The host of the download base URL (the LAN address of Home Assistant) is
        asked for a TCP connection (closed at once, nothing sent); one that is accepted
        means another program listens there. Without a usable address nothing is checked;
        a host name is resolved first and checked only if all its addresses lie in the LAN
        (shelly_net.resolve_lan_address), so the check never reaches outside the LAN."""
        try:
            host = urlsplit(url).hostname if url else None
        except ValueError:
            host = None
        if not host:
            log.warning("Host port %d not checked: no download base URL", port)
            return
        address = await resolve_lan_address(host)
        if address is None:
            log.warning("Host port %d not checked: %s is no address in the local network", port, plain(host, 100))
            return
        try:
            await port_open(address, port)
        except (TimeoutError, OSError):
            return  # refused or no answer: nothing listens on that port
        raise ValueError(f"host_port: port {port} is already in use on {plain(host, 100)} – choose another port")

    # ----------------------------------------------------------------------------------------- App options

    def options_view(self) -> dict[str, Any]:
        """App options for the Settings tab (secret only as set/not set)."""
        return app_options.public_view(self.options)

    async def save_options(self, changes: object, user: str | None = None) -> list[str]:
        """Validate and store in settings.json; the caller restarts the app unless only
        options of app_options.NO_RESTART changed (app_options.needs_restart).

        "host_port" is no option of settings.json: a new host port of the download server
        is set in Home Assistant (POST /addons/self/options {"network": …}, open to every
        app) and applies with the restart. The attempt is written to the activity log with
        the changed keys and values. Returns the list of changed keys (empty: nothing
        saved, no restart)."""
        port = None
        if isinstance(changes, dict) and "host_port" in changes:
            port = self._host_port_change(changes["host_port"])
            changes = {k: v for k, v in changes.items() if k != "host_port"}
        new, changed = app_options.merge(self.options, changes)
        if not changed and port is None:
            return []
        restart = port is not None or app_options.needs_restart(changed)
        # A restart would cancel running work; options without a restart can always be saved
        busy = self.busy or (f"Schedule “{self.schedule_running}” is running" if self.schedule_running else None)
        if busy and restart:
            raise ShellyError(f"Not possible – {busy}")
        diff = app_options.change_list(self.options, new, changed)
        if port is not None:
            await self._check_port_free(port, str(new.get("download_base_url") or ""))
            diff.append({"option": "host_port", "old": str(self.host_port or "off"), "new": str(port)})
            try:
                await self._supervisor_post("/addons/self/options", {"network": {DOWNLOAD_PORT_KEY: port}})
            except ShellyError as err:
                self.events.add("options", ok=False, changes=diff, user=user, error=str(err))
                raise
            self.host_port = port
            changed = [*changed, "host_port"]
        if new != self.options:
            try:
                app_options.write_settings(new)
            except OSError as err:
                error = f"Settings could not be stored: {err.strerror or type(err).__name__}"
                self.events.add("options", ok=False, changes=diff, user=user, error=error)
                raise ShellyError(error) from None
            self.settings_warning = None  # the stored set is complete and valid now
        log.info("App options changed by %s (%s)%s", user, ", ".join(changed), " – restarting" if restart else "")
        self.events.add("options", changes=diff, user=user)
        self.options = new
        if {"download_base_url", "host_port"} & set(changed) and self.connection_test is not None:
            self.connection_test = None  # a test result never outlives a changed address or host port
            self.save()
        return changed

    @property
    def download_base_issue(self) -> str | None:
        """Why installations are blocked by download_base_url, else None.

        "missing": not set (optional option without default). "not_private": an IP address
        outside the private networks, saved before the option refused it. "invalid": not a
        usable address (odd characters, bad host name). Both can only be corrected."""
        if not self.download_base:
            return "missing"
        problem = app_options.download_base_problem(self.download_base)
        if problem:
            return "not_private" if problem == app_options.DOWNLOAD_BASE_NOT_PRIVATE else "invalid"
        return None

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
        kind "all": only while nothing else runs (no single-device check, installation,
        Reload Device Info, restore or run of a schedule). kind "device": not during the full check, and not for
        a device that is being checked, installed, reloaded or restored – other devices may be
        checked or installed at the same time. No check starts while a store import
        writes its files; while a schedule runs only single-device checks start.
        Raises ShellyError otherwise."""
        if self.first_start_open:
            raise ShellyError(FIRST_START_BLOCK)
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        if kind != "device" and self.schedule_running is not None:
            raise ShellyError(f"Schedule “{self.schedule_running}” is running – please try again afterwards")
        if self._import_storing:
            raise ShellyError("Store import in progress – please try again in a moment")
        if self.reloading and (kind != "device" or device_id in self.reloading):
            raise ShellyError("Device info is being reloaded – please try again in a moment")
        if self.restoring and (kind != "device" or device_id in self.restoring):
            raise ShellyError("A backup is being restored – please try again afterwards")
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
        """Text of the busy state while a check, installations, reloading or a restore is
        running (the full check button is blocked while this is set). It is not shown
        under the page title: the rows, the progress line and the dialogs show what runs.
        A schedule run is not named here: the Schedules tab shows it under "Last run",
        the UI blocks the button by schedule_running."""
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
        if self.restoring:
            parts.append("Restoring a backup")
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
