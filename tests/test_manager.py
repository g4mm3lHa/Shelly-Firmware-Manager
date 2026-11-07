"""Backend tests: single-device check, progress, concurrency, robustness (2.2.1, adapted to 3.0)."""

import asyncio
import json

import manager
import pytest
from conftest import OPTIONS, A, B, C
from shelly import ShellyError


async def test_full_check_provides_firmware_and_reuses_download(mgr, net):
    """One download per firmware, reused for the second device; progress is cleared."""
    assert await mgr.scan(user="u")
    assert mgr.devices[A]["status"] == manager.STATUS_READY
    assert mgr.devices[C]["status"] == manager.STATUS_READY
    assert mgr.devices[B]["status"] == manager.STATUS_CURRENT
    assert net.downloads == 1  # second device reuses the stored file
    assert net.offer_calls == 1  # one offer per (gen, type)
    assert mgr.progress is None and mgr.activity == {} and not mgr.checking
    assert mgr.last_scan and mgr.schedule_changed.is_set()
    for dev in mgr.devices.values():
        assert dev["last_success"] == dev["last_check"]


async def test_single_check_keeps_last_scan_and_logs(mgr, net):
    """Single-device check: only this device, last_scan and schedule unchanged."""
    await mgr.scan()
    before = mgr.last_scan
    other = dict(mgr.devices[B])
    mgr.schedule_changed.clear()
    net.devices[A] = ("Kitchen", "192.168.1.10", "1.5.0", "1.5.0")  # updated meanwhile
    await asyncio.sleep(1.1)
    result = await mgr.check_device(A, user="alice")
    assert result == {"status": manager.STATUS_CURRENT, "error": None}
    assert mgr.last_scan == before
    assert not mgr.schedule_changed.is_set()
    assert mgr.devices[B] == other  # other devices untouched
    assert mgr.devices[A]["last_check"] > before
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert entry["trigger"] == "device" and entry["devices"] == 1 and entry["user"] == "alice"


async def test_single_check_downloads_missing_firmware(mgr, net):
    """Missing firmware is downloaded like in the full check; stored firmware is reused."""
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["status"] == manager.STATUS_READY and net.downloads == 1
    mgr.devices[C] = {"name": "Garage"}
    await mgr.check_device(C)
    assert mgr.devices[C]["status"] == manager.STATUS_READY and net.downloads == 1


async def test_single_check_error_keeps_last_success(mgr, net):
    """A failed attempt updates last_check, keeps last_success and the known firmware."""
    await mgr.scan()
    success = mgr.devices[B]["last_success"]
    await asyncio.sleep(1.1)
    net.unreachable.add(B)
    result = await mgr.check_device(B)
    dev = mgr.devices[B]
    assert result["status"] == manager.STATUS_ERROR
    assert dev["last_success"] == success < dev["last_check"]
    assert dev["version"] == "1.5.0"


async def test_single_check_unregistered(mgr, net):
    """Device removed from the registry: unregistered, failure logged, no crash."""
    await mgr.scan()
    del net.devices[C]
    result = await mgr.check_device(C)
    assert mgr.devices[C]["status"] == manager.STATUS_UNREGISTERED
    assert "registry" in result["error"]


async def test_unknown_device_raises_keyerror(mgr):
    """Unknown device ID."""
    with pytest.raises(KeyError):
        await mgr.check_device("nope")


async def test_reservation_blocks_second_check(mgr, net):
    """A reserved check refuses further checks and approvals until it ends."""
    await mgr.scan()
    net.gate = asyncio.Event()
    mgr.reserve_check("device", A)
    assert mgr.checking and mgr.activity[A] == manager.ACTIVITY_WAITING
    with pytest.raises(ShellyError):
        mgr.reserve_check("all")
    with pytest.raises(ShellyError):
        mgr.reserve_check("device", B)
    assert await mgr.scan() is False  # automatic check during a reserved one: skipped
    task = asyncio.create_task(mgr.check_device(A))
    await asyncio.sleep(0.05)
    assert mgr.activity[A] == manager.ACTIVITY_CHECKING
    assert mgr.progress["kind"] == "device" and mgr.progress["phase"] == "devices"
    assert "Checking Kitchen" in mgr.busy
    net.gate.set()
    await task
    assert not mgr.checking and mgr.activity == {}


async def test_progress_counts_during_full_check(mgr, net):
    """Global progress: total, done and errors; 3.0 checks up to CHECK_PARALLEL
    devices at once, so all three devices are being checked together."""
    net.gate = asyncio.Event()
    net.unreachable.add(B)
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    p = mgr.progress
    assert p["kind"] == "all" and p["total"] == 3 and p["done"] == 0
    states = sorted(mgr.activity.values())
    assert states == ["checking", "checking", "checking"]
    net.gate.set()
    await task
    log = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert log["trigger"] == "scheduled" or log["trigger"] == "manual"


