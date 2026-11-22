"""Installation: target version verification, Gen2+ OTA WebSocket, HTTP fallback,
download diagnosis per device, check after confirmed installations.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import hashlib
import json
import time

import data_paths
import download_server
import manager_core
import manager_installs
import pytest
import shelly
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from conftest import A, B, C
from helpers import store


def test_install_timing_constants():
    """Polling (devices without OTA events): first look after 30 s, every 10 s, early
    abort after 2 min without download, at most 5 min. OTA events: 60 s without
    progress, 30 s for the restart, 30 s after it, polled every 5 s."""
    assert (manager_installs.INSTALL_FIRST_CHECK_S, manager_installs.INSTALL_POLL_S) == (30, 10)
    assert (manager_installs.INSTALL_NO_DOWNLOAD_S, manager_installs.INSTALL_WAIT_S) == (120, 300)
    assert (manager_installs.OTA_STALL_S, manager_installs.OTA_RESTART_WAIT_S) == (60, 30)
    assert (manager_installs.OTA_BOOT_WAIT_S, manager_installs.OTA_POLL_S) == (30, 5)


@pytest.fixture
def run_install(mgr, monkeypatch, quick_install):
    """Run Manager._install against a device answering with the given versions."""
    monkeypatch.setattr(manager_installs, "INSTALL_NO_DOWNLOAD_S", 0.08)  # clearly before the upper limit

    async def run(old, target, answers, gen=2, served=False):
        dev = {"name": "Kitchen", "gen": gen, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
               "version": old, "pending_version": target, "pending_build": "b-old"}
        mgr.devices[A] = dev
        calls = []

        async def device_info(*_args, **_kwargs):
            if not calls:  # pre-check before the update command
                calls.append("pre")
                return {"mac": "AA", "build": "b-old", "version": old}
            calls.append("poll")
            if served:
                mgr.downloads["x.zip"] = {"served": {"192.168.1.10": 1}, "rejected": set()}
            version = answers[min(len(calls) - 2, len(answers) - 1)]
            return {"mac": "AA", "build": "b-old", "version": version}

        async def trigger(*_args):
            return None

        monkeypatch.setattr(shelly, "file_sha256", lambda *_a: "s")
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

    def __init__(self, password=None, events=(), redirect=False, keep_open=False):
        """keep_open: after the events the WebSocket stays open until release is set
        (a device still running; closing it stands for the restart)."""
        self.password, self.events, self.redirect = password, list(events), redirect
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
           "version": "1.4.0", "pending_version": "1.5.0", "pending_build": "b-old", "status": manager_core.STATUS_READY}
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
    monkeypatch.setattr(shelly, "file_sha256", lambda *_a: "s")
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


async def ota_run(mgr, monkeypatch, events, versions, keep_open=False):
    """Install over FakeShelly; after the pre-check device_info answers from versions
    (None: no answer, the last entry repeats). Returns (result or exception, device, phases seen)."""
    async with FakeShelly(events=events, keep_open=keep_open) as fake:
        monkeypatch.setattr(shelly, "OtaChannel", lambda _origin, *a: REAL_CHANNEL(fake.origin, *a))
        dev = install_device(mgr)
        answers, phases = ["1.4.0", *versions], []  # first: the pre-check of the installation

        async def device_info(*_args, **_kwargs):
            phases.append(dev.get("ota_phase"))
            version = answers.pop(0) if len(answers) > 1 else answers[0]
            if version is None:
                raise shelly.ShellyError("not reachable")
            return {"mac": "AA", "build": "b-old", "version": version}

        monkeypatch.setattr(shelly, "device_info", device_info)
        try:
            result = await mgr._install(A, dev, "s")
        except shelly.ShellyError as err:
            result = err
        fake.release.set()
    return result, dev, phases


async def test_ota_success_restart_and_new_version(mgr, ota_install, monkeypatch):
    """100 % / ota_success, the WebSocket closes (restart): "Restarting", then the
    new version is confirmed; no polling limit of the fallback is involved."""
    result, dev, phases = await ota_run(mgr, monkeypatch, DONE, [None, "1.5.0"])
    assert result == (True, None) and dev["status"] == manager_core.STATUS_CURRENT
    assert "restarting" in phases and "ota_phase" not in dev and ota_install["http"] == 0


async def test_ota_old_version_after_restart_is_an_error(mgr, ota_install, monkeypatch):
    result, _dev, _phases = await ota_run(mgr, monkeypatch, DONE, ["1.4.0"])
    assert isinstance(result, shelly.ShellyError) and "still reports 1.4.0" in str(result)


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


async def test_ota_websocket_lost_before_applied_falls_back_to_polling(mgr, ota_install, monkeypatch):
    events = [{"event": "ota_progress", "msg": "", "progress_percent": 40}]
    result, _dev, phases = await ota_run(mgr, monkeypatch, events, ["1.4.0", "1.4.0", "1.5.0"])
    assert result == (True, None) and "restarting" not in phases


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
    mgr._reset_download(mgr.devices[B])  # B starts: A's record stays
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


@pytest.fixture
def updating(mgr, net, monkeypatch, quick_install):
    """End-to-end installs against FakeNet: the update command "installs" the version
    of the served store file on the fake device; short timings."""
    result = {"accept": True}

    async def trigger(_session, _gen, origin, url, _auth=None):
        if result["accept"]:
            device = net.by_origin(origin)
            version = mgr.firmware[url.rsplit("/", 1)[-1]]["version"]
            net.devices[device] = (*net.devices[device][:2], version, version)

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    return result


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


async def test_restart_first_without_answer_installs_nothing(mgr, monkeypatch):
    """No answer within the limit after the restart: error, no update command."""
    monkeypatch.setattr(manager_installs, "RESTART_SETTLE_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_POLL_S", 0)
    monkeypatch.setattr(manager_installs, "RESTART_WAIT_S", 0.05)
    dev = {"name": "Kitchen", "gen": 2, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
           "version": "1.4.0", "pending_version": "1.5.0", "pending_build": "b-old"}
    triggered = []

    async def gone(*_args, **_kwargs):
        raise TimeoutError()

    async def restart(*_args):
        return None

    async def trigger(*_args):
        triggered.append(1)

    monkeypatch.setattr(shelly, "file_sha256", lambda *_a: "s")
    monkeypatch.setattr(shelly, "restart", restart)
    monkeypatch.setattr(shelly, "device_info", gone)
    monkeypatch.setattr(shelly, "trigger_update", trigger)
    with pytest.raises(shelly.ShellyError, match="did not answer within 2 minutes"):
        await mgr._install(A, dev, "s", restart_first=True)
    assert not triggered
