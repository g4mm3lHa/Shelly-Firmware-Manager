"""Full and single-device checks: progress, reservation, single flight, cancellation,
ignored devices, Reload Device Info, log severity, firmware store first.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio

import data_paths
import manager_checks
import manager_core
import pytest
import shelly
from conftest import A, B, C
from helpers import make_manager, store
from shelly import ShellyError


async def test_full_check_provides_firmware_and_reuses_download(mgr, net):
    """One download per firmware, reused for the second device; progress is cleared."""
    assert await mgr.scan(user="u")
    assert mgr.devices[A]["status"] == manager_core.STATUS_READY
    assert mgr.devices[C]["status"] == manager_core.STATUS_READY
    assert mgr.devices[B]["status"] == manager_core.STATUS_CURRENT
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
    assert result == {"status": manager_core.STATUS_CURRENT, "error": None}
    assert mgr.last_scan == before
    assert not mgr.schedule_changed.is_set()
    assert mgr.devices[B] == other  # other devices untouched
    assert mgr.devices[A]["last_check"] > before
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert entry["trigger"] == "device" and entry["devices"] == 1 and entry["user"] == "alice"


async def test_downloads_and_checks_name_the_user(mgr, net):
    """A download is logged with the user of the check that fetched it; scheduled
    checks and their downloads name "Schedule"."""
    assert await mgr.scan(user="alice")
    [download] = [e for e in mgr.events.entries if e["kind"] == "download"]
    [check] = [e for e in mgr.events.entries if e["kind"] == "check"]
    assert download["user"] == check["user"] == "alice" and mgr._check_user is None
    mgr.devices.clear()
    for name in list(mgr.firmware):
        (data_paths.FW_DIR / name).unlink()
    mgr.firmware.clear()
    assert await mgr.scan(automatic=True)
    download, check = ([e for e in mgr.events.entries if e["kind"] == kind][-1] for kind in ("download", "check"))
    assert download["user"] == check["user"] == manager_checks.SCHEDULE_USER == "Schedule"


async def test_single_check_downloads_missing_firmware(mgr, net):
    """Missing firmware is downloaded like in the full check; stored firmware is reused."""
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["status"] == manager_core.STATUS_READY and net.downloads == 1
    mgr.devices[C] = {"name": "Garage"}
    await mgr.check_device(C)
    assert mgr.devices[C]["status"] == manager_core.STATUS_READY and net.downloads == 1


async def test_single_check_error_keeps_last_success(mgr, net):
    """A failed attempt updates last_check, keeps last_success and the known firmware."""
    await mgr.scan()
    mgr.devices[B]["last_success"] -= 5  # back-dated instead of waiting for the clock to tick
    mgr.devices[B]["last_check"] -= 5
    success = mgr.devices[B]["last_success"]
    net.unreachable.add(B)
    result = await mgr.check_device(B)
    dev = mgr.devices[B]
    assert result["status"] == manager_core.STATUS_ERROR
    assert dev["last_success"] == success < dev["last_check"]
    assert dev["version"] == "1.5.0"


async def test_single_check_unregistered(mgr, net):
    """Device removed from the registry: unregistered, failure logged, no crash."""
    await mgr.scan()
    del net.devices[C]
    result = await mgr.check_device(C)
    assert mgr.devices[C]["status"] == manager_core.STATUS_UNREGISTERED
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
    assert mgr.checking and mgr.activity[A] == manager_core.ACTIVITY_WAITING
    with pytest.raises(ShellyError):
        mgr.reserve_check("all")
    with pytest.raises(ShellyError):
        mgr.reserve_check("device", B)
    assert await mgr.scan() is False  # automatic check during a reserved one: skipped
    task = asyncio.create_task(mgr.check_device(A))
    await asyncio.sleep(0.05)
    assert mgr.activity[A] == manager_core.ACTIVITY_CHECKING
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
    assert manager_core.ACTIVITY_DOWNLOADING in mgr.activity.values()
    assert mgr.progress["phase"] == "download"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr.firmware == {} and mgr.activity == {}
    assert all(d.get("status") != manager_core.STATUS_READY for d in mgr.devices.values())


def changelog_page(mgr, versions=("1.5.0",)):
    """Replace the page fetch: every call is recorded and delivers these versions."""
    calls = []

    async def refresh(_session, gens, _pins):
        calls.append(set(gens))
        mgr.changelogs.data["2"] = {"fetched": 0, "versions": {
            v: {"date": "2026-10-01", "entries": [{"category": None, "text": f"Fix {v}", "models": []}]} for v in versions}}

    mgr.changelogs.refresh = refresh
    return calls


async def test_changelog_is_kept_with_the_firmware_and_requested_once(mgr, net):
    """The changelog of a provided firmware is stored with its store entry; a
    stored changelog is never requested again and is shown without the page."""
    calls = changelog_page(mgr)
    await mgr.scan()
    name = mgr.devices[A]["file"]
    assert calls == [{2}] and mgr.firmware[name]["changelog"]["entries"][0]["text"] == "Fix 1.5.0"
    await mgr.scan()
    await mgr.check_device(A)
    assert calls == [{2}]
    mgr.changelogs.data.clear()
    assert [v["version"] for v in mgr.changelog_for(A)] == ["1.5.0"]
    assert mgr.firmware_list()[0]["changelog"] is True  # list: flag only, the text is fetched on demand


async def test_changelog_page_requested_only_for_provided_firmware_without_one(mgr, net):
    """No update provided: no request. A version the page does not list stays
    without changelog and is asked for again (the hourly limit is in Changelogs)."""
    calls = changelog_page(mgr, versions=("1.4.0",))
    for device in (A, C):
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")
    await mgr.scan()
    assert calls == []
    net.devices[A] = (*net.devices[A][:2], "1.4.0", "1.5.0")
    await mgr.scan()
    await mgr.check_device(A)
    assert calls == [{2}, {2}] and "changelog" not in mgr.firmware[mgr.devices[A]["file"]]


async def test_fetched_page_fills_all_stored_firmware_without_changelog(mgr, net):
    """A page that is fetched anyway also serves the other stored versions (no
    extra request); versions between installed and offered come from that page."""
    older, _ = store(mgr, "Plus1", "1.3.0")
    calls = changelog_page(mgr, versions=("1.5.0", "1.4.5", "1.3.0"))
    await mgr.scan()
    assert calls == [{2}] and mgr.firmware[older]["changelog"]["entries"][0]["text"] == "Fix 1.3.0"
    assert [v["version"] for v in mgr.changelog_for(A)] == ["1.5.0", "1.4.5"]


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
    for name in list(mgr.firmware):  # force a new download (a provided file
        (data_paths.FW_DIR / name).unlink()  # cannot be deleted in the UI, remove it directly)
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
    leaves no task running (the shared offer task is cancelled as well)."""
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
    assert dev["status"] == manager_core.STATUS_READY and dev["name"] == "Kitchen renamed"
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
    assert dev["status"] == manager_core.STATUS_CURRENT and dev["last_update"] and net.offer_calls == offers


