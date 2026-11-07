"""Security rules of both web servers (formerly the workspace smoke test, 3.0.1).

UI app: strict CSP, no Server banner, 32 KB limit for JSON bodies, no secrets in the
settings view. Download server: only provided files, sandbox CSP, no other paths, no
Server banner, no request bodies. No network: test servers on 127.0.0.1 only."""

import download_server
import manager
import pytest
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
        "/api/ui",
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
    monkeypatch.setattr(download_server, "FW_DIR", manager.FW_DIR)  # bound at import
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

