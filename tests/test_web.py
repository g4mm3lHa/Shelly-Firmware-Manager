"""HTTP tests of the UI app: endpoints, CSRF, Ingress, 409s, upload, headers.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import json
import re

import data_paths
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
        if not mgr.checking and not mgr.device_checks:
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
    """Two quick requests for one device: the second is refused synchronously (no
    double check), as is the full check; another device is checked in parallel."""
    await mgr.scan()
    net.gate = asyncio.Event()
    r1 = await client.post(f"/api/devices/{A}/check", json={}, headers=hdr(client))
    r2 = await client.post(f"/api/devices/{A}/check", json={}, headers=hdr(client))
    r3 = await client.post("/api/scan", json={}, headers=hdr(client))
    r4 = await client.post(f"/api/devices/{B}/check", json={}, headers=hdr(client))
    assert (r1.status, r2.status, r3.status, r4.status) == (200, 409, 409, 200)  # B: in parallel
    state = await (await client.get("/api/state")).json()
    assert not state["scanning"] and state["progress"] is None
    assert state["busy"] == "Checking 2 devices"
    net.gate.set()
    await wait_idle(mgr)
    checks = [e for e in mgr.events.entries if e["kind"] == "check" and e.get("trigger") == "device"]
    assert len(checks) == 2


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


async def test_remove_from_list_endpoint(client, mgr, net):
    """/forget: only removed devices, only with confirmation (CSRF and JSON rules);
    the state names the section and the reachability of the removed device."""
    await mgr.scan()
    url = f"/api/devices/{C}/forget"
    assert (await client.post(url, json={"confirm": True})).status == 403
    assert (await client.post(url, json={"confirm": True}, headers=hdr(client))).status == 400  # not removed
    net.removed.add(C)
    await mgr.scan()
    state = await (await client.get("/api/state")).json()
    row = next(d for d in state["devices"] if d["id"] == C)
    assert row["section"] == "removed" and row["removed"] and row["removed_reachable"] is True
    assert next(d for d in state["devices"] if d["id"] == A)["section"] == "updates" and state["others"] == []
    assert (await client.post(url, json={}, headers=hdr(client))).status == 400
    r = await client.post(url, json={"confirm": True}, headers=hdr(client))
    assert r.status == 200 and C not in mgr.devices
    assert (await client.post(url, json={"confirm": True}, headers=hdr(client))).status == 404


async def test_unknown_file_can_be_deleted_but_not_downloaded(client, mgr, net):
    """An unknown file of the firmware folder is listed and deletable over HTTP; other
    firmware routes do not serve it."""
    name = "e" * 64 + ".zip"
    (data_paths.FW_DIR / name).write_bytes(b"x" * 100)
    mgr.cleanup_firmware()
    listed = (await (await client.get("/api/firmware")).json())["firmware"]
    assert {"name": name, "unknown": True, "size": 100, "deletable": True, "locked": None} in listed
    assert (await client.get(f"/api/firmware/{name}/download")).status == 404
    url = f"/api/firmware/{name}/delete"
    r = await client.post(url, json={"confirm": True, "acknowledged": True}, headers=hdr(client))
    assert r.status == 200 and not (data_paths.FW_DIR / name).exists()
    assert (await client.post(url, json={"confirm": True, "acknowledged": True}, headers=hdr(client))).status == 404


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
    r = await client.post(url, json={"mode": "enable", "password": "Secret-9", "confirm": True, "acknowledged": True,
                          "keep": "no"}, headers=hdr(client))
    assert r.status == 400 and "Secret-9" not in await r.text()


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
    # Free disk space of the store volume (Firmware tab summary)
    assert isinstance(listing["free_space"], int) and listing["free_space"] > 0
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


async def test_http_downgrade_refused_while_the_option_is_off(client, mgr, net):
    """allow_downgrade off: the install request is refused with the Settings hint."""
    await mgr.scan()
    _name, sha = store(mgr, "Plus1", "1.4.0")
    mgr.allow_downgrade = False
    r = await client.post(f"/api/devices/{B}/install", json={"sha256": sha, "confirm": True, "downgrade": True,
                          "acknowledged": True}, headers={"X-CSRF-Token": client.csrf})
    assert r.status == 409 and "Allow downgrades" in await r.text()
    assert (await (await client.get("/api/state")).json())["allow_downgrade"] is False


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
    mgr.devices[A]["status"] = "installing"  # a file being installed stays locked
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


async def test_http_store_export_and_import(client, mgr, net):
    """Export (password once, single-use download), import in three steps; the
    password never appears in the state or the log."""
    store(mgr, "Plus1", "1.3.0")
    assert (await client.post("/api/firmware/export", json={})).status == 403  # CSRF
    r = await client.post("/api/firmware/export", json={}, headers=hdr(client))
    assert r.status == 200
    prepared = await r.json()
    assert prepared["files"] == 1 and len(prepared["password"]) == 24
    assert (await client.get("/api/firmware/export/zz")).status == 404
    r = await client.get(f"/api/firmware/export/{prepared['token']}")
    assert r.status == 200 and r.headers["Content-Type"] == "application/zip"
    assert "attachment" in r.headers["Content-Disposition"]
    data = await r.read()
    assert (await client.get(f"/api/firmware/export/{prepared['token']}")).status == 409  # single use
    for name in list(mgr.firmware):
        (data_paths.FW_DIR / name).unlink()
    mgr.firmware.clear()

    r = await client.post("/api/firmware/import", data=data, headers=binary(client, "store.zip"))
    assert r.status == 200, await r.text()
    token = (await r.json())["token"]
    r = await client.post("/api/firmware/import/check", json={"token": token, "password": "wrong"}, headers=hdr(client))
    assert r.status == 400 and "password" in await r.text()
    r = await client.post(
        "/api/firmware/import/check", json={"token": token, "password": prepared["password"]}, headers=hdr(client)
    )
    assert r.status == 200 and len((await r.json())["new"]) == 1
    r = await client.post("/api/firmware/import/confirm", json={"token": token}, headers=hdr(client))
    assert r.status == 200 and (await r.json())["stored"] == 1
    blobs = json.dumps(mgr.public_state()) + json.dumps(mgr.events.list())
    assert prepared["password"] not in blobs


async def test_http_store_import_guards(client, mgr, net):
    """CSRF, the size limit of the option and a missing length are checked before
    anything is stored."""
    r = await client.post("/api/firmware/import", data=b"x" * 10, headers={**binary(client), "X-CSRF-Token": "x"})
    assert r.status == 403
    mgr.import_max = 100
    r = await client.post("/api/firmware/import", data=b"x" * 101, headers=binary(client))
    assert r.status == 413
    r = await client.post("/api/firmware/import", data=b"x" * 50, headers=binary(client))
    assert r.status == 400 and "ZIP" in await r.text()
    r = await client.post("/api/firmware/import", data=b"{}", headers={**hdr(client), "Content-Type": "application/json"})
    assert r.status in (400, 415)
    assert (await client.post("/api/firmware/export", json={}, headers=hdr(client))).status == 409  # empty store


async def test_http_first_start_and_bulk_passwords(client, mgr, net):
    """First-start question: checks refused until Skip. Bulk passwords: validated."""
    mgr.first_start_open = True
    assert (await client.post("/api/scan", json={}, headers=hdr(client))).status == 409
    assert (await client.get("/api/state")).status == 200
    assert (await client.post("/api/first-start/skip", json={}, headers=hdr(client))).status == 200
    assert not mgr.first_start_open
    state = await (await client.get("/api/state")).json()
    assert state["first_start_open"] is False and state["import_max_mb"] == 100
    r = await client.post("/api/devices/passwords", json={"mode": "set", "devices": [A], "password": 5},
                          headers=hdr(client))
    assert r.status == 400
    r = await client.post("/api/devices/passwords", json={"mode": "set", "devices": [A], "password": "pw"},
                          headers=hdr(client))
    assert r.status == 400 and "Confirmation" in await r.text()


async def test_http_upload_and_import_in_parts(client, mgr, net, monkeypatch):
    """Parts with X-Upload-Total/Offset/Transfer (Ingress refuses bodies above 16 MiB);
    invalid headers, oversized parts and totals are refused."""
    import manager_store

    monkeypatch.setattr(manager_store, "PART_MAX", 20000)
    monkeypatch.setattr(web_ui, "PART_MAX", 20000)
    await mgr.scan()
    data = firmware_zip({"name": "Plus1", "version": "1.3.0"}, size=50000)
    transfer, answer = None, None
    for offset in range(0, len(data), 20000):
        part = data[offset : offset + 20000]
        headers = {**binary(client), "X-Upload-Total": str(len(data)), "X-Upload-Offset": str(offset)}
        if transfer:
            headers["X-Upload-Transfer"] = transfer
        r = await client.post("/api/firmware/upload", data=part, headers=headers)
        assert r.status == 200, await r.text()
        answer = await r.json()
        transfer = answer.get("transfer")
    assert answer["detected"]["version"] == "1.3.0" and answer["size"] == len(data)
    bad = {**binary(client), "X-Upload-Total": "x"}
    assert (await client.post("/api/firmware/upload", data=b"x" * 10, headers=bad)).status == 400
    bad = {**binary(client), "X-Upload-Total": "100", "X-Upload-Transfer": "zz"}
    assert (await client.post("/api/firmware/upload", data=b"x" * 10, headers=bad)).status == 400
    big = {**binary(client), "X-Upload-Total": str(65 * 1024 * 1024)}
    assert (await client.post("/api/firmware/upload", data=b"x" * 10, headers=big)).status == 413
    assert (await client.post("/api/firmware/upload", data=b"x" * 20001, headers=binary(client))).status == 413
    mgr.import_max = 1000
    big = {**binary(client), "X-Upload-Total": "1001"}
    assert (await client.post("/api/firmware/import", data=b"x" * 10, headers=big)).status == 413


async def test_http_save_refuses_a_public_ip_download_base_url(client, mgr, net):
    """POST /api/settings names the reason in the error text and changes nothing."""
    for url, text in (("http://203.0.113.10:8098", "not in a private network"),
                      ("http://8.8.8.8 ", "http(s)://host:port expected"),
                      ("http://user:pw@192.168.1.2:8098", "without user name")):
        r = await client.post("/api/settings", json={"download_base_url": url}, headers=hdr(client))
        assert r.status == 400 and text in await r.text(), url
    assert mgr.download_base == "http://192.168.1.2:8098"


async def test_http_install_refused_with_a_stored_bad_download_base_url(net):
    """An address saved before the rule existed: state shows the issue, install and downgrade answer 409."""
    from helpers import make_manager, web_client
    for url, issue in (("http://203.0.113.10:8098", "not_private"), ("http://8.8.8.8 ", "invalid")):
        m = make_manager(net, download_base_url=url)
        c = await web_client(m)
        try:
            m.devices[A] = {"status": "ready", "sha256": "a" * 64, "gen": 2, "ip": "192.168.1.50"}
            m.devices[B] = {"status": "up-to-date", "gen": 2, "ip": "192.168.1.51"}
            state = await (await c.get("/api/state")).json()
            assert state["download_base"]["issue"] == issue and state["download_base"]["message"]
            _name, sha = store(m, "Plus1", "1.4.0")
            r = await c.post(f"/api/devices/{A}/install", json={"sha256": "a" * 64, "confirm": True}, headers=hdr(c))
            assert r.status == 409 and await r.text() == state["download_base"]["message"]
            m.allow_downgrade = True
            r = await c.post(f"/api/devices/{B}/install", json={"sha256": sha, "confirm": True, "downgrade": True,
                             "acknowledged": True}, headers=hdr(c))
            assert r.status == 409 and await r.text() == state["download_base"]["message"]
            r = await c.post("/api/settings/download-base/confirm", json={"url": url}, headers=hdr(c))
            assert r.status == 409  # nothing to confirm
        finally:
            await c.close()
