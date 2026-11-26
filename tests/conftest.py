"""Shared fixtures: a Manager on a temporary /data with all network access mocked.

No test opens a connection: the HA registry, the Shellys and the Shelly cloud are
replaced by in-memory fakes (FakeNet)."""

import asyncio
import hashlib
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "shelly_firmware_manager" / "app"
sys.path.insert(0, str(APP))

import data_paths
import manager
import manager_backups
import manager_installs
import shelly
import shelly_cloud
import shelly_net
import web_ui
from aiohttp.test_utils import TestClient, TestServer

OPTIONS = {
    "check_interval_days": 7,
    "check_time": "02:45",
    "download_base_url": "http://192.168.1.2:8098",
    "restrict_downloads_to_devices": True,
    "gen1_updates_enabled": True,
    "max_parallel_installs": 2,
    "log_retention_days": 30,
    "log_entry_limit": 1000,
    "allow_downgrade": True,
    "keep_installed": False,  # switched on by the tests of "Keep installed versions"
    "backup_before_install": False,  # switched on by the tests of the backups
}


# HA device IDs (32 hex characters, as checked by the web UI)
A, B, C = ("0" * 31 + "a", "0" * 31 + "b", "0" * 31 + "c")



def vendor_content(version):
    """Bytes of the fake manufacturer file of a Gen2+ version (1.5.0: b"x" * 70000);
    its offer URL ends in their SHA-256 like the real ones."""
    content = b"x" * 70000
    return content if version == "1.5.0" else version.encode() + content  # another version, another file

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
        self.asleep = set()  # devices that do not answer at all (timeout)
        self.battery = set()  # devices whose settings report a sleep period (battery devices)
        self.downloads = 0
        self.offer_calls = 0  # requests with the installed version (next step)
        self.offer_version = "1.5.0"  # stable version offered by the manufacturer's server
        self.steps = {}  # installed version -> next step offered for it (else offer_version)
        self.newest_version = None  # answer without installed version (None: offer_version)
        self.beta_version = None  # beta of the server's answers when asked with beta (None: no beta)
        self.beta_newest_only = False  # the beta only in the answer without installed version
        self.beta_requests = []  # beta flag of every offer request
        self.newest_calls = 0
        self.offer_error = None  # exception raised by requests with the installed version
        self.reference_answer = "1.3.3"  # answer to the reference request (Plus1 1.0.7)
        self.reference_calls = 0
        self.gate = None  # asyncio.Event: device_info waits for it (simulates slow devices)
        self.download_gate = None
        self.info_calls = 0
        self.settings_calls = []  # extended flag of every read_settings call
        self.settings_error = None  # exception raised by read_settings
        self.removed = set()  # devices removed from the HA registry (still answering unless unreachable)
        self.ha_battery = set()  # devices with a Shelly battery sensor in Home Assistant
        self.extra_registry = []  # further registry entries (other integrations, sub-devices)
        self.areas = {}  # area registry of Home Assistant: area ID -> name
        self.device_areas = {}  # device ID -> area ID (registry field area_id)
        self.area_error = None  # exception raised by the request of the area registry
        self.resolvable = set()  # host names that resolve (Host name column)
        self.ha_entries = []  # config entries of the Shelly integration (config_entries/get)
        self.ha_flows = []  # flows in progress (config_entries/flow/progress)
        self.setup_error = None  # exception raised by the request of entries and flows
        self.builds = {}  # offered version -> build ID named by the server

    def registry(self):
        """Registry entries as delivered by HA."""
        return [
            {"id": i, "name": n, "identifiers": [["shelly", i]], "configuration_url": f"http://{ip}/",
             "connections": [["mac", f"aa:bb:cc:00:00:0{i[-1]}"]], "area_id": self.device_areas.get(i)}
            for i, (n, ip, _v, _o) in self.devices.items()
            if i not in self.removed
        ] + list(self.extra_registry)

    async def area_registry(self):
        """Area registry as delivered by HA."""
        await asyncio.sleep(0)
        if self.area_error is not None:
            raise self.area_error
        return [{"area_id": a, "name": n} for a, n in self.areas.items()]

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
        "BACKUP_DIR": data / "backups",
        "SETTINGS_FILE": data / "settings.json",
    }.items():
        monkeypatch.setattr(data_paths, name, value)

    async def resolve(url):
        return url.rstrip("/")

    async def device_info(_session, origin, auth_for=None, on_identity=None):
        if fake.gate is not None:
            await fake.gate.wait()
        device_id = fake.by_origin(origin)
        if device_id in fake.unreachable:
            raise shelly.ShellyError("Device not reachable")
        if device_id in fake.asleep:
            raise TimeoutError()
        if on_identity:
            on_identity(2, f"shelly-{device_id}")
        fake.info_calls += 1
        return {
            "auth_enabled": False,
            "gen": 2,
            "shelly_id": f"shelly-{device_id}",
            "type": "Plus1",
            "model": "SNSW-001X16EU",
            "mac": "AABBCC00000" + device_id[-1].upper(),
            "version": fake.devices[device_id][2],
            "build": "build-" + fake.devices[device_id][2],
        }

    async def read_settings(*args, **_kwargs):
        fake.settings_calls.append(args[5] if len(args) > 5 else False)  # extended flag
        if fake.settings_error is not None:
            raise fake.settings_error
        answer = {"shelly_name": "plus1", "ntp": "time.local", "eco": False, "ap": False, "ble": "unsupported", "outputs": []}
        # The device reports its sleep period (battery device) whenever it answers
        answer["battery"] = any(args[2] == f"http://{fake.devices[i][1]}" for i in fake.battery if i in fake.devices)
        return answer

    def offer(version):
        # Like the real Gen2+ URLs the offer URL ends in the SHA-256 of the file
        sha = hashlib.sha256(vendor_content(version)).hexdigest()
        return {"version": version, "url": f"https://fw.example/Plus1-{version}/{sha}", "steps": "1",
                "build": fake.builds.get(version, "")}

    def with_beta(answer, beta):
        # Option beta_firmware: the beta of the server's answer (fake.beta_version)
        if answer is not None and beta and fake.beta_version:
            beta_offer = offer(fake.beta_version)
            answer.update(beta_version=fake.beta_version, beta_url=beta_offer["url"], beta_build=beta_offer["build"])
        return answer

    async def fetch_offer(_session, fw_type, _pins=None, version=None, beta=False):
        fake.beta_requests.append(beta)
        if (fw_type, version) == ("Plus1", "1.0.7"):  # reference request of every check
            fake.reference_calls += 1
            return offer(fake.reference_answer) if fake.reference_answer else None
        if version is None:  # newest version
            fake.newest_calls += 1
            return with_beta(offer(fake.newest_version or fake.offer_version), beta)
        fake.offer_calls += 1
        if fake.offer_error is not None:
            raise fake.offer_error
        return with_beta(offer(fake.steps.get(version, fake.offer_version)), beta and not fake.beta_newest_only)

    async def download_firmware(_session, url, target_dir, _pins=None, https_only=False):
        fake.downloads += 1
        if fake.download_gate is not None:
            await fake.download_gate.wait()
        version = url.rsplit("/", 2)[-2].split("-", 1)[-1]
        content = vendor_content(version)
        sha = hashlib.sha256(content).hexdigest()
        (target_dir / f"{sha}.zip").write_bytes(content)
        return f"{sha}.zip", sha, len(content), True

    async def refresh(*_args, **_kwargs):
        return None

    async def host_resolves(host):
        return host in fake.resolvable

    class NoWebSocket:
        """Gen2+ installations try the device WebSocket first; in tests there is
        none unless a test installs its own fake (the HTTP fallback is used)."""

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            raise OSError("no device WebSocket in tests")

    monkeypatch.setattr(shelly_net, "resolve_device_origin", resolve)
    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly, "read_settings", read_settings)
    monkeypatch.setattr(shelly_cloud, "fetch_offer", fetch_offer)
    monkeypatch.setattr(shelly_cloud, "download_firmware", download_firmware)
    monkeypatch.setattr(shelly, "OtaChannel", NoWebSocket)
    monkeypatch.setattr(shelly_net, "host_resolves", host_resolves)
    monkeypatch.setattr(manager_backups, "AFTER_UPDATE_WAIT_S", 0)
    fake.refresh = refresh
    fake.data = data
    return fake


