"""Manager, layer 3 of 6: reading and changing a single Shelly – identity, settings,
feature states, restart and the device password. The device password can also be
stored or set on several devices in one step (set_passwords): each device is still
set, verified, stored and logged on its own.

Nothing here looks for firmware: these are local device operations only.
"""

import asyncio
import logging
import time
from typing import Any

import aiohttp
import shelly
from shelly import UNSUPPORTED, AuthError, DeviceAuth, ShellyError, clip, error_text

from manager_core import (
    ACTIVITY_CHECKING, ACTIVITY_DOWNLOADING, ACTIVITY_RELOADING, Device, STATUS_AVAILABLE, STATUS_CURRENT,
    STATUS_INSTALLING, STATUS_READY, STATUS_UNCLEAR, severity_of,
)
from manager_store import FirmwareStore

log = logging.getLogger("sfm.manager")

PASSWORD_MODES = ("store", "enable", "change", "remove", "forget")

BULK_MODES = ("store", "set")  # several devices: store the existing password, or set one (enable or change)


class DeviceSettings(FirmwareStore):
    """Device settings and the per-device password."""

    # ---------------------------------------------------------------------------------- Reading the device

    @staticmethod
    def _apply_info(dev: Device, info: dict[str, Any]) -> None:
        """Take over the identity read from the device; a changed version (e.g.
        update via the Shelly app) sets last_update."""
        if dev.get("version") and dev["version"] != info["version"]:
            dev["last_update"] = int(time.time())
        dev.update(**info)

    async def _read_settings(self, device_id: str, dev: Device, extended: bool = False) -> bool:
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
            message = error_text(err, 300) or type(err).__name__
            dev["settings_error"] = {"message": message, "severity": severity_of(err)}
            return False
        dev.pop("settings_error", None)
        return True

    def _settings_device(self, device_id: str) -> Device:
        """Device whose settings may be read/changed now (reachable, not installing)."""
        dev = self.devices[device_id]
        if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
            raise ShellyError("Installation in progress – please try again afterwards")
        if device_id in self.device_checks or self.activity.get(device_id) in (ACTIVITY_CHECKING, ACTIVITY_DOWNLOADING):
            raise ShellyError("Device is being checked – please try again afterwards")
        if device_id in self.reloading:
            raise ShellyError("Device info is being reloaded – please try again in a moment")
        if not dev.get("origin") or not dev.get("gen"):
            raise ShellyError("Device has not been checked successfully yet")
        return dev

    async def refresh_settings(self, device_id: str) -> dict[str, Any]:
        """Read the current settings directly from the Shelly (before editing)."""
        dev = self._settings_device(device_id)
        settings = await shelly.read_settings(
            self.session, dev["gen"], dev["origin"], dev["type"], self._auth(device_id, dev)
        )
        dev.update(settings)
        dev.pop("settings_error", None)
        self.save()
        return settings

    async def reload_device(self, device_id: str) -> dict[str, str | None]:
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
            raise ShellyError(f"Device not reachable: {error_text(err, 200) or type(err).__name__}") from None
        finally:
            self.reloading.discard(device_id)
            self.activity.pop(device_id, None)
            self.cleanup_firmware()
            self.save()
        return {"warning": None if ok else f"Device status not readable: {dev['settings_error']['message']}"}

    # --------------------------------------------------------------------------------- Changing the device

    def set_ignored(self, device_id: str, ignored: object) -> None:
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

    async def change_settings(
        self, device_id: str, changes: dict[str, Any], user: str = "unknown"
    ) -> tuple[dict[str, Any], bool, bool, list[str]]:
        """Validate, send to the Shelly, read back.

        Returns (settings, restart_required, verified, not_applied). verified is False
        if the read-back failed (the change is then unconfirmed); not_applied names the
        settings the device accepted but reports with another value afterwards. Every
        attempt that reaches the device is written to the activity log – as failed if
        a value was not applied."""
        dev = self._settings_device(device_id)
        clean = shelly.validate_settings(changes, dev.get("outputs") or [])
        if "ap" in clean and dev.get("gen") == 1:
            raise ValueError(shelly.GEN1_AP_NOTE)
        for key, label in (
            ("ap", "an access point switch"),
            ("ble", "a global Bluetooth switch"),
            ("debug_ws", "websocket debug log"),
        ):
            if key in clean and dev.get(key) == UNSUPPORTED:
                raise ValueError(f"This device/firmware has no {label}")
        diff = self._settings_diff(dev, clean)
        try:
            restart = await shelly.write_settings(
                self.session, dev["gen"], dev["origin"], clean, self._auth(device_id, dev)
            )
        except (TimeoutError, ShellyError, aiohttp.ClientError, ValueError) as err:
            error = error_text(err, 300) or type(err).__name__
            self.events.add("setting", ok=False, device=dev.get("name"), changes=diff, user=user, error=error)
            raise
        if "eco" in clean:  # the device may restart and miss the read-back
            dev["eco"] = clean["eco"]
        verified = await self._read_settings(device_id, dev)  # show what the device really stored
        not_applied = self._not_applied(dev, clean) if verified else []
        if not_applied:
            error = "Not applied by the Shelly: " + ", ".join(not_applied)
            log.warning("%s: %s (changed by %s)", dev.get("name"), error, user)
            self.events.add("setting", ok=False, device=dev.get("name"), changes=diff, user=user, error=error)
        else:
            log.info("%s: settings changed by %s (%s)", dev.get("name"), user, ", ".join(sorted(clean)))
            self.events.add("setting", device=dev.get("name"), changes=diff, user=user)
        self.save()
        settings = {k: dev.get(k) for k in ("shelly_name", "ntp", "eco", "ap", "ble", "debug_ws", "outputs")}
        return settings, restart, verified, not_applied

    @staticmethod
    def _not_applied(dev: Device, clean: dict[str, Any]) -> list[str]:
        """Labels of written settings whose read-back value differs from the written one."""
        labels: list[str] = []
        for key, prop, label in (
            ("name", "shelly_name", "Shelly name"),
            ("ntp", "ntp", "NTP server"),
            ("eco", "eco", "Eco mode"),
            ("ap", "ap", "Access point"),
            ("ble", "ble", "Bluetooth"),
            ("debug_ws", "debug_ws", "Websocket debug log"),
        ):
            if key not in clean:
                continue
            wanted, stored = clean[key], dev.get(prop)
            if isinstance(wanted, str):
                stored = stored or ""  # no name reported = empty name
            if stored != wanted:
                labels.append(label)
        outputs = {o["key"]: o for o in dev.get("outputs") or []}
        for key, value in clean.get("aop", {}).items():
            output = outputs.get(key, {})
            if output.get("aop") != value:
                name = output.get("name") if output.get("name") not in (None, key) else key
                labels.append(f"Power-on action {clip(name, 64)}")
        return labels

    @staticmethod
    def _settings_diff(dev: Device, clean: dict[str, Any]) -> list[dict[str, Any]]:
        """Old -> new values of a settings change, readable for the log."""
        onoff: dict[object, str] = {True: "on", False: "off"}
        diff: list[dict[str, Any]] = []
        if "name" in clean:
            diff.append({"setting": "Shelly name", "old": dev.get("shelly_name"), "new": clean["name"]})
        if "ntp" in clean:
            diff.append({"setting": "NTP server", "old": dev.get("ntp"), "new": clean["ntp"]})
        for key, label in (
            ("eco", "Eco mode"),
            ("ap", "Access point"),
            ("ble", "Bluetooth"),
            ("debug_ws", "Websocket debug log"),
        ):
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

    async def restart_device(self, device_id: str) -> None:
        """Restart the Shelly (same restrictions as changing settings). Not logged."""
        dev = self._settings_device(device_id)
        await shelly.restart(self.session, dev["gen"], dev["origin"], self._auth(device_id, dev))
        log.info("%s: restart requested", dev.get("name"))

    # ------------------------------------------------------------------------------------- Device password

    async def set_device_password(
        self,
        device_id: str,
        mode: str,
        password: str | None = None,
        confirm: object = False,
        acknowledged: object = False,
        user: str | None = None,
        keep: object = True,
    ) -> dict[str, Any]:
        """Password dialog. Modes:

        - store:  the device is protected; test the entered password, then store it
        - enable: the device is unprotected; set a new password on the device
        - change: the device is protected and its password is stored; set a new one
        - remove: the device is protected and its password is stored; disable it
        - forget: delete the locally stored password only (the device is untouched)

        Every mode needs confirm=True, enable/change also acknowledged=True (the
        user stored the new password). keep=False (enable/change only): the new
        password is verified on the device but not stored in this app – a stored
        password is deleted once the device confirms the new one. The protection status is read live from
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
        if not isinstance(keep, bool) or (not keep and mode not in ("enable", "change")):
            raise ValueError("Invalid storage choice")
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
            result = await self._password_action(device_id, dev, mode, password, gen, origin, realm, keep)
        except (TimeoutError, ShellyError, aiohttp.ClientError, ValueError) as err:
            error = error_text(err, 300) or type(err).__name__
            if not isinstance(err, ValueError):  # input errors never reached the device
                self.events.add("password", ok=False, device=name, action=mode, user=user, error=error)
            raise
        finally:
            self.save()
        action = {"store": "stored", "enable": "enabled", "change": "changed", "remove": "removed"}[mode]
        if keep:
            log.info("%s: password %s by %s", name, action, user)
            self.events.add("password", device=name, action=action, user=user)
        else:
            log.info("%s: password %s by %s, not stored in this app", name, action, user)
            self.events.add("password", device=name, action=action, user=user, stored=False)
        return result

    def bulk_password_step(self, device_id: str, mode: str) -> tuple[str | None, str]:
        """What a multi-device password action does on one device: (single-device
        mode, "") or (None, reason why the device is skipped). "set" enables the
        protection of an unprotected device and changes the password of a protected
        one whose current password is stored (the app needs it to change)."""
        dev = self.devices.get(device_id)
        if dev is None:
            return None, "Unknown device"
        try:
            self._settings_device(device_id)
        except ShellyError as err:
            return None, str(err)
        protected = dev.get("auth_enabled")
        if protected is None:
            return None, "Protection state unknown – reload device info"
        if mode == "store":
            return ("store", "") if protected else (None, "Not password protected")
        if not protected:
            return "enable", ""
        if not self.credentials.has_device_password(device_id):
            return None, "Store the current password first"
        return "change", ""

    async def set_passwords(
        self,
        mode: str,
        device_ids: object,
        password: str | None,
        confirm: object = False,
        acknowledged: object = False,
        user: str | None = None,
        keep: object = True,
    ) -> list[dict[str, Any]]:
        """Password action for several devices in one step – each device is set,
        verified, stored and logged on its own exactly like the single-device dialog
        (set_device_password). Runs in parallel up to the option
        max_parallel_installs; a failure on one device does not stop the others,
        devices the action does not fit or that are busy are skipped.
        Returns one result per device: {"id", "name", "result": ok|error|skipped,
        "action", "message"}."""
        if mode not in BULK_MODES:
            raise ValueError("Unknown password action")
        if confirm is not True:
            raise ValueError("Confirmation required")
        if mode == "set" and acknowledged is not True:
            raise ValueError("Confirm that the new password is stored securely")
        if not isinstance(keep, bool) or (not keep and mode != "set"):
            raise ValueError("Invalid storage choice")
        if not isinstance(password, str) or not password:
            raise ValueError("No password entered")
        if not isinstance(device_ids, list) or not device_ids or len(device_ids) > 500:
            raise ValueError("Choose at least one device")
        if not all(isinstance(i, str) and i in self.devices for i in device_ids):
            raise ValueError("Unknown device in the selection")
        ids = list(dict.fromkeys(device_ids))
        slots = asyncio.Semaphore(int(self.options.get("max_parallel_installs", 5)))

        async def one(device_id: str) -> dict[str, Any]:
            """Run the action on one device (skipped if it does not fit or is busy)."""
            name = self.devices[device_id].get("name") or "?"
            async with slots:
                single, reason = self.bulk_password_step(device_id, mode)
                if single is None:
                    return {"id": device_id, "name": name, "result": "skipped", "action": None, "message": reason}
                try:
                    await self.set_device_password(
                        device_id, single, password, confirm=True, acknowledged=single != "store" or None,
                        user=user, keep=keep if single != "store" else True,
                    )
                except (TimeoutError, ShellyError, aiohttp.ClientError, ValueError, KeyError) as err:
                    message = error_text(err, 300) or type(err).__name__
                    return {"id": device_id, "name": name, "result": "error", "action": single, "message": message}
                return {"id": device_id, "name": name, "result": "ok", "action": single, "message": ""}

        results = await asyncio.gather(*(one(i) for i in ids))
        summary = {k: sum(1 for r in results if r["result"] == k) for k in ("ok", "error", "skipped")}
        log.info("Passwords (%s) on %d devices by %s: %s", mode, len(ids), user, summary)
        return list(results)

    async def _password_action(
        self,
        device_id: str,
        dev: Device,
        mode: str,
        password: str | None,
        gen: int,
        origin: str,
        realm: str | None,
        keep: bool = True,
    ) -> dict[str, Any]:
        """Device part of set_device_password (see there); raises on any failure."""
        protected = await shelly.protection_status(self.session, origin)
        dev["auth_enabled"] = protected
        current = self._auth(device_id, dev)
        candidate = None
        if mode in ("store", "enable", "change"):
            candidate = self.credentials.candidate(gen, realm, password)
        if mode == "store" and candidate is not None:
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
            answer: BaseException | None = None
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
        if protected is True and new is not None and await self._auth_works(dev, new):
            # Replaces (or, if not kept, deletes) the old credential only now
            if keep:
                self.credentials.store(device_id, new)
            else:
                self.credentials.remove(device_id)
            return {"protected": True}
        if mode == "change" and await self._auth_works(dev, current):
            raise ShellyError(
                self._unverified("The password was not changed – the previous password still works", answer)
            )
        raise ShellyError(
            self._unverified(
                "The new password could not be confirmed – the previously stored password was kept", answer
            )
        )

    async def _auth_works(self, dev: Device, auth: DeviceAuth | None) -> bool:
        """True if an authenticated read succeeds with auth. Only meaningful while
        the device is protected – an unprotected device accepts anything."""
        try:
            await shelly.read_settings(self.session, dev["gen"], dev["origin"], dev.get("type"), auth)
        except (TimeoutError, ShellyError, aiohttp.ClientError):  # AuthError included
            return False
        return True

    @staticmethod
    def _unverified(text: str, answer: BaseException | None) -> str:
        """Message for an unconfirmed password change, with the device's answer."""
        return f"{text} ({clip(answer, 120)})" if answer else text
