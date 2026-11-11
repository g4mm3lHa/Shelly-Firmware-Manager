"""Local demo server with fake data for UI tests (no network access).

Usage: python demo.py <app_dir> <port> <scenario: idle|busy|legacy|missing|rc7>
legacy: download_base_url is the former example value (not confirmed) and one device
installs with device progress; missing: download_base_url not set; rc7: idle
plus stored older versions (one of them uploaded) for downgrade and upload checks.
Serves the UI app below /ui/ and a same-origin wrapper /wrap that embeds it in an
iframe (like the HA Ingress panel)."""

import hashlib
import sys
import tempfile
import time
from pathlib import Path

app_dir, port, scenario = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sys.path.insert(0, app_dir)
import manager  # noqa: E402
import web_ui  # noqa: E402
from aiohttp import web  # noqa: E402
import logging  # noqa: E402
logging.basicConfig(level=logging.INFO, format="%(message)s")

data = Path(tempfile.mkdtemp())
for name, value in {"STATE_FILE": data / "state.json", "FW_DIR": data / "fw", "CREDENTIALS_FILE": data / "c.json",
                    "CHANGELOG_FILE": data / "cl.json", "EVENTS_FILE": data / "ev.json"}.items():
    setattr(manager, name, value)
if hasattr(web_ui, "FW_DIR"):
    web_ui.FW_DIR = data / "fw"

opts = {"check_interval_days": 7, "check_time": "02:45", "download_base_url": "http://192.168.1.2:8098", "restrict_downloads_to_devices": True,
        "gen1_updates_enabled": True, "gen1_allowed_types": ["SHSW-1"], "max_parallel_installs": 2,
        "log_retention_days": 30, "log_entry_limit": 1000}
if scenario == "legacy":
    import app_options  # noqa: E402
    opts["download_base_url"] = app_options.LEGACY_DOWNLOAD_BASE
elif scenario == "missing":
    del opts["download_base_url"]
m = manager.Manager(opts, session=None, token="t")
now = int(time.time())
names = ["Kitchen light", "Living room ceiling with a really long device name", "Hall", "Garage door", "Pool pump",
         "Office desk", "Bedroom shutter", "Heat pump 3EM", "Garden plug", "Basement", "Attic fan", "Terrace",
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
        # power-on action: "restore_last" (green icon) or another value (red icon, 3.1.1); every 4th device has no output
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
    }
if scenario == "rc7":  # older versions for the downgrade list, one uploaded
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
m.devices[f"{4:032x}"]["settings_error"] = {"message": "Device not reachable", "severity": "warning"}
# A device with a confirmed installation stays in the updates card as "Updated";
# password protection with (green lock) and without (yellow lock) a password stored in the app
m.devices[f"{6:032x}"]["updated"] = True
m.credentials.entries[f"{1:032x}"] = {"gen": 2, "realm": "shelly-1", "ha1": "0" * 64}
# three consecutive log entries of one device (grouped by a side bar) and a check with mixed findings
m.events.add("setting", device="Pool pump", changes=[{"setting": "Eco mode", "old": "on", "new": "off"}], user="demo")
m.events.add("install", event="started", device="Pool pump", type="Plus1", old="1.4.0", new="1.5.0", user="demo")
m.events.add("install", ok=False, severity="warning", event="unconfirmed", device="Pool pump", type="Plus1", old="1.4.0", new="1.5.0",
             user="demo", detail="The Shelly downloaded the firmware but still reports 1.4.0 – check the device log or the Shelly's "
             "web interface and try again with enabled websocket debugging")
m.events.add("check", severity="error", trigger="manual", devices=25, duration=30.7, updates=1, warnings=1, errors=1,
             result="1 update(s), 1 warning(s), 1 error(s)", user="demo",
             findings=[{"device": "Sauna", "version": "1.6.2", "offer": "2.0.3", "source": "manufacturer", "severity": "success"},
                       {"device": "Doorbell", "error": "Cannot connect to host 192.168.1.34:80", "severity": "warning"},
                       {"device": "Fridge", "error": "The Shelly rejected the password", "severity": "error"}])
if scenario == "legacy":  # an installation started before (device progress shown)
    ids = list(m.devices)
    m.devices[ids[7]].update(status="installing", ota_progress=55, message="The Shelly is downloading the firmware: 55 %")
    m.installing.add(ids[7])
if scenario == "busy":
    ids = list(m.devices)
    m.progress = {"kind": "all", "total": 25, "done": 18, "errors": 2, "phase": "devices"}
    m.activity.update({ids[18]: "checking", ids[19]: "downloading"})
    m.activity.update(dict.fromkeys(ids[20:], "waiting"))


async def wrap(_request):
    """Same-origin page embedding the UI like the HA panel does."""
    return web.Response(text='<!doctype html><html><body style="margin:0"><iframe src="/ui/" '
                        'style="border:0;width:100vw;height:100vh"></iframe></body></html>', content_type="text/html")

root = web.Application()
root.router.add_get("/wrap", wrap)
root.add_subapp("/ui/", web_ui.build_ui_app(m, ingress_ip="127.0.0.1"))
web.run_app(root, host="127.0.0.1", port=port, print=None)
