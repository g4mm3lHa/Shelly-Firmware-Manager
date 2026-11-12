"""Manager, layer 5 of 6: installations and downgrades, and what the download server
needs to serve the files.

Installations are always started manually and bound to the SHA-256 of the provided
file. Several run in parallel up to a limit (asyncio.Semaphore); nothing starts during
a check. Success is only reported when the device answers with exactly the target
version.
"""

import asyncio
import contextlib
import hmac
import logging
import time
from typing import Any

import aiohttp
import data_paths
import shelly
from shelly import DeviceAuth, ShellyError, error_text

from manager_checks import Checks
from manager_core import (
    PENDING, Device, STATUS_AVAILABLE, STATUS_CURRENT, STATUS_ERROR, STATUS_INSTALLING, STATUS_READY,
    STATUS_UNCLEAR, holds_file,
)

log = logging.getLogger("sfm.manager")

INSTALL_FIRST_CHECK_S = 30  # wait after the update command

INSTALL_POLL_S = 10  # then check every 10 s

INSTALL_NO_DOWNLOAD_S = 120  # no download request: give up after 2 min (a Shelly may

# still answer with the old version for a while after accepting the update)
INSTALL_WAIT_S = 180  # upper limit (3 min) in case the Shelly gets stuck

MAX_SERVED_IPS = 16  # download diagnosis: client addresses recorded per file


class Installs(Checks):
    """Approved installations, their verification and the download diagnosis."""

    # -------------------------------------------------------------------------- Installation and downgrade

    def approval_matches(self, device_id: str, sha: str) -> bool:
        """An approval is only valid while exactly this file (SHA-256) is provided –
        also for a retry after a failed installation (the file stays provided until
        the next check of the device)."""
        dev = self.devices.get(device_id) or {}
        if dev.get("gen") == 1 and not self.gen1_enabled:
            return False  # Gen1 updates disabled in the settings
        retry = dev.get("status") == STATUS_ERROR and holds_file(dev)
        return (dev.get("status") == STATUS_READY or retry) and hmac.compare_digest(str(dev.get("sha256")), sha)

    async def install(self, device_id: str, sha: str, approved_by: str, downgrade: bool = False) -> bool:
        """Install an approved update or downgrade (see _install_approved). True only
        if the target version is confirmed. No check follows: the device keeps its
        result until the next full check or a single check of the device."""
        return await self._install_approved(device_id, sha, approved_by, downgrade)

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
        if dev.pop("install_failed", None) and not downgrade:
            dev["status"] = STATUS_READY  # retry: the file is provided again (download server)
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
                elif ok or dev.get("status") == STATUS_UNCLEAR:
                    # Confirmed or unconfirmed update: stays in the "Updates available"
                    # group until the next full check or a single check of this device
                    dev["updated"] = True
                event = "confirmed" if ok else "unconfirmed"
                self.events.add("install", ok=ok, event=event, detail=diagnosis, **entry)
                return ok
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            dev.update(status=STATUS_ERROR, message=error_text(err, 500) or type(err).__name__)
            if not downgrade and dev.get("file"):
                # Stays in "Updates available" with its file for a retry until the next
                # full check or a single check of this device
                dev["install_failed"] = True
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
        if await asyncio.to_thread(shelly.file_sha256, data_paths.FW_DIR, dev["file"]) != sha:
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
        await self._pause(ota, INSTALL_FIRST_CHECK_S, dev)
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
            await self._pause(ota, INSTALL_POLL_S, dev)

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

    async def _pause(self, ota: dict[str, Any], seconds: float, dev: Device) -> None:
        """Sleep while waiting for the device; ends at once on a Gen2+ ota_error."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ota["failed"].wait(), seconds)
        if ota["failed"].is_set():
            raise shelly.OtaError(self._ota_error_text(dev, ota["error"]))

    def _ota_error_text(self, dev: Device, error: str) -> str:
        """Message of a Gen2+ ota_error. If the download server has served the file to
        this device, the device got it and rejected it (e.g. an installed version too
        old for a direct update); otherwise the download most likely did not reach it."""
        if self._downloaded(dev):
            return (
                f"The Shelly reports an update error: {error}. It downloaded the firmware but "
                "rejected it – the installed version may be too old for a direct update to "
                f"{dev.get('pending_version')}: install an intermediate version first (web "
                "interface of the Shelly or upload in the Firmware tab)"
            )
        return (
            f"The Shelly reports an update error: {error}. Check that "
            f"{self.download_base} is reachable from the Shelly (LAN IP address of Home "
            "Assistant, port 8098)"
        )

    # ----------------------------------------------------------------------------- Download server support

    def may_download(self, name: str, client_ip: str) -> bool:
        """May client_ip download the file name? Only provided files, optionally only to Shellys.
        Rejected attempts on provided files are recorded for the installation diagnosis;
        served downloads are counted by record_download (GET only)."""
        # Only while an update is provided or being installed (not for a failed one)
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
