"""Manager, layer 4 of 8: backups of the device settings – storing them (by hand, before
an installation, by a schedule), listing, restoring, download and import.

A backup belongs to a device by its MAC address (backup_store holds the encrypted
files); what is read from a device, compared and written back is shelly_backup.
Stored backups are encrypted with the key of this app. A downloaded file is encrypted
with a password generated for it, which is returned once and kept nowhere.

Restoring changes a device, so it is narrow on purpose: one restore at a time, only to
the device the backup was made on (MAC address and model are read from the device),
always after a new backup of the current settings ("Before restore"), and the network
settings only if the request names and acknowledges them. While it runs the device is
neither checked nor installed nor changed otherwise (self.restoring).

Never part of an answer or the log: the settings themselves, except the differences
the restore dialog lists (passwords and keys are no part of a backup at all).
"""

import asyncio
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import backup_store
import shelly
import shelly_backup
import store_transfer
import timetable
from backup_store import BackupError, normalize_mac
from events import worst
from shelly import AuthError, ShellyError, clip, error_text

from manager_core import Device, STATUS_INSTALLING, severity_of, unexpected_text, unreachable
from manager_devices import DeviceSettings

log = logging.getLogger("sfm.manager")

BACKUP_PARALLEL = 4  # devices read at the same time by "Back up now" and by a schedule run

DOWNLOAD_TTL_S = 600  # a prepared download must be fetched within 10 min

IMPORT_TTL_S = 900  # a staged backup file that is not imported within 15 min is dropped

APP_USER = "App"  # user of what the app does by itself (retention), in the log

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")  # token of a prepared download or a staged import

# Why a device has no backup (shown in the card "No Backup"; see Backups._no_backup)
NONE_REMOVED = "removed"  # removed from Home Assistant: not backed up any more
NONE_PASSWORD = "password"  # noqa: S105 – a reason, not a password: protected, and its password is not stored
NONE_ASLEEP = "asleep"  # battery device that did not answer
NONE_UNREACHABLE = "unreachable"  # did not answer at the last attempt
NONE_FAILED = "failed"  # the last attempt failed for another reason (see its message)

# Result of one device of a run (see Backups._backup_result)
RESULT_STORED = "stored"
RESULT_UNCHANGED = "unchanged"  # nothing changed since its newest backup (schedule runs only)
RESULT_ASLEEP = "asleep"
RESULT_UNREACHABLE = "unreachable"
RESULT_REFUSED = "refused"

REMOVED_TEXT = "Removed from Home Assistant – not backed up any more"

WRONG_FILE_PASSWORD = "Wrong password, or the file was changed since the download"  # noqa: S105 – a message

ENTRY_VIEW = ("id", "at", "reason", "note", "fw", "size", "parts", "kept", "imported")


def _failure_text(err: BaseException) -> str:
    """Message of a failed backup or restore for the dialog and the log."""
    text = error_text(err, 300) or type(err).__name__
    return f"Device not reachable: {text}" if unreachable(err) else text


