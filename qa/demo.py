"""Local demo server with fake data for UI tests (no network access).

Usage: python demo.py <app_dir> <port> <scenario: idle|busy|missing|features|first|public|docs|diagnosis>
missing: download_base_url not set; public: download_base_url is a public IP address (saved
before it was refused) and one device installs with device progress; features: idle
plus stored older versions (one of them uploaded) for downgrade and upload checks, and backups of device
settings with Shellys in memory that answer a backup and a restore; docs: features with
fewer open updates and other models, for the screenshots of the documentation; diagnosis: features
plus Home Assistant areas, a device that did not answer, a comparison after an update, the card
"Getting started" and a fake connection test (automatic: reachable, a chosen device: not reachable).
Serves the UI app below /ui/ and a same-origin wrapper /wrap that embeds it in an
iframe (like the HA Ingress panel)."""

import asyncio
import hashlib
import sys
import tempfile
import time
from pathlib import Path

app_dir, port, scenario = sys.argv[1], int(sys.argv[2]), sys.argv[3]
diagnosis = scenario == "diagnosis"
if diagnosis:
    scenario = "features"
sys.path.insert(0, app_dir)
import data_paths
import manager
from shelly import ShellyError
import web_ui
from aiohttp import web
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

data = Path(tempfile.mkdtemp())
for name, value in {"STATE_FILE": data / "state.json", "FW_DIR": data / "fw", "CREDENTIALS_FILE": data / "c.json",
                    "CHANGELOG_FILE": data / "cl.json", "EVENTS_FILE": data / "ev.json",
                    "BACKUP_DIR": data / "backups", "SETTINGS_FILE": data / "settings.json"}.items():
    setattr(data_paths, name, value)

opts = {"check_interval_days": 7, "check_time": "02:45", "download_base_url": "http://192.168.1.2:8098", "restrict_downloads_to_devices": True,
        "gen1_updates_enabled": True, "max_parallel_installs": 2,
        "log_retention_days": 30, "log_entry_limit": 1000, "allow_downgrade": True, "import_max_mb": 100}
if scenario == "public":
    opts["download_base_url"] = "http://203.0.113.10:8098"
elif scenario == "missing":   # also shows downgrades switched off
    del opts["download_base_url"]
    del opts["allow_downgrade"]
m = manager.Manager(opts, session=None, token="t")
# Fresh installation: only the "first" demo shows the first-start question
m.first_start_open = scenario == "first"
m.getting_started = False  # demos play an existing installation; the diagnosis scenario shows the card
now = int(time.time())
names = ["Kitchen light", "Living room ceiling with a really long device name", "Hall", "Garage door", "Pool pump",
         "Office desk", "Bedroom shutter", "Heat pump", "Garden plug", "Basement", "Attic fan", "Terrace",
         "Washing machine", "Dryer", "Fridge", "Boiler", "Entrance", "Bathroom", "Kids room", "Guest room",
         "Workshop", "Carport", "Sauna", "Wallbox meter", "Doorbell"]
statuses = ["ready", "available", "up-to-date", "up-to-date", "error", "unconfirmed", "up-to-date", "ready",
            "skipped", "unregistered"]
content = b"x" * 70000
sha = hashlib.sha256(content).hexdigest()
(data / "fw").mkdir(exist_ok=True)
(data / "fw" / f"{sha}.zip").write_bytes(content)
m._register_firmware(f"{sha}.zip", "Plus1", 2, "1.5.0", sha, len(content), True)
# Changelog kept with the stored firmware (Firmware tab, installation dialog)
m.firmware[f"{sha}.zip"]["changelog"] = {"date": "2026-10-01", "entries": [
    {"category": "Security", "text": "Fixed a vulnerability in the web server", "models": []},
    {"category": "Fixed", "text": "Wi-Fi reconnect after a router restart", "models": ["ShellyPlus1"]},
    {"category": "Added", "text": "Support for longer device names in the web interface and in all exported configuration files", "models": []}]}
