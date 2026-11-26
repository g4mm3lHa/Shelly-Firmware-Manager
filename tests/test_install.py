"""Installation: target version verification, Gen2+ OTA WebSocket, HTTP fallback,
download diagnosis per device, check after confirmed installations.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import data_paths
import download_server
import manager_core
import manager_installs
import pytest
import shelly
import shelly_cloud
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from conftest import A, B, C
from helpers import store


def test_install_timing_constants():
    """Polling (devices without OTA events): first look after 30 s, every 10 s, early
    abort after 2 min without download, at most 5 min. OTA events: 60 s without
    progress (120 s with a weak Wi-Fi signal), 30 s for the restart, 90 s after it, polled
    every 5 s. Before the update command: 5 attempts within 60 s while the Shelly does not
    answer. Two-step update: the second pass must start within 3 min, usually within 2.5 min,
    and end within 5 min after its new download; it is expected from 2.2.0 on."""
    assert (manager_installs.INSTALL_FIRST_CHECK_S, manager_installs.INSTALL_POLL_S) == (30, 10)
    assert (manager_installs.INSTALL_NO_DOWNLOAD_S, manager_installs.INSTALL_WAIT_S) == (120, 300)
    assert (manager_installs.OTA_STALL_S, manager_installs.OTA_STALL_WEAK_S, manager_installs.OTA_RESTART_WAIT_S) == (60, 120, 30)
    assert (manager_installs.OTA_BOOT_WAIT_S, manager_installs.OTA_POLL_S) == (90, 5)
    assert (manager_installs.PRECHECK_ATTEMPTS, manager_installs.PRECHECK_WINDOW_S, manager_installs.WEAK_RSSI) == (5, 60, -80)
    assert (manager_installs.TWO_STEP_START_S, manager_installs.TWO_STEP_USUAL_S) == (180, 150)
    assert manager_installs.TWO_STEP_FINISH_S == 300
    assert manager_installs.TWO_STEP_VERSION == (2, 2, 0)


@pytest.fixture
def run_install(mgr, monkeypatch, quick_install):
    """Run Manager._install against a device answering with the given versions (None: no
    answer, e.g. while it restarts). served: the file was served to the device once (at the
    first poll); hook(version) is called before each answer after the pre-check."""
    monkeypatch.setattr(manager_installs, "INSTALL_NO_DOWNLOAD_S", 0.08)  # clearly before the upper limit

    async def run(old, target, answers, gen=2, served=False, hook=None):
        dev = {"name": "Kitchen", "gen": gen, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
               "version": old, "pending_version": target, "pending_build": "b-old"}
        mgr.devices[A] = dev
        calls = []

        async def device_info(*_args, **_kwargs):
            if not calls:  # pre-check before the update command
                calls.append("pre")
                return {"mac": "AA", "build": "b-old", "version": old}
            calls.append("poll")
            if served and "x.zip" not in mgr.downloads:
                mgr.downloads["x.zip"] = {"served": {"192.168.1.10": 1}, "rejected": set()}
            version = answers[min(len(calls) - 2, len(answers) - 1)]
            if hook is not None:
                hook(version)
            if version is None:
                raise shelly.ShellyError("not reachable")
            return {"mac": "AA", "build": "b-old", "version": version}

        async def trigger(*_args):
            return None

        monkeypatch.setattr(shelly_cloud, "file_sha256", lambda *_a: "s")
        monkeypatch.setattr(shelly, "device_info", device_info)
        monkeypatch.setattr(shelly, "trigger_update", trigger)
        ok, diagnosis = await mgr._install(A, dev, "s")
        return ok, diagnosis, dev, calls.count("poll")

    return run


async def test_exact_target_version_is_success(run_install):
    ok, diagnosis, dev, _ = await run_install("1.4.0", "1.5.0", ["1.4.0", "1.5.0"], served=True)
    assert ok and diagnosis is None and dev["status"] == manager_core.STATUS_CURRENT


async def test_gen1_compares_the_version_number(run_install):
    ok, *_ = await run_install("20230101-000000/v1.13.0-gaaaa", "20230913-112003/v1.14.0-gcb84623",
                               ["20230913-112003/v1.14.0-gcb84623"], gen=1, served=True)
    assert ok


async def test_newer_version_is_unconfirmed_with_hint(run_install):
    """A newer version than the target is no longer reported as success."""
    ok, diagnosis, dev, polls = await run_install("1.4.0", "1.5.0", ["1.6.0"])
    assert not ok and dev["status"] == manager_core.STATUS_UNCLEAR
    assert "1.6.0" in diagnosis and "1.5.0" in diagnosis
    assert polls > 10  # no early abort: waits until the upper limit


async def test_old_version_without_download_aborts_after_no_download_limit(run_install):
    ok, diagnosis, dev, polls = await run_install("1.4.0", "1.5.0", ["1.4.0"])
    assert not ok and "never requested" in diagnosis
    assert 3 <= polls < 15  # stopped at INSTALL_NO_DOWNLOAD_S, well before INSTALL_WAIT_S


async def test_old_version_after_download_keeps_waiting(run_install):
    ok, diagnosis, _dev, polls = await run_install("1.4.0", "1.5.0", ["1.4.0"], served=True)
    assert not ok and "downloaded the firmware" in diagnosis and polls > 10


REAL_CHANNEL = shelly.OtaChannel  # conftest replaces it with a fake per test


class FakeShelly:
    """Local Gen2 RPC WebSocket at /rpc with optional Digest auth (official format)."""

    def __init__(self, password=None, events=(), redirect=None, keep_open=False, blocked=None):
        """redirect: target URL the device redirects the WebSocket request to (None: no
        redirect). keep_open: after the events the WebSocket stays open until release
        is set (a device still running; closing it stands for the restart). blocked(frame)
        -> bool answers the frame with error code 429 before the password check (the
        sign-in block of firmware 2.0+)."""
        self.password, self.events, self.redirect, self.blocked = password, list(events), redirect, blocked
        self.keep_open, self.release = keep_open, asyncio.Event()
        self.realm = "shellyplus1-aabbcc"
        self.frames = []

    def challenge(self):
        return {"auth_type": "digest", "nonce": "AAAA-nonce", "realm": self.realm, "algorithm": "SHA-256"}

    def valid(self, auth):
        sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
        ha1 = sha(f"admin:{self.realm}:{self.password}")
        expected = sha(f"{ha1}:{auth['nonce']}:{auth['nc']}:{auth['cnonce']}:auth:{sha('dummy_method:dummy_uri')}")
        return auth.get("response") == expected and auth.get("nc") == "00000001" and isinstance(auth.get("cnonce"), int)

    async def handler(self, request):
        if self.redirect:
            raise web.HTTPFound(self.redirect)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            frame = json.loads(msg.data)
            self.frames.append(frame)
            if self.blocked is not None and self.blocked(frame):
                await ws.send_json({"id": frame["id"], "src": self.realm, "dst": frame["src"],
                                    "error": {"code": 429, "message": "Too many requests"}})
                continue
            if self.password and not (frame.get("auth") and self.valid(frame["auth"])):
                await ws.send_json({"id": frame["id"], "src": self.realm, "dst": frame["src"],
                                    "error": {"code": 401, "message": json.dumps(self.challenge())}})
                continue
            await ws.send_json({"id": frame["id"], "src": self.realm, "dst": frame["src"], "result": None})
            for event in self.events:
                await ws.send_json({"src": self.realm, "dst": frame["src"], "method": "NotifyEvent",
                                    "params": {"ts": 1.0, "events": [{"component": "sys", "ts": 1.0, **event}]}})
            if self.keep_open:
                await self.release.wait()
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


@pytest.fixture
def short_block_wait(monkeypatch):
    """The wait after code 429 to a frame without credentials, shortened for the tests."""
    monkeypatch.setattr(shelly, "AUTH_BLOCK_WAIT_S", 0.01)


async def test_channel_429_to_the_unsigned_frame_waits_once(short_block_wait):
    """Code 429 to the first, unsigned frame: one wait, a new unsigned frame, then the
    signed one is accepted."""
    async with FakeShelly(password="pw-1", blocked=lambda frame: frame["id"] == 1) as dev:
        async with REAL_CHANNEL(dev.origin, shelly.DeviceAuth(gen=2, password="pw-1")) as ch:
            assert await ch.update("http://h/x.zip") is True
    assert [f["id"] for f in dev.frames] == [1, 2, 3] and ["auth" in f for f in dev.frames] == [False, False, True]


async def test_channel_429_after_the_wait_or_to_the_signed_frame_is_blocked(short_block_wait):
    """Code 429 still there after the wait, or to the signed frame: AuthBlockedError and no
    further frame; without credentials code 429 is an ordinary device error."""
    auth = shelly.DeviceAuth(gen=2, password="pw-1")
    async with FakeShelly(password="pw-1", blocked=lambda frame: "auth" not in frame) as dev:
        async with REAL_CHANNEL(dev.origin, auth) as ch:
            with pytest.raises(shelly.AuthBlockedError, match="temporarily blocks sign-ins"):
                await ch.update("http://h/x.zip")
    assert len(dev.frames) == 2 and not any("auth" in f for f in dev.frames)
    async with FakeShelly(password="pw-1", blocked=lambda frame: "auth" in frame) as dev:
        async with REAL_CHANNEL(dev.origin, auth) as ch:
            with pytest.raises(shelly.AuthBlockedError):
                await ch.update("http://h/x.zip")
    assert len(dev.frames) == 2 and "auth" in dev.frames[1]
    async with FakeShelly(blocked=lambda _frame: True) as dev, REAL_CHANNEL(dev.origin) as ch:
        with pytest.raises(shelly.ShellyError, match=r"^Device reports: Too many requests \(code 429\)$") as err:
            await ch.update("http://h/x.zip")
    assert not isinstance(err.value, shelly.AuthError) and len(dev.frames) == 1


class StubSocket:
    """WebSocket whose first frame is answered with the given error and whose second
    send fails (the connection ended)."""

    def __init__(self, error):
        self.error, self.sent = error, []

    async def send_json(self, frame):
        if self.sent:
            raise ConnectionResetError("Cannot write to closing transport")
        self.sent.append(frame)

    async def receive(self, timeout=None):
        return SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"id": 1, "error": self.error}))


@pytest.mark.parametrize("error", [
    {"code": 401, "message": json.dumps({"auth_type": "digest", "nonce": 7, "realm": "r", "algorithm": "SHA-256"})},
    {"code": 429, "message": "Too many requests"},
])
async def test_channel_retry_frame_that_cannot_be_sent_never_falls_back(short_block_wait, error):
    """The first frame was refused (not executed); the retry frame cannot be sent: a
    ShellyError (nothing installed), never ChannelUnavailable – that would send the
    update command over HTTP."""
    channel = REAL_CHANNEL("http://127.0.0.1:9", shelly.DeviceAuth(gen=2, password="pw-1"))
    channel.ws = StubSocket(error)
    with pytest.raises(shelly.ShellyError, match="^Connection to the Shelly lost before the update command – "
                                                 "nothing was installed$"):
        await channel.update("http://h/x.zip")
    assert len(channel.ws.sent) == 1


def test_ws_auth_refuses_downgrades():
    auth = shelly.DeviceAuth(gen=2, password="x")
    for challenge in ({"auth_type": "basic", "realm": "r", "nonce": "n"},
                      {"auth_type": "digest", "realm": "r", "nonce": "n", "algorithm": "MD5"},
                      {"auth_type": "digest", "realm": "", "nonce": "n"}, "not a dict"):
        with pytest.raises(shelly.ShellyError):
            auth.ws_auth(challenge)


async def test_channel_refuses_redirects():
    async with FakeShelly(redirect="http://203.0.113.1/rpc") as dev:
        with pytest.raises(shelly.ShellyError, match="redirect") as err:
            async with REAL_CHANNEL(dev.origin):
                pass
    assert not isinstance(err.value, shelly.HttpsRequiredError)


async def test_channel_names_a_redirect_to_https():
    """A WebSocket redirect to HTTPS is refused as HttpsRequiredError; its target is not in the text."""
    async with FakeShelly(redirect="https://192.168.1.10/rpc") as dev:
        with pytest.raises(shelly.HttpsRequiredError) as err:
            async with REAL_CHANNEL(dev.origin):
                pass
    assert str(err.value) == shelly.HTTPS_REQUIRED and "192.168.1.10" not in str(err.value)


def install_device(m, gen=2, old="1.4.0", target="1.5.0"):
    """A ready device A for Manager._install (installed version old, update to target)."""
    dev = {"name": "Kitchen", "gen": gen, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
           "version": old, "pending_version": target, "pending_build": "b-old", "status": manager_core.STATUS_READY}
    m.devices[A] = dev
    return dev


@pytest.fixture
def ota_install(monkeypatch):
    """Installs with the device WebSocket: polling slow enough for the OTA events to
    arrive (50 ms), generous limits, and a device that keeps reporting the old version."""
    monkeypatch.setattr(manager_installs, "INSTALL_FIRST_CHECK_S", 0.05)
    monkeypatch.setattr(manager_installs, "INSTALL_POLL_S", 0.05)
    monkeypatch.setattr(manager_installs, "INSTALL_NO_DOWNLOAD_S", 5)
    monkeypatch.setattr(manager_installs, "INSTALL_WAIT_S", 10)
    monkeypatch.setattr(manager_installs, "OTA_TICK_S", 0.01)
    monkeypatch.setattr(manager_installs, "OTA_POLL_S", 0.02)
    monkeypatch.setattr(manager_installs, "OTA_STALL_S", 0.3)
    monkeypatch.setattr(manager_installs, "OTA_RESTART_WAIT_S", 0.3)
    monkeypatch.setattr(manager_installs, "OTA_BOOT_WAIT_S", 0.3)
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 0.4)
    monkeypatch.setattr(manager_installs, "TWO_STEP_USUAL_S", 0.3)
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 0.6)
    monkeypatch.setattr(shelly_cloud, "file_sha256", lambda *_a: "s")
    calls = {"http": 0}

    async def device_info(*_args, **_kwargs):
        return {"mac": "AA", "build": "b-old", "version": "1.4.0"}

    async def trigger(*_args):
        calls["http"] += 1

    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly, "trigger_update", trigger)
    return calls


async def test_ota_error_ends_installation_at_once(mgr, ota_install, monkeypatch):
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 10},
              {"event": "ota_error", "msg": "Connection error: -14"}]
    async with FakeShelly(events=events) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr)
        started = time.monotonic()
        with pytest.raises(shelly.OtaError, match="Connection error: -14"):
            await mgr._install(A, dev, "s")
        assert time.monotonic() - started < 2  # not the 3-minute early stop
    assert ota_install["http"] == 0 and "ota_progress" not in dev


async def test_ota_error_marks_install_failed(mgr, ota_install, monkeypatch):
    async with FakeShelly(events=[{"event": "ota_error", "msg": "Bad url"}]) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr)
        dev["sha256"] = "s"
        monkeypatch.setattr(mgr, "approval_matches", lambda *_a: True)
        assert await mgr.install(A, "s", "u") is False
    assert dev["status"] == manager_core.STATUS_ERROR and "Bad url" in dev["message"]
    assert mgr.events.entries[-1]["event"] == "failed"


async def test_progress_is_shown_and_success_confirmed(mgr, ota_install, monkeypatch):
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
    assert dev["status"] == manager_core.STATUS_CURRENT and "ota_progress" not in dev


DONE = [{"event": "ota_progress", "msg": "", "progress_percent": 100}, {"event": "ota_success", "msg": ""}]


async def ota_run(mgr, monkeypatch, events, versions, keep_open=False, old="1.4.0", target="1.5.0", **options):
    """Install over FakeShelly; after the pre-check device_info answers from versions
    (None: no answer, the last entry repeats). Options: hook(version) is called before each
    answer after the pre-check; full=True runs the whole path (Manager.install: status, log);
    step_target is stored on the device. Returns (result or exception, device, phases seen)."""
    hook, full = options.get("hook"), options.get("full", False)
    async with FakeShelly(events=events, keep_open=keep_open) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr, old=old, target=target)
        if "step_target" in options:
            dev["step_target"] = options["step_target"]
        answers, phases = [old, *versions], []  # first: the pre-check of the installation

        async def device_info(*_args, **_kwargs):
            phases.append(dev.get("ota_phase"))
            version = answers.pop(0) if len(answers) > 1 else answers[0]
            if hook is not None and len(phases) > 1:
                hook(version)
            if version is None:
                raise shelly.ShellyError("not reachable")
            return {"mac": "AA", "build": "b-old", "version": version}

        monkeypatch.setattr(shelly, "device_info", device_info)
        try:
            if full:
                dev["sha256"] = "s"
                monkeypatch.setattr(mgr, "approval_matches", lambda *_a: True)
                result = await mgr.install(A, "s", "u")
            else:
                mgr.installing.add(A)  # as Manager.install does around it
                result = await mgr._install(A, dev, "s")
        except shelly.ShellyError as err:
            result = err
        finally:
            mgr.installing.discard(A)
        fake.release.set()
    return result, dev, phases


async def test_ota_success_restart_and_new_version(mgr, ota_install, monkeypatch):
    """100 % / ota_success, the WebSocket closes (restart): "Restarting", then the
    new version is confirmed; no polling limit of the fallback is involved."""
    result, dev, phases = await ota_run(mgr, monkeypatch, DONE, [None, "1.5.0"])
    assert result == (True, None) and dev["status"] == manager_core.STATUS_CURRENT
    assert "restarting" in phases and "ota_phase" not in dev and ota_install["http"] == 0


async def test_ota_old_version_after_restart_is_an_error(mgr, ota_install, monkeypatch):
    """No sign of a two-step update (offer, device steps, crossing 2.2.0): an error at once."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 5)
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, ["1.4.0"])
    assert isinstance(result, shelly.ShellyError) and "still reports 1.4.0 – the update was not applied" in str(result)
    assert time.monotonic() - started < 2  # no wait for a second pass


