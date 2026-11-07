"""Targeted tests of the 3.0 features: schedule, migrations, ignored devices,
Reload Device Info, bounded parallel checks, log severity, device capabilities
and the password workflow. All device/cloud/Supervisor access is mocked."""

import asyncio
import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import app_options
import manager
import pytest
import schedule
import shelly
from conftest import OPTIONS, A, B, C
from credentials import Credentials
from events import EventLog

BERLIN = ZoneInfo("Europe/Berlin")


def ts(text):
    """Epoch of an ISO timestamp with offset."""
    return datetime.fromisoformat(text).timestamp()


# ---------------------------------------------------------------- Schedule


def test_schedule_option_bounds():
    """Interval 1–30 days, time HH:MM, log limit 100–3000."""
    for key, value in (("check_interval_days", 0), ("check_interval_days", 31), ("check_time", "24:00"),
                       ("check_time", "2:45"), ("log_entry_limit", 99), ("log_entry_limit", 3001)):
        with pytest.raises(ValueError):
            app_options.merge(OPTIONS, {key: value})
    new, changed = app_options.merge(OPTIONS, {"check_interval_days": 30, "check_time": "23:59", "log_entry_limit": 3000})
    assert changed == ["check_interval_days", "check_time", "log_entry_limit"]


def test_anchor_is_next_clock_occurrence():
    """Before the clock time today -> today, after it -> tomorrow."""
    state, changed = schedule.configure(None, 7, "02:45", ts("2026-11-06T01:00:00+01:00"), BERLIN)
    assert changed and state["anchor"] == "2026-11-06" and state["last_date"] is None
    state, _ = schedule.configure(None, 7, "02:45", ts("2026-11-06T03:00:00+01:00"), BERLIN)
    assert state["anchor"] == "2026-11-07"
    same, changed = schedule.configure(state, 7, "02:45", ts("2026-12-01T00:00:00+01:00"), BERLIN)
    assert same is state and not changed
    moved, changed = schedule.configure(state, 3, "02:45", ts("2026-12-01T00:00:00+01:00"), BERLIN)
    assert changed and moved["anchor"] == "2026-12-01"


def test_catch_up_collapses_and_keeps_cadence():
    """Missed dates run once; the cadence stays anchored."""
    state = {"days": 7, "time": "02:45", "anchor": "2026-11-07", "last_date": None}
    now = ts("2026-11-29T12:00:00+01:00")  # 07, 14, 21, 28 missed
    due, following = schedule.status(state, now, BERLIN)
    assert due == date(2026, 11, 28) and following == ts("2026-12-05T02:45:00+01:00")
    state["last_date"] = due.isoformat()
    assert schedule.seconds_until(state, now, BERLIN) == pytest.approx(following - now)
    # Before the anchor nothing is due
    assert schedule.status(state | {"last_date": None}, ts("2026-11-06T12:00:00+01:00"), BERLIN)[0] is None


def test_dst_gap_runs_at_first_valid_instant():
    """2026-03-29 02:30 does not exist in Berlin: run at 03:00 CEST, next day normal."""
    assert schedule.instant(date(2026, 3, 29), "02:30", BERLIN) == ts("2026-03-29T03:00:00+02:00")
    assert schedule.instant(date(2026, 3, 30), "02:30", BERLIN) == ts("2026-03-30T02:30:00+02:00")
    state = {"days": 1, "time": "02:30", "anchor": "2026-03-28", "last_date": "2026-03-28"}
    assert schedule.status(state, ts("2026-03-29T02:59:59+02:00"), BERLIN)[0] is None
    assert schedule.status(state, ts("2026-03-29T03:00:00+02:00"), BERLIN)[0] == date(2026, 3, 29)