for i, n in enumerate(names):
    st = statuses[i % len(statuses)]
    gen = 1 if st == "skipped" else (2 + i % 2)
    m.devices[f"{i:032x}"] = {
        "name": n, "shelly_name": f"shelly-{i}", "gen": gen, "model": "SNSW-001X16EU", "type": "Plus1" if gen > 1 else "SHSW-1",
        "version": "1.4.0", "build": "20250101-000000/1.4.0-gdemo", "mac": f"AABBCC00{i:04X}", "offer_version": "1.5.0" if st in ("ready", "available") else None, "status": st,
        "message": "Device not reachable" if st == "error" else "", "origin": f"http://192.168.1.{10 + i}",
        "last_check": now - 3600, "last_success": now - (90000 if st == "error" else 3600),
        "last_update": now - 86400 * 3 if i % 3 == 0 else None, "ntp": "time.local", "eco": i % 2 == 0,
        # power-on action: "restore_last" (green icon) or another value (red icon); every 4th device has no output
        "outputs": [{"key": "switch:0", "name": None, "aop": "restore_last" if i % 8 < 4 else "off", "options": ["off", "on", "restore_last"]}] if i % 4 else [],
        "file": f"{sha}.zip" if st == "ready" else None, "sha256": sha if st == "ready" else None, "size": len(content),
        "pending_version": "1.5.0" if st == "ready" else None,
        # Source of the provided update – the first device got it from the store
        "offer_source": ("store" if i == 0 else "manufacturer") if st == "ready" else None,
        "source_url": "https://fwcdn.shelly.cloud/gen2/Plus1/demo" if st == "ready" and i else None,
        # 3.0 feature states: on/off, unsupported and unknown (None) variants
        "ap": [False, True, None, "unsupported"][i % 4], "ble": "unsupported" if gen == 1 else [True, False, None][i % 3],
        "actions": [False, True, None][i % 3], "schedules": [True, False, "unsupported", None][i % 4],
        "auth_enabled": [False, True, None][i % 3], "ignored": i in (3, 12),
        # websocket debug log: off (grey), on (yellow), unknown; Gen1 has none
        "debug_ws": "unsupported" if gen == 1 else [False, True, False, None][i % 4],
    }
if scenario in ("features", "docs"):  # older versions for the downgrade list, one uploaded
    for version, source in (("1.3.1", "download"), ("1.3.0", "download"), ("1.2.0", "upload")):
        older = f"Plus1 {version} ".encode() * 6000
        older_sha = hashlib.sha256(older).hexdigest()
        (data / "fw" / f"{older_sha}.zip").write_bytes(older)
        m._register_firmware(f"{older_sha}.zip", "Plus1", 2, version, older_sha, len(older), True, source=source,
                             sha_checked=False)
        m.firmware[f"{older_sha}.zip"]["downloaded"] = now - 86400 * (40 if source == "upload" else 20)
m.last_scan = now - 3600
m.events.add("check", trigger="manual", devices=25, duration=41.2, result="ok", findings=[], user="demo")
m.events.add("check", trigger="device", devices=1, duration=1.2, result="ok", findings=[], user="demo")
m.events.add("check", severity="error", trigger="manual", devices=25, duration=19.8, updates=0, warnings=2, errors=5,
             result="0 update(s), 2 warning(s), 5 error(s)", user="demo",
             findings=[{"device": "Hall", "error": "Device not reachable", "severity": "warning"},
                       {"device": "Garage door", "error": "The Shelly rejected the password", "severity": "error"}])
m.events.add("check", trigger="install", devices=2, duration=2.4, updates=2, warnings=0, errors=0,
             result="2 update(s), 0 warning(s), 0 error(s)",
             findings=[{"device": "Kitchen light", "version": "1.4.0", "offer": "1.5.0", "source": "store", "severity": "success"},
                       {"device": "Hall", "version": "1.4.0", "offer": "1.5.0", "source": "upload", "severity": "success"}])
m.events.add("password", device="Kitchen light", action="enabled", user="demo")
# Downloads carry the user of their check; scheduled checks name "Schedule"
m.events.add("download", type="Plus1", version="1.5.0", size=len(content), sha256=sha, https=True, user="Schedule")
m.events.add("check", trigger="scheduled", devices=25, duration=38.0, result="ok", findings=[], user="Schedule")
m.devices[f"{4:032x}"]["settings_error"] = {"message": "Device not reachable", "severity": "warning"}
# A device with a confirmed installation stays in the updates card as "Updated";
# password protection with (green lock) and without (yellow lock) a password stored in the app
m.devices[f"{6:032x}"]["updated"] = True
# An unconfirmed installation also stays in the updates card (status "Unconfirmed", no checkbox)
m.devices[f"{5:032x}"]["updated"] = True
# A failed installation stays there with checkbox and Install (retry with the provided file)
m.devices[f"{14:032x}"].update(install_failed=True, file=f"{sha}.zip", sha256=sha, pending_version="1.5.0", offer_version="1.5.0",
                                offer_source="store", message="The Shelly reports an update error: error. It downloaded the firmware "
                                "but rejected it – the installed version may be too old for a direct update to 1.5.0")
