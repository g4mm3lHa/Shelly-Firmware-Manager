"""Beta firmware (option beta_firmware, Gen2+): which version is offered, how it is
marked, notified and withdrawn, and the extra confirmation of its installation.

All traffic is mocked (conftest.FakeNet)."""

import asyncio

import manager_core
import pytest
import shelly
from conftest import A, B, C
from helpers import make_manager


async def beta_manager(net, **options):
    """Manager with beta firmware switched on (registry and notifications faked)."""
    m = make_manager(net, **{"beta_firmware": True, **options})
    m.notified = []

    async def notify(*args):
        m.notified.append(args)
        return True

    async def registry():
        return net.registry()

    m.notify = notify
    m._registry = registry
    return m


def test_is_prerelease_and_upload_versions():
    """Beta and RC versions are pre-releases; stable versions and unknown text are not."""
    assert shelly.is_prerelease("1.5.0-beta2") and shelly.is_prerelease("1.5.0-rc1")
    assert not shelly.is_prerelease("1.5.0") and not shelly.is_prerelease("nonsense")


async def test_no_beta_while_the_option_is_off(mgr, net):
    """Option off: the server is asked without beta and the stable version is provided."""
    net.beta_version = "1.6.0-beta1"
    await mgr.scan()
    dev = mgr.devices[A]
    assert dev["pending_version"] == "1.5.0" and not dev.get("beta")
    assert not any(net.beta_requests)


async def test_app_log_names_offered_or_no_beta(net, caplog):
    """Option on: every checked Gen2+ device gets one app log line whether the server
    offered a beta version or none."""
    caplog.set_level("INFO", logger="sfm.manager")
    m = await beta_manager(net)
    await m.scan()
    assert "Kitchen: no beta version offered by the Shelly update server" in caplog.text
    caplog.clear()
    net.beta_version = "1.6.0-beta1"
    await m.check_device(A)
    assert "Kitchen: beta version 1.6.0-beta1 offered" in caplog.text


async def test_higher_beta_is_offered_and_marked(net):
    """Option on: a beta higher than the stable offer is provided, marked as beta, in
    its own section and never held back by the offer delay."""
    net.beta_version = "1.6.0-beta1"
    m = await beta_manager(net, offer_after_days=30)
    await m.scan()
    dev = m.devices[A]
    assert dev["pending_version"] == "1.6.0-beta1" and dev["beta"] is True
    assert dev["status"] == manager_core.STATUS_READY and "beta version" in dev["message"]
    assert manager_core.section_of(dev) == manager_core.SECTION_BETA
    state = {d["id"]: d for d in m.public_state()["devices"]}
    assert state[A]["beta"] is True and state[A]["section"] == "beta" and m.public_state()["beta_firmware"] is True
    assert any(net.beta_requests)
    # The download is logged as beta
    downloads = [e for e in m.events.entries if e["kind"] == "download"]
    assert downloads and downloads[-1].get("beta") is True


async def test_lower_beta_keeps_the_stable_offer(net):
    """A beta that is not higher than the stable offer is ignored."""
    net.beta_version = "1.5.0-beta3"
    m = await beta_manager(net)
    await m.scan()
    assert m.devices[A]["pending_version"] == "1.5.0" and not m.devices[A].get("beta")
    assert m.devices[B]["status"] == manager_core.STATUS_CURRENT and not m.devices[B].get("beta")


async def test_intermediate_version_comes_before_a_beta(net):
    """A required intermediate version is always provided first, never a beta."""
    net.steps = {"1.4.0": "1.4.5"}
    net.offer_version = "1.7.0"
    net.beta_version = "1.8.0-beta1"
    m = await beta_manager(net)
    await m.scan()
    dev = m.devices[A]
    assert dev["pending_version"] == "1.4.5" and dev["step_target"] == "1.7.0" and not dev.get("beta")


async def test_beta_from_the_answer_without_the_installed_version(net):
    """No beta in the answer for the installed version: the newest answer names it."""
    net.beta_version = "1.6.0-beta1"
    net.beta_newest_only = True
    m = await beta_manager(net)
    await m.scan()
    assert m.devices[A]["pending_version"] == "1.6.0-beta1" and m.devices[A]["beta"] is True


async def test_notification_counts_betas_separately(net):
    """An automatic check names beta versions separately and marks their lines."""
    net.beta_version = "1.6.0-beta1"
    m = await beta_manager(net)
    await m.scan(automatic=True)
    title, text, _id = m.notified[-1]
    assert title == "Shelly firmware: 3 new beta update(s) found"
    assert text.count("(beta)") == 3


async def test_beta_upload_only_while_the_option_is_on(net):
    """A manual upload may name a beta version only while the option is on."""
    from helpers import chunks, firmware_zip

    m = await beta_manager(net)
    await m.scan()
    m.beta_firmware = False
    data = firmware_zip()
    staged = await m.stage_upload(chunks(data), "fw.zip", len(data))
    with pytest.raises(ValueError):
        m.confirm_upload(staged["token"], "Plus1", "1.7.0-beta1")
    m.beta_firmware = True
    entry = m.confirm_upload(staged["token"], "Plus1", "1.7.0-beta1")
    assert entry["version"] == "1.7.0-beta1"
    with pytest.raises(ValueError):
        staged = await m.stage_upload(chunks(firmware_zip(size=6000)), "fw.zip", len(firmware_zip(size=6000)))
        m.confirm_upload(staged["token"], "Plus1", "1.7.0-dev1")


async def test_beta_is_withdrawn_when_the_option_is_off_at_start(net):
    """Option switched off: at the start a provided beta is withdrawn (Not checked),
    logged, and its scheduled installation lapses."""
    net.beta_version = "1.6.0-beta1"
    m = await beta_manager(net)
    await m.scan()
    assert m.devices[A]["beta"] is True
    m.devices[A]["planned"] = {"at": 2_000_000_000, "by": "alice", "on": 1, "sha256": m.devices[A]["sha256"],
                               "version": "1.4.0", "target": "1.6.0-beta1", "beta": True, "restart": False}
    m.save()
    off = await beta_manager(net, beta_firmware=False)
    await off.start_timetable()
    dev = off.devices[A]
    assert dev["status"] == manager_core.STATUS_SKIPPED and not dev.get("beta") and dev.get("file") is None
    assert "planned" not in dev and "withdrawn" in dev["message"]
    kinds = [(e["kind"], e.get("action") or e.get("event")) for e in off.events.entries]
    assert ("device", "beta_withdrawn") in kinds and ("install", "plan_lapsed") in kinds
    assert off.notified  # the lapsed plan is notified
    assert off.devices[C]["status"] == manager_core.STATUS_SKIPPED


async def test_http_beta_installation_needs_the_beta_tick(net):
    """The installation of a beta needs "beta_acknowledged" – also when scheduled."""
    from helpers import web_client

    net.beta_version = "1.6.0-beta1"
    m = await beta_manager(net)
    await m.scan()
    started = []

    async def install(device_id, sha, approved_by, downgrade=False, restart_first=False):
        started.append(device_id)
        return True

    m.install = install
    c = await web_client(m)
    try:
        sha, hdr = m.devices[A]["sha256"], {"X-CSRF-Token": c.csrf}
        r = await c.post(f"/api/devices/{A}/install", json={"sha256": sha, "confirm": True}, headers=hdr)
        assert r.status == 400 and not started
        body = {"sha256": sha, "confirm": True, "beta_acknowledged": True}
        r = await c.post(f"/api/devices/{A}/install", json=body, headers=hdr)
        assert r.status == 200
        await asyncio.sleep(0)
        assert started == [A]
    finally:
        await c.close()
