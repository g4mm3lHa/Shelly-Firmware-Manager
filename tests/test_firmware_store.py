"""Firmware store: no automatic deletion, manual delete, downgrade of Gen2+ devices,
manual upload (stage + confirm).

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import hashlib

import data_paths
import manager
import manager_core
import manager_store
import pytest
import shelly
from conftest import OPTIONS, A, B, C
from helpers import chunks, firmware_zip, store
from shelly import ShellyError


async def test_no_automatic_deletion_of_stored_firmware(mgr, net):
    """Any number of versions stays stored after checks and installations."""
    await mgr.scan()
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.3.1", "1.4.0"):
        store(mgr, "Plus1", version)
    store(mgr, "SHSW-1", "1.13.0", gen=1)
    before = set(mgr.firmware)
    assert len(before) == 8
    mgr.cleanup_firmware()
    await mgr.scan()
    await mgr.check_device(A)
    assert set(mgr.firmware) == before
    assert all((data_paths.FW_DIR / n).is_file() for n in before)
    assert not hasattr(manager_store, "KEEP_PER_TYPE")


async def test_housekeeping_keeps_temporary_files_and_drops_orphans(mgr, net):
    """Unknown files and entries without file are cleaned, temporary files are not
    (they belong to a running download/upload); leftovers are removed at start."""
    name, _ = store(mgr, "Plus1", "1.3.0")
    gone, _ = store(mgr, "Plus1", "1.2.0")
    (data_paths.FW_DIR / gone).unlink()
    (data_paths.FW_DIR / ("f" * 64 + ".zip")).write_bytes(b"x" * 2000)  # not in the store
    part = data_paths.FW_DIR / (manager_store.UPLOAD_PREFIX + "abc")
    part.write_bytes(b"x")
    mgr.cleanup_firmware()
    assert name in mgr.firmware and gone not in mgr.firmware
    assert not (data_paths.FW_DIR / ("f" * 64 + ".zip")).exists()
    assert part.exists()
    manager.Manager(dict(OPTIONS), session=None, token="t")  # restart
    assert not part.exists()


async def test_total_size_and_sources_in_the_list(mgr, net):
    """Total store size; upload entries are marked and have no https flag."""
    store(mgr, "Plus1", "1.3.0")
    store(mgr, "Plus1", "1.2.0", source=manager_core.SOURCE_UPLOAD)
    assert mgr.store_size() == sum(m["size"] for m in mgr.firmware.values())
    by_version = {f["version"]: f for f in mgr.firmware_list()}
    assert by_version["1.3.0"]["source"] == "download" and by_version["1.3.0"]["https"] is True
    assert by_version["1.2.0"]["source"] == "upload" and by_version["1.2.0"]["https"] is None


async def test_free_space_of_the_store_volume(mgr, net, monkeypatch):
    """Free disk space for the Firmware tab; an unreadable volume gives None."""
    assert mgr.free_space() == mgr._free_space() > 0

    def unreadable(_self):
        raise OSError("no volume")
    monkeypatch.setattr(type(mgr), "_free_space", unreadable)
    assert mgr.free_space() is None


async def test_entries_before_rc7_count_as_downloads(mgr, net):
    name, _ = store(mgr, "Plus1", "1.3.0")
    del mgr.firmware[name]["source"]
    assert mgr.firmware_list()[0]["source"] == "download"


async def test_delete_locked_only_while_installing(mgr, net):
    await mgr.scan()
    provided = mgr.devices[A]["file"]
    mgr.devices[A]["status"] = manager_core.STATUS_INSTALLING
    entry = next(f for f in mgr.firmware_list() if f["name"] == provided)
    assert not entry["deletable"] and "installed" in entry["locked"]
    with pytest.raises(shelly.ShellyError):
        mgr.delete_firmware(provided, confirm=True, acknowledged=True)
    assert provided in mgr.firmware


async def test_deleting_a_provided_file_releases_its_devices(mgr, net):
    """A file only provided to devices can be deleted: they lose the provision (status
    "Available", markers gone), the log entry names them, the next check decides
    again."""
    await mgr.scan()
    provided = mgr.devices[A]["file"]
    mgr.devices[A].update(status=manager_core.STATUS_ERROR, install_failed=True, step_target="1.7.0")
    entry = next(f for f in mgr.firmware_list() if f["name"] == provided)
    assert entry["deletable"] and entry["locked"] is None and {d["name"] for d in entry["devices"]} == {"Kitchen", "Garage"}
    mgr.delete_firmware(provided, user="u", confirm=True, acknowledged=True)
    assert provided not in mgr.firmware and not (data_paths.FW_DIR / provided).exists()
    for dev in (mgr.devices[A], mgr.devices[C]):
        assert dev["status"] == manager_core.STATUS_AVAILABLE and dev["file"] is None and dev["sha256"] is None
        assert "install_failed" not in dev and "step_target" not in dev and not manager_core.holds_file(dev)
        assert "check the device again" in dev["message"]
    assert not mgr.approval_matches(A, entry["sha256"])
    logged = mgr.events.entries[-1]
    assert logged["kind"] == "delete" and logged["released"] == ["Garage", "Kitchen"]
    await mgr.check_device(A)  # the next check provides the offer again (downloaded)
    assert mgr.devices[A]["status"] == manager_core.STATUS_READY and net.downloads == 2


async def test_delete_needs_second_confirmation(mgr, net):
    name, _ = store(mgr, "Plus1", "1.3.0")
    for confirm, ack in ((False, False), (True, False), (False, True)):
        with pytest.raises(ValueError):
            mgr.delete_firmware(name, user="u", confirm=confirm, acknowledged=ack)
    assert name in mgr.firmware
    mgr.delete_firmware(name, user="u", confirm=True, acknowledged=True)
    assert name not in mgr.firmware and not (data_paths.FW_DIR / name).exists()
    entry = mgr.events.entries[-1]
    assert entry["kind"] == "delete" and entry["source"] == "download" and entry["user"] == "u"


async def test_downgrade_lists_all_older_stable_versions(mgr, net):
    """All stored older stable versions of the firmware ID, newest first;
    neither the installed nor newer versions, no pre-releases, no other types."""
    await mgr.scan()  # B has 1.5.0 installed, 1.5.0 is stored
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.6.0", "1.4.1-beta1"):
        store(mgr, "Plus1", version)
    store(mgr, "Plus2PM", "1.2.0")
    store(mgr, "Plus1", "0.9.0", gen=1)  # wrong generation
    versions = [v["version"] for v in mgr.downgrade_versions(B)]
    assert versions == ["1.4.0", "1.3.0", "1.2.0", "1.1.0", "1.0.0"]


async def test_downgrade_prefers_download_over_upload_of_the_same_version(mgr, net):
    await mgr.scan()
    store(mgr, "Plus1", "1.3.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    name, _ = store(mgr, "Plus1", "1.3.0", content=b"d" * 3000)
    entries = mgr.downgrade_versions(B)
    assert [(e["version"], e["name"], e["source"]) for e in entries] == [("1.3.0", name, "download")]


async def test_downgrade_also_for_up_to_date_and_ignored_devices(mgr, net):
    """Up-to-date and ignored devices can be downgraded."""
    await mgr.scan()
    store(mgr, "Plus1", "1.4.0")
    mgr.set_ignored(B, True)
    assert mgr.devices[B]["status"] == manager_core.STATUS_CURRENT
    assert mgr.downgrade_versions(B) and mgr.downgrade_blocker(B) is None


async def test_no_downgrade_for_gen1(mgr, net):
    """Gen1 never offers a downgrade."""
    await mgr.scan()
    store(mgr, "SHSW-1", "1.13.0", gen=1)
    mgr.devices[A].update(gen=1, type="SHSW-1", version="20230913-112003/v1.14.0-gcb84623")
    assert mgr.downgrade_versions(A) == []
    assert "Gen1" in mgr.downgrade_blocker(A)


async def test_downgrade_refused_while_the_option_is_off(mgr, net, quick_install):
    """allow_downgrade off (missing option = off): blocked with the Settings hint and
    refused by install itself; the list of stored versions stays readable."""
    await mgr.scan()
    _name, sha = store(mgr, "Plus1", "1.4.0")
    mgr.allow_downgrade = False
    assert mgr.downgrade_blocker(B) == manager_store.DOWNGRADE_OFF
    assert [v["version"] for v in mgr.downgrade_versions(B)] == ["1.4.0"]
    assert await mgr.install(B, sha, approved_by="u", downgrade=True) is False
    assert mgr.devices[B]["version"] == "1.5.0"
    off = manager.Manager({k: v for k, v in OPTIONS.items() if k != "allow_downgrade"}, session=None, token="t")
    assert off.allow_downgrade is False and off.public_state()["allow_downgrade"] is False


async def test_downgrade_blocked_during_check_and_without_download_url(mgr, net):
    await mgr.scan()
    store(mgr, "Plus1", "1.4.0")
    mgr.reserve_check("all")
    assert "Check" in mgr.downgrade_blocker(B)
    mgr.progress = None
    mgr.download_base = None
    assert "download base URL" in mgr.downgrade_blocker(B)


async def test_downgrade_installs_the_selected_version(mgr, net, monkeypatch, quick_install):
    """End to end: same install path, target = stored older version; the device's
    newer offer is shown as available again; logged as downgrade."""
    await mgr.scan()  # A: 1.4.0 with 1.5.0 provided (ready)
    name, sha = store(mgr, "Plus1", "1.3.0")
    served = []

    async def trigger(_session, _gen, origin, url, _auth=None):
        assert url.endswith("/" + name)
        served.append(mgr.may_download(name, "192.168.1.10"))  # Shelly fetches the file
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.3.0", "1.5.0")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    assert await mgr.install(A, sha, "alice", downgrade=True) is True
    dev = mgr.devices[A]
    assert served == [True] and dev["version"] == "1.3.0"
    assert dev["status"] == manager_core.STATUS_AVAILABLE and "a newer version is provided again" in dev["message"]
    assert "downgrade" not in dev and mgr.notified[-1][0] == "Shelly downgraded: Kitchen"
    events = [e for e in mgr.events.entries if e["kind"] == "install"]
    assert [e["event"] for e in events] == ["started", "confirmed"]
    assert all(e["downgrade"] and e["old"] == "1.4.0" and e["new"] == "1.3.0" for e in events)
    assert name in mgr.firmware  # nothing deleted afterwards
    await mgr.check_device(A)  # the newer version is provided again from the store
    assert mgr.devices[A]["status"] == manager_core.STATUS_READY and net.downloads == 1


async def test_downgrade_refused_for_unknown_or_newer_file(mgr, net):
    await mgr.scan()
    _, newer = store(mgr, "Plus1", "1.6.0")
    assert await mgr.install(B, "0" * 64, "u", downgrade=True) is False
    assert await mgr.install(B, newer, "u", downgrade=True) is False
    assert mgr.installing == set() and mgr.devices[B]["status"] == manager_core.STATUS_CURRENT


async def test_failed_downgrade_is_an_error_and_keeps_the_files(mgr, net, monkeypatch, quick_install):
    await mgr.scan()
    name, sha = store(mgr, "Plus1", "1.3.0")

    async def refuse(*_args, **_kwargs):
        raise shelly.ShellyError("Shelly refused the update")

    monkeypatch.setattr(shelly, "trigger_update", refuse)
    assert await mgr.install(B, sha, "u", downgrade=True) is False
    dev = mgr.devices[B]
    assert dev["status"] == manager_core.STATUS_ERROR and "refused" in dev["message"]
    assert "downgrade" not in dev and name in mgr.firmware
    assert mgr.events.entries[-1]["event"] == "failed" and mgr.events.entries[-1]["downgrade"]


async def test_restart_during_downgrade_is_unconfirmed(mgr, net):
    await mgr.scan()
    mgr.devices[B].update(status=manager_core.STATUS_INSTALLING, downgrade=True)
    mgr.save()
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.devices[B]["status"] == manager_core.STATUS_UNCLEAR and "downgrade" not in m.devices[B]


async def test_upload_prefills_from_manifest_and_registers(mgr, net):
    """Type/version pre-filled from manifest.json, confirmed by the user; a
    matching SHA-256 is recorded as checked."""
    await mgr.scan()
    data = firmware_zip({"name": "Plus1", "platform": "esp32", "build_id": "20240101-000000/1.3.0-gabc"})
    staged = await mgr.stage_upload(chunks(data), "plus1-1.3.0.zip", len(data), "alice")
    assert staged["detected"] == {"type": "Plus1", "version": "1.3.0"} and staged["types"] == ["Plus1"]
    sha = hashlib.sha256(data).hexdigest()
    assert staged["sha256"] == sha and staged["size"] == len(data)
    entry = mgr.confirm_upload(staged["token"], "Plus1", "1.3.0", sha.upper(), "alice")
    assert entry["source"] == "upload" and entry["sha_checked"] is True and entry["gen"] == 2
    assert (data_paths.FW_DIR / entry["name"]).read_bytes() == data
    assert [v["version"] for v in mgr.downgrade_versions(B)] == ["1.3.0"]
    log = mgr.events.entries[-1]
    assert log["kind"] == "upload" and log["user"] == "alice" and log["sha_checked"] is True
    assert not list(data_paths.FW_DIR.glob(manager_store.UPLOAD_PREFIX + "*"))


async def test_upload_without_manifest_or_sha(mgr, net):
    await mgr.scan()
    data = firmware_zip()
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    assert staged["detected"] == {"type": None, "version": None}
    entry = mgr.confirm_upload(staged["token"], "Plus1", "1.2.0", "")
    assert entry["sha_checked"] is False


async def test_upload_sha_mismatch_keeps_the_upload_for_a_correction(mgr, net):
    await mgr.scan()
    data = firmware_zip()
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    with pytest.raises(shelly.ShellyError, match="does not match"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.2.0", "a" * 64)
    with pytest.raises(ValueError):
        mgr.confirm_upload(staged["token"], "Plus1", "1.2.0", "xyz")
    mgr.confirm_upload(staged["token"], "Plus1", "1.2.0", hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize(
    ("fw_type", "version"),
    [("Plus2PM", "1.2.0"), ("SHSW-1", "1.2.0"), ("Plus1", "1.2.0-beta1"), ("Plus1", "1.2"), ("Plus1", None)],
)
async def test_upload_refuses_unknown_type_or_unstable_version(mgr, net, fw_type, version):
    await mgr.scan()
    data = firmware_zip()
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    with pytest.raises(ValueError):
        mgr.confirm_upload(staged["token"], fw_type, version)
    assert mgr.firmware_list()[0]["source"] == "download"  # nothing registered


async def test_upload_refuses_duplicates(mgr, net):
    await mgr.scan()
    stored = next(iter(mgr.firmware))
    data = (data_paths.FW_DIR / stored).read_bytes()
    staged = await mgr.stage_upload(chunks(data), "same.bin", len(data))  # any extension
    assert staged["duplicate"] == "Plus1 1.5.0"
    with pytest.raises(shelly.ShellyError, match="already stored"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.4.0")
    data2 = firmware_zip()
    staged = await mgr.stage_upload(chunks(data2), "other.zip", len(data2))
    with pytest.raises(shelly.ShellyError, match="already stored"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.5.0")


async def test_upload_checks_extension_size_and_zip_format(mgr, net):
    with pytest.raises(ValueError, match="zip"):
        await mgr.stage_upload(chunks(b"x" * 5000), "fw.exe", 5000)
    with pytest.raises(ValueError, match="small"):
        await mgr.stage_upload(chunks(b"x" * 10), "fw.bin", 10)
    with pytest.raises(ValueError, match="64 MB"):
        await mgr.stage_upload(chunks(b""), "fw.bin", shelly.MAX_FW_SIZE + 1)
    with pytest.raises(ValueError, match="ZIP"):
        await mgr.stage_upload(chunks(b"x" * 5000), "fw.zip", 5000)
    with pytest.raises(ValueError, match="larger than announced"):
        await mgr.stage_upload(chunks(b"x" * 6000), "fw.bin", 5000)
    with pytest.raises(ValueError, match="incomplete"):
        await mgr.stage_upload(chunks(b"x" * 4000), "fw.bin", 5000)
    assert not list(data_paths.FW_DIR.glob(manager_core.PART_PREFIX + "*"))


async def test_upload_token_expiry_and_cancel(mgr, net, monkeypatch):
    await mgr.scan()
    data = firmware_zip()
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    with pytest.raises(shelly.ShellyError):
        mgr.confirm_upload("0" * 32, "Plus1", "1.2.0")
    mgr.cancel_upload(staged["token"])
    assert mgr._upload is None and not list(data_paths.FW_DIR.glob(manager_core.PART_PREFIX + "*"))
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    mgr._upload["created"] -= manager_store.UPLOAD_TTL_S + 1
    with pytest.raises(shelly.ShellyError, match="upload the file again"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.2.0")


async def test_uploaded_file_is_provided_as_update(mgr, net):
    """An uploaded file is provided when the server offers exactly its version (Gen2+:
    the server decides) – marked as upload, nothing is downloaded."""
    await mgr.scan()
    for name in list(mgr.firmware):
        (data_paths.FW_DIR / name).unlink()
    mgr.firmware.clear()
    upload, _ = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    downloads = net.downloads
    await mgr.check_device(A)
    assert net.downloads == downloads
    assert mgr.devices[A]["file"] == upload and mgr.devices[A]["offer_source"] == "upload"
    listed = next(f for f in mgr.firmware_list() if f["name"] == upload)
    assert listed["devices"] == [{"id": A, "name": "Kitchen"}] and listed["deletable"]


async def test_stored_changelog_is_available_for_the_firmware_tab(mgr, net):
    """The list flags a stored changelog; the text comes from the store
    entry only (one version, entries marked) – nothing is requested for it."""
    await mgr.scan()
    name = mgr.devices[A]["file"]
    other, _ = store(mgr, "Plus1", "1.3.0")
    mgr.firmware[name]["changelog"] = {"date": "2026-10-01", "entries": [
        {"category": "Security", "text": "Fix", "models": []}, {"category": None, "text": "For Plus 1", "models": ["ShellyPlus1"]}]}
    flags = {f["name"]: f["changelog"] for f in mgr.firmware_list()}
    assert flags == {name: True, other: False}
    [version] = mgr.firmware_changelog(name)
    assert (version["version"], version["date"]) == ("1.5.0", "2026-10-01")
    assert version["entries"][0]["security"] and version["entries"][1]["this_model"]
    assert mgr.firmware_changelog(other) is None


async def test_firmware_list_names_the_devices_with_their_ids(mgr, net):
    """The Firmware tab gets ID and name of every device a file is provided
    for (Install button), ordered by name; other files have none."""
    await mgr.scan()
    other, _ = store(mgr, "Plus1", "1.3.0")
    files = {f["name"]: f for f in mgr.firmware_list()}
    assert files[mgr.devices[A]["file"]]["devices"] == [{"id": C, "name": "Garage"}, {"id": A, "name": "Kitchen"}]
    assert files[other]["devices"] == [] and all("used_by" not in f for f in files.values())


async def test_deletion_refused_during_check(mgr, net):
    """Firmware cannot be deleted while a check is reserved or running."""
    await mgr.scan()
    name = next(iter(mgr.firmware))
    mgr.reserve_check("device", B)
    with pytest.raises(ShellyError):
        mgr.delete_firmware(name, user="u")
    assert all(not f["deletable"] for f in mgr.firmware_list())
    mgr.progress = None
    mgr.activity.clear()