def test_dst_repeated_hour_runs_once_at_first_occurrence():
    """2026-10-25 02:30 happens twice: first occurrence, deduplicated by date."""
    first = ts("2026-10-25T02:30:00+02:00")
    assert schedule.instant(date(2026, 10, 25), "02:30", BERLIN) == first
    state = {"days": 1, "time": "02:30", "anchor": "2026-10-24", "last_date": "2026-10-24"}
    due, _ = schedule.status(state, first, BERLIN)
    assert due == date(2026, 10, 25)
    state["last_date"] = due.isoformat()
    assert schedule.status(state, ts("2026-10-25T02:30:00+01:00"), BERLIN)[0] is None


def test_local_zone_from_environment(monkeypatch):
    """The HA time zone comes from TZ; unknown/missing values fall back to UTC."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert schedule.local_zone()[1] == "Europe/Berlin"
    monkeypatch.setenv("TZ", "Nowhere/Invalid")
    assert schedule.local_zone()[1] == "UTC"


async def test_automatic_check_records_attempt_once(mgr, net):
    """The due date is recorded at the start – also when the check fails."""
    mgr.schedule = {"days": 7, "time": "02:45", "anchor": "2020-01-01", "last_date": None}
    assert mgr.scan_due_in(datetime.now(UTC).timestamp()) == 0

    async def broken():
        raise manager.ShellyError("registry down")

    mgr._registry = broken
    assert await mgr.scan(automatic=True)
    assert mgr.schedule["last_date"] is not None
    assert mgr.scan_due_in(datetime.now(UTC).timestamp()) > 0  # no tight retry loop
    assert json.loads(manager.STATE_FILE.read_text())["schedule"] == mgr.schedule


async def test_due_check_waits_for_installations(mgr, net):
    """A running installation postpones the due check instead of consuming it."""
    mgr.schedule = {"days": 7, "time": "02:45", "anchor": "2020-01-01", "last_date": None}
    mgr.installing.add(A)
    assert not await mgr.scan(automatic=True)
    assert mgr.schedule["last_date"] is None
    mgr.installing.clear()
    assert await mgr.scan(automatic=True)


async def test_no_immediate_check_after_upgrade(net):
    """A v2 state (last_scan, old version) is not due at once: next clock time."""
    manager.STATE_FILE.write_text(json.dumps({"devices": {}, "last_scan": 1, "scanned_version": "2.2.1"}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t", version="3.0.0")
    assert m.scan_due_in(datetime.now(UTC).timestamp()) > 0
    assert m.schedule["days"] == 7 and m.schedule["time"] == "02:45"


# ---------------------------------------------------------------- Migrations


def test_removed_options_never_propagate():
    """Legacy keys (shared password, hourly interval, old log limit) are dropped."""
    legacy = {**OPTIONS, "shelly_password": "secret!", "check_interval_hours": 24, "log_max_entries": 20000}
    view = app_options.public_view(legacy)
    assert "shelly_password" not in view and "check_interval_hours" not in view and "log_max_entries" not in view
    assert "secret!" not in json.dumps(view)
    new, _ = app_options.merge(legacy, {"check_time": "03:00"})
    assert "shelly_password" not in new


async def test_supervisor_store_cleaned_once(net):
    """A v2 state triggers one options save without the secret, then never again."""
    manager.STATE_FILE.write_text(json.dumps({"devices": {}, "last_scan": 1}))
    m = manager.Manager({**OPTIONS, "shelly_password": "secret!"}, session=None, token="t")
    posts = []

    async def post(path, body):
        posts.append((path, body))

    m._supervisor_post = post
    assert await m.migrate_options()
    assert await m.migrate_options()
    assert len(posts) == 1 and posts[0][0] == "/addons/self/options"
    assert "secret!" not in json.dumps(posts) and "shelly_password" not in posts[0][1]["options"]
    m2 = manager.Manager(dict(OPTIONS), session=None, token="t")  # restart: marker persisted
    m2._supervisor_post = post
    assert await m2.migrate_options() and len(posts) == 1


async def test_supervisor_cleanup_failure_retried(net):
    """A failing cleanup keeps the marker unset (retry at the next start)."""
    manager.STATE_FILE.write_text(json.dumps({"devices": {}}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")

    async def post(*_args):
        raise manager.ShellyError("Supervisor not reachable")

    m._supervisor_post = post
    assert not await m.migrate_options()
    assert "options" not in json.loads(manager.STATE_FILE.read_text()).get("migrations", {})


async def test_fresh_install_needs_no_cleanup(net):
    """Without a previous state there is nothing to clean."""
    m = manager.Manager(dict(OPTIONS), session=None, token="t")

    async def post(*_args):
        raise AssertionError("no Supervisor call expected")

    m._supervisor_post = post
    assert await m.migrate_options()


def test_no_shared_password_fallback(tmp_path):
    """Credentials only come from per-device entries."""
    creds = Credentials(tmp_path / "c.json")
    assert creds.auth_for(A, 2) is None and creds.auth_for(A, 1) is None


def test_log_limit_trims_once_and_keeps_limit(tmp_path):
    """Initial trim to the newest 1000; a configured 3000 survives a restart."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    path.write_text(json.dumps([{"ts": now - 5000 + i, "kind": "check", "ok": True, "n": i} for i in range(5000)]))
    log = EventLog(path, 90, 1000)
    assert len(log.entries) == 1000 and log.entries[-1]["n"] == 4999
    path.write_text(json.dumps([{"ts": now - 5000 + i, "kind": "check", "ok": True} for i in range(5000)]))
    assert len(EventLog(path, 90, 3000).entries) == 3000
    assert len(EventLog(path, 90, 3000).entries) == 3000