async def test_cancel_clears_activity(mgr, net):
    """App stop during a check: no device remains 'checking' and no progress remains."""
    net.gate = asyncio.Event()
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    assert mgr.activity
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr.activity == {} and mgr.progress is None and not mgr.checking


async def test_cancel_during_download_leaves_no_file(mgr, net):
    """Cancelled download: device not 'ready', no firmware registered."""
    net.download_gate = asyncio.Event()
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    assert manager.ACTIVITY_DOWNLOADING in mgr.activity.values()
    assert mgr.progress["phase"] == "download"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr.firmware == {} and mgr.activity == {}
    assert all(d.get("status") != manager.STATUS_READY for d in mgr.devices.values())


async def test_partial_downloads_removed_at_start(net):
    """Leftover .part files from a crash are deleted when the app starts."""
    fw = manager.FW_DIR
    fw.mkdir(parents=True)
    (fw / ".part-0123456789abcdef").write_bytes(b"half")
    (fw / ("a" * 64 + ".zip")).write_bytes(b"keep")
    manager.Manager(dict(OPTIONS), session=None, token="t")
    assert not (fw / ".part-0123456789abcdef").exists()
    assert (fw / ("a" * 64 + ".zip")).exists()


async def test_save_failure_keeps_previous_state(mgr, net, monkeypatch):
    """Write error (e.g. disk full): old state.json intact, error shown, check continues."""
    await mgr.scan()
    old = manager.STATE_FILE.read_text()
    real = type(manager.STATE_FILE).write_text

    def failing(self, *args, **kwargs):
        if self.suffix == ".tmp":
            raise OSError(28, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(manager.STATE_FILE), "write_text", failing)
    assert await mgr.scan() is True
    assert manager.STATE_FILE.read_text() == old
    assert "No space left" in mgr.storage_error
    assert mgr.public_state()["storage_error"]
    assert not manager.STATE_FILE.with_suffix(".tmp").exists()
    monkeypatch.setattr(type(manager.STATE_FILE), "write_text", real)
    assert mgr.save() and mgr.storage_error is None


async def test_state_file_compact_and_no_runtime_state(mgr, net):
    """state.json is compact JSON and does not persist activity/progress."""
    await mgr.scan()
    text = manager.STATE_FILE.read_text()
    assert "\n" not in text and "activity" not in text and "progress" not in json.loads(text)


async def test_restart_during_install_is_unconfirmed(net):
    """An installation interrupted by a restart is never shown as success."""
    manager.STATE_FILE.write_text(json.dumps({"devices": {A: {"name": "K", "status": "installing", "last_check": 5}}}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.devices[A]["status"] == manager.STATUS_UNCLEAR
    assert m.progress is None and m.activity == {}


async def test_legacy_state_gets_last_success(net):
    """2.2.0 state: devices without error adopt last_check as last_success."""
    manager.STATE_FILE.write_text(json.dumps({"devices": {
        "a": {"name": "A", "status": "up-to-date", "last_check": 10},
        "b": {"name": "B", "status": "error", "last_check": 11}}}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.devices["a"]["last_success"] == 10
    assert "last_success" not in m.devices["b"]


async def test_settings_refused_while_device_checked(mgr, net):
    """No settings change on a device while it is being checked."""
    await mgr.scan()
    net.gate = asyncio.Event()
    task = asyncio.create_task(mgr.check_device(B))
    await asyncio.sleep(0.05)
    with pytest.raises(ShellyError):
        mgr._settings_device(B)
    net.gate.set()
    await task
    mgr._settings_device(B)  # allowed again


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


async def test_manual_and_single_checks_do_not_shift_schedule(mgr, net):
    """3.0: only the configured schedule counts – manual and single-device checks
    leave it unchanged (replaces the 2.x interval-from-last-check test)."""
    before = dict(mgr.schedule)
    await mgr.scan(user="u")
    await mgr.check_device(A)
    assert mgr.schedule == before and mgr.schedule["last_date"] is None


async def test_changelog_refresh_gets_offered_versions(mgr, net):
    """Full and single checks pass the offered versions so a fresh cache is reused."""
    calls = []

    async def refresh(_session, gens, _pins, wanted=None):
        calls.append((gens, wanted))

    mgr.changelogs.refresh = refresh
    await mgr.scan()
    await mgr.check_device(A)
    assert calls == [({2}, {2: {"1.5.0"}}), ({2}, {2: {"1.5.0"}})]
