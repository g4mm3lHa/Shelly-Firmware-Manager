"""Firmware store: no automatic deletion, manual delete, downgrade of Gen2+ devices,
manual upload (stage + confirm).

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import hashlib

import manager
import pytest
import shelly
from shelly import ShellyError

from conftest import OPTIONS, A, B, C
from helpers import chunks, firmware_zip, store


@pytest.fixture
def fast_install(monkeypatch):
    """Short installation timings for end-to-end installs."""
    monkeypatch.setattr(manager, "INSTALL_FIRST_CHECK_S", 0)
    monkeypatch.setattr(manager, "INSTALL_POLL_S", 0.01)
    monkeypatch.setattr(manager, "INSTALL_NO_DOWNLOAD_S", 0.1)
    monkeypatch.setattr(manager, "INSTALL_WAIT_S", 0.3)


async def test_no_automatic_deletion_of_stored_firmware(mgr, net):
    """Q6: any number of versions stays stored after checks and installations."""
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
    assert all((manager.FW_DIR / n).is_file() for n in before)
    assert not hasattr(manager, "KEEP_PER_TYPE")


async def test_housekeeping_keeps_temporary_files_and_drops_orphans(mgr, net):
    """Q8: unknown files and entries without file are cleaned, temporary files are not
    (they belong to a running download/upload); leftovers are removed at start."""
    name, _ = store(mgr, "Plus1", "1.3.0")
    gone, _ = store(mgr, "Plus1", "1.2.0")
    (manager.FW_DIR / gone).unlink()
    (manager.FW_DIR / ("f" * 64 + ".zip")).write_bytes(b"x" * 2000)  # not in the store
    part = manager.FW_DIR / (manager.UPLOAD_PREFIX + "abc")
    part.write_bytes(b"x")
    mgr.cleanup_firmware()
    assert name in mgr.firmware and gone not in mgr.firmware
    assert not (manager.FW_DIR / ("f" * 64 + ".zip")).exists()
    assert part.exists()
    manager.Manager(dict(OPTIONS), session=None, token="t")  # restart
    assert not part.exists()


async def test_total_size_and_sources_in_the_list(mgr, net):
    """Q10: total store size; upload entries are marked and have no https flag."""
    store(mgr, "Plus1", "1.3.0")
    store(mgr, "Plus1", "1.2.0", source=manager.SOURCE_UPLOAD)
    assert mgr.store_size() == sum(m["size"] for m in mgr.firmware.values())
    by_version = {f["version"]: f for f in mgr.firmware_list()}
    assert by_version["1.3.0"]["source"] == "download" and by_version["1.3.0"]["https"] is True
    assert by_version["1.2.0"]["source"] == "upload" and by_version["1.2.0"]["https"] is None


async def test_entries_before_rc7_count_as_downloads(mgr, net):
    name, _ = store(mgr, "Plus1", "1.3.0")
    del mgr.firmware[name]["source"]
    assert mgr.firmware_list()[0]["source"] == "download"


async def test_delete_locked_while_provided_or_installing(mgr, net):
    await mgr.scan()
    provided = mgr.devices[A]["file"]
    entry = next(f for f in mgr.firmware_list() if f["name"] == provided)
    assert not entry["deletable"] and "pending update" in entry["locked"]
    with pytest.raises(manager.ShellyError):
        mgr.delete_firmware(provided, confirm=True, acknowledged=True)
    mgr.devices[A]["status"] = manager.STATUS_INSTALLING
    assert "installed" in next(f for f in mgr.firmware_list() if f["name"] == provided)["locked"]
    assert provided in mgr.firmware


async def test_delete_needs_second_confirmation(mgr, net):
    name, _ = store(mgr, "Plus1", "1.3.0")
    for confirm, ack in ((False, False), (True, False), (False, True)):
        with pytest.raises(ValueError):
            mgr.delete_firmware(name, user="u", confirm=confirm, acknowledged=ack)
    assert name in mgr.firmware
    mgr.delete_firmware(name, user="u", confirm=True, acknowledged=True)
    assert name not in mgr.firmware and not (manager.FW_DIR / name).exists()
    entry = mgr.events.entries[-1]
    assert entry["kind"] == "delete" and entry["source"] == "download" and entry["user"] == "u"


async def test_downgrade_lists_all_older_stable_versions(mgr, net):
    """Q1/Q7: all stored older stable versions of the firmware ID, newest first;
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
    store(mgr, "Plus1", "1.3.0", source=manager.SOURCE_UPLOAD, content=b"u" * 3000)
    name, _ = store(mgr, "Plus1", "1.3.0", content=b"d" * 3000)
    entries = mgr.downgrade_versions(B)
    assert [(e["version"], e["name"], e["source"]) for e in entries] == [("1.3.0", name, "download")]


