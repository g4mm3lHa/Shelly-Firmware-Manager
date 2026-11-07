"""rc6: download_base_url without default (R1), Gen2+ OTA events over the device
WebSocket (R3), download diagnosis per device (R4), parallel stop (R5), GET-only
download counting (R6) and shared tasks of a cancelled check (R7).

All device traffic goes to local test servers on 127.0.0.1 or to fakes."""

import asyncio
import hashlib
import json
import re
import time

import app_options
import download_server
import main
import manager
import pytest
import shelly
import web_ui
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from conftest import OPTIONS, A, B
from test_rc5 import app_env, _send_sigterm  # noqa: F401 – fixture reused

LEGACY = app_options.LEGACY_DOWNLOAD_BASE
REAL_CHANNEL = shelly.OtaChannel  # conftest replaces it with a fake per test


def make_manager(net, **options):
    """Manager with custom options (net fixture patches files and network)."""
    m = manager.Manager({**OPTIONS, **options}, session=None, token="t")
    m.notify = lambda *_a: asyncio.sleep(0)
    return m


def without_url():
    """Options as delivered by the Supervisor when download_base_url is unset."""
    return {k: v for k, v in OPTIONS.items() if k != "download_base_url"}


# ---------------------------------------------------------------- R1


async def test_missing_url_blocks_installations(net):
    m = manager.Manager(without_url(), session=None, token="t")
    assert m.download_base is None and m.download_base_issue == "missing"
    m.devices[A] = {"status": manager.STATUS_READY, "sha256": "a" * 64, "gen": 2}
    assert await m.install(A, "a" * 64, "u") is False
    state = m.public_state()["download_base"]
    assert state["issue"] == "missing" and "Settings tab" in state["message"]


async def test_legacy_value_needs_confirmation_which_persists(net):
    m = make_manager(net, download_base_url=LEGACY)
    assert m.download_base_issue == "unconfirmed"
    with pytest.raises(manager.ShellyError):
        m.confirm_download_base("http://192.168.178.51:8098", user="u")  # stale view
    m.confirm_download_base(LEGACY, user="alice")
    assert m.download_base_issue is None
    entry = m.events.entries[-1]
    assert entry["kind"] == "options" and entry["confirmed"] and entry["user"] == "alice"
    m2 = make_manager(net, download_base_url=LEGACY + "/")  # restart, trailing slash
    assert m2.download_base_issue is None
    m3 = make_manager(net, download_base_url="http://192.168.1.5:8098")
    assert m3.download_base_issue is None  # any other address needs no confirmation


async def test_changing_the_url_drops_the_confirmation(net):
    m = make_manager(net, download_base_url=LEGACY)
    m.confirm_download_base(LEGACY)

    async def post(*_args):
        return None

    m._supervisor_post = post
    assert await m.save_options({"download_base_url": "http://192.168.1.5:8098"}) == ["download_base_url"]
    assert m.download_base_confirmed is None
    assert json.loads(manager.STATE_FILE.read_text())["download_base_confirmed"] is None


def test_empty_url_unsets_the_option():
    new, changed = app_options.merge({**OPTIONS}, {"download_base_url": ""})
    assert changed == ["download_base_url"] and "download_base_url" not in new
    new, changed = app_options.merge(without_url(), {"download_base_url": ""})
    assert changed == [] and "download_base_url" not in new
    with pytest.raises(ValueError):
        app_options.merge(OPTIONS, {"check_time": ""})  # only optional options can be emptied


async def web_client(m):
    """Test client of the UI app with the CSRF token."""
    client = TestClient(TestServer(web_ui.build_ui_app(m, ingress_ip="127.0.0.1")))
    await client.start_server()
    page = await (await client.get("/")).text()
    client.csrf = re.search(r'name="csrf" content="([0-9a-f]{64})"', page).group(1)
    return client