def _file_name(dev: Device, entry: dict[str, Any], day: str) -> str:
    """Name of a downloaded backup file: backup-<Shelly name>-<date>.sfmb."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(dev.get("shelly_name") or dev.get("name") or entry["mac"]))
    return f"backup-{name.strip('_')[:48] or entry['mac']}-{day}{backup_store.EXTENSION}"


class _UnexpectedAnswer(ShellyError):
    """A device answered in a shape no code path expects (logged with its traceback):
    an error in the log, not a warning."""


def _failure_severity(err: BaseException) -> str:
    """Log severity of a backup that was not stored."""
    return "error" if isinstance(err, _UnexpectedAnswer) else severity_of(err)


class Backups(DeviceSettings):
    """Backups of the device settings."""

    # ------------------------------------------------------------------------------------ Storing a backup

    def _target(self, device_id: str, dev: Device) -> shelly_backup.Target:
        """The device as shelly_backup reaches it."""
        return shelly_backup.Target(self.session, dev["gen"], dev["origin"], self._auth(device_id, dev))

    def _backup_device(self, device_id: str) -> Device:
        """Device that may be backed up now (see _settings_device); a device removed
        from Home Assistant is not backed up any more."""
        dev = self._settings_device(device_id)
        if dev.get("removed"):
            raise ShellyError(REMOVED_TEXT)
        return dev

    async def _read_backup(self, device_id: str, dev: Device) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read the identity and everything a backup holds from the device:
        (meta without ID, time and reason; data)."""
        info = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
        mac = normalize_mac(info.get("mac"))
        if mac is None:
            raise ShellyError("The device reported no MAC address")
        gen = int(info["gen"])
        target = shelly_backup.Target(self.session, gen, dev["origin"], self._auth(device_id, dev))
        data = await shelly_backup.read(target)
        data["protected"] = info.get("auth_enabled")  # compared at a restore, never written
        meta = {
            "mac": mac,
            "gen": gen,
            "device": dev.get("name"),
            "model": info.get("model"),
            "type": info.get("type"),
            "fw": info.get("version"),
            "parts": shelly_backup.summary(gen, data),
            "digest": shelly_backup.digest(gen, data),
        }
        return meta, data

    async def _backup(
        self,
        device_id: str,
        dev: Device,
        reason: str,
        note: str | None = None,
        user: str = "unknown",
        only_changed: bool = False,
        log_entry: bool = True,
    ) -> dict[str, Any] | None:
        """Read the settings of a device and store them as a backup; then the
        retention of this device is applied (the newest backup_keep that are not
        marked as kept stay). Returns the entry of the new backup – None if
        only_changed is set and the device reports the same settings as in its newest
        backup. A failure is noted on the device (backup_failed), logged (unless
        log_entry is False: a run over several devices writes one entry for all of
        them) and raised. No guard here: the callers decide whether the device may be
        read now."""
        name = dev.get("name")
        try:
            meta, data = await self._read_backup(device_id, dev)
            newest = next(iter(self.backups.of(meta["mac"])), None)
            if only_changed and newest is not None and newest["digest"] == meta["digest"]:
                if dev.pop("backup_failed", None) is not None:
                    self.save()
                return None
            meta.update(id=secrets.token_hex(8), at=int(time.time()), reason=reason, note=note)
            entry = self.backups.add(meta, data)
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            self._backup_failed(dev, reason, note, user, err, _failure_text(err), severity_of(err), log_entry)
            raise
        except Exception as err:
            # An answer of an unexpected shape: no backup of this device, the others go on
            log.exception("%s: unexpected error while backing up the settings", name)
            text = unexpected_text(err)
            self._backup_failed(dev, reason, note, user, err, text, "error", log_entry)
            raise _UnexpectedAnswer(text) from None
        dev.pop("backup_failed", None)
        log.info("%s: settings backed up (%s, %d bytes) by %s", name, reason, entry["size"], user)
        if log_entry:
            self.events.add(
                "backup", event="stored", device=name, reason=reason, note=note, size=entry["size"],
                parts=entry["parts"], user=user,
            )  # fmt: skip
        for removed in self.backups.prune(entry["mac"], self.backup_keep):
            log.info("%s: oldest backup removed (more than %d stored)", name, self.backup_keep)
            self.events.add(
                "backup", event="pruned", device=name, backup_at=removed["at"], keep=self.backup_keep, user=APP_USER
            )
        self.save()
        return entry

    def _backup_failed(
        self,
        dev: Device,
        reason: str,
        note: str | None,
        user: str,
        err: BaseException,
        text: str,
        severity: str,
        log_entry: bool,
    ) -> None:
        """Note a failed backup on the device and in the log. Before an installation
        it is always an error: the installation does not start."""
        if isinstance(err, AuthError):
            code = NONE_PASSWORD
        elif unreachable(err):
            code = NONE_ASLEEP if dev.get("battery") is True else NONE_UNREACHABLE
        else:
            code = NONE_FAILED
        dev["backup_failed"] = {"at": int(time.time()), "reason": code, "message": clip(text, 300)}
        log.warning("%s: backup of the settings failed: %s", dev.get("name"), text)
        if log_entry:
            before_install = reason in ("update", "downgrade")
            self.events.add(
                "backup", severity="error" if before_install else severity, event="failed", device=dev.get("name"),
                reason=reason, note=note, error=clip(text, 300), user=user,
            )  # fmt: skip
        self.save()

    async def backup_now(self, device_id: str, user: str = "unknown") -> dict[str, Any]:
        """"Back up now" for one device. Returns the view of the new backup; raises
        ShellyError if the device cannot be backed up now or does not answer."""
        dev = self._backup_device(device_id)
        try:
            entry = await self._backup(device_id, dev, "manual", user=user)
        except (TimeoutError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            raise ShellyError(_failure_text(err)) from None
        return self._entry_view(entry or {})

    async def backup_before_installation(self, device_id: str, dev: Device, user: str) -> None:
        """The backup right before an update or downgrade is installed (option
        backup_before_install). ShellyError if it fails: nothing is installed then."""
        if not self.backup_before_install:
            return
        dev["message"] = "Backing up the settings before the installation"
        reason = "downgrade" if dev.get("downgrade") else "update"
        try:
            await self._backup(device_id, dev, reason, f"{dev.get('version')} → {dev.get('pending_version')}", user)
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            raise ShellyError(
                f"Backup before the installation failed – nothing was installed: {_failure_text(err)}"
            ) from None

    async def _backup_result(
        self,
        device_id: str,
        reason: str,
        note: str | None,
        user: str,
        only_changed: bool = False,
        log_entry: bool = True,
    ) -> dict[str, Any]:
        """The backup of one device of a run ("Back up now" for several devices,
        schedule), which never fails as a whole: {"id", "device", "result", "severity",
        "detail", "size", "parts", "last"} with result RESULT_*; severity is the one
        of the device in the log entry of the run, detail the error of a backup that
        was not stored, last the time of the newest backup the device keeps."""
        dev = self.devices.get(device_id) or {}
        result: dict[str, Any] = {"id": device_id, "device": str(dev.get("name") or "?"), "severity": "success"}
        try:
            if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
                raise ShellyError("Installation in progress")
            if device_id in self.restoring:
                raise ShellyError("A backup is being restored")
            if dev.get("removed"):
                raise ShellyError(REMOVED_TEXT)
            if not dev.get("origin") or not dev.get("gen"):
                raise ShellyError("Device has not been checked successfully yet")
            entry = await self._backup(device_id, dev, reason, note, user, only_changed, log_entry)
            if entry is None:
                result["result"] = RESULT_UNCHANGED
            else:
                result.update(result=RESULT_STORED, size=entry["size"], parts=entry["parts"])
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            if unreachable(err):
                result["result"] = RESULT_ASLEEP if dev.get("battery") is True else RESULT_UNREACHABLE
            else:
                result["result"] = RESULT_REFUSED
            result.update(severity=_failure_severity(err), detail=_failure_text(err))
            newest = next(iter(self.backups.of(dev.get("mac"))), None)
            if newest is not None:
                result["last"] = newest["at"]
        return result

    def reserve_backup_run(self, devices: object) -> list[str]:
        """"Back up now" for several devices: reserve the run synchronously, before
        its background task starts. devices: "all" (every listed Shelly that is not
        removed) or a list of device IDs. Returns the IDs; ValueError for an invalid
        selection, ShellyError while something runs that changes devices."""
        if self.backup_job is not None and not self.backup_job["finished"]:
            raise ShellyError("A backup run is in progress – please wait")
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        if self.schedule_running is not None:
            raise ShellyError(f"Schedule “{self.schedule_running}” is running – please try again afterwards")
        if self.restoring:
            raise ShellyError("A backup is being restored – please try again afterwards")
        if devices == "all":
            ids = [i for i, d in self.devices.items() if not d.get("removed")]
        elif (
            isinstance(devices, list)
            and 0 < len(devices) <= timetable.MAX_DEVICES
            and all(isinstance(i, str) and i in self.devices for i in devices)
            and len(set(devices)) == len(devices)
        ):
            ids = list(devices)
        else:
            raise ValueError('devices: "all" or a list of device IDs expected')
        if not ids:
            raise ShellyError("No device to back up")
        self.backup_job = {"total": len(ids), "done": 0, "finished": False, "results": []}
        return ids

    async def run_backups(self, ids: list[str], user: str = "unknown") -> None:
        """Back up the devices of a reserved run, BACKUP_PARALLEL at a time; the
        result per device is collected in backup_job for the dialog. The run writes
        one log entry that names every device – also when the app stops during it."""
        job = self.backup_job
        if job is None:
            return
        slots = asyncio.Semaphore(BACKUP_PARALLEL)
        started = time.monotonic()
        interrupted = False

        async def one(device_id: str) -> None:
            """One device of the run."""
            async with slots:
                result = await self._backup_result(device_id, "manual", None, user, log_entry=False)
            job["results"].append(result)
            job["done"] += 1

        try:
            await asyncio.gather(*(one(device_id) for device_id in ids))
        except asyncio.CancelledError:
            interrupted = True
            raise
        finally:
            job["finished"] = True
            self._log_backup_run(job["results"], len(ids), user, time.monotonic() - started, interrupted)

    def _log_backup_run(
        self, results: list[dict[str, Any]], chosen: int, user: str, duration: float, interrupted: bool
    ) -> None:
        """The log entry of a run of "Back up now" over several devices: the counts
        and one finding per device ({"device", "result", "severity", "detail", "size",
        "parts"}), sorted by name. Its severity is the highest of its devices; a run
        the app stopped is at least a warning."""
        stored = sum(1 for r in results if r["result"] == RESULT_STORED)
        asleep = sum(1 for r in results if r["result"] == RESULT_ASLEEP)
        failed = len(results) - stored - asleep
        parts = [f"{stored} stored"]
        if asleep:
            parts.append(f"{asleep} asleep")
        if failed:
            parts.append(f"{failed} not backed up")
        if interrupted:
            parts.append("interrupted – the app stopped")
        text = " · ".join(parts)
        fields = ("device", "result", "severity", "detail", "size", "parts")
        findings = [{f: r[f] for f in fields if r.get(f) is not None} for r in results]
        findings.sort(key=lambda f: str(f["device"]).casefold())
        self.events.add(
            "backup",
            severity=worst("warning" if interrupted else "success", *(str(r["severity"]) for r in results)),
            event="run",
            devices=chosen,
            stored=stored,
            duration=round(duration),
            result=text,
            findings=findings or None,
            interrupted=True if interrupted else None,
            user=user,
        )
        log.info("Backup run of %d device(s) by %s: %s", chosen, user, text)

    def clear_backup_run(self) -> None:
        """The dialog showed the result of the finished run: forget it."""
        if self.backup_job is not None and self.backup_job["finished"]:
            self.backup_job = None

    # --------------------------------------------------------------------------------------------- Views

    @staticmethod
    def _entry_view(entry: dict[str, Any]) -> dict[str, Any]:
        """One backup for the UI (no digest, no MAC address)."""
        return {key: entry.get(key) for key in ENTRY_VIEW}

    def _device_by_mac(self, mac: object) -> tuple[str, Device] | None:
        """The listed device with this MAC address."""
        wanted = normalize_mac(mac)
        if wanted is None:
            return None
        return next(((i, d) for i, d in self.devices.items() if normalize_mac(d.get("mac")) == wanted), None)

    def _no_backup(self, device_id: str, dev: Device) -> dict[str, Any]:
        """Why a device has no backup: {"reason"} (NONE_*, None: none was made yet)
        plus the time and message of a failed attempt."""
        if dev.get("removed"):
            return {"reason": NONE_REMOVED}
        if dev.get("auth_enabled") is True and not self.credentials.has_device_password(device_id):
            return {"reason": NONE_PASSWORD}
        failed = dev.get("backup_failed")
        if isinstance(failed, dict):
            return {"reason": failed.get("reason"), "at": failed.get("at"), "message": failed.get("message")}
        return {"reason": None}

    def backup_counts(self) -> dict[str, dict[str, Any]]:
        """Per MAC address: {"count", "kept", "last", "reason", "size"} of its stored
        backups (last, reason: time and reason of the newest one)."""
        counts: dict[str, dict[str, Any]] = {}
        for entry in self.backups.entries:  # newest first
            row = counts.setdefault(
                entry["mac"], {"count": 0, "kept": 0, "last": entry["at"], "reason": entry["reason"], "size": 0}
            )
            row["count"] += 1
            row["kept"] += 1 if entry["kept"] else 0
            row["size"] += entry["size"]
        return counts

    def backups_view(self) -> dict[str, Any]:
        """Everything the Backups tab and the "Backups" dialog of a device show: every
        listed device with its backups (newest first) or the reason why it has none,
        then the backups of devices that are no longer listed (id None)."""
        by_mac: dict[str, list[dict[str, Any]]] = {}
        for entry in self.backups.entries:
            by_mac.setdefault(entry["mac"], []).append(self._entry_view(entry))
        devices: list[dict[str, Any]] = []
        for device_id, dev in self.devices.items():
            listed = by_mac.pop(normalize_mac(dev.get("mac")) or "", [])
            devices.append(
                {
                    "id": device_id,
                    "name": dev.get("name"),
                    "shelly_name": dev.get("shelly_name"),
                    "model": dev.get("model"),
                    "gen": dev.get("gen"),
                    "version": dev.get("version"),
                    "removed": bool(dev.get("removed")),
                    "battery": dev.get("battery") is True,
                    "ignored": bool(dev.get("ignored")),
                    "backups": listed,
                    "none": None if listed else self._no_backup(device_id, dev),
                }
            )
        for mac, listed in by_mac.items():
            newest = self.backups.get(listed[0]["id"]) or {}
            devices.append(
                {
                    "id": None,
                    "mac": mac,
                    "name": newest.get("device") or mac,
                    "model": newest.get("model"),
                    "gen": newest.get("gen"),
                    "backups": listed,
                    "none": None,
                }
            )
        return {
            "rev": self.backups.rev,
            "keep": self.backup_keep,
            "totals": self.backups.totals(),
            "warning": self.backups.warning,
            "devices": devices,
        }

    # ------------------------------------------------------------------------------- Keeping and deleting

    def _stored(self, backup_id: object) -> dict[str, Any]:
        """Index entry of a stored backup; KeyError for an unknown ID."""
        entry = self.backups.get(backup_id) if isinstance(backup_id, str) else None
        if entry is None:
            raise KeyError(backup_id)
        return entry

    def _in_restore(self, backup_id: str) -> bool:
        """True while exactly this backup is being restored."""
        job = self.restore_job
        return job is not None and not job["finished"] and job["backup"] == backup_id

    def keep_backup(self, backup_id: str, kept: object) -> dict[str, Any]:
        """Mark a backup as kept (never removed automatically, not counted by the
        retention) or take the mark off; without the mark the retention applies again
        at the next backup of the device."""
        self._stored(backup_id)
        if not isinstance(kept, bool):
            raise ValueError("kept: true or false expected")
        return self._entry_view(self.backups.set_kept(backup_id, kept))

    def delete_backup(self, backup_id: str, confirm: object, user: str = "unknown") -> None:
        """Delete one backup (only with confirm). Logged."""
        entry = self._stored(backup_id)
        if confirm is not True:
            raise ValueError("Deletion not confirmed")
        if self._in_restore(backup_id):
            raise ShellyError("This backup is being restored – please try again afterwards")
        self.backups.delete(backup_id)
        known = self._device_by_mac(entry["mac"])
        name = known[1].get("name") if known else entry.get("device")
        log.info("%s: backup of %s deleted by %s", name, timetable.local_text(entry["at"], self.tz), user)
        self.events.add(
            "backup", event="deleted", device=name, backup_at=entry["at"], reason=entry["reason"], user=user
        )

    # ------------------------------------------------------------------------------------------ Restoring

    def _restore_subject(self, backup_id: str) -> tuple[dict[str, Any], str, Device]:
        """(entry, device ID, device) of a backup that could be restored now: its
        device is listed and nothing else works on it (see _settings_device)."""
        entry = self._stored(backup_id)
        known = self._device_by_mac(entry["mac"])
        if known is None:
            raise ShellyError("The device of this backup is no longer listed – it can only be downloaded or deleted")
        device_id, _dev = known
        if self.checking:
            raise ShellyError("Check in progress – please try again afterwards")
        return entry, device_id, self._settings_device(device_id)

    @staticmethod
    def _other_device(entry: dict[str, Any], mac: object, model: object) -> dict[str, Any] | None:
        """None if a backup belongs to the device with this MAC address and model;
        else both identities (a backup is only ever restored to its own device)."""
        same_model = not entry.get("model") or not model or entry["model"] == model
        if entry["mac"] == normalize_mac(mac) and same_model:
            return None
        return {
            "backup_mac": entry["mac"],
            "backup_model": entry.get("model"),
            "device_mac": normalize_mac(mac),
            "device_model": model,
        }

    async def preview_restore(self, backup_id: str) -> dict[str, Any]:
        """Step 1 of a restore: read the device and compare it with the backup.
        Nothing is changed. Returns {"backup", "device", "reachable", "error",
        "other", "fw_differs", "protection", "parts"}: other = both identities if the
        device at the address is not the one of the backup (then nothing else is
        compared); parts = [{"part", "rows", "skipped"}] in the order they are
        written (the network settings last); protection = "same" or "differs" (the
        password protection is never restored), None if unknown."""
        entry, device_id, dev = self._restore_subject(backup_id)
        answer: dict[str, Any] = {
            "backup": self._entry_view(entry),
            "device": {"id": device_id, "name": dev.get("name"), "model": dev.get("model"), "gen": dev.get("gen")},
            "reachable": True,
        }
        try:
            info = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
            answer["device"]["version"] = info.get("version")
            answer["other"] = self._other_device(entry, info.get("mac"), info.get("model"))
            if answer["other"] is not None or int(info["gen"]) != entry["gen"]:
                answer["other"] = answer["other"] or self._other_device({**entry, "mac": ""}, info.get("mac"), None)
                return answer
            _meta, data = self.backups.load(backup_id)
            target = shelly_backup.Target(self.session, entry["gen"], dev["origin"], self._auth(device_id, dev))
            current = await shelly_backup.read(target)
        except (TimeoutError, aiohttp.ClientError, OSError, KeyError) as err:
            answer.update(reachable=False, error=_failure_text(err))
            return answer
        diff = shelly_backup.compare(entry["gen"], data, current)
        order = shelly_backup.GEN2_PARTS if entry["gen"] >= 2 else shelly_backup.GEN1_PARTS
        answer["fw_differs"] = bool(entry.get("fw")) and entry["fw"] != info.get("version")
        stored, now = data.get("protected"), info.get("auth_enabled")
        if isinstance(stored, bool) and isinstance(now, bool):
            answer["protection"] = "same" if stored == now else "differs"
        answer["parts"] = [
            {"part": part, "rows": shelly_backup.public_rows(diff[part]["rows"]), "skipped": diff[part]["skipped"]}
            for part in order
        ]
        return answer

    def reserve_restore(self, backup_id: str, parts: object, network_acknowledged: object, user: str) -> None:
        """Step 2 of a restore: check the request and reserve the restore
        synchronously, before its background task starts. parts: the parts to restore
        (shelly_backup.GEN2_PARTS / GEN1_PARTS); "network" only together with
        network_acknowledged True – the user stated to know that the device can become
        unreachable. ValueError for an invalid request, ShellyError if it cannot run now."""
        entry, device_id, dev = self._restore_subject(backup_id)
        allowed = shelly_backup.GEN2_PARTS if entry["gen"] >= 2 else shelly_backup.GEN1_PARTS
        if (
            not isinstance(parts, list)
            or not parts
            or len(set(parts)) != len(parts)
            or any(part not in allowed for part in parts)
        ):
            raise ValueError("parts: the parts to restore expected")
        if shelly_backup.PART_NETWORK in parts and network_acknowledged is not True:
            raise ValueError("Restoring the network settings is not confirmed")
        if self.restoring:
            raise ShellyError("Another backup is being restored – please wait")
        if self.schedule_running is not None:
            raise ShellyError(f"Schedule “{self.schedule_running}” is running – please try again afterwards")
        if self.backup_job is not None and not self.backup_job["finished"]:
            raise ShellyError("A backup run is in progress – please try again afterwards")
        chosen = [part for part in allowed if part in parts]
        self.restoring.add(device_id)
        self.restore_job = {
            "device": device_id,
            "name": dev.get("name"),
            "backup": backup_id,
            "backup_at": entry["at"],
            "parts": chosen,
            # What the progress view lists; a restart is added when the device asks for one
            "steps": ["backup", *chosen, shelly_backup.STEP_VERIFY],
            "step": "backup",
            "finished": False,
            "result": None,
            "error": None,
            "safety": None,  # time of the backup "Before restore"
            "user": user,
        }

    async def run_restore(self) -> None:
        """Carry out the reserved restore: identity of the device, backup "Before
        restore", writing the chosen parts, reading back (see shelly_backup.restore).
        The outcome is kept in restore_job for the dialog and written to the log."""
        job = self.restore_job
        if job is None or job["finished"]:
            return
        device_id, user = job["device"], job["user"]
        dev = self.devices.get(device_id) or {}
        entry = self.backups.get(job["backup"]) or {}

        def step(name: str) -> None:
            """Progress for the dialog."""
            if name not in job["steps"]:
                job["steps"].insert(len(job["steps"]) - 1, name)
            job["step"] = name

        try:
            info = await shelly.device_info(self.session, dev["origin"], self._auth_for(device_id))
            if self._other_device(entry, info.get("mac"), info.get("model")) or int(info["gen"]) != entry["gen"]:
                raise ShellyError("Another device answers at the address – nothing was restored")
            _meta, data = self.backups.load(job["backup"])
            try:
                safety = await self._backup(device_id, dev, "restore", user=user)
            except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
                raise ShellyError(
                    f"The backup before the restore failed – nothing was restored: {_failure_text(err)}"
                ) from None
            job["safety"] = safety["at"] if safety else None
            result = await shelly_backup.restore(self._target(device_id, dev), data, job["parts"], step)
            job["result"] = result
            self._log_restore(dev, entry, job, result)
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError, KeyError, ValueError) as err:
            self._restore_failed(dev, entry, job, _failure_text(err))
        except Exception as err:
            # An error no code path expects must not leave the device "restoring"
            log.exception("%s: unexpected error while restoring a backup", dev.get("name"))
            self._restore_failed(dev, entry, job, unexpected_text(err))
        finally:
            self.restoring.discard(device_id)
            job["finished"] = True
            if job["result"] is not None and job["result"]["confirmed"]:
                await self._read_settings(device_id, dev, extended=True)  # the app shows what the device holds now
            self.save()

    def _restore_failed(self, dev: Device, entry: dict[str, Any], job: dict[str, Any], text: str) -> None:
        """A restore that ended with an error before its result."""
        job["error"] = text
        log.warning("%s: restoring the backup failed: %s", dev.get("name"), text)
        self.events.add(
            "backup", ok=False, event="restore_failed", device=dev.get("name"), backup_at=entry.get("at"),
            restored=job["parts"], error=clip(text, 300), user=job["user"],
        )  # fmt: skip

    def _log_restore(self, dev: Device, entry: dict[str, Any], job: dict[str, Any], result: dict[str, Any]) -> None:
        """Log entry of a finished restore: the parts, the number of changes and what
        was not applied (keys of the settings only, never their values)."""
        changes = sum(part["total"] for part in result["parts"])
        findings = [
            {"part": part["part"], "key": clip(row["key"], 120), "error": clip(row.get("error"), 200) or None}
            for part in result["parts"]
            for row in part["open"]
        ]
        if not result["confirmed"]:
            severity = "error"
        elif findings or result["restart"] is False:
            severity = "warning"
        else:
            severity = "success"
        log.info(
            "%s: backup of %s restored by %s (%s; %d changes, %d not applied)",
            dev.get("name"), timetable.local_text(entry["at"], self.tz), job["user"], ", ".join(job["parts"]),
            changes, len(findings),
        )  # fmt: skip
        self.events.add(
            "backup", severity=severity, event="restored", device=dev.get("name"), backup_at=entry["at"],
            restored=job["parts"], changes=changes, not_applied=len(findings), findings=findings[:100] or None,
            confirmed=result["confirmed"], restart=result["restart"], error=result["lost"], user=job["user"],
        )  # fmt: skip

    def clear_restore(self) -> None:
        """The dialog showed the result of the finished restore: forget it."""
        if self.restore_job is not None and self.restore_job["finished"]:
            self.restore_job = None

    def restore_view(self) -> dict[str, Any] | None:
        """The running or last restore for the dialog (no user name)."""
        job = self.restore_job
        return None if job is None else {key: value for key, value in job.items() if key != "user"}

    # -------------------------------------------------------------------------------- Download and import

    async def prepare_backup_download(self, backup_id: str, user: str = "unknown") -> dict[str, Any]:
        """Step 1 of a download: generate the password of the file and derive its key
        (in a worker thread: scrypt takes a moment). The password is returned once and
        kept nowhere; the file encrypted with it waits in memory for the download
        (DOWNLOAD_TTL_S, single use). Returns {"token", "password", "filename",
        "size", "expires_in"}."""
        entry = self._stored(backup_id)
        password = store_transfer.generate_password()
        salt = secrets.token_bytes(backup_store.SALT_BYTES)
        key = await asyncio.to_thread(backup_store.derive_key, password, salt)
        try:
            blob = self.backups.sealed_copy(backup_id, key, salt)
        except BackupError as err:
            raise ShellyError(str(err)) from None
        known = self._device_by_mac(entry["mac"])
        day = timetable.local_text(entry["at"], self.tz)[:10]
        filename = _file_name(known[1] if known else {"name": entry.get("device")}, entry, day)
        self._backup_download = {
            "token": secrets.token_hex(16),
            "blob": blob,
            "filename": filename,
            "created": time.monotonic(),
            "device": known[1].get("name") if known else entry.get("device"),
            "backup_at": entry["at"],
            "user": user,
        }
        return {
            "token": self._backup_download["token"],
            "password": password,
            "filename": filename,
            "size": len(blob),
            "expires_in": DOWNLOAD_TTL_S,
        }

    def take_backup_download(self, token: object) -> tuple[bytes, str]:
        """Step 2: (content, file name) of the download prepared with token (single
        use). Logged."""
        pending, self._backup_download = self._backup_download, None
        if (
            not pending
            or time.monotonic() - pending["created"] > DOWNLOAD_TTL_S
            or not shelly.same_secret(token, pending["token"])
        ):
            raise ShellyError("Download expired – please start it again")
        log.info("%s: backup downloaded by %s", pending["device"], pending["user"])
        self.events.add(
            "backup", event="downloaded", device=pending["device"], backup_at=pending["backup_at"],
            user=pending["user"],
        )  # fmt: skip
        return pending["blob"], pending["filename"]

    async def stage_backup_import(
        self, device_id: str, chunks: AsyncIterator[bytes], length: int | None
    ) -> dict[str, Any]:
        """Step 1 of an import for a device: take the file (at most
        backup_store.MAX_FILE) into memory and look at its header. Nothing is stored;
        a file staged before is dropped. Returns {"token", "size"}."""
        if device_id not in self.devices:
            raise KeyError(device_id)
        too_large = "The file is too large for a backup"
        if length is not None and length > backup_store.MAX_FILE:
            raise ValueError(too_large)
        self._backup_import = None
        parts: list[bytes] = []
        size = 0
        async for chunk in chunks:
            size += len(chunk)
            if size > backup_store.MAX_FILE:
                raise ValueError(too_large)
            parts.append(chunk)
        blob = b"".join(parts)
        header, _offset = backup_store.read_header(blob)
        backup_store.password_salt(header)  # a file without a password seal is refused here already
        self._backup_import = {
            "token": secrets.token_hex(16),
            "device": device_id,
            "blob": blob,
            "created": time.monotonic(),
            "checked": None,
        }
        return {"token": self._backup_import["token"], "size": len(blob)}

    def _staged_backup(self, token: object) -> dict[str, Any]:
        """The staged backup file with this token; ShellyError if it expired."""
        staged = self._backup_import
        if (
            not staged
            or time.monotonic() - staged["created"] > IMPORT_TTL_S
            or not shelly.same_secret(token, staged["token"])
        ):
            self._backup_import = None
            raise ShellyError("The file is no longer available – please choose it again")
        return staged

    async def check_backup_import(self, token: object, password: object) -> dict[str, Any]:
        """Step 2: open the staged file with its password and check it: the seal
        (password and unchanged content), the device (MAC address and model of the
        device the import was started for, as known from its last check – nothing is
        requested from the device) and whether the backup is stored already. Returns
        {"backup", "other", "known"}; the import is possible if other is None and known
        is False."""
        staged = self._staged_backup(token)
        text = store_transfer.normalize_password(password)
        header, _offset = backup_store.read_header(staged["blob"])
        key = await asyncio.to_thread(backup_store.derive_key, text, backup_store.password_salt(header))
        meta, data = backup_store.open_sealed(key, staged["blob"], WRONG_FILE_PASSWORD)
        clean = backup_store.clean_meta(meta)
        dev = self.devices.get(staged["device"]) or {}
        other = self._other_device(clean, dev.get("mac"), dev.get("model"))
        known = self.backups.known(clean)
        staged["checked"] = None if other or known else (clean, data)
        staged["created"] = time.monotonic()  # the confirmation gets the full time
        return {
            # As it is listed after the import: with both marks
            "backup": {**self._entry_view(clean), "size": len(staged["blob"]), "kept": True, "imported": True},
            "other": other,
            "known": known,
        }

    def confirm_backup_import(self, token: object, user: str = "unknown") -> dict[str, Any]:
        """Step 3: store the checked file as a backup of its device, encrypted with
        the key of this app and marked as imported and kept (the retention does not
        remove it). Nothing is changed on the device. Logged."""
        staged = self._staged_backup(token)
        if staged["checked"] is None:
            raise ShellyError("The file has not been checked, or it cannot be imported")
        clean, data = staged["checked"]
        self._backup_import = None
        entry = self.backups.add(clean, data, kept=True, imported=True)
        name = (self.devices.get(staged["device"]) or {}).get("name")
        log.info("%s: backup of %s imported by %s", name, timetable.local_text(entry["at"], self.tz), user)
        self.events.add(
            "backup", event="imported", device=name, backup_at=entry["at"], size=entry["size"], parts=entry["parts"],
            user=user,
        )  # fmt: skip
        return self._entry_view(entry)

    def cancel_backup_import(self, token: object) -> None:
        """Discard the staged backup file (Cancel in the import dialog)."""
        staged = self._backup_import
        if staged and shelly.same_secret(token, staged["token"]):
            self._backup_import = None
