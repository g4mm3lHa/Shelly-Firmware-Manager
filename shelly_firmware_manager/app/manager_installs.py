"""Manager, layer 5 of 6: installations and downgrades, and what the download server
needs to serve the files.

Installations are always started manually and bound to the SHA-256 of the provided
file. Several run in parallel up to a limit (asyncio.Semaphore); nothing starts during
a check. Success is only reported when the device answers with exactly the target
version. A confirmed intermediate version is followed by a check of the device, so the
next step is provided at once (installed only after a new approval).
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
    STATUS_UNCLEAR, holds_file, markdown_text, unexpected_text,
)

log = logging.getLogger("sfm.manager")

# Waiting for the result of an installation. Gen2+ over the device WebSocket: the
# device's own OTA events decide (see _await_over_events); without them (Gen1, HTTP
# fallback, WebSocket lost before the update was applied) the version is polled.
INSTALL_FIRST_CHECK_S = 30  # polling: wait after the update command

INSTALL_POLL_S = 10  # polling: then check every 10 s

# No download request: give up after 2 min (a Shelly may still answer with the old
# version for a while after accepting the update)
INSTALL_NO_DOWNLOAD_S = 120

INSTALL_WAIT_S = 300  # polling: upper limit (5 min) in case the Shelly gets stuck

OTA_STALL_S = 60  # events: no new progress for 60 s = the update got stuck
OTA_RESTART_WAIT_S = 30  # events: after 100 % the Shelly must restart within 30 s
OTA_BOOT_WAIT_S = 30  # events: and report the target version within 30 s after the restart
OTA_POLL_S = 5  # events: query interval around the restart
OTA_TICK_S = 1  # events: check interval while the update runs

# "Restart the device first" (retry after a failed installation): after the restart
# command the device goes down, then it must answer again within RESTART_WAIT_S
RESTART_SETTLE_S = 1  # wait before the first query
RESTART_DOWN_S = 30  # a device that still answers is queried this long for going down
RESTART_DOWN_POLL_S = 1  # query interval until it stops answering
RESTART_WAIT_S = 120  # the device must answer again within 2 minutes
RESTART_POLL_S = 3  # query interval while waiting for its answer

MAX_SERVED_IPS = 16  # download diagnosis: client addresses recorded per file


class Installs(Checks):
    """Approved installations, their verification and the download diagnosis."""

    # -------------------------------------------------------------------------- Installation and downgrade

    def approval_matches(self, device_id: str, sha: str) -> bool:
        """An approval is only valid while exactly this file (SHA-256) is provided –
        also for a retry after a failed installation and for a stored version offered
        while the server could not be asked (the file stays provided until the next
        check of the device; the web UI requires the risk acknowledgment for it)."""
        dev = self.devices.get(device_id) or {}
        if dev.get("gen") == 1 and not self.gen1_enabled:
            return False  # Gen1 updates disabled in the settings
        retry = dev.get("status") == STATUS_ERROR and holds_file(dev)
        return (dev.get("status") == STATUS_READY or retry) and hmac.compare_digest(str(dev.get("sha256")), sha)

    async def install(
        self, device_id: str, sha: str, approved_by: str, downgrade: bool = False, restart_first: bool = False
    ) -> bool:
        """Install an approved update or downgrade (see _install_approved). True only
        if the target version is confirmed. Normally no check follows: the device
        keeps its result until the next full check or a single check of the device.
        After a confirmed intermediate version the device is checked again at once
        (see _check_step). restart_first: restart the device and wait until it answers
        again before the installation starts (see _restart_and_wait)."""
        ok = await self._install_approved(device_id, sha, approved_by, downgrade, restart_first)
        await self._check_step(device_id, approved_by)
        return ok

    async def _check_step(self, device_id: str, user: str) -> None:
        """Check a device that installed an intermediate version: the next step of the
        server is provided (log trigger "step"). Other installations and single
        checks do not delay it; skipped if it cannot start (e.g. the full check
        runs) – the device then shows the next step at its next check."""
        if device_id not in self._step_checks:
            return
        self._step_checks.discard(device_id)
        try:
            await self.check_device(device_id, user, trigger="step")
        except (ShellyError, KeyError) as err:
            log.info("Check after the intermediate version skipped: %s", err)

    async def _install_approved(
        self, device_id: str, sha: str, approved_by: str, downgrade: bool = False, restart_first: bool = False
    ) -> bool:
        """Install an approved update. True only if the target version is confirmed.

        Several installations run in parallel, at most max_parallel_installs at a
        time; others wait for a free slot. Nothing starts during the full check or
        while the device itself is checked, and each device is in progress at most
        once; other devices may be checked meanwhile. approved_by is only written to the log.

        downgrade (Gen2+ only): install the stored older version with this
        SHA-256 instead of the provided update. The confirmation flags are checked by
        the web UI; the same install path (pre-check, download server, OTA events,
        verification of the exact target version) is used."""
        if (
            self.checking
            or self.download_base_issue
            or device_id in self.installing
            or device_id in self.reloading
            or device_id in self.device_checks
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
        retry = dev.pop("install_failed", None) or dev.get("store_fallback")
        if retry and not downgrade:
            dev["status"] = STATUS_READY  # retry / manual option: the file is provided again (download server)
        unverified = bool(dev.get("store_fallback")) and not downgrade
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
                if unverified:
                    entry["unverified"] = True  # stored version installed without the server's answer
                if restart_first:
                    entry["restarted"] = True  # the device was restarted before the installation
                self.events.add("install", event="started", **entry)
                ok, diagnosis = await self._install(device_id, dev, sha, restart_first)
                if ok and downgrade:
                    self._after_downgrade(dev)
                elif ok or dev.get("status") == STATUS_UNCLEAR:
                    # Confirmed or unconfirmed update: stays in the "Updates available"
                    # group until the next full check or a single check of this device
                    dev["updated"] = True
                    dev.pop("store_fallback", None)
                    if ok and dev.get("step_target") is not None:
                        self._step_checks.add(device_id)  # intermediate version: next step at once
                event = "confirmed" if ok else "unconfirmed"
                self.events.add("install", ok=ok, event=event, detail=diagnosis, **entry)
                return ok
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            self._install_failed(dev, entry, downgrade, error_text(err, 500) or type(err).__name__)
            return False
        except Exception as err:
            # An error no code path expects must not leave the device "installing"
            log.exception("%s: unexpected error during the installation", dev.get("name"))
            self._install_failed(dev, entry, downgrade, unexpected_text(err))
            return False
        finally:
            self.installing.discard(device_id)
            dev.pop("downgrade", None)
            self.cleanup_firmware()  # housekeeping only, stored firmware stays
            self.save()

    def _install_failed(self, dev: Device, entry: dict[str, Any] | None, downgrade: bool, message: str) -> None:
        """Record a failed installation on the device and in the activity log (entry:
        log context, None if the installation never started)."""
        dev.update(status=STATUS_ERROR, message=message)
        if not downgrade and dev.get("file"):
            # Stays in "Updates available" with its file for a retry until the next
            # full check or a single check of this device
            dev["install_failed"] = True
        if entry:
            self.events.add("install", ok=False, event="failed", detail=message, **entry)

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

    async def _verify_unchanged(self, device_id: str, dev: Device) -> None:
        """Pre-check of an installation: the device at the address is the checked one
        (MAC) and still runs the checked build; ShellyError otherwise."""
        current = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
        if current["mac"] != dev.get("mac") or current["build"] != dev.get("pending_build"):
            raise ShellyError("Device or firmware changed since the check – please check again")

    async def _restart_and_wait(self, device_id: str, dev: Device, auth: DeviceAuth | None) -> None:
        """Restart the device and wait until it answers again. A device that still
        answers after the restart command (slow shutdown) is queried every
        RESTART_DOWN_POLL_S until it stops answering – an answer before that is not
        taken as "back". If it never stops answering within RESTART_DOWN_S, the
        restart was too short to see and the installation goes on. After it went
        down, /shelly is polled every RESTART_POLL_S; ShellyError if it does not
        answer within RESTART_WAIT_S after the restart command – nothing is
        installed then."""
        dev["message"] = "Restarting the device before the installation"
        await shelly.restart(self.session, dev["gen"], dev["origin"], auth)
        started = time.monotonic()
        went_down = False
        await asyncio.sleep(RESTART_SETTLE_S)
        while True:
            try:
                await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
                if went_down:
                    return
                if time.monotonic() - started >= RESTART_DOWN_S:
                    log.info("%s: no pause seen after the restart command – installing", dev["name"])
                    return
            except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
                went_down = True
                if time.monotonic() - started >= RESTART_WAIT_S:
                    raise ShellyError(
                        "The device did not answer within 2 minutes after its restart – nothing was installed"
                    ) from err
            await asyncio.sleep(RESTART_POLL_S if went_down else RESTART_DOWN_POLL_S)

    async def _install(
        self, device_id: str, dev: Device, sha: str, restart_first: bool = False
    ) -> tuple[bool, str | None]:
        """Actual installation: optional restart, pre-check, update command, wait for
        the target version. Returns (confirmed, diagnosis or None)."""
        # 1. The approval is valid only for exactly this file and this device state
        if await asyncio.to_thread(shelly.file_sha256, data_paths.FW_DIR, dev["file"]) != sha:
            raise ShellyError("Firmware file was modified")
        auth = self._auth(device_id, dev)
        if restart_first:
            # Only the approved device is restarted: identity and build first
            await self._verify_unchanged(device_id, dev)
            await self._restart_and_wait(device_id, dev, auth)
        await self._verify_unchanged(device_id, dev)

        # 2. Trigger the update; the Shelly downloads from the app's download server.
        # Gen2+: over the device WebSocket, so the device's own OTA events
        # (progress, error) arrive; plain HTTP as fallback if the WebSocket is unusable.
        url = f"{self.download_base}/{dev['file']}"
        self._reset_download(dev)
        ota: dict[str, Any] = {
            "dev": dev,
            "error": None,
            "failed": asyncio.Event(),  # ota_error
            "applied": asyncio.Event(),  # ota_success or 100 %
            "closed": asyncio.Event(),  # the device WebSocket ended (restart)
            "last": None,  # time of the last OTA event
            "events": False,  # True while the OTA events are followed
        }
        reader = await self._update_over_websocket(dev, url, auth, ota) if dev["gen"] >= 2 else None
        ota["events"] = reader is not None
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
            dev.pop("ota_phase", None)

    async def _await_target_version(self, device_id: str, dev: Device, ota: dict[str, Any]) -> tuple[bool, str | None]:
        """3. Wait for the result. Success only when the device reports exactly the
        target version (parsed version number; Gen1 the vX.Y.Z part). Gen2+ with
        OTA events: see _await_over_events; if the WebSocket ends before the update
        was applied, the version is polled (_await_by_polling).
        Returns (confirmed, diagnosis or None); raises ShellyError on an error."""
        if ota["events"]:
            result = await self._await_over_events(device_id, dev, ota)
            if result is not None:
                return result
            log.info("%s: device WebSocket ended before the update was applied – polling the version", dev["name"])
        return await self._await_by_polling(device_id, dev, ota)

    async def _await_over_events(
        self, device_id: str, dev: Device, ota: dict[str, Any]
    ) -> tuple[bool, str | None] | None:
        """Gen2+ with OTA events:
        - ota_error → error at once;
        - no download request after 2 min, or no new progress for 60 s → error;
        - after 100 % / ota_success the Shelly must restart within 30 s (WebSocket
          closed or no answer), shown as "Restarting";
        - within 30 s after the restart it must report the target version; the old
          version, another version or no answer → error.
        Returns None if the WebSocket ended before the update was applied (the
        caller polls instead)."""
        started = time.monotonic()
        while not ota["applied"].is_set():
            await self._wait_ota(ota, OTA_TICK_S, "applied", "closed")
            if ota["applied"].is_set():
                break
            if ota["closed"].is_set():
                return None
            now = time.monotonic()
            if ota["last"] is None and not self._downloaded(dev):
                if now - started >= INSTALL_NO_DOWNLOAD_S:
                    raise ShellyError(f"Target version not confirmed. {self._install_diagnosis(dev)}")
            elif now - (ota["last"] or started) >= OTA_STALL_S:
                at = dev.get("ota_progress")
                raise ShellyError(
                    f"The update stopped{f' at {at} %' if at is not None else ''}: no progress "
                    f"from the Shelly for {OTA_STALL_S} s"
                )

        # Applied: the Shelly restarts (its WebSocket closes or it stops answering)
        target = shelly.parse_version(dev["pending_version"])
        applied_at = time.monotonic()
        restarted = ota["closed"].is_set()
        while not restarted and time.monotonic() - applied_at < OTA_RESTART_WAIT_S:
            info = await self._poll_version(device_id, dev)
            if info is None:
                restarted = True
            elif shelly.parse_version(info["version"]) == target:
                return await self._confirm(device_id, dev, info), None  # restarted between two queries
            else:
                await self._wait_ota(ota, OTA_POLL_S, "closed")
                restarted = ota["closed"].is_set()
        if not restarted:
            raise ShellyError(
                f"The Shelly applied the update but did not restart within {OTA_RESTART_WAIT_S} s"
            )

        dev.update(ota_phase="restarting", message="The Shelly restarts")
        old = shelly.parse_version(dev["version"]) if dev.get("version") else None
        restart_at = time.monotonic()
        while time.monotonic() - restart_at < OTA_BOOT_WAIT_S:
            await asyncio.sleep(OTA_POLL_S)
            info = await self._poll_version(device_id, dev)
            if info is None:
                continue
            reported = shelly.parse_version(info["version"])
            if reported == target:
                return await self._confirm(device_id, dev, info), None
            if reported == old:
                raise ShellyError(
                    f"After the restart the Shelly still reports {info['version']} – the update was not applied"
                )
            raise ShellyError(
                f"After the restart the Shelly reports {info['version']} instead of {dev['pending_version']}"
            )
        raise ShellyError(f"No answer from the Shelly within {OTA_BOOT_WAIT_S} s after the restart")

    async def _poll_version(self, device_id: str, dev: Device) -> dict[str, Any] | None:
        """Device info, or None while the Shelly does not answer (restart)."""
        try:
            return await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
        except (TimeoutError, aiohttp.ClientError, ShellyError) as err:
            log.debug("Waiting for %s: %s", dev["name"], err)
            return None

    async def _wait_ota(self, ota: dict[str, Any], seconds: float, *events: str) -> None:
        """Sleep up to seconds or until one of the named OTA events is set; an
        ota_error raises OtaError at once."""
        waiters = [asyncio.ensure_future(ota[name].wait()) for name in ("failed", *events)]
        try:
            await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        if ota["failed"].is_set():
            raise shelly.OtaError(self._ota_error_text(ota["dev"], ota["error"]))

    async def _confirm(self, device_id: str, dev: Device, info: dict[str, Any]) -> bool:
        """The device reports the target version: installed."""
        dev.update(
            info,
            status=STATUS_CURRENT,
            file=None,
            sha256=None,
            last_update=int(time.time()),
            message="Updated",
        )
        title = "Shelly downgraded" if dev.get("downgrade") else "Shelly updated"
        await self.notify(f"{title}: {dev['name']}", markdown_text(info["version"]), f"sfm_{device_id}")
        return True

    async def _await_by_polling(self, device_id: str, dev: Device, ota: dict[str, Any]) -> tuple[bool, str | None]:
        """Without OTA events: wait 30 s, then check every 10 s until the Shelly is
        reachable again, at most 5 min. A different version (also a newer one) is
        never reported as success. If it has not even requested the file after
        2 min, stop waiting – the update will never arrive. A Gen2+ ota_error event
        still ends the wait at once (raises OtaError)."""
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
                    return await self._confirm(device_id, dev, info), None
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
                ota["last"] = time.monotonic()
                if kind == "ota_progress" and event["progress"] is not None:
                    dev["ota_progress"] = event["progress"]
                    dev["message"] = f"The Shelly is downloading the firmware: {event['progress']} %"
                    if event["progress"] >= 100:
                        ota["applied"].set()
                elif kind == "ota_begin":
                    dev["ota_progress"] = dev.get("ota_progress") or 0
                    dev["message"] = "The Shelly started the update"
                elif kind == "ota_success":
                    dev["ota_progress"] = 100
                    dev["message"] = "Update applied, the Shelly restarts"
                    ota["applied"].set()
                elif kind == "ota_error":
                    ota["error"] = msg or "no details"
                    ota["failed"].set()
                    return
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            log.debug("%s: device WebSocket ended: %s", dev["name"], err)  # restart – expected
        finally:
            ota["closed"].set()
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