async def test_reload_partial_failure_and_locks(mgr, net):
    """A failed status read is a warning with unknown state; refused during a check."""
    await mgr.scan()
    net.settings_error = shelly.AuthError("The Shelly rejected the password")
    result = await mgr.reload_device(B)
    assert "rejected" in result["warning"] and mgr.devices[B]["settings_error"]["severity"] == "error"
    net.gate = asyncio.Event()
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    with pytest.raises(shelly.ShellyError):
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
    assert peak == manager_checks.CHECK_PARALLEL
    assert net.offer_calls == 1 and net.downloads == 1
    ready = [d for d in mgr.devices.values() if d["status"] == manager_core.STATUS_READY]
    assert len(ready) == 12
    # Devices reached after the download get the same manufacturer offer – a
    # file downloaded by this check is not labelled as "from the store"
    assert {d["offer_source"] for d in ready} == {"manufacturer"} and all(d["source_url"] for d in ready)


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


# ---------------------------------------------------------------- Firmware store first


async def test_store_first_provides_the_next_higher_version_without_asking_the_server(mgr, net):
    """Several newer stored versions: each device gets the next higher one to its
    installed version; the manufacturer's server is not asked at all."""
    store(mgr, "Plus1", "1.7.0")
    store(mgr, "Plus1", "1.5.0")
    store(mgr, "Plus1", "1.6.0")
    store(mgr, "Plus1", "1.3.0")  # older: irrelevant
    store(mgr, "Other", "9.0.0")  # other firmware ID: irrelevant
    assert await mgr.scan(user="u")
    assert net.offer_calls == 0 and net.downloads == 0
    pending = {i: mgr.devices[i]["pending_version"] for i in (A, B, C)}
    assert pending == {A: "1.5.0", B: "1.6.0", C: "1.5.0"}  # A, C: 1.4.0 installed, B: 1.5.0
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_READY and dev["offer_version"] == "1.5.0"
    assert dev["offer_source"] == "store" and dev["source_url"] is None
    assert dev["message"] == "Provided from the firmware store, not installed"
    assert mgr.approval_matches(A, dev["sha256"]) and mgr.firmware[dev["file"]]["version"] == "1.5.0"
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert {f["source"] for f in entry["findings"]} == {"store"} and entry["updates"] == 3


