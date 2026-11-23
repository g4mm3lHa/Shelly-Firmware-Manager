"""Offer logic of a check: Gen2+ next steps (server first, stored file of the offered
version, intermediate versions, store fallback, reference request), Gen1 firmware store
first, the manufacturer's server (redirects, limits, offers, SHA-256 of the URL), keep
installed versions and the hold-back of new versions (offer_after_days).

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import datetime

import data_paths
import manager_core
import pytest
import shelly
import shelly_cloud
from conftest import A, B, C, vendor_content
from helpers import make_manager, store
from shelly import ShellyError


# ---------------------------------------------------------------- Gen2+: the server decides, the store serves


async def test_server_decides_and_a_stored_file_of_the_offered_version_is_used(mgr, net):
    """Gen2+: the server is asked per firmware ID and installed version; the stored file
    the offer URL names (same SHA-256) is used instead of a download – newer stored
    versions are not offered."""
    name, _ = store(mgr, "Plus1", "1.5.0", content=vendor_content("1.5.0"))
    store(mgr, "Plus1", "1.7.0")  # newer, but not offered by the server
    assert await mgr.scan(user="u")
    assert net.offer_calls == 2 and net.downloads == 0  # installed 1.4.0 and 1.5.0
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_READY and dev["file"] == name and dev["pending_version"] == "1.5.0"
    assert dev["offer_source"] == "store" and dev["message"] == "Provided from the firmware store, not installed"
    assert mgr.devices[B]["status"] == manager_core.STATUS_CURRENT  # 1.5.0 installed, 1.5.0 offered
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert {f["source"] for f in entry["findings"]} == {"store"} and entry["updates"] == 2


@pytest.mark.parametrize("source", [manager_core.SOURCE_DOWNLOAD, manager_core.SOURCE_IMPORT])
async def test_provided_file_the_offer_does_not_name_is_replaced(mgr, net, source):
    """A device that is ready with a file the offer URL does not name (provided under
    older rules, e.g. an imported foreign file) does not keep it: the next check
    provides the manufacturer's file. An upload of the version stays provided."""
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    foreign, fsha = store(mgr, "Plus1", "1.5.0", source=source, content=b"foreign" * 3000)
    dev.update(file=foreign, sha256=fsha)
    await mgr.check_device(A)
    assert dev["status"] == manager_core.STATUS_READY and dev["file"] != foreign and not mgr.approval_matches(A, fsha)
    upload, usha = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    dev.update(file=upload, sha256=usha)
    await mgr.check_device(A)
    assert dev["file"] == upload and mgr.approval_matches(A, usha)


async def test_uploaded_file_of_the_offered_version_is_used(mgr, net):
    name, sha = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert net.offer_calls == 1 and net.downloads == 0
    assert (dev["file"], dev["sha256"], dev["offer_source"]) == (name, sha, "upload") and "uploaded file" in dev["message"]
    assert mgr.public_state()["devices"][0]["offer_source"] == "upload"


async def test_download_is_preferred_over_an_upload_of_the_same_version(mgr, net):
    """The file the offer URL names comes before an upload of the same version."""
    store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    name, _ = store(mgr, "Plus1", "1.5.0", content=vendor_content("1.5.0"))
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["file"] == name and mgr.devices[A]["offer_source"] == "store"


async def test_upload_of_the_offered_version_is_the_deliberate_exception(mgr, net):
    """Gen2+: the user's own upload of exactly the offered version is provided although
    the offer URL names another SHA-256 – labelled as upload, nothing downloaded."""
    name, _ = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["file"] == name and mgr.devices[A]["offer_source"] == "upload" and net.downloads == 0


@pytest.mark.parametrize("source", [manager_core.SOURCE_DOWNLOAD, manager_core.SOURCE_IMPORT])
async def test_stored_file_with_another_sha_than_the_offer_is_not_used(mgr, net, source):
    """Gen2+: a stored file of the offered version whose SHA-256 differs from the one the
    offer URL names never replaces the manufacturer's file – whatever its source says
    (e.g. an imported file labelled as a download by its creator): it is downloaded."""
    foreign, _ = store(mgr, "Plus1", "1.5.0", source=source, content=b"foreign" * 3000)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert net.downloads == 1 and dev["file"] != foreign and dev["offer_source"] == "manufacturer"
    assert dev["source_url"].endswith(dev["sha256"])


