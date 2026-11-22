"""Security rules of both web servers (formerly the workspace smoke test, 3.0.1).

UI app: strict CSP, no Server banner, 32 KB limit for JSON bodies, no secrets in the
settings view. Download server: only provided files, sandbox CSP, no other paths, no
Server banner, no request bodies. No network: test servers on 127.0.0.1 only."""

import download_server
import pytest
from helpers import chunks
from aiohttp.test_utils import TestClient, TestServer


async def test_ui_page_headers(client):
    """The page carries the strict CSP and no Server header."""
    page = await client.get("/")
    assert page.status == 200
    csp = page.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "frame-ancestors 'self'" in csp
    assert "Server" not in page.headers
    assert page.headers["Cache-Control"] == "no-store" and page.headers["X-Content-Type-Options"] == "nosniff"


async def test_ui_json_body_limit(client):
    """A JSON body above 32 KB is refused before any handler runs."""
    r = await client.post(
        "/api/logs/clear",
        data="{" + " " * 40000 + "}",
        headers={"X-CSRF-Token": client.csrf, "Content-Type": "application/json"},
    )
    assert r.status == 413


async def test_settings_view_has_no_secrets(client):
    """The settings view only contains option keys of config.yaml."""
    settings = await (await client.get("/api/settings")).json()
    assert "shelly_password" not in settings and "download_base_url" in settings


@pytest.fixture
async def dl(mgr, monkeypatch):
    """Test client of the download server (serving files see test_install)."""
    async with TestClient(TestServer(download_server.build_download_app(mgr))) as c:
        yield c


async def test_download_server_serves_nothing_unprovided(dl, mgr):
    """Unprovided file, other paths and bodies: 404/405/413, always with the sandbox CSP."""
    r = await dl.get("/" + "a" * 64 + ".bin")
    assert r.status == 404
    assert "sandbox" in r.headers["Content-Security-Policy"] and "Server" not in r.headers
    assert (await dl.get("/index.html")).status == 404
    assert (await dl.get("/../" + "a" * 64 + ".bin")).status == 404
    assert (await dl.post("/" + "a" * 64 + ".bin", data=b"x")).status in (405, 413)
    assert mgr.downloads == {}  # nothing recorded for unknown files



async def test_refusals_carry_the_security_headers(client, mgr):
    """Answers of the access checks themselves – wrong CSRF token, wrong content
    type – carry the same headers as every other answer."""
    wrong_token = await client.post("/api/scan", json={}, headers={"X-CSRF-Token": "0" * 64})
    wrong_type = await client.post("/api/scan", data="x", headers={"X-CSRF-Token": client.csrf, "Content-Type": "text/plain"})
    assert (wrong_token.status, wrong_type.status) == (403, 415)
    for answer in (wrong_token, wrong_type):
        assert "default-src 'none'" in answer.headers["Content-Security-Policy"]
        assert answer.headers["X-Content-Type-Options"] == "nosniff" and "Server" not in answer.headers


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"X-CSRF-Token": "ü" * 64}, b"{}", 403),  # non-ASCII token: wrong token, no error
        ({"Content-Type": "application/json; charset=x-unknown"}, b"{}", 400),  # unknown charset
        ({}, b'{"confirm": ' + b"[" * 50000 + b"]" * 50000 + b"}", 413),  # too large anyway
        ({}, b"[" * 20000, 400),  # nested too deeply (RecursionError in json)
    ],
    ids=["non-ascii-token", "unknown-charset", "too-large", "too-deep"],
)
async def test_odd_requests_are_refused_with_the_security_headers(client, headers, body, status):
    """Odd but small requests are refused as client errors (never a 500), with the headers."""
    answer = await client.post(
        "/api/logs/clear", data=body,
        headers={"X-CSRF-Token": client.csrf, "Content-Type": "application/json", **headers},
    )
    assert answer.status == status
    assert "default-src 'none'" in answer.headers["Content-Security-Policy"]


async def test_non_ascii_tokens_of_transfers_are_unknown_tokens(client, mgr):
    """A token with other characters while an upload and an import are staged: an
    unknown token (409), never an error of the comparison."""
    await mgr.stage_upload(chunks(b"u" * 5000), "fw.bin", 5000)
    await mgr.stage_import(chunks(b"PK\x05\x06" + b"\0" * 18), 22)
    for path in ("/api/firmware/upload/confirm", "/api/firmware/import/check", "/api/firmware/import/confirm"):
        answer = await client.post(path, json={"token": "ü" * 32, "type": "Plus1", "version": "1.0.0", "password": "x"},
                                   headers={"X-CSRF-Token": client.csrf})
        assert answer.status == 409, path


async def test_foreign_address_is_refused_with_the_security_headers(mgr):
    """A request that does not come from the Ingress proxy gets 403 with the headers."""
    import web_ui

    async with TestClient(TestServer(web_ui.build_ui_app(mgr, ingress_ip="172.30.32.2"))) as c:
        answer = await c.get("/")
        assert answer.status == 403 and "default-src 'none'" in answer.headers["Content-Security-Policy"]


async def test_unexpected_error_is_answered_without_details(client, mgr, monkeypatch):
    """An error no handler expects: 500 with the headers, the error type only – the
    details go to the app log."""
    def broken():
        raise RuntimeError("internal detail")

    monkeypatch.setattr(mgr, "firmware_list", broken)
    answer = await client.get("/api/firmware")
    text = await answer.text()
    assert answer.status == 500 and "RuntimeError" in text and "internal detail" not in text
    assert "default-src 'none'" in answer.headers["Content-Security-Policy"]


async def test_ids_from_the_url_take_no_trailing_line_break(client, mgr, net):
    """A device ID or file name followed by an encoded line break is unknown."""
    await mgr.scan()
    device = next(iter(mgr.devices))
    name = next(iter(mgr.firmware))
    assert (await client.get(f"/api/devices/{device}/changelog")).status == 200
    assert (await client.get(f"/api/devices/{device}%0A/changelog")).status == 404
    assert (await client.get(f"/api/firmware/{name}/changelog")).status == 200
    assert (await client.get(f"/api/firmware/{name}%0A/changelog")).status == 404