async def test_web_install_refused_and_confirm_endpoint(net):
    m = make_manager(net, download_base_url=LEGACY)
    m.devices[A] = {"status": manager.STATUS_READY, "sha256": "a" * 64, "gen": 2, "name": "K"}
    client = await web_client(m)
    try:
        hdr = {"X-CSRF-Token": client.csrf}
        r = await client.post(f"/api/devices/{A}/install", json={"sha256": "a" * 64, "confirm": True}, headers=hdr)
        assert r.status == 409 and "former example value" in await r.text()
        url = "/api/settings/download-base/confirm"
        assert (await client.post(url, json={"url": LEGACY})).status == 403  # CSRF
        assert (await client.post(url, json={"url": "http://x:1"}, headers=hdr)).status == 409
        r = await client.post(url, json={"url": LEGACY}, headers=hdr)
        assert r.status == 200 and m.download_base_issue is None
        state = await (await client.get("/api/state")).json()
        assert state["download_base"] == {"url": LEGACY, "issue": None, "message": None}
    finally:
        await client.close()


# ---------------------------------------------------------------- R3: device WebSocket


class FakeShelly:
    """Local Gen2 RPC WebSocket at /rpc with optional Digest auth (official format)."""

    def __init__(self, password=None, events=(), redirect=False):
        self.password, self.events, self.redirect = password, list(events), redirect
        self.realm = "shellyplus1-aabbcc"
        self.frames = []

    def challenge(self):
        return {"auth_type": "digest", "nonce": "AAAA-nonce", "realm": self.realm, "algorithm": "SHA-256"}

    def valid(self, auth):
        sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
        ha1 = sha(f"admin:{self.realm}:{self.password}")
        expected = sha(f"{ha1}:{auth['nonce']}:{auth['nc']}:{auth['cnonce']}:auth:{sha('dummy_method:dummy_uri')}")
        return auth.get("response") == expected and auth.get("nc") == "00000001" and isinstance(auth.get("cnonce"), int)

    async def handler(self, request):
        if self.redirect:
            raise web.HTTPFound("http://203.0.113.1/rpc")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            frame = json.loads(msg.data)
            self.frames.append(frame)
            if self.password and not (frame.get("auth") and self.valid(frame["auth"])):
                await ws.send_json({"id": frame["id"], "src": self.realm, "dst": frame["src"],
                                    "error": {"code": 401, "message": json.dumps(self.challenge())}})
                continue
            await ws.send_json({"id": frame["id"], "src": self.realm, "dst": frame["src"], "result": None})
            for event in self.events:
                await ws.send_json({"src": self.realm, "dst": frame["src"], "method": "NotifyEvent",
                                    "params": {"ts": 1.0, "events": [{"component": "sys", "ts": 1.0, **event}]}})
            await ws.close()
        return ws

    async def __aenter__(self):
        app = web.Application()
        app.router.add_get("/rpc", self.handler)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.origin = f"http://127.0.0.1:{self.server.port}"
        return self

    async def __aexit__(self, *_exc):
        await self.server.close()


async def collect(channel):
    return [e async for e in channel.events()]


async def test_channel_sends_update_and_reads_ota_events():
    events = [{"event": "ota_begin", "msg": "Waiting for data"},
              {"event": "ota_progress", "msg": "Downloading", "progress_percent": 42},
              {"event": "ota_error", "msg": "Unexpected end of data"},
              {"event": "unrelated"}]
    async with FakeShelly(events=events) as dev, REAL_CHANNEL(dev.origin) as ch:
        assert await ch.update("http://192.168.1.2:8098/x.zip") is True
        got = await collect(ch)
    assert dev.frames[0]["method"] == "Shelly.Update" and dev.frames[0]["src"].startswith("sfm-")
    assert dev.frames[0]["params"] == {"url": "http://192.168.1.2:8098/x.zip"}
    assert [e["event"] for e in got] == ["ota_begin", "ota_progress", "ota_error"]
    assert got[1]["progress"] == 42 and got[2]["msg"] == "Unexpected end of data"


