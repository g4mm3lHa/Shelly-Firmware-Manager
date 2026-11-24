"""Diagnosis and orientation: the connection test of the download server (manager,
download server route, API), "Try again" of a device that did not answer, the text for a
bug ticket (masking), the card "Getting started" and the areas of Home Assistant
(column, schedules).

No connection is opened: the device requests are replaced per test."""

import json

import data_paths
import diagnostics
import download_server
import manager
import pytest
import shelly
import timetable
from aiohttp.test_utils import TestClient, TestServer
from conftest import OPTIONS, A, B, C
from helpers import make_manager
from shelly import ShellyError

URL = OPTIONS["download_base_url"].rstrip("/")


def fetching(mgr, answer_code=200, client=None, error=None, calls=None):
    """shelly.fetch_test of a device that fetches the test address from the download
    server (as client, default its own address) and reports answer_code; error: raised
    instead (the device could not make the request)."""

    async def fetch(_session, origin, url, _auth=None):
        if calls is not None:
            calls.append((origin, url))
        if error is not None:
            raise error
        token = url.rsplit("/", 1)[1]
        assert url.startswith(URL + "/connection-test/")
        mgr.test_fetched(token, client or origin.split("//")[1], True)
        return {"code": answer_code, "message": "OK"}

    return fetch


async def test_connection_test_reachable(mgr, monkeypatch):
    """The device fetches the test address once: reachable, stored, logged, in the state."""
    await mgr.scan()
    calls = []
    monkeypatch.setattr(shelly, "fetch_test", fetching(mgr, calls=calls))
    result = await mgr.run_connection_test(A, "alice")
    assert result["ok"] is True and result["device"] == "Kitchen" and result["ip"] == "192.168.1.10"
    assert result["url"] == URL and result["error"] is None and len(calls) == 1
    assert mgr.public_state()["connection_test"]["ok"] is True
    entry = mgr.events.entries[-1]
    assert (entry["kind"], entry["ok"], entry["user"]) == ("connection", True, "alice")
    assert mgr._test is None  # the test address is gone again


async def test_connection_test_failures(mgr, monkeypatch):
    """No connection (the device's -104), another server answering, a request from
    another address: not reachable, with the reason."""
    await mgr.scan()
    monkeypatch.setattr(shelly, "fetch_test", fetching(mgr, error=ShellyError("Device reports: -104 Deadline Exceeded")))
    result = await mgr.run_connection_test(A)
    assert result["ok"] is False and "timeout after" in result["error"]

    async def other_server(_session, _origin, _url, _auth=None):
        return {"code": 404, "message": "Not Found"}

    monkeypatch.setattr(shelly, "fetch_test", other_server)
    assert "Another web server answered" in (await mgr.run_connection_test(A))["error"]
    monkeypatch.setattr(shelly, "fetch_test", fetching(mgr, client="192.168.1.250"))
    result = await mgr.run_connection_test(A)
    assert result["ok"] is False and "from 192.168.1.250" in result["error"]
    assert mgr.events.entries[-1]["severity"] == "error"


async def test_connection_test_tries_the_next_device(mgr, monkeypatch):
    """Without a chosen device: a device that does not answer is skipped (at most three)."""
    await mgr.scan()
    tried = []

    async def fetch(_session, origin, url, _auth=None):
        tried.append(origin)
        if len(tried) == 1:
            raise TimeoutError()
        mgr.test_fetched(url.rsplit("/", 1)[1], origin.split("//")[1], True)
        return {"code": 200, "message": "OK"}

    monkeypatch.setattr(shelly, "fetch_test", fetch)
    result = await mgr.run_connection_test(None)
    assert result["ok"] is True and len(tried) == 2


async def test_connection_test_refusals(net, mgr, monkeypatch):
    """Not without a usable download base URL, a known Gen2+ device, nor twice at once."""
    with pytest.raises(ShellyError, match="run a check first"):
        await mgr.run_connection_test(None)
    await mgr.scan()
    with pytest.raises(ShellyError, match="cannot run the test"):
        await mgr.run_connection_test("f" * 32)
    mgr._test = {"token": "0" * 32, "ip": "x", "fetched": False, "from": set()}
    with pytest.raises(ShellyError, match="already running"):
        await mgr.run_connection_test(A)
    mgr._test = None
    unset = make_manager(net, download_base_url="")
    with pytest.raises(ShellyError, match="Installations are disabled"):
        await unset.run_connection_test(None)


async def test_a_changed_url_drops_the_shown_result(mgr, monkeypatch):
    """The result belongs to the address it was made with."""
    await mgr.scan()
    monkeypatch.setattr(shelly, "fetch_test", fetching(mgr))
    await mgr.run_connection_test(A)
    mgr.download_base = "http://192.168.1.3:8098"
    assert mgr.public_state()["connection_test"] is None


