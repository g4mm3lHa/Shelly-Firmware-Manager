"""HTTP tests of the UI app: new single-device endpoint, CSRF, Ingress, 409s."""

import asyncio
import re

import pytest
import web_ui
from aiohttp.test_utils import TestClient, TestServer
from conftest import A, B


@pytest.fixture
async def client(mgr):
    """Test client; the test server connects from 127.0.0.1 = 'Ingress' here."""
    app = web_ui.build_ui_app(mgr, ingress_ip="127.0.0.1")
    async with TestClient(TestServer(app)) as c:
        page = await (await c.get("/")).text()
        c.csrf = re.search(r'name="csrf" content="([0-9a-f]{64})"', page).group(1)
        yield c


def hdr(c):
    """Headers of a valid write request."""
    return {"X-CSRF-Token": c.csrf}


async def wait_idle(mgr):
    """Wait until background checks finished."""
    for _ in range(200):
        if not mgr.checking:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("check did not finish")


async def test_check_device_endpoint(client, mgr, net):
    """POST /api/devices/{id}/check starts only this device; state shows it afterwards."""
    await mgr.scan()
    last = mgr.last_scan
    r = await client.post(f"/api/devices/{A}/check", json={}, headers=hdr(client))
    assert r.status == 200 and (await r.json())["started"]
    await wait_idle(mgr)
    state = await (await client.get("/api/state")).json()
    assert state["last_scan"] == last and state["progress"] is None
    dev = next(d for d in state["devices"] if d["id"] == A)
    assert dev["activity"] is None and dev["last_success"]


async def test_double_tap_gets_409(client, mgr, net):
    """Two quick requests: the second is refused synchronously (no double check)."""
    await mgr.scan()
    net.gate = asyncio.Event()
    r1 = await client.post(f"/api/devices/{A}/check", json={}, headers=hdr(client))
    r2 = await client.post(f"/api/devices/{A}/check", json={}, headers=hdr(client))
    r3 = await client.post("/api/scan", json={}, headers=hdr(client))
    r4 = await client.post(f"/api/devices/{B}/check", json={}, headers=hdr(client))
    assert (r1.status, r2.status, r3.status, r4.status) == (200, 409, 409, 409)
    state = await (await client.get("/api/state")).json()
    assert state["scanning"] and state["progress"]["kind"] == "device"
    assert "Checking Kitchen" in state["busy"]
    net.gate.set()
    await wait_idle(mgr)
    checks = [e for e in mgr.events.entries if e["kind"] == "check" and e.get("trigger") == "device"]
    assert len(checks) == 1


async def test_double_scan_gets_409(client, mgr, net):
    """'Check now' twice: one check only."""
    net.gate = asyncio.Event()
    r1 = await client.post("/api/scan", json={}, headers=hdr(client))
    r2 = await client.post("/api/scan", json={}, headers=hdr(client))
    assert (r1.status, r2.status) == (200, 409)
    net.gate.set()
    await wait_idle(mgr)
    assert len([e for e in mgr.events.entries if e["kind"] == "check"]) == 1


async def test_check_endpoint_requires_csrf_json_and_known_id(client, mgr):
    """CSRF, JSON content type and device ID validation apply to the new endpoint."""
    await mgr.scan()
    assert (await client.post(f"/api/devices/{A}/check", json={})).status == 403
    r = await client.post(f"/api/devices/{A}/check", data="x", headers={**hdr(client), "Content-Type": "text/plain"})
    assert r.status == 415
    assert (await client.post("/api/devices/" + "f" * 32 + "/check", json={}, headers=hdr(client))).status == 404
    assert (await client.post("/api/devices/../check", json={}, headers=hdr(client))).status in (404, 400)
    assert not mgr.checking


async def test_non_ingress_refused(mgr):
    """Requests not coming from the Ingress proxy are refused."""
    app = web_ui.build_ui_app(mgr)  # default: 172.30.32.2
    async with TestClient(TestServer(app)) as c:
        assert (await c.get("/api/state")).status == 403
        assert (await c.post(f"/api/devices/{A}/check", json={})).status == 403


async def test_security_headers_on_new_endpoint(client, mgr):
    """Security headers also on the new endpoint and on errors."""
    await mgr.scan()
    r = await client.post(f"/api/devices/{A}/check", json={}, headers=hdr(client))
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    await wait_idle(mgr)
    r = await client.post("/api/devices/" + "f" * 32 + "/check", json={}, headers=hdr(client))
    assert "Content-Security-Policy" in r.headers


