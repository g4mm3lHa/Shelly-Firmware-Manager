"""Local demo server with fake data for UI tests (no network access).

Usage: python demo.py <app_dir> <port> <scenario: idle|busy>
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
m = manager.Manager(opts, session=None, token="t", version="3.0.0")
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
for i, n in enumerate(names):
    st = statuses[i % len(statuses)]
    gen = 1 if st == "skipped" else (2 + i % 2)
    m.devices[f"{i:032x}"] = {
        "name": n, "shelly_name": f"shelly-{i}", "gen": gen, "model": "SNSW-001X16EU", "type": "Plus1" if gen > 1 else "SHSW-1",
        "version": "1.4.0", "offer_version": "1.5.0" if st in ("ready", "available") else None, "status": st,
        "message": "Device not reachable" if st == "error" else "", "origin": f"http://192.168.1.{10 + i}",
        "last_check": now - 3600, "last_success": now - (90000 if st == "error" else 3600),
        "last_update": now - 86400 * 3 if i % 3 == 0 else None, "ntp": "time.local", "eco": i % 2 == 0,
        "outputs": [{"key": "switch:0", "name": None, "state": "off", "aop": "off", "options": ["off", "on", "restore_last"]}] if i % 4 else [],
        "file": f"{sha}.zip" if st == "ready" else None, "sha256": sha if st == "ready" else None, "size": len(content),
        "pending_version": "1.5.0" if st == "ready" else None,
        # 3.0 feature states: on/off, unsupported and unknown (None) variants
        "ap": [False, True, None, "unsupported"][i % 4], "ble": "unsupported" if gen == 1 else [True, False, None][i % 3],
        "actions": [False, True, None][i % 3], "schedules": [True, False, "unsupported", None][i % 4],
        "auth_enabled": [False, True, None][i % 3], "ignored": i in (3, 12),
    }
m.last_scan = now - 3600
m.scanned_version = "2.2.1"
m.events.add("check", trigger="manual", devices=25, duration=41.2, result="ok", findings=[], user="demo")
m.events.add("check", trigger="device", devices=1, duration=1.2, result="ok", findings=[], user="demo")
m.events.add("check", severity="error", trigger="manual", devices=25, duration=19.8, updates=0, warnings=2, errors=5,
             result="0 update(s), 2 warning(s), 5 error(s)", user="demo",
             findings=[{"device": "Hall", "error": "Device not reachable", "severity": "warning"},
                       {"device": "Garage door", "error": "The Shelly rejected the password", "severity": "error"}])
m.events.add("password", device="Kitchen light", action="enabled", user="demo")
m.devices[f"{4:032x}"]["settings_error"] = {"message": "Device not reachable", "severity": "warning"}
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
