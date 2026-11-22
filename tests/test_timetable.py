"""Schedules tab: rules and next runs of schedules (timetable.py), scheduled
installations ("Install at") and schedule runs (manager_schedules), and their API.

All traffic is mocked (conftest.FakeNet)."""

import asyncio
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import manager_core
import manager_schedules
import pytest
import shelly
import timetable
from conftest import A, B, C

BERLIN = ZoneInfo("Europe/Berlin")


def epoch(text):
    """Epoch of a local "YYYY-MM-DD HH:MM" in Berlin."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN).timestamp()


def local(ts):
    return datetime.fromtimestamp(ts, BERLIN).strftime("%Y-%m-%d %H:%M")


def schedule_data(**fields):
    """Valid user input of a schedule."""
    return {"name": "Night restart", "action": "restart", "time": "03:00", "repeat": {"kind": "daily"},
            "devices": "all", "pause": 0, **fields}


# ---------------------------------------------------------------- Rules (pure functions)


def test_validate_normalises_and_refuses():
    """Name required and trimmed, pause only for restart, weekdays sorted, devices must
    be listed devices; readable errors otherwise."""
    clean = timetable.validate(schedule_data(name="  Cloud  ", action="cloud_off", pause=5), {A})
    assert clean["name"] == "Cloud" and clean["pause"] is None and clean["on"] is True
    clean = timetable.validate(schedule_data(repeat={"kind": "weekdays", "days": [4, 0, 4]}, devices=[A, A]), {A})
    assert clean["repeat"] == {"kind": "weekdays", "days": [0, 4]} and clean["devices"] == [A]
    for bad, match in [
        ({"name": " "}, "name"),
        ({"action": "reboot"}, "Action"),
        ({"time": "24:00"}, "Time"),
        ({"pause": 601}, "Pause"),
        ({"pause": True}, "Pause"),
        ({"repeat": {"kind": "weekdays", "days": []}}, "weekday"),
        ({"repeat": {"kind": "every", "n": 0}}, "every"),
        ({"devices": [B]}, "unknown device"),
        ({"devices": []}, "Devices"),
        ({"on": "yes"}, "on"),
    ]:
        with pytest.raises(ValueError, match=match):
            timetable.validate(schedule_data(**bad), {A})


def test_next_run_daily_weekdays_and_off():
    after = epoch("2026-11-23 04:00")  # a Monday
    item = {"on": True, "time": "03:00", "repeat": {"kind": "daily"}}
    assert local(timetable.next_run(item, after, BERLIN)) == "2026-11-24 03:00"
    assert local(timetable.next_run(item, epoch("2026-11-23 02:59"), BERLIN)) == "2026-11-23 03:00"
    item["repeat"] = {"kind": "weekdays", "days": [0]}  # Mondays
    assert local(timetable.next_run(item, after, BERLIN)) == "2026-11-30 03:00"
    item["on"] = False
    assert timetable.next_run(item, after, BERLIN) is None


def test_every_n_days_counts_from_its_anchor():
    """"Every n days" counts from the first run after saving."""
    now = epoch("2026-11-23 04:00")
    anchor = timetable.anchor_for("03:00", now, BERLIN)
    assert anchor == "2026-11-24"  # today's time has passed
    item = {"on": True, "time": "03:00", "repeat": {"kind": "every", "n": 3}, "anchor": anchor}
    first = timetable.next_run(item, now, BERLIN)
    assert local(first) == "2026-11-24 03:00"
    assert local(timetable.next_run(item, first, BERLIN)) == "2026-11-27 03:00"
    assert local(timetable.next_run(item, epoch("2026-12-05 12:00"), BERLIN)) == "2026-12-06 03:00"


def test_daylight_saving_rules():
    """A time in the spring gap runs at the first valid instant after it; a repeated
    autumn time at its first occurrence."""
    item = {"on": True, "time": "02:30", "repeat": {"kind": "daily"}}
    spring = timetable.next_run(item, epoch("2026-03-28 12:00"), BERLIN)
    assert local(spring) == "2026-03-29 03:00"
    autumn = timetable.next_run(item, epoch("2026-10-24 12:00"), BERLIN)
    first = datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0).timestamp()
    assert autumn == first


def test_install_time_limits():
    now = epoch("2026-11-23 10:00")
    assert local(timetable.install_time("2026-11-24T03:00", now, BERLIN)) == "2026-11-24 03:00"
    for text, match in [("2026-11-23T09:00", "future"), ("2026-12-01T10:01", "7 days"), ("2026-11-24", "date"),
                        ("2026-02-30T03:00", "date"), (None, "date")]:
        with pytest.raises(ValueError, match=match):
            timetable.install_time(text, now, BERLIN)


def test_texts_for_the_log():
    names = {A: "Kitchen", B: "Hall", C: "Garage", "d" * 32: "Attic"}
    assert timetable.devices_text("all", names) == "All Shellys"
    assert timetable.devices_text([A, B, C, "d" * 32], names) == "Kitchen, Hall, Garage and 1 more"
    item = {"time": "05:00", "repeat": {"kind": "weekdays", "days": [0, 3]}}
    assert timetable.repeat_text(item) == "Mon, Thu · 05:00"
    old = {"name": "A", "on": True, "action": "restart", "pause": 10, "time": "05:00", "repeat": {"kind": "daily"},
           "devices": "all"}
    new = {**old, "on": False, "pause": 20}
    assert timetable.changes(old, new, names) == ["Switched off", "Pause: 10 s → 20 s"]
    assert date.fromisoformat(timetable.anchor_for("23:59", epoch("2026-11-23 10:00"), BERLIN)) == date(2026, 11, 23)


# ---------------------------------------------------------------- Scheduled installations


@pytest.fixture
def updating(mgr, net, monkeypatch, quick_install):
    """The update command "installs" the version of the served store file."""

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        version = mgr.firmware[url.rsplit("/", 1)[-1]]["version"]
        net.devices[device] = (*net.devices[device][:2], version, version)

    monkeypatch.setattr(shelly, "trigger_update", trigger)


async def run_jobs(mgr, at):
    """Start the jobs due at `at` and wait for them."""
    mgr.start_due(at)
    await asyncio.gather(*list(mgr._timetable_tasks))


def soon():
    """An "Install at" text 2 hours ahead (local time of the tests: Berlin)."""
    return datetime.fromtimestamp(time.time() + 7200, BERLIN).strftime("%Y-%m-%dT%H:%M")


async def test_scheduled_installation_runs_at_its_time(mgr, net, updating):
    """The plan is stored and shown, logged as scheduled, installed at its time with the
    approving user marked "(scheduled)" and notified."""
    await mgr.scan()
    at = mgr.plan_install(A, mgr.devices[A]["sha256"], soon(), "alice")
    view = next(d for d in mgr.public_state()["devices"] if d["id"] == A)["planned"]
    assert view["at"] == int(at) and view["by"] == "alice" and view["target"] == "1.5.0" and "sha256" not in view
    assert mgr.due_in(time.time()) == pytest.approx(at - time.time(), abs=2)
    await run_jobs(mgr, at)
    dev = mgr.devices[A]
    assert dev["version"] == "1.5.0" and "planned" not in dev
    events = [(e["kind"], e.get("event"), e.get("user")) for e in mgr.events.entries]
    assert ("install", "scheduled", "alice") in events and ("install", "confirmed", "alice (scheduled)") in events
    assert "installed as scheduled" in mgr.notified[-1][1]


async def test_missed_plan_lapses_without_notification(mgr, net, updating):
    await mgr.scan()
    at = mgr.plan_install(A, mgr.devices[A]["sha256"], soon(), "alice")
    mgr.notified.clear()
    mgr.start_due(at + manager_schedules.MISSED_AFTER_S + 1)  # the app was not running at that time
    await asyncio.gather(*list(mgr._timetable_tasks))
    assert mgr.devices[A]["version"] == "1.4.0" and mgr.devices[A]["status"] == manager_core.STATUS_READY
    lapsed = mgr.events.entries[-1]
    assert lapsed["event"] == "plan_missed" and lapsed["severity"] == "warning" and not mgr.notified


async def test_plan_lapses_when_the_device_changed(mgr, net, updating):
    """Another version on the device at the time: nothing is installed, the plan
    lapses with the reason (log warning and notification)."""
    await mgr.scan()
    at = mgr.plan_install(A, mgr.devices[A]["sha256"], soon(), "alice")
    mgr.devices[A]["version"] = "1.4.1"  # e.g. updated in the Shelly app, seen by Reload Device Info
    await run_jobs(mgr, at)
    entry = mgr.events.entries[-1]
    assert entry["event"] == "plan_lapsed" and "1.4.1" in entry["detail"] and "was not started" in mgr.notified[-1][1]


async def test_check_with_a_new_offer_lapses_the_plan(mgr, net, updating):
    """A check that provides another file lets the plan lapse at once."""
    await mgr.scan()
    mgr.plan_install(A, mgr.devices[A]["sha256"], soon(), "alice")
    net.offer_version = "1.6.0"
    await mgr.check_device(A)
    await asyncio.gather(*list(mgr._timetable_tasks))
    assert "planned" not in mgr.devices[A]
    assert mgr.events.entries[-1]["event"] == "plan_lapsed"


async def test_plan_refused_for_a_wrong_approval_or_time(mgr, net):
    await mgr.scan()
    with pytest.raises(shelly.ShellyError):
        mgr.plan_install(A, "0" * 64, soon(), "alice")
    with pytest.raises(ValueError):
        mgr.plan_install(A, mgr.devices[A]["sha256"], "2020-01-01T03:00", "alice")


async def test_http_install_at_cancel_and_install_now(client, mgr, net, monkeypatch):
    """"at" schedules instead of installing; cancel needs the confirmation; installing
    now drops the plan."""
    await mgr.scan()
    hdr = {"X-CSRF-Token": client.csrf}
    sha = mgr.devices[A]["sha256"]
    r = await client.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True, "at": "2020-01-01T03:00"},
                          headers=hdr)
    assert r.status == 400
    r = await client.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True, "at": soon()}, headers=hdr)
    assert r.status == 200 and (await r.json())["scheduled"] is True and mgr.devices[A]["planned"]
    url = f"/api/devices/{A}/planned/cancel"
    assert (await client.post(url, json={}, headers=hdr)).status == 400
    assert (await client.post(url, json={"confirm": True}, headers=hdr)).status == 200
    assert "planned" not in mgr.devices[A]
    assert (await client.post(url, json={"confirm": True}, headers=hdr)).status == 409
    started = []

    async def install(device_id, sha, approved_by, downgrade=False, restart_first=False):
        started.append(device_id)
        return True

    monkeypatch.setattr(mgr, "install", install)
    await client.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True, "at": soon()}, headers=hdr)
    r = await client.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True}, headers=hdr)
    assert r.status == 200
    await asyncio.sleep(0)
    assert started == [A] and "planned" not in mgr.devices[A]
    assert mgr.events.entries[-1]["event"] == "plan_cancelled" and mgr.events.entries[-1]["detail"]


# ---------------------------------------------------------------- Schedule runs


@pytest.fixture
def actions(mgr, net, monkeypatch):
    """Fake restart and settings writes; records the calls per device IP."""
    calls = {"restart": [], "write": [], "unreachable": set(), "restart_required": False}

    async def restart(_session, _gen, origin, _auth=None):
        if origin in calls["unreachable"]:
            raise TimeoutError()
        calls["restart"].append(origin)

    async def write_settings(_session, _gen, origin, changes, _auth=None):
        if origin in calls["unreachable"]:
            raise TimeoutError()
        calls["write"].append((origin, dict(changes)))
        return calls["restart_required"]

    real_read = shelly.read_settings

    async def read_settings(*args, **kwargs):
        answer = await real_read(*args, **kwargs)
        written = [c for o, c in calls["write"] if o == args[2]]
        if written:
            answer["cloud"] = written[-1].get("cloud")
        return answer

    monkeypatch.setattr(shelly, "restart", restart)
    monkeypatch.setattr(shelly, "write_settings", write_settings)
    monkeypatch.setattr(shelly, "read_settings", read_settings)
    return calls


async def test_restart_run_leaves_out_devices_and_logs_once(mgr, net, actions):
    """All Shellys: ignored and battery devices are left out and named; one log entry
    with the result; "Last run" shows it; no notification when all were reached."""
    await mgr.scan()
    mgr.devices[B]["ignored"] = True
    mgr.devices[C]["battery"] = True
    item = mgr.create_schedule(schedule_data(), "alice")
    assert item["next"] and mgr.events.entries[-1]["event"] == "created"
    mgr.notified.clear()
    await run_jobs(mgr, item["next"])
    assert actions["restart"] == ["http://192.168.1.10"]
    run = mgr.events.entries[-1]
    assert run["kind"] == "schedule" and run["event"] == "run" and run["trigger"] == "schedule"
    assert run["result"] == "1 of 1 restarted · 2 left out" and run["severity"] == "success"
    assert {x["reason"] for x in run["left_out"]} == {"ignored", "battery device"}
    view = mgr.public_state()["schedules"][0]
    assert view["last"]["text"] == run["result"] and view["next"] > item["next"] - 1 and not view["running"]
    assert not mgr.notified and mgr.schedule_running is None


async def test_staggered_restart_and_unreachable_device_notifies(mgr, net, actions, monkeypatch):
    """With a pause the devices are restarted one after another; a device that is not
    reached makes the run a warning with one HA notification."""
    await mgr.scan()
    sleeps = []
    real_sleep = asyncio.sleep

    async def sleep(seconds, *args):
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(manager_schedules.asyncio, "sleep", sleep)
    actions["unreachable"].add("http://192.168.1.11")
    item = mgr.create_schedule(schedule_data(pause=10, devices=[A, B, C]), "alice")
    await run_jobs(mgr, item["next"])
    assert sleeps.count(10) == 2 and len(actions["restart"]) == 2
    run = mgr.events.entries[-1]
    assert run["result"] == "2 of 3 restarted · 1 not reachable" and run["severity"] == "warning"
    title, text, nid = mgr.notified[-1]
    assert "Night restart" in title and "Hall" in text and nid == f"sfm_schedule_{item['id']}"


async def test_cloud_switch_restarts_when_required_without_setting_entries(mgr, net, actions):
    """Cloud off: written to each device, restarted when the device asks for it; only
    the run is logged, no "setting" entry per device."""
    await mgr.scan()
    actions["restart_required"] = True
    item = mgr.create_schedule(schedule_data(action="cloud_off", devices=[A, C]), "alice")
    before = len(mgr.events.entries)
    await run_jobs(mgr, item["next"])
    assert [c for _o, c in actions["write"]] == [{"cloud": False}, {"cloud": False}]
    assert len(actions["restart"]) == 2
    new = mgr.events.entries[before:]
    assert [e["kind"] for e in new] == ["schedule"] and new[0]["result"] == "2 of 2 switched"
    assert {f["detail"] for f in new[0]["findings"]} == {"switched off, restarted"}


async def test_bluetooth_leaves_out_devices_without_the_switch(mgr, net, actions):
    await mgr.scan()  # the fake devices report Bluetooth as unsupported
    item = mgr.create_schedule(schedule_data(action="ble_off", devices=[A]), "alice")
    await run_jobs(mgr, item["next"])
    run = mgr.events.entries[-1]
    assert run["result"] == "No device to run on · 1 left out" and run["severity"] == "warning"
    assert run["left_out"] == [{"device": "Kitchen", "reason": "no Bluetooth switch"}]


async def test_missed_run_and_waiting_for_running_work(mgr, net, actions, monkeypatch):
    """Missed by the app: logged as missed only. Due while an installation runs: waits
    up to RUN_WAIT_S, then missed."""
    await mgr.scan()
    item = mgr.create_schedule(schedule_data(), "alice")
    mgr.notified.clear()
    await run_jobs(mgr, item["next"] + manager_schedules.MISSED_AFTER_S + 1)
    missed = mgr.events.entries[-1]
    assert missed["event"] == "missed" and missed["severity"] == "warning" and not actions["restart"]
    assert mgr.public_state()["schedules"][0]["last"]["severity"] == "missed" and not mgr.notified
    monkeypatch.setattr(manager_schedules, "RUN_WAIT_S", 0.05)
    monkeypatch.setattr(manager_schedules, "RUN_WAIT_POLL_S", 0.01)
    mgr.installing.add(A)
    await run_jobs(mgr, mgr.schedules[0]["next"])
    assert mgr.events.entries[-1]["event"] == "missed" and "installations" in mgr.events.entries[-1]["detail"]
    assert not actions["restart"]


async def test_run_blocks_the_full_check_and_installations(mgr, net, actions, monkeypatch):
    """While a run goes on the full check and installations are refused."""
    await mgr.scan()
    item = mgr.create_schedule(schedule_data(), "alice")
    mgr.reserve_schedule_run(item["id"])
    with pytest.raises(shelly.ShellyError, match="Night restart"):
        mgr.reserve_check("all")
    assert "Night restart" in mgr.busy
    await mgr.run_schedule_now(item["id"], "bob")
    assert mgr.schedule_running is None and mgr.events.entries[-1]["trigger"] == "manual"
    assert mgr.events.entries[-1]["user"] == "bob"


async def test_http_schedules_crud_and_run_now(client, mgr, net, actions):
    await mgr.scan()
    hdr = {"X-CSRF-Token": client.csrf}
    r = await client.post("/api/schedules", json=schedule_data(name=""), headers=hdr)
    assert r.status == 400
    r = await client.post("/api/schedules", json=schedule_data(), headers=hdr)
    assert r.status == 200
    view = await r.json()
    sid = view["id"]
    assert view["devices_text"] == "All Shellys" and view["next_text"]
    state = await (await client.get("/api/state")).json()
    assert state["schedules"][0]["id"] == sid and state["beta_firmware"] is False
    r = await client.post(f"/api/schedules/{sid}", json=schedule_data(time="04:00", on=False), headers=hdr)
    assert r.status == 200 and (await r.json())["next"] is None
    changed = mgr.events.entries[-1]
    assert changed["event"] == "changed" and "Switched off" in changed["changes"]
    assert (await client.post("/api/schedules/ffffffffffff", json=schedule_data(), headers=hdr)).status == 404
    assert (await client.post(f"/api/schedules/{sid}/run", json={}, headers=hdr)).status == 400
    mgr.reserve_check("all")
    assert (await client.post(f"/api/schedules/{sid}/run", json={"confirm": True}, headers=hdr)).status == 409
    mgr.progress = None
    r = await client.post(f"/api/schedules/{sid}/run", json={"confirm": True}, headers=hdr)
    assert r.status == 200
    for _ in range(20):
        await asyncio.sleep(0)
    assert mgr.events.entries[-1]["event"] == "run" and len(actions["restart"]) == 3
    assert (await client.post(f"/api/schedules/{sid}/delete", json={}, headers=hdr)).status == 400
    assert (await client.post(f"/api/schedules/{sid}/delete", json={"confirm": True}, headers=hdr)).status == 200
    assert mgr.schedules == [] and mgr.events.entries[-1]["event"] == "deleted"


async def test_schedules_survive_a_restart(mgr, net):
    """Schedules are part of state.json."""
    from helpers import make_manager

    mgr.create_schedule(schedule_data(repeat={"kind": "every", "n": 2}), "alice")
    again = make_manager(net)
    await again.start_timetable()
    assert again.schedules[0]["name"] == "Night restart" and again.schedules[0]["anchor"]