# ---- Two-step update: the device installs the update a second time by itself


def second_pass_views(mgr, seen):
    """hook for ota_run: records the step view and the shown progress at each answer."""
    return lambda _version: seen.append((mgr.install_step_view(A), mgr.devices[A].get("ota_progress")))


def running_for(monkeypatch, uptime):
    """shelly.link_status reports the running time uptime (0: the device just restarted;
    a large value: it kept running, the connection was lost). Returns the list of reads."""
    reads = []

    async def link(*_args, **_kwargs):
        reads.append(uptime)
        return {"uptime": uptime, "rssi": None}

    monkeypatch.setattr(shelly, "link_status", link)
    return reads


def serve_again(mgr, address="192.168.1.10", at=2, seen=None):
    """hook for ota_run: before the at-th answer on the old version 2.1.1 the download server
    serves the file again to address (the device's own one by default). seen: step views."""
    old_answers = []

    def hook(version):
        if seen is not None:
            seen.append(mgr.install_step_view(A))
        if version == "2.1.1":
            old_answers.append(version)
        if len(old_answers) == at and not hook.done:
            hook.done = True
            mgr.may_download("x.zip", address)
            mgr.record_download("x.zip", address)

    hook.done = False
    return hook


async def test_ota_two_step_update_confirmed_after_second_pass(mgr, ota_install, monkeypatch):
    """2.1.1 -> 2.2.0 crosses 2.2.0: back on 2.1.1 after the restart, the app waits for the
    second pass (step install with second_pass, reboot while down) and confirms 2.2.0."""
    seen = []
    result, dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1", "2.1.1", None, "2.2.0"],
                                         old="2.1.1", target="2.2.0", hook=second_pass_views(mgr, seen))
    assert result == (True, None) and dev["status"] == manager_core.STATUS_CURRENT
    passes = [(view["step"], progress) for view, progress in seen if view and view["second_pass"]]
    assert ("install", None) in passes and ("reboot", None) in passes  # no stale progress of the first pass
    install = next(view for view, _ in seen if view and view["second_pass"])
    assert (install["n"], install["usual"], install["give_up"]) == (4, 0.3, 0.4)


@pytest.mark.parametrize("signal", ["device", "offer"])
async def test_ota_two_step_by_device_steps_or_offer(mgr, ota_install, monkeypatch, signal):
    """1.4.0 -> 1.5.0 does not cross 2.2.0: the device's nsteps or the offer with several
    steps (step_target "") still makes the app wait for the second pass."""
    asked = []

    async def nsteps(_session, _origin, version, _auth=None):
        asked.append(version)
        return 1 if signal == "device" else None

    monkeypatch.setattr(shelly, "update_nsteps", nsteps)
    options = {"step_target": ""} if signal == "offer" else {}
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "1.4.0", None, "1.5.0"], **options)
    assert result == (True, None) and asked == ["1.5.0"]