async def test_shutdown_cancels_running_check(mgr, net):
    """App stop during a check: the task is cancelled and nothing stays 'checking'."""
    app = web_ui.build_ui_app(mgr, ingress_ip="127.0.0.1")
    c = TestClient(TestServer(app))
    await c.start_server()
    page = await (await c.get("/")).text()
    token = re.search(r'name="csrf" content="([0-9a-f]{64})"', page).group(1)
    net.gate = asyncio.Event()
    assert (await c.post("/api/scan", json={}, headers={"X-CSRF-Token": token})).status == 200
    await asyncio.sleep(0.05)
    assert mgr.activity
    await c.close()  # runs on_shutdown -> cancel_background
    assert mgr.activity == {} and mgr.progress is None and not mgr.checking


async def test_state_etag_304(client, mgr):
    """Unchanged state: 304 without body; changed state: new ETag and full body."""
    await mgr.scan()
    r1 = await client.get("/api/state")
    tag = r1.headers["ETag"]
    assert r1.status == 200 and len(await r1.read()) > 100
    r2 = await client.get("/api/state", headers={"If-None-Match": tag})
    assert r2.status == 304 and await r2.read() == b"" and r2.headers["ETag"] == tag
    assert r2.headers["Cache-Control"] == "no-store"
    mgr.devices[A]["name"] = "Renamed"
    r3 = await client.get("/api/state", headers={"If-None-Match": tag})
    assert r3.status == 200 and r3.headers["ETag"] != tag


async def test_static_cache_only_for_current_version(client):
    """Versioned static URL -> long browser cache; without/old version -> no-store; page itself no-store."""
    page = await client.get("/")
    html = await page.text()
    assert page.headers["Cache-Control"] == "no-store"
    version = re.search(r'static/app\.js\?v=([0-9a-f]{16})"', html).group(1)
    assert f"static/style.css?v={version}" in html and "__ASSET_VERSION__" not in html
    r = await client.get(f"/static/app.js?v={version}")
    assert r.status == 200 and r.headers["Cache-Control"] == web_ui.STATIC_CACHE
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert (await client.get("/static/app.js")).headers["Cache-Control"] == "no-store"
    assert (await client.get("/static/app.js?v=0000000000000000")).headers["Cache-Control"] == "no-store"
    assert (await client.get(f"/static/nope.js?v={version}")).status == 404


async def test_ignore_and_reload_endpoints(client, mgr, net):
    """3.0: /ignore and /reload with CSRF, JSON, ID and type checks."""
    await mgr.scan()
    for path, body in (("ignore", {"ignored": True}), ("reload", {})):
        assert (await client.post(f"/api/devices/{A}/{path}", json=body)).status == 403
        r = await client.post(f"/api/devices/{A}/{path}", data="x", headers={**hdr(client), "Content-Type": "text/plain"})
        assert r.status == 415
        assert (await client.post("/api/devices/" + "f" * 32 + f"/{path}", json=body, headers=hdr(client))).status == 404
    assert (await client.post(f"/api/devices/{A}/ignore", json={"ignored": "yes"}, headers=hdr(client))).status == 400
    r = await client.post(f"/api/devices/{A}/ignore", json={"ignored": True}, headers=hdr(client))
    assert r.status == 200 and (await r.json()) == {"ignored": True}
    offers = net.offer_calls
    r = await client.post(f"/api/devices/{B}/reload", json={}, headers=hdr(client))
    assert r.status == 200 and (await r.json())["warning"] is None and net.offer_calls == offers


async def test_password_endpoint_validation(client, mgr, net):
    """Unknown mode and missing confirmation are refused; no secret is echoed."""
    await mgr.scan()
    url = f"/api/devices/{A}/password"
    r = await client.post(url, json={"mode": "bogus", "password": "x", "confirm": True}, headers=hdr(client))
    assert r.status == 400
    r = await client.post(url, json={"mode": "enable", "password": "Secret-9", "confirm": False}, headers=hdr(client))
    assert r.status == 400 and "Secret-9" not in await r.text()
    r = await client.post(url, json={"mode": "forget", "confirm": True}, headers=hdr(client))
    assert r.status == 200 and (await r.json())["password_set"] is False