async def test_imported_file_named_by_the_offer_becomes_a_verified_download(mgr, net):
    """An imported file whose SHA-256 the offer URL names is provided and from then on
    counts as a download over HTTPS (confirmed by the update server)."""
    name, _ = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_IMPORT, content=vendor_content("1.5.0"))
    mgr.firmware[name]["https"] = None
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["file"] == name and net.downloads == 0 and mgr.devices[A]["offer_source"] == "store"
    assert mgr.firmware[name]["source"] == manager_core.SOURCE_DOWNLOAD and mgr.firmware[name]["https"] is True


async def test_installed_version_is_sent_and_intermediate_versions_are_marked(mgr, net, monkeypatch):
    """The next step of the server for the installed version is provided; below the
    newest version it is marked as intermediate version (target: the newest)."""
    sent = []
    fake = shelly_cloud.fetch_offer

    async def recording(session, fw_type, pins=None, version=None, beta=False):
        sent.append((fw_type, version))
        return await fake(session, fw_type, pins, version)

    monkeypatch.setattr(shelly_cloud, "fetch_offer", recording)
    net.steps = {"1.4.0": "1.5.0"}
    net.offer_version = "1.7.0"
    await mgr.scan()
    assert ("Plus1", "1.4.0") in sent and ("Plus1", "1.5.0") in sent and ("Plus1", None) in sent
    assert sent.count(("Plus1", "1.4.0")) == 1  # shared by A and C
    a, b = mgr.devices[A], mgr.devices[B]
    assert a["pending_version"] == "1.5.0" and a["step_target"] == "1.7.0"
    assert "intermediate version on the way to 1.7.0" in a["message"]
    assert b["pending_version"] == "1.7.0" and b.get("step_target") is None
    assert net.newest_calls == 1 and mgr.public_state()["devices"][0].get("step_target") in ("1.7.0", None)


async def test_file_provided_as_intermediate_version_keeps_a_store_marker(mgr, net):
    """A file provided as intermediate version is marked in the store for good
    (Firmware tab label); a file provided as the newest version is not."""
    net.steps = {"1.4.0": "1.5.0"}
    net.offer_version = "1.7.0"
    await mgr.scan()
    a, b = mgr.devices[A], mgr.devices[B]
    assert mgr.firmware[a["file"]].get("intermediate") is True
    assert "intermediate" not in mgr.firmware[b["file"]]
    listed = {f["name"]: f["intermediate"] for f in mgr.firmware_list()}
    assert listed == {a["file"]: True, b["file"]: False}
    step_file = a["file"]
    net.steps = {}
    await mgr.check_device(A)  # the next check provides the newest version: marker stays
    assert a["file"] != step_file and mgr.firmware[step_file].get("intermediate") is True


async def test_several_steps_without_newer_version_are_an_intermediate_version(mgr, net, monkeypatch):
    fake = shelly_cloud.fetch_offer

    async def steps(session, fw_type, pins=None, version=None, beta=False):
        offer = await fake(session, fw_type, pins, version)
        return {**offer, "steps": "2"} if version == "1.4.0" else offer

    monkeypatch.setattr(shelly_cloud, "fetch_offer", steps)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["step_target"] == "" and "intermediate version;" in mgr.devices[A]["message"]


async def test_server_error_offers_a_stored_version_for_a_manual_installation(mgr, net):
    """The server cannot be asked: error as before; a newer stored version is offered
    for a manual installation with the risk hint (marker store_fallback)."""
    name, sha = store(mgr, "Plus1", "1.6.0")
    net.offer_error = ShellyError("408, 'Request Timeout'")
    mgr.devices.update({A: {"name": "Kitchen"}, B: {"name": "Hall"}})
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_ERROR and dev["store_fallback"] is True
    assert dev["message"].startswith("408, 'Request Timeout'. Stored version 1.6.0 is ready")
    assert "at your own risk" in dev["message"] and dev["error_level"] == "warning"
    assert (dev["file"], dev["pending_version"], dev["offer_source"]) == (name, "1.6.0", "store")
    assert manager_core.holds_file(dev) and mgr.approval_matches(A, sha)
    await mgr.check_device(B)  # 1.5.0 installed: 1.6.0 is newer as well
    assert mgr.devices[B]["store_fallback"] is True
    net.offer_error = None
    await mgr.check_device(A)  # server answers again: the marker is gone
    assert "store_fallback" not in mgr.devices[A] and mgr.devices[A]["pending_version"] == "1.5.0"


