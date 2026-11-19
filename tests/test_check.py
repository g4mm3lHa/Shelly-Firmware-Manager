"""Full and single-device checks: progress, reservation, single flight, cancellation,
ignored devices, Reload Device Info, log severity, Gen2+ next steps (server first,
stored file of the offered version, intermediate versions, store fallback, reference
request), Gen1 firmware store first.

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
    assert net.offer_calls == 2  # one request per firmware ID and installed version (1.4.0, 1.5.0)
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
    assert download["user"] == check["user"] == "alice"
    assert mgr._scan_user is None and mgr._check_users == {}
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


async def test_battery_device_not_reachable_is_asleep(mgr, net):
    """A battery device detected while awake stays marked; when it does not answer, a
    check shows it as asleep (warning in the log, not an error) and keeps the known
    firmware. A device without the marker, or a battery device that answers with an
    error, is an error as before."""
    net.battery.add(B)
    await mgr.scan()
    assert mgr.devices[B]["battery"] is True and "battery" not in mgr.devices[A]
    assert next(d for d in mgr.public_state()["devices"] if d["id"] == B)["battery"] is True
    net.asleep.update({A, B})
    await mgr.scan()
    dev = mgr.devices[B]
    assert dev["status"] == manager_core.STATUS_ASLEEP and dev["battery"] is True
    assert dev["message"].startswith("Battery device asleep") and dev["version"] == "1.5.0"
    assert mgr.devices[A]["status"] == manager_core.STATUS_ERROR
    log = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    asleep = next(f for f in log["findings"] if f["device"] == "Hall")
    assert asleep["severity"] == "warning" and asleep["error"].startswith("Battery device asleep")
    net.asleep.clear()
    net.unreachable.add(B)  # an answer of the device, not a missing one
    await mgr.check_device(B)
    assert mgr.devices[B]["status"] == manager_core.STATUS_ERROR


def test_asleep_device_keeps_store_fallback_file():
    """A stored version offered for a manual installation stays held while the battery
    device sleeps, like after any failed check."""
    dev = {"file": "x.zip", "status": manager_core.STATUS_ASLEEP, "store_fallback": True}
    assert manager_core.holds_file(dev)
    assert not manager_core.holds_file({**dev, "store_fallback": False})


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
    """A reserved single check refuses the full check and a second check of the same
    device until it ends; other devices can be checked at the same time."""
    await mgr.scan()
    net.gate = asyncio.Event()
    mgr.reserve_check("device", A)
    assert not mgr.checking and mgr.device_checks == {A: "queued"}
    assert mgr.activity[A] == manager_core.ACTIVITY_WAITING
    with pytest.raises(ShellyError):
        mgr.reserve_check("all")
    with pytest.raises(ShellyError):
        mgr.reserve_check("device", A)
    assert await mgr.scan() is False  # full check during a single one: refused
    task = asyncio.create_task(mgr.check_device(A))
    other = asyncio.create_task(mgr.check_device(B))
    await asyncio.sleep(0.05)
    assert mgr.device_checks == {A: "running", B: "running"} and mgr.progress is None
    assert mgr.activity[A] == manager_core.ACTIVITY_CHECKING
    assert mgr.busy == "Checking 2 devices"
    net.gate.set()
    await asyncio.gather(task, other)
    assert not mgr.checking and mgr.activity == {} and mgr.device_checks == {}


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


def _forget_downloads(mgr, net):
    """Remove all stored files so the next check downloads again (a provided file
    cannot be deleted in the UI, remove it directly)."""
    for name in list(mgr.firmware):
        (data_paths.FW_DIR / name).unlink()
    mgr.firmware.clear()
    net.downloads = 0


async def test_parallel_single_checks_share_one_download(mgr, net):
    """Single-device checks of two devices with the same firmware ID and offered
    version download the file once: the second check waits for the running download
    (shown as downloading for both) and provides the same file."""
    await mgr.scan()  # devices known
    _forget_downloads(mgr, net)
    net.download_gate = asyncio.Event()
    mgr.reserve_check("device", A)
    first = asyncio.create_task(mgr.check_device(A, user="alice"))
    await asyncio.sleep(0.05)
    mgr.reserve_check("device", C)
    second = asyncio.create_task(mgr.check_device(C, user="bob"))
    await asyncio.sleep(0.05)
    assert net.downloads == 1
    assert mgr.activity == {A: manager_core.ACTIVITY_DOWNLOADING, C: manager_core.ACTIVITY_DOWNLOADING}
    net.download_gate.set()
    assert (await first)["status"] == (await second)["status"] == manager_core.STATUS_READY
    assert net.downloads == 1
    assert mgr.devices[A]["file"] == mgr.devices[C]["file"] and mgr.devices[A]["file"] in mgr.firmware
    assert {mgr.devices[d]["offer_source"] for d in (A, C)} == {"manufacturer"}
    downloads = [e for e in mgr.events.entries if e["kind"] == "download"]
    assert downloads[-1]["user"] == "alice"  # one entry, user of the check that started it
    assert len(downloads) == 2  # the first one is from the full check above
    assert mgr._provisions == {} and mgr._provision_waiters == {} and mgr._fetching == set()
    assert mgr.activity == {}


async def test_single_check_after_shared_download_uses_stored_file(mgr, net):
    """A single-device check started after the download of the same firmware ended
    uses the stored file instead of downloading it again."""
    await mgr.scan()
    _forget_downloads(mgr, net)
    await mgr.check_device(A)
    await mgr.check_device(C)
    assert net.downloads == 1
    assert mgr.devices[C]["status"] == manager_core.STATUS_READY
    assert mgr.devices[C]["file"] == mgr.devices[A]["file"]


async def test_failed_shared_download_fails_all_waiting_checks(mgr, net, monkeypatch):
    """A failed shared download is an error for every check waiting for it (no
    retries); the next check starts a new download."""
    await mgr.scan()
    _forget_downloads(mgr, net)
    gate = asyncio.Event()
    calls = 0

    async def failing(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await gate.wait()
        raise ShellyError("Download failed")

    monkeypatch.setattr(shelly, "download_firmware", failing)
    mgr.reserve_check("device", A)
    first = asyncio.create_task(mgr.check_device(A))
    await asyncio.sleep(0.05)
    mgr.reserve_check("device", C)
    second = asyncio.create_task(mgr.check_device(C))
    await asyncio.sleep(0.05)
    gate.set()
    await first
    await second
    assert calls == 1
    assert mgr.devices[A]["status"] == mgr.devices[C]["status"] == manager_core.STATUS_ERROR
    assert mgr._provisions == {} and mgr._provision_waiters == {} and mgr._fetching == set()
    await mgr.check_device(A)
    assert calls == 2


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
    # Named in the entry and counted in the result, but no finding (severity unchanged)
    assert check["ignored"] == ["Kitchen renamed"] and check["result"].endswith(", 1 ignored")
    assert all(f.get("device") != "Kitchen renamed" for f in check["findings"])
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
    assert net.offer_calls == 2 and net.downloads == 1  # 1.4.0 and 1.5.0 installed
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


# ---------------------------------------------------------------- Gen2+: the server decides, the store serves


async def test_server_decides_and_a_stored_file_of_the_offered_version_is_used(mgr, net):
    """Gen2+: the server is asked per firmware ID and installed version; a stored file
    of exactly the offered version is used instead of a download – newer stored
    versions are not offered."""
    name, _ = store(mgr, "Plus1", "1.5.0")
    store(mgr, "Plus1", "1.7.0")  # newer, but not offered by the server
    assert await mgr.scan(user="u")
    assert net.offer_calls == 2 and net.downloads == 0  # installed 1.4.0 and 1.5.0
    dev = mgr.devices[A]
    assert dev["status"] == manager_core.STATUS_READY and dev["file"] == name and dev["pending_version"] == "1.5.0"
    assert dev["offer_source"] == "store" and dev["message"] == "Provided from the firmware store, not installed"
    assert mgr.devices[B]["status"] == manager_core.STATUS_CURRENT  # 1.5.0 installed, 1.5.0 offered
    entry = [e for e in mgr.events.entries if e["kind"] == "check"][-1]
    assert {f["source"] for f in entry["findings"]} == {"store"} and entry["updates"] == 2


async def test_uploaded_file_of_the_offered_version_is_used(mgr, net):
    name, sha = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD)
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert net.offer_calls == 1 and net.downloads == 0
    assert (dev["file"], dev["sha256"], dev["offer_source"]) == (name, sha, "upload") and "uploaded file" in dev["message"]
    assert mgr.public_state()["devices"][0]["offer_source"] == "upload"


async def test_download_is_preferred_over_an_upload_of_the_same_version(mgr, net):
    store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_UPLOAD, content=b"u" * 3000)
    name, _ = store(mgr, "Plus1", "1.5.0")
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.devices[A]["file"] == name and mgr.devices[A]["offer_source"] == "store"


async def test_installed_version_is_sent_and_intermediate_versions_are_marked(mgr, net, monkeypatch):
    """The next step of the server for the installed version is provided; below the
    newest version it is marked as intermediate version (target: the newest)."""
    sent = []
    fake = shelly.fetch_offer

    async def recording(session, fw_type, pins=None, version=None):
        sent.append((fw_type, version))
        return await fake(session, fw_type, pins, version)

    monkeypatch.setattr(shelly, "fetch_offer", recording)
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
    fake = shelly.fetch_offer

    async def steps(session, fw_type, pins=None, version=None):
        offer = await fake(session, fw_type, pins, version)
        return {**offer, "steps": "2"} if version == "1.4.0" else offer

    monkeypatch.setattr(shelly, "fetch_offer", steps)
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
    store(mgr, "Plus1", "1.6.0")
    original, hashed = shelly.file_sha256, []

    def counting(target_dir, name):
        hashed.append(name)
        return original(target_dir, name)

    monkeypatch.setattr(shelly, "file_sha256", counting)
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
    fake = shelly.fetch_offer

    async def failing_reference(session, fw_type, pins=None, version=None):
        if version == "1.0.7":
            raise ShellyError("502, 'Bad Gateway'")
        return await fake(session, fw_type, pins, version)

    monkeypatch.setattr(shelly, "fetch_offer", failing_reference)
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


async def test_offer_request_sends_only_app_and_version(monkeypatch):
    """The installed version goes in the header Shelly-Info – only "app" and "ver";
    without version no header (newest version). nsteps is returned as steps."""
    fake_cloud(monkeypatch, {
        "https://updates.shelly.cloud/update/Plus1": (200, {}, b'{"stable": {"url": "u", "version": "1.3.3", "nsteps": 2}}'),
    })
    headers = shelly._CloudRequest.headers_seen
    headers.clear()
    assert await shelly.fetch_offer(None, "Plus1", None, "1.0.7") == {"version": "1.3.3", "url": "u", "steps": "2"}
    assert await shelly.fetch_offer(None, "Plus1") == {"version": "1.3.3", "url": "u", "steps": "2"}
    assert headers == [{"Shelly-Info": '{"app":"Plus1","ver":"1.0.7"}'}, None]


def test_sha256_named_by_the_firmware_url():
    sha = "ddd5a7b49ff3e65240d1264eb531f82da2aa86d3d05d045c5226a81e7ea2e43d"
    assert shelly.url_sha256(f"https://fwcdn.shelly.cloud/gen2-ntest/Plus1/{sha}") == sha
    assert shelly.url_sha256(f"https://fwcdn.shelly.cloud/x/{sha.upper()}") == sha
    assert shelly.url_sha256("https://fw.example/Plus1.zip") is None
    assert shelly.url_sha256(f"https://fwcdn.shelly.cloud/{sha}/Plus1.zip") is None


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
    name, sha, size, https = await shelly.download_firmware(None, f"https://fwcdn.shelly.cloud/Plus1/{good}", tmp_path)
    assert sha == good and size == 5000 and https and (tmp_path / name).is_file()
    with pytest.raises(ShellyError, match="does not match the SHA-256"):
        await shelly.download_firmware(None, f"https://fwcdn.shelly.cloud/Plus1/{bad}", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]  # nothing else stored, no temp file