async def test_download_server_test_address(mgr):
    """The test address answers only during a test, only to its device, once; HEAD does
    not use it up; it never serves a file."""
    token = "a" * 32
    async with TestClient(TestServer(download_server.build_download_app(mgr))) as dl:
        assert (await dl.get(f"/connection-test/{token}")).status == 404  # no test running
        mgr._test = {"token": token, "ip": "127.0.0.1", "fetched": False, "from": set()}
        assert (await dl.get("/connection-test/" + "b" * 32)).status == 404  # wrong token
        assert (await dl.head(f"/connection-test/{token}")).status == 200
        answer = await dl.get(f"/connection-test/{token}")
        assert answer.status == 200 and (await answer.text()) == download_server.TEST_ANSWER
        assert "sandbox" in answer.headers["Content-Security-Policy"]
        assert (await dl.get(f"/connection-test/{token}")).status == 404  # only once
        mgr._test = {"token": token, "ip": "192.168.1.10", "fetched": False, "from": set()}
        assert (await dl.get(f"/connection-test/{token}")).status == 404  # another address
        assert mgr._test["from"] == {"127.0.0.1"}
        assert (await dl.get("/connection-test/../" + token)).status == 404


async def test_connection_test_over_http(client, mgr, monkeypatch):
    """POST /api/settings/connection-test: the result; a bad device ID is refused."""
    await mgr.scan()
    monkeypatch.setattr(shelly, "fetch_test", fetching(mgr))
    headers = {"X-CSRF-Token": client.csrf}
    answer = await client.post("/api/settings/connection-test", json={"device": None}, headers=headers)
    assert answer.status == 200 and (await answer.json())["ok"] is True
    bad = await client.post("/api/settings/connection-test", json={"device": "../x"}, headers=headers)
    assert bad.status == 400


# ---------------------------------------------------------------- "Try again"


async def test_probe_of_a_device_that_did_not_answer(mgr, net, monkeypatch):
    """Address from Home Assistant, port, answer – each with its result; nothing changes."""
    net.asleep.add(A)  # the fake raises TimeoutError: no answer at all
    await mgr.scan()
    assert mgr.devices[A]["unreachable"] is True and mgr.devices[B].get("unreachable") is None
    net.unreachable.add(B)  # an answer that is an error: not "unreachable"
    await mgr.check_device(B)
    assert mgr.devices[B]["unreachable"] is False
    opened = []

    async def port_open(ip, port):
        opened.append((ip, port))
        raise TimeoutError()

    monkeypatch.setattr(shelly, "port_open", port_open)
    steps = await mgr.probe_device(A)
    assert steps["address"] == {"state": "ok", "ip": "192.168.1.10", "moved": False}
    assert steps["port"]["state"] == "error" and "no answer after" in steps["port"]["text"]
    assert steps["device"]["state"] == "skipped" and opened == [("192.168.1.10", 80)]

    async def port_ok(_ip, _port):
        return 0.01

    async def answers(_session, _origin):
        return 0.2

    monkeypatch.setattr(shelly, "port_open", port_ok)
    monkeypatch.setattr(shelly, "answers", answers)
    steps = await mgr.probe_device(A)
    assert steps["port"]["state"] == "ok" and steps["device"] == {"state": "ok", "seconds": 0.2}
    assert mgr.devices[A]["status"] == "error"  # no check, no change
    net.removed.add(A)
    assert (await mgr.probe_device(A))["address"]["state"] == "error"


async def test_probe_over_http(client, mgr, monkeypatch):
    """POST /api/devices/{id}/probe answers with the steps."""
    await mgr.scan()

    async def port_ok(_ip, _port):
        return 0.01

    async def answers(_session, _origin):
        return 0.1

    monkeypatch.setattr(shelly, "port_open", port_ok)
    monkeypatch.setattr(shelly, "answers", answers)
    answer = await client.post(f"/api/devices/{A}/probe", json={}, headers={"X-CSRF-Token": client.csrf})
    assert answer.status == 200 and (await answer.json())["device"]["state"] == "ok"


# ---------------------------------------------------------------- Text for a bug ticket


def test_masking():
    """Names, IPv4 (first two numbers kept), IPv6 and MAC addresses are masked."""
    masker = diagnostics.Masker(["Kitchen light", "shellyplus1-a8032ab12345"])
    text = masker.text(
        "Kitchen light at 192.168.1.10 (fe80::1:2:3) MAC A8:03:2A:B1:23:45 host shellyplus1-a8032ab12345.local 20:14"
    )
    assert "Kitchen" not in text and "192.168.x.x" in text and "[IPv6]" in text
    assert "****2345" in text and "a8032ab12345" not in text and "20:14" in text
    assert text.count("[Name 1]") == 2