async def test_server_error_without_newer_stored_version_stays_a_plain_error(mgr, net):
    store(mgr, "Plus1", "1.6.0-beta1")  # pre-release: never offered from the store
    store(mgr, "Plus1", "1.7.0", gen=1)  # other generation
    net.offer_error = ShellyError("502, 'Bad Gateway'")
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_ERROR and dev["message"] == "502, 'Bad Gateway'"
    assert "store_fallback" not in dev and not manager_core.holds_file(dev)


async def test_damaged_stored_file_of_the_offered_version_is_downloaded_again(mgr, net):
    bad, _ = store(mgr, "Plus1", "1.5.0", content=b"x" * 70000)
    (data_paths.FW_DIR / bad).write_bytes(b"damaged" * 500)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert net.downloads == 1 and mgr.devices[A]["offer_source"] == "manufacturer"


async def test_stored_file_is_verified_once_per_check(mgr, net, monkeypatch):
    """Twelve devices of one type share one checksum verification of the stored file."""
    for i in range(9):
        net.devices[f"{i:031x}e"] = (f"Dev {i}", f"192.168.1.{50 + i}", "1.4.0", "1.5.0")
    net.offer_version = "1.6.0"
    store(mgr, "Plus1", "1.6.0", content=vendor_content("1.6.0"))
    original, hashed = shelly_cloud.file_sha256, []

    def counting(target_dir, name):
        hashed.append(name)
        return original(target_dir, name)

    monkeypatch.setattr(shelly_cloud, "file_sha256", counting)
    await mgr.scan()
    assert len(hashed) == 1 and net.downloads == 0
    assert all(d["pending_version"] == "1.6.0" for d in mgr.devices.values())
    await mgr.scan()  # already provided and unchanged: nothing to verify or download again
    assert len(hashed) == 1 and net.downloads == 0 and mgr._verifying == {} and mgr._fresh == {}
    mgr.devices[A]["status"] = manager_core.STATUS_CURRENT  # e.g. after a downgrade: provided again
    await mgr.check_device(A)
    assert len(hashed) == 2  # verified again by the next check that uses it


async def test_reference_request_detects_a_server_that_ignores_the_version(mgr, net):
    """One reference request per check; an answer other than the known step is a
    warning finding of the check, no answer is no conclusion."""
    await mgr.scan()
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert net.reference_calls == 1 and all(f["device"] != "Shelly update server" for f in entry["findings"])
    net.reference_answer = "1.7.6"
    await mgr.check_device(A)
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    [warning] = [f for f in entry["findings"] if f["device"] == "Shelly update server"]
    assert warning["severity"] == "warning" and "got 1.7.6" in warning["error"] and entry["severity"] == "warning"
    assert net.reference_calls == 2 and mgr._header_check is None
    net.reference_answer = None  # no offer at all: also a deviation
    await mgr.check_device(A)
    assert "got nothing" in [e for e in mgr.events.entries if e["kind"] == "check"][-1]["findings"][-1]["error"]


async def test_reference_request_failure_is_no_finding(mgr, net, monkeypatch):
    fake = shelly_cloud.fetch_offer

    async def failing_reference(session, fw_type, pins=None, version=None, beta=False):
        if version == "1.0.7":
            raise ShellyError("502, 'Bad Gateway'")
        return await fake(session, fw_type, pins, version)

    monkeypatch.setattr(shelly_cloud, "fetch_offer", failing_reference)
    await mgr.scan()
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert all(f["device"] != "Shelly update server" for f in entry["findings"])


