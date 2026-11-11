"""Shared fixtures: a Manager on a temporary /data with all network access mocked.

No test opens a connection: the HA registry, the Shellys and the Shelly cloud are
replaced by in-memory fakes (FakeNet)."""

import asyncio
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "shelly_firmware_manager" / "app"
sys.path.insert(0, str(APP))

import manager
import shelly
import web_ui
from aiohttp.test_utils import TestClient, TestServer

OPTIONS = {
    "check_interval_days": 7,
    "check_time": "02:45",
    "download_base_url": "http://192.168.1.2:8098",
    "restrict_downloads_to_devices": True,
    "gen1_updates_enabled": True,
    "gen1_allowed_types": ["SHSW-1"],
    "max_parallel_installs": 2,
    "log_retention_days": 30,
    "log_entry_limit": 1000,
}


# HA device IDs (32 hex characters, as checked by the web UI)
A, B, C = ("0" * 31 + "a", "0" * 31 + "b", "0" * 31 + "c")


class FakeNet:
    """In-memory replacement for registry, devices and manufacturer servers."""

    def __init__(self):
        # device ID -> (registry name, IP, installed version, offered version)
        self.devices = {
            A: ("Kitchen", "192.168.1.10", "1.4.0", "1.5.0"),
            B: ("Hall", "192.168.1.11", "1.5.0", "1.5.0"),
            C: ("Garage", "192.168.1.12", "1.4.0", "1.5.0"),
        }
        self.unreachable = set()
        self.downloads = 0
        self.offer_calls = 0
        self.offer_version = "1.5.0"  # stable version offered by the manufacturer's server
        self.gate = None  # asyncio.Event: device_info waits for it (simulates slow devices)
        self.download_gate = None
        self.info_calls = 0
        self.settings_calls = []  # extended flag of every read_settings call
        self.settings_error = None  # exception raised by read_settings

    def registry(self):
        """Registry entries as delivered by HA."""
        return [
            {"id": i, "name": n, "identifiers": [["shelly", i]], "configuration_url": f"http://{ip}/"}
            for i, (n, ip, _v, _o) in self.devices.items()
        ]

    def by_origin(self, origin):
        """Device ID for an origin URL."""
        return next(i for i, d in self.devices.items() if f"http://{d[1]}" == origin)


@pytest.fixture
def net(monkeypatch, tmp_path):
    """Patch all files to tmp_path and all network functions to FakeNet."""
    fake = FakeNet()
    monkeypatch.setenv("TZ", "Europe/Berlin")  # what the Supervisor passes (HA time zone)
    data = tmp_path / "data"
    data.mkdir()
    for name, value in {
        "DATA_DIR": data,
        "STATE_FILE": data / "state.json",
        "FW_DIR": data / "firmware",
        "CREDENTIALS_FILE": data / "credentials.json",
        "CHANGELOG_FILE": data / "changelog.json",
        "EVENTS_FILE": data / "events.json",
    }.items():
        monkeypatch.setattr(manager, name, value)

    async def resolve(url):
        return url.rstrip("/")

    async def device_info(_session, origin, auth_for=None, on_identity=None):
        if fake.gate is not None:
            await fake.gate.wait()
        device_id = fake.by_origin(origin)
        if device_id in fake.unreachable:
            raise shelly.ShellyError("Device not reachable")
        if on_identity:
            on_identity(2, f"shelly-{device_id}")
        fake.info_calls += 1
        return {
            "auth_enabled": False,
            "gen": 2,
            "shelly_id": f"shelly-{device_id}",
            "type": "Plus1",
            "model": "SNSW-001X16EU",
            "mac": "AABBCC" + device_id[-1].upper(),
            "version": fake.devices[device_id][2],
            "build": "build-" + fake.devices[device_id][2],
        }

    async def read_settings(*args, **_kwargs):
        fake.settings_calls.append(args[5] if len(args) > 5 else False)  # extended flag
        if fake.settings_error is not None:
            raise fake.settings_error
        return {"shelly_name": "plus1", "ntp": "time.local", "eco": False, "ap": False, "ble": "unsupported", "outputs": []}

    async def fetch_offer(_session, _fw_type, _pins=None):
        fake.offer_calls += 1
        return {"version": fake.offer_version, "url": "https://fw.example/Plus1.zip"}

    async def download_firmware(_session, _url, target_dir, _pins=None, https_only=False):
        fake.downloads += 1
        if fake.download_gate is not None:
            await fake.download_gate.wait()
        content = b"x" * 70000
        if fake.offer_version != "1.5.0":  # another offered version is another file
            content = fake.offer_version.encode() + content
        import hashlib

        sha = hashlib.sha256(content).hexdigest()
        (target_dir / f"{sha}.zip").write_bytes(content)
        return f"{sha}.zip", sha, len(content), True

    async def refresh(*_args, **_kwargs):
        return None

    class NoWebSocket:
        """rc6: Gen2+ installations try the device WebSocket first; in tests there is
        none unless a test installs its own fake (the HTTP fallback is used)."""

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            raise OSError("no device WebSocket in tests")

    monkeypatch.setattr(shelly, "resolve_device_origin", resolve)
    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly, "read_settings", read_settings)
    monkeypatch.setattr(shelly, "fetch_offer", fetch_offer)
    monkeypatch.setattr(shelly, "download_firmware", download_firmware)
    monkeypatch.setattr(shelly, "OtaChannel", NoWebSocket)
    fake.refresh = refresh
    fake.data = data
    return fake


@pytest.fixture
async def mgr(net):
    """Manager with fake registry; notifications are recorded instead of sent."""
    m = manager.Manager(dict(OPTIONS), session=None, token="t", version="3.0.1")

    async def registry():
        await asyncio.sleep(0)
        return net.registry()

    async def notify(*args):
        m.notified.append(args)

    m.notified = []
    m._registry = registry
    m.notify = notify
    m.changelogs.refresh = net.refresh
    return m


@pytest.fixture
async def client(mgr):
    """Test client of the UI app with the CSRF token of the delivered page; the test
    server connects from 127.0.0.1, which plays the Ingress proxy here."""
    app = web_ui.build_ui_app(mgr, ingress_ip="127.0.0.1")
    async with TestClient(TestServer(app)) as c:
        page = await (await c.get("/")).text()
        c.csrf = re.search(r'name="csrf" content="([0-9a-f]{64})"', page).group(1)
        yield c