# The update server could not be asked: a stored version is offered for a manual installation
m.devices[f"{16:032x}"].update(status="error", store_fallback=True, error_level="warning", file=f"{sha}.zip", sha256=sha,
                                pending_version="1.5.0", offer_version="1.5.0", offer_source="store",
                                message="408, 'Request Timeout', 'https://updates.shelly.cloud/update/Plus1'. Stored version 1.5.0 is "
                                "ready and can be installed manually – at your own risk: without the answer of the Shelly update "
                                "server it cannot be verified whether an intermediate version is required first.")
# Battery devices (detected from the device while awake): one asleep (not reachable at
# the last check, IP dimmed), one awake with an update
m.devices[f"{24:032x}"].update(battery=True, status="asleep", error_level="warning",
                                message="Battery device asleep – wake it up (e.g. press its button) and check it again. "
                                "Last attempt: Cannot connect to host 192.168.1.34:80")
m.devices[f"{21:032x}"]["battery"] = True
# The Doorbell was recognised as battery device by Home Assistant (battery sensor)
m.devices[f"{24:032x}"]["battery_source"] = "home_assistant"
if scenario == "docs":  # screenshots of the documentation: fewer open updates, other models
    docs_models = [("SNSW-102P16EU", "Plus2PM"), ("SNPL-00112EU", "PlusPlugS"), ("SNSW-001P16EU", "Plus1PM"),
                   ("SNSN-0024X", "PlusI4"), ("SPEM-003CEBEU", "Pro3EM"), ("SNSW-001X8EU", "PlusUni")]
    for i, d in enumerate(m.devices.values()):
        if i in (5, 6, 14, 16):  # leave the sections of finished or failed installations out
            d.update(status="up-to-date", updated=False, install_failed=False, store_fallback=False, error_level=None, message="",
                     step_target=None, offer_version=None, file=None, sha256=None, pending_version=None, offer_source=None, source_url=None)
        elif d["status"] in ("ready", "available") and i not in (0, 7, 10):
            d.update(status="up-to-date", offer_version=None, file=None, sha256=None, pending_version=None, offer_source=None,
                     source_url=None)
        if d["gen"] > 1 and d["status"] in ("up-to-date", "unregistered", "asleep") and not d.get("hold"):
            d["model"], d["type"] = docs_models[i % len(docs_models)]
# Host names of the registry entries (column "Host name"); the others were set up by IP
for i in range(0, 25, 3):
    m.devices[f"{i:032x}"]["host_name"] = f"shelly-{i}.local"
# Removed from Home Assistant: one still reachable at its last address, one not
m.devices[f"{22:032x}"].update(removed=now - 7200, removed_reachable=True)
m.devices[f"{13:032x}"].update(removed=now - 7200, removed_reachable=False, status="error", error_level="error",
                                message="Removed from Home Assistant / the Shelly integration and no longer reachable")
# No usable address in the device registry (IP outside the private networks)
m.devices[f"{19:032x}"].update(status="error", error_level="error", message="Device address 203.0.113.7 is not in a private network",
                                address_issue={"reason": "not_private", "message": "Device address 203.0.113.7 is not in a private network"})
# Shellys of other integrations (read-only card "Other Shellys")
m.others = [
    {"id": "o1", "name": "Bedroom thermostat", "kind": "blu_gateway", "model": "SBTR-001AEU", "firmware": "1.2.3", "gateway": "Living room hub"},
    {"id": "o2", "name": "Desk plug", "kind": "matter", "model": "Shelly Plug Matter", "firmware": "1.1.0", "gateway": None},
    {"id": "o3", "name": "Door/window contact", "kind": "bluetooth", "model": "SBDW-002C", "firmware": "1.0.20", "gateway": None},
    {"id": "o4", "name": "Garage sensor", "kind": "zigbee2mqtt", "model": "Shelly Door Zigbee", "firmware": "2.0.0", "gateway": None},
    {"id": "o5", "name": "Hall motion sensor", "kind": "zha", "model": "Shelly Motion Zigbee", "firmware": "2.0.1", "gateway": None},
]
# A file in the firmware folder the store does not list (Firmware tab: unknown files)
(data / "fw" / ("e" * 64 + ".zip")).write_bytes(b"y" * 120000)
m.unknown_files = {"e" * 64 + ".zip": 120000}
# Log entries: files read back from the disk, a removed device removed from the list
m.events.add("store", severity="warning", action="adopted", unknown=1, user="demo",
             adopted=[{"type": "Plus1", "version": "1.5.0", "sha256": sha}])
