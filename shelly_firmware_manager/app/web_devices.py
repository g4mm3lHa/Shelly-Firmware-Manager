"""Routes of the Devices tab: checks, installations (also scheduled and downgrades),
device settings, restart, passwords (single and several devices), ignore, reload,
remove from list, the diagnosis of a device that did not answer ("Try again"), the text
for a bug ticket and hiding the card "Getting started".

An installation is bound to the SHA-256 shown to the user and needs the confirmation
flags (confirm; a downgrade or a store fallback also acknowledged; a beta version
beta_acknowledged, also when scheduled) – checked here and again by the manager.
"""

import contextlib
import re

from aiohttp import web
from events import user_from
from manager import Manager
from manager_core import DOWNLOAD_BASE_MESSAGES
from shelly import ShellyError
from web_base import Background, Handlers, confirmed_body, guarded, json_body, translated, unconfirmed

SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class DeviceRoutes(Handlers):
    """Handlers of the Devices tab and the device dialogs."""

    def __init__(self, mgr: Manager, background: Background, app_version: str) -> None:
        """app_version: named in the text for a bug ticket."""
        super().__init__(mgr, background)
        self.app_version = app_version

    async def probe(self, request: web.Request) -> web.StreamResponse:
        """"Try again" of a device that did not answer: address, port and answer, with
        the times (nothing stored, no check started)."""
        device_id = self.device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        return web.json_response(await guarded(self.mgr.probe_device(device_id)))

    async def report(self, request: web.Request) -> web.StreamResponse:
        """Text of "Details for a bug ticket" (names and addresses masked, no passwords)."""
        device_id = self.device_id_from(request)
        return web.json_response({"text": await self.mgr.device_report(device_id, self.app_version)})

    async def hide_getting_started(self, request: web.Request) -> web.StreamResponse:
        """"Hide" on the card "Getting started"."""
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        self.mgr.hide_getting_started()
        return web.json_response({"hidden": True})

    async def scan(self, request: web.Request) -> web.StreamResponse:
        """Start a manual check in the background."""
        translated(lambda: self.mgr.reserve_check("all"), bad=())  # synchronous: a second request gets 409
        self.background(self.mgr.scan(user=user_from(request.headers)))
        return web.json_response({"started": True})

    async def check_device(self, request: web.Request) -> web.StreamResponse:
        """Start the check of a single device in the background (no installation)."""
        device_id = self.device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        self.start_device_check(device_id, user_from(request.headers))
        return web.json_response({"started": True})

    async def offer_now(self, request: web.Request) -> web.StreamResponse:
        """"Offer update now": lift the hold of the version held back for this device
        (option offer_after_days) and check the device in the background, which then
        provides it (installed only after an approval)."""
        device_id = self.device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        user = user_from(request.headers)
        if not self.mgr.held_version(device_id):
            raise web.HTTPConflict(text="No update is held back for this device")
        # Reserve the check first: if it cannot start now (409), the hold stays as it is
        translated(lambda: self.mgr.reserve_check("device", device_id), bad=())
        self.mgr.release_hold(device_id, user)
        self.run_device_check(device_id, user)
        return web.json_response({"started": True})

    def start_device_check(self, device_id: str, user: str) -> None:
        """Reserve and start the check of one device in the background (409 if it
        cannot start now)."""
        translated(lambda: self.mgr.reserve_check("device", device_id), bad=())  # synchronous: no double start
        self.run_device_check(device_id, user)

    def run_device_check(self, device_id: str, user: str) -> None:
        """Run the reserved check of one device in the background."""

        async def run() -> None:
            """Single-device check; failures are already logged by the manager."""
            with contextlib.suppress(ShellyError, KeyError):
                await self.mgr.check_device(device_id, user=user)

        self.background(run())

    async def install(self, request: web.Request) -> web.StreamResponse:
        """Start an approved installation, bound to the SHA-256 shown to the user.

        {"downgrade": true} (Gen2+): install the stored older version with that
        SHA-256; needs the second confirmation ("acknowledged": true) as well.
        {"at": "YYYY-MM-DDTHH:MM"}: schedule the installation instead (local time of
        Home Assistant, at most 7 days ahead; updates only). A beta version needs
        "beta_acknowledged": true, also when scheduled. Installing a device now drops
        its scheduled installation."""
        device_id = self.device_id_from(request)
        body = await confirmed_body(request)
        sha = body.get("sha256")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise unconfirmed()
        if self.mgr.schedule_running is not None:
            raise web.HTTPConflict(
                text=f"Schedule “{self.mgr.schedule_running}” is running – please try again afterwards"
            )
        if body.get("downgrade") is True:
            if body.get("acknowledged") is not True:
                raise unconfirmed()
            blocker = self.mgr.downgrade_blocker(device_id)
            if blocker:
                raise web.HTTPConflict(text=blocker)
            if self.mgr.downgrade_match(device_id, sha) is None:
                raise web.HTTPConflict(text="The selected version is not available for a downgrade (anymore)")
            self.background(self.mgr.install(device_id, sha, approved_by=user_from(request.headers), downgrade=True))
            return web.json_response({"started": True})
        if self.mgr.checking:
            raise web.HTTPConflict(text="Check in progress – please try again afterwards")
        if self.mgr.download_base_issue:  # Unset, not in a private network or not a valid address
            raise web.HTTPConflict(text=DOWNLOAD_BASE_MESSAGES[self.mgr.download_base_issue])
        if device_id in self.mgr.installing:
            raise web.HTTPConflict(text="Installation for this device is already running")
        if device_id in self.mgr.device_checks:
            raise web.HTTPConflict(text="Device is being checked – please try again afterwards")
        if device_id in self.mgr.reloading:  # checked before a scheduled installation is dropped below
            raise web.HTTPConflict(text="Device info is being reloaded – please try again in a moment")
        if self.mgr.devices[device_id].get("ignored"):
            raise web.HTTPConflict(text="Device is ignored – resume it first")
        if not self.mgr.approval_matches(device_id, sha):
            raise web.HTTPConflict(text="Approval does not match the provided update (anymore)")
        # A stored version offered without the server's answer needs the risk acknowledgment
        if self.mgr.devices[device_id].get("store_fallback") and body.get("acknowledged") is not True:
            raise unconfirmed()
        restart_first = body.get("restart", False)  # "Restart the device first"
        if not isinstance(restart_first, bool):
            raise web.HTTPBadRequest(text="restart: true or false expected")
        if restart_first and not self.mgr.devices[device_id].get("install_failed"):
            raise web.HTTPBadRequest(text="A restart first is only offered after a failed installation")
        if self.mgr.devices[device_id].get("beta") and body.get("beta_acknowledged") is not True:
            raise unconfirmed()
        user = user_from(request.headers)
        if "at" in body:
            at = translated(lambda: self.mgr.plan_install(device_id, sha, body["at"], user, restart_first))
            return web.json_response({"scheduled": True, "at": int(at)})
        self.mgr.cancel_plan(device_id, user, reason="installed_now")
        self.background(self.mgr.install(device_id, sha, approved_by=user, restart_first=restart_first))
        return web.json_response({"started": True})

    async def planned_cancel(self, request: web.Request) -> web.StreamResponse:
        """Cancel the scheduled installation of a device."""
        device_id = self.device_id_from(request)
        await confirmed_body(request)
        if not self.mgr.cancel_plan(device_id, user_from(request.headers)):
            raise web.HTTPConflict(text="No installation is scheduled for this device (anymore)")
        return web.json_response({"cancelled": True})

    async def wifi_signal(self, request: web.Request) -> web.StreamResponse:
        """Wi-Fi signal of the device, read now (installation dialog)."""
        device_id = self.device_id_from(request)
        return web.json_response(await guarded(self.mgr.wifi_signal(device_id)))

    async def downgrades(self, request: web.Request) -> web.StreamResponse:
        """Stored older stable versions for a downgrade (Gen1: none)."""
        device_id = self.device_id_from(request)
        dev = self.mgr.devices[device_id]
        return web.json_response(
            {
                "installed": dev.get("version"),
                "type": dev.get("type"),
                "versions": self.mgr.downgrade_versions(device_id),
                "blocked": self.mgr.downgrade_blocker(device_id),
            }
        )

    async def changelog(self, request: web.Request) -> web.StreamResponse:
        """Changelog between installed and offered version (plain text entries)."""
        entries = self.mgr.changelog_for(self.device_id_from(request))
        return web.json_response({"available": entries is not None, "versions": entries or []})

    async def settings_refresh(self, request: web.Request) -> web.StreamResponse:
        """Read the live settings from the Shelly before editing."""
        device_id = self.device_id_from(request)
        return web.json_response(await guarded(self.mgr.refresh_settings(device_id)))

    async def settings_change(self, request: web.Request) -> web.StreamResponse:
        """Write validated settings to the Shelly (logged with the HA user)."""
        device_id = self.device_id_from(request)
        body = await json_body(request)
        user = user_from(request.headers)
        settings, restart, verified, not_applied = await guarded(self.mgr.change_settings(device_id, body, user))
        return web.json_response(
            {**settings, "restart_required": restart, "verified": verified, "not_applied": not_applied}
        )

    async def restart(self, request: web.Request) -> web.StreamResponse:
        """Restart the Shelly; the row shows "Restarting" until it answers again."""
        device_id = self.device_id_from(request)
        await guarded(self.mgr.restart_watched(device_id))
        self.background(self.mgr.watch_restart(device_id))
        return web.json_response({"started": True})

    async def device_password(self, request: web.Request) -> web.StreamResponse:
        """Password dialog actions (mode: store | enable | change | remove | forget).

        The confirmation flags are checked again by the manager; keep=false
        (enable/change) verifies the new password without storing it. The password is
        never returned – the answer only carries the device's protection status
        and whether credentials are stored."""
        device_id = self.device_id_from(request)
        body = await json_body(request)
        mode, password = body.get("mode"), body.get("password")
        keep = body.get("keep", True)   # enable/change: store the new password in this app
        if (
            not isinstance(mode, str)
            or (password is not None and not isinstance(password, str))
            or not isinstance(keep, bool)
        ):
            raise web.HTTPBadRequest(text="Invalid password request")
        result = await guarded(
            self.mgr.set_device_password(
                device_id,
                mode,
                password,
                confirm=body.get("confirm"),
                acknowledged=body.get("acknowledged"),
                user=user_from(request.headers),
                keep=keep,
            )
        )
        return web.json_response(
            {"protected": result.get("protected"), "password_set": self.mgr.credentials.has_device_password(device_id)}
        )

    async def devices_passwords(self, request: web.Request) -> web.StreamResponse:
        """Password action for several devices (mode: store | set), each device on
        its own; returns the result per device. The password is never returned."""
        body = await json_body(request)
        password = body.get("password")
        keep = body.get("keep", True)
        if (password is not None and not isinstance(password, str)) or not isinstance(keep, bool):
            raise web.HTTPBadRequest(text="Invalid password request")
        results = await guarded(
            self.mgr.set_passwords(
                str(body.get("mode")), body.get("devices"), password, confirm=body.get("confirm"),
                acknowledged=body.get("acknowledged"), user=user_from(request.headers), keep=keep,
            )
        )
        return web.json_response({"results": results})

    async def ignore(self, request: web.Request) -> web.StreamResponse:
        """Ignore or resume a device (local only, no device request)."""
        device_id = self.device_id_from(request)
        ignored = (await json_body(request)).get("ignored")
        translated(lambda: self.mgr.set_ignored(device_id, ignored), conflict=())
        return web.json_response({"ignored": bool(self.mgr.devices[device_id].get("ignored"))})

    async def forget_device(self, request: web.Request) -> web.StreamResponse:
        """Remove from list: forget a device removed from Home Assistant (its stored
        data and password; logged with the HA user). Needs {"confirm": true}."""
        device_id = self.device_id_from(request)
        body = await json_body(request)
        user = user_from(request.headers)
        translated(lambda: self.mgr.forget_device(device_id, confirm=body.get("confirm"), user=user))
        return web.json_response({"ok": True})

    async def reload_device(self, request: web.Request) -> web.StreamResponse:
        """Reload Device Info: re-read identity, firmware and status from the device
        only (no manufacturer request). Answers {"warning": text or null}."""
        device_id = self.device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        return web.json_response(await guarded(self.mgr.reload_device(device_id)))