async def test_ota_two_step_second_pass_never_comes(mgr, ota_install, monkeypatch):
    """Nothing starts within TWO_STEP_START_S: an error that says to start the update again;
    the file stays provided for the retry, nothing more is served after the error."""
    result, dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0",
                                         full=True)
    assert result is False and dev["status"] == manager_core.STATUS_ERROR and dev["install_failed"]
    assert dev["message"] == ("After the restart the Shelly still reports 2.1.1. The update to 2.2.0 can run in two "
                              "steps, but the second step did not start within 0.4 s – start the update again")
    assert mgr.events.entries[-1]["event"] == "failed" and "two_step" not in mgr.events.entries[-1]
    assert not mgr.may_download("x.zip", "192.168.1.10")


async def test_ota_two_step_other_version_is_an_error(mgr, ota_install, monkeypatch):
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1", "2.1.5"], old="2.1.1",
                                          target="2.2.0")
    assert isinstance(result, shelly.ShellyError) and "reports 2.1.5 instead of 2.2.0" in str(result)


async def test_second_pass_download_is_served_again(mgr, ota_install, monkeypatch):
    """During the second pass the file is still served to the device; a new download starts
    the second pass. Back on the old version after its restart (it runs shorter than the gap):
    the update was not applied."""
    running_for(monkeypatch, 0)
    served, old_answers = [], []

    def hook(version):
        if version == "2.1.1":
            old_answers.append(version)
        if len(old_answers) == 2 and not served:  # the first answer on 2.1.1 started the wait
            served.append(mgr.may_download("x.zip", "192.168.1.10"))
            mgr.record_download("x.zip", "192.168.1.10")

    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1", "2.1.1", None, "2.1.1"],
                                          old="2.1.1", target="2.2.0", hook=hook)
    assert served == [True]
    assert isinstance(result, shelly.ShellyError) and "still reports 2.1.1 – the update was not applied" in str(result)


async def test_second_pass_restart_cut_off_while_down(mgr, ota_install, monkeypatch):
    """The device goes down shortly before TWO_STEP_START_S ends: the second pass has started,
    so the boot limit applies – without an answer the error is "No answer", not "did not start"."""
    versions = [None, *["2.1.1"] * 14, None]
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0")
    assert isinstance(result, shelly.ShellyError) and "No answer from the Shelly within 0.3 s" in str(result)


async def test_two_step_log_mark(mgr, ota_install, monkeypatch):
    """A confirmed two-step installation is marked in the log; a normal one is not."""
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1", None, "2.2.0"], old="2.1.1",
                                          target="2.2.0", full=True)
    entry = mgr.events.entries[-1]
    assert result is True and entry["event"] == "confirmed" and entry["two_step"] is True
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "1.5.0"], full=True)
    assert result is True and "two_step" not in mgr.events.entries[-1]


async def test_polling_waits_for_the_second_pass(run_install, monkeypatch):
    """Without OTA events: downloaded, restarted, back on 2.1.1 – the limit grows by the
    second-pass window, and 2.2.0 after the usual limit is confirmed."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 0.8)
    running_for(monkeypatch, 0)
    ok, diagnosis, dev, polls = await run_install("2.1.1", "2.2.0", ["2.1.1", None, *["2.1.1"] * 35, "2.2.0"],
                                                  served=True)
    assert ok and diagnosis is None and polls > 30 and dev["status"] == manager_core.STATUS_CURRENT


async def test_polling_second_pass_missing_is_unconfirmed(run_install, monkeypatch, mgr):
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 0.4)
    running_for(monkeypatch, 0)
    ok, diagnosis, dev, _polls = await run_install("2.1.1", "2.2.0", ["2.1.1", None, "2.1.1"], served=True)
    assert not ok and dev["status"] == manager_core.STATUS_UNCLEAR
    assert diagnosis == ("The Shelly still reports 2.1.1. The update to 2.2.0 can run in two steps, but the second "
                         "step did not finish – check the device, then install again")
    assert mgr.install_steps[A]["second_pass"]


@pytest.mark.parametrize("uptime", [None, 10_000])
async def test_polling_lost_connection_is_no_second_pass(run_install, monkeypatch, mgr, uptime):
    """One poll without answer while the first pass still downloads, then the old version: the
    device kept running (or its running time is unknown), so no second pass is shown, the limit
    stays, and 2.2.0 in one pass is no two-step installation."""
    asked = []

    async def nsteps(*_args, **_kwargs):
        asked.append(True)
        return 1

    monkeypatch.setattr(shelly, "update_nsteps", nsteps)
    reads = running_for(monkeypatch, uptime)
    ok, diagnosis, _dev, _polls = await run_install("2.1.1", "2.2.0", ["2.1.1", None, "2.1.1", "2.2.0"], served=True)
    assert ok and diagnosis is None
    assert not mgr.install_steps[A]["second_pass"] and not asked and reads[-1:] == [uptime]
    assert mgr.install_steps[A]["give_up"] == manager_installs.INSTALL_WAIT_S


async def test_second_pass_lost_connection_during_download_goes_on(mgr, ota_install, monkeypatch):
    """One poll without answer during the new download, then the old version again: the device
    kept running (the connection was lost), so the second pass goes on with its total limit and
    the target version is confirmed."""
    running_for(monkeypatch, 10_000)
    seen = []
    versions = [None, "2.1.1", "2.1.1", None, "2.1.1", "2.1.1", "2.2.0"]
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr, seen=seen))
    assert result == (True, None)
    after = [view for view in seen if view and view["second_pass"]]
    assert [view["step"] for view in after][-2:] == ["install", "install"]
    assert (after[-1]["give_up"], after[-1]["usual"]) == (0.6, manager_installs.STEP_USUAL_POLLING_S)


async def test_second_pass_total_limit_after_the_new_download(mgr, ota_install, monkeypatch):
    """After the new download the whole second pass has TWO_STEP_FINISH_S: still the old version
    then is an error that names the limit and says to start the update again; the pill can turn
    yellow before it (usual below give_up)."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 5)
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 0.2)
    monkeypatch.setattr(manager_installs, "STEP_USUAL_POLLING_S", 0.1)
    seen = []
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr, seen=seen))
    assert isinstance(result, shelly.ShellyError)
    assert str(result) == ("After the restart the Shelly still reports 2.1.1. The update to 2.2.0 can run in two "
                           "steps, but the second step did not finish within 0.2 s – start the update again")
    downloading = [view for view in seen if view and view["give_up"] == 0.2]
    assert downloading and downloading[-1]["usual"] < downloading[-1]["give_up"]


def other_device(mgr, status=manager_core.STATUS_CURRENT, file=None):
    """A second known device B at 192.168.1.11 (with file: its installation of that file runs)."""
    mgr.devices[B] = {"name": "Hall", "gen": 2, "origin": "http://192.168.1.11", "mac": "BB", "status": status,
                      "file": file, "version": "2.1.1"}
    if file is not None:
        mgr.installing.add(B)


async def test_second_pass_counts_an_unattributable_download_of_the_only_installation(mgr, ota_install,
                                                                                      monkeypatch):
    """NAT (Docker without the sender IP, restrict_downloads_to_devices off): the download comes
    from an address of no known device. While no other device installs the file it can only be
    this device's: the second pass started, so the total limit after it applies and the target
    version after the start window is confirmed."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    mgr.restrict_downloads = False
    versions = [None, *["2.1.1"] * 30, "2.2.0"]
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr, address="172.30.32.1"))
    assert result == (True, None)
    assert time.monotonic() - started > manager_installs.TWO_STEP_START_S  # confirmed after the start window


async def test_second_pass_never_counts_the_download_of_another_known_device(mgr, ota_install, monkeypatch):
    """A download from the address of another known Shelly is that device's: the start window stays."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    mgr.restrict_downloads = False
    other_device(mgr)
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr, address="192.168.1.11"))
    assert isinstance(result, shelly.ShellyError) and "did not start within 0.4 s" in str(result)


async def test_second_pass_never_counts_an_ambiguous_download(mgr, ota_install, monkeypatch):
    """NAT while another device installs the same file: the download cannot be attributed, so it
    does not start the second pass and the start window stays as it is."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    mgr.restrict_downloads = False
    other_device(mgr, manager_core.STATUS_INSTALLING, "x.zip")
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr, address="172.30.32.1"))
    assert isinstance(result, shelly.ShellyError) and "did not start within 0.4 s" in str(result)
    assert time.monotonic() - started < 2  # not the total limit of a started second pass


async def test_second_pass_never_counts_the_download_of_an_installation_still_sending_its_command(
        mgr, ota_install, monkeypatch):
    """NAT: B installs the same file but its status is not installing yet (it still waits for the
    answer to its update command, and the Shelly already downloads). B's download does not count
    for A: the start window stays, with its own error."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    mgr.restrict_downloads = False
    other_device(mgr, manager_core.STATUS_READY, "x.zip")
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr, address="172.30.32.1"))
    assert isinstance(result, shelly.ShellyError) and "did not start within 0.4 s" in str(result)
    assert time.monotonic() - started < 2  # not the total limit of a started second pass


async def test_second_pass_judges_a_nat_download_when_it_arrives(mgr, ota_install, monkeypatch):
    """NAT: B's download arrives while B installs the same file, and B's installation ends before
    A's next poll (e.g. an ota_error). The download stays B's: the start window of A stays."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    mgr.restrict_downloads = False
    other_device(mgr, manager_core.STATUS_INSTALLING, "x.zip")
    serve = serve_again(mgr, address="172.30.32.1")

    def hook(version):
        serve(version)
        if serve.done and B in mgr.installing:  # B ends right after its download
            mgr.devices[B]["status"] = manager_core.STATUS_ERROR
            mgr.installing.discard(B)

    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0",
                                          hook=hook)
    assert isinstance(result, shelly.ShellyError) and "did not start within 0.4 s" in str(result)


def test_nat_downloads_are_attributed_to_the_only_installation(mgr):
    """A GET from an address of no known device counts for the installation of its file only
    while it is the only one; the device's own address always counts for it."""
    dev = install_device(mgr)
    mgr.restrict_downloads = False
    mgr.installing.add(A)
    assert mgr.may_download("x.zip", "172.30.32.1")
    mgr.record_download("x.zip", "172.30.32.1")
    assert mgr._own_downloads(A, dev) == 1
    other_device(mgr, manager_core.STATUS_READY, "x.zip")
    mgr.record_download("x.zip", "172.30.32.1")  # two installations: B's or A's
    mgr.record_download("x.zip", "192.168.1.11")  # B's own address: never A's
    mgr.record_download("x.zip", "192.168.1.10")  # A's own address
    assert mgr._own_downloads(A, dev) == 2 and mgr._own_downloads(B, mgr.devices[B]) == 1


