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
Per firmware ID the two highest versions are kept.

Progress of a running check is kept in memory only (self.progress, self.activity):
the UI shows it while polling, nothing of it is persisted.
"""

import asyncio
import hmac
import json
import logging
import re
import time
from pathlib import Path

import aiohttp
import app_options
import schedule
import shelly
from changelog import Changelogs
from credentials import Credentials
from events import EventLog, worst
from shelly import UNSUPPORTED, AuthError, ShellyError, clip

log = logging.getLogger("sfm.manager")

DATA_DIR = Path("/data")
STATE_FILE = DATA_DIR / "state.json"
FW_DIR = DATA_DIR / "firmware"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
CHANGELOG_FILE = DATA_DIR / "changelog.json"
EVENTS_FILE = DATA_DIR / "events.json"

# Device status – shown like this in the UI
STATUS_CURRENT = "up-to-date"
STATUS_AVAILABLE = "available"  # update exists, firmware not (yet) downloaded
STATUS_READY = "ready"  # firmware downloaded, waiting for manual installation
STATUS_INSTALLING = "installing"
STATUS_UNCLEAR = "unconfirmed"  # installation without confirmed target version
STATUS_ERROR = "error"
STATUS_UNREGISTERED = "unregistered"
STATUS_SKIPPED = "skipped"  # Gen1 device while Gen1 updates are disabled
PENDING = (STATUS_READY, STATUS_INSTALLING)  # device holds a firmware file
# Status values of 2.0 – renamed in 2.1, translated once when loading
LEGACY_STATUS = {
    "current": STATUS_CURRENT,
    "unclear": STATUS_UNCLEAR,
    "not registered": STATUS_UNREGISTERED,
}

INSTALL_FIRST_CHECK_S = 30  # wait after the update command
INSTALL_POLL_S = 10  # then check every 10 s
INSTALL_NO_DOWNLOAD_S = 180  # no download request: give up after 3 min (a Shelly may
# still answer with the old version for a while after accepting the update)
INSTALL_WAIT_S = 300  # upper limit in case the Shelly gets stuck
KEEP_PER_TYPE = 2  # stored firmwares per firmware ID
SUPERVISOR = "http://supervisor"  # Supervisor API (Core API proxy below /core)
SUPERVISOR_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Per-device activity during a check (memory only, shown in the device row)
ACTIVITY_WAITING = "waiting"  # part of the running check, not yet reached
ACTIVITY_CHECKING = "checking"  # device / manufacturer server is being queried
ACTIVITY_DOWNLOADING = "downloading"  # firmware for this device is being downloaded
ACTIVITY_RELOADING = "reloading"  # Reload Device Info is reading the device
PART_PREFIX = ".part-"  # temporary download files (see shelly._store)

UI_TABLES = ("devices",)  # tables whose sort order/columns are stored
UI_VERSION = 3  # 3.0: column order, "action" replaces "eco"/"restart" (v2 is migrated)
COLUMN_RE = re.compile(r"^[a-z_]{1,32}$")
MAX_COLUMNS = 30

# Parallel device queries of one full check. Each Shelly is a small embedded HTTP
# server that is only asked by one request at a time; 4 devices in parallel cut
# the check time of larger installations by roughly a factor of 4 while keeping
# Wi-Fi and Home Assistant load low. Internal constant, not a setting.
CHECK_PARALLEL = 4
PASSWORD_MODES = ("store", "enable", "change", "remove", "forget")
OPTIONS_MIGRATION = 3  # state["migrations"]["options"]: Supervisor store cleaned for v3


def severity_of(err):
    """Log severity of a device error: authentication and internal errors are
    errors; unreachable devices, timeouts and temporary/missing data warnings."""
    if isinstance(err, AuthError) or not isinstance(err, (TimeoutError, ShellyError, aiohttp.ClientError, OSError)):
        return "error"
    return "warning"


class Manager:
    """Holds the state of all Shellys and controls checks, storage and installation."""

    def __init__(self, options, session: aiohttp.ClientSession, token: str, pins=None, version=""):
        """Apply the options and load the stored state, passwords, changelogs and log."""
        options = app_options.with_defaults(options)
        self.options = options  # complete option set, needed to save changes
        self.check_days = int(options["check_interval_days"])
        self.check_time = options["check_time"]
        self.tz, self.tz_name = schedule.local_zone()
        self.download_base = options["download_base_url"].rstrip("/")
        self.restrict_downloads = bool(options["restrict_downloads_to_devices"])
        self.gen1_allowed = frozenset(options.get("gen1_allowed_types", []))
        self.gen1_enabled = bool(options["gen1_updates_enabled"])
        self.session = session
        self.token = token
        self.pins = pins  # certificate pins for shelly.cloud
        self.version = version  # app version (stored with every check)
        self.lock = asyncio.Lock()  # check (exclusive)
        self.install_slots = asyncio.Semaphore(int(options.get("max_parallel_installs", 5)))
        self.installing = set()  # device IDs: running or waiting
        self.last_scan = None
        self.scanned_version = None  # app version of the last check
        self.devices = {}  # HA device ID -> state
        self.downloads = {}  # file name -> {"served": n, "rejected": {ip, ...}}
        self.firmware = {}  # file name -> metadata of the stored firmware
        self.ui = {}  # table settings (sort order, hidden columns) for all users
        self.activity = {}  # HA device ID -> ACTIVITY_* while a check runs
        self.progress = None  # running check: {"kind", "total", "done", "errors", "phase"}
        self.storage_error = None  # last failure writing state.json (shown in the UI)
        self.schedule = None  # automatic check schedule (see schedule.py), persisted
        self.migrations = {}  # completed one-time migrations, persisted
        self.reloading = set()  # device IDs whose Reload Device Info is running
        self._provisions = {}  # (firmware ID, version) -> running provide task (single flight)
        self._downloads_running = 0  # for the progress phase of a parallel check
        # Set whenever the schedule changes (check finished): wakes the scheduler in
        # main.py instead of letting it poll every minute.
        self.schedule_changed = asyncio.Event()
        FW_DIR.mkdir(parents=True, exist_ok=True)
        self._remove_partial_downloads()
        self.credentials = Credentials(CREDENTIALS_FILE)
        self.changelogs = Changelogs(CHANGELOG_FILE)
        # v3 limit option: every install starts once at the default of 1000 (the old
        # log_max_entries key is dropped by the Supervisor); pruning trims at once.
        self.events = EventLog(EVENTS_FILE, options["log_retention_days"], options["log_entry_limit"])
        self._load()
        self.schedule, changed = schedule.configure(
            self.schedule, self.check_days, self.check_time, time.time(), self.tz
        )
        if changed:  # new install, v2 upgrade or changed interval/time: anchor anew
            self.save()

    # ------------------------------------------------------------ Persistence

    def _load(self):
        """Load the state of the last run; missing or broken file = fresh start."""
        try:
            data = json.loads(STATE_FILE.read_text())
            if not isinstance(data, dict):
                raise ValueError("state is not an object")
            self.devices = data.get("devices") or {}
            self.last_scan = data.get("last_scan")
            self.scanned_version = data.get("scanned_version")
            self.firmware = data.get("firmware") if isinstance(data.get("firmware"), dict) else {}
            if isinstance(data.get("ui"), dict) and data.get("ui_version") in (2, UI_VERSION):
                self.ui = data["ui"] if data["ui_version"] == UI_VERSION else self._migrate_ui(data["ui"])
            self.schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else None
            self.migrations = data.get("migrations") if isinstance(data.get("migrations"), dict) else {}
        except FileNotFoundError:
            self.migrations = {"options": OPTIONS_MIGRATION}  # fresh install: nothing to clean
            return
        except (ValueError, OSError) as err:
            log.warning("State file unreadable, starting fresh: %s", err)
            return
        for dev in self.devices.values():
            dev["status"] = LEGACY_STATUS.get(dev.get("status"), dev.get("status"))
            # Before 2.2.1 only the last attempt was stored: a device without error
            # was checked successfully at that time.
            if "last_success" not in dev and dev.get("status") not in (STATUS_ERROR, None):
                dev["last_success"] = dev.get("last_check")
            # A restart during an installation leaves an unclear state
            if dev.get("status") == STATUS_INSTALLING:
                dev.update(status=STATUS_UNCLEAR, message="App was restarted during the installation")

    @staticmethod
    def _migrate_ui(old):
        """v2 table settings -> v3: "eco"/"restart" become the "action" column
        (hidden only if both were hidden), the removed AOP column is dropped
        (rc3: AOP is an icon in the action column), a sort on a removed column
        falls back to the device name."""
        entry = old.get("devices") if isinstance(old.get("devices"), dict) else {}
        hidden = {c for c in entry.get("hidden") or [] if isinstance(c, str) and COLUMN_RE.match(c)}
        if {"eco", "restart"} <= hidden:
            hidden.add("action")
        hidden -= {"eco", "restart", "aop"}
        sort = entry.get("sort")
        if not (isinstance(sort, list) and len(sort) == 2 and sort[0] not in ("eco", "restart", "aop")):
            sort = ["device", "asc"]
        return {"devices": {"sort": sort, "hidden": sorted(hidden)}}

    async def migrate_options(self):
        """One-time v3 cleanup of the options stored by the Supervisor.

        The Supervisor drops unknown keys only from what it passes to the app;
        its own store may still hold the removed shared password. Saving the
        current, already cleaned option set once replaces that store. Runs at
        start before any device check; the value is never read or logged. On
        failure it is retried at the next start."""
        if self.migrations.get("options") == OPTIONS_MIGRATION:
            return True
        try:
            await self._supervisor_post("/addons/self/options", {"options": self.options})
        except ShellyError as err:
            log.warning("Cleaning the stored app options failed, retrying at the next start: %s", err)
            return False
        self.migrations = {"options": OPTIONS_MIGRATION}
        self.save()
        log.info("Stored app options cleaned for v3 (removed options deleted)")
        return True

    @staticmethod
    def _remove_partial_downloads():
        """Delete temporary download files left behind by a crash or restart.
        They are never offered for installation (only <sha256>.<ext> names are)."""
        for path in FW_DIR.glob(PART_PREFIX + "*"):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
                log.info("Removed incomplete download %s", path.name)

    def save(self):
        """Write the state atomically (temporary file first, then rename).

        A write error (e.g. full disk) keeps the previous state file intact, is
        logged and shown in the UI – it must not abort a check or installation."""
        temp = STATE_FILE.with_suffix(".tmp")
        state = {
            "devices": self.devices,
            "last_scan": self.last_scan,
            "scanned_version": self.scanned_version,
            "firmware": self.firmware,
            "ui": self.ui,
            "ui_version": UI_VERSION,
            "schedule": self.schedule,
            "migrations": self.migrations,
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

    def set_ui(self, prefs):
        """Store table settings shared by all users. Raises ValueError on bad input."""
        clean = {}
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
            if not valid_sort or not valid_lists:
                raise ValueError(f"Invalid table settings for {table}")
            if not all(isinstance(c, str) and COLUMN_RE.match(c) for c in [*hidden, *order]):
                raise ValueError(f"Invalid column name for {table}")
            clean[table] = {"sort": sort, "hidden": sorted(set(hidden)), "order": list(dict.fromkeys(order))}
        self.ui = clean
        self.save()

    # ------------------------------------------------------------ Firmware store

    def _register_firmware(self, name, fw_type, gen, version, sha, size, https=None):
        """Add a firmware to the store (basis for reuse and retention).
        https: whether the download was protected by TLS (Gen1: never)."""
        self.firmware[name] = {
            "type": clip(fw_type, 64),
            "gen": gen,
            "version": clip(version, 64),
            "sha256": sha,
            "size": size,
            "downloaded": int(time.time()),
            "https": bool(https) if https is not None else gen != 1,
        }

    def _in_use(self):
        """Files a device currently holds as update or is installing."""
        return {d["file"] for d in self.devices.values() if d.get("status") in PENDING and d.get("file")}

    def cleanup_firmware(self):
        """Keep only the two highest versions per firmware ID.

        Files currently provided for a device are kept in addition.
        Missing files disappear from the store, unknown files from the disk."""
        self.firmware = {n: m for n, m in self.firmware.items() if (FW_DIR / n).is_file()}
        by_type = {}
        for name, meta in self.firmware.items():
            by_type.setdefault(meta.get("type"), []).append(name)
        in_use = self._in_use()
        keep = set(in_use)
        for names in by_type.values():
            names.sort(
                key=lambda n: shelly.parse_version(self.firmware[n].get("version") or ""), reverse=True
            )
            keep.update(names[:KEEP_PER_TYPE])
        self.firmware = {n: m for n, m in self.firmware.items() if n in keep}
        for path in FW_DIR.iterdir():
            if path.name not in keep:
                path.unlink(missing_ok=True)
        # Download diagnosis only for files currently provided
        self.downloads = {k: v for k, v in self.downloads.items() if k in in_use}

    async def _stored_firmware(self, fw_type, version):
        """Already stored firmware for ID and version – after checksum verification."""
        for name, meta in list(self.firmware.items()):
            if meta.get("type") == fw_type and meta.get("version") == version:
                try:
                    if await asyncio.to_thread(shelly.file_sha256, FW_DIR, name) == meta.get("sha256"):
                        return name, meta
                except (OSError, ShellyError):
                    pass
                log.warning("Stored firmware %s is damaged and will be downloaded again", name)
                self.firmware.pop(name, None)
        return None

    def firmware_list(self):
        """Stored firmwares for the "Firmware" tab."""
        users = {}  # file name -> devices it is provided for
        for dev in self.devices.values():
            if dev.get("status") in PENDING and dev.get("file"):
                users.setdefault(dev["file"], []).append(dev.get("name") or "?")
        installing = {self.devices.get(i, {}).get("file") for i in self.installing}
        return [
            {
                "name": name,
                **{k: meta.get(k) for k in ("type", "gen", "version", "sha256", "size", "downloaded")},
                "https": meta.get("https", meta.get("gen") != 1),
                "used_by": sorted(users.get(name, [])),
                "deletable": not self.checking and name not in installing,
            }
            for name, meta in self.firmware.items()
        ]

    def delete_firmware(self, name, user="unknown"):
        """Delete a stored firmware. Blocked during a check or an installation using it.
        Devices it was provided for download it again at the next check."""
        if name not in self.firmware:
            raise KeyError(name)
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        if name in {self.devices.get(i, {}).get("file") for i in self.installing}:
            raise ShellyError("Firmware is being installed")
        for dev in self.devices.values():
            if dev.get("file") == name and dev.get("status") == STATUS_READY:
                dev.update(
                    status=STATUS_AVAILABLE,
                    file=None,
                    sha256=None,
                    message="Firmware deleted – will be downloaded again at the next check",
                )
        meta = self.firmware.pop(name)
        (FW_DIR / name).unlink(missing_ok=True)
        log.info("Firmware %s deleted by %s", name, user)
        self.events.add(
            "delete", type=meta.get("type"), version=meta.get("version"), sha256=meta.get("sha256"), user=user
        )
        self.save()

    # ------------------------------------------------------------ Home Assistant

    async def _registry(self):
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
                    return msg["result"]

    async def notify(self, title, message, notification_id):
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

    async def _supervisor_post(self, path, body):
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

    def options_view(self):
        """App options for the Settings tab (secret only as set/not set)."""
        return app_options.public_view(self.options)

    async def save_options(self, changes, user=None):
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
            self.events.add("options", ok=False, changes=diff, user=user, error=clip(err, 300))
            raise
        log.info("App options changed by %s (%s) – restarting", user, ", ".join(changed))
        self.events.add("options", changes=diff, user=user)
        self.options = new
        return changed

    async def restart_app(self):
        """Ask the Supervisor to restart this app so new options take effect."""
        await self._supervisor_post("/addons/self/restart", None)

    # ------------------------------------------------------------ Check

    @property
    def checking(self):
        """True while a check runs or has been reserved by a request (see reserve_check)."""
        return self.lock.locked() or self.progress is not None

    def reserve_check(self, kind="all", device_id=None):
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
            self.progress = {
                "kind": "device",
                "device": self.devices[device_id].get("name"),
                "total": 1,
                "done": 0,
                "errors": 0,
                "phase": "queued",
            }
            self.activity[device_id] = ACTIVITY_WAITING
        else:
            self.progress = {"kind": "all", "total": 0, "done": 0, "errors": 0, "phase": "queued"}

    def _reserved(self, kind, device_id=None):
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
    def busy(self):
        """Text for the UI while a check or installations are running."""
        if self.checking:
            if self.progress and self.progress.get("kind") == "device":
                return f"Checking {self.progress.get('device') or 'device'}"
            return "Check in progress"
        if self.installing:
            return f"Installations running: {len(self.installing)}"
        return None

    def scan_due_in(self, now):
        """Seconds until the next automatic check; 0 = due now (see schedule.py).

        Only the schedule counts: neither an app update, a manual check nor a
        Reload Device Info shifts it."""
        return schedule.seconds_until(self.schedule, now, self.tz)

    async def scan(self, automatic=False, user=None):
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
            failure, checked = None, []
            self.progress["phase"] = "registry"
            try:
                checked = await self._scan_all()
                self.progress["phase"] = "changelog"
                await self._refresh_changelogs()
            except Exception as err:  # report overall errors (e.g. registry), do not crash
                log.exception("Check failed")
                failure = clip(err, 500) or type(err).__name__
                await self.notify("Shelly firmware check failed", failure, "sfm_summary")
            finally:
                # Also runs on cancellation (app stop): no device stays "checking"
                self.activity.clear()
                self.progress = None
                self.last_scan = int(time.time())
                self.scanned_version = self.version
                self.cleanup_firmware()
                self.save()
                self.schedule_changed.set()
            self._log_check(checked, automatic, user, time.monotonic() - started, failure)
        if automatic:
            await self._notify_new_updates()
        return True

    def _log_check(self, checked, automatic, user, duration, failure, trigger=None):
        """One log entry per check; findings = devices with an update, a warning or
        an error. trigger overrides the derived value ("device" for single-device checks).
        Every finding carries a severity; "error" holds the message of warnings
        and errors (key kept for historic entries). The entry gets the highest
        severity of its findings, an overall failure is an error."""
        findings = []
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
                        "severity": "success",
                    }
                )
            problem = dev.get("settings_error")
            if problem:  # firmware checked, device settings/status not readable
                findings.append(
                    {"device": dev.get("name"), "error": f"Device status: {problem['message']}", "severity": problem["severity"]}
                )
        counts = {level: sum(1 for f in findings if f["severity"] == level) for level in ("warning", "error")}
        updates = sum(1 for f in findings if "offer" in f)
        self.events.add(
            "check",
            severity=worst("error" if failure else "success", *(f["severity"] for f in findings)),
            trigger=trigger or ("scheduled" if automatic else "manual"),
            user=None if automatic else user or "unknown",
            devices=len(checked),
            duration=round(duration, 1),
            result=failure or f"{updates} update(s), {counts['warning']} warning(s), {counts['error']} error(s)",
            updates=updates,
            warnings=counts["warning"],
            errors=counts["error"],
            findings=findings,
        )

    async def _refresh_changelogs(self):
        """Fetch the changelog pages once per check – only if updates were found,
        only for the generations concerned, never with device data."""
        await self._refresh_changelogs_for(self.devices.values())

    async def _refresh_changelogs_for(self, devices):
        """Refresh the changelogs of the generations of devices with an update;
        the offered versions let a fresh stored changelog be reused."""
        wanted = {}  # 1 | 2 -> offered versions
        for d in devices:
            if d.get("status") in (STATUS_READY, STATUS_AVAILABLE):
                wanted.setdefault(1 if d.get("gen") == 1 else 2, set()).add(d.get("offer_version"))
        if wanted:
            await self.changelogs.refresh(self.session, set(wanted), self.pins, wanted)

    def changelog_for(self, device_id):
        """Changelog entries between the installed and the offered version (None: unknown)."""
        dev = self.devices[device_id]
        if not dev.get("offer_version") or not dev.get("version"):
            return None
        return self.changelogs.between(dev.get("gen"), dev["version"], dev["offer_version"], dev.get("type"))

    async def _scan_all(self):
        """Check all Shelly main devices from the HA device registry.
        Returns the IDs of the checked devices."""
        offers = {}  # (gen, firmware ID) -> shared offer task: one request per type
        entries = self._shelly_entries(await self._registry())
        # Ignored devices count as registered (seen) but are not contacted
        seen = set(entries)
        for device_id in seen & set(self.devices):
            if self.devices[device_id].get("ignored"):
                entry = entries.pop(device_id)
                self.devices[device_id]["name"] = clip(entry.get("name_by_user") or entry.get("name") or device_id, 100)
        # Every device of this check is "waiting" until it is reached
        self.progress.update(total=len(entries), phase="devices")
        self.activity.update(dict.fromkeys(entries, ACTIVITY_WAITING))
        slots = asyncio.Semaphore(CHECK_PARALLEL)

        async def check(device_id, entry):
            """Check one device within the parallel limit and count the result."""
            async with slots:
                ok = await self._check_entry(device_id, entry, offers)
            if not ok:
                self.progress["errors"] += 1
            self.progress["done"] += 1

        # TaskGroup: a cancelled check (app stop) cancels every device query
        async with asyncio.TaskGroup() as group:
            for device_id, entry in entries.items():
                group.create_task(check(device_id, entry))

        for device_id, dev in self.devices.items():
            if device_id not in seen:
                dev.update(status=STATUS_UNREGISTERED, message="")
        return sorted(entries)

    def _shelly_entries(self, registry):
        """Shelly main devices from the registry, ordered by name: {device ID: entry}.

        Sub-devices (channels of e.g. Pro 3EM or Strip) carry via_device_id; only the
        main device has firmware of its own – stale sub-device entries are removed."""
        entries = {}
        for entry in registry:
            is_shelly = any(i and i[0] == "shelly" for i in entry.get("identifiers", []))
            if not is_shelly or entry.get("disabled_by"):
                continue
            if entry.get("via_device_id"):
                self.devices.pop(entry["id"], None)
                self.credentials.remove(entry["id"])
                continue
            entries[entry["id"]] = entry
        order = sorted(entries, key=lambda i: str(entries[i].get("name_by_user") or entries[i].get("name") or i).lower())
        return {i: entries[i] for i in order}

    async def _check_entry(self, device_id, entry, offers):
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
                status=STATUS_ERROR, message=clip(err, 500) or type(err).__name__, error_level=severity_of(err)
            )
            return False
        else:
            dev["last_success"] = dev["last_check"]
            return True
        finally:
            self.activity.pop(device_id, None)

    async def check_device(self, device_id, user=None):
        """Check a single device without starting the full check.

        Same steps as the full check for this one device (device info, offer,
        reuse of stored firmware, download of missing firmware) – never an
        installation, a restart or a settings change. The time of the last full
        check (last_scan) and the schedule stay unchanged. Raises ShellyError if a
        check or an installation is running."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        if not self._reserved("device", device_id):
            self.reserve_check("device", device_id)
        async with self.lock:
            dev = self.devices[device_id]
            started = time.monotonic()
            failure = None
            try:
                self.progress["phase"] = "registry"
                entry = self._shelly_entries(await self._registry()).get(device_id)
                if entry is None:
                    dev.update(status=STATUS_UNREGISTERED, message="")
                    failure = "Device is no longer in the Home Assistant device registry"
                else:
                    self.progress["phase"] = "devices"
                    if not await self._check_entry(device_id, entry, {}):
                        self.progress["errors"] = 1
                    self.progress["done"] = 1
                    if dev.get("status") in (STATUS_READY, STATUS_AVAILABLE):
                        self.progress["phase"] = "changelog"
                        await self._refresh_changelogs_for([dev])
            except Exception as err:  # registry failure etc.: report, do not crash
                log.exception("Device check failed")
                failure = clip(err, 500) or type(err).__name__
            finally:
                self.activity.clear()
                self.progress = None
                self.cleanup_firmware()
                self.save()
            self._log_check(
                [device_id], False, user, time.monotonic() - started, failure, trigger="device"
            )
        return {"status": dev.get("status"), "error": failure}

    async def _scan_device(self, device_id, dev, configuration_url, offers):
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
                message="Gen1 updates are disabled in the settings",
                file=None,
                sha256=None,
            )
            return

        # Query the offer only once per firmware ID and check; parallel devices of
        # the same type await the same request (single flight)
        key = (info["gen"], info["type"])
        if key not in offers:
            offers[key] = asyncio.ensure_future(shelly.fetch_offer(self.session, info, self.gen1_allowed, self.pins))
        offer = await offers[key]
        dev["offer_version"] = offer["version"] if offer else None
        if not offer or shelly.parse_version(offer["version"]) <= shelly.parse_version(info["version"]):
            dev.update(
                status=STATUS_CURRENT,
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
            return

        # One provide task per firmware ID and version: a parallel device of the same
        # type waits for it instead of downloading the file a second time
        fw_key = (info["type"], offer["version"])
        task = self._provisions.get(fw_key)
        if task is None:
            task = self._provisions[fw_key] = asyncio.ensure_future(self._provide(device_id, dev, info, offer))
            task.add_done_callback(lambda _t, k=fw_key: self._provisions.pop(k, None))
        name, sha, size = await task
        dev.update(
            status=STATUS_READY,
            file=name,
            sha256=sha,
            size=size,
            pending_version=offer["version"],
            pending_build=info["build"],
            source_url=clip(offer["url"], 500),
            message="Provided, not installed",
        )

    async def _provide(self, device_id, dev, info, offer):
        """Stored firmware (after checksum verification) or a new download.
        Returns (file name, sha256, size)."""
        stored = await self._stored_firmware(info["type"], offer["version"])
        if stored:
            name, meta = stored
            log.info("%s: using stored firmware %s %s", dev["name"], info["type"], offer["version"])
            return name, meta["sha256"], meta["size"]
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
        self.events.add("download", type=info["type"], version=offer["version"], size=size, sha256=sha, https=https)
        return name, sha, size

    @staticmethod
    def _apply_info(dev, info):
        """Take over the identity read from the device; a changed version (e.g.
        update via the Shelly app) sets last_update."""
        if dev.get("version") and dev["version"] != info["version"]:
            dev["last_update"] = int(time.time())
        dev.update(**info)

    async def _notify_new_updates(self):
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

    def _auth(self, device_id, dev):
        """Stored credentials of a device, else None (v3: no shared password)."""
        return self.credentials.auth_for(device_id, dev.get("gen") or 1)

    def _auth_for(self, device_id):
        """Credentials lookup by generation, for requests that detect the generation."""
        return lambda gen: self.credentials.auth_for(device_id, gen)

    async def _read_settings(self, device_id, dev, extended=False):
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
            dev["settings_error"] = {"message": clip(err, 300) or type(err).__name__, "severity": severity_of(err)}
            return False
        dev.pop("settings_error", None)
        return True

    def _settings_device(self, device_id):
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

    async def refresh_settings(self, device_id):
        """Read the current settings directly from the Shelly (before editing)."""
        dev = self._settings_device(device_id)
        settings = await shelly.read_settings(
            self.session, dev["gen"], dev["origin"], dev["type"], self._auth(device_id, dev)
        )
        dev.update(settings)
        dev.pop("settings_error", None)
        self.save()
        return settings

    def set_ignored(self, device_id, ignored):
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

    async def reload_device(self, device_id):
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
            raise ShellyError(f"Device not reachable: {clip(err, 200) or type(err).__name__}") from None
        finally:
            self.reloading.discard(device_id)
            self.activity.pop(device_id, None)
            self.cleanup_firmware()
            self.save()
        return {"warning": None if ok else f"Device status not readable: {dev['settings_error']['message']}"}

    async def change_settings(self, device_id, changes, user="unknown"):
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
            error = clip(err, 300) or type(err).__name__
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
    def _settings_diff(dev, clean):
        """Old -> new values of a settings change, readable for the log."""
        onoff = {True: "on", False: "off"}
        diff = []
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

    async def restart_device(self, device_id):
        """Restart the Shelly (same restrictions as changing settings). Not logged."""
        dev = self._settings_device(device_id)
        await shelly.restart(self.session, dev["gen"], dev["origin"], self._auth(device_id, dev))
        log.info("%s: restart requested", dev.get("name"))

    async def set_device_password(self, device_id, mode, password=None, confirm=False, acknowledged=False, user=None):
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
            error = clip(err, 300) or type(err).__name__
            if not isinstance(err, ValueError):  # input errors never reached the device
                self.events.add("password", ok=False, device=name, action=mode, user=user, error=error)
            raise
        finally:
            self.save()
        action = {"store": "stored", "enable": "enabled", "change": "changed", "remove": "removed"}[mode]
        log.info("%s: password %s by %s", name, action, user)
        self.events.add("password", device=name, action=action, user=user)
        return result

    async def _password_action(self, device_id, dev, mode, password, gen, origin, realm):
        """Device part of set_device_password (see there); raises on any failure."""
        protected = await shelly.protection_status(self.session, origin)
        dev["auth_enabled"] = protected
        current = self._auth(device_id, dev)
        if mode in ("store", "enable", "change"):
            candidate = self.credentials.candidate(gen, realm, password)
        if mode == "store":
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
            answer = None
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
        if protected is True and await self._auth_works(dev, new):
            self.credentials.store(device_id, new)  # replaces the old credential only now
            return {"protected": True}
        if mode == "change" and await self._auth_works(dev, current):
            raise ShellyError(self._unverified("The password was not changed – the previous password still works", answer))
        raise ShellyError(
            self._unverified("The new password could not be confirmed – the previously stored password was kept", answer)
        )

    async def _auth_works(self, dev, auth):
        """True if an authenticated read succeeds with auth. Only meaningful while
        the device is protected – an unprotected device accepts anything."""
        try:
            await shelly.read_settings(self.session, dev["gen"], dev["origin"], dev.get("type"), auth)
        except (TimeoutError, ShellyError, aiohttp.ClientError):  # AuthError included
            return False
        return True

    @staticmethod
    def _unverified(text, answer):
        """Message for an unconfirmed password change, with the device's answer."""
        return f"{text} ({clip(answer, 120)})" if answer else text

    # ------------------------------------------------------------ Installation

    def approval_matches(self, device_id, sha):
        """An approval is only valid while exactly this file (SHA-256) is provided."""
        dev = self.devices.get(device_id) or {}
        if dev.get("gen") == 1 and not self.gen1_enabled:
            return False  # Gen1 updates disabled in the settings
        return dev.get("status") == STATUS_READY and hmac.compare_digest(str(dev.get("sha256")), sha)

    async def install(self, device_id, sha, approved_by):
        """Install an approved update. True only if the target version is confirmed.

        Several installations run in parallel, at most max_parallel_installs at a
        time; others wait for a free slot. Nothing starts during a check, and each
        device is in progress at most once. approved_by is only written to the log."""
        if (
            self.checking
            or device_id in self.installing
            or device_id in self.reloading
            or not self.approval_matches(device_id, sha)
        ):
            return False
        dev = self.devices[device_id]
        self.installing.add(device_id)  # no await before this: no double starts possible
        dev["message"] = "Approved, waiting for a free installation slot"
        log.info("Installation %s → %s approved by %s", dev["name"], dev["pending_version"], approved_by)
        entry = None  # log context, set once the installation actually starts
        try:
            async with self.install_slots:
                dev["message"] = "Installation started"
                entry = {
                    "device": dev.get("name"),
                    "type": dev.get("type"),
                    "old": dev.get("version"),
                    "new": dev.get("pending_version"),
                    "user": approved_by,
                }
                self.events.add("install", event="started", **entry)
                ok, diagnosis = await self._install(device_id, dev, sha)
                event = "confirmed" if ok else "unconfirmed"
                self.events.add("install", ok=ok, event=event, detail=diagnosis, **entry)
                return ok
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            dev.update(status=STATUS_ERROR, message=clip(err, 500) or type(err).__name__)
            if entry:
                self.events.add("install", ok=False, event="failed", detail=dev["message"], **entry)
            return False
        finally:
            self.installing.discard(device_id)
            self.cleanup_firmware()  # keeps files of installations still running
            self.save()

    async def _install(self, device_id, dev, sha):
        """Actual installation: pre-check, update command, wait for the target version.
        Returns (confirmed, diagnosis or None)."""
        # 1. The approval is valid only for exactly this file and this device state
        if await asyncio.to_thread(shelly.file_sha256, FW_DIR, dev["file"]) != sha:
            raise ShellyError("Firmware file was modified")
        auth = self._auth(device_id, dev)
        current = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
        if current["mac"] != dev.get("mac") or current["build"] != dev.get("pending_build"):
            raise ShellyError("Device or firmware changed since the check – please check again")

        # 2. Trigger the update; the Shelly downloads from the app's download server
        self.downloads.pop(dev["file"], None)
        await shelly.trigger_update(
            self.session, dev["gen"], dev["origin"], f"{self.download_base}/{dev['file']}", auth
        )
        dev.update(status=STATUS_INSTALLING, message="Update accepted, waiting for restart")
        self.save()

        # 3. Report success only when the device reports exactly the target version
        # (parsed version number; Gen1 the vX.Y.Z part): wait 30 s, then check every
        # 10 s until the Shelly is reachable again. A different version (also a newer
        # one) is never reported as success. If it has not even requested the file
        # after 3 min, stop waiting – the update will never arrive.
        target = shelly.parse_version(dev["pending_version"])
        old = shelly.parse_version(dev["version"]) if dev.get("version") else None
        unexpected = None  # last reported version that is neither old nor target
        started = time.monotonic()
        await asyncio.sleep(INSTALL_FIRST_CHECK_S)
        while (elapsed := time.monotonic() - started) < INSTALL_WAIT_S:
            downloaded = bool(self.downloads.get(dev["file"], {}).get("served"))
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
                    await self.notify(f"Shelly updated: {dev['name']}", info["version"], f"sfm_{device_id}")
                    return True, None
                # Neither old nor target: keep waiting (polling is unchanged), but the
                # result can only be "unconfirmed" with this version as hint
                unexpected = info["version"] if reported != old else None
                if not unexpected and not downloaded and elapsed >= INSTALL_NO_DOWNLOAD_S:
                    break  # reachable, old version, nothing downloaded
            except (TimeoutError, aiohttp.ClientError, ShellyError) as err:
                log.debug("Waiting for %s: %s", dev["name"], err)  # restart – expected
            dev["message"] = (
                "Firmware transferred, waiting for restart"
                if downloaded
                else "Waiting for the Shelly to download the firmware"
            )
            await asyncio.sleep(INSTALL_POLL_S)

        diagnosis = (
            f"The Shelly reports {unexpected} instead of the target version {dev['pending_version']}"
            if unexpected
            else self._install_diagnosis(dev)
        )
        log.warning("Installation %s not confirmed: %s", dev["name"], diagnosis)
        dev.update(status=STATUS_UNCLEAR, message=f"Target version not confirmed. {diagnosis}")
        return False, diagnosis

    # ------------------------------------------------------------ Download server

    def may_download(self, name, client_ip):
        """May client_ip download the file name? Only provided files, optionally only to Shellys.
        Attempts on provided files are recorded for the installation diagnosis."""
        owners = [d for d in self.devices.values() if d.get("status") in PENDING and d.get("file") == name]
        if not owners:
            return False  # unknown file: record nothing
        allowed = not self.restrict_downloads or any(
            shelly.origin_ip(d.get("origin", "")) == client_ip for d in owners
        )
        record = self.downloads.setdefault(name, {"served": 0, "rejected": set()})
        if allowed:
            record["served"] += 1
        elif len(record["rejected"]) < 8:  # limited in case someone floods requests
            record["rejected"].add(client_ip)
        return allowed

    def _install_diagnosis(self, dev):
        """Explains why the target version was not reached."""
        record = self.downloads.get(dev.get("file"), {})
        if record.get("served"):
            return (
                "The Shelly downloaded the firmware but still reports "
                f"{dev.get('version')} – check the device log or the Shelly's web interface"
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

    def public_state(self):
        """Only fields the UI needs (no internal addresses/builds)."""
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
            "info_refreshed",
            "settings_error",
            "error_level",
        )
        devices = []
        for key, dev in self.devices.items():
            origin = dev.get("origin")  # checked by resolve_device_origin: private IP
            devices.append(
                {
                    "id": key,
                    **{f: dev.get(f) for f in fields},
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
            "ui": self.ui,
        }
