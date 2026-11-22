"""Manager, layer 6 of 7: the Schedules tab – schedules (restart, cloud and Bluetooth
on/off at a clock time) and scheduled installations ("Install at" of the installation
dialog).

Both are started by the loop run_timetable in main.py (start_due). Rules and the next
run of a schedule: timetable.py.

Scheduled installation: the approval of the installation dialog is stored on the
device ("planned") and carried out once at its time. It installs only the exact file
(SHA-256) that was approved, on a device that still runs the version shown in the
dialog; otherwise the plan lapses (log warning, Home Assistant notification). A plan
whose time the app missed by more than MISSED_AFTER_S lapses as well (log only). The
result of a scheduled installation is also sent as Home Assistant notification.

Schedule run: one action on the chosen devices; runs one at a time. While a check, an
installation, Reload Device Info or a store import runs, the run waits up to
RUN_WAIT_S, then it counts as missed. Left out (and named in the log): removed,
ignored and battery devices, devices that are installing, being checked or were
never checked, devices without the switch (Bluetooth: Gen1 too). One log entry per
run; a Home Assistant notification once per run if a device was not reached or
refused the action. A missed run is only logged.
"""

import asyncio
import logging
import time
from typing import Any

import aiohttp
import timetable
from shelly import UNSUPPORTED, ShellyError, error_text, plain

from manager_core import (
    PENDING, Device, STATUS_INSTALLING, STATUS_SKIPPED, markdown_text,
)
from manager_installs import Installs

log = logging.getLogger("sfm.manager")

MISSED_AFTER_S = 300  # a job started more than 5 min after its time counts as missed
RUN_WAIT_S = 1800  # a schedule run waits at most 30 min for a check or installation
RUN_WAIT_POLL_S = 10  # and looks again every 10 s
NOTIFY_TITLE_PLAN = "Shelly firmware: scheduled installation"
SCHEDULE_USER = "Schedule"  # user of a run started by its time (log)


def _clock(ts: float | int | None, tz: Any) -> str | None:
    """Local "YYYY-MM-DD HH:MM" of an epoch, None for none."""
    return timetable.local_text(ts, tz) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None