m.events.add("device", action="forgotten", device="Old plug", user="demo")
# An intermediate version on the way to the newest one (its file keeps the store marker)
m.firmware[f"{sha}.zip"]["intermediate"] = True
m.devices[f"{7:032x}"].update(step_target="1.7.0", message="Provided, not installed – intermediate version on the way to 1.7.0; "
                               "the device is checked again after its installation")
m.events.add("install", event="confirmed", ok=True, unverified=True, device="Entrance", type="Plus1", old="1.4.0", new="1.5.0",
             user="demo")
m.events.add("check", trigger="step", devices=1, duration=1.4, updates=1, warnings=0, errors=0,
             result="1 update(s), 0 warning(s), 0 error(s)", user="demo",
             findings=[{"device": "Heat pump", "version": "1.5.0", "offer": "1.7.0", "severity": "success"}])
m.credentials.entries[f"{1:032x}"] = {"gen": 2, "realm": "shelly-1", "ha1": "0" * 64}
# three consecutive log entries of one device (grouped by a side bar) and a check with mixed findings
m.events.add("setting", device="Pool pump", changes=[{"setting": "Eco mode", "old": "on", "new": "off"}], user="demo")
m.events.add("install", event="started", device="Pool pump", type="Plus1", old="1.4.0", new="1.5.0", user="demo")
m.events.add("install", ok=False, severity="warning", event="unconfirmed", device="Pool pump", type="Plus1", old="1.4.0", new="1.5.0",
             user="demo", detail="The Shelly downloaded the firmware but still reports 1.4.0 – check the device log or the Shelly's "
             "web interface and try again with enabled websocket debugging")
# ignored devices are named in the entry (normal text colour) and counted in the result
m.events.add("check", severity="error", trigger="manual", devices=23, duration=30.7, updates=1, warnings=1, errors=1,
             result="1 update(s), 1 warning(s), 1 error(s), 2 ignored", user="demo", ignored=["Garage door", "Washing machine"],
             findings=[{"device": "Sauna", "version": "1.6.2", "offer": "2.0.3", "source": "manufacturer", "severity": "success"},
                       {"device": "Doorbell", "error": "Cannot connect to host 192.168.1.34:80", "severity": "warning"},
                       {"device": "Fridge", "error": "The Shelly rejected the password", "severity": "error"}])
if scenario in ("features", "docs"):  # next version: cloud, Bluetooth proxy, offer delay, Shellys not listed, imported file
    for i, key in enumerate(m.devices):
        d = m.devices[key]
        d["cloud"] = [True, False, None, False][i % 4]
        d["cloud_connected"] = d["cloud"] is True and i % 8 == 0
        if d.get("offer_version"):
            d["offer_date"] = "2026-10-01"
    m.devices[f"{1:032x}"]["ble_proxy"] = True
    m.offer_after_days = 7
    held_from = time.strftime("%Y-%m-%d", time.localtime(now + 5 * 86400))
    built = time.strftime("%Y-%m-%d", time.localtime(now - 2 * 86400))
    for i in (9, 11):
        m.devices[f"{i:032x}"].update(status="up-to-date", offer_version=None, message="",
                                      hold={"version": "1.6.0", "built": built, "until": held_from})
    m.listing = {"listed": 25, "missing": [
        {"name": "Garden shed plug", "model": "SNPL-00112EU", "reason": "disabled"},
        {"name": "shellyplus1pm-a8032ab1c2d4", "model": None, "reason": "discovered"},
        {"name": "shelly1-98F4AB", "model": None, "reason": "ignored"}]}
    imported = b"Plus1 1.2.3 imported " * 5000
    imported_sha = hashlib.sha256(imported).hexdigest()
    (data / "fw" / f"{imported_sha}.zip").write_bytes(imported)
    m._register_firmware(f"{imported_sha}.zip", "Plus1", 2, "1.2.3", imported_sha, len(imported), True, source="import")
    m.firmware[f"{imported_sha}.zip"]["downloaded"] = now - 86400 * 2