async def test_server_is_asked_once_for_devices_without_a_stored_version(mgr, net):
    """Same firmware ID, different installed versions: the decision is made per
    device – one server request for the device the store has nothing newer for."""
    store(mgr, "Plus1", "1.5.0")
    net.offer_version = "1.6.0"
    await mgr.scan()
    assert net.offer_calls == 1 and net.downloads == 1
    assert {i: mgr.devices[i]["pending_version"] for i in (A, B, C)} == {A: "1.5.0", B: "1.6.0", C: "1.5.0"}
    assert {i: mgr.devices[i]["offer_source"] for i in (A, B, C)} == {A: "store", B: "manufacturer", C: "store"}
    assert mgr.devices[B]["source_url"] and mgr.devices[B]["message"] == "Provided, not installed"
    findings = [e for e in mgr.events.entries if e["kind"] == "check"][-1]["findings"]
    assert sorted(f["source"] for f in findings) == ["manufacturer", "store", "store"]


async def test_single_stored_newer_version_is_used_and_uploads_count(mgr, net):
    """One newer file is enough, and an uploaded file is provided like a download –
    marked as upload."""
    name, sha = store(mgr, "Plus1", "1.6.0", source=manager_core.SOURCE_UPLOAD)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert net.offer_calls == 0 and dev["status"] == manager_core.STATUS_READY
    assert (dev["file"], dev["sha256"], dev["pending_version"]) == (name, sha, "1.6.0")
    assert dev["offer_source"] == "upload" and "uploaded file" in dev["message"]
    assert mgr.public_state()["devices"][0]["offer_source"] == "upload"


async def test_download_is_preferred_over_an_upload_of_the_same_version(mgr, net):
    store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    name, _ = store(mgr, "Plus1", "1.5.0")
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["file"] == name and mgr.devices[A]["offer_source"] == "store"


async def test_provided_update_is_replaced_by_the_stored_version(mgr, net):
    """A device that is already "ready" with the manufacturer's version gets the
    next higher stored version at the next check; the old approval is void."""
    net.offer_version = "1.6.0"
    await mgr.scan()
    old = mgr.devices[A]["sha256"]
    assert mgr.devices[A]["pending_version"] == "1.6.0" and mgr.devices[A]["offer_source"] == "manufacturer"
    name, sha = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD)
    calls = net.offer_calls
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert net.offer_calls == calls and net.downloads == 1
    assert (dev["file"], dev["pending_version"], dev["offer_version"]) == (name, "1.5.0", "1.5.0")
    assert dev["offer_source"] == "upload" and dev["source_url"] is None
    assert mgr.approval_matches(A, sha) and not mgr.approval_matches(A, old)
    assert len(mgr.firmware) == 2  # the downloaded 1.6.0 stays stored for the next step


async def test_damaged_stored_file_is_skipped(mgr, net):
    """Checksum mismatch: the file counts as not stored – the next higher one is
    taken, and without another one the server is asked."""
    bad, _ = store(mgr, "Plus1", "1.5.0")
    good, _ = store(mgr, "Plus1", "1.6.0")
    (data_paths.FW_DIR / bad).write_bytes(b"damaged" * 500)
    mgr.devices.update({A: {"name": "Kitchen"}, C: {"name": "Garage"}})
    await mgr.check_device(A)
    assert mgr.devices[A]["file"] == good and bad not in mgr.firmware and net.offer_calls == 0
    (data_paths.FW_DIR / good).unlink()
    await mgr.check_device(C)
    assert net.offer_calls == 1 and mgr.devices[C]["offer_source"] == "manufacturer"
    assert good not in mgr.firmware and mgr.devices[A]["status"] == manager_core.STATUS_READY  # A: checked again later


async def test_stored_file_is_verified_once_per_check(mgr, net, monkeypatch):
    """Twelve devices of one type share one checksum verification of the stored file."""
    for i in range(9):
        net.devices[f"{i:031x}e"] = (f"Dev {i}", f"192.168.1.{50 + i}", "1.4.0", "1.5.0")
    store(mgr, "Plus1", "1.6.0")
    original, hashed = shelly.file_sha256, []

    def counting(target_dir, name):
        hashed.append(name)
        return original(target_dir, name)

    monkeypatch.setattr(shelly, "file_sha256", counting)
    await mgr.scan()
    assert len(hashed) == 1 and net.offer_calls == 0
    assert all(d["pending_version"] == "1.6.0" for d in mgr.devices.values())
    await mgr.scan()
    assert len(hashed) == 2 and mgr._verifying == {} and mgr._fresh == {}  # verified again by the next check