def gen1_net(net, monkeypatch, types):
    """Let the fake devices answer as Gen1 devices of the given types {device ID: type}
    and count the requests for the Gen1 firmware list."""
    calls = []

    async def device_info(_session, origin, auth_for=None, on_identity=None):
        device_id = net.by_origin(origin)
        return {"auth_enabled": False, "gen": 1, "shelly_id": None, "type": types[device_id], "model": types[device_id],
                "mac": "AABBCC" + device_id[-1].upper(), "version": "20230101-000000/v1.13.0-g0000000",
                "build": "20230101-000000/v1.13.0-g0000000"}

    async def fetch_gen1_offers(_session, _pins=None):
        calls.append(1)
        return {"SHSW-1": {"version": "20230913-112003/v1.14.0-gcb84623", "url": "http://fw.example/SHSW-1.zip"},
                "SHSW-25": {"version": "20230913-112003/v1.14.0-gcb84623", "url": "http://fw.example/SHSW-25.zip"}}

    monkeypatch.setattr(shelly, "device_info", device_info)
    monkeypatch.setattr(shelly_cloud, "fetch_gen1_offers", fetch_gen1_offers)
    return calls


async def test_gen1_list_is_requested_once_for_all_types(net, monkeypatch):
    """The manufacturer serves one list for all Gen1 types – one request per
    check, whatever the number of types; every Gen1 type is checked (there is no
    list of allowed types; a stored value of older versions is ignored)."""
    calls = gen1_net(net, monkeypatch, {A: "SHSW-1", B: "SHPLG-S", C: "SHSW-25"})
    m = make_manager(net, gen1_allowed_types=["SHSW-1"])
    m._registry = lambda: asyncio.sleep(0, net.registry())
    await m.scan()
    assert len(calls) == 1
    assert m.devices[A]["status"] == manager_core.STATUS_READY and m.devices[A]["offer_source"] == "manufacturer"
    assert m.devices[B]["status"] == manager_core.STATUS_CURRENT and m.devices[B]["message"] == "No stable offer"
    assert m.devices[C]["status"] == manager_core.STATUS_READY


async def test_gen1_store_first_for_every_type(net, monkeypatch):
    """Gen1 devices of every type use the store first."""
    calls = gen1_net(net, monkeypatch, {A: "SHSW-1", B: "SHSW-1", C: "SHSW-25"})
    m = make_manager(net)
    m._registry = lambda: asyncio.sleep(0, net.registry())
    store(m, "SHSW-1", "20230913-112003/v1.14.0-gcb84623", gen=1)
    store(m, "SHSW-25", "20230913-112003/v1.14.0-gcb84623", gen=1)
    await m.scan()
    assert calls == [] and net.downloads == 0
    assert m.devices[A]["offer_source"] == "store" and m.devices[B]["status"] == manager_core.STATUS_READY
    assert m.devices[C]["offer_source"] == "store" and m.devices[C]["status"] == manager_core.STATUS_READY


async def test_gen1_imported_file_is_not_provided_store_first(net, monkeypatch):
    """Gen1: an imported file is no store first offer – the manufacturer's list is asked
    and its file downloaded, also when the import claims a higher version."""
    calls = gen1_net(net, monkeypatch, {A: "SHSW-1"})
    m = make_manager(net)
    m._registry = lambda: asyncio.sleep(0, net.registry())
    m.devices[A] = {"name": "Hall"}
    store(m, "SHSW-1", "20991231-235959/v9.9.9-gdeadbee", gen=1, source=manager_core.SOURCE_IMPORT)
    await m.check_device(A)
    dev = m.devices[A]
    assert calls == [1] and net.downloads == 1
    assert dev["offer_source"] == "manufacturer" and dev["pending_version"] == "20230913-112003/v1.14.0-gcb84623"


async def test_gen1_imported_file_is_a_store_fallback_while_the_list_is_unreachable(net, monkeypatch):
    """Gen1: while the manufacturer's list cannot be asked, an imported newer version is
    only offered for a manual installation at the user's risk (store fallback)."""
    gen1_net(net, monkeypatch, {A: "SHSW-1"})

    async def unreachable(_session, _pins=None):
        raise ShellyError("502, 'Bad Gateway'")

    monkeypatch.setattr(shelly_cloud, "fetch_gen1_offers", unreachable)
    m = make_manager(net)
    m._registry = lambda: asyncio.sleep(0, net.registry())
    m.devices[A] = {"name": "Hall"}
    name, _ = store(m, "SHSW-1", "20230913-112003/v1.14.0-gcb84623", gen=1, source=manager_core.SOURCE_IMPORT)
    await m.check_device(A)
    dev = m.devices[A]
    assert dev["status"] == manager_core.STATUS_ERROR and dev["store_fallback"] is True
    assert dev["file"] == name and dev["offer_source"] == "import" and net.downloads == 0
    assert "at your own risk" in dev["message"]