def nat_batch(mgr, own=None, finish=4):
    """hook for ota_run behind NAT while device B installs the same file: before the 2nd old
    answer of A the download server serves the file to the NAT address (B's download), before
    the finish-th old answer B leaves the installing status; own: before that old answer A
    downloads the file again (NAT, B is done)."""
    old_answers = []

    def hook(version):
        if version != "2.1.1":
            return
        old_answers.append(version)
        if len(old_answers) in (2, own):
            mgr.may_download("x.zip", "172.30.32.1")
            mgr.record_download("x.zip", "172.30.32.1")
        if len(old_answers) == finish:
            mgr.devices[B]["status"] = manager_core.STATUS_CURRENT
            mgr.installing.discard(B)

    return hook


async def test_second_pass_behind_nat_counts_from_its_own_download(mgr, ota_install, monkeypatch):
    """NAT while B installs the same file: B's download does not count for A, and B leaving the
    installing status is no download either. A's own later download (B done) starts the second
    pass, so its total limit counts from there and the target version is confirmed."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 5)
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 0.6)
    mgr.restrict_downloads = False
    other_device(mgr, manager_core.STATUS_INSTALLING, "x.zip")
    seen = []
    hook = nat_batch(mgr, own=40)

    def watched(version):
        hook(version)
        seen.append(mgr.install_step_view(A))

    versions = [None, *["2.1.1"] * 50, "2.2.0"]
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0",
                                          hook=watched)
    assert result == (True, None)
    limits = [view["give_up"] for view in seen if view and view["second_pass"]]
    assert limits.index(0.6) > 30  # the total limit only from A's own download (the 40th old answer) on


async def test_second_pass_a_finished_batch_is_no_download(mgr, ota_install, monkeypatch):
    """NAT while B installs the same file: after B finished, A goes down and answers on its old
    version again, having restarted. Without a download of its own that is no "not applied":
    the start window goes on and ends with its own error."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    running_for(monkeypatch, 0)
    mgr.restrict_downloads = False
    other_device(mgr, manager_core.STATUS_INSTALLING, "x.zip")
    versions = [None, *["2.1.1"] * 5, None, "2.1.1"]
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0",
                                          hook=nat_batch(mgr))
    assert isinstance(result, shelly.ShellyError) and "did not start within 0.4 s" in str(result)


def test_the_address_of_an_installing_device_is_always_counted(mgr):
    """The diagnosis keeps MAX_SERVED_IPS addresses per file, but the download of a device that
    installs the file is counted even when they are taken (earlier installations of a batch)."""
    dev = install_device(mgr)
    dev["status"] = manager_core.STATUS_INSTALLING
    mgr.downloads["x.zip"] = {"served": {f"192.168.2.{n}": 1 for n in range(manager_installs.MAX_SERVED_IPS)},
                              "rejected": set()}
    mgr.record_download("x.zip", "192.168.3.1")  # another client: not recorded
    mgr.record_download("x.zip", "192.168.1.10")
    assert "192.168.3.1" not in mgr.downloads["x.zip"]["served"]
    assert mgr.downloads["x.zip"]["served"]["192.168.1.10"] == 1


async def test_second_pass_download_during_a_gap_is_checked_for_a_restart(mgr, ota_install, monkeypatch):
    """The device downloads the file again while it does not answer (weak Wi-Fi), then answers
    on its old version and runs shorter than the gap: it restarted after the new download, so
    the update was not applied – at once, not after the total limit."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    reads = running_for(monkeypatch, 0)
    gaps = []

    def hook(version):
        if version is None:
            gaps.append(version)
            if len(gaps) == 2:  # the gap of the second pass (the first one is the restart of the first pass)
                mgr.may_download("x.zip", "192.168.1.10")
                mgr.record_download("x.zip", "192.168.1.10")

    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1", "2.1.1", None, "2.1.1"],
                                          old="2.1.1", target="2.2.0", hook=hook)
    assert isinstance(result, shelly.ShellyError) and "still reports 2.1.1 – the update was not applied" in str(result)
    assert time.monotonic() - started < 2 and reads[-1] == 0


def refused_after(monkeypatch, refusal, first=1):
    """shelly.link_status answers the first reads (the pre-check before the update command),
    then raises refusal (a refused or blocked sign-in). Returns the list of reads."""
    reads = []

    async def link(*_args, **_kwargs):
        reads.append(len(reads) + 1)
        if len(reads) > first:
            raise refusal
        return {"uptime": None, "rssi": None}

    monkeypatch.setattr(shelly, "link_status", link)
    return reads


@pytest.mark.parametrize("refusal", [shelly.AuthBlockedError("blocked"), shelly.AuthError("password")])
async def test_refused_running_time_after_the_command_keeps_the_installation(mgr, ota_install, monkeypatch,
                                                                            refusal):
    """Second pass over the device connection: the running time after a gap meets a refused or
    blocked sign-in. That is an unknown running time (no restart), the second pass goes on and
    is confirmed; no further signed request goes to the device (later gaps read nothing)."""
    reads = refused_after(monkeypatch, refusal)
    versions = [None, "2.1.1", "2.1.1", None, "2.1.1", None, "2.1.1", "2.2.0"]
    result, dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0",
                                         hook=serve_again(mgr), full=True)
    assert result is True and dev["status"] == manager_core.STATUS_CURRENT and not dev.get("install_failed")
    assert mgr.events.entries[-1]["event"] == "confirmed" and reads == [1, 2]  # pre-check, then one refused read


async def test_refused_update_steps_end_the_signed_requests(mgr, ota_install, monkeypatch):
    """A refused sign-in while the device steps are read (two-step signal): no signal from the
    device, and the running time after a later gap is not read either."""
    reads = running_for(monkeypatch, 0)
    asked = []

    async def nsteps(*_args, **_kwargs):
        asked.append(True)
        raise shelly.AuthBlockedError("blocked")

    monkeypatch.setattr(shelly, "update_nsteps", nsteps)
    versions = [None, "2.1.1", "2.1.1", None, "2.1.1", "2.2.0"]
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, versions, old="2.1.1", target="2.2.0",
                                          hook=serve_again(mgr))
    assert result == (True, None) and asked == [True] and reads == [0]  # only the pre-check read
    assert mgr.install_steps[A]["signin_refused"]


def test_no_signed_version_query_after_a_refusal(mgr, monkeypatch):
    """After a refused sign-in in the installation the version queries carry no credentials."""
    monkeypatch.setattr(mgr.credentials, "auth_for", lambda *_a: "credentials")
    mgr._set_step(A, "install")
    assert mgr._poll_auth(A)(2) == "credentials"
    mgr._update_step(A, signin_refused=True)
    mgr._set_step(A, "reboot")  # stays with the installation
    assert mgr._poll_auth(A)(2) is None and mgr._signin_refused(A)


async def test_refused_version_query_ends_the_signed_requests(mgr, monkeypatch):
    """A version query after the update command signs Shelly.GetDeviceInfo only if /shelly lacks
    a field. A refused or blocked sign-in there is no answer and is recorded once: the next
    queries carry no credentials."""
    monkeypatch.setattr(mgr.credentials, "auth_for", lambda *_a: "credentials")
    dev = install_device(mgr)
    mgr._set_step(A, "verify")
    sent = []

    async def device_info(_session, _origin, auth_for=None):
        sent.append(auth_for(2))
        raise shelly.AuthBlockedError("blocked") if len(sent) == 1 else shelly.AuthError("password required")

    monkeypatch.setattr(shelly, "device_info", device_info)
    assert await mgr._poll_version(A, dev) is None and mgr._signin_refused(A)
    assert await mgr._poll_version(A, dev) is None
    assert sent == ["credentials", None]


async def test_ota_refused_version_query_keeps_the_installation(mgr, ota_install, monkeypatch):
    """Over the device connection: a version query after the restart meets a refused sign-in. The
    installation goes on, the later queries are unsigned, and the target version is confirmed."""
    refused = []

    def hook(version):
        if version == "refused":
            refused.append(version)
            raise shelly.AuthError("password")

    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "refused", "1.5.0"], hook=hook)
    assert result == (True, None) and refused and mgr._signin_refused(A)


async def test_polling_refused_version_query_keeps_the_installation(run_install, monkeypatch, mgr):
    """Without the device connection: the same – no error, no further signed request."""

    def hook(version):
        if version == "refused":
            raise shelly.AuthBlockedError("blocked")

    ok, diagnosis, _dev, _polls = await run_install("1.4.0", "1.5.0", ["refused", "1.5.0"], hook=hook)
    assert ok and diagnosis is None and mgr._signin_refused(A)


async def test_ota_error_during_the_second_pass_ends_it_at_once(mgr, ota_install, monkeypatch):
    """An ota_error that arrives while the app waits for the second pass (the device WebSocket is
    still open, e.g. after a timed-out query) ends the installation at once with the device's
    error, not after the start window with the generic one."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 5)
    wait = mgr._await_second_pass

    async def second_pass(device_id, dev, ota, version, polling=False):
        async def fail():
            await asyncio.sleep(0.1)
            ota["error"] = "image check failed"  # as _read_ota_events records it
            ota["failed"].set()

        task = asyncio.create_task(fail())
        try:
            return await wait(device_id, dev, ota, version, polling)
        finally:
            await task

    monkeypatch.setattr(mgr, "_await_second_pass", second_pass)
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None, "2.1.1"], old="2.1.1", target="2.2.0")
    assert isinstance(result, shelly.OtaError) and "image check failed" in str(result)
    assert time.monotonic() - started < 2


