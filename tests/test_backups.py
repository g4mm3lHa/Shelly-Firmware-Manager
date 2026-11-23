"""Backups of device settings: the encrypted files (backup_store), reading, comparing
and restoring on the device side (shelly_backup), the manager layer (manager_backups)
with installation, schedules, download, import and store export, and the API.

No connection is opened: the Shellys are in-memory fakes (FakeShelly, FakeGen1)."""

import asyncio
import json

import backup_store
import manager_backups
import manager_core
import pytest
import shelly
import shelly_backup
import store_transfer
from backup_store import BackupError, BackupStore
from conftest import A, B, C
from fake_shelly import SCRIPT, FakeGen1, FakeShelly
from helpers import chunks, make_manager, web_client
from shelly import AuthError, ShellyError


def target_of(device, gen=2):
    """A shelly_backup.Target whose requests go to the fake device."""
    return shelly_backup.Target(None, gen, "http://192.168.1.10", None)


@pytest.fixture
def shellys(monkeypatch):
    """Fake Shellys by origin; the device requests of shelly_backup go to them."""
    devices = {}

    async def rpc(_session, origin, method, params=None, _auth=None):
        return await devices[origin].rpc(method, params)

    async def get(_session, origin, path, _auth=None, params=None):
        device = devices[origin]
        if path == "/shelly":
            if device.down:
                raise TimeoutError()
            return {}
        return await device.get(path, params)

    async def restart(_session, _gen, origin, _auth=None):
        devices[origin].restart()

    monkeypatch.setattr(shelly, "device_rpc", rpc)
    monkeypatch.setattr(shelly, "device_get", get)
    monkeypatch.setattr(shelly, "restart", restart)
    monkeypatch.setattr(shelly_backup, "RESTART_FIRST_S", 0)
    monkeypatch.setattr(shelly_backup, "RESTART_POLL_S", 0.01)
    monkeypatch.setattr(shelly_backup, "RESTART_WAIT_S", 0.2)
    return devices


@pytest.fixture
def one(shellys):
    """One Gen2+ fake at the address of the targets."""
    shellys["http://192.168.1.10"] = FakeShelly()
    return shellys["http://192.168.1.10"]


@pytest.fixture
async def backed(mgr, net, shellys):
    """A checked manager whose three devices are fake Shellys."""
    for device_id, (_name, ip, _v, _o) in net.devices.items():
        shellys[f"http://{ip}"] = FakeShelly("AABBCC00000" + device_id[-1].upper())
    await mgr.scan()
    return mgr


def manager_with(net, shellys, **options):
    """A manager with other options (like the fixture mgr) whose devices are fake Shellys."""
    m = make_manager(net, **options)

    async def registry():
        await asyncio.sleep(0)
        return net.registry()

    m._registry = registry
    m.changelogs.refresh = net.refresh
    for device_id, (_name, ip, _v, _o) in net.devices.items():
        shellys[f"http://{ip}"] = FakeShelly("AABBCC00000" + device_id[-1].upper())
    return m


def fake(shellys, net, device_id):
    return shellys[f"http://{net.devices[device_id][1]}"]


def backup_events(mgr, event=None):
    return [e for e in mgr.events.entries if e["kind"] == "backup" and (event is None or e["event"] == event)]


# ---------------------------------------------------------------- Files and list (backup_store)

META = {
    "id": "0123456789abcdef",
    "mac": "aa:bb:cc:00:00:0a",
    "gen": 2,
    "at": 1_800_000_000,
    "reason": "manual",
    "device": "Kitchen",
    "model": "SNSW-001X16EU",
    "fw": "1.4.0",
    "parts": {"scripts": 2, "nonsense": 9},
    "digest": "d" * 64,
    "extra": "dropped",
}


def test_sealed_file_roundtrip_and_refusals():
    """A file opens only with its key and unchanged; the content is not readable in it."""
    key = bytes(range(32))
    blob = backup_store.seal(key, {"mac": "AABBCC00000A"}, {"config": {"wifi": {"ssid": "Home-Network"}}})
    assert blob.startswith(backup_store.MAGIC) and b"Home-Network" not in blob and b"ssid" not in blob
    assert backup_store.open_sealed(key, blob, "wrong") == (
        {"mac": "AABBCC00000A"},
        {"config": {"wifi": {"ssid": "Home-Network"}}},
    )
    with pytest.raises(BackupError, match="wrong"):
        backup_store.open_sealed(bytes(32), blob, "wrong")
    for place in (-40, -1, 12):  # ciphertext, tag, header: any change is refused
        changed = bytearray(blob)
        changed[place] ^= 1
        with pytest.raises(BackupError):
            backup_store.open_sealed(key, bytes(changed), "wrong")
    for bad in (
        b"",
        b"PK\x03\x04" + bytes(100),
        backup_store.MAGIC + (9999).to_bytes(4, "big") + bytes(100),
        bytes(backup_store.MAX_FILE + 1),
    ):
        with pytest.raises(BackupError):
            backup_store.read_header(bad)
    other_version = blob.replace(b'"format_version":1', b'"format_version":2')
    with pytest.raises(BackupError, match="not supported"):
        backup_store.read_header(other_version)
    with pytest.raises(BackupError, match="not downloaded with a password"):
        backup_store.password_salt(backup_store.read_header(blob)[0])


def test_meta_is_reduced_to_known_fields():
    clean = backup_store.clean_meta(dict(META))
    assert clean["mac"] == "AABBCC00000A" and "extra" not in clean
    assert clean["parts"] == {"settings": True, "scripts": 2, "schedules": 0, "webhooks": 0}
    gen1 = backup_store.clean_meta({**META, "gen": 1, "parts": {"actions": 3, "partial": True}, "reason": "x"})
    assert gen1["parts"] == {"settings": True, "actions": 3, "partial": True} and gen1["reason"] == "manual"
    for bad in ({"mac": "nope"}, {"at": "now"}, {"gen": 0}):
        with pytest.raises(BackupError):
            backup_store.clean_meta({**META, **bad})