async def test_gen2_store_fallback_names_an_imported_file(mgr, net):
    """Gen2+: the store fallback also offers an imported file, with its own source."""
    name, _ = store(mgr, "Plus1", "1.6.0", source=manager_core.SOURCE_IMPORT)
    net.offer_error = ShellyError("502, 'Bad Gateway'")
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert dev["store_fallback"] is True and dev["file"] == name and dev["offer_source"] == "import"


async def test_device_texts_are_literal_text_in_the_notification(mgr, net):
    """Name and versions in the Home Assistant notification (Markdown): control and
    bidi characters removed, Markdown characters escaped – nothing becomes a link."""
    net.devices[A] = ("[Click](https://evil.example)\u202e", "192.168.1.10", "1.4.0\n# Big", "1.5.0")
    await mgr.scan(automatic=True)
    message = mgr.notified[-1][1]
    assert "\\[Click\\]\\(https\\:\\/\\/evil\\.example\\)" in message and "\u202e" not in message
    assert "1\\.4\\.0\\# Big" in message and "\n# Big" not in message


def test_plain_removes_control_and_format_characters():
    """shelly.plain: only printable text, length limited like clip."""
    assert shelly.plain("1.2.3\n\r\x1b[31m\u202e\u2028x", 64) == "1.2.3[31mx"
    assert shelly.plain("a" * 100, 10) == "a" * 10 and shelly.plain(None) == ""


def test_parse_json_refuses_values_nested_too_deeply():
    """A deeply nested value is invalid JSON (ValueError), not a RecursionError."""
    with pytest.raises(ValueError, match="nested too deeply"):
        shelly.parse_json(b"[" * 200000 + b"]" * 200000)
    assert shelly.parse_json('{"a": [1]}') == {"a": [1]}


# ---------------------------------------------------------------- Manufacturer server access


class _FakeBody:
    """Response body that yields its data in chunks (like aiohttp's StreamReader)."""

    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        for i in range(0, len(self.data), size):
            yield self.data[i : i + size]


def fake_cloud(monkeypatch, pages):
    """Replace _CloudRequest: pages maps URL -> (status, headers, body bytes)."""
    seen = []

    class Request:
        headers_seen: list = []

        def __init__(self, _session, url, _pins, _timeout, headers=None):
            seen.append(url)
            Request.headers_seen.append(headers)
            status, headers, body = pages[url]
            self.resp = type("Resp", (), {
                "status": status, "headers": headers, "charset": None, "content": _FakeBody(body),
                "raise_for_status": lambda _self: None if status < 400 else (_ for _ in ()).throw(ValueError(status)),
            })()

        async def __aenter__(self):
            return self.resp

        async def __aexit__(self, *_exc):
            return None

    monkeypatch.setattr(shelly_cloud, "_CloudRequest", Request)
    return seen


async def test_cloud_json_and_text_share_redirect_and_limit_checks(monkeypatch):
    """JSON and text pages follow redirects only within shelly.cloud and keep the size limit."""
    seen = fake_cloud(monkeypatch, {
        "https://updates.shelly.cloud/a": (302, {"Location": "/b"}, b""),
        "https://updates.shelly.cloud/b": (200, {}, b'{"stable": 1}'),
        "https://shelly-api-docs.shelly.cloud/x": (301, {"Location": "https://evil.example/x"}, b""),
        "https://shelly-api-docs.shelly.cloud/big": (200, {}, b"x" * 3000),
    })
    assert await shelly_cloud._cloud_json(None, "https://updates.shelly.cloud/a", None) == {"stable": 1}
    assert seen == ["https://updates.shelly.cloud/a", "https://updates.shelly.cloud/b"]
    with pytest.raises(shelly.ShellyError, match="not allowed"):
        await shelly_cloud.fetch_cloud_text(None, "https://shelly-api-docs.shelly.cloud/x", None, 1024)
    with pytest.raises(shelly.ShellyError, match="larger"):
        await shelly_cloud.fetch_cloud_text(None, "https://shelly-api-docs.shelly.cloud/big", None, 1024)