# Running time and Wi-Fi signal during an installation: not reported by default (tests of
# the weak-Wi-Fi tolerance set their own answer; the real function is kept for its own tests)
REAL_LINK_STATUS = shelly.link_status


@pytest.fixture(autouse=True)
def no_link_status(monkeypatch):
    """shelly.link_status answers {"uptime": None, "rssi": None} unless a test replaces it."""

    async def unknown(*_args, **_kwargs):
        return {"uptime": None, "rssi": None}

    monkeypatch.setattr(shelly, "link_status", unknown)


@pytest.fixture
def quick_install(monkeypatch):
    """Short installation timings for installs that run to the end in a test
    (first poll at once, then every 10 ms, limits of 0.1 s and 0.3 s)."""
    monkeypatch.setattr(manager_installs, "INSTALL_FIRST_CHECK_S", 0)
    monkeypatch.setattr(manager_installs, "INSTALL_POLL_S", 0.01)
    monkeypatch.setattr(manager_installs, "INSTALL_NO_DOWNLOAD_S", 0.1)
    monkeypatch.setattr(manager_installs, "INSTALL_WAIT_S", 0.3)


@pytest.fixture
def updating(mgr, net, monkeypatch, quick_install):
    """End-to-end installs against FakeNet: the update command "installs" the version
    of the served store file on the fake device; short timings. The returned dict's
    "accept" = False makes the device ignore the command."""
    result = {"accept": True}

    async def trigger(_session, _gen, origin, url, _auth=None):
        if result["accept"]:
            device = net.by_origin(origin)
            version = mgr.firmware[url.rsplit("/", 1)[-1]]["version"]
            net.devices[device] = (*net.devices[device][:2], version, version)

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    return result


@pytest.fixture
async def mgr(net):
    """Manager with fake registry; notifications are recorded instead of sent."""
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    m.first_start_open = False  # tests start like an installation that already answered the question

    async def registry():
        await asyncio.sleep(0)
        return net.registry()

    async def notify(*args):
        m.notified.append(args)
        return True

    async def battery_devices():
        await asyncio.sleep(0)
        return set(net.ha_battery)

    async def setup_state():
        await asyncio.sleep(0)
        if net.setup_error is not None:
            raise net.setup_error
        return list(net.ha_entries), list(net.ha_flows)

    m.notified = []
    m._registry = registry
    m._ha_setup_state = setup_state
    m._battery_devices = battery_devices
    m._area_registry = net.area_registry
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