def test_store_keeps_lists_and_prunes(tmp_path):
    """Files are private and encrypted; the newest N that are not kept stay; the list
    survives a restart and is built again from the files if it is lost."""
    store = BackupStore(tmp_path / "b")
    for n in range(5):
        store.add({**META, "id": f"{n:016x}", "at": META["at"] + n}, {"config": {"n": n}})
    assert [e["at"] - META["at"] for e in store.of("AA-BB-CC-00-00-0A")] == [4, 3, 2, 1, 0]
    assert (tmp_path / "b" / "key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "b" / f"{0:016x}.sfmb").stat().st_mode & 0o777 == 0o600
    store.set_kept(f"{0:016x}", True)
    removed = store.prune("AABBCC00000A", 2)
    assert sorted(e["at"] - META["at"] for e in removed) == [1, 2]
    assert [e["at"] - META["at"] for e in store.entries] == [4, 3, 0] and store.totals()["count"] == 3
    assert not (tmp_path / "b" / f"{1:016x}.sfmb").exists()
    with pytest.raises(BackupError, match="stored already"):
        store.add({**META, "id": f"{4:016x}"}, {})
    again = BackupStore(tmp_path / "b")
    assert again.entries == store.entries and again.load(f"{4:016x}")[1] == {"config": {"n": 4}}
    (tmp_path / "b" / "index.json").write_text("{broken")
    rebuilt = BackupStore(tmp_path / "b")
    assert [e["id"] for e in rebuilt.entries] == [e["id"] for e in store.entries]
    assert not any(e["kept"] for e in rebuilt.entries)  # the marks lived in the list only
    assert rebuilt.known({"id": "f" * 16, "mac": "AABBCC00000A", "at": META["at"] + 4, "digest": "d" * 64})
    assert not rebuilt.known({"id": "f" * 16, "mac": "AABBCC00000A", "at": 1, "digest": "d" * 64})
    with pytest.raises(BackupError):
        rebuilt.load("../../etc/passwd")


def test_lost_key_is_reported_and_files_stay(tmp_path):
    store = BackupStore(tmp_path / "b")
    store.add(dict(META), {"config": {}})
    (tmp_path / "b" / "key").unlink()
    (tmp_path / "b" / "index.json").unlink()
    fresh = BackupStore(tmp_path / "b")
    assert fresh.entries == [] and "cannot be read" in fresh.warning
    assert (tmp_path / "b" / f"{META['id']}.sfmb").exists()


def test_password_copy_opens_with_the_password_only(tmp_path):
    store = BackupStore(tmp_path / "b")
    store.add(dict(META), {"config": {"a": 1}}, kept=True)
    salt = bytes(16)
    key = backup_store.derive_key("ABCD-EFGH", salt)
    blob = store.sealed_copy(META["id"], key, salt)
    header, _ = backup_store.read_header(blob)
    assert backup_store.password_salt(header) == salt
    meta, data = backup_store.open_sealed(key, blob, "wrong")
    assert data == {"config": {"a": 1}} and meta["kept"] is True and meta["id"] == META["id"]
    with pytest.raises(BackupError, match="wrong"):
        backup_store.open_sealed(store.key, blob, "wrong")


# ---------------------------------------------------------------- Device side, Gen2+ (shelly_backup)


async def test_read_leaves_out_secrets_and_the_ha_script(one):
    """Passwords are removed, the Bluetooth proxy script of Home Assistant is no part,
    and code read in parts that cut a character is complete."""
    data = await shelly_backup.read(target_of(one))
    assert "pass" not in data["config"]["mqtt"] and "hunter2" not in json.dumps(data)
    assert [s["name"] for s in data["scripts"]] == ["auto-light.js"]
    assert data["scripts"][0]["code"] == SCRIPT and data["scripts"][0]["running"] is True
    assert len(data["schedules"]) == 1 and len(data["webhooks"]) == 1
    assert shelly_backup.summary(2, data) == {"settings": True, "scripts": 1, "schedules": 1, "webhooks": 1}


async def test_digest_ignores_what_changes_by_itself(one):
    before = await shelly_backup.read(target_of(one))
    one.config["sys"]["cfg_rev"] += 5
    one.config["sys"]["device"]["fw_id"] = "20260601-1/1.5.0"
    one.scripts[1]["running"] = False
    same = await shelly_backup.read(target_of(one))
    assert shelly_backup.digest(2, before) == shelly_backup.digest(2, same)
    one.config["switch:0"]["initial_state"] = "on"
    assert shelly_backup.digest(2, before) != shelly_backup.digest(2, await shelly_backup.read(target_of(one)))


async def test_compare_lists_differences_per_part(one):
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"].update(initial_state="restore_last", auto_off_delay=300.0)
    one.config["wifi"]["sta"].update(ssid="Guest", is_open=True)
    one.config["sys"]["device"]["profile"] = "cover"
    del one.config["input:0"]
    one.scripts[1]["code"] = SCRIPT + "// more\n"
    one.scripts[5] = {"name": "test.js", "enable": False, "running": False, "code": "x"}
    one.jobs[0]["timespec"] = "0 0 23 * * *"
    one.hooks.clear()
    diff = shelly_backup.compare(2, backup, await shelly_backup.read(target_of(one)))
    assert [(r["key"], r["device"], r["backup"]) for r in diff["settings"]["rows"]] == [
        ("switch:0 · initial_state", "restore_last", "off"),
        ("switch:0 · auto_off_delay", "300.0", "60.0"),
    ]
    assert diff["settings"]["skipped"] == [
        {"key": "sys · device.profile", "reason": "fixed"},
        {"key": "input:0 · type", "reason": "unknown"},
    ]
    assert shelly_backup.public_rows(diff["network"]["rows"]) == [
        {"key": "wifi · sta.ssid", "device": "Guest", "backup": "Home", "wifi_name": True}
    ]
    assert not any("wifi_name" in r for r in diff["settings"]["rows"])
    scripts = {r["key"]: r for r in shelly_backup.public_rows(diff["scripts"]["rows"])}
    assert scripts["auto-light.js"] == {
        "key": "auto-light.js",
        "state": "changed",
        "device_lines": 6,
        "backup_lines": 5,
        "autostart": True,
    }
    assert scripts["test.js"] == {"key": "test.js", "state": "extra", "device_lines": 1}
    assert [(r["state"], r["key"]) for r in diff["schedules"]["rows"]] == [("changed", "0 0 22 * * *")]
    assert [(r["state"], r["key"], r["backup"]) for r in diff["webhooks"]["rows"]] == [
        ("missing", "Motion", "input.toggle_on · 0")
    ]
    assert "item" not in shelly_backup.public_rows(diff["webhooks"]["rows"])[0]


async def test_restore_makes_the_chosen_parts_equal(one):
    """Settings, scripts, schedules and webhooks are written and read back; the network
    settings stay as they are unless named; a restart starts the scripts again."""
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"].update(initial_state="restore_last", auto_off_delay=300.0)
    one.config["wifi"]["sta"]["ssid"] = "Guest"
    one.scripts[1].update(code="broken", running=False)
    one.scripts[5] = {"name": "test.js", "enable": True, "running": True, "code": "x"}
    one.jobs.append({"id": 9, "enable": True, "timespec": "@sunset", "calls": []})
    one.hooks[0]["urls"] = ["http://192.168.1.9/x"]
    one.restart_for.add("switch:0")
    steps = []
    result = await shelly_backup.restore(
        target_of(one), backup, ["webhooks", "scripts", "settings", "schedules"], steps.append
    )
    assert steps == ["settings", "scripts", "schedules", "webhooks", "restart", "verify"]
    assert [(p["part"], p["total"], p["open"]) for p in result["parts"]] == [
        ("settings", 2, []),
        ("scripts", 2, []),
        ("schedules", 1, []),
        ("webhooks", 1, []),
    ]
    assert result["restart"] is True and result["confirmed"] and result["running"] == 1 and result["lost"] is None
    assert one.config["switch:0"]["initial_state"] == "off" and one.config["wifi"]["sta"]["ssid"] == "Guest"
    assert one.scripts[1]["code"] == SCRIPT and one.scripts[1]["running"] and 5 not in one.scripts
    assert shelly.BLE_PROXY_SCRIPT in [s["name"] for s in one.scripts.values()]  # never removed
    assert [j["id"] for j in one.jobs] == [1] and one.hooks[0]["urls"] == backup["webhooks"][0]["urls"]
    assert one.restarts == 1 and "WiFi.SetConfig" not in one.calls
    again = await shelly_backup.restore(target_of(one), backup, ["network"], steps.append)
    assert one.config["wifi"]["sta"]["ssid"] == "Home" and again["parts"][0]["total"] == 1
    assert "WiFi.SetConfig" in one.calls


async def test_refused_values_are_named_and_the_rest_is_written(one):
    """One refused value does not keep the others of its component from being
    restored; what the device does not report as in the backup stays open."""
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"].update(initial_state="on", auto_off=True, auto_off_delay=1.0)
    one.hooks.clear()
    one.refused |= {"auto_off", "input.toggle_on"}
    result = await shelly_backup.restore(target_of(one), backup, ["settings", "webhooks"], lambda _s: None)
    settings, webhooks = result["parts"]
    assert settings["total"] == 3 and [o["key"] for o in settings["open"]] == ["switch:0 · auto_off"]
    assert "invalid value" in settings["open"][0]["error"]
    assert one.config["switch:0"]["initial_state"] == "off" and one.config["switch:0"]["auto_off_delay"] == 60.0
    assert webhooks["open"][0]["key"] == "Motion" and "unknown event" in webhooks["open"][0]["error"]
    assert result["restart"] is None and result["confirmed"]


async def test_restore_reports_a_device_that_stops_answering(one):
    backup = await shelly_backup.read(target_of(one))
    one.config["wifi"]["sta"]["ssid"] = "Guest"
    real = one.rpc

    async def rpc(method, params=None):
        answer = await real(method, params)
        if method == "WiFi.SetConfig":
            one.down = True  # the device left for the other network
        return answer

    one.rpc = rpc
    result = await shelly_backup.restore(target_of(one), backup, ["network"], lambda _s: None)
    assert result["confirmed"] is False and result["lost"] == "TimeoutError"
    assert [o["key"] for o in result["parts"][0]["open"]] == ["wifi · sta.ssid"]
    one.down = False
    with pytest.raises(AuthError):
        one.rpc = lambda *_a: (_ for _ in ()).throw(AuthError("The Shelly rejected the password"))
        await shelly_backup.restore(target_of(one), backup, ["settings"], lambda _s: None)


async def test_new_scripts_are_created_in_small_parts(one):
    backup = await shelly_backup.read(target_of(one))
    long_code = "// ☀\n" + "let x = 'ä';\n" * 400
    backup["scripts"].append({"id": 7, "name": "long.js", "enable": False, "running": True, "code": long_code})
    result = await shelly_backup.restore(target_of(one), backup, ["scripts"], lambda _s: None)
    created = next(s for s in one.scripts.values() if s["name"] == "long.js")
    assert created["code"] == long_code and created["running"] and not created["enable"]
    assert one.calls.count("Script.PutCode") > 4 and result["running"] == 2 and result["parts"][0]["open"] == []


# ---------------------------------------------------------------- Device side, Gen1


async def test_gen1_backup_and_restore(shellys):
    """Gen1: /settings without secrets and state, the actions, and the endpoints that
    write them back; what the device ignores is named."""
    device = shellys["http://192.168.1.10"] = FakeGen1()
    target = target_of(device, gen=1)
    backup = await shelly_backup.read(target)
    assert "password" not in backup["settings"]["login"] and "key" not in backup["settings"]["wifi_ap"]
    assert shelly_backup.summary(1, backup) == {"settings": True, "actions": 0}
    first = shelly_backup.digest(1, backup)
    device.settings.update(time="12:01", unixtime=1700000060)
    device.settings["relays"][0]["ison"] = True
    assert shelly_backup.digest(1, await shelly_backup.read(target)) == first
    device.settings.update(led_status_disable=True, mode="roller")
    device.settings["relays"][0].update(default_state="last", auto_off=300.0, schedule_rules=[])
    device.settings["mqtt"]["enable"] = True
    device.settings["coiot"]["update_period"] = 60
    device.settings["sntp"]["enabled"] = False
    device.settings["wifi_sta"]["ssid"] = "Guest"
    device.settings["wifi_ap"]["enabled"] = True
    device.actions["out_on_url"][0] = {"index": 0, "urls": ["http://192.168.1.2/on"], "enabled": True}
    diff = shelly_backup.compare(1, backup, await shelly_backup.read(target))
    assert {r["key"] for r in diff["settings"]["rows"]} == {
        "led_status_disable",
        "relays.0 · default_state",
        "relays.0 · auto_off",
        "relays.0 · schedule_rules",
        "mqtt · enable",
        "coiot · update_period",
        "sntp · enabled",
    }
    assert diff["settings"]["skipped"] == [{"key": "mode", "reason": "fixed"}]
    assert [(r["key"], r.get("wifi_name")) for r in diff["network"]["rows"]] == [("wifi_sta · ssid", True)]
    assert [(r["key"], r["device"], r["backup"]) for r in diff["actions"]["rows"]] == [
        ("out_on_url (0)", "http://192.168.1.2/on", "–")
    ]
    device.calls.clear()
    result = await shelly_backup.restore(target, backup, ["settings", "actions"], lambda _s: None)
    settings, actions = result["parts"]
    assert [o["key"] for o in settings["open"]] == ["sntp · enabled"] and actions["open"] == []
    assert device.settings["relays"][0]["schedule_rules"] == ["0700-0123456-on"]
    assert device.settings["wifi_sta"]["ssid"] == "Guest" and device.settings["wifi_ap"]["enabled"] is True
    writes = {path: dict(pairs) for path, pairs in device.calls if pairs}
    assert writes["/settings/relay/0"] == {
        "default_state": "off",
        "auto_off": "0.0",
        "schedule_rules": "0700-0123456-on",
    }
    assert writes["/settings"]["mqtt_enable"] == "false" and writes["/settings"]["coiot_update_period"] == "15"
    assert ("urls[]", "") in next(pairs for path, pairs in device.calls if path == "/settings/actions" and pairs)
    assert result["restart"] is None and result["running"] is None


async def test_gen1_without_readable_actions_is_partial(shellys):
    device = shellys["http://192.168.1.10"] = FakeGen1(actions=False)
    backup = await shelly_backup.read(target_of(device, gen=1))
    assert backup["actions"] is None
    assert shelly_backup.summary(1, backup) == {"settings": True, "actions": 0, "partial": True}
    assert shelly_backup.compare(1, backup, backup)["actions"] == {"rows": [], "skipped": []}


# ---------------------------------------------------------------- Manager: storing


async def test_backup_now_stores_logs_and_applies_the_retention(net, shellys):
    """A manual backup is stored encrypted and logged; more than backup_keep are
    removed oldest first (logged with the user "App"); a kept one does not count."""
    mgr = manager_with(net, shellys, backup_keep=2)
    await mgr.scan()
    first = await mgr.backup_now(A, "alice")
    assert first["reason"] == "manual" and first["fw"] == "1.4.0" and first["size"] > 100
    assert first["parts"] == {"settings": True, "scripts": 1, "schedules": 1, "webhooks": 1}
    stored = backup_events(mgr, "stored")[-1]
    assert stored["device"] == "Kitchen" and stored["user"] == "alice" and stored["size"] == first["size"]
    raw = b"".join(p.read_bytes() for p in (net.data / "backups").glob("*.sfmb"))
    assert b"auto-light" not in raw and b"Kitchen" not in raw
    mgr.keep_backup(first["id"], True)
    for _ in range(3):
        await mgr.backup_now(A, "alice")
    entries = mgr.backups.of("AABBCC00000A")
    assert len(entries) == 3 and sum(e["kept"] for e in entries) == 1
    pruned = backup_events(mgr, "pruned")
    assert len(pruned) == 1 and pruned[0]["user"] == "App" and pruned[0]["keep"] == 2
    state = next(d for d in mgr.public_state()["devices"] if d["id"] == A)
    assert state["backups"]["count"] == 3 and state["backups"]["kept"] == 1
    assert mgr.public_state()["backup_totals"]["devices"] == 1
    view = mgr.backups_view()
    kitchen = next(d for d in view["devices"] if d["id"] == A)
    assert len(kitchen["backups"]) == 3 and kitchen["none"] is None and "digest" not in kitchen["backups"][0]
    assert next(d for d in view["devices"] if d["id"] == B)["none"] == {"reason": None}


async def test_failed_backups_name_the_reason(backed, net, shellys):
    """Not reachable, asleep (battery device) and a missing password are told apart;
    a removed device is not backed up; nothing is stored."""
    mgr = backed
    fake(shellys, net, A).down = True
    with pytest.raises(ShellyError, match="Device not reachable"):
        await mgr.backup_now(A, "alice")
    assert mgr.devices[A]["backup_failed"]["reason"] == "unreachable"
    assert backup_events(mgr, "failed")[-1]["severity"] == "error"  # it should answer
    mgr.devices[A]["battery"] = True
    with pytest.raises(ShellyError):
        await mgr.backup_now(A, "alice")
    assert next(d for d in mgr.backups_view()["devices"] if d["id"] == A)["none"]["reason"] == "asleep"
    assert backup_events(mgr, "failed")[-1]["severity"] == "warning"  # it sleeps
    fake(shellys, net, A).down = False

    async def locked(_method, _params=None):
        raise AuthError("The Shelly requires a password – store it in the device's password dialog")

    fake(shellys, net, B).rpc = locked
    with pytest.raises(ShellyError, match="requires a password"):
        await mgr.backup_now(B, "alice")
    assert mgr.devices[B]["backup_failed"]["reason"] == "password"
    assert backup_events(mgr, "failed")[-1]["severity"] == "error"
    mgr.devices[C]["removed"] = 1
    with pytest.raises(ShellyError, match="Removed from Home Assistant"):
        await mgr.backup_now(C, "alice")
    assert mgr.backups.entries == []
    await mgr.backup_now(A, "alice")
    assert "backup_failed" not in mgr.devices[A]


async def test_back_up_now_for_several_devices(backed, net, shellys):
    mgr = backed
    fake(shellys, net, B).down = True
    with pytest.raises(ValueError):
        mgr.reserve_backup_run([A, A])
    with pytest.raises(ValueError):
        mgr.reserve_backup_run(["f" * 32])
    ids = mgr.reserve_backup_run("all")
    with pytest.raises(ShellyError, match="in progress"):
        mgr.reserve_backup_run([A])
    assert sorted(ids) == [A, B, C] and mgr.public_state()["backup_job"]["total"] == 3
    await mgr.run_backups(ids, "alice")
    job = mgr.public_state()["backup_job"]
    assert job["finished"] and job["done"] == 3
    results = {r["id"]: r for r in job["results"]}
    assert results[A]["result"] == "stored" and results[A]["size"] and results[B]["result"] == "unreachable"
    assert "Device not reachable" in results[B]["detail"] and len(mgr.backups.entries) == 2
    mgr.clear_backup_run()
    assert mgr.backup_job is None
    # One log entry for the run names every device; none per device
    assert not backup_events(mgr, "stored") and not backup_events(mgr, "failed")
    (entry,) = backup_events(mgr, "run")
    assert entry["severity"] == "error" and entry["user"] == "alice" and entry["devices"] == 3
    assert entry["stored"] == 2 and entry["result"] == "2 stored · 1 not backed up"
    findings = {f["device"]: f for f in entry["findings"]}
    assert [f["device"] for f in entry["findings"]] == sorted(findings, key=str.casefold)
    kitchen = findings[mgr.devices[A]["name"]]
    assert kitchen["result"] == "stored" and kitchen["severity"] == "success" and kitchen["size"] == results[A]["size"]
    assert kitchen["parts"] == results[A]["parts"] and "id" not in kitchen and "detail" not in kitchen
    down = findings[mgr.devices[B]["name"]]
    assert down["result"] == "unreachable" and down["severity"] == "error" and "Device not reachable" in down["detail"]


async def test_backup_run_rates_never_checked_devices_like_their_check(backed):
    """A device without a successful check is not backed up: an error if its check
    rated it an error (e.g. it did not answer), a warning otherwise (a battery device
    asleep)."""
    mgr = backed
    for device_id in (B, C):
        mgr.devices[device_id].pop("origin")
    mgr.devices[B].update(status=manager_core.STATUS_ERROR, error_level="error")
    mgr.devices[C].update(status=manager_core.STATUS_ASLEEP, error_level="warning", battery=True)
    await mgr.run_backups(mgr.reserve_backup_run([A, B, C]), "alice")
    (entry,) = backup_events(mgr, "run")
    findings = {f["device"]: f for f in entry["findings"]}
    assert findings[mgr.devices[B]["name"]]["severity"] == "error"
    assert findings[mgr.devices[C]["name"]]["severity"] == "warning"
    assert "not been checked" in findings[mgr.devices[C]["name"]]["detail"] and entry["severity"] == "error"


async def test_backup_run_names_sleeping_devices_and_an_interruption(backed, net, shellys):
    """A battery device that does not answer is counted as asleep; a run the app stops
    is logged with what was done."""
    mgr = backed
    fake(shellys, net, B).down = True
    mgr.devices[B]["battery"] = True
    await mgr.run_backups(mgr.reserve_backup_run([A, B]), "alice")
    (entry,) = backup_events(mgr, "run")
    assert entry["result"] == "1 stored · 1 asleep" and entry["severity"] == "warning"
    assert {f["device"]: f["result"] for f in entry["findings"]} == {
        mgr.devices[A]["name"]: "stored",
        mgr.devices[B]["name"]: "asleep",
    }
    mgr.clear_backup_run()
    fake(shellys, net, B).down = False
    mgr.devices[B]["battery"] = False
    await mgr.run_backups(mgr.reserve_backup_run([A, B]), "alice")
    assert backup_events(mgr, "run")[-1]["severity"] == "success"
    mgr.clear_backup_run()

    async def stopped(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise asyncio.CancelledError

    mgr._backup_result = stopped
    with pytest.raises(asyncio.CancelledError):
        await mgr.run_backups(mgr.reserve_backup_run([A]), "alice")
    last = backup_events(mgr, "run")[-1]
    assert last["interrupted"] is True and last["severity"] == "warning" and mgr.backup_job["finished"]
    assert last["result"] == "0 stored · interrupted – the app stopped" and "findings" not in last


# ---------------------------------------------------------------- Manager: installation and schedules


async def test_backup_before_the_installation(net, shellys, monkeypatch, quick_install):
    """With the option on, an installation stores a backup first; if that fails,
    nothing is installed and the reason is shown and logged."""
    mgr = manager_with(net, shellys, backup_before_install=True)
    triggered = []

    async def trigger(_session, _gen, origin, url, _auth=None):
        triggered.append(origin)
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    await mgr.scan()
    assert mgr.public_state()["backup_before_install"] is True
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    entry = mgr.backups.of("AABBCC00000A")[0]
    assert entry["reason"] == "update" and entry["note"] == "1.4.0 → 1.5.0" and entry["fw"] == "1.4.0"
    kinds = [(e["kind"], e.get("event")) for e in mgr.events.entries if e["kind"] in ("backup", "install")]
    assert kinds == [("backup", "stored"), ("install", "started"), ("install", "confirmed")]
    fake(shellys, net, C).down = True
    assert await mgr.install(C, mgr.devices[C]["sha256"], "alice") is False
    assert triggered == ["http://192.168.1.10"]
    assert "Backup before the installation failed – nothing was installed" in mgr.devices[C]["message"]
    failed = mgr.events.entries[-1]
    assert (failed["kind"], failed["event"], failed["severity"], failed["reason"]) == (
        "backup",
        "failed",
        "error",
        "update",
    )
    assert mgr.devices[C]["install_failed"] is True  # the file stays provided for a retry


async def test_schedule_backup_stores_only_changes(backed, net, shellys):
    """A backup run stores what changed since the newest backup; a sleeping battery
    device is counted as asleep (not failed, not left out, the run stays a success); a
    device that does not answer makes the run an error; one log entry per run."""
    mgr = backed
    data = {
        "name": "Weekly backup",
        "action": "backup",
        "time": "03:30",
        "repeat": {"kind": "daily"},
        "devices": "all",
        "pause": 0,
    }
    item = mgr.create_schedule(data, "alice")
    mgr.devices[C].update(battery=True)
    fake(shellys, net, C).down = True
    mgr.start_due(item["next"])
    await asyncio.gather(*list(mgr._timetable_tasks))
    run = mgr.events.entries[-1]
    assert run["kind"] == "schedule" and run["action"] == "Backup"
    assert run["result"] == "2 stored · 0 unchanged · 1 asleep · 0 not backed up" and run["severity"] == "success"
    assert "left_out" not in run and mgr.schedules[0]["last"]["counts"]["asleep"] == 1
    assert {f["device"]: (f["result"], f["severity"]) for f in run["findings"]}["Garage"] == ("asleep", "warning")
    assert not backup_events(mgr) and not mgr.notified
    entry = mgr.backups.of("AABBCC00000A")[0]
    assert entry["reason"] == "schedule" and entry["note"] == "Weekly backup"
    fake(shellys, net, A).config["switch:0"]["initial_state"] = "on"
    fake(shellys, net, B).down = True
    mgr.start_due(mgr.schedules[0]["next"])
    await asyncio.gather(*list(mgr._timetable_tasks))
    run = mgr.events.entries[-1]
    assert run["result"] == "1 stored · 0 unchanged · 1 asleep · 1 not backed up" and run["severity"] == "error"
    assert mgr.schedules[0]["last"]["severity"] == "error"  # a device that should answer did not
    assert len(mgr.backups.of("AABBCC00000A")) == 2 and "Hall" in mgr.notified[-1][1]
    fake(shellys, net, B).down = False
    mgr.start_due(mgr.schedules[0]["next"])
    await asyncio.gather(*list(mgr._timetable_tasks))
    assert mgr.events.entries[-1]["result"] == "0 stored · 2 unchanged · 1 asleep · 0 not backed up"
    assert mgr.schedules[0]["last"]["counts"]["unchanged"] == 2 and len(mgr.backups.entries) == 3


# ---------------------------------------------------------------- Manager: restoring


async def test_preview_and_restore(backed, net, shellys):
    """The preview lists the differences without changing anything; the restore stores
    a backup "Before restore" first, writes the chosen parts and logs the result."""
    mgr = backed
    device = fake(shellys, net, A)
    backup = await mgr.backup_now(A, "alice")
    device.config["switch:0"]["initial_state"] = "on"
    device.config["wifi"]["sta"]["ssid"] = "Guest"
    device.scripts[1]["code"] = "changed"
    calls = len(device.calls)
    preview = await mgr.preview_restore(backup["id"])
    assert preview["reachable"] and preview["other"] is None and preview["fw_differs"] is False
    assert preview["protection"] == "same" and preview["device"]["name"] == "Kitchen"
    counts = {p["part"]: len(p["rows"]) for p in preview["parts"]}
    assert counts == {"settings": 1, "scripts": 1, "schedules": 0, "webhooks": 0, "network": 1}
    assert preview["parts"][0]["rows"] == [{"key": "switch:0 · initial_state", "device": "on", "backup": "off"}]
    assert preview["parts"][-1]["rows"] == [
        {"key": "wifi · sta.ssid", "device": "Guest", "backup": "Home", "wifi_name": True}
    ]
    assert not any("SetConfig" in c or "Put" in c for c in device.calls[calls:])
    with pytest.raises(ValueError, match="not confirmed"):
        mgr.reserve_restore(backup["id"], ["network"], False, "alice")
    with pytest.raises(ValueError):
        mgr.reserve_restore(backup["id"], ["actions"], False, "alice")
    mgr.reserve_restore(backup["id"], ["scripts", "settings"], False, "alice")
    state = mgr.public_state()
    assert state["restore"]["steps"] == ["backup", "settings", "scripts", "verify"] and "user" not in state["restore"]
    assert "Restoring a backup" in state["busy"] and state["busy_note"] == "Restoring a backup"
    for blocked in (
        lambda: mgr.reserve_check("all"),
        lambda: mgr.reserve_check("device", A),
        lambda: mgr._settings_device(A),
        lambda: mgr.reserve_restore(backup["id"], ["settings"], False, "a"),
        lambda: mgr.delete_backup(backup["id"], True),
    ):
        with pytest.raises(ShellyError):
            blocked()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is False
    await mgr.run_restore()
    job = mgr.public_state()["restore"]
    assert job["finished"] and job["error"] is None and job["safety"]
    assert [(p["part"], p["total"], p["open"]) for p in job["result"]["parts"]] == [
        ("settings", 1, []),
        ("scripts", 1, []),
    ]
    assert device.config["switch:0"]["initial_state"] == "off" and device.config["wifi"]["sta"]["ssid"] == "Guest"
    newest = mgr.backups.of("AABBCC00000A")[0]
    assert newest["reason"] == "restore" and len(mgr.backups.entries) == 2
    entry = backup_events(mgr, "restored")[-1]
    assert entry["severity"] == "success" and entry["changes"] == 2 and entry["not_applied"] == 0
    assert entry["restored"] == ["settings", "scripts"] and entry["user"] == "alice"
    assert not mgr.restoring and mgr.public_state()["busy"] is None
    mgr.clear_restore()
    assert mgr.public_state()["restore"] is None
    mgr.reserve_restore(backup["id"], ["network"], True, "alice")
    await mgr.run_restore()
    assert device.config["wifi"]["sta"]["ssid"] == "Home"


async def test_restore_only_to_the_device_of_the_backup(backed, net, shellys, monkeypatch):
    """Another device at the address, a device that does not answer and a failing
    backup "Before restore": nothing is written."""
    mgr = backed
    device = fake(shellys, net, A)
    backup = await mgr.backup_now(A, "alice")
    device.config["switch:0"]["initial_state"] = "on"
    real_info = shelly.device_info

    async def other_info(*args, **kwargs):
        return {**await real_info(*args, **kwargs), "mac": "AABBCC0000FF"}

    monkeypatch.setattr(shelly, "device_info", other_info)
    preview = await mgr.preview_restore(backup["id"])
    assert preview["other"] == {
        "backup_mac": "AABBCC00000A",
        "backup_model": "SNSW-001X16EU",
        "device_mac": "AABBCC0000FF",
        "device_model": "SNSW-001X16EU",
    }
    assert "parts" not in preview
    mgr.reserve_restore(backup["id"], ["settings"], False, "alice")
    await mgr.run_restore()
    assert "Another device" in mgr.restore_job["error"] and device.config["switch:0"]["initial_state"] == "on"
    assert backup_events(mgr, "restore_failed")[-1]["severity"] == "error" and not mgr.restoring
    monkeypatch.setattr(shelly, "device_info", real_info)
    device.down = True
    net.asleep.add(A)
    preview = await mgr.preview_restore(backup["id"])
    assert preview["reachable"] is False and "Device not reachable" in preview["error"]
    net.asleep.clear()
    mgr.reserve_restore(backup["id"], ["settings"], False, "alice")
    await mgr.run_restore()
    assert "backup before the restore failed – nothing was restored" in mgr.restore_job["error"]
    device.down = False
    mgr.devices.pop(A)
    with pytest.raises(ShellyError, match="no longer listed"):
        await mgr.preview_restore(backup["id"])
    orphan = mgr.backups_view()["devices"][-1]
    assert orphan["id"] is None and orphan["name"] == "Kitchen" and len(orphan["backups"]) == 1


# ---------------------------------------------------------------- Manager: download, import, export


async def test_download_and_import_roundtrip(backed, net, shellys):
    """A downloaded file opens only with its password; the import checks the device
    and whether the backup is stored already, and marks it as imported and kept."""
    mgr = backed
    backup = await mgr.backup_now(A, "alice")
    prepared = await mgr.prepare_backup_download(backup["id"], "alice")
    assert prepared["filename"].startswith("backup-plus1-") and prepared["filename"].endswith(".sfmb")
    content, filename = mgr.take_backup_download(prepared["token"])
    assert filename == prepared["filename"] and b"auto-light" not in content
    assert backup_events(mgr, "downloaded")[-1]["user"] == "alice"
    with pytest.raises(ShellyError, match="expired"):
        mgr.take_backup_download(prepared["token"])
    with pytest.raises(BackupError, match="no backup of this app"):
        await mgr.stage_backup_import(A, chunks(b"PK\x03\x04" + bytes(200)), 204)
    with pytest.raises(ValueError, match="too large"):
        await mgr.stage_backup_import(A, chunks(bytes(10)), backup_store.MAX_FILE + 1)
    stored_file = next((net.data / "backups").glob("*.sfmb")).read_bytes()
    with pytest.raises(BackupError, match="not downloaded with a password"):
        await mgr.stage_backup_import(A, chunks(stored_file), len(stored_file))
    staged = await mgr.stage_backup_import(A, chunks(content), len(content))
    with pytest.raises(BackupError, match="Wrong password"):
        await mgr.check_backup_import(staged["token"], "AAAA-BBBB-CCCC-DDDD-EEEE")
    checked = await mgr.check_backup_import(staged["token"], prepared["password"].lower())
    assert checked["known"] is True and checked["other"] is None
    with pytest.raises(ShellyError, match="cannot be imported"):
        mgr.confirm_backup_import(staged["token"], "alice")
    staged = await mgr.stage_backup_import(B, chunks(content), len(content))
    checked = await mgr.check_backup_import(staged["token"], prepared["password"])
    assert checked["other"]["backup_mac"] == "AABBCC00000A" and checked["other"]["device_mac"] == "AABBCC00000B"
    mgr.delete_backup(backup["id"], True, "alice")
    assert backup_events(mgr, "deleted") and mgr.backups.entries == []
    staged = await mgr.stage_backup_import(A, chunks(content), len(content))
    checked = await mgr.check_backup_import(staged["token"], prepared["password"])
    assert checked == {
        "backup": {**backup, "size": len(content), "kept": True, "imported": True},
        "other": None,
        "known": False,
    }
    entry = mgr.confirm_backup_import(staged["token"], "bob")
    assert entry["imported"] and entry["kept"] and entry["at"] == backup["at"] and entry["id"] == backup["id"]
    assert backup_events(mgr, "imported")[-1]["user"] == "bob"
    with pytest.raises(ShellyError):
        mgr.confirm_backup_import(staged["token"], "bob")
    device = fake(shellys, net, A)
    device.config["switch:0"]["initial_state"] = "on"
    mgr.reserve_restore(entry["id"], ["settings"], False, "bob")
    await mgr.run_restore()
    assert device.config["switch:0"]["initial_state"] == "off"


async def test_store_export_carries_the_backups(backed, net, shellys, tmp_path):
    """The store export holds the backups encrypted with the key of the export
    password; the import stores the ones that are missing (the "Kept" mark comes along)."""
    mgr = backed
    first = await mgr.backup_now(A, "alice")
    second = await mgr.backup_now(B, "alice")
    mgr.keep_backup(first["id"], True)
    prepared = await mgr.prepare_export("alice")
    assert prepared["backups"] == 2
    path, _name = await mgr.build_export(prepared["token"], "3.11.0")
    raw = path.read_bytes()
    assert b"Backups/AABBCC00000A/" in raw and b"auto-light" not in raw
    assert mgr.events.entries[-1]["kind"] == "export" and mgr.events.entries[-1]["backups"] == 2
    mgr.delete_backup(first["id"], True, "alice")
    staged = await mgr.stage_import(chunks(raw), len(raw))
    path.unlink()
    summary = await mgr.check_import(staged["token"], prepared["password"])
    assert summary["backups"] == 2 and summary["backups_new"] == 1
    counts = await mgr.confirm_import(staged["token"], "bob")
    assert counts["backups"] == 1 and mgr.events.entries[-1]["backups"] == 1
    restored = mgr.backups.get(first["id"])
    assert restored["kept"] and restored["imported"] and restored["at"] == first["at"]
    assert not mgr.backups.get(second["id"])["imported"]
    assert mgr.backups.load(first["id"])[1]["scripts"][0]["code"] == SCRIPT
    checked = store_transfer.read_export
    with pytest.raises(store_transfer.TransferError):
        target = tmp_path / "x.zip"
        target.write_bytes(raw.replace(b"Backups/AABBCC00000A/", b"Backups/AABBCC00000X/"))
        checked(target, store_transfer.normalize_password(prepared["password"]))


# ---------------------------------------------------------------- API


async def test_http_backups(backed, net, shellys):
    """The API of the backups: lists, backup, keep, download, import, preview, restore
    and delete – with the CSRF and confirmation rules of the other write requests."""
    mgr = backed
    c = await web_client(mgr)
    hdr = {"X-CSRF-Token": c.csrf}
    try:
        assert (await c.post(f"/api/devices/{A}/backup", json={})).status == 403
        r = await c.post(f"/api/devices/{A}/backup", json={}, headers=hdr)
        backup = (await r.json())["backup"]
        assert r.status == 200 and backup["reason"] == "manual"
        listed = await (await c.get("/api/backups")).json()
        kitchen = next(d for d in listed["devices"] if d["id"] == A)
        assert listed["keep"] == 10 and kitchen["backups"][0]["id"] == backup["id"]
        assert "AABBCC" not in json.dumps(listed)
        bid = backup["id"]
        assert (await c.post(f"/api/backups/{'0' * 16}/keep", json={"kept": True}, headers=hdr)).status == 404
        assert (await c.post("/api/backups/nope/delete", json={"confirm": True}, headers=hdr)).status == 404
        assert (await c.post(f"/api/backups/{bid}/keep", json={"kept": "yes"}, headers=hdr)).status == 400
        r = await c.post(f"/api/backups/{bid}/keep", json={"kept": True}, headers=hdr)
        assert (await r.json())["backup"]["kept"] is True
        r = await c.post(f"/api/backups/{bid}/download", json={}, headers=hdr)
        prepared = await r.json()
        assert set(prepared) == {"token", "password", "filename", "size", "expires_in"}
        r = await c.get(f"/api/backups/download/{prepared['token']}")
        content = await r.read()
        assert r.status == 200 and prepared["filename"] in r.headers["Content-Disposition"]
        assert r.headers["Content-Security-Policy"] and len(content) == prepared["size"]
        assert (await c.get(f"/api/backups/download/{prepared['token']}")).status == 409
        assert (await c.get("/api/backups/download/x")).status == 404
        binary = {**hdr, "Content-Type": "application/octet-stream"}
        assert (await c.post(f"/api/devices/{A}/backups/import", json={}, headers=hdr)).status == 415
        r = await c.post(f"/api/devices/{A}/backups/import", data=b"junk" * 50, headers=binary)
        assert r.status == 400 and "no backup of this app" in await r.text()
        r = await c.post(f"/api/devices/{A}/backups/import", data=content, headers=binary)
        token = (await r.json())["token"]
        r = await c.post("/api/backups/import/check", json={"token": token, "password": "WRONG"}, headers=hdr)
        assert r.status == 400 and "Wrong password" in await r.text()
        r = await c.post(
            "/api/backups/import/check", json={"token": token, "password": prepared["password"]}, headers=hdr
        )
        assert (await r.json())["known"] is True
        assert (await c.post("/api/backups/import/confirm", json={"token": token}, headers=hdr)).status == 409
        assert (await c.post("/api/backups/import/cancel", json={"token": token}, headers=hdr)).status == 200
        fake(shellys, net, A).config["switch:0"]["initial_state"] = "on"
        r = await c.post(f"/api/backups/{bid}/preview", json={}, headers=hdr)
        assert [len(p["rows"]) for p in (await r.json())["parts"]] == [1, 0, 0, 0, 0]
        body = {"parts": ["settings"]}
        assert (await c.post(f"/api/backups/{bid}/restore", json=body, headers=hdr)).status == 400
        r = await c.post(f"/api/backups/{bid}/restore", json={"parts": ["network"], "confirm": True}, headers=hdr)
        assert r.status == 400 and "not confirmed" in await r.text()
        r = await c.post(f"/api/backups/{bid}/restore", json={**body, "confirm": True}, headers=hdr)
        assert (await r.json()) == {"started": True}
        for _ in range(200):
            await asyncio.sleep(0.01)
            if mgr.restore_job["finished"]:
                break
        state = await (await c.get("/api/state")).json()
        assert state["restore"]["result"]["parts"][0]["open"] == [] and state["backups_rev"] >= 2
        assert (await c.post("/api/backups/restore/clear", json={}, headers=hdr)).status == 200
        r = await c.post("/api/backups/run", json={"devices": [B, C]}, headers=hdr)
        assert (await r.json()) == {"started": True, "total": 2}
        for _ in range(200):
            await asyncio.sleep(0.01)
            if mgr.backup_job["finished"]:
                break
        assert (await c.post("/api/backups/run", json={"devices": []}, headers=hdr)).status == 400
        assert (await c.post("/api/backups/run/clear", json={}, headers=hdr)).status == 200
        assert (await c.post(f"/api/backups/{bid}/delete", json={}, headers=hdr)).status == 400
        assert (await c.post(f"/api/backups/{bid}/delete", json={"confirm": True}, headers=hdr)).status == 200
        assert (await c.post(f"/api/backups/{bid}/preview", json={}, headers=hdr)).status == 404
    finally:
        await c.close()


# ---------------------------------------------------------------- Log mark and check severity


async def test_error_mark_of_the_log_and_unreachable_devices(mgr, net):
    """The mark of the newest error entry (the "!" of the Logs tab) moves only with an
    error; a device that should answer and does not is an error of the check, a
    sleeping battery device a warning."""
    assert mgr.events.latest("error") == 0
    await mgr.scan()
    assert mgr.events.latest() > 0 and mgr.public_state()["log_latest_error"] == 0
    net.asleep.update({A, C})
    mgr.devices[C]["battery"] = True
    await mgr.scan()
    findings = {f["device"]: f["severity"] for f in mgr.events.entries[-1]["findings"]}
    assert findings["Kitchen"] == "error" and findings["Garage"] == "warning"
    assert mgr.events.entries[-1]["severity"] == "error" and mgr.devices[A]["error_level"] == "error"
    mark = mgr.events.latest("error")
    assert mark == mgr.events.latest() == mgr.public_state()["log_latest_error"]
    mgr.events.add("setting", device="x")
    assert mgr.events.latest("error") == mark < mgr.events.latest()
    assert manager_backups.NONE_ASLEEP == "asleep"