if scenario == "features":  # beta firmware, a scheduled installation and schedules
    import timetable
    m.beta_firmware = True
    m.devices[f"{20:032x}"].update(status="ready", beta=True, offer_version="1.6.0-beta1", pending_version="1.6.0-beta1",
                                    file=f"{sha}.zip", sha256=sha, offer_source="manufacturer", message="")
    planned = m.devices[f"{17:032x}"]
    planned["planned"] = {"at": now + 86400 + 3600, "by": "demo", "on": now - 600, "sha256": sha, "version": "1.4.0",
                          "target": "1.5.0", "beta": False, "restart": False}
    ids = list(m.devices)
    m.schedules = [
        {"id": "a1b2c3d4e5f6", "name": "Weekly restart", "on": True, "action": "restart", "pause": 10, "time": "04:00",
         "repeat": {"kind": "weekdays", "days": [0, 3]}, "devices": "all", "anchor": None, "next": None,
         "last": {"at": now - 2 * 86400, "severity": "warning", "text": "22 of 23 restarted · 1 not reachable · 2 left out",
                  "counts": {"done": 22, "total": 23, "unreachable": 1, "refused": 0, "left": 2}}},
        {"id": "0f1e2d3c4b5a", "name": "Bluetooth off at night", "on": False, "action": "ble_off", "pause": None, "time": "23:30",
         "repeat": {"kind": "daily"}, "devices": ids[:5], "anchor": None, "next": None,
         "last": {"at": now - 86400, "severity": "missed", "text": "Missed – the app was not running", "reason": "the app was not running"}},
    ]
    m.schedules.append(
        {"id": "9a8b7c6d5e4f", "name": "Weekly backup", "on": True, "action": "backup", "pause": None, "time": "03:15",
         "repeat": {"kind": "weekdays", "days": [6]}, "devices": "all", "anchor": None, "next": None,
         "last": {"at": now - 5 * 86400, "severity": "error", "text": "3 stored · 17 unchanged · 1 asleep · 2 not backed up · 2 left out",
                  "counts": {"done": 3, "total": 25, "run": 23, "unchanged": 17, "asleep": 1, "unreachable": 2, "refused": 0,
                             "left": 2, "interrupted": False}}})
    for item in m.schedules:
        item["next"] = timetable.next_run(item, now, m.tz) if item["on"] else None
    m.events.add("schedule", severity="warning", event="run", name="Weekly restart", action="Restart", trigger="scheduled",
                 devices=23, done=22, duration=241, result="22 of 23 restarted · 1 not reachable · 2 left out", user="Schedule",
                 findings=[{"device": "Hall", "result": "unreachable", "detail": "Device not reachable"}],
                 left_out=[{"device": "Doorbell", "reason": "battery device"}, {"device": "Garage door", "reason": "ignored"}])
    m.events.add("schedule", severity="error", event="run", name="Weekly backup", action="Backup", trigger="scheduled",
                 devices=4, done=1, duration=6, result="1 stored · 1 unchanged · 1 asleep · 1 not backed up · 1 left out",
                 user="Schedule",
                 findings=[{"device": "Kitchen light", "result": "ok", "detail": "stored", "severity": "success"},
                           {"device": "Bathroom", "result": "unchanged", "detail": "unchanged", "severity": "success"},
                           {"device": "Doorbell", "result": "asleep", "detail": "Device not reachable", "severity": "warning"},
                           {"device": "Hall", "result": "unreachable", "detail": "Device not reachable", "severity": "error"}],
                 left_out=[{"device": "Garage door", "reason": "ignored"}])
    m.events.add("install", device="Bathroom", event="scheduled", type="Plus1", old="1.4.0", new="1.5.0",
                 at=timetable.local_text(now + 86400 + 3600, m.tz), user="demo")