async def test_offer_with_invalid_step_count_is_a_readable_error(monkeypatch):
    """A non-numeric "nsteps" in the manufacturer's offer is reported as
    ShellyError, not as "invalid literal for int()"."""
    fake_cloud(monkeypatch, {
        "https://updates.shelly.cloud/update/Plus1": (200, {}, b'{"stable": {"url": "u", "version": "1.5.0", "nsteps": "x"}}'),
    })
    with pytest.raises(ShellyError, match="nsteps"):
        await shelly_cloud.fetch_offer(None, "Plus1")


async def test_gen1_list_is_one_document_for_all_types(monkeypatch):
    """The Gen1 list is fetched once; the offer of a type is read from it."""
    seen = fake_cloud(monkeypatch, {"https://api.shelly.cloud/files/firmware": (200, {}, (
        b'{"isok": true, "data": {"SHSW-1": {"url": "http://x/SHSW-1.zip", "version": "v1.14.0"}, "SHEM": {"version": "v1"}}}'))})
    offers = await shelly_cloud.fetch_gen1_offers(None)
    assert seen == ["https://api.shelly.cloud/files/firmware"]
    assert shelly_cloud.gen1_offer(offers, "SHSW-1") == {"version": "v1.14.0", "url": "http://x/SHSW-1.zip"}
    assert shelly_cloud.gen1_offer(offers, "SHEM") is None and shelly_cloud.gen1_offer(offers, "unknown") is None
    fake_cloud(monkeypatch, {"https://api.shelly.cloud/files/firmware": (200, {}, b'{"isok": false}')})
    with pytest.raises(ShellyError, match="reports an error"):
        await shelly_cloud.fetch_gen1_offers(None)


def test_error_text_drops_the_field_labels_and_keeps_the_values():
    """Aiohttp's HTTP error text without "message=" and "url=" – the status,
    the reason and the complete URL stay."""
    raw = "408, message='Request Timeout', url='https://updates.shelly.cloud/update/Mini1G3'"
    assert shelly.error_text(raw) == "408, 'Request Timeout', 'https://updates.shelly.cloud/update/Mini1G3'"
    assert shelly.error_text("Cannot connect to host 192.168.1.5:80 ssl:default") == "Cannot connect to host 192.168.1.5:80 ssl:default"
    assert shelly.error_text("https://x.example/?url=1&message=2") == "https://x.example/?url=1&message=2"  # no label
    assert shelly.error_text("x" * 50, 10) == "x" * 10 and shelly.error_text(None) == ""


async def test_check_error_message_uses_the_readable_text(mgr, net, monkeypatch):
    async def failing(*_args, **_kwargs):
        raise ShellyError("408, message='Request Timeout', url='https://updates.shelly.cloud/update/Plus1'")

    monkeypatch.setattr(shelly_cloud, "fetch_offer", failing)
    await mgr.scan()
    assert mgr.devices[A]["message"] == "408, 'Request Timeout', 'https://updates.shelly.cloud/update/Plus1'"


async def test_offer_request_sends_only_app_and_version(monkeypatch):
    """The installed version goes in the header Shelly-Info – only "app" and "ver";
    without version no header (newest version). nsteps is returned as steps."""
    body = b'{"stable": {"url": "u", "version": "1.3.3", "nsteps": 2, "build_id": "20240625-123456/1.3.3-gabc"}}'
    fake_cloud(monkeypatch, {"https://updates.shelly.cloud/update/Plus1": (200, {}, body)})
    headers = shelly_cloud._CloudRequest.headers_seen
    headers.clear()
    expected = {"version": "1.3.3", "url": "u", "steps": "2", "build": "20240625-123456/1.3.3-gabc"}
    assert await shelly_cloud.fetch_offer(None, "Plus1", None, "1.0.7") == expected
    assert await shelly_cloud.fetch_offer(None, "Plus1") == expected
    assert headers == [{"Shelly-Info": '{"app":"Plus1","ver":"1.0.7"}'}, None]