async def test_channel_digest_auth_with_stored_ha1():
    async with FakeShelly(password="pw-1") as dev:
        auth = shelly.DeviceAuth(gen=2, ha1=shelly.gen2_ha1(dev.realm, "pw-1"), realm=dev.realm)
        async with REAL_CHANNEL(dev.origin, auth) as ch:
            assert await ch.update("http://h/x.zip") is True
        assert dev.frames[1]["auth"]["username"] == "admin" and dev.frames[1]["auth"]["algorithm"] == "SHA-256"
        wrong = shelly.DeviceAuth(gen=2, ha1=shelly.gen2_ha1(dev.realm, "nope"), realm=dev.realm)
        async with REAL_CHANNEL(dev.origin, wrong) as ch:
            with pytest.raises(shelly.AuthError):
                await ch.update("http://h/x.zip")
        other = shelly.DeviceAuth(gen=2, ha1="0" * 64, realm="another-device")
        async with REAL_CHANNEL(dev.origin, other) as ch:
            with pytest.raises(shelly.ShellyError, match="another device ID"):
                await ch.update("http://h/x.zip")


def test_ws_auth_refuses_downgrades():
    auth = shelly.DeviceAuth(gen=2, password="x")
    for challenge in ({"auth_type": "basic", "realm": "r", "nonce": "n"},
                      {"auth_type": "digest", "realm": "r", "nonce": "n", "algorithm": "MD5"},
                      {"auth_type": "digest", "realm": "", "nonce": "n"}, "not a dict"):
        with pytest.raises(shelly.ShellyError):
            auth.ws_auth(challenge)


async def test_channel_refuses_redirects():
    async with FakeShelly(redirect=True) as dev:
        with pytest.raises(shelly.ShellyError, match="redirect"):
            async with REAL_CHANNEL(dev.origin):
                pass


def install_device(m, gen=2):
    """A ready device A for Manager._install."""
    dev = {"name": "Kitchen", "gen": gen, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
           "version": "1.4.0", "pending_version": "1.5.0", "pending_build": "b-old", "status": manager.STATUS_READY}
    m.devices[A] = dev
    return dev


@pytest.fixture
def fast_install(monkeypatch):
    """Short install timings and a device that answers with fixed versions."""
    monkeypatch.setattr(manager, "INSTALL_FIRST_CHECK_S", 0.05)
    monkeypatch.setattr(manager, "INSTALL_POLL_S", 0.05)
    monkeypatch.setattr(manager, "INSTALL_NO_DOWNLOAD_S", 5)
    monkeypatch.setattr(manager, "INSTALL_WAIT_S", 10)
    monkeypatch.setattr(shelly, "file_sha256", lambda *_a: "s")
    calls = {"http": 0}

    async def device_info(*_args, **_kwargs):
        return {"mac": "AA", "build": "b-old", "version": "1.4.0"}

    async def trigger(*_args):
        calls["http"] += 1

    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly, "trigger_update", trigger)
    return calls


async def test_ota_error_ends_installation_at_once(mgr, fast_install, monkeypatch):
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 10},
              {"event": "ota_error", "msg": "Connection error: -14"}]
    async with FakeShelly(events=events) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr)
        started = time.monotonic()
        with pytest.raises(shelly.OtaError, match="Connection error: -14"):
            await mgr._install(A, dev, "s")
        assert time.monotonic() - started < 2  # not the 3-minute early stop
    assert fast_install["http"] == 0 and "ota_progress" not in dev


async def test_ota_error_marks_install_failed(mgr, fast_install, monkeypatch):
    async with FakeShelly(events=[{"event": "ota_error", "msg": "Bad url"}]) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr)
        dev["sha256"] = "s"
        monkeypatch.setattr(mgr, "approval_matches", lambda *_a: True)
        assert await mgr.install(A, "s", "u") is False
    assert dev["status"] == manager.STATUS_ERROR and "Bad url" in dev["message"]
    assert mgr.events.entries[-1]["event"] == "failed"


