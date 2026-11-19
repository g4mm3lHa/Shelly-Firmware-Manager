"""App options: bounds, unknown keys, optional download_base_url and its confirmation.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import json
import re
from pathlib import Path

import aiohttp
import app_options
import data_paths
import manager
import manager_core
import pytest
import shelly
from conftest import OPTIONS, A
from helpers import make_manager, web_client, without_url

ROOT = Path(__file__).resolve().parent.parent  # repository root
APP_DIR = ROOT / "shelly_firmware_manager"


def test_schedule_option_bounds():
    """Interval 1–30 days, time HH:MM, log limit 100–3000."""
    for key, value in (("check_interval_days", 0), ("check_interval_days", 31), ("check_time", "24:00"),
                       ("check_time", "2:45"), ("log_entry_limit", 99), ("log_entry_limit", 3001)):
        with pytest.raises(ValueError):
            app_options.merge(OPTIONS, {key: value})
    new, changed = app_options.merge(OPTIONS, {"check_interval_days": 30, "check_time": "23:59", "log_entry_limit": 3000})
    assert changed == ["check_interval_days", "check_time", "log_entry_limit"]


def test_import_size_option():
    """Max. import size: 1–2048 MB, default 100 MB; used in bytes by the manager."""
    for value in (0, 2049, "100"):
        with pytest.raises(ValueError):
            app_options.merge(OPTIONS, {"import_max_mb": value})
    new, changed = app_options.merge(OPTIONS, {"import_max_mb": 2048})
    assert changed == ["import_max_mb"] and new["import_max_mb"] == 2048
    assert app_options.DEFAULTS["import_max_mb"] == 100
    assert manager.Manager({**OPTIONS, "import_max_mb": 5}, session=None, token="t").import_max == 5 * 1048576
    assert manager.Manager(dict(OPTIONS), session=None, token="t").import_max == 100 * 1048576


def test_removed_options_never_propagate():
    """Legacy keys (shared password, hourly interval, old log limit) are dropped."""
    legacy = {**OPTIONS, "shelly_password": "secret!", "check_interval_hours": 24, "log_max_entries": 20000}
    view = app_options.public_view(legacy)
    assert "shelly_password" not in view and "check_interval_hours" not in view and "log_max_entries" not in view
    assert "secret!" not in json.dumps(view)
    new, _ = app_options.merge(legacy, {"check_time": "03:00"})
    assert "shelly_password" not in new


def test_allow_downgrade_is_an_optional_switch():
    """allow_downgrade: no default (missing = off), only true/false accepted."""
    without = {k: v for k, v in OPTIONS.items() if k != "allow_downgrade"}
    assert "allow_downgrade" not in app_options.public_view(without)
    with pytest.raises(ValueError):
        app_options.merge(without, {"allow_downgrade": "yes"})
    new, changed = app_options.merge(without, {"allow_downgrade": True})
    assert new["allow_downgrade"] is True and changed == ["allow_downgrade"]


LEGACY = app_options.LEGACY_DOWNLOAD_BASE


async def test_missing_url_blocks_installations(net):
    m = manager.Manager(without_url(), session=None, token="t")
    assert m.download_base is None and m.download_base_issue == "missing"
    m.devices[A] = {"status": manager_core.STATUS_READY, "sha256": "a" * 64, "gen": 2}
    assert await m.install(A, "a" * 64, "u") is False
    state = m.public_state()["download_base"]
    assert state["issue"] == "missing" and "Settings tab" in state["message"]


async def test_legacy_value_needs_confirmation_which_persists(net):
    m = make_manager(net, download_base_url=LEGACY)
    assert m.download_base_issue == "unconfirmed"
    with pytest.raises(shelly.ShellyError):
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
    assert json.loads(data_paths.STATE_FILE.read_text())["download_base_confirmed"] is None


def test_empty_url_unsets_the_option():
    new, changed = app_options.merge({**OPTIONS}, {"download_base_url": ""})
    assert changed == ["download_base_url"] and "download_base_url" not in new
    new, changed = app_options.merge(without_url(), {"download_base_url": ""})
    assert changed == [] and "download_base_url" not in new
    with pytest.raises(ValueError):
        app_options.merge(OPTIONS, {"check_time": ""})  # only optional options can be emptied


async def test_web_install_refused_and_confirm_endpoint(net):
    m = make_manager(net, download_base_url=LEGACY)
    m.devices[A] = {"status": manager_core.STATUS_READY, "sha256": "a" * 64, "gen": 2, "name": "K"}
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


class _InfoResponse:
    """Answer of the fake Supervisor to GET /addons/self/info."""

    def __init__(self, body, status=200):
        self.body, self.status = body, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientError(f"status {self.status}")

    async def json(self, content_type=None):
        return self.body


class _InfoSession:
    """Session that only answers GET /addons/self/info (counts the requests)."""

    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.answers.pop(0)


async def test_app_page_link(net):
    """Changelog link: the app page from the Supervisor slug, read once; the list of
    apps while the slug is not available (error or invalid slug), then asked again."""
    m = make_manager(net)
    m.session = _InfoSession(_InfoResponse({}, status=403), _InfoResponse({"data": {"slug": "../x"}}),
                             _InfoResponse({"result": "ok", "data": {"slug": "a0d7b954_shelly_firmware_manager"}}))
    assert await m.app_page() == "/config/apps"
    assert await m.app_page() == "/config/apps"
    assert await m.app_page() == "/config/app/a0d7b954_shelly_firmware_manager/info"
    assert await m.app_page() == "/config/app/a0d7b954_shelly_firmware_manager/info"
    assert m.session.urls == ["http://supervisor/addons/self/info"] * 3
    client = await web_client(m)
    try:
        r = await client.get("/api/settings/app-page")
        assert (await r.json()) == {"url": "/config/app/a0d7b954_shelly_firmware_manager/info"}
    finally:
        await client.close()


def _translations():
    """{key: (name, description)} of translations/en.yaml in file order (small parser:
    two-space keys, name/description, folded ">-" texts)."""
    out, key, field = {}, None, None
    for line in (APP_DIR / "translations" / "en.yaml").read_text().splitlines():
        if line.startswith("network:"):
            break
        if re.match(r"^  \w+:$", line):
            key = line.strip()[:-1]
            out[key] = {"name": "", "description": ""}
        elif m := re.match(r"^    (name|description): ?(.*)$", line):
            field = m.group(1)
            out[key][field] = "" if m.group(2) == ">-" else m.group(2)
        elif line.startswith("      ") and key:
            out[key][field] = (out[key][field] + " " + line.strip()).strip()
    return {k: (v["name"], v["description"]) for k, v in out.items()}


def test_app_configuration_matches_settings_tab():
    """Configuration tab of the app page = Settings tab: same options in the same order
    (schema and translations), same names and help texts. Allowed differences: the
    Gen1 type list (list editor in HA) and the changelog hint after the help text of
    the last option with a visible help text (HA shows none for the certificate list)."""
    js = (ROOT / "ui-src" / "js" / "app-settings.js").read_text()
    fields = re.findall(r'^  \["(\w+)", "([^"]+)", "\w+", "([^"]*)", ', js, re.M)
    config = (APP_DIR / "config.yaml").read_text()
    schema = re.findall(r"^  (\w+):", config[config.index("\nschema:"):], re.M)
    texts = _translations()
    assert [f[0] for f in fields] == schema == list(texts) and set(schema) == app_options.KEYS
    for key, name, desc in fields:
        tname, tdesc = texts[key]
        assert tname == name, key
        if key == "gen1_allowed_types":
            assert tdesc == "One type per entry, e.g. SHSW-1, SHSW-PM"
        elif key == "max_parallel_installs":
            assert tdesc.startswith(desc + ". ") and "Changelog" in tdesc
        else:
            assert tdesc == desc, key
