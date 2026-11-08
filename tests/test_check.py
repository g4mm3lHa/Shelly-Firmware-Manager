"""Full and single-device checks: progress, reservation, single flight, cancellation,
ignored devices, Reload Device Info, log severity.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio

import manager
import pytest
import shelly
from shelly import ShellyError

from conftest import A, B, C


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
    mgr.last_scan -= 5  # back-dated instead of waiting for the clock to tick
    before = mgr.last_scan
    other = dict(mgr.devices[B])
    mgr.schedule_changed.clear()
    net.devices[A] = ("Kitchen", "192.168.1.10", "1.5.0", "1.5.0")  # updated meanwhile
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
    mgr.devices[B]["last_success"] -= 5  # back-dated instead of waiting for the clock to tick
    mgr.devices[B]["last_check"] -= 5
    success = mgr.devices[B]["last_success"]
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


async def test_changelog_refresh_gets_offered_versions(mgr, net):
    """Full and single checks pass the offered versions so a fresh cache is reused."""
    calls = []

    async def refresh(_session, gens, _pins, wanted=None):
        calls.append((gens, wanted))

    mgr.changelogs.refresh = refresh
    await mgr.scan()
    await mgr.check_device(A)
    assert calls == [({2}, {2: {"1.5.0"}}), ({2}, {2: {"1.5.0"}})]


def _foreign_pending():
    """Unfinished tasks other than the running test itself."""
    current = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not current and not t.done()]


async def test_cancelled_check_leaves_no_running_download(mgr, net):
    """App stop during a download: the shared download task is cancelled too."""
    net.download_gate = asyncio.Event()  # the download never finishes on its own
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    assert mgr._provisions  # a shared download task is running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr._provisions == {}
    assert _foreign_pending() == []


async def test_cancelled_single_check_leaves_no_running_download(mgr, net):
    """Same for the single-device check."""
    await mgr.scan()  # device known
    for name in list(mgr.firmware):  # force a new download (rc7: a provided file
        (manager.FW_DIR / name).unlink()  # cannot be deleted in the UI, remove it directly)
    mgr.firmware.clear()
    net.download_gate = asyncio.Event()
    mgr.reserve_check("device", A)
    task = asyncio.create_task(mgr.check_device(A))
    await asyncio.sleep(0.05)
    assert mgr._provisions
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr._provisions == {}
    assert _foreign_pending() == []


async def test_cancelled_check_cancels_shared_offer_task(mgr, net, monkeypatch):
    """Two devices of one type wait for one shared offer task; cancelling the check
    leaves no task running (rc4 result confirmed for the offer as well)."""
    gate = asyncio.Event()

    async def slow_offer(*_args, **_kwargs):
        await gate.wait()

    monkeypatch.setattr(shelly, "fetch_offer", slow_offer)
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and t is not task and not t.done()]
    assert pending  # device tasks and the shared offer task are running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [t for t in asyncio.all_tasks() if t is not current and not t.done()] == []


async def test_ignored_device_not_contacted_and_stays_registered(mgr, net):
    """Ignored: no device request, known update kept, not 'unregistered'."""
    await mgr.scan()
    mgr.set_ignored(A, True)
    calls = net.info_calls
    net.devices[A] = ("Kitchen renamed", *net.devices[A][1:])
    await mgr.scan()
    assert net.info_calls == calls + 2  # only B and C
    dev = mgr.devices[A]
    assert dev["status"] == manager.STATUS_READY and dev["name"] == "Kitchen renamed"
    check = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert check["devices"] == 2
    await mgr.check_device(A)  # explicit single-device check stays possible
    assert net.info_calls == calls + 3
    mgr.set_ignored(A, False)
    assert "ignored" not in mgr.devices[A] and net.info_calls == calls + 3  # resume sends nothing


async def test_reload_device_reads_only_the_device(mgr, net):
    """No offer/download/changelog call, offer and check time kept."""
    await mgr.scan()
    dev = mgr.devices[A]
    before = {k: dev.get(k) for k in ("last_check", "last_success", "offer_version", "status")}
    offers, downloads = net.offer_calls, net.downloads
    net.settings_calls.clear()
    result = await mgr.reload_device(A)
    assert result == {"warning": None}
    assert net.offer_calls == offers and net.downloads == downloads
    assert {k: dev.get(k) for k in before} == before and dev["info_refreshed"]
    assert net.settings_calls == [True]  # extended status read
    # Version reached the known offer (e.g. updated in the Shelly app): up to date
    net.devices[A] = (*net.devices[A][:2], "1.5.0", "1.5.0")
    await mgr.reload_device(A)
    assert dev["status"] == manager.STATUS_CURRENT and dev["last_update"] and net.offer_calls == offers


async def test_reload_partial_failure_and_locks(mgr, net):
    """A failed status read is a warning with unknown state; refused during a check."""
    await mgr.scan()
    net.settings_error = shelly.AuthError("The Shelly rejected the password")
    result = await mgr.reload_device(B)
    assert "rejected" in result["warning"] and mgr.devices[B]["settings_error"]["severity"] == "error"
    net.gate = asyncio.Event()
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    with pytest.raises(manager.ShellyError):
        await mgr.reload_device(B)
    net.gate.set()
    await task


async def test_parallel_check_is_bounded_and_single_flight(mgr, net, monkeypatch):
    """At most CHECK_PARALLEL devices at once; one offer and one download per firmware."""
    for i in range(10):
        net.devices[f"{i:031x}e"] = (f"Dev {i}", f"192.168.1.{50 + i}", "1.4.0", "1.5.0")
    running, peak = 0, 0
    original = shelly.device_info

    async def counting(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        try:
            return await original(*args, **kwargs)
        finally:
            running -= 1

    monkeypatch.setattr(shelly, "device_info", counting)
    assert await mgr.scan()
    assert peak == manager.CHECK_PARALLEL
    assert net.offer_calls == 1 and net.downloads == 1
    assert len([d for d in mgr.devices.values() if d["status"] == manager.STATUS_READY]) == 12


async def test_check_severity_levels(mgr, net):
    """Unreachable = warning, auth failure = error; entry gets the worst level."""
    net.unreachable.add(B)
    await mgr.scan()
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert entry["severity"] == "warning" and entry["ok"] and entry["warnings"] == 1
    assert {f["severity"] for f in entry["findings"]} == {"success", "warning"}
    net.unreachable.clear()
    net.settings_error = shelly.AuthError("The Shelly requires a password")
    await mgr.scan()
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert entry["severity"] == "error" and not entry["ok"] and entry["errors"] == 3
    assert "0 warning(s), 3 error(s)" in entry["result"]


# ---------------------------------------------------------------- Manufacturer server access (3.0.1)


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
        def __init__(self, _session, url, _pins, _timeout):
            seen.append(url)
            status, headers, body = pages[url]
            self.resp = type("Resp", (), {
                "status": status, "headers": headers, "charset": None, "content": _FakeBody(body),
                "raise_for_status": lambda _self: None if status < 400 else (_ for _ in ()).throw(ValueError(status)),
            })()

        async def __aenter__(self):
            return self.resp

        async def __aexit__(self, *_exc):
            return None

    monkeypatch.setattr(shelly, "_CloudRequest", Request)
    return seen


async def test_cloud_json_and_text_share_redirect_and_limit_checks(monkeypatch):
    """JSON and text pages follow redirects only within shelly.cloud and keep the size limit."""
    seen = fake_cloud(monkeypatch, {
        "https://updates.shelly.cloud/a": (302, {"Location": "/b"}, b""),
        "https://updates.shelly.cloud/b": (200, {}, b'{"stable": 1}'),
        "https://shelly-api-docs.shelly.cloud/x": (301, {"Location": "https://evil.example/x"}, b""),
        "https://shelly-api-docs.shelly.cloud/big": (200, {}, b"x" * 3000),
    })
    assert await shelly._cloud_json(None, "https://updates.shelly.cloud/a", None) == {"stable": 1}
    assert seen == ["https://updates.shelly.cloud/a", "https://updates.shelly.cloud/b"]
    with pytest.raises(shelly.ShellyError, match="not allowed"):
        await shelly.fetch_cloud_text(None, "https://shelly-api-docs.shelly.cloud/x", None, 1024)
    with pytest.raises(shelly.ShellyError, match="larger"):
        await shelly.fetch_cloud_text(None, "https://shelly-api-docs.shelly.cloud/big", None, 1024)