if scenario in ("features", "docs"):  # backups of device settings and Shellys in memory that answer
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
    import shelly
    import shelly_backup
    from fake_shelly import FakeGen1, FakeShelly

    fakes = {}
    for i in (0, 1, 2, 3, 6, 9, 11):
        dev = m.devices[f"{i:032x}"]
        fakes[dev["origin"]] = fake = FakeShelly(dev["mac"])
        fake.config["sys"]["device"]["name"] = dev["name"]
        fake.chunk = 1024
    gen1 = m.devices[f"{8:032x}"]
    fakes[gen1["origin"]] = FakeGen1()
    fakes[gen1["origin"]].settings["device"]["mac"] = gen1["mac"]
    pace = {"s": 0.0}  # pause per request, set after the demo data was stored: the steps of a restore stay visible

    def device_of(origin):
        return next(d for d in m.devices.values() if d["origin"] == origin)

    async def answering(origin):
        """The Shelly in memory at this address; every other device does not answer."""
        await asyncio.sleep(pace["s"])
        if origin not in fakes:
            raise TimeoutError
        return fakes[origin]

    async def fake_rpc(_session, origin, method, params=None, _auth=None):
        return await (await answering(origin)).rpc(method, params)

    async def fake_get(_session, origin, path, _auth=None, params=None):
        fake = await answering(origin)
        return {} if path == "/shelly" else await fake.get(path, params)

    async def fake_info(_session, origin, _auth_for=None, _on_identity=None):
        await answering(origin)
        dev = device_of(origin)
        return {"auth_enabled": dev.get("auth_enabled"), "gen": dev["gen"], "shelly_id": dev["shelly_name"], "type": dev["type"],
                "model": dev["model"], "mac": dev["mac"], "version": dev["version"], "build": dev["build"]}

    async def fake_restart(_session, gen, origin, _auth=None):
        fake = await answering(origin)
        if gen > 1:
            fake.restart()

    shelly.device_rpc, shelly.device_get, shelly.device_info, shelly.restart = fake_rpc, fake_get, fake_info, fake_restart
    shelly_backup.RESTART_FIRST_S, shelly_backup.RESTART_POLL_S = 1, 0.5

    def stored(i, age_days, reason, note=None, kept=False, imported=False, version=None):
        """Store a backup of device i as it answers now."""
        dev = m.devices[f"{i:032x}"]
        target = shelly_backup.Target(None, dev["gen"], dev["origin"], None)
        content = asyncio.run(shelly_backup.read(target))
        content["protected"] = dev.get("auth_enabled")
        return m.backups.add({
            "id": hashlib.sha256(f"{i}-{age_days}".encode()).hexdigest()[:16], "at": now - int(age_days * 86400), "reason": reason,
            "note": note, "mac": dev["mac"], "gen": dev["gen"], "device": dev["name"], "model": dev["model"], "type": dev["type"],
            "fw": version or dev["version"], "parts": shelly_backup.summary(dev["gen"], content),
            "digest": shelly_backup.digest(dev["gen"], content)}, content, kept=kept, imported=imported)

    kitchen = fakes[m.devices[f"{0:032x}"]["origin"]]
    stored(0, 200, "manual", kept=True, version="1.2.0")
    stored(0, 61, "manual", imported=True, kept=True, version="1.3.0")
    stored(0, 40, "schedule", "Weekly backup", version="1.3.1")
    stored(0, 12, "update", "1.3.1 → 1.4.0", version="1.3.1")
    # Since its backup of three days ago the device was changed: settings, a script, a schedule, a webhook
    stored(0, 3, "manual", "before rewiring")
    kitchen.config["sys"]["device"]["name"] = "Kitchen light (new)"
    kitchen.config["switch:0"].update(initial_state="on", auto_off=True)
    kitchen.config["wifi"]["sta"]["ssid"] = "Home-IoT"
    kitchen.config["mqtt"]["enable"] = True
    kitchen.scripts[1]["code"] += "// changed afterwards\nprint(n);\n"
    kitchen.scripts[3] = {"name": "night-mode.js", "enable": False, "running": False, "code": "let night = true;\n"}
    kitchen.jobs.clear()
    kitchen.hooks.append({"id": 2, "cid": 0, "enable": True, "event": "switch.on", "name": "Notify",
                          "urls": ["http://192.168.1.2:8123/api/webhook/on"]})
    stored(0, 0.05, "manual")
    stored(1, 2, "update", "1.3.1 → 1.4.0", version="1.3.1")
    stored(2, 45, "schedule", "Weekly backup")
    stored(3, 9, "downgrade", "1.5.0 → 1.4.0", version="1.5.0")
    stored(6, 1, "restore")
    stored(6, 20, "schedule", "Weekly backup")
    stored(8, 5, "manual")
    stored(9, 33, "manual", kept=True)
    # A device removed from Home Assistant keeps its backups; one of a device that is not listed any more
    removed = m.devices[f"{13:032x}"]
    fakes[removed["origin"]] = FakeShelly(removed["mac"])
    stored(13, 70, "schedule", "Weekly backup")
    del fakes[removed["origin"]]
    data_of_old = asyncio.run(shelly_backup.read(shelly_backup.Target(None, 2, m.devices[f"{2:032x}"]["origin"], None)))
    m.backups.add({"id": "01d0a1bc00000000",
                   "at": now - 120 * 86400, "reason": "manual", "note": None, "mac": "AABBCC00FFFF", "gen": 2,
                   "device": "Old plug", "model": "SNPL-00112EU", "type": "PlusPlugS", "fw": "1.3.0",
                   "parts": shelly_backup.summary(2, data_of_old), "digest": shelly_backup.digest(2, data_of_old)}, data_of_old)
    pace["s"] = 0.15
    # Failed attempts: asleep, not reachable, another failure
    m.devices[f"{24:032x}"]["backup_failed"] = {"at": now - 7200, "reason": "asleep", "message": "Device not reachable"}
    m.devices[f"{23:032x}"]["backup_failed"] = {"at": now - 7200, "reason": "unreachable", "message": "Device not reachable"}
    m.devices[f"{5:032x}"]["backup_failed"] = {"at": now - 3600, "reason": "failed",
                                               "message": "Device reports: out of memory while reading the scripts"}
    m.events.add("backup", event="stored", device="Kitchen light", reason="manual", note="before rewiring", size=1840,
                 parts={"settings": True, "scripts": 1, "schedules": 1, "webhooks": 1}, user="demo")
    m.events.add("backup", severity="error", event="failed", device="Office desk", reason="update", note="1.4.0 → 1.5.0",
                 error="Device reports: out of memory while reading the scripts", user="demo")
    m.events.add("backup", severity="warning", event="restored", device="Bedroom shutter", backup_at=now - 20 * 86400,
                 restored=["settings", "scripts"], changes=7, not_applied=1, confirmed=True, restart=True, error=None, user="demo",
                 findings=[{"part": "settings", "key": "MQTT · Server", "error": "Device reports: invalid value for server"}])
    # One entry for a run of "Back up now" over several devices
    m.events.add("backup", severity="warning", event="run", devices=4, stored=2, duration=6, user="demo",
                 result="2 stored · 1 asleep · 1 not backed up",
                 findings=[{"device": "Garage door", "result": "unreachable", "severity": "warning",
                            "detail": "Device not reachable: timeout"},
                           {"device": "Garden sensor", "result": "asleep", "severity": "warning",
                            "detail": "Device not reachable: timeout"},
                           {"device": "Kitchen light", "result": "stored", "severity": "success", "size": 1840,
                            "parts": {"settings": True, "scripts": 1, "schedules": 1, "webhooks": 1}},
                           {"device": "Living room", "result": "stored", "severity": "success", "size": 1210,
                            "parts": {"settings": True, "scripts": 0, "schedules": 2, "webhooks": 0}}])