class Schedules(Installs):
    """Scheduled installations and the schedules of the Schedules tab."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """See ManagerCore; adds the running jobs of the timetable (memory only)."""
        super().__init__(*args, **kwargs)
        self._timetable_tasks: set[asyncio.Task[Any]] = set()
        self._running_id: str | None = None  # ID of the schedule whose run is going on

    # ------------------------------------------------------------------------ Start of the app

    async def start_timetable(self) -> None:
        """At the start: a beta version provided while beta firmware is switched off now
        is withdrawn (the device shows "Not checked" until its next check; a scheduled
        installation of it lapses). Also completes the timetable fields of every
        schedule (next run)."""
        if not self.beta_firmware:
            for device_id, dev in self.devices.items():
                if dev.get("beta") and (dev.get("status") in PENDING or dev.get("file")):
                    await self._withdraw_beta(device_id, dev)
        now = time.time()
        for item in self.schedules:
            if item.get("on") and not isinstance(item.get("next"), (int, float)):
                item["next"] = timetable.next_run(item, now, self.tz)
        self.save()

    async def _withdraw_beta(self, device_id: str, dev: Device) -> None:
        """Withdraw the provided beta version of a device (option switched off)."""
        version = dev.get("pending_version") or dev.get("offer_version")
        plan = dev.pop("planned", None)
        dev.update(
            status=STATUS_SKIPPED,
            message="Beta version withdrawn – beta firmware is switched off in the settings",
            offer_version=None,
            offer_source=None,
            file=None,
            sha256=None,
            size=None,
            source_url=None,
        )
        for key in ("beta", "install_failed", "updated", "hold", "offer_date", "step_target"):
            dev.pop(key, None)
        self.events.add(
            "device", severity="warning", action="beta_withdrawn", device=dev.get("name"), version=version
        )
        if plan:
            await self._plan_lapsed(device_id, dev, plan, "beta firmware is switched off in the settings")
        self.cleanup_firmware()

    # ----------------------------------------------------------------- Timetable loop (main.py)

    def due_in(self, now: float) -> float:
        """Seconds until the next scheduled installation or schedule run (inf: none)."""
        times = [float(d["planned"]["at"]) for d in self.devices.values() if d.get("planned")]
        times += [float(s["next"]) for s in self.schedules if s.get("on") and isinstance(s.get("next"), (int, float))]
        return max(0.0, min(times) - now) if times else float("inf")

    def start_due(self, now: float) -> None:
        """Start every job whose time has come, each as its own task: scheduled
        installations first (a schedule run due at the same time then waits for them),
        then the schedules. A job is taken off synchronously, so it starts only once."""
        for device_id, dev in list(self.devices.items()):
            plan = dev.get("planned")
            if plan and float(plan["at"]) <= now:
                dev.pop("planned")
                self.save()
                self._spawn(self._run_plan(device_id, plan, now))
        for item in self.schedules:
            planned = item.get("next")
            if not item.get("on") or not isinstance(planned, (int, float)) or planned > now:
                continue
            item["next"] = timetable.next_run(item, max(now, float(planned)), self.tz)
            self.save()
            self._spawn(self._due_schedule(str(item["id"]), float(planned), now))

    def _spawn(self, coro: Any) -> None:
        """Run a job of the timetable as task (kept until it ends, cancelled by stop_timetable)."""
        task = asyncio.create_task(coro)
        self._timetable_tasks.add(task)
        task.add_done_callback(self._timetable_tasks.discard)

    async def stop_timetable(self) -> None:
        """Cancel running jobs of the timetable (app stop)."""
        tasks = list(self._timetable_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---------------------------------------------------------------- Scheduled installations

    def plan_install(
        self, device_id: str, sha: str, at_text: object, user: str, restart_first: bool = False
    ) -> float:
        """Schedule the approved installation of the provided update for a time
        "YYYY-MM-DDTHH:MM" (local time of Home Assistant, at most 7 days ahead).
        Replaces an earlier plan of the device. Returns the epoch of the time; raises
        ValueError (time) or ShellyError (approval no longer valid)."""
        at = timetable.install_time(at_text, time.time(), self.tz)
        if not self.approval_matches(device_id, sha):
            raise ShellyError("Approval does not match the provided update (anymore)")
        dev = self.devices[device_id]
        dev["planned"] = {
            "at": int(at),
            "by": user,
            "on": int(time.time()),
            "sha256": sha,
            "version": dev.get("version"),
            "target": dev.get("pending_version"),
            "beta": bool(dev.get("beta")),
            "restart": bool(restart_first),
        }
        self.events.add(
            "install",
            event="scheduled",
            device=dev.get("name"),
            type=dev.get("type"),
            old=dev.get("version"),
            new=dev.get("pending_version"),
            user=user,
            at=timetable.local_text(at, self.tz),
            beta=True if dev.get("beta") else None,
        )
        log.info("%s: installation of %s scheduled for %s by %s", dev.get("name"), dev.get("pending_version"),
                 timetable.local_text(at, self.tz), user)
        self.save()
        self.timetable_changed.set()
        return at

    def cancel_plan(self, device_id: str, user: str, reason: str = "cancelled") -> bool:
        """Cancel the scheduled installation of a device (reason: "cancelled" or
        "installed_now"). False if there is none."""
        dev = self.devices.get(device_id) or {}
        plan = dev.pop("planned", None)
        if not plan:
            return False
        self.events.add(
            "install",
            event="plan_cancelled",
            device=dev.get("name"),
            type=dev.get("type"),
            old=plan.get("version"),
            new=plan.get("target"),
            user=user,
            at=_clock(plan.get("at"), self.tz),
            detail="Installed now instead" if reason == "installed_now" else None,
        )
        self.save()
        self.timetable_changed.set()
        return True

    def _plan_blocker(self, device_id: str, dev: Device, plan: dict[str, Any]) -> str | None:
        """Why a scheduled installation cannot start now (None: it can)."""
        if self.checking:
            return "a full check was running"
        if self.schedule_running is not None:
            return f"the schedule “{self.schedule_running}” was running"
        if self.download_base_issue:
            return "installations are disabled (download base URL)"
        if device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
            return "the device was already installing"
        if device_id in self.device_checks or device_id in self.reloading:
            return "the device was being checked"
        if dev.get("version") != plan.get("version"):
            return f"the device runs {plain(dev.get('version'), 64) or 'another version'} now"
        if plan.get("beta") and not self.beta_firmware:
            return "beta firmware is switched off in the settings"
        if not self.approval_matches(device_id, str(plan.get("sha256"))):
            return "the approved file is no longer provided"
        return None

    async def _run_plan(self, device_id: str, plan: dict[str, Any], now: float) -> None:
        """Carry out a scheduled installation (already taken off the device)."""
        dev = self.devices.get(device_id)
        if dev is None:
            return  # device removed from the list meanwhile
        if now - float(plan["at"]) > MISSED_AFTER_S:
            await self._plan_lapsed(device_id, dev, plan, "the app was not running at that time", notify=False)
            return
        blocker = self._plan_blocker(device_id, dev, plan)
        if blocker:
            await self._plan_lapsed(device_id, dev, plan, blocker)
            return
        restart_first = bool(plan.get("restart")) and bool(dev.get("install_failed"))
        ok = await self.install(
            device_id, str(plan["sha256"]), approved_by=f"{plan.get('by')} (scheduled)", restart_first=restart_first
        )
        name = markdown_text(dev.get("name"), 120)
        if ok:
            text = f"{name}: {markdown_text(plan.get('target'))} installed as scheduled."
        else:
            text = (
                f"{name}: the scheduled installation of {markdown_text(plan.get('target'))} failed – "
                f"{markdown_text(dev.get('message'), 300)}"
            )
        await self.notify(NOTIFY_TITLE_PLAN, text, f"sfm_planned_{device_id}")

    async def _plan_lapsed(
        self, device_id: str, dev: Device, plan: dict[str, Any], reason: str, notify: bool = True
    ) -> None:
        """A scheduled installation that is not carried out: log warning and (unless
        missed) a Home Assistant notification. The device keeps its provided file."""
        at = _clock(plan.get("at"), self.tz)
        missed = not notify
        self.events.add(
            "install",
            severity="warning",
            event="plan_missed" if missed else "plan_lapsed",
            device=dev.get("name"),
            type=dev.get("type"),
            old=plan.get("version"),
            new=plan.get("target"),
            user=plan.get("by"),
            at=at,
            detail=reason,
        )
        log.warning("%s: scheduled installation (%s) not started: %s", dev.get("name"), at, reason)
        self.save()
        if notify:
            await self.notify(
                NOTIFY_TITLE_PLAN,
                f"{markdown_text(dev.get('name'), 120)}: the installation of {markdown_text(plan.get('target'))} "
                f"scheduled for {markdown_text(at)} was not started – {markdown_text(reason, 300)}.",
                f"sfm_planned_{device_id}",
            )

    def _review_plans(self) -> None:
        """After a check: a plan whose device now runs another version or no longer
        holds the approved file lapses at once (logged; notified at its time would be
        too late to act on)."""
        for device_id, dev in self.devices.items():
            plan = dev.get("planned")
            if not plan:
                continue
            reason = None
            if dev.get("version") != plan.get("version"):
                reason = f"the device runs {plain(dev.get('version'), 64) or 'another version'} now"
            elif not self.approval_matches(device_id, str(plan.get("sha256"))):
                reason = "the approved file is no longer provided"
            if reason:
                dev.pop("planned")
                self._spawn(self._plan_lapsed(device_id, dev, plan, reason))
                self.timetable_changed.set()

    async def scan(self, automatic: bool = False, user: str | None = None) -> bool:
        """Full check (see Checks.scan); scheduled installations are reviewed afterwards."""
        try:
            return await super().scan(automatic=automatic, user=user)
        finally:
            self._review_plans()

    async def check_device(self, device_id: str, user: str | None = None, trigger: str = "device") -> dict[str, Any]:
        """Single-device check (see Checks.check_device); its plan is reviewed afterwards."""
        try:
            return await super().check_device(device_id, user=user, trigger=trigger)
        finally:
            self._review_plans()

    # ---------------------------------------------------------------------------- Schedules

    def _schedule(self, sid: str) -> dict[str, Any]:
        """The schedule with this ID; KeyError if there is none."""
        for item in self.schedules:
            if item.get("id") == sid:
                return item
        raise KeyError(sid)

    def _names(self) -> dict[str, str]:
        """HA device ID -> device name (log texts)."""
        return {k: str(d.get("name") or "?") for k, d in self.devices.items()}

    def create_schedule(self, data: object, user: str) -> dict[str, Any]:
        """Add a schedule (fields see timetable.validate); logged."""
        if len(self.schedules) >= timetable.MAX_SCHEDULES:
            raise ValueError(f"At most {timetable.MAX_SCHEDULES} schedules")
        clean = timetable.validate(data, set(self.devices))
        now = time.time()
        item: dict[str, Any] = {
            "id": timetable.new_id({str(s.get("id")) for s in self.schedules}),
            **clean,
            "anchor": timetable.anchor_for(clean["time"], now, self.tz) if clean["repeat"]["kind"] == "every" else None,
            "last": None,
        }
        item["next"] = timetable.next_run(item, now, self.tz)
        self.schedules.append(item)
        self.events.add(
            "schedule",
            event="created",
            name=item["name"],
            action=timetable.ACTIONS[item["action"]],
            repeat=timetable.repeat_text(item),
            devices=timetable.devices_text(item["devices"], self._names()),
            user=user,
        )
        self.save()
        self.timetable_changed.set()
        return item

    def update_schedule(self, sid: str, data: object, user: str) -> dict[str, Any]:
        """Change a schedule; the changed fields are logged. A new time or repeat rule
        starts "every n days" anew from the next run."""
        item = self._schedule(sid)
        clean = timetable.validate(data, set(self.devices))
        found = timetable.changes(item, clean, self._names())
        now = time.time()
        timing = item.get("time") != clean["time"] or item.get("repeat") != clean["repeat"]
        if clean["repeat"]["kind"] != "every":
            item["anchor"] = None
        elif timing or not item.get("anchor"):
            item["anchor"] = timetable.anchor_for(clean["time"], now, self.tz)
        item.update(clean)
        item["next"] = timetable.next_run(item, now, self.tz)
        if found:
            self.events.add("schedule", event="changed", name=item["name"], changes=found, user=user)
        self.save()
        self.timetable_changed.set()
        return item

    def delete_schedule(self, sid: str, user: str) -> None:
        """Delete a schedule (a run going on finishes); logged."""
        item = self._schedule(sid)
        self.schedules = [s for s in self.schedules if s is not item]
        self.events.add("schedule", event="deleted", name=item.get("name"), user=user)
        self.save()
        self.timetable_changed.set()

    def _run_blocker(self) -> str | None:
        """What a schedule run waits for (None: it can start now)."""
        if self.schedule_running is not None:
            return f"the schedule “{self.schedule_running}” is running"
        if self.checking:
            return "a full check is running"
        if self.installing:
            return "installations are running"
        if self.device_checks:
            return "single-device checks are running"
        if self.reloading:
            return "device info is being reloaded"
        if self._import_storing:
            return "a store import is running"
        return None

    def reserve_schedule_run(self, sid: str) -> dict[str, Any]:
        """"Run now": reserve the run synchronously (ShellyError if something else runs,
        KeyError for an unknown schedule)."""
        item = self._schedule(sid)
        if self.first_start_open:
            raise ShellyError("Not before the first-start question is answered")
        blocker = self._run_blocker()
        if blocker:
            raise ShellyError(f"Not possible now – {blocker}")
        self.schedule_running = str(item["name"])
        self._running_id = sid
        return item

    async def run_schedule_now(self, sid: str, user: str) -> None:
        """Run a reserved schedule now (see reserve_schedule_run)."""
        try:
            item = self._schedule(sid)
        except KeyError:
            self.schedule_running = self._running_id = None
            return
        await self._run(item, trigger="manual", user=user)

    async def _due_schedule(self, sid: str, planned: float, now: float) -> None:
        """A schedule whose time has come: missed if the app was not running then;
        otherwise it waits up to RUN_WAIT_S for running work, then runs."""
        try:
            item = self._schedule(sid)
        except KeyError:
            return  # deleted meanwhile
        if now - planned > MISSED_AFTER_S:
            self._missed(item, planned, "the app was not running at that time")
            return
        deadline = time.monotonic() + RUN_WAIT_S
        while (blocker := self._run_blocker() or ("first start" if self.first_start_open else None)) is not None:
            if time.monotonic() >= deadline:
                self._missed(item, planned, f"{blocker} for more than {RUN_WAIT_S // 60} minutes")
                return
            await asyncio.sleep(RUN_WAIT_POLL_S)
        if item not in self.schedules or not item.get("on"):
            return  # deleted or switched off while waiting
        self.schedule_running = str(item["name"])
        self._running_id = sid
        await self._run(item, trigger="schedule", user=SCHEDULE_USER)

    def _missed(self, item: dict[str, Any], planned: float, reason: str) -> None:
        """A run the app missed: log warning only, "Missed" in the tab."""
        at = timetable.local_text(planned, self.tz)
        self.events.add(
            "schedule",
            severity="warning",
            event="missed",
            name=item.get("name"),
            action=timetable.ACTIONS.get(str(item.get("action")), "?"),
            at=at,
            detail=reason,
            user=SCHEDULE_USER,
        )
        log.warning("Schedule %s (%s) missed: %s", item.get("name"), at, reason)
        item["last"] = {"at": int(planned), "severity": "missed", "text": f"Missed – {reason}", "reason": reason}
        self.save()

    def _targets(self, item: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
        """Devices of a run and those left out (with the reason)."""
        action = str(item["action"])
        chosen = list(self.devices) if item.get("devices") == "all" else list(item.get("devices") or [])
        setting = timetable.SETTING_ACTIONS.get(action, (None, None))[0]
        targets: list[str] = []
        left: list[dict[str, str]] = []
        for device_id in chosen:
            dev = self.devices.get(device_id)
            reason = None
            if dev is None:
                reason = "no longer listed"
            elif dev.get("removed"):
                reason = "removed from Home Assistant"
            elif dev.get("ignored"):
                reason = "ignored"
            elif dev.get("battery"):
                reason = "battery device"
            elif device_id in self.installing or dev.get("status") == STATUS_INSTALLING:
                reason = "installation running"
            elif device_id in self.device_checks or device_id in self.reloading:
                reason = "being checked"
            elif not dev.get("origin") or not dev.get("gen"):
                reason = "never checked"
            elif setting == "ble" and dev.get("gen") == 1:
                reason = "Gen1 (no Bluetooth switch)"
            elif setting and dev.get(setting) == UNSUPPORTED:
                reason = "no Bluetooth switch" if setting == "ble" else "no cloud switch"
            if reason:
                name = str(dev.get("name") or "?") if dev else "?"
                left.append({"device": name, "reason": reason})
            else:
                targets.append(device_id)
        return targets, left

    async def _act(self, device_id: str, action: str, user: str) -> dict[str, str]:
        """The action of a schedule on one device: {"device", "result", "detail"}
        with result ok / unreachable / refused."""
        dev = self.devices.get(device_id) or {}
        name = str(dev.get("name") or "?")
        try:
            if action == "restart":
                await self.restart_device(device_id)
                return {"device": name, "result": "ok", "detail": "restarted"}
            setting, value = timetable.SETTING_ACTIONS[action]
            _, restart, verified, not_applied = await self.change_settings(
                device_id, {setting: value}, user, log_entry=False
            )
            if not_applied:
                return {"device": name, "result": "refused", "detail": "not applied by the Shelly"}
            detail = "switched on" if value else "switched off"
            if restart:  # the change takes effect only after a restart
                await self.restart_device(device_id)
                detail += ", restarted"
            if not verified:
                detail += ", not read back"
            return {"device": name, "result": "ok", "detail": detail}
        except (TimeoutError, aiohttp.ClientConnectionError, OSError) as err:
            return {"device": name, "result": "unreachable", "detail": error_text(err, 200) or type(err).__name__}
        except (ShellyError, aiohttp.ClientError, ValueError, KeyError) as err:
            return {"device": name, "result": "refused", "detail": error_text(err, 200) or type(err).__name__}

    async def _run(self, item: dict[str, Any], trigger: str, user: str) -> None:
        """One run of a schedule (schedule_running is already set)."""
        started = time.monotonic()
        action = str(item["action"])
        try:
            targets, left = self._targets(item)
            results: list[dict[str, str]] = []
            if action == "restart" and item.get("pause"):
                for index, device_id in enumerate(targets):
                    if index:
                        await asyncio.sleep(int(item["pause"]))
                    results.append(await self._act(device_id, action, user))
            else:
                results = list(await asyncio.gather(*(self._act(d, action, user) for d in targets)))
            self._record_run(item, trigger, user, results, left, time.monotonic() - started)
            failed = [r for r in results if r["result"] != "ok"]
            if failed:
                names = ", ".join(markdown_text(r["device"], 120) for r in failed[:20])
                more = f" and {len(failed) - 20} more" if len(failed) > 20 else ""
                await self.notify(
                    f"Shelly schedule “{plain(item.get('name'), 64)}”",
                    f"{len(failed)} of {len(results)} device(s) not reached or refused the action: {names}{more}.\n\n"
                    "Details in the Logs tab of the “Shelly Firmware” panel.",
                    f"sfm_schedule_{item.get('id')}",
                )
        finally:
            self.schedule_running = self._running_id = None
            self.save()

    def _record_run(
        self,
        item: dict[str, Any],
        trigger: str,
        user: str,
        results: list[dict[str, str]],
        left: list[dict[str, str]],
        duration: float,
    ) -> None:
        """Log entry and "Last run" of a finished run."""
        action = str(item["action"])
        done = sum(1 for r in results if r["result"] == "ok")
        unreachable = sum(1 for r in results if r["result"] == "unreachable")
        refused = sum(1 for r in results if r["result"] == "refused")
        verb = "restarted" if action == "restart" else "switched"
        parts = [f"{done} of {len(results)} {verb}"] if results else ["No device to run on"]
        if unreachable:
            parts.append(f"{unreachable} not reachable")
        if refused:
            parts.append(f"{refused} refused")
        if left:
            parts.append(f"{len(left)} left out")
        text = " · ".join(parts)
        severity = "success" if results and done == len(results) else "warning"
        self.events.add(
            "schedule",
            severity=severity,
            event="run",
            name=item.get("name"),
            action=timetable.ACTIONS.get(action, action),
            trigger=trigger,
            devices=len(results),
            done=done,
            duration=round(duration),
            result=text,
            findings=results or None,
            left_out=left or None,
            user=user,
        )
        log.info("Schedule %s (%s): %s", item.get("name"), trigger, text)
        item["last"] = {
            "at": int(time.time()),
            "severity": severity,
            "text": text,
            "counts": {
                "done": done,
                "total": len(results),
                "unreachable": unreachable,
                "refused": refused,
                "left": len(left),
            },
        }

    # --------------------------------------------------------------------------------- Views

    def planned_view(self, dev: Device) -> dict[str, Any] | None:
        """The scheduled installation of a device for the UI (no SHA-256)."""
        plan = dev.get("planned")
        if not plan:
            return None
        return {
            "at": plan.get("at"),
            "at_text": _clock(plan.get("at"), self.tz),
            "by": plan.get("by"),
            "on": plan.get("on"),
            "on_text": _clock(plan.get("on"), self.tz),
            "version": plan.get("version"),
            "target": plan.get("target"),
            "beta": bool(plan.get("beta")),
        }

    def schedules_view(self) -> list[dict[str, Any]]:
        """Schedules for the Schedules tab."""
        names = self._names()
        views = []
        for item in self.schedules:
            devices = item.get("devices")
            targets, left = self._targets(item)
            last = item.get("last") if isinstance(item.get("last"), dict) else None
            views.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "on": bool(item.get("on")),
                    "action": item.get("action"),
                    "pause": item.get("pause"),
                    "time": item.get("time"),
                    "repeat": item.get("repeat"),
                    "devices": devices,
                    "devices_text": timetable.devices_text(devices, names),
                    "count": len(targets),
                    "left_out": len(left),
                    "next": item.get("next") if item.get("on") else None,
                    "next_text": _clock(item.get("next"), self.tz) if item.get("on") else None,
                    "last": {**last, "at_text": _clock(last.get("at"), self.tz)} if last else None,
                    "running": self._running_id is not None and self._running_id == item.get("id"),
                }
            )
        return views

