"""Manager, layer 6 of 9: checks – which firmware is installed, which one is provided.

Within a check up to CHECK_PARALLEL devices are queried at the same time; offers and
firmware downloads are shared (single flight), so parallelism never duplicates a
request or a file. Ignored devices are left out of the full check (an explicit
single-device check is still possible).
Gen2+: the manufacturer's server decides. It is asked per firmware ID and installed
version the way a Shelly asks itself (header Shelly-Info, see shelly_cloud.fetch_offer), so
it answers with the next allowed step – a required intermediate version first. A stored
file of exactly the offered version is used instead of a download only if the offer URL
names its SHA-256 (or it is the user's own upload). If the server cannot be asked, a
newer stored version is only offered for a manual installation at the user's risk
(marker store_fallback). After a confirmed intermediate version the device is checked
again at once (see manager_installs). With the option beta_firmware the beta version of
the same answer is offered instead of the stable one when it is higher – never before a
required intermediate version, never held back by offer_after_days (marker beta).
Gen1: the firmware store comes first – the next higher stored download or upload is
provided; the manufacturer's list is only asked for devices without such a file, once
per check.
Imported files are never provided this way: only as store fallback while the list
cannot be asked (see manager_store._store_offer).
A check never installs anything.
A device that is no longer in the device registry stays listed with the marker removed
(see _mark_removed): the full check only tests whether it still answers at its last
address and reports that in the log; a single-device check checks it there. Shellys of
other integrations are listed read-only (other_shellys).
"""

import asyncio
import contextlib
import datetime
import functools
import logging
import time
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import data_paths
import schedule
import shelly
import shelly_cloud
import shelly_net
from events import worst
from manager_backups import Backups
from manager_core import (
    ACTIVITY_CHECKING,
    ACTIVITY_DOWNLOADING,
    ACTIVITY_SERVER,
    ACTIVITY_WAITING,
    BATTERY_FROM_DEVICE,
    BATTERY_FROM_HA,
    SOURCE_DOWNLOAD,
    SOURCE_IMPORT,
    SOURCE_UPLOAD,
    STATUS_ASLEEP,
    STATUS_AVAILABLE,
    STATUS_CURRENT,
    STATUS_ERROR,
    STATUS_READY,
    STATUS_SKIPPED,
    Device,
    holds_file,
    markdown_text,
    severity_of,
    unexpected_text,
    unreachable,
)
from shelly import AuthError, HttpsRequiredError, ShellyError, clip, error_text, plain
from shelly_net import AddressError

log = logging.getLogger("sfm.manager")

# Status message of a battery device that was not reachable (STATUS_ASLEEP); {error}
# is the error of the attempt
ASLEEP_MESSAGE = "Battery device asleep – wake it up (e.g. press its button) and check it again. Last attempt: {error}"


# Log findings of a device that was removed from Home Assistant (full and single checks)
REMOVED_REACHABLE = "Removed from Home Assistant / the Shelly integration, but still reachable at its last address"

REMOVED_UNREACHABLE = "Removed from Home Assistant / the Shelly integration and no longer reachable"

# Kinds of other Shellys (read-only list, see other_shellys)
OTHER_BLU_GATEWAY = "blu_gateway"  # BLU device (e.g. BLU TRV) behind a Shelly gateway, Shelly integration

OTHER_BLUETOOTH = "bluetooth"  # BLU device of the BTHome / Bluetooth integration

OTHER_ZHA = "zha"  # Zigbee device of ZHA

OTHER_ZIGBEE2MQTT = "zigbee2mqtt"  # Zigbee device of Zigbee2MQTT (MQTT integration)

OTHER_MQTT = "mqtt"  # other device of the MQTT integration

OTHER_MATTER = "matter"  # Matter device

OTHER_MAX = 500  # entries of the list kept at most

AREA_NAME_MAX = 64  # characters of an area name taken over from Home Assistant

# Why a Shelly that Home Assistant knows is not listed (see _update_listing)
MISSING_DISABLED = "disabled"  # device (or its config entry) disabled in Home Assistant

MISSING_DISCOVERED = "discovered"  # discovered by Home Assistant, not set up yet

MISSING_IGNORED = "ignored"  # discovery ignored in Home Assistant (config entry with source "ignore")

# Flow sources of a discovery (a flow started by the user or a reauthentication is none)
DISCOVERY_SOURCES = frozenset({"zeroconf", "dhcp", "bluetooth", "discovery", "integration_discovery", "ssdp"})

# Manufacturer texts that mark a Shelly in another integration (lower case, heuristic)
SHELLY_MAKERS = ("shelly", "allterco")


def _is_shelly(entry: dict[str, Any]) -> bool:
    """True for a registry entry of the Shelly integration."""
    return any(isinstance(i, list | tuple) and i and i[0] == "shelly" for i in entry.get("identifiers") or [])


def _domains(entry: dict[str, Any], key: str) -> set[str]:
    """First parts of the identifiers or connections of a registry entry."""
    return {str(i[0]) for i in entry.get(key) or [] if isinstance(i, list | tuple) and i}


def _registry_mac(entry: dict[str, Any]) -> str | None:
    """MAC address of a registry entry (connection "mac") in the form the device
    reports it (upper case, no separators), else None."""
    for item in entry.get("connections") or []:
        if isinstance(item, list | tuple) and len(item) == 2 and item[0] == "mac" and isinstance(item[1], str):
            mac = item[1].replace(":", "").replace("-", "").upper()
            return mac or None
    return None


def _bluetooth_mac(entry: dict[str, Any]) -> int | None:
    """Bluetooth MAC address of a registry entry (connection "bluetooth") as a
    number, else None."""
    for item in entry.get("connections") or []:
        if isinstance(item, list | tuple) and len(item) == 2 and item[0] == "bluetooth" and isinstance(item[1], str):
            text = item[1].replace(":", "").replace("-", "")
            if len(text) == 12:
                try:
                    return int(text, 16)
                except ValueError:
                    return None
    return None


