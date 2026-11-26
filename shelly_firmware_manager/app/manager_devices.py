"""Manager, layer 4 of 9: reading and changing a single Shelly – identity, settings,
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
from shelly import (
    UNSUPPORTED, AuthBlockedError, AuthError, DeviceAuth, HttpsRequiredError, ShellyError, clip, error_text,
)

from manager_core import (
    ACTIVITY_CHECKING, ACTIVITY_DOWNLOADING, ACTIVITY_RELOADING, ACTIVITY_SERVER, BATTERY_FROM_DEVICE, Device,
    STATUS_AVAILABLE, STATUS_CURRENT, STATUS_INSTALLING, STATUS_READY, STATUS_UNCLEAR, severity_of, unexpected_text,
)
from manager_transfer import Transfers

log = logging.getLogger("sfm.manager")

PASSWORD_MODES = ("store", "enable", "change", "remove", "forget")

BULK_MODES = ("store", "set")  # several devices: store the existing password, or set one (enable or change)

# Settings returned after a change (the device dialogs show what the device stored)
SETTINGS_ANSWER = (
    "shelly_name", "ntp", "eco", "ap", "ap_config", "ble", "ble_proxy", "cloud", "cloud_connected", "debug_ws",
    "outputs",
)


class DeviceSettings(Transfers):
    """Device settings and the per-device password."""

    # ---------------------------------------------------------------------------------- Reading the device

    @staticmethod
    def _apply_info(dev: Device, info: dict[str, Any]) -> None:
        """Take over the identity read from the device; a changed version (e.g.
        update via the Shelly app) sets last_update."""
        if dev.get("version") and dev["version"] != info["version"]:
            dev["last_update"] = int(time.time())
        dev.update(**info)

    async def _read_settings(
        self, device_id: str, dev: Device, extended: bool = False, raise_auth: bool = False
    ) -> bool:
        """Read settings and feature states (see shelly.read_settings). True on success.

        A failure must not stop the firmware check: the old values stay, but
        settings_error marks them as unknown (shown as such, logged as warning or
        error) – a failed read-back is never reported as confirmed. Exception
        raise_auth: a missing or refused password (AuthError) and a device that
        requires HTTPS (HttpsRequiredError) are raised after settings_error is set, so
        the check fails as an error of the device. A
        battery state reported by the device replaces one derived from Home
        Assistant (battery_source)."""
        try:
            answer = await shelly.read_settings(
                self.session, dev["gen"], dev["origin"], dev["type"], self._auth(device_id, dev), extended
            )
            dev.update(answer)
            if "battery" in answer:
                dev["battery_source"] = BATTERY_FROM_DEVICE
        except (TimeoutError, ShellyError, aiohttp.ClientError, KeyError, ValueError) as err:
            log.warning("%s: reading settings failed: %s", dev.get("name"), err)
            message = error_text(err, 300) or type(err).__name__
            dev["settings_error"] = {"message": message, "severity": severity_of(err)}
            if raise_auth and isinstance(err, (AuthError, HttpsRequiredError)):
                raise
            return False
        except Exception as err:
            # An answer of an unexpected shape: the values stay unknown as well
            log.exception("%s: unexpected error while reading the settings", dev.get("name"))
            dev["settings_error"] = {"message": unexpected_text(err), "severity": "error"}
            return False
        dev.pop("settings_error", None)
        return True

    def _settings_device(self, device_id: str) -> Device:
        """Device whose settings may be read/changed now (reachable; not installing,
        reloading, restoring or being checked – in a full check also while it waits for
        the Shelly update server or downloads its firmware)."""
        dev = self.devices[device_id]
        if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
            raise ShellyError("Installation in progress – please try again afterwards")
        if device_id in self.device_checks or self.activity.get(device_id) in (
            ACTIVITY_CHECKING, ACTIVITY_SERVER, ACTIVITY_DOWNLOADING
        ):
            raise ShellyError("Device is being checked – please try again afterwards")
        if device_id in self.reloading:
            raise ShellyError("Device info is being reloaded – please try again in a moment")
        if device_id in self.restoring:
            raise ShellyError("A backup is being restored – please try again afterwards")
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
                    for marker in ("install_failed", "store_fallback", "step_target"):
                        dev.pop(marker, None)  # they belong to the offer that is installed now
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
        if ignored and dev.get("removed"):
            raise ValueError("A removed device cannot be ignored")
        if ignored:
            dev["ignored"] = True
        else:
            dev.pop("ignored", None)
        self.save()

    def forget_device(self, device_id: str, confirm: object = False, user: str = "unknown") -> None:
        """Remove from list: forget a device that was removed from Home Assistant
        (marker removed) – its stored data and stored password. Only with confirm,
        and not while the device is checked, installed or reloaded. A device that
        shows up in the registry again is found anew by the next full check. Logged."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        dev = self.devices[device_id]
        if not dev.get("removed"):
            raise ValueError("Only devices removed from Home Assistant can be removed from the list")
        if confirm is not True:
            raise ValueError("Removal not confirmed")
        busy = device_id in self.installing or dev.get("status") == STATUS_INSTALLING or device_id in self.restoring
        if busy or device_id in self.device_checks or device_id in self.reloading or self.checking:
            raise ShellyError("Device is being checked, installed or restored – please try again afterwards")
        name = str(dev.get("name") or device_id)
        self.credentials.remove(device_id)  # a write error raises ShellyError: the device stays listed
        self.devices.pop(device_id)
        log.info("Removed device %s removed from the list by %s", name, user)
        self.events.add("device", action="forgotten", device=name, user=user)
        self.cleanup_firmware()
        self.save()

    async def change_settings(
        self, device_id: str, changes: dict[str, Any], user: str = "unknown", log_entry: bool = True
    ) -> tuple[dict[str, Any], bool, bool, list[str]]:
        """Validate, send to the Shelly, read back.

        Returns (settings, restart_required, verified, not_applied). verified is False
        if the read-back failed (the change is then unconfirmed); not_applied names the
        settings the device accepted but reports with another value afterwards. Every
        attempt that reaches the device is written to the activity log – as failed if
        a value was not applied. An access point form the Shelly refused raises a
        ShellyError with a readable text that keeps the device's answer (_ap_refusal).
        log_entry False: no entry of its own (the run of a schedule writes one entry for
        all its devices)."""
        dev = self._settings_device(device_id)
        clean = shelly.validate_settings(changes, dev.get("outputs") or [])
        if ("ap" in clean or "ap_config" in clean) and dev.get("gen") == 1:
            raise ValueError(shelly.GEN1_AP_NOTE)
        if "ap_config" in clean:
            if dev.get("ap") == UNSUPPORTED:
                raise ValueError("This device/firmware has no access point switch")
            if "range_extender" in clean["ap_config"] and (dev.get("ap_config") or {}).get("range_extender") is None:
                raise ValueError("This device/firmware has no range extender")
            # "keep" leaves the protection as it is: only for a running access point the
            # device reports with a password (as the form offers it, read just before).
            # Otherwise an access point that is open by default would be turned on open
            # without the accepted risk.
            protected = dev.get("ap") is True and (dev.get("ap_config") or {}).get("open") is False
            if clean["ap_config"].get("security") == "keep" and not protected:
                raise ValueError("Choose a password or an open access point")
        for key, label in (
            ("ap", "an access point switch"),
            ("ble", "a global Bluetooth switch"),
            ("debug_ws", "websocket debug log"),
            ("cloud", "a cloud switch"),
        ):
            if key in clean and dev.get(key) == UNSUPPORTED:
                raise ValueError(f"This device/firmware has no {label}")
        diff = self._settings_diff(dev, clean)
        try:
            restart = await shelly.write_settings(
                self.session, dev["gen"], dev["origin"], clean, self._auth(device_id, dev)
            )
        except (TimeoutError, ShellyError, aiohttp.ClientError, ValueError) as err:
            readable = self._ap_refusal(err, dev, clean)
            # A readable refusal keeps the device's answer (up to 160 characters): 400 hold it in full
            error = (error_text(readable, 400) if readable else error_text(err, 300)) or type(err).__name__
            if log_entry:
                self.events.add("setting", ok=False, device=dev.get("name"), changes=diff, user=user, error=error)
            if readable:
                raise ShellyError(readable) from err
            raise
        if "eco" in clean:  # the device may restart and miss the read-back
            dev["eco"] = clean["eco"]
        verified = await self._read_settings(device_id, dev)  # show what the device really stored
        not_applied = self._not_applied(dev, clean) if verified else []
        if not_applied:
            error = "Not applied by the Shelly: " + ", ".join(not_applied)
            log.warning("%s: %s (changed by %s)", dev.get("name"), error, user)
            if log_entry:
                self.events.add("setting", ok=False, device=dev.get("name"), changes=diff, user=user, error=error)
        else:
            log.info("%s: settings changed by %s (%s)", dev.get("name"), user, ", ".join(sorted(clean)))
            if log_entry:
                self.events.add("setting", device=dev.get("name"), changes=diff, user=user)
        self.save()
        settings = {k: dev.get(k) for k in SETTINGS_ANSWER}
        return settings, restart, verified, not_applied

    @staticmethod
    def _ap_refusal(err: BaseException, dev: Device, clean: dict[str, Any]) -> str:
        """Readable text of an access point form the Shelly refused (shelly.ap_refusal), or ""
        if err is no such refusal or keeps its text. Only a change of the access point form
        alone is read this way: with other settings in the same request (possible through the
        API only) the refusal may belong to another call, so it keeps its text. Only the
        device's own refusal (a plain ShellyError of the RPC answer) is rewritten; a password,
        sign-in or HTTPS error keeps its kind and text. dev is not read again after a failed
        write, so its ap_config is the state read just before the dialog opened: open False
        means the access point had a password."""
        if set(clean) != {"ap_config"} or type(err) is not ShellyError:
            return ""
        text = str(err)
        readable = shelly.ap_refusal(
            text, clean["ap_config"].get("security") == "open", (dev.get("ap_config") or {}).get("open") is False
        )
        return readable if readable != text else ""

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
            ("cloud", "cloud", "Cloud"),
        ):
            if key not in clean:
                continue
            wanted, stored = clean[key], dev.get(prop)
            if isinstance(wanted, str):
                stored = stored or ""  # no name reported = empty name
            if stored != wanted:
                labels.append(label)
        labels += DeviceSettings._ap_not_applied(dev, clean.get("ap_config"))
        outputs = {o["key"]: o for o in dev.get("outputs") or []}
        for key, value in clean.get("aop", {}).items():
            output = outputs.get(key, {})
            if output.get("aop") != value:
                name = output.get("name") if output.get("name") not in (None, key) else key
                labels.append(f"Power-on action {clip(name, 64)}")
        return labels

    @staticmethod
    def _ap_not_applied(dev: Device, wanted: dict[str, Any] | None) -> list[str]:
        """Labels of the access point form the read-back does not show (an unknown value,
        None, counts as applied: the device does not report it)."""
        if wanted is None:
            return []
        stored = dev.get("ap_config") or {}
        labels = ["Access point"] if dev.get("ap") != wanted["enable"] else []
        if wanted.get("ssid") and stored.get("ssid") not in (None, wanted["ssid"]):
            labels.append("Access point name")
        is_open = {"password": False, "open": True}.get(wanted.get("security", "keep"))
        if is_open is not None and stored.get("open") not in (None, is_open):
            labels.append("Access point security")
        if "range_extender" in wanted and stored.get("range_extender") not in (None, wanted["range_extender"]):
            labels.append("Range extender")
        return labels

    @staticmethod
    def _ap_diff(dev: Device, wanted: dict[str, Any]) -> list[dict[str, Any]]:
        """Old -> new of the access point form for the log – never the password itself,
        only that one was set."""
        onoff: dict[object, str] = {True: "on", False: "off"}
        stored = dev.get("ap_config") or {}
        diff = [{"setting": "Access point", "old": onoff.get(dev.get("ap")), "new": onoff[wanted["enable"]]}]
        if "ssid" in wanted and wanted["ssid"] != stored.get("ssid"):
            diff.append({"setting": "Access point name", "old": stored.get("ssid") or "default",
                         "new": wanted["ssid"] or "default"})
        security = wanted.get("security", "keep")
        if security != "keep":
            old = "open" if stored.get("open") is True else "password" if stored.get("open") is False else None
            diff.append({"setting": "Access point security", "old": old,
                         "new": "open" if security == "open" else "new password"})
        if "range_extender" in wanted:
            diff.append({"setting": "Range extender", "old": onoff.get(stored.get("range_extender")),
                         "new": onoff[wanted["range_extender"]]})
        return diff

    @staticmethod
    def _settings_diff(dev: Device, clean: dict[str, Any]) -> list[dict[str, Any]]:
        """Old -> new values of a settings change, readable for the log; a value the
        change leaves as it is (e.g. "Access point: on -> on") gets no line."""
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
            ("cloud", "Cloud"),
        ):
            if key in clean:
                diff.append({"setting": label, "old": onoff.get(dev.get(key)), "new": onoff[clean[key]]})
        if "ap_config" in clean:
            diff += DeviceSettings._ap_diff(dev, clean["ap_config"])
        outputs = {o["key"]: o for o in dev.get("outputs") or []}
        for key, value in clean.get("aop", {}).items():
            old = outputs.get(key, {})
            label = old.get("name") if old.get("name") not in (None, key) else key
            diff.append(
                {"setting": f"Power-on action {clip(label, 64)}", "old": old.get("aop"), "new": value}
            )
        return [line for line in diff if line["old"] != line["new"]]

    async def wifi_signal(self, device_id: str) -> dict[str, int | None]:
        """Wi-Fi signal of the device in dBm, read now for the installation dialog
        (same restrictions as reading the settings; not stored, not logged):
        {"rssi": dBm or None} – None for a device that is not on Wi-Fi (cable); a value
        that cannot be read raises ShellyError (the dialog shows "unknown")."""
        dev = self._settings_device(device_id)
        try:
            rssi = await shelly.wifi_signal(self.session, dev["gen"], dev["origin"], self._auth(device_id, dev))
        except (TimeoutError, aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            raise ShellyError(f"Device not reachable: {error_text(err, 200) or type(err).__name__}") from None
        return {"rssi": rssi}

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
        credential. Exception: a Gen2+ Shelly.SetAuth refused at sign-in (wrong stored
        password, or the device blocks sign-ins) was not executed and is not verified –
        each further signed request would count as one more failed attempt on the
        device. Results are logged – never the password or hash."""
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
                except Exception as err:
                    # An error no code path expects stays the result of this device
                    log.exception("%s: unexpected error while setting the password", name)
                    message = unexpected_text(err)
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
        """Device part of set_device_password (see there); raises on any failure.

        Gen2+: the device ID of the live /shelly answer replaces the known one (as a
        check does) and is the realm sent to Shelly.SetAuth – a known ID from before a
        firmware update could be outdated. It is taken only from the same device
        (shelly.Protection.same_device: the same MAC suffix as the known ID or MAC);
        another Shelly at the address leaves the known ID, which that Shelly is
        expected to refuse. "store" tests the password against the realm the device's
        current password is bound to (shelly.Protection.realm); after the 2.2.0 ID
        change of the 1PM Mini Gen3/Gen4 that is the former ID, the result then
        carries realm_outdated=True and a log line says to change the password once,
        which binds it to the device ID again. If the device does not accept the
        password under that realm, "store" tests it once more under the device ID. A
        stored HA1 answers only a challenge of its own realm (DeviceAuth), so at most
        one of both tests signs a request. Nothing is changed on the device by
        "store"."""
        state = await shelly.protection_status(self.session, origin)
        protected = state.enabled
        dev["auth_enabled"] = protected
        live = gen >= 2 and state.device_id is not None and state.same_device(realm, dev.get("mac"))
        if live:
            dev["shelly_id"] = realm = state.device_id  # an empty ID never replaces a known one
        current = self._auth(device_id, dev)
        candidate = None
        if mode in ("store", "enable", "change"):
            tested = (state.realm or realm) if mode == "store" and live else realm
            candidate = self.credentials.candidate(gen, tested, password)
        if mode == "store" and candidate is not None:
            if protected is False:
                raise ShellyError("The device is not password protected – use 'Enable password protection' instead")
            works = await self._auth_works(dev, candidate)
            if not works and gen >= 2 and realm and candidate.realm != realm:
                # The device may challenge with its device ID after all. Only the
                # candidate of the challenge realm signs; the other one is refused
                # locally (DeviceAuth), so this sends no second signed attempt
                candidate = self.credentials.candidate(gen, realm, password)
                works = await self._auth_works(dev, candidate)
            if not works:
                raise AuthError("The Shelly rejected the password")
            self.credentials.store(device_id, candidate)
            if gen >= 2 and candidate.realm != realm:
                log.info(
                    "%s: stored password is bound to the former device ID of the Shelly – change it once to fix this",
                    dev.get("name"),
                )
                return {"protected": protected, "realm_outdated": True}
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
        except AuthError as err:
            # Gen2+: Shelly.SetAuth refused at sign-in (401) or blocked (429) was not
            # executed. Nothing is verified then: each further signed read would count
            # as one more failed attempt towards the device's sign-in block. Gen1 keeps
            # the verification of an ambiguous /settings/login answer.
            if gen < 2:
                answer = err
            elif isinstance(err, AuthBlockedError) or current is None:
                raise
            else:
                raise AuthError("The Shelly rejected the stored current password – nothing was changed") from None
        except (TimeoutError, ShellyError, aiohttp.ClientError) as err:
            answer = err  # the change may still have been applied: verify below
        # Verification against the device itself: a new password counts only once it
        # answers the device's challenge, whose realm DeviceAuth compares with its own
        try:
            protected = (await shelly.protection_status(self.session, origin)).enabled
        except (TimeoutError, ShellyError, aiohttp.ClientError):
            protected = None
        dev["auth_enabled"] = protected
        if mode == "remove":
            if protected is False:
                self.credentials.remove(device_id)
                return {"protected": False}
            raise ShellyError(self._unverified("Password protection was not confirmed as removed", answer))
        try:
            confirmed = protected is True and new is not None and await self._auth_works(dev, new)
            if not confirmed and mode == "change" and await self._auth_works(dev, current):
                raise ShellyError(
                    self._unverified("The password was not changed – the previous password still works", answer)
                )
        except AuthBlockedError as err:
            # A blocked device answers no further sign-in: the stored password stays
            raise ShellyError(
                self._unverified(
                    "The new password could not be confirmed – the previously stored password was kept", err
                )
            ) from None
        if confirmed and new is not None:
            # Replaces (or, if not kept, deletes) the old credential only now
            try:
                if keep:
                    self.credentials.store(device_id, new)
                else:
                    self.credentials.remove(device_id)
            except ShellyError as err:
                # The device has the new password, only the file could not be written
                raise ShellyError(
                    f"The device confirmed the new password, but this app could not save the change: {err}. "
                    "It applies only until the app restarts – solve the storage problem, then check the "
                    "stored password of this device"
                ) from None
            return {"protected": True}
        raise ShellyError(
            self._unverified(
                "The new password could not be confirmed – the previously stored password was kept", answer
            )
        )

    async def _auth_works(self, dev: Device, auth: DeviceAuth | None) -> bool:
        """True if an authenticated read succeeds with auth. Only meaningful while
        the device is protected – an unprotected device accepts anything. A sign-in
        block (AuthBlockedError) is raised: it says nothing about the password."""
        try:
            await shelly.read_settings(self.session, dev["gen"], dev["origin"], dev.get("type"), auth)
        except AuthBlockedError:
            raise
        except (TimeoutError, ShellyError, aiohttp.ClientError):  # AuthError included
            return False
        return True

    @staticmethod
    def _unverified(text: str, answer: BaseException | None) -> str:
        """Message for an unconfirmed password change, with the device's answer."""
        return f"{text} ({clip(answer, 120)})" if answer else text
