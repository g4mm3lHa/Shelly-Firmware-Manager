"""HTTP tests of the UI app: endpoints, CSRF, Ingress, 409s, upload, headers.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import json
import re

import shelly
import web_ui
from aiohttp.test_utils import TestClient, TestServer
from conftest import A, B, C
from helpers import binary, firmware_zip, store


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


async def test_http_upload_flow(client, mgr, net):
    await mgr.scan()
    data = firmware_zip({"name": "Plus1", "version": "1.3.0"}, size=60000)  # larger than 32 KB
    r = await client.post("/api/firmware/upload", data=data, headers=binary(client))
    assert r.status == 200, await r.text()
    staged = await r.json()
    assert staged["detected"]["version"] == "1.3.0" and "path" not in staged
    r = await client.post("/api/firmware/upload/confirm", json={"token": staged["token"], "type": "Plus1",
                          "version": "1.3.0"}, headers={"X-CSRF-Token": client.csrf})
    assert r.status == 200 and (await r.json())["version"] == "1.3.0"
    listing = await (await client.get("/api/firmware")).json()
    assert listing["total_size"] == mgr.store_size() and len(listing["firmware"]) == 2
    # Devices a file is provided for, with their IDs (Install button of the Firmware tab)
    provided = next(f for f in listing["firmware"] if f["version"] == "1.5.0")
    assert {d["id"] for d in provided["devices"]} == {A, C} and all(d["name"] for d in provided["devices"])
    state = await (await client.get("/api/state")).json()
    assert next(d for d in state["devices"] if d["id"] == A)["offer_source"] == "manufacturer"
    # Changelog of a stored firmware for the Firmware tab (flag in the list, text on demand)
    assert provided["changelog"] is False
    r = await client.get(f"/api/firmware/{provided['name']}/changelog")
    assert r.status == 200 and await r.json() == {"available": False, "versions": []}
    mgr.firmware[provided["name"]]["changelog"] = {"date": None, "entries": [{"category": None, "text": "Fix", "models": []}]}
    data = await (await client.get(f"/api/firmware/{provided['name']}/changelog")).json()
    assert data["available"] and data["versions"][0]["version"] == "1.5.0" and data["versions"][0]["entries"][0]["text"] == "Fix"
    assert (await client.get(f"/api/firmware/{'0' * 64}.zip/changelog")).status == 404


async def test_http_upload_guards(client, mgr, net):
    data = firmware_zip()
    r = await client.post("/api/firmware/upload", data=data, headers={**binary(client), "X-CSRF-Token": "x"})
    assert r.status == 403
    r = await client.post("/api/firmware/upload", json={"a": 1}, headers={"X-CSRF-Token": client.csrf})
    assert r.status == 415  # JSON is not accepted here
    # Binary bodies stay refused everywhere else, and JSON keeps its 32 KB limit
    r = await client.post("/api/firmware/upload/confirm", data=data, headers=binary(client))
    assert r.status == 415
    r = await client.post("/api/firmware/upload/confirm", data=json.dumps({"x": "y" * 40000}),
                          headers={"X-CSRF-Token": client.csrf, "Content-Type": "application/json"})
    assert r.status == 413
    # Announced size above 64 MiB: refused before the body is read (last – the
    # server closes the connection)
    r = await client.post("/api/firmware/upload", data=data,
                          headers={**binary(client), "Content-Length": str(shelly.MAX_FW_SIZE + 1)})
    assert r.status == 413


async def test_http_downgrade_and_delete_need_acknowledgment(client, mgr, net):
    await mgr.scan()
    name, sha = store(mgr, "Plus1", "1.4.0")
    r = await client.get(f"/api/devices/{B}/downgrades")
    body = await r.json()
    assert body["installed"] == "1.5.0" and [v["version"] for v in body["versions"]] == ["1.4.0"]
    assert body["blocked"] is None
    hdr = {"X-CSRF-Token": client.csrf}
    r = await client.post(f"/api/devices/{B}/install", json={"sha256": sha, "confirm": True, "downgrade": True},
                          headers=hdr)
    assert r.status == 400
    r = await client.post(f"/api/devices/{B}/install", json={"sha256": "1" * 64, "confirm": True,
                          "downgrade": True, "acknowledged": True}, headers=hdr)
    assert r.status == 409
    r = await client.post(f"/api/firmware/{name}/delete", json={"confirm": True}, headers=hdr)
    assert r.status == 400 and name in mgr.firmware
    r = await client.post(f"/api/firmware/{name}/delete", json={"confirm": True, "acknowledged": True}, headers=hdr)
    assert r.status == 200 and name not in mgr.firmware
    provided = mgr.devices[A]["file"]
    r = await client.post(f"/api/firmware/{provided}/delete", json={"confirm": True, "acknowledged": True},
                          headers=hdr)
    assert r.status == 409


async def test_http_install_of_a_store_fallback_needs_acknowledgment(client, mgr, net, monkeypatch):
    """A stored version offered while the server could not be asked is installed only
    with the risk acknowledgment."""
    store(mgr, "Plus1", "1.6.0")
    net.offer_error = shelly.ShellyError("408, 'Request Timeout'")
    await mgr.scan()
    started = []

    async def install(device_id, sha, approved_by, downgrade=False):
        started.append(device_id)
        return True

    monkeypatch.setattr(mgr, "install", install)
    sha, hdr = mgr.devices[A]["sha256"], {"X-CSRF-Token": client.csrf}
    r = await client.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True}, headers=hdr)
    assert r.status == 400 and not started
    r = await client.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True, "acknowledged": True},
                          headers=hdr)
    assert r.status == 200
    await asyncio.sleep(0)
    assert started == [A]


async def test_log_entries_can_be_deleted_over_http(client, mgr, net):
    """Delete one entry / all entries – confirmation and CSRF token required."""
    await mgr.scan()
    await mgr.check_device(A)
    entries = (await (await client.get("/api/logs")).json())["entries"]
    target = entries[0]["id"]
    url = f"/api/logs/{target}/delete"
    assert (await client.post(url, json={"confirm": True})).status == 403  # no CSRF token
    assert (await client.post(url, json={}, headers=hdr(client))).status == 400
    assert (await client.post("/api/logs/zzz/delete", json={"confirm": True}, headers=hdr(client))).status == 404
    assert (await client.post(f"/api/logs/{'0' * 12}/delete", json={"confirm": True}, headers=hdr(client))).status == 404
    assert (await client.post(url, json={"confirm": True}, headers=hdr(client))).status == 200
    left = (await (await client.get("/api/logs")).json())["entries"]
    assert target not in [e["id"] for e in left] and len(left) == len(entries) - 1
    assert (await client.post("/api/logs/clear", json={}, headers=hdr(client))).status == 400
    r = await client.post("/api/logs/clear", json={"confirm": True}, headers={**hdr(client), "X-Remote-User-Name": "alice"})
    assert r.status == 200 and (await r.json())["deleted"] == len(left)
    [entry] = (await (await client.get("/api/logs")).json())["entries"]
    assert entry["kind"] == "log" and entry["user"] == "alice" and entry["entries"] == len(left)


async def test_state_counts_the_stored_older_versions(mgr, net):
    """The row menu disables "Downgrade" without a stored older version."""
    await mgr.scan()
    count = lambda: {d["id"]: d["downgrades"] for d in mgr.public_state()["devices"]}
    assert count() == {A: 0, B: 0, C: 0}  # only 1.5.0 is stored: not older than any device
    store(mgr, "Plus1", "1.3.0")
    store(mgr, "Plus1", "1.4.0")
    store(mgr, "Plus1", "1.4.0", source="upload", content=b"u" * 3000)  # same version: counted once
    store(mgr, "Plus1", "1.2.0-beta1")
    assert count() == {A: 1, B: 2, C: 1}  # A, C on 1.4.0, B on 1.5.0
    assert {i: len(mgr.downgrade_versions(i)) for i in (A, B, C)} == count()


async def test_state_shows_the_downgrade_marker(mgr, net):
    await mgr.scan()
    mgr.devices[B]["downgrade"] = True
    assert next(d for d in mgr.public_state()["devices"] if d["id"] == B)["downgrade"] is True
