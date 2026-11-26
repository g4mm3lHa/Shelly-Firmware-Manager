"""Manager, layer 7 of 9: installations and downgrades, and what the download server
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
import secrets
import time
from typing import Any

import aiohttp
import data_paths
import shelly
import shelly_cloud
import shelly_net
from shelly import AuthError, DeviceAuth, ShellyError, error_text

from manager_checks import Checks
from manager_core import (
    ACTIVITY_RESTARTING, DOWNLOAD_BASE_MESSAGES, PENDING, Device, STATUS_ASLEEP, STATUS_AVAILABLE, STATUS_CURRENT,
    STATUS_ERROR, STATUS_INSTALLING, STATUS_READY, STATUS_UNCLEAR, holds_file, markdown_text, section_of,
    unexpected_text,
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
OTA_STALL_WEAK_S = 120  # the same with a weak Wi-Fi signal (WEAK_RSSI or lower at the start)
OTA_RESTART_WAIT_S = 30  # events: after 100 % the Shelly must restart within 30 s
OTA_BOOT_WAIT_S = 90  # events: and report the target version within 90 s after the restart
OTA_POLL_S = 5  # events: query interval around the restart
OTA_TICK_S = 1  # events: check interval while the update runs

# Two-step update (see _await_second_pass): firmware that reorganises the memory of a Gen2+
# device installs in two passes. The device restarts on its old version and, after a pause
# of about 1.5 min, downloads and installs the update a second time by itself. The app
# never sends a second command; it waits for the second pass to start, then for its result.
TWO_STEP_VERSION = (2, 2, 0)  # first version that reorganises the memory (major, minor, patch)
TWO_STEP_START_S = 180  # the second pass must start within 3 min (download again, or a restart)
TWO_STEP_USUAL_S = 150  # it usually starts within 2.5 min (longer = late: the pill turns yellow)
# Once the device downloads the file again, the whole second pass (download, writing, restart)
# must end within this time: the download server reports no progress, so one total limit applies
TWO_STEP_FINISH_S = INSTALL_WAIT_S

# "Restart the device first" (retry after a failed installation): after the restart
# command the device goes down, then it must answer again within RESTART_WAIT_S
RESTART_SETTLE_S = 1  # wait before the first query
RESTART_DOWN_S = 30  # a device that still answers is queried this long for going down
RESTART_DOWN_POLL_S = 1  # query interval until it stops answering
RESTART_WAIT_S = 120  # the device must answer again within 2 minutes
RESTART_POLL_S = 3  # query interval while waiting for its answer

MAX_SERVED_IPS = 16  # download diagnosis: client addresses recorded per file

# Weak Wi-Fi before the update command: the pre-check and the command are tried again
# while the Shelly does not answer (time-out, connection error) – up to PRECHECK_ATTEMPTS
# attempts within PRECHECK_WINDOW_S. A refusal of the device (password, other device,
# other firmware) is never repeated, and the command only if it surely never left.
PRECHECK_ATTEMPTS = 5
PRECHECK_WINDOW_S = 60
PRECHECK_PAUSE_S = 5  # pause between two attempts
# Reading running time and Wi-Fi signal: optional, never stops an installation – except a
# refused or blocked sign-in (the update command would be refused the same way)
LINK_TIMEOUT_S = 5
WEAK_RSSI = -80  # dBm and lower: weak signal (as the check list of the installation dialog)

# Steps of an installation as the status pill shows them (install_step_view), in their
# order. Per step: the time it usually takes at most (longer = "late": the pill turns
# yellow), counted from the last progress or answer of the Shelly, else from the start of
# the step; the pre-check is late also from its second attempt on.
INSTALL_STEPS = ("backup", "restart", "precheck", "install", "reboot", "verify")
STEP_USUAL_S = {"backup": 60, "restart": 60, "precheck": 5, "install": 30, "reboot": 15, "verify": 30}
STEP_USUAL_POLLING_S = 120  # "install" without OTA events (Gen1, HTTP fallback): no progress is reported

TEST_PATH = "/connection-test/"  # path of the test address on the download server (download_server)

TEST_TRIES = 3  # connection test without a chosen device: devices tried at most while none answers

TEST_FROM_MAX = 8  # connection test: other client addresses recorded at most

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
        After a confirmed update with a backup right before it, the device is compared
        with that backup (compare_after_update); after a confirmed intermediate version
        the device is checked again at once (see _check_step). restart_first: restart the
        device and wait until it answers again before the installation starts (see
        _restart_and_wait)."""
        ok = await self._install_approved(device_id, sha, approved_by, downgrade, restart_first)
        before = self._before_backups.pop(device_id, None)
        if ok and before is not None and not downgrade:
            await self.compare_after_update(device_id, before, approved_by)
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
        while the device itself is checked or restored, and each device is in progress
        at most once; other devices may be checked meanwhile. approved_by is only
        written to the log. With the option backup_before_install the settings of the
        device are backed up first; if that fails, nothing is installed.

        downgrade (Gen2+ only): install the stored older version with this
        SHA-256 instead of the provided update. The confirmation flags are checked by
        the web UI; the same install path (pre-check, download server, OTA events,
        verification of the exact target version) is used."""
        if (
            self.checking
            or self.download_base_issue
            or device_id in self.installing
            or device_id in self.reloading
            or device_id in self.restoring
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
        dev.pop("after_update", None)  # and so is the comparison of the last update
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
                downgrade_from=section_of(dev),  # the device list keeps the device in this section
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
                # Option "Back up before every installation": no backup, no installation
                if self.backup_before_install:
                    self._set_step(device_id, "backup")
                await self.backup_before_installation(device_id, dev, approved_by)
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
                if ok:
                    diagnosis = self._losses_text(device_id)  # success with connection losses: a hint line
                if ok and downgrade:
                    self._after_downgrade(dev)
                elif ok or dev.get("status") == STATUS_UNCLEAR:
                    # Confirmed or unconfirmed update: stays in the "Updates available"
                    # group until the next full check or a single check of this device
                    dev["updated"] = True
                    dev.pop("store_fallback", None)
                    if ok and dev.get("step_target") is not None:
                        self._step_checks.add(device_id)  # intermediate version: next step at once
                if ok and (self.install_steps.get(device_id) or {}).get("second_pass"):
                    entry["two_step"] = True  # installed in two passes (the log shows a hint line)
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
            self.install_steps.pop(device_id, None)
            dev.pop("downgrade", None)
            dev.pop("downgrade_from", None)
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

    # ------------------------------------------------------------------------- Steps and weak Wi-Fi

    def _set_step(self, device_id: str, step: str, usual: float | None = None, give_up: float | None = None) -> None:
        """The installation of device_id enters step (one of INSTALL_STEPS). usual:
        longer = late (default STEP_USUAL_S); give_up: when the app reports an error
        (None: no fixed limit). The Wi-Fi signal, the connection losses, a restart the
        app asked for but did not see and the second pass of a two-step update stay
        with the installation."""
        now = time.monotonic()
        before = self.install_steps.get(device_id) or {}
        self.install_steps[device_id] = {
            "step": step,
            "since": now,
            "quiet": now,  # last sign of life within the step (progress, answer)
            "usual": usual if usual is not None else STEP_USUAL_S[step],
            "give_up": give_up,
            "attempt": None,
            "rssi": before.get("rssi"),
            "losses": before.get("losses", 0),
            "restart_unseen": before.get("restart_unseen", False),
            "second_pass": before.get("second_pass", False),
        }

    def _update_step(self, device_id: str, **fields: Any) -> None:
        """Change fields of the running step (nothing if no step is set)."""
        step = self.install_steps.get(device_id)
        if step is not None:
            step.update(fields)

    def install_step_view(self, device_id: str) -> dict[str, Any] | None:
        """The running step for the status pill and its tooltip, or None: {"step",
        "n", "of" (position in INSTALL_STEPS), "seconds" (quiet time: since the last
        progress or answer, else the start of the step), "usual", "late", "give_up",
        "attempt", "attempts", "rssi", "second_pass" (the device installs the update a
        second time: two-step update)}. Not stored – only while the installation runs."""
        step = self.install_steps.get(device_id)
        if step is None:
            return None
        quiet = int(time.monotonic() - step["quiet"])
        attempt = step["attempt"]
        return {
            "step": step["step"],
            "n": INSTALL_STEPS.index(step["step"]) + 1,
            "of": len(INSTALL_STEPS),
            "seconds": quiet,
            "usual": step["usual"],
            "late": quiet >= step["usual"] or (attempt or 1) > 1,
            "give_up": step["give_up"],
            "attempt": attempt,
            "attempts": PRECHECK_ATTEMPTS if attempt else None,
            "rssi": step["rssi"],
            "second_pass": step["second_pass"],
        }

    def _weak(self, device_id: str) -> int | None:
        """The Wi-Fi signal read at the start of the installation if it is weak, else None."""
        rssi = (self.install_steps.get(device_id) or {}).get("rssi")
        return rssi if isinstance(rssi, int) and rssi <= WEAK_RSSI else None

    def _stall_limit(self, device_id: str) -> int:
        """Seconds without progress after which a running download counts as stuck:
        longer with a weak Wi-Fi signal."""
        return OTA_STALL_WEAK_S if self._weak(device_id) is not None else OTA_STALL_S

    def _losses_text(self, device_id: str) -> str | None:
        """Hint line of a confirmed installation whose connection was lost (counted per gap of
        unanswered attempts before the update command), else None."""
        losses = (self.install_steps.get(device_id) or {}).get("losses", 0)
        if not losses:
            return None
        weak = self._weak(device_id)
        times = "once" if losses == 1 else f"{losses} times"
        signal = f" (weak Wi-Fi: {weak} dBm)" if weak is not None else ""
        return f"Connection to the Shelly lost {times} while waiting – tried again{signal}"

    async def _read_link(self, device_id: str, dev: Device) -> dict[str, int | None]:
        """Running time and Wi-Fi signal of the device ({} if it cannot be read in
        LINK_TIMEOUT_S – both are optional). A refused or blocked sign-in (AuthError) is
        raised: the update command would be refused the same way, and one more signed
        request counts as one more failed attempt on the device."""
        try:
            async with asyncio.timeout(LINK_TIMEOUT_S):
                return await shelly.link_status(self.session, dev["gen"], dev["origin"], self._auth(device_id, dev))
        except AuthError:
            raise
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
            log.debug("%s: running time and Wi-Fi signal not read: %s", dev.get("name"), err)
            return {}

    async def _command_update(
        self, device_id: str, dev: Device, url: str, auth: DeviceAuth | None, ota: dict[str, Any]
    ) -> asyncio.Task[None] | None:
        """Step "precheck": pre-check (_verify_unchanged) and update command, tolerant of
        a weak Wi-Fi. While the Shelly does not answer (time-out, connection error) both
        are tried again, up to PRECHECK_ATTEMPTS attempts within PRECHECK_WINDOW_S; a
        refusal of the device is raised at once. The command is sent again only if it
        surely never left (no connection); without an answer to a sent command the
        installation waits for the device as usual (never a second command).

        After the first answer the Wi-Fi signal is read (longer download tolerance when
        it is weak). After an answer that followed unanswered attempts, the running time
        tells a restart of the device (it runs shorter than the time since its last
        answer) from a lost connection: a restart stops the installation, a lost
        connection is counted for the hint line of the result. Only the pre-check is
        bounded by the window; a command on its way is never cut off. Returns the OTA
        event reader (Gen2+ WebSocket) or None (HTTP: the version is polled)."""
        self._set_step(device_id, "precheck", give_up=PRECHECK_WINDOW_S)
        started = time.monotonic()
        last_alive = started  # last answer of the device (before the first one: the start of the step)
        missed = False  # unanswered attempts since the last answer
        linked = False  # running time and signal read after the first answer
        attempt = 0
        while True:
            attempt += 1
            self._update_step(device_id, attempt=attempt)
            try:
                left = max(PRECHECK_PAUSE_S, PRECHECK_WINDOW_S - (time.monotonic() - started))
                async with asyncio.timeout(left):
                    await self._verify_unchanged(device_id, dev)
                answered = time.monotonic()
                self._update_step(device_id, quiet=answered)
                if missed or not linked:
                    await self._after_answer(device_id, dev, last_alive if missed else None)
                last_alive, missed, linked = answered, False, True
                reader = await self._update_over_websocket(dev, url, auth, ota) if dev["gen"] >= 2 else None
                if reader is None:
                    await self._update_over_http(dev, url, auth)
                return reader
            except (TimeoutError, aiohttp.ClientError, OSError) as err:
                # No answer, or the command never left: try again while the window lasts
                missed = True
                elapsed = time.monotonic() - started
                log.info("%s: no answer before the update command (attempt %d): %s",
                         dev["name"], attempt, error_text(err, 120) or type(err).__name__)
                if attempt >= PRECHECK_ATTEMPTS or elapsed + PRECHECK_PAUSE_S >= PRECHECK_WINDOW_S:
                    weak = self._weak(device_id)
                    raise ShellyError(
                        f"Connection to the Shelly lost before the update command – no answer in {attempt} "
                        f"attempts within {round(elapsed)} s. Nothing was installed."
                        f"{f' Wi-Fi signal: {weak} dBm (weak).' if weak is not None else ''}"
                    ) from None
                await asyncio.sleep(PRECHECK_PAUSE_S)

    async def _after_answer(self, device_id: str, dev: Device, last_alive: float | None) -> None:
        """The device answered the pre-check: read its running time and Wi-Fi signal
        once, and after unanswered attempts (last_alive: time of its answer before them,
        or the start of the step) decide between a lost connection (counted) and a
        restart (ShellyError: nothing is installed) – it restarted if it runs shorter
        than the time since last_alive. Restart & install: a restart the app asked for
        but did not see (the device kept answering) is the expected one, once."""
        link = await self._read_link(device_id, dev)
        if link.get("rssi") is not None:
            self._update_step(device_id, rssi=link["rssi"])
        if last_alive is None:
            return
        uptime = link.get("uptime")
        step = self.install_steps.get(device_id) or {}
        if uptime is not None and uptime < time.monotonic() - last_alive:
            if step.get("restart_unseen"):
                step["restart_unseen"] = False
                log.info("%s: the restart before the installation happened now", dev["name"])
                return
            raise ShellyError(
                f"The Shelly restarted unexpectedly before the update command (running for {uptime} s). "
                "Nothing was installed."
            )
        if step:
            step["losses"] += 1

    async def _update_over_http(self, dev: Device, url: str, auth: DeviceAuth | None) -> None:
        """Update command over HTTP (Gen1, or Gen2+ without a usable WebSocket). A
        connection that was never made raises (the command did not leave: it may be
        sent again); without an answer to a sent command the device may update anyway –
        the installation then waits for it and the command is never sent twice."""
        try:
            await shelly.trigger_update(self.session, dev["gen"], dev["origin"], url, auth)
        except aiohttp.ClientConnectorError:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            log.warning("%s: no answer to the update command (%s) – waiting for the device",
                        dev["name"], error_text(err, 120) or type(err).__name__)

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
        self._set_step(device_id, "restart", give_up=RESTART_WAIT_S)
        await shelly.restart(self.session, dev["gen"], dev["origin"], auth)
        if not await self._back_after_restart(device_id, dev, time.monotonic()):
            raise ShellyError("The device did not answer within 2 minutes after its restart – nothing was installed")

    async def _back_after_restart(self, device_id: str, dev: Device, started: float) -> bool:
        """Wait after a restart command sent at started (monotonic) until the device
        answers again (see _restart_and_wait). False if it did not answer within
        RESTART_WAIT_S."""
        went_down = False
        await asyncio.sleep(RESTART_SETTLE_S)
        while True:
            try:
                await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
                if went_down:
                    return True
                if time.monotonic() - started >= RESTART_DOWN_S:
                    log.info("%s: no pause seen after the restart command", dev["name"])
                    self._update_step(device_id, restart_unseen=True)  # Restart & install: it may still come
                    return True
            except (TimeoutError, ShellyError, aiohttp.ClientError, OSError):
                went_down = True
                if time.monotonic() - started >= RESTART_WAIT_S:
                    return False
            await asyncio.sleep(RESTART_POLL_S if went_down else RESTART_DOWN_POLL_S)

    async def restart_watched(self, device_id: str) -> None:
        """Restart from the device row: send the command (an error is raised for the
        dialog), then the row shows "Restarting" (ACTIVITY_RESTARTING) until the device
        answers again – see watch_restart, run in the background by the caller."""
        await self.restart_device(device_id)
        self.activity[device_id] = ACTIVITY_RESTARTING

    async def watch_restart(self, device_id: str) -> None:
        """Wait for the device after restart_watched. Back in time: the row shows its
        status from before again. No answer within RESTART_WAIT_S: the device shows the
        error (the next check replaces it)."""
        started = time.monotonic()
        dev = self.devices.get(device_id)
        try:
            if dev is not None and not await self._back_after_restart(device_id, dev, started):
                log.warning("%s: no answer within %d s after the restart", dev.get("name"), RESTART_WAIT_S)
                dev.update(status=STATUS_ERROR, error_level="error", unreachable=True,
                           message=f"No answer from the Shelly within {RESTART_WAIT_S} s after the restart")
                self.save()
        finally:
            if self.activity.get(device_id) == ACTIVITY_RESTARTING:
                self.activity.pop(device_id, None)

    async def _install(
        self, device_id: str, dev: Device, sha: str, restart_first: bool = False
    ) -> tuple[bool, str | None]:
        """Actual installation: optional restart, pre-check, update command, wait for
        the target version. Returns (confirmed, diagnosis or None).

        The pre-check (_verify_unchanged: same MAC, still the checked build) runs before
        a restart and again before the update command, so a device swapped at the
        address or updated meanwhile is never sent the approved file. Gen2+ sends the
        command over the RPC WebSocket (OTA events), HTTP only if nothing was sent
        there; Gen1 over HTTP."""
        # 1. The approval is valid only for exactly this file and this device state
        if await asyncio.to_thread(shelly_cloud.file_sha256, data_paths.FW_DIR, dev["file"]) != sha:
            raise ShellyError("Firmware file was modified")
        auth = self._auth(device_id, dev)
        if restart_first:
            # Only the approved device is restarted: identity and build first
            await self._verify_unchanged(device_id, dev)
            await self._restart_and_wait(device_id, dev, auth)

        # 2. Pre-check again and trigger the update; the Shelly downloads from the app's
        # download server. Gen2+: over the device WebSocket, so the device's own OTA
        # events (progress, error) arrive; plain HTTP as fallback if the WebSocket is
        # unusable. Both tolerate a weak Wi-Fi (see _command_update).
        url = f"{self.download_base}/{dev['file']}"
        self._reset_download(dev)
        ota: dict[str, Any] = {
            "device_id": device_id,
            "dev": dev,
            "error": None,
            "failed": asyncio.Event(),  # ota_error
            "applied": asyncio.Event(),  # ota_success or 100 %
            "closed": asyncio.Event(),  # the device WebSocket ended (restart)
            "last": None,  # time of the last OTA event
            "events": False,  # True while the OTA events are followed
        }
        reader = await self._command_update(device_id, dev, url, auth, ota)
        ota["events"] = reader is not None
        self._set_step(
            device_id, "install", usual=None if reader is not None else STEP_USUAL_POLLING_S,
            give_up=INSTALL_NO_DOWNLOAD_S if reader is not None else INSTALL_WAIT_S,
        )  # fmt: skip
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
            self._set_step(device_id, "install", usual=STEP_USUAL_POLLING_S, give_up=INSTALL_WAIT_S)
        return await self._await_by_polling(device_id, dev, ota)

    async def _await_over_events(
        self, device_id: str, dev: Device, ota: dict[str, Any]
    ) -> tuple[bool, str | None] | None:
        """Gen2+ with OTA events:
        - ota_error → error at once;
        - no download request after 2 min, or no new progress for 60 s (120 s with a weak
          Wi-Fi signal, _stall_limit) → error;
        - after 100 % / ota_success the Shelly must restart within 30 s (WebSocket
          closed or no answer), shown as "Restarting";
        - within 90 s after the restart it must report the target version; another
          version or no answer → error. The old version is an error too, unless a
          two-step update is expected (_two_step_expected): then the app waits for the
          second pass of the device (_await_second_pass).
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
                continue
            stall = self._stall_limit(device_id)
            self._update_step(device_id, give_up=stall)  # downloading: the stall limit applies
            if now - (ota["last"] or started) >= stall:
                at = dev.get("ota_progress")
                raise ShellyError(
                    f"The update stopped{f' at {at} %' if at is not None else ''}: no progress "
                    f"from the Shelly for {stall} s"
                )

        # Applied: the Shelly restarts (its WebSocket closes or it stops answering)
        target = shelly.parse_version(dev["pending_version"])
        self._set_step(device_id, "reboot", give_up=OTA_RESTART_WAIT_S)
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
        self._set_step(device_id, "verify", give_up=OTA_BOOT_WAIT_S)
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
                if await self._two_step_expected(device_id, dev, info["version"]):
                    return await self._await_second_pass(device_id, dev, info["version"])
                raise ShellyError(
                    f"After the restart the Shelly still reports {info['version']} – the update was not applied"
                )
            raise ShellyError(
                f"After the restart the Shelly reports {info['version']} instead of {dev['pending_version']}"
            )
        raise ShellyError(f"No answer from the Shelly within {OTA_BOOT_WAIT_S} s after the restart")

    @staticmethod
    def _two_step_possible(dev: Device) -> bool:
        """A two-step update can only happen on a Gen2+ device and never on a downgrade."""
        return dev.get("gen", 1) >= 2 and not dev.get("downgrade")

    async def _two_step_expected(self, device_id: str, dev: Device, reported: str) -> bool:
        """A Gen2+ update that came back on its old version (reported) may run in two
        steps: True if one of these signals says so –
        - the offer of the update server named several steps (step_target "", stored
          by the check; works without a device request),
        - the device lists the target version with nsteps in its available updates
          (shelly.update_nsteps; empty while the device has had no contact with the
          Shelly cloud for a day),
        - the update crosses TWO_STEP_VERSION: the old version (major, minor, patch)
          is below it, the target at or above it (pre-releases count as their number).
        Never for Gen1 or a downgrade (_two_step_possible). All signals are logged, for
        the diagnosis."""
        if not self._two_step_possible(dev):
            return False
        target = str(dev["pending_version"])
        offer = dev.get("step_target") == ""
        nsteps = await shelly.update_nsteps(self.session, dev["origin"], target, self._auth(device_id, dev))
        try:
            crossing = shelly.parse_version(reported)[:3] < TWO_STEP_VERSION <= shelly.parse_version(target)[:3]
        except ShellyError:
            crossing = False
        expected = offer or nsteps is not None or crossing
        log.info(
            "%s: back on %s after the update to %s – two-step update %s (offer with several steps: %s, "
            "steps reported by the device: %s, crossing %s: %s)",
            dev.get("name"), shelly.plain(reported, 64), shelly.plain(target, 64),
            "expected" if expected else "not expected", offer, nsteps, ".".join(map(str, TWO_STEP_VERSION)), crossing,
        )  # fmt: skip
        return expected

    async def _restarted(self, device_id: str, dev: Device, last_alive: float) -> bool:
        """The device answers again after a gap without answers (last_alive: time of its
        answer before the gap): True if it restarted in the gap – it runs shorter than the
        time since last_alive, the rule of the pre-check (_after_answer). False after a
        lost connection, and when the running time cannot be read. One signed request; a
        refused or blocked sign-in (AuthError) is raised (see _read_link)."""
        uptime = (await self._read_link(device_id, dev)).get("uptime")
        restarted = uptime is not None and uptime < time.monotonic() - last_alive
        log.info("%s: answers again after %d s without an answer (running for %s s) – %s", dev["name"],
                 round(time.monotonic() - last_alive), uptime, "restarted" if restarted else "connection lost")
        return restarted

    async def _await_second_pass(self, device_id: str, dev: Device, version: str) -> tuple[bool, str | None]:
        """Two-step update: the device came back on its old version (version) after an
        applied update and installs it a second time by itself – the app never sends a
        second command. The status stays installing, so the download server still
        serves the file to the device; the step is "install" with second_pass, "reboot"
        while the device does not answer, "install" again when it answers on the old
        version after that.

        Two phases:
        - the second pass must start within TWO_STEP_START_S: the app serves the file to
          this device's own address again, or the device stops answering (it restarts).
          A download that cannot be attributed to a device (see _served_count) does not
          start it;
        - once the device downloaded the file again, the whole second pass (download,
          writing, restart) must end within TWO_STEP_FINISH_S – the download server
          reports no progress; after going down the device must answer within
          OTA_BOOT_WAIT_S.
        Exactly the target version: confirmed. Another version: error. The old version
        after a gap without answers that followed a new download: the running time
        decides (_restarted) – a restart means the update was not applied (error), a lost
        connection goes on waiting for the second pass. The old version after a gap
        without a new download goes on waiting within the first window. Returns
        (True, None); raises ShellyError otherwise."""
        target = shelly.parse_version(dev["pending_version"])
        old = shelly.parse_version(version)
        dev.pop("ota_progress", None)  # the progress of the first pass is over
        dev.pop("ota_phase", None)
        started = time.monotonic()
        deadline = started + TWO_STEP_START_S  # the second pass must have started by then
        served = self._served_count(dev, own_only=True)  # downloads of the file by this device so far
        served_at: float | None = None  # time the device downloaded the file again
        down_since: float | None = None  # time the device stopped answering
        last_alive = started  # last answer of the device (it just answered on its old version)
        self._set_step(device_id, "install", usual=TWO_STEP_USUAL_S, give_up=TWO_STEP_START_S)
        self._update_step(device_id, second_pass=True)
        log.info("%s: waiting up to %d s for the second pass of the update", dev["name"], TWO_STEP_START_S)
        while True:
            await asyncio.sleep(OTA_POLL_S)
            info = await self._poll_version(device_id, dev)
            now = time.monotonic()
            if info is None:
                if down_since is None:  # the device restarts (or the connection is lost)
                    down_since = now
                    log.info("%s: second pass – no answer from the Shelly (%s)", dev["name"],
                             "after downloading the file again" if served_at is not None else "no new download seen")
                    self._set_step(device_id, "reboot", usual=OTA_RESTART_WAIT_S, give_up=OTA_BOOT_WAIT_S)
                elif now - down_since >= OTA_BOOT_WAIT_S:
                    raise ShellyError(f"No answer from the Shelly within {OTA_BOOT_WAIT_S} s after the restart")
                continue
            reported = shelly.parse_version(info["version"])
            if reported == target:
                log.info("%s: installed in two steps", dev["name"])
                return await self._confirm(device_id, dev, info), None
            if reported != old:
                raise ShellyError(
                    f"After the restart the Shelly reports {info['version']} instead of {dev['pending_version']}"
                )
            if down_since is not None:  # answers again on the old version
                down_since = None
                if served_at is None:  # no new download seen: the first window goes on
                    self._set_step(device_id, "install", usual=TWO_STEP_USUAL_S, give_up=TWO_STEP_START_S)
                    self._update_step(device_id, since=started, quiet=started)
                elif await self._restarted(device_id, dev, last_alive):
                    raise ShellyError(
                        f"After the restart the Shelly still reports {info['version']} – the update was not applied"
                    )
                else:  # connection lost during the new download: the second pass goes on
                    self._set_step(device_id, "install", usual=STEP_USUAL_POLLING_S, give_up=TWO_STEP_FINISH_S)
                    self._update_step(device_id, since=served_at, quiet=served_at)
            last_alive = time.monotonic()
            if served_at is None and self._served_count(dev, own_only=True) > served:
                served_at = now
                log.info("%s: second pass – the Shelly downloads the file again from the app", dev["name"])
                self._update_step(device_id, quiet=now, usual=STEP_USUAL_POLLING_S, give_up=TWO_STEP_FINISH_S)
            if served_at is not None:
                if now - served_at >= TWO_STEP_FINISH_S:
                    raise ShellyError(
                        f"After the restart the Shelly still reports {info['version']}. The update to "
                        f"{dev['pending_version']} can run in two steps, but the second step did not finish within "
                        f"{TWO_STEP_FINISH_S} s – start the update again"
                    )
            elif now >= deadline:
                raise ShellyError(
                    f"After the restart the Shelly still reports {info['version']}. The update to "
                    f"{dev['pending_version']} can run in two steps, but the second step did not start within "
                    f"{TWO_STEP_START_S} s – start the update again"
                )

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
        if device_id not in self.notify_muted:
            await self.notify(f"{title}: {dev['name']}", markdown_text(info["version"]), f"sfm_{device_id}")
        return True

    async def _await_by_polling(self, device_id: str, dev: Device, ota: dict[str, Any]) -> tuple[bool, str | None]:
        """Without OTA events: wait 30 s, then check every 10 s until the Shelly is
        reachable again, at most 5 min. A different version (also a newer one) is
        never reported as success. If it has not even requested the file after
        2 min, stop waiting – the update will never arrive. A Gen2+ ota_error event
        still ends the wait at once (raises OtaError).

        Two-step update: a Gen2+ device that downloaded the file, stopped answering and
        answers again on its old version is checked for a restart by its running time
        (_restarted: a lost connection, or an unknown running time, is no restart and
        waits for the next gap). After a restart the device is asked once whether a second
        pass is expected (_two_step_expected). If so, the step shows the second pass and
        the limit grows to at least TWO_STEP_START_S from then, and to OTA_BOOT_WAIT_S
        from each restart after it. Without the target version the result stays
        unconfirmed, with the two-step diagnosis."""
        target = shelly.parse_version(dev["pending_version"])
        old = shelly.parse_version(dev["version"]) if dev.get("version") else None
        unexpected = None  # last reported version that is neither old nor target
        limit: float = INSTALL_WAIT_S  # upper limit of the wait (longer for a second pass)
        two_step: bool | None = None  # second pass expected (None: not asked yet)
        went_down = False  # the device stopped answering after the download (maybe its restart)
        gap_from = 0.0  # time of the last answer before that gap (the restart check measures from it)
        down = False  # it did not answer the last query
        started = time.monotonic()
        last_alive = started  # last answer of the device (before the first one: the update command)
        await self._pause(ota, INSTALL_FIRST_CHECK_S, dev)
        while (elapsed := time.monotonic() - started) < limit:
            downloaded = self._downloaded(dev)
            check_restart = False  # back on the old version after a gap: did it restart?
            try:
                info = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
                down = False
                reported = shelly.parse_version(info["version"])
                last_alive = time.monotonic()  # a valid answer: the device runs
                if reported == target:
                    return await self._confirm(device_id, dev, info), None
                # Neither old nor target: keep waiting (polling is unchanged), but the
                # result can only be "unconfirmed" with this version as hint
                unexpected = info["version"] if reported != old else None
                if not unexpected and not downloaded and elapsed >= INSTALL_NO_DOWNLOAD_S:
                    break  # reachable, old version, nothing downloaded
                check_restart = not unexpected and went_down and two_step is None
            except (TimeoutError, aiohttp.ClientError, ShellyError) as err:
                log.debug("Waiting for %s: %s", dev["name"], err)  # restart – expected
                if two_step and not down:  # the second pass restarts the device
                    limit = max(limit, elapsed + OTA_BOOT_WAIT_S)
                    self._update_step(device_id, give_up=round(limit))
                if downloaded and not went_down:
                    went_down, gap_from = True, last_alive
                down = True
            if check_restart:
                if not self._two_step_possible(dev):
                    two_step = False  # Gen1 or a downgrade: no running time needed
                elif await self._restarted(device_id, dev, gap_from):
                    two_step = await self._two_step_expected(device_id, dev, info["version"])
                    if two_step:
                        limit = max(limit, elapsed + TWO_STEP_START_S)
                        self._update_step(device_id, second_pass=True, give_up=round(limit))
                else:
                    went_down = False  # a lost connection, not the restart: wait for the next gap
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
            else self._install_diagnosis(dev, two_step=bool(two_step))
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
                self._update_step(ota["device_id"], quiet=ota["last"])  # progress: no longer quiet
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
            shelly_net.origin_ip(d.get("origin", "")) == client_ip for d in owners
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

    def _served_count(self, dev: Device, own_only: bool = False) -> int:
        """GETs of this device's file served to it so far. A request from an address that
        belongs to no known device (Docker without the sender IP, see
        restrict_downloads_to_devices) cannot be attributed and counts for every device
        of that file – unless own_only: then only the requests from the device's own
        address count (another Shelly's download of the same file never counts)."""
        served = (self.downloads.get(str(dev.get("file"))) or {}).get("served") or {}
        own = int(served.get(shelly_net.origin_ip(dev.get("origin", "")), 0))
        if own_only:
            return own
        known = {shelly_net.origin_ip(d["origin"]) for d in self.devices.values() if d.get("origin")}
        return own + sum(int(count) for address, count in served.items() if address not in known)

    def _reset_download(self, dev: Device) -> None:
        """Forget earlier downloads of this device only (installation start)."""
        record = self.downloads.get(str(dev.get("file")))
        if record is not None:
            record["served"].pop(shelly_net.origin_ip(dev.get("origin", "")), None)

    def _downloaded(self, dev: Device) -> bool:
        """True if this device has started downloading its file (see _served_count)."""
        return self._served_count(dev) > 0

    def _install_diagnosis(self, dev: Device, two_step: bool = False) -> str:
        """Explains why the target version was not reached. two_step: a second pass of
        a two-step update was expected but the target version did not come."""
        record = self.downloads.get(str(dev.get("file")), {})
        if two_step and self._downloaded(dev):
            # Unconfirmed cannot be installed directly: a check of the device provides it again
            return (
                f"The Shelly still reports {dev.get('version')}. The update to {dev.get('pending_version')} "
                "can run in two steps, but the second step did not finish – check the device, then install again"
            )
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
                f"{shelly_net.origin_ip(dev['origin'])}. Disable the option "
                "restrict_downloads_to_devices"
            )
        return (
            f"The Shelly never requested the firmware – is {self.download_base} reachable "
            "from the Shelly (IP of Home Assistant, port from the Network section)?"
        )

    # ----------------------------------------------------------------------- Connection test of the server

    def test_devices(self) -> list[str]:
        """Devices that can run a connection test (Gen2+ with a checked address, not
        removed, ignored or busy), the ones that answered last first. A sleeping battery
        device is left out: it would not answer."""
        found = [
            (device_id, dev)
            for device_id, dev in self.devices.items()
            if (dev.get("gen") or 0) >= 2
            and dev.get("origin")
            and not dev.get("removed")
            and not dev.get("ignored")
            and dev.get("battery") is not True
            and dev.get("status") != STATUS_ASLEEP
            and device_id not in self.installing
            and device_id not in self.restoring
        ]
        found.sort(key=lambda item: (item[1].get("status") == STATUS_ERROR, -(item[1].get("last_success") or 0)))
        return [device_id for device_id, _dev in found]

    def test_fetched(self, token: str, client_ip: str, consume: bool) -> bool:
        """Download server: may client_ip fetch the test address with token? Only the
        device of the running test, and only once (consume: a GET; HEAD does not count).
        Requests with the right token from another address are recorded for the result."""
        test = self._test
        if test is None or not hmac.compare_digest(token, test["token"]):
            return False
        if client_ip != test["ip"]:
            if len(test["from"]) < TEST_FROM_MAX:
                test["from"].add(client_ip)
            return False
        if test["fetched"]:
            return False
        if consume:
            test["fetched"] = True
        return True

    async def run_connection_test(self, device_id: str | None, user: str = "unknown") -> dict[str, Any]:
        """Connection test of the download server: a Gen2+ Shelly fetches a test address
        of the download server (HTTP.GET), which answers only during this test, only to
        that device and only once, with a short text (never a file). Nothing is changed
        on the device. device_id None: the first device of test_devices that answers
        (at most TEST_TRIES are tried). The result is stored (connection_test), logged
        and returned; ShellyError if the test cannot start."""
        if self.download_base_issue:
            raise ShellyError(DOWNLOAD_BASE_MESSAGES[self.download_base_issue])
        if self._test is not None:
            raise ShellyError("A connection test is already running")
        candidates = self.test_devices()
        if device_id is not None:
            if device_id not in candidates:
                raise ShellyError("This device cannot run the test now (Gen 2 or newer, checked, not busy)")
            candidates = [device_id]
        if not candidates:
            raise ShellyError("No Shelly of Gen 2 or newer is known yet – run a check first")
        url = str(self.download_base)
        result: dict[str, Any] = {}
        for tried in candidates[:TEST_TRIES]:
            result = await self._test_with(tried, url)
            if not result.pop("silent", False):
                break  # the device answered: its result counts
        result.update(at=int(time.time()), url=url)
        self.connection_test = result
        self.save()
        outcome = "reachable" if result["ok"] else result["error"]
        if result.get("detail"):
            outcome += f" ({result['detail']})"  # the device's raw answer
        log.info("Connection test with %s: %s", result.get("device"), outcome)
        self.events.add(
            "connection", ok=result["ok"], severity="success" if result["ok"] else "error", device=result.get("device"),
            url=url, error=result.get("error"), detail=result.get("detail"), user=user,
        )  # fmt: skip
        return result

    async def _test_with(self, device_id: str, url: str) -> dict[str, Any]:
        """One device of the connection test: {"ok", "device", "ip", "seconds", "error"};
        "silent" if the device itself did not answer (another one may be tried)."""
        dev = self.devices[device_id]
        ip = shelly_net.origin_ip(str(dev["origin"])) or ""
        result: dict[str, Any] = {"ok": False, "device": dev.get("name"), "ip": ip, "seconds": None, "error": None}
        token = secrets.token_hex(16)
        self._test = {"token": token, "ip": ip, "fetched": False, "from": set()}
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            origin, auth = str(dev["origin"]), self._auth(device_id, dev)
            answer = await shelly.fetch_test(self.session, origin, url + TEST_PATH + token, auth)
            seconds = round(loop.time() - started, 1)
            test = self._test
            if test["fetched"] and answer["code"] == 200:
                result.update(ok=True, seconds=seconds)
            elif test["from"]:
                others = ", ".join(sorted(test["from"]))
                result["error"] = (
                    f"The request reached the download server from {others}, not from the device address {ip} – "
                    "switch off the option “Serve downloads only to known Shellys” or check the network (NAT)"
                )
            elif answer["code"] is not None:
                result["error"] = (
                    f"Another web server answered at {url} (HTTP {answer['code']}) – check the IP address and the port"
                )
            else:
                result["error"] = "The device gave no answer code"
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            log.warning("Connection test: %s did not answer", dev.get("name"))
            result.update(error=f"Device not reachable: {error_text(err, 200) or type(err).__name__}", silent=True)
        except ShellyError as err:
            raw = error_text(err, 300)
            result["error"] = shelly.fetch_error(raw, url)
            if result["error"] != raw:
                result["detail"] = raw  # the device's own answer: Logs tab, app log and bug ticket text only
        finally:
            self._test = None
        return result