if scenario == "public":  # an installation started before (device progress shown)
    ids = list(m.devices)
    m.devices[ids[7]].update(status="installing", ota_progress=55, message="The Shelly is downloading the firmware: 55 %")
    m.installing.add(ids[7])
if scenario == "busy":
    ids = list(m.devices)
    m.progress = {"kind": "all", "total": 25, "done": 18, "errors": 2, "phase": "devices"}
    m.activity.update({ids[18]: "checking", ids[19]: "downloading", ids[17]: "server", ids[16]: "restarting"})
    m.activity.update(dict.fromkeys(ids[20:], "waiting"))

if diagnosis:
    areas = ["Kitchen", "Living room", "Hall", "Garage", "Garden", "Office", "Bedroom", "Basement"]
    for i, d in enumerate(m.devices.values()):
        d["area"] = areas[i % len(areas)] if i % 5 != 4 else None
    m.devices[f"{4:032x}"]["unreachable"] = True
    m.devices[f"{6:032x}"].update(version="1.5.0", after_update={
        "at": now - 600, "backup_at": now - 700, "from_version": "1.4.0", "to_version": "1.5.0",
        "changed": [{"key": "cloud · enable", "before": "false", "now": "true"},
                    {"key": "switch:0 · initial_state", "before": "restore_last", "now": "off"}],
        "new": [{"key": "ble · rpc.enable", "value": "true"}, {"key": "mqtt · enable_control", "value": "true"},
                {"key": "switch:0 · reverse", "value": "false"}],
        # Firmware 1.5.0 removed ble.observer: a field gone while its component is still reported
        "gone": [{"key": "ble · observer.enable", "before": "false"}],
        "changed_count": 2, "new_count": 3, "gone_count": 1,
        "scripts": {"before": 2, "again": 1, "stopped": ["ble-gateway"]}})
    m.getting_started = True

    async def demo_test(device_id, user="unknown"):
        """Connection test without a device: automatic reachable, a chosen device not."""
        await asyncio.sleep(1.5)
        dev = m.devices[device_id or m.test_devices()[0]]
        ok = device_id is None
        m.connection_test = {"ok": ok, "device": dev["name"], "ip": dev["origin"][7:], "seconds": 0.21 if ok else None,
                             "error": None if ok else "No connection to http://192.168.1.2:8098: timeout after 8 s (Shelly code -104)",
                             "at": int(time.time()), "url": m.download_base}
        return m.connection_test

    async def demo_probe(_device_id):
        """"Try again": address found, port without answer."""
        await asyncio.sleep(1)
        return {"address": {"state": "ok", "ip": "192.168.1.14", "moved": False},
                "port": {"state": "error", "port": 80, "text": "no answer after 5 s"}, "device": {"state": "skipped", "text": "Not tested"}}

    async def demo_ha_version():
        return "2026.11.2"

    m.run_connection_test = demo_test
    m.probe_device = demo_probe
    m.ha_version = demo_ha_version


