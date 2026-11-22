"""Local demo server with fake data for UI tests (no network access).

Usage: python demo.py <app_dir> <port> <scenario: idle|busy|legacy|missing|features|first|public|docs>
legacy: download_base_url is the former example value (not confirmed) and one device
installs with device progress; missing: download_base_url not set; public: download_base_url is a public IP address (saved
before it was refused); features: idle
plus stored older versions (one of them uploaded) for downgrade and upload checks; docs: features with
fewer open updates and other models, for the screenshots of the documentation.
Serves the UI app below /ui/ and a same-origin wrapper /wrap that embeds it in an
iframe (like the HA Ingress panel)."""

import hashlib
import sys
import tempfile
import time
from pathlib import Path

app_dir, port, scenario = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sys.path.insert(0, app_dir)
import data_paths
import manager
import web_ui
from aiohttp import web
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

data = Path(tempfile.mkdtemp())
for name, value in {"STATE_FILE": data / "state.json", "FW_DIR": data / "fw", "CREDENTIALS_FILE": data / "c.json",
                    "CHANGELOG_FILE": data / "cl.json", "EVENTS_FILE": data / "ev.json"}.items():
    setattr(data_paths, name, value)

opts = {"check_interval_days": 7, "check_time": "02:45", "download_base_url": "http://192.168.1.2:8098", "restrict_downloads_to_devices": True,
        "gen1_updates_enabled": True, "max_parallel_installs": 2,
        "log_retention_days": 30, "log_entry_limit": 1000, "allow_downgrade": True, "import_max_mb": 100}
if scenario == "legacy":
    import app_options
    opts["download_base_url"] = app_options.LEGACY_DOWNLOAD_BASE
elif scenario == "public":
    opts["download_base_url"] = "http://203.0.113.10:8098"
elif scenario == "missing":   # also shows downgrades switched off
    del opts["download_base_url"]
    del opts["allow_downgrade"]
m = manager.Manager(opts, session=None, token="t")
# Fresh installation: only the "first" demo shows the first-start question
m.first_start_open = scenario == "first"
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
if scenario == "legacy":  # an installation started before (device progress shown)
    ids = list(m.devices)
    m.devices[ids[7]].update(status="installing", ota_progress=55, message="The Shelly is downloading the firmware: 55 %")
    m.installing.add(ids[7])
if scenario == "busy":
    ids = list(m.devices)
    m.progress = {"kind": "all", "total": 25, "done": 18, "errors": 2, "phase": "devices"}
    m.activity.update({ids[18]: "checking", ids[19]: "downloading"})
    m.activity.update(dict.fromkeys(ids[20:], "waiting"))


async def demo_signal(device_id):
    """Wi-Fi signal of the installation dialog: the demo reaches no device (some weak, Gen1 on the cable)."""
    dev = m.devices[device_id]
    return {"rssi": None if dev.get("gen") == 1 else -50 - (int(device_id, 16) * 7) % 40}

m.wifi_signal = demo_signal


async def wrap(_request):
    """Same-origin page embedding the UI like the HA panel does."""
    return web.Response(text='<!doctype html><html><body style="margin:0"><iframe src="/ui/" '
                        'style="border:0;width:100vw;height:100vh"></iframe></body></html>', content_type="text/html")

root = web.Application()
root.router.add_get("/wrap", wrap)
# The features demo runs as a pre-release (red "(beta)" mark), the others as a final version
root.add_subapp("/ui/", web_ui.build_ui_app(m, ingress_ip="127.0.0.1", app_version="9.9.9-rc1" if scenario == "features" else ""))
web.run_app(root, host="127.0.0.1", port=port, print=None)
