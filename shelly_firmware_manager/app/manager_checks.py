"""Manager, layer 4 of 6: checks – which firmware is installed, which one is provided.

Within a check up to CHECK_PARALLEL devices are queried at the same time; offers and
firmware downloads are shared (single flight), so parallelism never duplicates a
request or a file. Ignored devices are left out of the full check (an explicit
single-device check is still possible).
Gen2+: the manufacturer's server decides. It is asked per firmware ID and installed
version the way a Shelly asks itself (header Shelly-Info, see shelly.fetch_offer), so
it answers with the next allowed step – a required intermediate version first. A stored
file of exactly the offered version is used instead of a download (downloads and
uploads). If the server cannot be asked, a newer stored version is only offered for a
manual installation at the user's risk (marker store_fallback). After a confirmed
intermediate version the device is checked again at once (see manager_installs).
Gen1: the firmware store comes first – the next higher stored version is provided; the
manufacturer's list is only asked for devices without such a file, once per check.
A check never installs anything.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Iterable
from typing import Any

import aiohttp
import data_paths
import schedule
import shelly
from events import worst
from shelly import ShellyError, clip, error_text

from manager_core import (
    ACTIVITY_CHECKING,
    ACTIVITY_DOWNLOADING,
    ACTIVITY_WAITING,
    Device,
    SOURCE_UPLOAD,
    STATUS_ASLEEP,
    STATUS_AVAILABLE,
    STATUS_CURRENT,
    STATUS_ERROR,
    STATUS_READY,
    STATUS_SKIPPED,
    STATUS_UNREGISTERED,
    holds_file,
    severity_of,
)
from manager_devices import DeviceSettings

log = logging.getLogger("sfm.manager")

# Status message of a battery device that was not reachable (STATUS_ASLEEP); {error}
# is the error of the attempt
ASLEEP_MESSAGE = "Battery device asleep – wake it up (e.g. press its button) and check it again. Last attempt: {error}"


def _unreachable(err: BaseException) -> bool:
    """True if the device did not answer at all (timeout, connection failure) – not
    for an answer the device gave, e.g. a refused password."""
    return isinstance(err, (TimeoutError, aiohttp.ClientConnectionError, OSError))


# Shared manufacturer requests of one check (single flight): ("step", firmware ID,
# installed version) -> next step for this version, ("newest", firmware ID) -> newest
# version, GEN1_OFFERS -> firmware list of all Gen1 types
OfferTasks = dict[tuple[Any, ...], "asyncio.Future[Any]"]

GEN1_OFFERS: tuple[Any, ...] = ("gen1",)

# Reference request: does the server still answer with the next step for an installed
# version? Verified on 2026-11-13: a Plus1 with 1.0.7 is offered the
# required intermediate version 1.3.3 (without the version: the newest). Only these
# fixed values are sent – no data of the user's devices.
HEADER_CHECK = ("Plus1", "1.0.7", "1.3.3")

HEADER_WARNING = (
    "The Shelly update server no longer answers with the next step for an installed "
    "version (reference: Plus1 1.0.7 should be offered 1.3.3, got {got}). Required "
    "intermediate versions cannot be detected – an offered update may not be installable."
)

# Error detail of a device whose server request failed while a newer version is stored
STORE_FALLBACK_HINT = (
    "Stored version {version} is ready and can be installed manually – at your own risk: "
    "without the answer of the Shelly update server it cannot be verified whether an "
    "intermediate version is required first."
)

# Where the update provided for a device comes from (device field offer_source)
OFFER_MANUFACTURER = "manufacturer"  # offered by the manufacturer's server in this check

OFFER_STORE = "store"  # stored download of an earlier check, server not asked

OFFER_UPLOAD = "upload"  # manually uploaded file from the store, server not asked

OFFER_MESSAGES = {
    OFFER_MANUFACTURER: "Provided, not installed",
    OFFER_STORE: "Provided from the firmware store, not installed",
    OFFER_UPLOAD: "Provided from the firmware store (uploaded file), not installed",
}

# User of scheduled checks and their downloads in the activity log
SCHEDULE_USER = "Schedule"

# Parallel device queries of one full check. Each Shelly is a small embedded HTTP
# server that is only asked by one request at a time; 4 devices in parallel cut
# the check time of larger installations by roughly a factor of 4 while keeping
# Wi-Fi and Home Assistant load low. Internal constant, not a setting.
CHECK_PARALLEL = 4


class Checks(DeviceSettings):
    """Full check and single-device check."""

    # ------------------------------------------ Full check, single-device check

    def scan_due_in(self, now: float) -> float:
        """Seconds until the next automatic check; 0 = due now (see schedule.py).

        Only the schedule counts: neither an app update, a manual check nor a
        Reload Device Info shifts it."""
        return schedule.seconds_until(self.schedule, now, self.tz)

    async def scan(self, automatic: bool = False, user: str | None = None) -> bool:
        """Check all Shellys. Returns False if it could not start (another check,
        single-device checks, installations or Reload Device Info running).
        Automatic checks record their scheduled date when they are due (one attempt
        per date); one that cannot start is cancelled and logged (no retry) and
        report newly found updates as a notification in HA.
        Every check is written to the activity log with the user, "Schedule" for
        automatic checks; its firmware downloads carry the same user."""
        if automatic:
            due, _ = schedule.status(self.schedule, time.time(), self.tz)
            if due:
                self.schedule = {**self.schedule, "last_date": due.isoformat()}
        if not self._reserved("all"):
            try:
                self.reserve_check("all")
            except ShellyError as err:
                if automatic:
                    self._skip_scheduled(str(err))
                return False
        async with self.lock:
            started = time.monotonic()
            failure: str | None = None
            checked: list[str] = []
            header_ignored: bool | None = None
            self._scan_user = SCHEDULE_USER if automatic else user or "unknown"
            self._progress()["phase"] = "registry"
            try:
                checked = await self._scan_all()
                header_ignored = await self._finish_header_check()
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
            self._log_check(
                checked, automatic, user, time.monotonic() - started, failure, header_ignored=header_ignored
            )
        if automatic:
            await self._notify_new_updates()
        return True

    def _skip_scheduled(self, reason: str) -> None:
        """A scheduled check that cannot start (other work running) is cancelled –
        not repeated – and written to the activity log and the app log."""
        log.warning("Scheduled check cancelled: %s", reason)
        self.events.add(
            "check",
            severity="warning",
            trigger="scheduled",
            devices=0,
            skipped=reason,
            result=f"Cancelled: {reason}",
            user=SCHEDULE_USER,
        )
        self.save()
        self.schedule_changed.set()

    async def check_device(self, device_id: str, user: str | None = None, trigger: str = "device") -> dict[str, Any]:
        """Check a single device without starting the full check.

        Same steps as the full check for this one device (device info, firmware
        store first, else offer and download of missing firmware) – never an
        installation, a restart or a settings change. The time of the last full
        check (last_scan) and the schedule stay unchanged. Raises ShellyError during
        the full check or while this device is checked, installed or reloaded;
        other devices may be checked or installed at the same time. trigger: log
        value ("device", or "step" for the check after an intermediate version)."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        if not self._reserved("device", device_id):
            self.reserve_check("device", device_id)
        self.device_checks[device_id] = "running"
        dev = self.devices[device_id]
        dev.pop("updated", None)  # Checked again by the user, no longer "just updated"
        dev.pop("install_failed", None)  # nor a failed installation to retry
        self._check_users[device_id] = user or "unknown"
        started = time.monotonic()
        failure = None
        header_ignored: bool | None = None
        try:
            entry = self._shelly_entries(await self._registry()).get(device_id)
            if entry is None:
                dev.update(status=STATUS_UNREGISTERED, message="")
                failure = "Device is no longer in the Home Assistant device registry"
            else:
                await self._check_entry(device_id, entry, {})
                header_ignored = await self._finish_header_check()
                if dev.get("status") == STATUS_READY:
                    await self._store_changelogs([dev])
        except Exception as err:  # registry failure etc.: report, do not crash
            log.exception("Device check failed")
            failure = error_text(err, 500) or type(err).__name__
        finally:
            self._end_device_check(device_id)
            self.cleanup_firmware()
            self.save()
        self._log_check(
            [device_id],
            False,
            user,
            time.monotonic() - started,
            failure,
            trigger=trigger,
            header_ignored=header_ignored,
        )
        return {"status": dev.get("status"), "error": failure}

    async def _scan_all(self) -> list[str]:
        """Check all Shelly main devices from the HA device registry.
        Returns the IDs of the checked devices."""
        offers: OfferTasks = {}  # shared manufacturer requests: one per firmware ID
        entries = self._shelly_entries(await self._registry())
        for dev in self.devices.values():  # A full check ends the "just updated" display
            dev.pop("updated", None)  # and the retry of a failed installation
            dev.pop("install_failed", None)
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
        attempt keeps the last known firmware so it is never shown as freshly checked.
        A known battery device (marker battery) that did not answer is STATUS_ASLEEP
        instead of STATUS_ERROR."""
        dev = self.devices.setdefault(device_id, {})
        dev["name"] = clip(entry.get("name_by_user") or entry.get("name") or device_id, 100)
        dev["last_check"] = int(time.time())
        self.activity[device_id] = ACTIVITY_CHECKING
        try:
            await self._scan_device(device_id, dev, entry.get("configuration_url"), offers)
        except (TimeoutError, ShellyError, aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            log.warning("%s: %s", dev["name"], err)
            text = error_text(err, 500) or type(err).__name__
            if dev.get("battery") is True and _unreachable(err):
                dev.update(status=STATUS_ASLEEP, message=ASLEEP_MESSAGE.format(error=text), error_level="warning")
            else:
                dev.update(status=STATUS_ERROR, message=text, error_level=severity_of(err))
            return False
        else:
            dev["last_success"] = dev["last_check"]
            return True
        finally:
            self.activity.pop(device_id, None)

    # ------------------------------------- One device: Gen2+ next step of the server, Gen1 firmware store first

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

        dev.pop("store_fallback", None)  # set again below if it still applies
        dev.pop("step_target", None)
        if info["gen"] == 1:
            # Gen1: the firmware store comes first. The next higher stored version is
            # provided – also instead of an update provided before – and the
            # manufacturer's list is not asked for this device.
            stored = await self._store_offer(info)
            if stored:
                self._provide_stored(dev, info, *stored)
                return
            offer = await self._manufacturer_offer(info, offers)
        else:
            offer = await self._step_offer(dev, info, offers)
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
            and (data_paths.FW_DIR / str(dev.get("file"))).is_file()
        )
        if not already:
            # One provide task per firmware ID and version, shared by the full check and
            # all single-device checks: a parallel device of the same type waits for it
            # instead of downloading the file a second time
            fw_key = (info["type"], offer["version"])
            task = self._provisions.get(fw_key)
            if task is None:
                provide = self._provide(fw_key, device_id, dev, info, offer)
                task = self._provisions[fw_key] = asyncio.ensure_future(provide)
                task.add_done_callback(functools.partial(self._provision_done, fw_key))
            waiters = self._provision_waiters.setdefault(fw_key, set())
            waiters.add(device_id)
            if fw_key in self._fetching:  # joined a running download
                self.activity[device_id] = ACTIVITY_DOWNLOADING
            try:
                name, sha, size = await task
            finally:
                waiters.discard(device_id)
                if self.activity.get(device_id) == ACTIVITY_DOWNLOADING:
                    self.activity[device_id] = ACTIVITY_CHECKING
            dev.update(
                file=name,
                sha256=sha,
                size=size,
                pending_version=offer["version"],
                pending_build=info["build"],
                source_url=clip(offer["url"], 500),
            )
        source = self._offer_source(str(dev.get("file")))
        message = OFFER_MESSAGES[source]
        if dev.get("step_target") is not None:
            # Permanent marker of the stored file: provided as an intermediate version
            # once, it stays labeled in the Firmware tab (never set retroactively)
            meta = self.firmware.get(str(dev.get("file")))
            if meta is not None:
                meta["intermediate"] = True
            towards = f" on the way to {dev['step_target']}" if dev["step_target"] else ""
            message += f" – intermediate version{towards}; the device is checked again after its installation"
        dev.update(status=STATUS_READY, offer_source=source, message=message)

    def _offer_source(self, name: str) -> str:
        """Source of the file provided for the manufacturer's offer: downloaded by this
        check (manufacturer), or a stored download or upload of exactly that version."""
        if name in self._fresh:
            return OFFER_MANUFACTURER
        meta = self.firmware.get(name)
        return OFFER_UPLOAD if meta and self._source(meta) == SOURCE_UPLOAD else OFFER_STORE

    def _provide_stored(self, dev: Device, info: dict[str, Any], name: str, meta: dict[str, Any]) -> None:
        """Gen1: provide the next higher stored version (firmware store first)."""
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

    async def _step_offer(self, dev: Device, info: dict[str, Any], offers: OfferTasks) -> dict[str, str] | None:
        """Gen2+: the manufacturer's next step for the installed version (one request
        per firmware ID and version and check, shared by parallel devices).

        An offer below the newest version (or with several steps) is an intermediate
        version: dev["step_target"] = newest version ("" if unknown). If the server
        cannot be asked, a newer stored version is offered for a manual installation
        (see _store_fallback) and the error is raised with a hint."""
        key = ("step", str(info["type"]), str(info["version"]))
        if key not in offers:
            offers[key] = asyncio.ensure_future(
                shelly.fetch_offer(self.session, info["type"], self.pins, str(info["version"]))
            )
            self._start_header_check()
        try:
            offer: dict[str, str] | None = await offers[key]
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            await self._store_fallback(dev, info, err)
            raise
        if offer and shelly.parse_version(offer["version"]) > shelly.parse_version(info["version"]):
            newest = await self._newest_version(info, offers)
            later = newest is not None and shelly.parse_version(newest) > shelly.parse_version(offer["version"])
            if later or int(offer.get("steps") or 1) > 1:
                dev["step_target"] = newest if later else ""
        return offer

    async def _newest_version(self, info: dict[str, Any], offers: OfferTasks) -> str | None:
        """Newest version of a Gen2+ firmware ID (request without installed version,
        once per check and firmware ID); None if the server could not be asked."""
        key = ("newest", str(info["type"]))
        if key not in offers:
            offers[key] = asyncio.ensure_future(shelly.fetch_offer(self.session, info["type"], self.pins))
        try:
            newest: dict[str, str] | None = await offers[key]
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.warning("Newest version of %s not available: %s", info["type"], err)
            return None
        return newest["version"] if newest else None

    async def _store_fallback(self, dev: Device, info: dict[str, Any], err: BaseException) -> None:
        """The manufacturer's server could not be asked for a Gen2+ device: a newer
        stored version is offered for a manual installation (marker store_fallback,
        the device stays in the error state) and the error is raised with a hint."""
        stored = await self._store_offer(info)
        if not stored:
            return
        name, meta = stored
        dev.update(
            offer_version=meta["version"],
            offer_source=OFFER_UPLOAD if self._source(meta) == SOURCE_UPLOAD else OFFER_STORE,
            file=name,
            sha256=meta["sha256"],
            size=meta["size"],
            pending_version=meta["version"],
            pending_build=info["build"],
            source_url=None,
            store_fallback=True,
        )
        hint = STORE_FALLBACK_HINT.format(version=meta["version"])
        raise ShellyError(f"{error_text(err, 300) or type(err).__name__}. {hint}") from err

    def _start_header_check(self) -> None:
        """Start the reference request of this check once (see HEADER_CHECK)."""
        if self._header_check is None:
            self._header_check = asyncio.ensure_future(self._header_check_request())

    async def _header_check_request(self) -> bool | None:
        """True if the server answers the reference request with the expected step,
        False if not, None if it could not be asked (no conclusion)."""
        app, version, expected = HEADER_CHECK
        try:
            offer = await shelly.fetch_offer(self.session, app, self.pins, version)
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.info("Reference request of the update server failed: %s", err)
            return None
        got = offer["version"] if offer else "nothing"
        if got != expected:
            log.warning(HEADER_WARNING.format(got=got))
            self._header_got = got
            return False
        return True

    async def _finish_header_check(self) -> bool | None:
        """Result of the reference request of the running checks: True if the server
        ignored the installed version, False if not, None if no request was made or
        it gave no answer."""
        task = self._header_check
        if task is None:
            return None
        result = await asyncio.shield(task)  # shared by parallel single-device checks
        return None if result is None else not result

    async def _manufacturer_offer(self, info: dict[str, Any], offers: OfferTasks) -> dict[str, str] | None:
        """Gen1 offer: the manufacturer serves one list for all types, so it is fetched
        once per check for all of them (single flight)."""
        if GEN1_OFFERS not in offers:
            offers[GEN1_OFFERS] = asyncio.ensure_future(shelly.fetch_gen1_offers(self.session, self.pins))
        return shelly.gen1_offer(await offers[GEN1_OFFERS], info["type"])

    def _provision_done(self, fw_key: tuple[str, str], _task: "asyncio.Future[Any]") -> None:
        """Forget a finished provide task (single flight per firmware ID and version)."""
        self._provisions.pop(fw_key, None)
        self._provision_waiters.pop(fw_key, None)

    async def _provide(
        self, fw_key: tuple[str, str], device_id: str, dev: Device, info: dict[str, Any], offer: dict[str, str]
    ) -> tuple[str, str, int]:
        """The firmware offered by the manufacturer's server: an already downloaded
        file of exactly this version (after checksum verification) or a new download.
        Runs once per fw_key (firmware ID, version); every device waiting for it shows
        "downloading" during the download. device_id, dev: the device whose check started
        it (log text and the user of the download entry).
        Returns (file name, sha256, size)."""
        stored = await self._stored_firmware(info["type"], offer["version"], uploads=info["gen"] >= 2)
        if stored:
            name, meta = stored
            log.info("%s: using stored firmware %s %s", dev["name"], info["type"], offer["version"])
            return name, str(meta["sha256"]), int(meta["size"])
        self._fetching.add(fw_key)
        for waiting in self._provision_waiters.get(fw_key, ()):
            self.activity[waiting] = ACTIVITY_DOWNLOADING
        self._downloads_running += 1
        if self.progress:
            self.progress["phase"] = "download"
        try:
            name, sha, size, https = await shelly.download_firmware(
                self.session, offer["url"], data_paths.FW_DIR, self.pins, https_only=info["gen"] >= 2
            )
        finally:
            self._fetching.discard(fw_key)
            self._downloads_running -= 1
            if self.progress and not self._downloads_running:
                self.progress["phase"] = "devices"
            for waiting in self._provision_waiters.get(fw_key, ()):
                if self.activity.get(waiting) == ACTIVITY_DOWNLOADING:
                    self.activity[waiting] = ACTIVITY_CHECKING
        self._register_firmware(name, info["type"], info["gen"], offer["version"], sha, size, https)
        self._fresh[name] = clip(offer["url"], 500)  # later devices of this check: same offer
        self.events.add(
            "download",
            type=info["type"],
            version=offer["version"],
            size=size,
            sha256=sha,
            https=https,
            user=self._check_users.get(device_id, self._scan_user),
        )
        return name, sha, size

    # -------------------------------------------------------------- Results: log, changelogs, notification

    def _log_check(
        self,
        checked: list[str],
        automatic: bool,
        user: str | None,
        duration: float,
        failure: str | None,
        trigger: str | None = None,
        header_ignored: bool | None = None,
    ) -> None:
        """One log entry per check; findings = devices with an update, a warning or
        an error. trigger overrides the derived value ("device" for single-device
        checks, "step" after an intermediate version). Automatic checks name
        SCHEDULE_USER as user. A server that ignored the installed version (reference
        request, see HEADER_CHECK) is a warning finding.
        An update finding names its source (OFFER_*: manufacturer, store or upload).
        Every finding carries a severity; "error" holds the message of warnings
        and errors (key kept for historic entries). The entry gets the highest
        severity of its findings, an overall failure is an error."""
        findings: list[dict[str, Any]] = []
        for device_id in checked:
            dev = self.devices.get(device_id, {})
            if dev.get("status") in (STATUS_ERROR, STATUS_ASLEEP):
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
        if header_ignored:
            findings.append(
                {
                    "device": "Shelly update server",
                    "error": HEADER_WARNING.format(got=self._header_got),
                    "severity": "warning",
                }
            )
        counts = {level: sum(1 for f in findings if f["severity"] == level) for level in ("warning", "error")}
        updates = sum(1 for f in findings if "offer" in f)
        self.events.add(
            "check",
            severity=worst("error" if failure else "success", *(f["severity"] for f in findings)),
            trigger=trigger or ("scheduled" if automatic else "manual"),
            user=SCHEDULE_USER if automatic else user or "unknown",
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
        meta = self.firmware.get(str(dev.get("file"))) if holds_file(dev) else None
        return self.changelogs.between(
            dev.get("gen"), dev["version"], dev["offer_version"], dev.get("type"), (meta or {}).get("changelog")
        )

    async def _notify_new_updates(self) -> None:
        """One HA notification if an automatic check found versions not reported before.
        Lists all open updates so that a replaced notification loses nothing."""
        open_updates = [d for d in self.devices.values() if d.get("status") in (STATUS_READY, STATUS_AVAILABLE)]
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