async def test_polling_refused_running_time_is_no_error(run_install, monkeypatch, mgr):
    """Without the device connection: a refused or blocked running-time read after the gap is an
    unknown running time – the wait goes on (here: the target version), never an error, and no
    further signed request is sent."""
    reads = refused_after(monkeypatch, shelly.AuthBlockedError("blocked"))
    answers = ["2.1.1", None, "2.1.1", None, "2.1.1", "2.2.0"]
    ok, diagnosis, dev, _polls = await run_install("2.1.1", "2.2.0", answers, served=True)
    assert ok and diagnosis is None and dev["status"] == manager_core.STATUS_CURRENT
    assert reads == [1, 2] and mgr.install_steps[A]["signin_refused"]


async def test_polling_second_pass_steps_follow_the_events_path(run_install, monkeypatch, mgr):
    """Without the device connection the second pass shows the same steps: "install" with
    second_pass and the usual time of the second pass counted from its start (not yellow at
    once), "reboot" while the device does not answer, "install" again after it."""
    running_for(monkeypatch, 0)
    seen = []
    answers = ["2.1.1", None, "2.1.1", "2.1.1", None, None, "2.1.1", "2.2.0"]
    ok, _diagnosis, _dev, _polls = await run_install("2.1.1", "2.2.0", answers, served=True,
                                                     hook=lambda _v: seen.append(mgr.install_step_view(A)))
    assert ok
    second = [view for view in seen if view["second_pass"]]
    assert [view["step"] for view in second] == ["install", "install", "reboot", "reboot", "install"]
    assert (second[0]["usual"], second[0]["late"], second[0]["give_up"]) == (
        manager_installs.TWO_STEP_USUAL_S, False, manager_installs.TWO_STEP_START_S)
    assert second[0]["seconds"] == 0 and second[-1]["usual"] == manager_installs.TWO_STEP_USUAL_S


async def test_polling_second_pass_has_the_total_limit_after_its_new_download(run_install, monkeypatch, mgr):
    """Without the device connection: once the device downloads the file again, the second pass
    has TWO_STEP_FINISH_S – the target version after the start window is still confirmed."""
    monkeypatch.setattr(manager_installs, "TWO_STEP_START_S", 0.2)
    monkeypatch.setattr(manager_installs, "TWO_STEP_FINISH_S", 3)
    running_for(monkeypatch, 0)
    seen = []
    answers = ["2.1.1", None, *["2.1.1"] * 40, "2.2.0"]
    ok, diagnosis, dev, _polls = await run_install("2.1.1", "2.2.0", answers, served=True,
                                                   hook=serve_again(mgr, at=12, seen=seen))
    assert ok and diagnosis is None and dev["status"] == manager_core.STATUS_CURRENT
    downloading = [view for view in seen if view and view["give_up"] == 3]
    assert downloading and downloading[-1]["usual"] == manager_installs.STEP_USUAL_POLLING_S


async def test_polling_second_pass_other_version_is_unconfirmed(run_install, monkeypatch):
    """Another version during the second pass without the device connection: unconfirmed with
    that version as hint (as on the rest of the polling path)."""
    running_for(monkeypatch, 0)
    ok, diagnosis, dev, _polls = await run_install("2.1.1", "2.2.0", ["2.1.1", None, "2.1.1", "2.1.5"],
                                                   served=True)
    assert not ok and dev["status"] == manager_core.STATUS_UNCLEAR
    assert diagnosis == "The Shelly reports 2.1.5 instead of the target version 2.2.0"


async def test_update_nsteps_reads_the_available_updates(monkeypatch):
    """Sys.GetStatus available_updates: nsteps of the entry with the target version (stable or
    beta, compared as version numbers); 0 is a value; anything else, or a failed request, is None –
    except a refused or blocked sign-in, which is raised."""
    from conftest import REAL_UPDATE_NSTEPS

    answer = {}

    async def rpc(_session, _origin, method, *_a, **_k):
        assert method == "Sys.GetStatus"
        if isinstance(answer.get("raise"), BaseException):
            raise answer["raise"]
        if answer.get("slow"):
            await asyncio.sleep(1)
        return answer["status"]

    monkeypatch.setattr(shelly, "_rpc", rpc)
    monkeypatch.setattr(shelly, "NSTEPS_TIMEOUT_S", 0.05)
    cases = [
        ({"available_updates": {"stable": {"version": "2.2.0", "nsteps": 1}}}, 1),
        ({"available_updates": {"beta": {"version": "2.2.0", "nsteps": 0}}}, 0),
        ({"available_updates": {"stable": {"version": "2.2.1", "nsteps": 1}, "beta": {"version": "2.2.0"}}}, None),
        ({"available_updates": {"stable": {"version": "2.2.0", "nsteps": True}}}, None),
        ({"available_updates": {"stable": {"version": "2.2.0", "nsteps": "1"}}}, None),
        ({"available_updates": {"stable": {"version": "2.2.0", "nsteps": 99}}}, None),
        ({"available_updates": {"stable": {"version": "2.2.0-beta1", "nsteps": 1}}}, None),
        ({"available_updates": {"stable": {"version": None, "nsteps": 1}}}, None),
        ({"available_updates": []}, None),
        ({}, None),
    ]
    for status, expected in cases:
        answer.update(status=status)
        assert await REAL_UPDATE_NSTEPS(None, "http://h", "2.2.0") == expected, status
    for failure in (shelly.ShellyError("x"), TimeoutError()):
        answer["raise"] = failure
        assert await REAL_UPDATE_NSTEPS(None, "http://h", "2.2.0") is None
    for refusal in (shelly.AuthBlockedError("blocked"), shelly.AuthError("password")):
        answer["raise"] = refusal  # the caller must know: no further signed request
        with pytest.raises(type(refusal)):
            await REAL_UPDATE_NSTEPS(None, "http://h", "2.2.0")
    answer.update(slow=True, status=cases[0][0])
    answer["raise"] = None
    assert await REAL_UPDATE_NSTEPS(None, "http://h", "2.2.0") is None  # own time limit


async def test_ota_no_answer_after_restart_is_an_error(mgr, ota_install, monkeypatch):
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, [None])
    assert isinstance(result, shelly.ShellyError) and "No answer from the Shelly within" in str(result)
    assert time.monotonic() - started < 2


async def test_ota_no_restart_is_an_error(mgr, ota_install, monkeypatch):
    """Applied, but the WebSocket stays open and the device keeps answering the old version."""
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, ["1.4.0"], keep_open=True)
    assert isinstance(result, shelly.ShellyError) and "did not restart" in str(result)


async def test_ota_restart_seen_by_polling(mgr, ota_install, monkeypatch):
    """WebSocket still open, the device stops answering: restart; then the new version."""
    result, dev, phases = await ota_run(mgr, monkeypatch, DONE, [None, None, "1.5.0"], keep_open=True)
    assert result == (True, None) and "restarting" in phases


async def test_ota_stall_without_progress_is_an_error(mgr, ota_install, monkeypatch):
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 40}]
    result, dev, _phases = await ota_run(mgr, monkeypatch, events, ["1.4.0"], keep_open=True)
    assert isinstance(result, shelly.ShellyError) and "stopped at 40 %" in str(result)


async def test_ota_stall_limit_is_longer_with_a_weak_signal(mgr, ota_install, monkeypatch):
    """A weak Wi-Fi signal at the start (-80 dBm or lower): the longer stall limit applies."""
    monkeypatch.setattr(manager_installs, "OTA_STALL_WEAK_S", 0.8)

    async def link(*_args, **_kwargs):
        return {"uptime": 100, "rssi": -85}

    monkeypatch.setattr(shelly, "link_status", link)
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 40}]
    started = time.monotonic()
    result, _dev, _phases = await ota_run(mgr, monkeypatch, events, ["1.4.0"], keep_open=True)
    assert isinstance(result, shelly.ShellyError) and "for 0.8 s" in str(result)
    assert time.monotonic() - started >= 0.8 and mgr.install_step_view(A) is not None  # _install alone keeps it


async def test_ota_websocket_lost_before_applied_falls_back_to_polling(mgr, ota_install, monkeypatch):
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 40}]
    result, _dev, phases = await ota_run(mgr, monkeypatch, events, ["1.4.0", "1.4.0", "1.5.0"])
    assert result == (True, None) and "restarting" not in phases
    # The step shows the limits of the polling, not those of the device events
    view = mgr.install_step_view(A)
    assert view["step"] == "install" and view["usual"] == manager_installs.STEP_USUAL_POLLING_S
    assert view["give_up"] == manager_installs.INSTALL_WAIT_S


async def test_no_websocket_falls_back_to_http(mgr, ota_install, monkeypatch):
    """conftest: no device WebSocket -> the update command goes over HTTP; Gen1 always HTTP."""
    for gen in (2, 1):
        dev = install_device(mgr, gen=gen)
        monkeypatch.setattr(manager_installs, "INSTALL_NO_DOWNLOAD_S", 0.1)
        ok, diagnosis = await mgr._install(A, dev, "s")
        assert not ok and "never requested" in diagnosis
    assert ota_install["http"] == 2


async def test_download_diagnosis_per_device(mgr):
    for key, ip in ((A, "192.168.1.10"), (B, "192.168.1.11")):
        mgr.devices[key] = {"status": manager_core.STATUS_READY, "file": "x.zip", "origin": f"http://{ip}"}
    assert mgr.may_download("x.zip", "192.168.1.10")
    mgr.record_download("x.zip", "192.168.1.10")
    assert mgr._downloaded(mgr.devices[A]) and not mgr._downloaded(mgr.devices[B])
    mgr._reset_download(B, mgr.devices[B])  # B starts: A's record stays
    assert mgr._downloaded(mgr.devices[A])
    mgr.record_download("x.zip", "172.30.32.1")  # NAT: cannot be attributed
    assert mgr._downloaded(mgr.devices[B])


async def test_only_get_counts_as_served(mgr, monkeypatch):
    content = b"x" * 2048
    name = hashlib.sha256(content).hexdigest() + ".zip"
    data_paths.FW_DIR.mkdir(parents=True, exist_ok=True)
    (data_paths.FW_DIR / name).write_bytes(content)
    mgr.devices[A] = {"status": manager_core.STATUS_READY, "file": name, "origin": "http://127.0.0.1"}
    async with TestClient(TestServer(download_server.build_download_app(mgr))) as client:
        assert (await client.head(f"/{name}")).status == 200
        assert not mgr._downloaded(mgr.devices[A])
        r = await client.get(f"/{name}")
        assert r.status == 200 and await r.read() == content
    assert mgr.downloads[name]["served"] == {"127.0.0.1": 1}