async def test_downgrade_also_for_up_to_date_and_ignored_devices(mgr, net):
    """D4: up-to-date and ignored devices can be downgraded."""
    await mgr.scan()
    store(mgr, "Plus1", "1.4.0")
    mgr.set_ignored(B, True)
    assert mgr.devices[B]["status"] == manager.STATUS_CURRENT
    assert mgr.downgrade_versions(B) and mgr.downgrade_blocker(B) is None


async def test_no_downgrade_for_gen1(mgr, net):
    """D4: Gen1 never offers a downgrade."""
    await mgr.scan()
    store(mgr, "SHSW-1", "1.13.0", gen=1)
    mgr.devices[A].update(gen=1, type="SHSW-1", version="20230913-112003/v1.14.0-gcb84623")
    assert mgr.downgrade_versions(A) == []
    assert "Gen1" in mgr.downgrade_blocker(A)


async def test_downgrade_blocked_during_check_and_without_download_url(mgr, net):
    await mgr.scan()
    store(mgr, "Plus1", "1.4.0")
    mgr.reserve_check("all")
    assert "Check" in mgr.downgrade_blocker(B)
    mgr.progress = None
    mgr.download_base = None
    assert "download base URL" in mgr.downgrade_blocker(B)


async def test_downgrade_installs_the_selected_version(mgr, net, monkeypatch, fast_install):
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
    assert dev["status"] == manager.STATUS_AVAILABLE and "a newer version is provided again" in dev["message"]
    # 3.1.0: a downgrade is never followed by the check after installations
    assert all(e.get("trigger") != "install" for e in mgr.events.entries if e["kind"] == "check")
    assert "downgrade" not in dev and mgr.notified[-1][0] == "Shelly downgraded: Kitchen"
    events = [e for e in mgr.events.entries if e["kind"] == "install"]
    assert [e["event"] for e in events] == ["started", "confirmed"]
    assert all(e["downgrade"] and e["old"] == "1.4.0" and e["new"] == "1.3.0" for e in events)
    assert name in mgr.firmware  # nothing deleted afterwards
    await mgr.check_device(A)  # the newer version is provided again from the store
    assert mgr.devices[A]["status"] == manager.STATUS_READY and net.downloads == 1


async def test_downgrade_refused_for_unknown_or_newer_file(mgr, net):
    await mgr.scan()
    _, newer = store(mgr, "Plus1", "1.6.0")
    assert await mgr.install(B, "0" * 64, "u", downgrade=True) is False
    assert await mgr.install(B, newer, "u", downgrade=True) is False
    assert mgr.installing == set() and mgr.devices[B]["status"] == manager.STATUS_CURRENT


async def test_failed_downgrade_is_an_error_and_keeps_the_files(mgr, net, monkeypatch, fast_install):
    await mgr.scan()
    name, sha = store(mgr, "Plus1", "1.3.0")

    async def refuse(*_args, **_kwargs):
        raise shelly.ShellyError("Shelly refused the update")

    monkeypatch.setattr(shelly, "trigger_update", refuse)
    assert await mgr.install(B, sha, "u", downgrade=True) is False
    dev = mgr.devices[B]
    assert dev["status"] == manager.STATUS_ERROR and "refused" in dev["message"]
    assert "downgrade" not in dev and name in mgr.firmware
    assert mgr.events.entries[-1]["event"] == "failed" and mgr.events.entries[-1]["downgrade"]


async def test_restart_during_downgrade_is_unconfirmed(mgr, net):
    await mgr.scan()
    mgr.devices[B].update(status=manager.STATUS_INSTALLING, downgrade=True)
    mgr.save()
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.devices[B]["status"] == manager.STATUS_UNCLEAR and "downgrade" not in m.devices[B]


