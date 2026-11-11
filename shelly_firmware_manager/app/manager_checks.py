"""Manager, layer 4 of 6: checks – which firmware is installed, which one is provided.

Within a check up to CHECK_PARALLEL devices are queried at the same time; offers and
firmware downloads are shared (single flight), so parallelism never duplicates a
request or a file. Ignored devices are left out of the full check (an explicit
single-device check is still possible).
The firmware store comes first: a check provides the next higher stored version of the
device's firmware ID (downloads and uploads); the manufacturer's server is only asked
for devices without such a file, once per firmware ID (Gen1: once for all types). A
check never installs anything, and no check follows an installation.
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
    ACTIVITY_CHECKING, ACTIVITY_DOWNLOADING, ACTIVITY_WAITING, Device, PENDING, SOURCE_UPLOAD, STATUS_AVAILABLE,
    STATUS_CURRENT, STATUS_ERROR, STATUS_READY, STATUS_SKIPPED, STATUS_UNREGISTERED, severity_of,
)
from manager_devices import DeviceSettings

log = logging.getLogger("sfm.manager")

# Shared manufacturer requests of one check (single flight): (gen, firmware ID) -> offer
# of a Gen2+ firmware ID, GEN1_OFFERS -> firmware list of all Gen1 types
OfferTasks = dict[tuple[int, str], "asyncio.Future[Any]"]

GEN1_OFFERS = (1, "")

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
        """Check all Shellys. Returns False if a check or installation is running.
        Automatic checks report newly found updates as a notification in HA and
        record their scheduled date when they start (one attempt per date).
        Every check is written to the activity log with the user, "Schedule" for
        automatic checks; its firmware downloads carry the same user."""
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
            self._check_user = SCHEDULE_USER if automatic else user or "unknown"
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
            self._check_user = user or "unknown"
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

    # ------------------------------------- One device: firmware store first, then the manufacturer's offer

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
            and (data_paths.FW_DIR / str(dev.get("file"))).is_file()
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
                self.session, offer["url"], data_paths.FW_DIR, self.pins, https_only=info["gen"] >= 2
            )
        finally:
            self._downloads_running -= 1
            if self.progress and not self._downloads_running:
                self.progress["phase"] = "devices"
            if self.activity.get(device_id) == ACTIVITY_DOWNLOADING:
                self.activity[device_id] = ACTIVITY_CHECKING
        self._register_firmware(name, info["type"], info["gen"], offer["version"], sha, size, https)
        self._fresh[name] = clip(offer["url"], 500)  # later devices of this check: same offer
        self.events.add(
            "download", type=info["type"], version=offer["version"], size=size, sha256=sha, https=https,
            user=self._check_user,
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
    ) -> None:
        """One log entry per check; findings = devices with an update, a warning or
        an error. trigger overrides the derived value ("device" for single-device
        checks). Automatic checks name SCHEDULE_USER as user.
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
        meta = self.firmware.get(str(dev.get("file"))) if dev.get("status") in PENDING else None
        return self.changelogs.between(
            dev.get("gen"), dev["version"], dev["offer_version"], dev.get("type"), (meta or {}).get("changelog")
        )

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