async def test_report_of_a_device(mgr, net, monkeypatch):
    """Versions, device, status, masked log lines of this device only; no password."""
    net.unreachable.add(A)
    await mgr.scan()
    mgr.credentials.store(A, mgr.credentials.candidate(2, "shellyplus1-a", "secret-pw"))

    async def ha_version():
        return "2026.11.2"

    monkeypatch.setattr(mgr, "ha_version", ha_version)
    text = await mgr.device_report(A, "3.12.0")
    assert "App: Firmware Manager for Shelly 3.12.0" in text and "Home Assistant: 2026.11.2" in text
    assert "Kitchen" not in text and "192.168.1.10" not in text and "192.168.x.x" in text
    assert "secret-pw" not in text and "Log (last" in text
    assert "Hall" not in text  # other devices stay out


async def test_report_over_http(client, mgr, monkeypatch):
    """GET /api/devices/{id}/report answers with the text."""
    await mgr.scan()

    async def ha_version():
        return None

    monkeypatch.setattr(mgr, "ha_version", ha_version)
    answer = await client.get(f"/api/devices/{A}/report")
    assert answer.status == 200 and "Home Assistant: unknown" in (await answer.json())["text"]


# ---------------------------------------------------------------- Getting started


async def test_getting_started_on_a_fresh_installation_only(net, client, mgr):
    """A fresh installation shows the card until it is hidden; a restart keeps that."""
    fresh = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert fresh.public_state()["getting_started"] is True
    fresh.hide_getting_started()
    assert manager.Manager(dict(OPTIONS), session=None, token="t").getting_started is False
    mgr.getting_started = True
    answer = await client.post("/api/getting-started/hide", json={}, headers={"X-CSRF-Token": client.csrf})
    assert answer.status == 200 and mgr.getting_started is False


async def test_an_existing_installation_gets_no_card(mgr):
    """A state file of an older version (without the field): no card."""
    mgr.save()
    state = json.loads(data_paths.STATE_FILE.read_text())
    del state["getting_started"]
    data_paths.STATE_FILE.write_text(json.dumps(state))
    assert manager.Manager(dict(OPTIONS), session=None, token="t").getting_started is False


# ---------------------------------------------------------------- Areas of Home Assistant


async def test_areas_of_home_assistant(mgr, net):
    """The area name per device; without area None; a failed area request keeps them."""
    net.areas = {"kitchen": "Küche", "garage": "Garage"}
    net.device_areas = {A: "kitchen", B: "garage"}
    await mgr.scan()
    assert (mgr.devices[A]["area"], mgr.devices[B]["area"], mgr.devices[C]["area"]) == ("Küche", "Garage", None)
    assert next(d for d in mgr.public_state()["devices"] if d["id"] == A)["area"] == "Küche"
    net.area_error = ShellyError("no answer")
    await mgr.scan()
    assert mgr.devices[A]["area"] == "Küche"
    net.area_error = None
    net.device_areas = {A: "garage"}
    await mgr.check_device(A)
    assert mgr.devices[A]["area"] == "Garage"


def test_schedule_areas_are_validated():
    """{"areas": [...]}: at least one, no control characters, duplicates dropped."""
    base = {"name": "Nightly", "action": "restart", "time": "03:00", "repeat": {"kind": "daily"}}
    clean = timetable.validate({**base, "devices": {"areas": ["Garage", "", "Garage"]}}, set())
    assert clean["devices"] == {"areas": ["Garage", ""]}
    for bad in ({"areas": []}, {"areas": ["x\n"]}, {"areas": [1]}, {"areas": ["x" * 65]}, {"x": 1}):
        with pytest.raises(ValueError):
            timetable.validate({**base, "devices": bad}, set())
    assert timetable.devices_text({"areas": ["Garage", ""]}, {}) == "Areas: Garage, Without area"
    areas = {A: "Garage", B: None, C: "Küche"}
    assert timetable.chosen_devices({"areas": ["Garage", ""]}, areas) == [A, B]
    assert timetable.chosen_devices("all", areas) == [A, B, C]


async def test_a_schedule_by_area_takes_new_devices(mgr, net):
    """A schedule by area acts on the devices in the area at the time of the run."""
    net.areas = {"garage": "Garage"}
    net.device_areas = {A: "garage"}
    await mgr.scan()
    data = {"name": "Garage", "action": "backup", "time": "03:00", "repeat": {"kind": "daily"},
            "devices": {"areas": ["Garage"]}}
    mgr.create_schedule(data, "alice")
    view = mgr.schedules_view()[0]
    assert view["count"] == 1 and view["devices_text"] == "Areas: Garage"
    net.device_areas[B] = "garage"
    await mgr.scan()
    assert mgr.schedules_view()[0]["count"] == 2