async def test_progress_is_shown_and_success_confirmed(mgr, fast_install, monkeypatch):
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 55}]
    async with FakeShelly(events=events) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr)
        seen = []

        async def device_info(*_args, **_kwargs):
            seen.append((dev.get("ota_progress"), dev.get("message")))
            version = "1.5.0" if len(seen) > 3 else "1.4.0"
            return {"mac": "AA", "build": "b-old", "version": version}

        monkeypatch.setattr(shelly, "device_info", device_info)
        assert await mgr._install(A, dev, "s") == (True, None)
    assert (55, "The Shelly is downloading the firmware: 55 %") in seen
    assert dev["status"] == manager.STATUS_CURRENT and "ota_progress" not in dev


async def test_no_websocket_falls_back_to_http(mgr, fast_install, monkeypatch):
    """conftest: no device WebSocket -> the update command goes over HTTP; Gen1 always HTTP."""
    for gen in (2, 1):
        dev = install_device(mgr, gen=gen)
        monkeypatch.setattr(manager, "INSTALL_NO_DOWNLOAD_S", 0.1)
        ok, diagnosis = await mgr._install(A, dev, "s")
        assert not ok and "never requested" in diagnosis
    assert fast_install["http"] == 2


# ---------------------------------------------------------------- R4 / R6


async def test_download_diagnosis_per_device(mgr):
    for key, ip in ((A, "192.168.1.10"), (B, "192.168.1.11")):
        mgr.devices[key] = {"status": manager.STATUS_READY, "file": "x.zip", "origin": f"http://{ip}"}
    assert mgr.may_download("x.zip", "192.168.1.10")
    mgr.record_download("x.zip", "192.168.1.10")
    assert mgr._downloaded(mgr.devices[A]) and not mgr._downloaded(mgr.devices[B])
    mgr._reset_download(mgr.devices[B])  # B starts: A's record stays
    assert mgr._downloaded(mgr.devices[A])
    mgr.record_download("x.zip", "172.30.32.1")  # NAT: cannot be attributed
    assert mgr._downloaded(mgr.devices[B])


async def test_only_get_counts_as_served(mgr, monkeypatch):
    content = b"x" * 2048
    name = hashlib.sha256(content).hexdigest() + ".zip"
    manager.FW_DIR.mkdir(parents=True, exist_ok=True)
    (manager.FW_DIR / name).write_bytes(content)
    monkeypatch.setattr(download_server, "FW_DIR", manager.FW_DIR)  # bound at import
    mgr.devices[A] = {"status": manager.STATUS_READY, "file": name, "origin": "http://127.0.0.1"}
    async with TestClient(TestServer(download_server.build_download_app(mgr))) as client:
        assert (await client.head(f"/{name}")).status == 200
        assert not mgr._downloaded(mgr.devices[A])
        r = await client.get(f"/{name}")
        assert r.status == 200 and await r.read() == content
    assert mgr.downloads[name]["served"] == {"127.0.0.1": 1}


# ---------------------------------------------------------------- R5


async def test_servers_are_stopped_in_parallel(app_env, monkeypatch):  # noqa: F811
    from aiohttp import web as aioweb

    running, overlap = 0, []
    original = aioweb.AppRunner.cleanup

    async def slow_cleanup(self):
        nonlocal running
        running += 1
        overlap.append(running)
        await asyncio.sleep(0.2)
        running -= 1
        await original(self)

    monkeypatch.setattr(aioweb.AppRunner, "cleanup", slow_cleanup)
    task = asyncio.create_task(main.main())
    await asyncio.sleep(0.2)
    await _send_sigterm()
    assert await asyncio.wait_for(task, 5) is None
    assert max(overlap) == 2  # both servers stopped at the same time


# ---------------------------------------------------------------- R7


async def test_cancelled_check_cancels_shared_offer_task(mgr, net, monkeypatch):
    """Two devices of one type wait for one shared offer task; cancelling the check
    leaves no task running (rc4 result confirmed for the offer as well)."""
    gate = asyncio.Event()

    async def slow_offer(*_args, **_kwargs):
        await gate.wait()

    monkeypatch.setattr(shelly, "fetch_offer", slow_offer)
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and t is not task and not t.done()]
    assert pending  # device tasks and the shared offer task are running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [t for t in asyncio.all_tasks() if t is not current and not t.done()] == []