def test_sha256_named_by_the_firmware_url():
    sha = "ddd5a7b49ff3e65240d1264eb531f82da2aa86d3d05d045c5226a81e7ea2e43d"
    assert shelly_cloud.url_sha256(f"https://fwcdn.shelly.cloud/gen2-ntest/Plus1/{sha}") == sha
    assert shelly_cloud.url_sha256(f"https://fwcdn.shelly.cloud/x/{sha.upper()}") == sha
    assert shelly_cloud.url_sha256("https://fw.example/Plus1.zip") is None
    assert shelly_cloud.url_sha256(f"https://fwcdn.shelly.cloud/{sha}/Plus1.zip") is None


async def test_download_must_match_the_sha256_of_its_url(monkeypatch, tmp_path):
    """A download whose checksum differs from the SHA-256 in the URL is not stored."""
    import hashlib

    data = b"f" * 5000
    good = hashlib.sha256(data).hexdigest()
    bad = "0" * 64
    fake_cloud(monkeypatch, {
        f"https://fwcdn.shelly.cloud/Plus1/{good}": (200, {}, data),
        f"https://fwcdn.shelly.cloud/Plus1/{bad}": (200, {}, data),
    })
    name, sha, size, https = await shelly_cloud.download_firmware(None, f"https://fwcdn.shelly.cloud/Plus1/{good}", tmp_path)
    assert sha == good and size == 5000 and https and (tmp_path / name).is_file()
    with pytest.raises(ShellyError, match="does not match the SHA-256"):
        await shelly_cloud.download_firmware(None, f"https://fwcdn.shelly.cloud/Plus1/{bad}", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]  # nothing else stored, no temp file


# ---- Keep installed versions, offer only after N days, Shellys not listed


async def test_keep_installed_stores_the_version_a_device_runs(mgr, net):
    """Option keep_installed: an up-to-date Gen2+ device gets its installed version
    stored while the server offers it (way back); nothing is provided to the device.
    Off: nothing is downloaded for an up-to-date device."""
    net.devices = {B: net.devices[B]}  # Hall runs 1.5.0, the offered version
    await mgr.scan()
    assert net.downloads == 0 and not mgr.firmware
    mgr.keep_installed = True
    await mgr.scan()
    dev = mgr.devices[B]
    assert net.downloads == 1 and dev["status"] == manager_core.STATUS_CURRENT and not dev.get("file")
    [meta] = mgr.firmware.values()
    assert (meta["type"], meta["version"], meta["source"]) == ("Plus1", "1.5.0", "download")
    assert mgr.public_state()["devices"][0]["way_back"] == {"version": "1.5.0", "stored": True}
    await mgr.scan()
    assert net.downloads == 1  # stored file named by the offer: only verified


async def test_keep_installed_without_sha_in_the_url_keeps_a_stored_download(mgr, net, monkeypatch):
    """An offer URL without SHA-256: a stored download of the installed version is kept,
    not loaded again at every check."""
    net.devices = {B: net.devices[B]}
    mgr.keep_installed = True
    fetch = shelly_cloud.fetch_offer

    async def without_sha(*args, **kwargs):
        offer = await fetch(*args, **kwargs)
        if offer:
            offer["url"] = offer["url"].rsplit("/", 1)[0] + "/Plus1.zip"
        return offer

    monkeypatch.setattr(shelly_cloud, "fetch_offer", without_sha)
    await mgr.scan()
    assert net.downloads == 1 and len(mgr.firmware) == 1
    await mgr.scan()
    assert net.downloads == 1 and len(mgr.firmware) == 1


async def test_keep_installed_failure_does_not_change_the_check(mgr, net, monkeypatch):
    """A failed download of the installed version is logged only."""
    net.devices = {B: net.devices[B]}
    mgr.keep_installed = True

    async def broken(*_args, **_kwargs):
        raise ShellyError("Download failed")

    monkeypatch.setattr(shelly_cloud, "download_firmware", broken)
    await mgr.scan()
    assert mgr.devices[B]["status"] == manager_core.STATUS_CURRENT and not mgr.firmware
    assert mgr.public_state()["devices"][0]["way_back"] == {"version": "1.5.0", "stored": False}


def test_build_date_from_build_id_or_gen1_version():
    """The build date starts a Gen2+ build ID and a Gen1 version text."""
    assert shelly.build_date("20241011-114455/1.4.4-g6d2a586") == "2024-10-11"
    assert shelly.build_date("20230913-112003/v1.14.0-gcb84623") == "2023-09-13"
    assert shelly.build_date("20231399-112003/v1.14.0") is None
    assert shelly.build_date("1.4.4") is None and shelly.build_date(None) is None