# ---------------------------------------------------------------- No check after installations


async def test_no_check_follows_an_installation(mgr, net, updating):
    """A confirmed installation (no intermediate version) is not followed by a check:
    no server request, no check log entry; the next version comes at the next check."""
    await mgr.scan()
    mgr.last_scan -= 5
    before, checks, calls = mgr.last_scan, len(mgr.events.entries), net.offer_calls
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    dev = mgr.devices[A]
    assert dev["version"] == "1.5.0" and dev["status"] == manager_core.STATUS_CURRENT and dev["updated"] is True
    assert mgr.last_scan == before and net.offer_calls == calls and not mgr.checking
    assert [e["kind"] for e in mgr.events.entries[checks:]] == ["install", "install"]
    net.offer_version = "1.6.0"
    await mgr.check_device(A)
    assert mgr.devices[A]["pending_version"] == "1.6.0"


async def test_intermediate_version_is_followed_by_a_check_of_the_device(mgr, net, updating):
    """After a confirmed intermediate version the device is checked at once (log
    trigger "step") and the next step is provided – not installed."""
    net.steps = {"1.4.0": "1.5.0"}
    net.offer_version = "1.7.0"
    await mgr.scan()
    assert mgr.devices[A]["step_target"] == "1.7.0"
    checks = len(mgr.events.entries)
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    dev = mgr.devices[A]
    assert dev["version"] == "1.5.0" and dev["status"] == manager_core.STATUS_READY
    assert dev["pending_version"] == "1.7.0" and dev.get("step_target") is None and "updated" not in dev
    kinds = [(e["kind"], e.get("trigger"), e.get("user")) for e in mgr.events.entries[checks:]]
    assert kinds[-1] == ("check", "step", "alice") and [k[0] for k in kinds].count("install") == 2
    assert not mgr._step_checks and not mgr.checking


async def test_no_check_after_an_intermediate_version_once_a_sign_in_was_refused(mgr, net, updating, monkeypatch):
    """The device refused or blocked a sign-in after the update command: the check after the
    intermediate version would read it with signed requests, so it is skipped. The installation
    stays confirmed and the next step comes with the next check of the device."""
    net.steps = {"1.4.0": "1.5.0"}
    net.offer_version = "1.7.0"
    await mgr.scan()
    trigger = shelly.trigger_update
    checked = []

    async def refused(*args, **kwargs):
        await trigger(*args, **kwargs)
        mgr._update_step(A, signin_refused=True)  # as a refused read after the command does

    async def check_device(*args, **kwargs):
        checked.append(args)

    monkeypatch.setattr(shelly, "trigger_update", refused)
    monkeypatch.setattr(mgr, "check_device", check_device)
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    dev = mgr.devices[A]
    assert dev["version"] == "1.5.0" and dev["status"] == manager_core.STATUS_CURRENT and dev["updated"] is True
    assert not checked and not mgr._step_checks


async def test_stored_version_offered_on_server_error_can_be_installed(mgr, net, updating):
    """The manual option on a server error: installable with the risk acknowledgment
    only (web UI), logged as unverified; the marker ends with the installation."""
    store(mgr, "Plus1", "1.6.0")
    net.offer_error = shelly.ShellyError("408, 'Request Timeout'")
    await mgr.scan()
    dev = mgr.devices[A]
    assert dev["store_fallback"] is True and dev["status"] == manager_core.STATUS_ERROR
    assert await mgr.install(A, dev["sha256"], "alice") is True
    assert dev["version"] == "1.6.0" and "store_fallback" not in dev and dev["updated"] is True
    started = [e for e in mgr.events.entries if e["kind"] == "install"][-2]
    assert started["unverified"] is True
# ---------------------------------------------------------------- "Updated" mark and texts


async def test_updated_mark_stays_until_the_next_user_check(mgr, net, updating):
    """A confirmed installation marks the device as just updated: a single check of
    the device or a full check ends it."""
    await mgr.scan()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    assert await mgr.install(C, mgr.devices[C]["sha256"], "alice") is True
    state = {d["id"]: d for d in mgr.public_state()["devices"]}
    assert state[A]["updated"] is True and state[A]["status"] == manager_core.STATUS_CURRENT and not state[B]["updated"]
    await mgr.check_device(A)
    assert "updated" not in mgr.devices[A] and mgr.devices[C]["updated"] is True
    await mgr.scan()
    assert "updated" not in mgr.devices[C]


async def test_unconfirmed_installation_keeps_the_mark(mgr, net, updating):
    """An unconfirmed installation stays in "Updates available" (status unconfirmed)
    until the next check of the device; a refused one (nothing provided) does not."""
    await mgr.scan()
    updating["accept"] = False  # the Shelly keeps its version
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is False
    assert mgr.devices[A]["status"] == manager_core.STATUS_UNCLEAR and mgr.devices[A]["updated"] is True
    assert await mgr.install(B, "0" * 64, "alice") is False  # nothing provided for B
    assert "updated" not in mgr.devices[B]
    await mgr.check_device(A)
    assert "updated" not in mgr.devices[A] and mgr.devices[A]["status"] == manager_core.STATUS_READY


async def test_no_updated_mark_after_downgrade(mgr, net, updating):
    await mgr.scan()
    _, older = store(mgr, "Plus1", "1.3.0")
    assert await mgr.install(B, older, "alice", downgrade=True) is True
    assert "updated" not in mgr.devices[B] and mgr.devices[B]["version"] == "1.3.0"


def test_diagnosis_after_a_download_recommends_websocket_debugging(mgr):
    """The hint for a Shelly that fetched the file but kept its version."""
    dev = {"file": "x.zip", "version": "2.0.3", "origin": "http://192.168.1.10"}
    mgr.downloads["x.zip"] = {"served": {"192.168.1.10": 1}, "rejected": set()}
    text = mgr._install_diagnosis(dev)
    assert text.endswith("check the device log or the Shelly's web interface and try again with enabled websocket debugging")
    assert "still reports 2.0.3" in text


# ---------------------------------------------------------------- Failed installations and retry


async def test_failed_installation_stays_provided_for_a_retry(mgr, net, updating, monkeypatch):
    """A failed installation keeps its file: error status with the marker
    install_failed (UI: "Updates available" with checkbox and Install), approval,
    store lock and Firmware tab as for a ready device; a retry may succeed; the next
    check of the device ends the marker."""
    await mgr.scan()
    real_trigger = shelly.trigger_update

    async def refuse(*_args):
        raise shelly.ShellyError("Update refused")

    monkeypatch.setattr(shelly, "trigger_update", refuse)
    dev, sha = mgr.devices[A], mgr.devices[A]["sha256"]
    assert await mgr.install(A, sha, "alice") is False
    assert dev["status"] == manager_core.STATUS_ERROR and dev["install_failed"] is True
    assert {d["id"]: d for d in mgr.public_state()["devices"]}[A]["install_failed"] is True
    assert mgr.approval_matches(A, sha) and dev["file"] in mgr._in_use()
    assert {"id": A, "name": dev["name"]} in next(f for f in mgr.firmware_list() if f["name"] == dev["file"])["devices"]
    assert not mgr.may_download(dev["file"], "192.168.1.10")  # served only while installing
    monkeypatch.setattr(shelly, "trigger_update", real_trigger)
    assert await mgr.install(A, sha, "alice") is True  # retry
    assert dev["status"] == manager_core.STATUS_CURRENT and "install_failed" not in dev
    monkeypatch.setattr(shelly, "trigger_update", refuse)
    assert await mgr.install(C, mgr.devices[C]["sha256"], "alice") is False
    assert mgr.devices[C]["install_failed"] is True
    await mgr.check_device(C)  # the next check of the device ends the marker
    assert "install_failed" not in mgr.devices[C] and mgr.devices[C]["status"] == manager_core.STATUS_READY


async def test_failed_downgrade_is_not_retryable(mgr, net, updating, monkeypatch):
    await mgr.scan()
    _, older = store(mgr, "Plus1", "1.3.0")

    async def refuse(*_args):
        raise shelly.ShellyError("Update refused")

    monkeypatch.setattr(shelly, "trigger_update", refuse)
    assert await mgr.install(B, older, "alice", downgrade=True) is False
    assert mgr.devices[B]["status"] == manager_core.STATUS_ERROR and "install_failed" not in mgr.devices[B]
    assert not mgr.approval_matches(B, older)


def test_ota_error_text_depends_on_the_download(mgr):
    """ota_error after the device fetched its file: the device rejected the firmware
    (e.g. too old for a direct update); without a download: reachability hint."""
    dev = {"file": "x.zip", "origin": "http://192.168.1.10", "pending_version": "1.7.6"}
    text = mgr._ota_error_text(dev, "error")
    assert "reachable from the Shelly" in text and "rejected it" not in text
    mgr.downloads["x.zip"] = {"served": {"192.168.1.10": 1}, "rejected": set()}
    text = mgr._ota_error_text(dev, "error")
    assert "downloaded the firmware but rejected it" in text and "intermediate version" in text
    assert "1.7.6" in text and "reachable" not in text


# ---------------------------------------------------------------- Download server: rejected requests


def test_rejected_requests_are_logged_once_per_client_and_window(caplog):
    caplog.set_level("WARNING", logger="sfm.download")
    rejects, t = download_server.RejectLog(), 1_000_000.0
    for i in range(5):
        rejects.rejected("a.zip", "192.168.1.50", now=t + i)
    rejects.rejected("a.zip", "192.168.1.51", now=t + 10)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["Download rejected: a.zip from 192.168.1.50", "Download rejected: a.zip from 192.168.1.51"]
    caplog.clear()
    rejects.rejected("a.zip", "192.168.1.50", now=t + download_server.REJECT_WINDOW_S + 1)
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0].startswith("4 more rejected download request(s) from 192.168.1.50 since ")
    assert lines[1] == "Download rejected: a.zip from 192.168.1.50" and len(lines) == 2