async def demo_signal(device_id):
    """Wi-Fi signal of the installation dialog: the demo reaches no device (some weak, Gen1 on the cable)."""
    dev = m.devices[device_id]
    return {"rssi": None if dev.get("gen") == 1 else -50 - (int(device_id, 16) * 7) % 40}

m.wifi_signal = demo_signal

# Current values of the device dialogs (NTP server, power-on action, eco mode, access point, ...):
# the demo reaches no Shelly, so it answers with the stored values of the device and one
# example output (a device that is not reachable still fails). QA only, not part of the app.
SETTING_KEYS = ("shelly_name", "ntp", "eco", "outputs", "ap", "ap_config", "ble", "debug_ws", "cloud", "cloud_connected",
                "ble_proxy", "battery", "gen1_hostname")
EXAMPLE_OUTPUTS = [{"key": "switch:0", "name": "switch:0", "aop": "restore_last", "options": ["off", "on", "restore_last"]}]


async def demo_refresh(device_id):
    """Settings of a device dialog from the stored values (refusals of the app stay)."""
    dev = m._settings_device(device_id)
    if dev.get("unreachable"):
        raise ShellyError("Device not reachable")
    values = {key: dev[key] for key in SETTING_KEYS if key in dev}
    values.setdefault("outputs", EXAMPLE_OUTPUTS)
    # A Shelly output without a name is named by its key (like shelly._output)
    values["outputs"] = [{**o, "name": o.get("name") or o["key"]} for o in values["outputs"]]
    values.setdefault("ntp", "time.local")
    # Access point details of a Gen2+ device: the default name, protected while it is on
    # (every second device also has a range extender); stored like a real read, so the
    # app decides on the same values as the form
    if dev.get("gen", 1) >= 2 and "ap_config" not in values:
        values["ap_config"] = dev["ap_config"] = {
            "ssid": f"{dev.get('type') or 'Shelly'}-{device_id[-12:].upper()}",
            "open": False if dev.get("ap") is True else True,
            "range_extender": False if int(device_id, 16) % 2 else None}
    return values

m.refresh_settings = demo_refresh


async def wrap(_request):
    """Same-origin page embedding the UI like the HA panel does."""
    return web.Response(text='<!doctype html><html><body style="margin:0"><iframe src="/ui/" '
                        'style="border:0;width:100vw;height:100vh"></iframe></body></html>', content_type="text/html")

root = web.Application()
root.router.add_get("/wrap", wrap)
# The features demo runs as a pre-release (red "(beta)" mark), the others as a final version
root.add_subapp("/ui/", web_ui.build_ui_app(m, ingress_ip="127.0.0.1", app_version="9.9.9-rc1" if scenario == "features" else ""))
web.run_app(root, host="127.0.0.1", port=port, print=None)
