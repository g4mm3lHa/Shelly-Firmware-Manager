"""Shared helpers of the test modules (plain functions, no fixtures)."""

import asyncio
import hashlib
import io
import json
import re
import zipfile

import data_paths
import manager
import manager_core
import web_ui
from aiohttp.test_utils import TestClient, TestServer
from conftest import OPTIONS


def make_manager(net, **options):
    """Manager with custom options (net fixture patches files and network)."""
    m = manager.Manager({**OPTIONS, **options}, session=None, token="t")
    m.first_start_open = False
    m.notify = lambda *_a: asyncio.sleep(0, True)
    m._battery_devices = lambda: asyncio.sleep(0, set(net.ha_battery))
    return m


async def web_client(m):
    """Test client of the UI app with the CSRF token."""
    client = TestClient(TestServer(web_ui.build_ui_app(m, ingress_ip="127.0.0.1")))
    await client.start_server()
    page = await (await client.get("/")).text()
    client.csrf = re.search(r'name="csrf" content="([0-9a-f]{64})"', page).group(1)
    return client


def without_url():
    """Options as delivered by the Supervisor when download_base_url is unset."""
    return {k: v for k, v in OPTIONS.items() if k != "download_base_url"}


def binary(c, name="fw.zip"):
    return {"X-CSRF-Token": c.csrf, "Content-Type": "application/octet-stream", "X-Filename": name}


async def chunks(data, size=4096):
    """Async iterator over data, like a streamed request body."""
    for i in range(0, len(data), size):
        await asyncio.sleep(0)
        yield data[i : i + size]


def firmware_zip(manifest=None, size=5000):
    """A small ZIP as a Gen2+ firmware file, optionally with manifest.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("app.bin", b"\x00" * size)
    return buf.getvalue()


def store(mgr, fw_type, version, gen=2, source=manager_core.SOURCE_DOWNLOAD, content=None):
    """Put a firmware file into the store (as a check or an upload would)."""
    data = content or (f"{fw_type}-{version}-".encode() * 2000)
    sha = hashlib.sha256(data).hexdigest()
    name = f"{sha}.zip"
    (data_paths.FW_DIR / name).write_bytes(data)
    mgr._register_firmware(name, fw_type, gen, version, sha, len(data), True, source=source)
    return name, sha