def test_rejected_clients_are_limited(caplog):
    caplog.set_level("WARNING", logger="sfm.download")
    rejects, t = download_server.RejectLog(), 1_000_000.0
    for i in range(download_server.REJECT_MAX_CLIENTS + 10):
        rejects.rejected("a.zip", f"10.0.{i // 250}.{i % 250}", now=t)
    assert len(rejects.clients) == download_server.REJECT_MAX_CLIENTS + 1  # + "other clients"
    assert rejects.clients[download_server.OTHER_CLIENTS][1] == 9
    assert len(caplog.records) == download_server.REJECT_MAX_CLIENTS + 1


async def test_download_server_limits_rejection_warnings(mgr, caplog):
    caplog.set_level("WARNING", logger="sfm.download")
    name = "a" * 64 + ".zip"
    async with TestClient(TestServer(download_server.build_download_app(mgr))) as client:
        for _ in range(20):
            assert (await client.get(f"/{name}")).status == 404
    assert len([r for r in caplog.records if r.name == "sfm.download"]) == 1


async def test_unexpected_error_ends_the_installation_as_failed(mgr, net, monkeypatch):
    """An error no code path expects must not leave the device "installing": it is
    shown and logged as a failed installation that can be retried."""
    await mgr.scan()
    sha = mgr.devices[A]["sha256"]

    async def broken(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(mgr, "_install", broken)
    assert await mgr.install(A, sha, "alice") is False
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_ERROR and dev["install_failed"] is True
    assert "Unexpected error (RuntimeError: boom)" in dev["message"]
    assert A not in mgr.installing and mgr.approval_matches(A, sha)
    failed = [e for e in mgr.events.entries if e["kind"] == "install"][-1]
    assert failed["event"] == "failed" and failed["ok"] is False and failed["user"] == "alice"


async def test_restart_first_waits_until_the_device_answers(run_install, monkeypatch):
    """"Restart the device first": restart command, then the device must answer again
    before the pre-check and the update command."""
    monkeypatch.setattr(manager_installs, "RESTART_SETTLE_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_POLL_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_DOWN_POLL_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_DOWN_S", 0.05)  # this fake device never goes down
    order = []

    async def restart(*_args):
        order.append("restart")

    monkeypatch.setattr(shelly, "restart", restart)
    original = manager_installs.Installs._install

    async def install(self, device_id, dev, sha, restart_first=False):
        return await original(self, device_id, dev, sha, True)

    monkeypatch.setattr(manager_installs.Installs, "_install", install)
    ok, _diagnosis, _dev, _polls = await run_install("1.4.0", "1.5.0", ["1.4.0", "1.5.0"], served=True)
    assert ok and order == ["restart"]


RESTART_DEV = {"name": "Kitchen", "gen": 2, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
               "version": "1.4.0", "pending_version": "1.5.0", "pending_build": "b-old"}


def restart_fakes(monkeypatch, answers):
    """Fake device for "Restart the device first": answers[i] is the i-th /shelly
    answer (an exception is raised, a dict returned; the last one repeats). Returns
    the list of calls ("restart", "info", "trigger") in order."""
    monkeypatch.setattr(manager_installs, "RESTART_SETTLE_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_POLL_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_DOWN_POLL_S", 0)
    calls = []

    async def info(*_args, **_kwargs):
        answer = answers[min(calls.count("info"), len(answers) - 1)]
        calls.append("info")
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def restart(*_args):
        calls.append("restart")

    async def trigger(*_args):
        calls.append("trigger")

    monkeypatch.setattr(shelly_cloud, "file_sha256", lambda *_a: "s")
    monkeypatch.setattr(shelly, "restart", restart)
    monkeypatch.setattr(shelly, "device_info", info)
    monkeypatch.setattr(shelly, "trigger_update", trigger)
    return calls


async def test_restart_first_checks_the_device_before_the_restart(mgr, monkeypatch):
    """Another device at the address (MAC) or another build: nothing is restarted."""
    calls = restart_fakes(monkeypatch, [{"mac": "BB", "build": "b-old"}])
    with pytest.raises(shelly.ShellyError, match="changed since the check"):
        await mgr._install(A, dict(RESTART_DEV), "s", restart_first=True)
    assert calls == ["info"]


async def test_restart_first_waits_for_a_slow_shutdown(mgr, monkeypatch):
    """A device that still answers after the restart command is not taken as back:
    the installation starts only after it stopped answering and answers again."""
    me = {"mac": "AA", "build": "b-old"}
    calls = restart_fakes(monkeypatch, [me, me, me, TimeoutError(), me])
    monkeypatch.setattr(mgr, "_await_target_version", lambda *_a: asyncio.sleep(0, (True, None)))
    monkeypatch.setattr(mgr, "_update_over_websocket", lambda *_a: asyncio.sleep(0, None))
    await mgr._install(A, dict(RESTART_DEV), "s", restart_first=True)
    # check, restart, two answers while going down, no answer, back, check again, update
    assert calls == ["info", "restart", "info", "info", "info", "info", "info", "trigger"]


async def test_restart_first_without_answer_installs_nothing(mgr, monkeypatch):
    """No answer within the limit after the restart: error, no update command."""
    monkeypatch.setattr(manager_installs, "RESTART_WAIT_S", 0.05)
    calls = restart_fakes(monkeypatch, [{"mac": "AA", "build": "b-old"}, TimeoutError()])
    with pytest.raises(shelly.ShellyError, match="did not answer within 2 minutes"):
        await mgr._install(A, dict(RESTART_DEV), "s", restart_first=True)
    assert "restart" in calls and "trigger" not in calls


async def test_restart_from_the_row_shows_restarting(mgr, monkeypatch):
    """Restart of the device row: "restarting" until the device answers again, then the
    status from before; no answer within RESTART_WAIT_S: the device shows the error."""
    calls = restart_fakes(monkeypatch, [shelly.ShellyError("down"), {"version": "1.4.0"}])
    mgr.devices[A] = dict(RESTART_DEV, status="ready", message="Provided, not installed")
    await mgr.restart_watched(A)
    assert calls == ["restart"] and mgr.activity[A] == "restarting"
    await mgr.watch_restart(A)
    assert A not in mgr.activity and mgr.devices[A]["status"] == "ready"
    calls = restart_fakes(monkeypatch, [shelly.ShellyError("down")])
    monkeypatch.setattr(manager_installs, "RESTART_WAIT_S", 0.05)
    await mgr.restart_watched(A)
    await mgr.watch_restart(A)
    dev = mgr.devices[A]
    assert A not in mgr.activity and dev["status"] == "error" and dev["unreachable"] is True
    assert "after the restart" in dev["message"]


# ---- Weak Wi-Fi: tolerance before the update command, restart or lost connection, steps


@pytest.fixture
def weak_wifi(mgr, monkeypatch, quick_install):
    """Installs over HTTP (no WebSocket) with short pauses before the update command;
    answers: one item per pre-check (an exception is raised), then the version 1.5.0.
    link: the answer of link_status (running time, Wi-Fi signal)."""
    monkeypatch.setattr(manager_installs, "PRECHECK_PAUSE_S", 0.01)
    monkeypatch.setattr(shelly_cloud, "file_sha256", lambda *_a: "s")
    state = {"answers": [], "link": {"uptime": 3600, "rssi": -84}, "sent": 0, "trigger": [], "steps": []}

    async def device_info(*_args, **_kwargs):
        state["steps"].append(mgr.install_step_view(A))
        answer = state["answers"].pop(0) if state["answers"] else {"version": "1.5.0"}
        if isinstance(answer, BaseException):
            raise answer
        return {"mac": "AA", "build": "b-old", "version": "1.4.0", **answer}

    async def link(*_args, **_kwargs):
        if isinstance(state["link"], BaseException):
            raise state["link"]
        return state["link"]

    async def trigger(*_args):
        state["sent"] += 1
        if state["trigger"]:
            raise state["trigger"].pop(0)

    class NoChannel:
        """The device WebSocket is not usable: the command goes over HTTP."""

        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            raise OSError("refused")

    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly, "link_status", link)
    monkeypatch.setattr(shelly, "trigger_update", trigger)
    monkeypatch.setattr(shelly, "OtaChannel", NoChannel)
    return state


async def run_weak(mgr, monkeypatch):
    """Install the ready device A through the whole path (log entries, steps)."""
    dev = install_device(mgr)
    dev["sha256"] = "s"
    monkeypatch.setattr(mgr, "approval_matches", lambda *_a: True)
    ok = await mgr.install(A, "s", "u")
    return ok, dev, mgr.events.entries[-1]


async def test_lost_connection_before_the_command_is_tried_again(mgr, monkeypatch, weak_wifi):
    """No answer twice, then an answer with a running time longer than the gap: one lost
    connection – the command is sent once, the result is green with a hint line."""
    weak_wifi["answers"] = [TimeoutError(), OSError("unreachable"), {}]
    ok, dev, entry = await run_weak(mgr, monkeypatch)
    assert ok and weak_wifi["sent"] == 1 and entry["event"] == "confirmed"
    assert entry["detail"] == "Connection to the Shelly lost once while waiting – tried again (weak Wi-Fi: -84 dBm)"
    precheck = [s for s in weak_wifi["steps"] if s and s["step"] == "precheck"]
    assert [s["attempt"] for s in precheck] == [1, 2, 3] and precheck[1]["late"]
    assert any(s and s["step"] == "install" and s["rssi"] == -84 for s in weak_wifi["steps"]) and precheck[0]["n"] == 3 and precheck[0]["of"] == 6
    assert mgr.install_step_view(A) is None and dev["status"] == manager_core.STATUS_CURRENT


async def test_restart_before_the_command_installs_nothing(mgr, monkeypatch, weak_wifi):
    """An answer after the gap with a running time shorter than the gap: the Shelly
    restarted on its own – nothing is sent, the installation fails with the time."""
    weak_wifi["answers"] = [TimeoutError(), {}]

    async def link(*_args, **_kwargs):
        return {"uptime": 0 if weak_wifi["answers"] == [] else 3600, "rssi": None}

    monkeypatch.setattr(shelly, "link_status", link)
    ok, dev, entry = await run_weak(mgr, monkeypatch)
    assert not ok and weak_wifi["sent"] == 0 and entry["event"] == "failed"
    assert entry["detail"] == ("The Shelly restarted unexpectedly before the update command (running for 0 s). "
                               "Nothing was installed.")
    assert dev["status"] == manager_core.STATUS_ERROR