def built_days_ago(days):
    """Build ID of a version built that many days ago (local date)."""
    day = datetime.date.today() - datetime.timedelta(days=days)
    return f"{day:%Y%m%d}-101500/1.5.0-gabc", day.isoformat()


async def test_young_version_is_held_back_until_it_is_old_enough(mgr, net):
    """offer_after_days: a version younger than N days (build date) is not offered:
    the device stays up to date with the marker hold, nothing is downloaded or
    notified. "Offer update now" lifts the hold for this version on this device."""
    build, day = built_days_ago(2)
    net.builds["1.5.0"] = build
    mgr.offer_after_days = 7
    await mgr.scan(automatic=True)
    dev = mgr.devices[A]
    until = (datetime.date.fromisoformat(day) + datetime.timedelta(days=7)).isoformat()
    assert dev["status"] == manager_core.STATUS_CURRENT and dev["hold"] == {"version": "1.5.0", "built": day, "until": until}
    assert net.downloads == 0 and not mgr.notified and not dev.get("offer_version")
    assert mgr.release_hold(A, "alice") == "1.5.0"
    entry = mgr.events.entries[-1]
    assert (entry["kind"], entry["action"], entry["version"], entry["user"]) == ("device", "offered_now", "1.5.0", "alice")
    await mgr.check_device(A)
    assert dev["status"] == manager_core.STATUS_READY and "hold" not in dev and dev["offer_date"] == day
    await mgr.scan()
    assert mgr.devices[A]["status"] == manager_core.STATUS_READY and mgr.devices[C].get("hold")
    with pytest.raises(ValueError):
        mgr.release_hold(B, "alice")  # nothing held back


async def test_old_enough_or_undated_version_is_offered_at_once(mgr, net):
    """A version at least N days old, or one without a build date, is offered."""
    mgr.offer_after_days = 7
    net.builds["1.5.0"] = built_days_ago(7)[0]
    await mgr.scan()
    assert mgr.devices[A]["status"] == manager_core.STATUS_READY
    net.builds["1.5.0"] = ""
    await mgr.check_device(C)
    assert mgr.devices[C]["status"] == manager_core.STATUS_READY and mgr.devices[C]["offer_date"] is None


async def test_gen2_upload_of_the_offered_version_is_never_held_back(mgr, net):
    """Gen2+ as Gen1: the user's own upload of exactly the offered version is provided
    at once, a held-back offer without such an upload is not."""
    net.builds["1.5.0"] = built_days_ago(2)[0]
    mgr.offer_after_days = 30
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["hold"]["version"] == "1.5.0"
    name, _ = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_READY and "hold" not in dev and dev["file"] == name
    assert net.downloads == 0


async def test_gen1_upload_is_never_held_back(net, monkeypatch):
    """Gen1 store first: a stored download is held back like an offer, the user's
    own upload is not."""
    gen1_net(net, monkeypatch, {A: "SHSW-1"})
    m = make_manager(net)
    m._registry = lambda: asyncio.sleep(0, net.registry())
    m.devices[A] = {"name": "Hall"}
    m.offer_after_days = 30
    young = f"{datetime.date.today():%Y%m%d}-101500/v1.14.0-gcb84623"
    name, _ = store(m, "SHSW-1", young, gen=1)
    await m.check_device(A)
    assert m.devices[A]["hold"]["version"] == young and m.devices[A]["status"] == manager_core.STATUS_CURRENT
    m.firmware[name]["source"] = manager_core.SOURCE_UPLOAD
    await m.check_device(A)
    assert m.devices[A]["status"] == manager_core.STATUS_READY and "hold" not in m.devices[A]


async def test_gen2_store_fallback_of_an_imported_file_names_its_origin(mgr, net):
    """The device message of a store fallback with an imported file says it is not
    confirmed by Shelly."""
    store(mgr, "Plus1", "1.6.0", source=manager_core.SOURCE_IMPORT)
    net.offer_error = ShellyError("502, 'Bad Gateway'")
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert "1.6.0 (imported file, not confirmed by Shelly)" in mgr.devices[A]["message"]