def _shelly_bluetooth_macs(registry: list[dict[str, Any]], macs: Iterable[object]) -> set[int]:
    """Bluetooth MAC addresses of the listed Shellys: a Gen2+ Shelly announces itself
    over Bluetooth with its Wi-Fi MAC address + 2. macs: MAC addresses the devices
    reported (upper case, no separators); the MACs of the Shelly registry entries
    are added."""
    found = set()
    for mac in [*macs, *(_registry_mac(e) for e in registry if isinstance(e, dict) and _is_shelly(e))]:
        text = str(mac or "")
        if len(text) == 12:
            try:
                found.add((int(text, 16) + 2) % (1 << 48))
            except ValueError:
                continue
    return found


def other_shellys(registry: list[dict[str, Any]], macs: Iterable[object] = ()) -> list[dict[str, Any]]:
    """Shellys the app cannot update, as a read-only list ordered by name: BLU devices
    behind a Shelly gateway (Shelly integration, own firmware and Bluetooth
    connection) and devices of the Bluetooth/BTHome, ZHA, MQTT (Zigbee2MQTT) and
    Matter integrations whose manufacturer text names Shelly (a heuristic: a device
    with another text is missed). A device that is also in the Shelly integration (one
    registry entry with both identifiers, e.g. Gen4 with Zigbee or Matter firmware) is a
    normal device. Disabled entries are left out, and so is an entry whose Bluetooth
    MAC address is the Wi-Fi MAC address + 2 of a listed Shelly (the Bluetooth
    integration's second entry of that Shelly; macs: see _shelly_bluetooth_macs).
    Entry: {"id", "name", "kind" (OTHER_*), "model", "firmware", "gateway" (name of the
    Shelly gateway or None)}."""
    names = {e.get("id"): e.get("name_by_user") or e.get("name") for e in registry if isinstance(e, dict)}
    shelly_ids = {e.get("id") for e in registry if isinstance(e, dict) and _is_shelly(e)}
    duplicates = _shelly_bluetooth_macs(registry, macs)
    found = []
    for entry in registry:
        if not isinstance(entry, dict) or entry.get("disabled_by") or not entry.get("id"):
            continue
        if _bluetooth_mac(entry) in duplicates:
            continue  # the listed Shelly itself, seen over Bluetooth
        ids, connections = _domains(entry, "identifiers"), _domains(entry, "connections")
        gateway = None
        if "shelly" in ids:
            if entry.get("via_device_id") not in shelly_ids or "bluetooth" not in connections:
                continue  # a Shelly of its own, or a channel of one (no firmware of its own)
            kind = OTHER_BLU_GATEWAY
            gateway = names.get(entry.get("via_device_id"))
        elif not any(m in str(entry.get("manufacturer") or "").lower() for m in SHELLY_MAKERS):
            continue
        elif ids & {"bthome", "bluetooth"} or "bluetooth" in connections:
            kind = OTHER_BLUETOOTH
        elif "zha" in ids:
            kind = OTHER_ZHA
        elif "mqtt" in ids:
            z2m = any(
                isinstance(i, list | tuple) and len(i) > 1 and str(i[1]).startswith("zigbee2mqtt")
                for i in entry.get("identifiers") or []
            )
            kind = OTHER_ZIGBEE2MQTT if z2m else OTHER_MQTT
        elif "matter" in ids:
            kind = OTHER_MATTER
        else:
            continue
        found.append(
            {
                "id": clip(entry["id"], 64),
                "name": clip(entry.get("name_by_user") or entry.get("name") or entry.get("model") or "?", 100),
                "kind": kind,
                "model": clip(entry.get("model") or entry.get("model_id") or "", 100) or None,
                "firmware": clip(entry.get("sw_version") or "", 64) or None,
                "gateway": clip(gateway, 100) if gateway else None,
            }
        )
    found.sort(key=lambda o: (str(o["name"]).lower(), str(o["id"])))
    return found[:OTHER_MAX]


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

# The same for an imported file (origin unverified): Gen2+ and Gen1
IMPORT_FALLBACK_HINT = (
    "Stored version {version} (imported file, not confirmed by Shelly) is ready and can be "
    "installed manually – at your own risk: without the answer of the Shelly update server "
    "it cannot be verified whether an intermediate version is required first."
)

IMPORT_FALLBACK_HINT_GEN1 = (
    "Stored version {version} (imported file, not confirmed by Shelly) is ready and can be "
    "installed manually – at your own risk."
)

# Where the update provided for a device comes from (device field offer_source)
OFFER_MANUFACTURER = "manufacturer"  # offered by the manufacturer's server in this check

OFFER_STORE = "store"  # stored download of an earlier check, server not asked

OFFER_UPLOAD = "upload"  # manually uploaded file from the store, server not asked

OFFER_IMPORT = "import"  # unverified file of a store import (store fallback only)

OFFER_MESSAGES = {
    OFFER_MANUFACTURER: "Provided, not installed",
    OFFER_STORE: "Provided from the firmware store, not installed",
    OFFER_UPLOAD: "Provided from the firmware store (uploaded file), not installed",
    OFFER_IMPORT: "Provided from the firmware store (imported file), not installed",
}

# User of scheduled checks and their downloads in the activity log
SCHEDULE_USER = "Schedule"

# Parallel device queries of one full check. Each Shelly is a small embedded HTTP
# server that is only asked by one request at a time; 4 devices in parallel cut
# the check time of larger installations by roughly a factor of 4 while keeping
# Wi-Fi and Home Assistant load low. Internal constant, not a setting.
CHECK_PARALLEL = 4