async def test_no_answer_in_all_attempts_installs_nothing(mgr, monkeypatch, weak_wifi):
    """Five unanswered pre-checks: error with the attempts, nothing sent; the Wi-Fi
    signal is named when it was read and weak."""
    weak_wifi["answers"] = [TimeoutError()] * 5
    ok, _dev, entry = await run_weak(mgr, monkeypatch)
    assert not ok and weak_wifi["sent"] == 0
    assert entry["detail"].startswith("Connection to the Shelly lost before the update command – no answer in 5 attempts")
    assert entry["detail"].endswith("Nothing was installed.")
    # Signal read at the first answer, then the connection is lost while the command is refused
    weak_wifi["answers"] = [{}] + [TimeoutError()] * 4
    weak_wifi["trigger"] = [aiohttp_connector_error()]
    ok, _dev, entry = await run_weak(mgr, monkeypatch)
    assert not ok and entry["detail"].endswith("Nothing was installed. Wi-Fi signal: -84 dBm (weak).")


async def test_window_limits_the_attempts(mgr, monkeypatch, weak_wifi):
    """The attempts end with the window even before the fifth one."""
    monkeypatch.setattr(manager_installs, "PRECHECK_WINDOW_S", 0.005)
    weak_wifi["answers"] = [TimeoutError()] * 5
    ok, _dev, entry = await run_weak(mgr, monkeypatch)
    assert not ok and "no answer in 1 attempts" in entry["detail"]


async def test_a_refusal_is_not_tried_again(mgr, monkeypatch, weak_wifi):
    """The device answers with a refusal (other firmware): no further attempt."""
    weak_wifi["answers"] = [{"build": "b-new"}, {}]
    ok, _dev, entry = await run_weak(mgr, monkeypatch)
    assert not ok and weak_wifi["sent"] == 0 and "changed since the check" in entry["detail"]
    assert weak_wifi["answers"] == [{}]


@pytest.mark.parametrize("refusal", [
    shelly.AuthError("The Shelly rejected the password"),
    shelly.AuthBlockedError("The Shelly temporarily blocks sign-ins (too many attempts) – wait a few minutes and try again"),
])
async def test_refused_sign_in_of_the_link_read_installs_nothing(mgr, monkeypatch, weak_wifi, refusal):
    """Reading running time and Wi-Fi signal is optional, but a refused or blocked sign-in
    stops the installation before the update command: the command would be refused the
    same way, and one more signed request is one more failed attempt on the device."""
    weak_wifi["link"] = refusal
    ok, dev, entry = await run_weak(mgr, monkeypatch)
    assert not ok and weak_wifi["sent"] == 0 and entry["event"] == "failed" and entry["detail"] == str(refusal)
    assert dev["status"] == manager_core.STATUS_ERROR


def aiohttp_connector_error():
    """A connection that was never made (nothing was sent)."""
    import aiohttp
    from aiohttp.client_reqrep import ConnectionKey

    key = ConnectionKey("192.168.1.10", 80, False, None, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(113, "No route to host"))


async def test_command_is_resent_only_if_it_never_left(mgr, monkeypatch, weak_wifi):
    """HTTP: no connection = the command did not leave, it is sent again after a new
    pre-check; no answer to a sent command = never sent twice, the installation waits."""
    weak_wifi["answers"] = [{}, {}]
    weak_wifi["trigger"] = [aiohttp_connector_error()]
    ok, *_ = await run_weak(mgr, monkeypatch)
    assert ok and weak_wifi["sent"] == 2
    weak_wifi.update(answers=[{}], sent=0, trigger=[TimeoutError()])
    ok, *_ = await run_weak(mgr, monkeypatch)
    assert ok and weak_wifi["sent"] == 1


async def test_weak_signal_doubles_the_stall_limit(mgr):
    """60 s without progress, 120 s with a weak Wi-Fi signal at the start (-80 dBm and lower)."""
    for rssi, limit in ((None, 60), (-79, 60), (-80, 120), (-90, 120)):
        mgr.install_steps[A] = {"rssi": rssi}
        assert mgr._stall_limit(A) == limit


def test_step_view_turns_late(mgr):
    """The pill turns yellow once a step takes longer than usual, or from the second attempt.
    The second pass of a two-step update stays with the following steps."""
    install_device(mgr)
    mgr._set_step(A, "verify", give_up=90)
    view = mgr.install_step_view(A)
    assert (view["step"], view["n"], view["usual"], view["give_up"], view["late"]) == ("verify", 6, 30, 90, False)
    assert view["second_pass"] is False
    mgr._update_step(A, second_pass=True)
    mgr._set_step(A, "reboot", give_up=90)
    assert mgr.install_step_view(A)["second_pass"] is True
    mgr._set_step(A, "verify", give_up=90)
    mgr.install_steps[A]["quiet"] -= 31
    assert mgr.install_step_view(A)["late"] and mgr.install_step_view(A)["seconds"] == 31
    mgr._set_step(A, "precheck", give_up=60)
    mgr._update_step(A, attempt=2)
    assert mgr.install_step_view(A)["late"] and mgr.install_step_view(A)["attempts"] == 5
    shown = next(d for d in mgr.public_state()["devices"] if d["id"] == A)
    assert shown["install_step"]["step"] == "precheck"


async def test_link_status_reads_uptime_and_signal(monkeypatch):
    """Gen2+ Shelly.GetStatus (sys.uptime, wifi.rssi while connected), Gen1 /status."""
    from conftest import REAL_LINK_STATUS

    async def rpc(_session, _origin, method, *_a, **_k):
        assert method == "Shelly.GetStatus"
        return {"sys": {"uptime": 273600}, "wifi": {"status": "got ip", "rssi": -84}}

    async def get(_session, _origin, path, *_a, **_k):
        assert path == "/status"
        return {"uptime": 14, "wifi_sta": {"connected": True, "rssi": -61}}

    monkeypatch.setattr(shelly, "_rpc", rpc)
    monkeypatch.setattr(shelly, "_device_json", get)
    assert await REAL_LINK_STATUS(None, 2, "http://h") == {"uptime": 273600, "rssi": -84}
    assert await REAL_LINK_STATUS(None, 1, "http://h") == {"uptime": 14, "rssi": -61}

    async def odd(*_a, **_k):
        return {"sys": {"uptime": True}, "wifi": {"status": "disconnected", "rssi": -50}}

    monkeypatch.setattr(shelly, "_rpc", odd)
    assert await REAL_LINK_STATUS(None, 2, "http://h") == {"uptime": None, "rssi": None}


async def test_restart_is_measured_from_the_last_answer(mgr, monkeypatch, weak_wifi):
    """The unanswered attempt itself takes time (time-outs): a running time shorter than
    the time since the last answer is a restart, even if it is longer than the pause."""
    clock = [1000.0]
    # Only the clock of the installation (the event loop keeps the real one)
    monkeypatch.setattr(manager_installs, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))

    async def device_info(*_args, **_kwargs):
        if not weak_wifi["answers"]:
            clock[0] += 5  # the second attempt answers 20 s after the start
            return {"mac": "AA", "build": "b-old", "version": "1.4.0"}
        weak_wifi["answers"].pop()
        clock[0] += 15  # the first attempt times out after 15 s
        raise TimeoutError

    async def link(*_args, **_kwargs):
        return {"uptime": 16, "rssi": None}  # restarted 16 s ago: after the start, before the answer

    weak_wifi["answers"] = [object()]
    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly, "link_status", link)
    dev = install_device(mgr)
    with pytest.raises(shelly.ShellyError, match="restarted unexpectedly"):
        await mgr._install(A, dev, "s")
    assert weak_wifi["sent"] == 0


async def test_restart_and_install_accepts_its_own_late_restart(mgr, monkeypatch, weak_wifi):
    """Restart & install whose restart was not seen: a restart found by the running time
    is the one the app asked for – once; the installation goes on."""
    weak_wifi["answers"] = [TimeoutError(), {}]
    mgr._set_step(A, "restart")
    mgr._update_step(A, restart_unseen=True)

    async def link(*_args, **_kwargs):
        return {"uptime": 0, "rssi": None}

    monkeypatch.setattr(shelly, "link_status", link)
    dev = install_device(mgr)
    assert await mgr._command_update(A, dev, "http://h/x.zip", None, {"dev": dev}) is None
    assert weak_wifi["sent"] == 1 and mgr.install_steps[A]["losses"] == 0
    assert mgr.install_steps[A]["restart_unseen"] is False


async def test_gen1_tolerates_a_weak_wifi_too(mgr, monkeypatch, weak_wifi):
    """Gen1 (HTTP only): the same attempts before the command."""
    weak_wifi["answers"] = [OSError("unreachable"), {}]
    dev = install_device(mgr, gen=1)
    dev["sha256"] = "s"
    monkeypatch.setattr(mgr, "approval_matches", lambda *_a: True)
    assert await mgr.install(A, "s", "u") and weak_wifi["sent"] == 1
    assert mgr.events.entries[-1]["detail"].startswith("Connection to the Shelly lost once")


async def test_step_is_cleared_after_a_failure(mgr, monkeypatch, weak_wifi):
    """No step stays behind: after a failed and after an unexpected error."""
    weak_wifi["answers"] = [{"build": "b-new"}]
    ok, *_ = await run_weak(mgr, monkeypatch)
    assert not ok and mgr.install_step_view(A) is None and A not in mgr.install_steps
    weak_wifi["answers"] = [RuntimeError("boom")]
    ok, *_ = await run_weak(mgr, monkeypatch)
    assert not ok and A not in mgr.install_steps and A not in mgr.installing


async def test_websocket_command_without_answer_is_never_sent_again(mgr, monkeypatch, weak_wifi):
    """Gen2+ over the device WebSocket: no answer to Shelly.Update = it may run; never a
    second command (neither WebSocket nor HTTP), the version is polled."""
    updates = []

    class SilentChannel:
        """Opens, takes the command, never answers, then closes (no OTA events)."""

        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def update(self, url):
            updates.append(url)
            return None

        async def events(self):
            return
            yield

    monkeypatch.setattr(shelly, "OtaChannel", SilentChannel)
    weak_wifi["answers"] = [{}]
    ok, *_ = await run_weak(mgr, monkeypatch)
    assert ok and len(updates) == 1 and weak_wifi["sent"] == 0