def test_ui_prefs_migrated_from_v2(net):
    """eco/restart -> action, removed AOP column and removed sort column dropped."""
    old = {"devices": {"sort": ["eco", "asc"], "hidden": ["eco", "restart", "ntp"]}}
    manager.STATE_FILE.write_text(json.dumps({"devices": {}, "ui": old, "ui_version": 2}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.ui == {"devices": {"sort": ["device", "asc"], "hidden": ["action", "ntp"]}}
    old["devices"] = {"sort": ["aop", "desc"], "hidden": ["eco", "aop"]}
    manager.STATE_FILE.write_text(json.dumps({"devices": {}, "ui": old, "ui_version": 2}))
    assert manager.Manager(dict(OPTIONS), session=None, token="t").ui == {"devices": {"sort": ["device", "asc"], "hidden": []}}


async def test_ui_prefs_order_validated(mgr):
    """Column order is stored deduplicated; invalid names are refused."""
    mgr.set_ui({"devices": {"sort": ["ip", "desc"], "hidden": [], "order": ["ip", "gen", "ip"]}})
    assert mgr.ui["devices"]["order"] == ["ip", "gen"]
    with pytest.raises(ValueError):
        mgr.set_ui({"devices": {"sort": ["ip", "desc"], "hidden": [], "order": ["<b>"]}})


# ---------------------------------------------------------------- Ignored devices


async def test_ignored_device_not_contacted_and_stays_registered(mgr, net):
    """Ignored: no device request, known update kept, not 'unregistered'."""
    await mgr.scan()
    mgr.set_ignored(A, True)
    calls = net.info_calls
    net.devices[A] = ("Kitchen renamed", *net.devices[A][1:])
    await mgr.scan()
    assert net.info_calls == calls + 2  # only B and C
    dev = mgr.devices[A]
    assert dev["status"] == manager.STATUS_READY and dev["name"] == "Kitchen renamed"
    check = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert check["devices"] == 2
    await mgr.check_device(A)  # explicit single-device check stays possible
    assert net.info_calls == calls + 3
    mgr.set_ignored(A, False)
    assert "ignored" not in mgr.devices[A] and net.info_calls == calls + 3  # resume sends nothing


# ---------------------------------------------------------------- Reload Device Info


async def test_reload_device_reads_only_the_device(mgr, net):
    """No offer/download/changelog call, offer and check time kept."""
    await mgr.scan()
    dev = mgr.devices[A]
    before = {k: dev.get(k) for k in ("last_check", "last_success", "offer_version", "status")}
    offers, downloads = net.offer_calls, net.downloads
    net.settings_calls.clear()
    result = await mgr.reload_device(A)
    assert result == {"warning": None}
    assert net.offer_calls == offers and net.downloads == downloads
    assert {k: dev.get(k) for k in before} == before and dev["info_refreshed"]
    assert net.settings_calls == [True]  # extended status read
    # Version reached the known offer (e.g. updated in the Shelly app): up to date
    net.devices[A] = (*net.devices[A][:2], "1.5.0", "1.5.0")
    await mgr.reload_device(A)
    assert dev["status"] == manager.STATUS_CURRENT and dev["last_update"] and net.offer_calls == offers


async def test_reload_partial_failure_and_locks(mgr, net):
    """A failed status read is a warning with unknown state; refused during a check."""
    await mgr.scan()
    net.settings_error = shelly.AuthError("The Shelly rejected the password")
    result = await mgr.reload_device(B)
    assert "rejected" in result["warning"] and mgr.devices[B]["settings_error"]["severity"] == "error"
    net.gate = asyncio.Event()
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    with pytest.raises(manager.ShellyError):
        await mgr.reload_device(B)
    net.gate.set()
    await task


# ---------------------------------------------------------------- Parallel checks


async def test_parallel_check_is_bounded_and_single_flight(mgr, net, monkeypatch):
    """At most CHECK_PARALLEL devices at once; one offer and one download per firmware."""
    for i in range(10):
        net.devices[f"{i:031x}e"] = (f"Dev {i}", f"192.168.1.{50 + i}", "1.4.0", "1.5.0")
    running, peak = 0, 0
    original = shelly.device_info

    async def counting(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        try:
            return await original(*args, **kwargs)
        finally:
            running -= 1

    monkeypatch.setattr(shelly, "device_info", counting)
    assert await mgr.scan()
    assert peak == manager.CHECK_PARALLEL
    assert net.offer_calls == 1 and net.downloads == 1
    assert len([d for d in mgr.devices.values() if d["status"] == manager.STATUS_READY]) == 12


# ---------------------------------------------------------------- Severity


async def test_check_severity_levels(mgr, net):
    """Unreachable = warning, auth failure = error; entry gets the worst level."""
    net.unreachable.add(B)
    await mgr.scan()
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert entry["severity"] == "warning" and entry["ok"] and entry["warnings"] == 1
    assert {f["severity"] for f in entry["findings"]} == {"success", "warning"}
    net.unreachable.clear()
    net.settings_error = shelly.AuthError("The Shelly requires a password")
    await mgr.scan()
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert entry["severity"] == "error" and not entry["ok"] and entry["errors"] == 3
    assert "0 warning(s), 3 error(s)" in entry["result"]


def test_historic_log_entries_unchanged(tmp_path):
    """Old entries without severity are loaded and saved as they are."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    old = [{"ts": now, "kind": "check", "ok": False, "result": "x"}]
    path.write_text(json.dumps(old))
    log = EventLog(path, 90, 1000)
    log.add("delete", type="t")
    stored = json.loads(path.read_text())
    assert stored[0] == old[0] and stored[1]["severity"] == "success"


# ---------------------------------------------------------------- Capabilities (shelly.py)


@pytest.fixture
def device_api(monkeypatch):
    """Fake _rpc/_device_json recording the calls; answers from a dict."""
    calls, answers = [], {}

    async def rpc(_session, _origin, method, params=None, auth=None):
        calls.append((method, params))
        answer = answers.get(method, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def device_json(_session, _origin, path, auth=None, params=None):
        calls.append((path, params))
        answer = answers.get(path, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(shelly, "_rpc", rpc)
    monkeypatch.setattr(shelly, "_device_json", device_json)
    return calls, answers


async def test_gen2_capabilities(device_api):
    """AP/BLE switches, active webhooks/schedules; unsupported vs. unknown."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"sys": {"device": {"eco_mode": True}}, "wifi": {"ap": {"enable": True}}, "ble": {"enable": False}}
    answers["Webhook.List"] = {"hooks": [{"enable": False}, {"enable": True, "urls": ["http://x"]}], "rev": 1}
    answers["Schedule.List"] = shelly.UnsupportedError("no handler")
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert (got["eco"], got["ap"], got["ble"], got["actions"], got["schedules"]) == (True, True, False, True, "unsupported")
    assert "urls" not in json.dumps(got)
    answers["Shelly.GetConfig"] = {"sys": {}, "wifi": {"ap": {"enable": False}}, "ble": {"rpc": {"enable": True}}}
    answers["Schedule.List"] = TimeoutError()
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert got["ble"] == "unsupported" and got["eco"] == "unsupported" and got["schedules"] is None
    calls.clear()
    await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None)
    assert [c[0] for c in calls] == ["Shelly.GetConfig"]  # dialogs: one request


async def test_gen1_capabilities(device_api):
    """Gen1: AP read-only, schedules from /settings, actions need urls."""
    _calls, answers = device_api
    answers["/settings"] = {"wifi_ap": {"enabled": False}, "relays": [{"default_state": "off", "schedule": True, "schedule_rules": ["0800-0123456-on"]}]}
    answers["/settings/actions"] = {"actions": {"out_on_url": [{"index": 0, "enabled": True, "urls": []}]}}
    got = await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None, extended=True)
    assert (got["ap"], got["ble"], got["schedules"], got["actions"]) == (False, "unsupported", True, False)


async def test_ap_and_ble_writes(device_api):
    """Gen2 AP sends only ap.enable; Gen1 AP and BLE are refused."""
    calls, _answers = device_api
    await shelly.write_settings(None, 2, "http://1.2.3.4", {"ap": False, "ble": True})
    assert calls == [("Wifi.SetConfig", {"config": {"ap": {"enable": False}}}), ("BLE.SetConfig", {"config": {"enable": True}})]
    with pytest.raises(ValueError):
        await shelly.write_settings(None, 1, "http://1.2.3.4", {"ap": True})


async def test_gen2_identity_from_shelly_endpoint(device_api):
    """Complete /shelly answer: no second GetDeviceInfo request."""
    calls, answers = device_api
    answers["/shelly"] = {"id": "shellyplus1-a", "mac": "aa", "model": "SNSW", "gen": 2, "fw_id": "b", "ver": "1.4.4", "app": "Plus1", "auth_en": True}
    info = await shelly.device_info(None, "http://1.2.3.4")
    assert [c[0] for c in calls] == ["/shelly"] and info["auth_enabled"] is True and info["type"] == "Plus1"


async def test_gen1_password_errors_never_contain_the_url(monkeypatch):
    """Network errors of /settings/login are re-raised without URL/query."""
    import aiohttp

    async def failing(*_args, **_kwargs):
        raise aiohttp.InvalidURL("http://1.2.3.4/settings/login?password=topsecret")

    monkeypatch.setattr(shelly, "_device_json", failing)
    with pytest.raises(shelly.ShellyError) as err:
        await shelly.set_protection(None, 1, "http://1.2.3.4", None, shelly.DeviceAuth(gen=1, password="topsecret"))
    assert "topsecret" not in str(err.value)


# ---------------------------------------------------------------- Password workflow


@pytest.fixture
async def device(mgr, net, monkeypatch):
    """Checked Gen2 device A with a fake protection state on the device."""
    await mgr.scan()
    box = {"protected": False, "ha1": None, "fail_write": None, "apply": True, "writes": 0}

    async def status(_session, _origin):
        return box["protected"]

    async def protect(_session, gen, _origin, realm, new_auth, auth=None):
        box["writes"] += 1
        if box["apply"]:
            box["protected"], box["ha1"] = (True, new_auth.ha1) if new_auth else (False, None)
        if box["fail_write"]:
            raise box["fail_write"]

    async def read(_session, gen, _origin, _type, auth=None, extended=False):
        if box["protected"] and (auth is None or auth.ha1 != box["ha1"]):
            raise shelly.AuthError("The Shelly rejected the password")
        return {}

    monkeypatch.setattr(shelly, "protection_status", status)
    monkeypatch.setattr(shelly, "set_protection", protect)
    monkeypatch.setattr(shelly, "read_settings", read)
    return box


async def test_enable_protection_verified_and_confirmed(mgr, device):
    """Confirmation and acknowledgment are required; success only after verification."""
    with pytest.raises(ValueError):
        await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=False)
    assert device["writes"] == 0
    result = await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True, user="u")
    assert result == {"protected": True} and mgr.credentials.has_device_password(A)
    assert mgr.events.entries[-1]["action"] == "enabled"