async def test_upload_prefills_from_manifest_and_registers(mgr, net):
    """Q4/Q5: type/version pre-filled from manifest.json, confirmed by the user; a
    matching SHA-256 is recorded as checked."""
    await mgr.scan()
    data = firmware_zip({"name": "Plus1", "platform": "esp32", "build_id": "20240101-000000/1.3.0-gabc"})
    staged = await mgr.stage_upload(chunks(data), "plus1-1.3.0.zip", len(data), "alice")
    assert staged["detected"] == {"type": "Plus1", "version": "1.3.0"} and staged["types"] == ["Plus1"]
    sha = hashlib.sha256(data).hexdigest()
    assert staged["sha256"] == sha and staged["size"] == len(data)
    entry = mgr.confirm_upload(staged["token"], "Plus1", "1.3.0", sha.upper(), "alice")
    assert entry["source"] == "upload" and entry["sha_checked"] is True and entry["gen"] == 2
    assert (manager.FW_DIR / entry["name"]).read_bytes() == data
    assert [v["version"] for v in mgr.downgrade_versions(B)] == ["1.3.0"]
    log = mgr.events.entries[-1]
    assert log["kind"] == "upload" and log["user"] == "alice" and log["sha_checked"] is True
    assert not list(manager.FW_DIR.glob(manager.UPLOAD_PREFIX + "*"))


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
    with pytest.raises(manager.ShellyError, match="does not match"):
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
    data = (manager.FW_DIR / stored).read_bytes()
    staged = await mgr.stage_upload(chunks(data), "same.bin", len(data))  # any extension
    assert staged["duplicate"] == "Plus1 1.5.0"
    with pytest.raises(manager.ShellyError, match="already stored"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.4.0")
    data2 = firmware_zip()
    staged = await mgr.stage_upload(chunks(data2), "other.zip", len(data2))
    with pytest.raises(manager.ShellyError, match="already stored"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.5.0")


async def test_upload_checks_extension_size_and_zip_format(mgr, net):
    with pytest.raises(ValueError, match="zip"):
        await mgr.stage_upload(chunks(b"x" * 5000), "fw.exe", 5000)
    with pytest.raises(ValueError, match="small"):
        await mgr.stage_upload(chunks(b"x" * 10), "fw.bin", 10)
    with pytest.raises(ValueError, match="64 MiB"):
        await mgr.stage_upload(chunks(b""), "fw.bin", shelly.MAX_FW_SIZE + 1)
    with pytest.raises(ValueError, match="ZIP"):
        await mgr.stage_upload(chunks(b"x" * 5000), "fw.zip", 5000)
    with pytest.raises(ValueError, match="larger than announced"):
        await mgr.stage_upload(chunks(b"x" * 6000), "fw.bin", 5000)
    with pytest.raises(ValueError, match="incomplete"):
        await mgr.stage_upload(chunks(b"x" * 4000), "fw.bin", 5000)
    assert not list(manager.FW_DIR.glob(manager.PART_PREFIX + "*"))


async def test_upload_token_expiry_and_cancel(mgr, net, monkeypatch):
    await mgr.scan()
    data = firmware_zip()
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    with pytest.raises(manager.ShellyError):
        mgr.confirm_upload("0" * 32, "Plus1", "1.2.0")
    mgr.cancel_upload(staged["token"])
    assert mgr._upload is None and not list(manager.FW_DIR.glob(manager.PART_PREFIX + "*"))
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data))
    mgr._upload["created"] -= manager.UPLOAD_TTL_S + 1
    with pytest.raises(manager.ShellyError, match="upload the file again"):
        mgr.confirm_upload(staged["token"], "Plus1", "1.2.0")


async def test_uploaded_file_is_provided_as_update(mgr, net):
    """3.1.0: an uploaded newer version is provided by the next
    check like a stored download – marked as upload, the server is not asked."""
    await mgr.scan()
    for name in list(mgr.firmware):
        (manager.FW_DIR / name).unlink()
    mgr.firmware.clear()
    upload, _ = store(mgr, "Plus1", "1.5.0", source=manager.SOURCE_UPLOAD, content=b"u" * 3000)
    downloads, offers = net.downloads, net.offer_calls
    await mgr.check_device(A)
    assert (net.downloads, net.offer_calls) == (downloads, offers)
    assert mgr.devices[A]["file"] == upload and mgr.devices[A]["offer_source"] == "upload"
    listed = next(f for f in mgr.firmware_list() if f["name"] == upload)
    assert listed["devices"] == [{"id": A, "name": "Kitchen"}] and not listed["deletable"]


async def test_firmware_list_names_the_devices_with_their_ids(mgr, net):
    """3.1.0: the Firmware tab gets ID and name of every device a file is provided
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