async def test_pre_release_and_foreign_generation_are_not_offered_from_the_store(mgr, net):
    store(mgr, "Plus1", "1.6.0-beta1")
    store(mgr, "Plus1", "1.7.0", gen=1)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert net.offer_calls == 1 and mgr.devices[A]["pending_version"] == "1.5.0"


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
    monkeypatch.setattr(shelly, "fetch_gen1_offers", fetch_gen1_offers)
    return calls


async def test_gen1_list_is_requested_once_for_all_types(net, monkeypatch):
    """The manufacturer serves one list for all Gen1 types – one request per
    check, whatever the number of types; a type that is not allowed is refused."""
    calls = gen1_net(net, monkeypatch, {A: "SHSW-1", B: "SHPLG-S", C: "SHSW-25"})
    m = make_manager(net, gen1_allowed_types=["SHSW-1", "SHPLG-S"])
    m._registry = lambda: asyncio.sleep(0, net.registry())
    await m.scan()
    assert len(calls) == 1
    assert m.devices[A]["status"] == manager_core.STATUS_READY and m.devices[A]["offer_source"] == "manufacturer"
    assert m.devices[B]["status"] == manager_core.STATUS_CURRENT and m.devices[B]["message"] == "No stable offer"
    assert m.devices[C]["status"] == manager_core.STATUS_ERROR and "not in the list of allowed types" in m.devices[C]["message"]


async def test_gen1_store_first_respects_the_allowed_types(net, monkeypatch):
    """Gen1 devices use the store as well – but never for a type that is not allowed."""
    calls = gen1_net(net, monkeypatch, {A: "SHSW-1", B: "SHSW-1", C: "SHSW-25"})
    m = make_manager(net)  # allowed: SHSW-1 only
    m._registry = lambda: asyncio.sleep(0, net.registry())
    store(m, "SHSW-1", "20230913-112003/v1.14.0-gcb84623", gen=1)
    store(m, "SHSW-25", "20230913-112003/v1.14.0-gcb84623", gen=1)
    await m.scan()
    assert calls == [] and net.downloads == 0
    assert m.devices[A]["offer_source"] == "store" and m.devices[B]["status"] == manager_core.STATUS_READY
    assert m.devices[C]["status"] == manager_core.STATUS_ERROR and m.devices[C].get("file") is None


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


async def test_offer_with_invalid_step_count_is_a_readable_error(monkeypatch):
    """A non-numeric "nsteps" in the manufacturer's offer is reported as
    ShellyError, not as "invalid literal for int()"."""
    fake_cloud(monkeypatch, {
        "https://updates.shelly.cloud/update/Plus1": (200, {}, b'{"stable": {"url": "u", "version": "1.5.0", "nsteps": "x"}}'),
    })
    with pytest.raises(ShellyError, match="nsteps"):
        await shelly.fetch_offer(None, "Plus1")


async def test_gen1_list_is_one_document_for_all_types(monkeypatch):
    """The Gen1 list is fetched once; the offer of a type is read from it."""
    seen = fake_cloud(monkeypatch, {"https://api.shelly.cloud/files/firmware": (200, {}, (
        b'{"isok": true, "data": {"SHSW-1": {"url": "http://x/SHSW-1.zip", "version": "v1.14.0"}, "SHEM": {"version": "v1"}}}'))})
    offers = await shelly.fetch_gen1_offers(None)
    assert seen == ["https://api.shelly.cloud/files/firmware"]
    assert shelly.gen1_offer(offers, "SHSW-1") == {"version": "v1.14.0", "url": "http://x/SHSW-1.zip"}
    assert shelly.gen1_offer(offers, "SHEM") is None and shelly.gen1_offer(offers, "unknown") is None
    fake_cloud(monkeypatch, {"https://api.shelly.cloud/files/firmware": (200, {}, b'{"isok": false}')})
    with pytest.raises(ShellyError, match="reports an error"):
        await shelly.fetch_gen1_offers(None)


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

    monkeypatch.setattr(shelly, "fetch_offer", failing)
    await mgr.scan()
    assert mgr.devices[A]["message"] == "408, 'Request Timeout', 'https://updates.shelly.cloud/update/Plus1'"