async def test_enable_with_ambiguous_answer(mgr, device):
    """Lost answer but the device is protected with the new password: success."""
    device["fail_write"] = TimeoutError()
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    assert mgr.credentials.has_device_password(A)


async def test_change_failure_keeps_previous_credential(mgr, device):
    """Device did not apply the change: old password kept, error logged."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    old = dict(mgr.credentials.entries[A])
    device["apply"] = False
    with pytest.raises(shelly.ShellyError, match="previous password still works"):
        await mgr.set_device_password(A, "change", "pw-2", confirm=True, acknowledged=True)
    assert mgr.credentials.entries[A] == old
    assert mgr.events.entries[-1]["severity"] == "error"


async def test_remove_protection_deletes_only_after_success(mgr, device):
    """Credentials are deleted only when the device reports no protection."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    device["apply"] = False
    with pytest.raises(shelly.ShellyError):
        await mgr.set_device_password(A, "remove", confirm=True)
    assert mgr.credentials.has_device_password(A)
    device["apply"] = True
    assert await mgr.set_device_password(A, "remove", confirm=True) == {"protected": False}
    assert not mgr.credentials.has_device_password(A)


async def test_store_requires_protected_device(mgr, device):
    """Storing on an unprotected device proves nothing and is refused."""
    with pytest.raises(shelly.ShellyError, match="not password protected"):
        await mgr.set_device_password(A, "store", "pw", confirm=True)
    device["protected"], device["ha1"] = True, shelly.gen2_ha1(mgr.devices[A]["shelly_id"], "right")
    with pytest.raises(shelly.AuthError):
        await mgr.set_device_password(A, "store", "wrong", confirm=True)
    await mgr.set_device_password(A, "store", "right", confirm=True)
    assert mgr.credentials.has_device_password(A)


async def test_secrets_never_in_state_or_logs(mgr, device):
    """Neither password nor HA1 appear in the log, state file or public state."""
    await mgr.set_device_password(A, "enable", "Very-Secret-1", confirm=True, acknowledged=True)
    ha1 = mgr.credentials.entries[A]["ha1"]
    blobs = [json.dumps(mgr.events.entries), manager.STATE_FILE.read_text(), json.dumps(mgr.public_state())]
    assert not any("Very-Secret-1" in b or ha1 in b for b in blobs)


async def test_public_state_fields(mgr, net):
    """Schedule replaces the hourly interval; the shared-password flag is gone."""
    await mgr.scan()
    state = mgr.public_state()
    assert "interval" not in state and "shared_password_set" not in state
    assert state["schedule"]["days"] == 7 and state["schedule"]["timezone"] == "Europe/Berlin"
    dev = next(d for d in state["devices"] if d["id"] == C)
    assert dev["ap"] is False and dev["ble"] == "unsupported" and dev["ignored"] is False