class Checks(Backups):
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
            ignored: list[str] = []
            removed: list[str] = []
            header_ignored: bool | None = None
            self._scan_user = SCHEDULE_USER if automatic else user or "unknown"
            self._progress()["phase"] = "registry"
            try:
                checked, ignored, removed = await self._scan_all()
                header_ignored = await self._finish_header_check()
                self._progress()["phase"] = "changelog"
                await self._store_changelogs(self.devices.values())
            except Exception as err:  # report overall errors (e.g. registry), do not crash
                log.exception("Check failed")
                failure = error_text(err, 500) or type(err).__name__
                await self.notify("Shelly firmware check failed", markdown_text(failure, 500), "sfm_summary")
            finally:
                # Also runs on cancellation (app stop): no device stays "checking"
                self._end_check()
                self.last_scan = int(time.time())
                self.cleanup_firmware()
                self.save()
                self.schedule_changed.set()
            self._log_check(
                checked,
                automatic,
                user,
                time.monotonic() - started,
                failure,
                header_ignored=header_ignored,
                ignored=ignored,
                removed=removed,
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
        value ("device", or "step" for the check after an intermediate version, which
        keeps the comparison after that update).
        A device that is no longer in the registry is marked removed and checked at
        its last address if it still answers there (finding in the log either way).
        Home Assistant is read in one scope (_ha_reads) that ends before the device is
        contacted; answers of up to HA_CACHE_S are reused (see ManagerCore._ha_cached)."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        if not self._reserved("device", device_id):
            self.reserve_check("device", device_id)
        self.device_checks[device_id] = "running"
        dev = self.devices[device_id]
        dev.pop("updated", None)  # Checked again by the user, no longer "just updated"
        if trigger != "step":  # the check after an intermediate version keeps the comparison
            dev.pop("after_update", None)  # of that update; any other check ends it
        dev.pop("install_failed", None)  # nor a failed installation to retry
        self._check_users[device_id] = user or "unknown"
        started = time.monotonic()
        failure = None
        header_ignored: bool | None = None
        removed: list[str] = []
        try:
            async with self._ha_reads():
                registry = await self._registry()
                entry = self._shelly_entries(registry).get(device_id)
                self.others = other_shellys(registry, (d.get("mac") for d in self.devices.values()))
                battery = False
                if entry is not None:
                    battery = device_id in await self._battery_devices()
                    await self._apply_areas({device_id: entry})
            checked = False
            if entry is None:
                self._mark_removed(dev)
                removed.append(device_id)
                if await self._probe_removed(device_id, dev):
                    origin = str(dev["origin"])
                    await self._check_entry(device_id, str(dev.get("name") or device_id), origin + "/", {}, False)
                    checked = True
            else:
                dev.pop("removed", None)
                dev.pop("removed_reachable", None)
                self._apply_ha_battery(dev, battery)
                await self._check_entry(device_id, self._entry_name(entry), entry.get("configuration_url"), {})
                checked = True
            if checked:
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
            removed=removed,
        )
        return {"status": dev.get("status"), "error": failure}

    async def _scan_all(self) -> tuple[list[str], list[str], list[str]]:
        """Check all Shelly main devices from the HA device registry.
        Returns the IDs of the checked devices, the names of the ignored
        (registered, not contacted) devices and the IDs of the removed devices
        (no longer in the registry: only tested whether they still answer).
        Home Assistant is read anew in one scope (_ha_reads, one connection) that ends
        before the devices are contacted; its answers refresh those single checks reuse."""
        offers: OfferTasks = {}  # shared manufacturer requests: one per firmware ID
        async with self._ha_reads(fresh=True):
            registry = await self._registry()
            entries = self._shelly_entries(registry)
            self.others = other_shellys(registry, (d.get("mac") for d in self.devices.values()))
            batteries = await self._battery_devices()
            self._readopt_removed(entries)
            await self._apply_areas(entries)
            for dev in self.devices.values():  # A full check ends the "just updated" display
                dev.pop("updated", None)  # and the retry of a failed installation
                dev.pop("after_update", None)  # and the comparison after the update
                dev.pop("install_failed", None)
            # Ignored devices count as registered (seen) but are not contacted
            seen = set(entries)
            ignored: list[str] = []
            for device_id in seen:
                dev = self.devices.setdefault(device_id, {})
                dev.pop("removed", None)  # back in Home Assistant: the stored data stays
                dev.pop("removed_reachable", None)
                self._apply_ha_battery(dev, device_id in batteries)
                if dev.get("ignored"):
                    entry = entries.pop(device_id)
                    dev["name"] = self._entry_name(entry)
                    ignored.append(dev["name"])
            await self._update_listing(registry, len(seen))
        removed = sorted(i for i in self.devices if i not in seen)
        for device_id in removed:
            self._mark_removed(self.devices[device_id])
        # Every device of this check is "waiting" until it is reached
        progress = self._progress()
        progress.update(total=len(entries) + len(removed), phase="devices")
        self.activity.update(dict.fromkeys([*entries, *removed], ACTIVITY_WAITING))
        await self._check_entries(entries, offers, removed)
        return sorted(entries), sorted(ignored, key=str.lower), removed

    async def _check_entries(
        self, entries: dict[str, dict[str, Any]], offers: OfferTasks, removed: list[str] | None = None
    ) -> None:
        """Check the given registry entries and test whether the removed devices
        still answer, up to CHECK_PARALLEL at the same time, and count the results
        in the progress of the running check."""
        progress = self._progress()
        slots = asyncio.Semaphore(CHECK_PARALLEL)

        async def check(device_id: str, entry: dict[str, Any]) -> None:
            """Check one device within the parallel limit and count the result."""
            async with slots:
                ok = await self._check_entry(device_id, self._entry_name(entry), entry.get("configuration_url"), offers)
            if not ok:
                progress["errors"] += 1
            progress["done"] += 1

        async def probe(device_id: str) -> None:
            """Test one removed device within the parallel limit and count the result."""
            async with slots:
                if not await self._probe_removed(device_id, self.devices[device_id]):
                    progress["errors"] += 1
            progress["done"] += 1

        # TaskGroup: a cancelled check (app stop) cancels every device query
        async with asyncio.TaskGroup() as group:
            for device_id, entry in entries.items():
                group.create_task(check(device_id, entry))
            for device_id in removed or []:
                group.create_task(probe(device_id))

    async def _update_listing(self, registry: list[dict[str, Any]], listed: int) -> None:
        """Shellys Home Assistant knows but the app does not list, with the reason
        (MISSING_*), for the line "N of M Shellys listed": disabled main devices of the
        Shelly integration (device registry), discoveries ignored in Home Assistant
        (config entries with source "ignore") and discoveries not set up yet (flows in
        progress). listed: number of Shelly main devices of the registry the app lists.
        A failed request of the config entries or flows leaves those reasons out
        (logged); the check goes on."""
        shelly_ids = {e.get("id") for e in registry if isinstance(e, dict) and _is_shelly(e)}
        missing: list[dict[str, Any]] = []
        for entry in registry:
            if isinstance(entry, dict) and _is_shelly(entry) and entry.get("disabled_by"):
                if entry.get("via_device_id") in shelly_ids:
                    continue  # channel or BLU device of a Shelly: no firmware of its own
                model = clip(entry.get("model_id") or entry.get("model") or "", 64) or None
                missing.append({"name": self._entry_name(entry), "model": model, "reason": MISSING_DISABLED})
        try:
            config_entries, flows = await self._ha_setup_state()
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:  # a hint only: the check goes on
            log.warning("Config entries of Home Assistant not available: %s", err or type(err).__name__)
            config_entries, flows = [], []
        for item in config_entries:
            if isinstance(item, dict) and item.get("domain", "shelly") == "shelly" and item.get("source") == "ignore":
                name = clip(item.get("title") or item.get("unique_id") or "Shelly", 100)
                missing.append({"name": name, "model": None, "reason": MISSING_IGNORED})
        for flow in flows:
            context = flow.get("context") if isinstance(flow, dict) else None
            if not isinstance(context, dict) or flow.get("handler") != "shelly":
                continue
            if context.get("source") not in DISCOVERY_SOURCES:
                continue
            placeholders = context.get("title_placeholders")
            found = placeholders.get("name") if isinstance(placeholders, dict) else None
            name = clip(found or context.get("unique_id") or "Shelly", 100)
            missing.append({"name": name, "model": None, "reason": MISSING_DISCOVERED})
        order = (MISSING_DISABLED, MISSING_DISCOVERED, MISSING_IGNORED)
        missing.sort(key=lambda m: (order.index(m["reason"]), str(m["name"]).lower()))
        self.listing = {"listed": listed, "missing": missing[:OTHER_MAX]}

    async def _apply_areas(self, entries: dict[str, dict[str, Any]]) -> None:
        """Area of Home Assistant per device of these registry entries ("area": its
        name, None without area). If the area registry cannot be read, the areas of
        the last check stay (logged). A single check may use an area registry read up
        to HA_CACHE_S before (see ManagerCore._ha_cached)."""
        try:
            listed = await self._area_registry()
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.warning("Area registry of Home Assistant not available: %s", err or type(err).__name__)
            return
        names = {
            a.get("area_id"): plain(a.get("name"), AREA_NAME_MAX)
            for a in listed
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a.get("name")
        }
        for device_id, entry in entries.items():
            dev = self.devices.setdefault(device_id, {})
            dev["area"] = names.get(entry.get("area_id")) or None

    @staticmethod
    def _entry_name(entry: dict[str, Any]) -> str:
        """Name of a device as shown in Home Assistant (user-given name first)."""
        return clip(entry.get("name_by_user") or entry.get("name") or entry.get("id") or "?", 100)

    @staticmethod
    def _mark_removed(dev: Device) -> None:
        """A device that is no longer in the device registry (removed from Home
        Assistant or the Shelly integration, or disabled there) stays listed with
        the marker removed (time it was noticed); an Ignored mark is dropped – the
        removed device is shown in its own section and skipped by the full check."""
        if not dev.get("removed"):
            dev["removed"] = int(time.time())
        dev.pop("ignored", None)

    def _readopt_removed(self, entries: dict[str, dict[str, Any]]) -> None:
        """A Shelly that was removed from Home Assistant and added again gets a new
        device ID: its stored entry (and stored password) moves to that ID when the
        MAC address of the new registry entry matches exactly one listed device that
        is no longer in the registry (marked removed already or not yet)."""
        for device_id, entry in entries.items():
            mac = _registry_mac(entry)
            if device_id in self.devices or mac is None:
                continue
            matches = [i for i, d in self.devices.items() if i not in entries and d.get("mac") == mac]
            if len(matches) != 1:
                continue
            old = matches[0]
            if old in self.installing or old in self.device_checks or old in self.reloading:
                continue
            self.devices[device_id] = self.devices.pop(old)
            try:
                self.credentials.rename(old, device_id)
            except ShellyError as err:
                log.warning("Stored password of %s not moved: %s", entry.get("name"), err)
            log.info("%s is back in Home Assistant with a new device ID – stored data kept", self._entry_name(entry))
            self._moved_device(old, device_id)

    def _moved_device(self, old: str, new: str) -> None:
        """Hook after a device moved to a new device ID (see _readopt_removed); the
        Schedules layer updates the schedules that name it."""

    @staticmethod
    def _apply_ha_battery(dev: Device, has_sensor: bool) -> None:
        """Battery device hint of Home Assistant (a Shelly battery sensor, see
        _battery_devices) for a device that never reported its battery state itself;
        what the device reports always wins (battery_source)."""
        if "battery" in dev and dev.get("battery_source") != BATTERY_FROM_HA:
            dev["battery_source"] = BATTERY_FROM_DEVICE  # also a value of older versions (read from the device)
            return
        if has_sensor:
            dev.update(battery=True, battery_source=BATTERY_FROM_HA)
        else:
            dev.pop("battery", None)
            dev.pop("battery_source", None)

    async def _probe_removed(self, device_id: str, dev: Device) -> bool:
        """Does a removed device still answer at its last address? Stored as
        removed_reachable (a refused password or a redirect to HTTPS counts as an
        answer); nothing else of the device changes. False without a known address."""
        reachable = False
        origin = dev.get("origin")
        if isinstance(origin, str) and origin:
            self.activity[device_id] = ACTIVITY_CHECKING
            try:
                await shelly.device_info(self.session, origin, self._auth_for(device_id))
                reachable = True
            except (AuthError, HttpsRequiredError):
                reachable = True
            except (TimeoutError, ShellyError, aiohttp.ClientError, KeyError, ValueError, OSError) as err:
                log.info("%s (removed) not reachable: %s", dev.get("name"), err)
            except Exception:
                log.exception("%s (removed): unexpected error while testing it", dev.get("name"))
        self.activity.pop(device_id, None)
        dev["removed_reachable"] = reachable
        return reachable

    def _shelly_entries(self, registry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Shelly main devices from the registry, ordered by name: {device ID: entry}.

        Sub-devices (channels of e.g. Pro 3EM or Strip, BLU devices behind a gateway) carry
        a via_device_id that points to another Shelly; only the main device has firmware of
        its own – stale sub-device entries are removed (BLU devices with firmware of their
        own are listed read-only, see other_shellys). A via_device_id that points to
        anything else (router, bridge of another integration) does not make a sub-device."""
        shelly_ids = {entry["id"] for entry in registry if _is_shelly(entry)}
        entries: dict[str, dict[str, Any]] = {}
        for entry in registry:
            if not _is_shelly(entry) or entry.get("disabled_by"):
                continue
            if entry.get("via_device_id") in shelly_ids:
                self.devices.pop(entry["id"], None)
                self.credentials.remove(entry["id"])
                continue
            entries[entry["id"]] = entry

        def label(i: str) -> str:
            """Sort key: the name shown in HA (user-given name first)."""
            return str(entries[i].get("name_by_user") or entries[i].get("name") or i).lower()

        order = sorted(entries, key=label)
        return {i: entries[i] for i in order}

    async def _check_entry(
        self,
        device_id: str,
        name: str,
        configuration_url: str | None,
        offers: OfferTasks,
        registered: bool = True,
    ) -> bool:
        """Check one device at the address of its registry entry (configuration_url),
        keep its result on the device and look up its host name. True on success.

        registered=False: a removed device checked at its last address (no host
        name of a registry entry, see _update_host_name)."""
        dev = self.devices.setdefault(device_id, {})
        dev["name"] = name
        dev["last_check"] = int(time.time())
        self.activity[device_id] = ACTIVITY_CHECKING
        try:
            ok = await self._attempt_check(device_id, dev, configuration_url, offers)
            await self._update_host_name(dev, configuration_url if registered else None)
            return ok
        finally:
            self.activity.pop(device_id, None)

    async def _attempt_check(
        self, device_id: str, dev: Device, configuration_url: str | None, offers: OfferTasks
    ) -> bool:
        """One check attempt of _check_entry. True on success.

        last_check = last attempt, last_success = last successful check; a failed
        attempt keeps the last known firmware so it is never shown as freshly checked.
        A known battery device (marker battery) that did not answer is STATUS_ASLEEP
        instead of STATUS_ERROR (a warning); any other device that did not answer is
        an error – it should be reachable (an ignored device is not contacted by the
        full check at all). A registry entry without a usable address sets
        address_issue ({"reason", "message"}, see shelly_net.AddressError)."""
        try:
            await self._scan_device(device_id, dev, configuration_url, offers)
        except (TimeoutError, ShellyError, aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            log.warning("%s: %s", dev["name"], err)
            text = error_text(err, 500) or type(err).__name__
            if isinstance(err, AddressError):
                dev["address_issue"] = {"reason": err.reason, "message": text}
            if dev.get("battery") is True and unreachable(err):
                dev.update(status=STATUS_ASLEEP, message=ASLEEP_MESSAGE.format(error=text), error_level="warning")
            else:
                level = "error" if unreachable(err) else severity_of(err)
                dev.update(status=STATUS_ERROR, message=text, error_level=level)
            # No answer at all: the device details explain the usual causes
            dev["unreachable"] = unreachable(err) and dev.get("status") == STATUS_ERROR
            return False
        except Exception as err:
            # Anything else (e.g. an answer of an unexpected shape) stays an error of
            # this device: the other devices of the check are not affected
            log.exception("%s: unexpected error during the check", dev["name"])
            dev.update(status=STATUS_ERROR, message=unexpected_text(err), error_level="error", unreachable=False)
            return False
        else:
            dev["last_success"] = dev["last_check"]
            dev.pop("unreachable", None)
            return True

    async def _update_host_name(self, dev: Device, configuration_url: str | None) -> None:
        """Host name column: the host name of the registry entry (configuration_url)
        wins, else <device id>.local from the ID the device reported (Gen2+ shelly_id,
        Gen1 gen1_hostname; the last known one if this attempt failed). A name set on
        the Shelly (shelly_name) is never used: the device keeps its .local host under
        the device ID, the name is only its DNS-SD service instance name. A router DNS
        name is shown when the integration entry was set up with that host name.
        Looked up at every check and kept only when it resolves (else None); the
        address it resolves to is not compared with the device. Display only: requests
        always go to the address of the registry entry. Option resolve_host_names off:
        no lookup, no host name."""
        if not self.resolve_host_names:
            dev.pop("host_name", None)
            return
        host = shelly_net.host_name_of(configuration_url) or shelly_net.local_host_name(
            dev.get("shelly_id") or dev.get("gen1_hostname")
        )
        dev["host_name"] = host if host and await shelly_net.host_resolves(host) else None

    # ------------------------------------- One device: Gen2+ next step of the server, Gen1 firmware store first

    async def _scan_device(
        self, device_id: str, dev: Device, configuration_url: str | None, offers: OfferTasks
    ) -> None:
        """Check one device and provide the new firmware if there is one."""
        origin = await shelly_net.resolve_device_origin(configuration_url)
        dev.pop("address_issue", None)
        dev.update(origin=origin)
        info = await shelly.device_info(
            self.session,
            origin,
            self._auth_for(device_id),
            lambda gen, sid: dev.update(gen=gen, shelly_id=sid),
        )
        self._apply_info(dev, info)
        # A device that needs a password the app does not have (or refuses the stored
        # one), or that requires HTTPS for its settings, fails the check: the row shows
        # the error the log shows
        await self._read_settings(device_id, dev, extended=True, raise_auth=True)

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

        dev.pop("store_fallback", None)  # set again below if it still applies
        dev.pop("step_target", None)
        dev.pop("beta", None)
        dev.pop("hold", None)
        dev.pop("offer_date", None)
        if info["gen"] == 1:
            # Gen1: the firmware store comes first. The next higher stored version is
            # provided – also instead of an update provided before – and the
            # manufacturer's list is not asked for this device.
            stored = await self._store_offer(info)
            if stored:
                version = str(stored[1]["version"])
                upload = self._source(stored[1]) == SOURCE_UPLOAD  # the user's own file is never held back
                if upload or not self._hold_back(dev, version, shelly.build_date(version)):
                    self._provide_stored(dev, info, *stored)
                return
            try:
                with self._asking_server(device_id):
                    offer = await self._manufacturer_offer(info, offers)
            except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
                await self._store_fallback(dev, info, err)
                raise
        else:
            with self._asking_server(device_id):
                offer = await self._step_offer(dev, info, offers)
                if offer is not None and self.beta_firmware:
                    offer = await self._beta_choice(dev, info, offer, offers)
        beta = offer is not None and offer.get("channel") == "beta"
        dev["offer_version"] = offer["version"] if offer else None
        if not offer or shelly.parse_version(offer["version"]) <= shelly.parse_version(info["version"]):
            dev.update(
                status=STATUS_CURRENT,
                offer_source=None,
                message="" if offer else "No stable offer",
                file=None,
                sha256=None,
            )
            if offer and info["gen"] >= 2 and not beta:  # only stable versions are kept as way back
                await self._keep_installed(device_id, dev, info, offer)
            return
        built = shelly.build_date(offer.get("build")) or shelly.build_date(offer["version"])
        # The user's own upload of exactly this version is never held back (as for Gen1),
        # nor is a beta version (the offer delay applies to stable versions only)
        held = not beta and not self._has_upload(info, offer["version"])
        if held and self._hold_back(dev, offer["version"], built):
            return
        if beta:
            dev["beta"] = True
        dev["offer_date"] = built

        # Already provided and device unchanged -> download nothing again. The provided
        # file must still be one the offer allows (_replaces_download): a file provided
        # under older rules (e.g. an imported file) is replaced by the regular provide.
        meta = self.firmware.get(str(dev.get("file")))
        already = (
            dev.get("status") == STATUS_READY
            and dev.get("pending_version") == offer["version"]
            and dev.get("pending_build") == info["build"]
            and meta is not None
            and meta.get("sha256") == dev.get("sha256")
            and self._replaces_download(meta, info["gen"], shelly_cloud.url_sha256(offer["url"]))
            and (data_paths.FW_DIR / str(dev.get("file"))).is_file()
        )
        if not already:
            name, sha, size = await self._shared_provide(device_id, dev, info, offer)
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
        if beta:
            message += " – beta version"
        dev.update(status=STATUS_READY, offer_source=source, message=message)

    async def _shared_provide(
        self, device_id: str, dev: Device, info: dict[str, Any], offer: dict[str, str]
    ) -> tuple[str, str, int]:
        """The file of the offered version (see _provide). One provide task per firmware
        ID and version, shared by the full check and all single-device checks: a
        parallel device of the same type waits for it instead of downloading the file a
        second time. Returns (file name, sha256, size)."""
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
            result: tuple[str, str, int] = await task
        finally:
            waiters.discard(device_id)
            if self.activity.get(device_id) == ACTIVITY_DOWNLOADING:
                self.activity[device_id] = ACTIVITY_CHECKING
        return result

    async def _keep_installed(
        self, device_id: str, dev: Device, info: dict[str, Any], offer: dict[str, str]
    ) -> None:
        """Option keep_installed (Gen2+): the server offers exactly the installed version
        (the device is up to date) – store that file as the device's way back, so that a
        downgrade stays possible after the next update. Nothing is provided to the
        device; a stored file the offer names is only verified. If the offer URL names
        no SHA-256, a stored download of the version is kept as it is (no new download
        at every check). A failure is logged and does not change the result of the check."""
        if not self.keep_installed or shelly.parse_version(offer["version"]) != shelly.parse_version(info["version"]):
            return
        if shelly_cloud.url_sha256(offer["url"]) is None and any(
            meta.get("type") == info["type"] and meta.get("version") == offer["version"]
            and (meta.get("gen") or 1) >= 2 and self._source(meta) == SOURCE_DOWNLOAD
            and not self._adopted_unconfirmed(meta)
            for meta in self.firmware.values()
        ):
            return  # the URL names no SHA-256: a stored download is kept, not loaded again each check
        try:
            await self._shared_provide(device_id, dev, info, offer)
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, ValueError) as err:
            log.warning("%s: installed version %s not stored: %s", dev["name"], offer["version"], err)

    def _has_upload(self, info: dict[str, Any], version: str) -> bool:
        """An upload of exactly this firmware ID and version is stored (Gen2+, not a
        file read back from the disk): the user's own file, never held back."""
        return any(
            meta.get("type") == info["type"] and meta.get("version") == version
            and (meta.get("gen") or 1) >= 2 and self._source(meta) == SOURCE_UPLOAD
            and not self._adopted_unconfirmed(meta)
            for meta in self.firmware.values()
        )

    def _hold_back(self, dev: Device, version: str, built: str | None) -> bool:
        """Option offer_after_days: a new version (not an upload) is offered only once
        it is that many days old, counted from its build date (local date of Home
        Assistant). While it is younger the device stays up to date with the marker
        hold {"version", "built", "until"} and nothing is downloaded, provided or
        notified. "Offer update now" (release_hold) lifts the hold for this version on
        this device. A version without a build date is offered at once. Returns True
        if the version is held back."""
        released = dev.get("hold_released")
        if released is not None and released != version:
            dev.pop("hold_released", None)  # a newer version is held again
        if self.offer_after_days <= 0 or not built or dev.get("hold_released") == version:
            return False
        until = datetime.date.fromisoformat(built) + datetime.timedelta(days=self.offer_after_days)
        if datetime.datetime.now(self.tz).date() >= until:
            return False
        dev.update(
            status=STATUS_CURRENT,
            offer_version=None,
            offer_source=None,
            message="",
            file=None,
            sha256=None,
            hold={"version": version, "built": built, "until": until.isoformat()},
        )
        return True

    def held_version(self, device_id: str) -> str | None:
        """The version held back for this device (option offer_after_days), else None."""
        hold = self.devices.get(device_id, {}).get("hold")
        return str(hold["version"]) if isinstance(hold, dict) and hold.get("version") else None

    def release_hold(self, device_id: str, user: str) -> str:
        """"Offer update now": lift the hold of the held version for this device (the
        caller has reserved the check of the device, which then provides it); logged
        in the activity log. Returns the version; ValueError if no version is held
        back."""
        version = self.held_version(device_id)
        if version is None:
            raise ValueError("No update is held back for this device")
        dev = self.devices[device_id]
        dev["hold_released"] = version
        log.info("%s: held update %s offered now by %s", dev.get("name"), version, user)
        self.events.add("device", action="offered_now", device=dev.get("name"), version=version, user=user)
        self.save()
        return version

    def _offer_source(self, name: str) -> str:
        """Source of the file provided for the manufacturer's offer: downloaded by this
        check (manufacturer), or a stored download or upload of exactly that version."""
        if name in self._fresh:
            return OFFER_MANUFACTURER
        meta = self.firmware.get(name)
        return self._stored_source(meta) if meta else OFFER_STORE

    def _stored_source(self, meta: dict[str, Any]) -> str:
        """Offer source (OFFER_*) of a stored file provided without a new download."""
        return {SOURCE_UPLOAD: OFFER_UPLOAD, SOURCE_IMPORT: OFFER_IMPORT}.get(self._source(meta), OFFER_STORE)

    def _provide_stored(self, dev: Device, info: dict[str, Any], name: str, meta: dict[str, Any]) -> None:
        """Gen1: provide the next higher stored version (firmware store first)."""
        # A file downloaded by this very check is the manufacturer's current offer
        fresh_url = self._fresh.get(name)
        source = OFFER_MANUFACTURER if fresh_url else self._stored_source(meta)
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
            offer_date=shelly.build_date(meta.get("version")),
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
                shelly_cloud.fetch_offer(
                    self.session, info["type"], self.pins, str(info["version"]), beta=self.beta_firmware
                )
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
        newest = await self._newest_offer(info, offers)
        return newest["version"] if newest else None

    async def _newest_offer(self, info: dict[str, Any], offers: OfferTasks) -> dict[str, str] | None:
        """Answer of the request without installed version (see _newest_version), with
        the beta offer while the option beta_firmware is on; None if the server could not
        be asked or names no stable version."""
        key = ("newest", str(info["type"]))
        if key not in offers:
            offers[key] = asyncio.ensure_future(
                shelly_cloud.fetch_offer(self.session, info["type"], self.pins, beta=self.beta_firmware)
            )
        try:
            newest: dict[str, str] | None = await offers[key]
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.warning("Newest version of %s not available: %s", info["type"], err)
            return None
        return newest

    async def _beta_choice(
        self, dev: Device, info: dict[str, Any], offer: dict[str, str], offers: OfferTasks
    ) -> dict[str, str]:
        """Option beta_firmware (Gen2+): the offer to provide – a required intermediate
        version always first (step_target set), otherwise the highest version: the beta
        version of the server's answer when it is higher than the stable offer and the
        installed version. The beta is taken from the answer for the installed version,
        else from the answer without it. A beta offer carries "channel": "beta"."""
        name = dev.get("name")
        if dev.get("step_target") is not None:
            log.info("%s: beta not asked – intermediate version %s comes first", name, offer["version"])
            return offer
        source = offer if offer.get("beta_url") else await self._newest_offer(info, offers) or {}
        version, url = source.get("beta_version"), source.get("beta_url")
        if not version or not url:
            log.info("%s: no beta version offered by the Shelly update server", name)
            return offer
        try:
            newer = shelly.parse_version(version) > max(
                shelly.parse_version(offer["version"]), shelly.parse_version(info["version"])
            )
        except ShellyError:
            log.info("%s: beta version %s not used (unknown version format)", name, shelly.plain(version, 64))
            return offer  # unknown version format: the stable offer stays
        if not newer:
            log.info("%s: beta version %s offered, not newer than %s", name, version, offer["version"])
            return offer
        log.info("%s: beta version %s offered", name, version)
        return {"version": version, "url": url, "steps": "1", "build": source.get("beta_build", ""), "channel": "beta"}

    async def _store_fallback(self, dev: Device, info: dict[str, Any], err: BaseException) -> None:
        """The manufacturer's server could not be asked: a newer stored version –
        imported files included – is offered for a manual installation at the user's
        risk (marker store_fallback: the installation needs the acknowledgment, the
        device stays in the error state) and the error is raised with a hint. Gen1:
        only imported files remain here, stored downloads are provided before
        (store first)."""
        stored = await self._store_offer(info, imports=True)
        if not stored:
            return
        name, meta = stored
        dev.update(
            offer_version=meta["version"],
            offer_source=self._stored_source(meta),
            file=name,
            sha256=meta["sha256"],
            size=meta["size"],
            pending_version=meta["version"],
            pending_build=info["build"],
            source_url=None,
            store_fallback=True,
        )
        if self._source(meta) != SOURCE_IMPORT:
            hint = STORE_FALLBACK_HINT
        else:
            hint = IMPORT_FALLBACK_HINT if info["gen"] >= 2 else IMPORT_FALLBACK_HINT_GEN1
        hint = hint.format(version=meta["version"])
        raise ShellyError(f"{error_text(err, 300) or type(err).__name__}. {hint}") from err

    @contextlib.contextmanager
    def _asking_server(self, device_id: str) -> Iterator[None]:
        """The row shows "Shelly server …" (ACTIVITY_SERVER) while the check of this
        device waits for the Shelly update server, "Checking …" again afterwards."""
        self.activity[device_id] = ACTIVITY_SERVER
        try:
            yield
        finally:
            if self.activity.get(device_id) == ACTIVITY_SERVER:
                self.activity[device_id] = ACTIVITY_CHECKING

    def _start_header_check(self) -> None:
        """Start the reference request of this check once (see HEADER_CHECK)."""
        if self._header_check is None:
            self._header_check = asyncio.ensure_future(self._header_check_request())

    async def _header_check_request(self) -> bool | None:
        """True if the server answers the reference request with the expected step,
        False if not, None if it could not be asked (no conclusion)."""
        app, version, expected = HEADER_CHECK
        try:
            offer = await shelly_cloud.fetch_offer(self.session, app, self.pins, version)
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
            offers[GEN1_OFFERS] = asyncio.ensure_future(shelly_cloud.fetch_gen1_offers(self.session, self.pins))
        return shelly_cloud.gen1_offer(await offers[GEN1_OFFERS], info["type"])

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
        stored = await self._stored_firmware(
            info["type"], offer["version"], info["gen"], offered_sha=shelly_cloud.url_sha256(offer["url"])
        )
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
            name, sha, size, https = await shelly_cloud.download_firmware(
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
            beta=True if offer.get("channel") == "beta" else None,  # source "Shelly server (beta)"
            user=self._check_users.get(device_id, self._scan_user),
        )
        return name, sha, size

    # -------------------------------------------------------------- Results: log, changelogs, notification

    # ------------------------------------------------------------- Diagnosis of a device that did not answer

    async def probe_device(self, device_id: str) -> dict[str, Any]:
        """"Try again" in the details of a device that did not answer: the address Home
        Assistant reports now, whether its port accepts a connection and whether the
        device answers, with the times. Nothing is stored or logged and no check is
        started; the device keeps its status until the next check. The device registry
        is read anew (it also refreshes the one single checks reuse). Each step:
        {"state": "ok" | "error" | "skipped", "text"}."""
        dev = self.devices[device_id]
        if device_id in self.installing or device_id in self.restoring or device_id in self.device_checks:
            raise ShellyError("The device is busy – please try again afterwards")
        steps: dict[str, dict[str, Any]] = {}
        skipped = {"state": "skipped", "text": "Not tested"}
        origin = None
        try:
            async with self._ha_reads(fresh=True):
                registry = await self._registry()
            entry = next((e for e in registry if isinstance(e, dict) and e.get("id") == device_id), None)
            if entry is None:
                raise ShellyError("The device is no longer in the device registry of Home Assistant")
            origin = await shelly_net.resolve_device_origin(entry.get("configuration_url"))
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            steps["address"] = {"state": "error", "text": error_text(err, 200) or type(err).__name__}
            return {"address": steps["address"], "port": skipped, "device": skipped}
        ip = shelly_net.origin_ip(origin) or ""
        port = urlsplit(origin).port or 80
        moved = dev.get("origin") not in (None, origin)
        steps["address"] = {"state": "ok", "ip": ip, "moved": moved}
        try:
            seconds = await shelly.port_open(ip, port)
            steps["port"] = {"state": "ok", "port": port, "seconds": round(seconds, 2)}
        except (TimeoutError, OSError) as err:
            timeout = isinstance(err, TimeoutError)
            reason = f"no answer after {shelly.PORT_TIMEOUT_S} s" if timeout else error_text(err, 120)
            steps["port"] = {"state": "error", "port": port, "text": reason or type(err).__name__}
            return {**steps, "device": skipped}
        try:
            seconds = await shelly.answers(self.session, origin)
            steps["device"] = {"state": "ok", "seconds": round(seconds, 2)}
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, ValueError) as err:
            steps["device"] = {"state": "error", "text": error_text(err, 200) or type(err).__name__}
        return steps

    def _log_check(
        self,
        checked: list[str],
        automatic: bool,
        user: str | None,
        duration: float,
        failure: str | None,
        trigger: str | None = None,
        header_ignored: bool | None = None,
        ignored: list[str] | None = None,
        removed: list[str] | None = None,
    ) -> None:
        """One log entry per check; findings = devices with an update, a warning or
        an error. trigger overrides the derived value ("device" for single-device
        checks, "step" after an intermediate version). Automatic checks name
        SCHEDULE_USER as user. A server that ignored the installed version (reference
        request, see HEADER_CHECK) is a warning finding.
        An update finding names its source (OFFER_*: manufacturer, store or upload).
        Every finding carries a severity; "error" holds the message of warnings
        and errors (key kept for historic entries). The entry gets the highest
        severity of its findings, an overall failure is an error. ignored: names of
        the ignored devices of a full check (not contacted) – listed in the entry and
        counted in the result, never a finding (no influence on the severity).
        removed: IDs of removed devices – a warning finding if the device still
        answers at its last address, an error finding if not (counted in the result).
        A removed device that a single check reached is also listed like a checked one."""
        findings: list[dict[str, Any]] = []
        for device_id in removed or []:
            dev = self.devices.get(device_id, {})
            reachable = dev.get("removed_reachable") is True
            findings.append(
                {
                    "device": dev.get("name"),
                    "error": REMOVED_REACHABLE if reachable else REMOVED_UNREACHABLE,
                    "severity": "warning" if reachable else "error",
                    "removed": True,
                }
            )
        for device_id in checked:
            if device_id in (removed or []) and self.devices.get(device_id, {}).get("removed_reachable") is not True:
                continue  # not reached: the removed finding says everything
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
                        "beta": True if dev.get("beta") else None,
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
        result = f"{updates} update(s), {counts['warning']} warning(s), {counts['error']} error(s)"
        if ignored:
            result += f", {len(ignored)} ignored"
        if removed:
            result += f", {len(removed)} removed"
        self.events.add(
            "check",
            severity=worst("error" if failure else "success", *(f["severity"] for f in findings)),
            trigger=trigger or ("scheduled" if automatic else "manual"),
            user=SCHEDULE_USER if automatic else user or "unknown",
            devices=len(checked),
            duration=round(duration, 1),
            result=failure or result,
            updates=updates,
            warnings=counts["warning"],
            errors=counts["error"],
            findings=findings,
            ignored=ignored or None,  # left out when empty
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
        Lists all open updates so that a replaced notification loses nothing; beta
        versions are counted separately and marked."""
        open_updates = [d for d in self.devices.values() if d.get("status") in (STATUS_READY, STATUS_AVAILABLE)]
        new = [d for d in open_updates if d.get("notified_version") != d.get("offer_version")]
        if not new:
            return
        for dev in new:
            dev["notified_version"] = dev.get("offer_version")
        self.save()
        lines = "\n".join(
            f"- {markdown_text(d.get('name'), 120)}: {markdown_text(d.get('version'))} → "
            f"{markdown_text(d.get('offer_version'))}" + (" (beta)" if d.get("beta") else "")
            for d in sorted(open_updates, key=lambda d: (d.get("name") or "").lower())
        )
        betas = sum(1 for d in new if d.get("beta"))
        counts = [f"{len(new) - betas} new update(s)"] if len(new) > betas else []
        if betas:
            counts.append(f"{betas} new beta update(s)")
        found = ", ".join(counts)
        await self.notify(
            f"Shelly firmware: {found} found",
            f"{lines}\n\nReview and install in the “Shelly Firmware” panel.",
            "sfm_updates",
        )
