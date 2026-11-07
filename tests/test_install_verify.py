"""3.0 rc3: verification after the update command.

Success only for exactly the target version; a different (also newer) version is
"unconfirmed" with a hint; the early abort (reachable, old version, never
downloaded) happens only after 3 minutes. The timing constants are shortened in
the tests; their production values are checked separately."""

import manager
import pytest
import shelly
from conftest import A


def test_install_timing_constants():
    """Polling unchanged (30 s, every 10 s, max. 5 min); early abort after 3 min."""
    assert (manager.INSTALL_FIRST_CHECK_S, manager.INSTALL_POLL_S) == (30, 10)
    assert (manager.INSTALL_NO_DOWNLOAD_S, manager.INSTALL_WAIT_S) == (180, 300)


@pytest.fixture
def run_install(mgr, monkeypatch):
    """Run Manager._install against a device answering with the given versions."""
    monkeypatch.setattr(manager, "INSTALL_FIRST_CHECK_S", 0)
    monkeypatch.setattr(manager, "INSTALL_POLL_S", 0.01)
    monkeypatch.setattr(manager, "INSTALL_NO_DOWNLOAD_S", 0.08)
    monkeypatch.setattr(manager, "INSTALL_WAIT_S", 0.3)

    async def run(old, target, answers, gen=2, served=False):
        dev = {"name": "Kitchen", "gen": gen, "origin": "http://192.168.1.10", "mac": "AA", "file": "x.zip",
               "version": old, "pending_version": target, "pending_build": "b-old"}
        mgr.devices[A] = dev
        calls = []

        async def device_info(*_args, **_kwargs):
            if not calls:  # pre-check before the update command
                calls.append("pre")
                return {"mac": "AA", "build": "b-old", "version": old}
            calls.append("poll")
            if served:
                mgr.downloads["x.zip"] = {"served": 1, "rejected": set()}
            version = answers[min(len(calls) - 2, len(answers) - 1)]
            return {"mac": "AA", "build": "b-new", "version": version}

        async def trigger(*_args):
            return None

        monkeypatch.setattr(shelly, "file_sha256", lambda *_a: "s")
        monkeypatch.setattr(shelly, "device_info", device_info)
        monkeypatch.setattr(shelly, "trigger_update", trigger)
        ok, diagnosis = await mgr._install(A, dev, "s")
        return ok, diagnosis, dev, calls.count("poll")

    return run


async def test_exact_target_version_is_success(run_install):
    ok, diagnosis, dev, _ = await run_install("1.4.0", "1.5.0", ["1.4.0", "1.5.0"], served=True)
    assert ok and diagnosis is None and dev["status"] == manager.STATUS_CURRENT


async def test_gen1_compares_the_version_number(run_install):
    ok, *_ = await run_install("20230101-000000/v1.13.0-gaaaa", "20230913-112003/v1.14.0-gcb84623",
                               ["20230913-112003/v1.14.0-gcb84623"], gen=1, served=True)
    assert ok


async def test_newer_version_is_unconfirmed_with_hint(run_install):
    """A newer version than the target is no longer reported as success."""
    ok, diagnosis, dev, polls = await run_install("1.4.0", "1.5.0", ["1.6.0"])
    assert not ok and dev["status"] == manager.STATUS_UNCLEAR
    assert "1.6.0" in diagnosis and "1.5.0" in diagnosis
    assert polls > 10  # no early abort: waits until the upper limit


async def test_old_version_without_download_aborts_after_no_download_limit(run_install):
    ok, diagnosis, dev, polls = await run_install("1.4.0", "1.5.0", ["1.4.0"])
    assert not ok and "never requested" in diagnosis
    assert 3 <= polls < 15  # stopped at INSTALL_NO_DOWNLOAD_S, well before INSTALL_WAIT_S


async def test_old_version_after_download_keeps_waiting(run_install):
    ok, diagnosis, _dev, polls = await run_install("1.4.0", "1.5.0", ["1.4.0"], served=True)
    assert not ok and "downloaded the firmware" in diagnosis and polls > 10
