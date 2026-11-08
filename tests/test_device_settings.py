"""Device settings and capabilities, password workflow, no secrets in state/logs.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import json

import manager
import pytest
import shelly
from shelly import ShellyError

from conftest import A, B, C


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


@pytest.fixture
def device_api(monkeypatch):
    """Fake _rpc/_device_json recording the calls; answers from a dict."""
    calls, answers = [], {}

    async def rpc(_session, _origin, method, params=None, auth=None):
        calls.append((method, params))
        answer = answers.get(method, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def device_json(_session, _origin, path, auth=None, params=None):
        calls.append((path, params))
        answer = answers.get(path, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(shelly, "_rpc", rpc)
    monkeypatch.setattr(shelly, "_device_json", device_json)
    return calls, answers


async def test_gen2_capabilities(device_api):
    """AP/BLE switches, active webhooks/schedules; unsupported vs. unknown."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"sys": {"device": {"eco_mode": True}}, "wifi": {"ap": {"enable": True}}, "ble": {"enable": False}}
    answers["Webhook.List"] = {"hooks": [{"enable": False}, {"enable": True, "urls": ["http://x"]}], "rev": 1}
    answers["Schedule.List"] = shelly.UnsupportedError("no handler")
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert (got["eco"], got["ap"], got["ble"], got["actions"], got["schedules"]) == (True, True, False, True, "unsupported")
    assert "urls" not in json.dumps(got)
    answers["Shelly.GetConfig"] = {"sys": {}, "wifi": {"ap": {"enable": False}}, "ble": {"rpc": {"enable": True}}}
    answers["Schedule.List"] = TimeoutError()
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert got["ble"] == "unsupported" and got["eco"] == "unsupported" and got["schedules"] is None
    calls.clear()
    await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None)
    assert [c[0] for c in calls] == ["Shelly.GetConfig"]  # dialogs: one request


async def test_gen1_capabilities(device_api):
    """Gen1: AP read-only, schedules from /settings, actions need urls."""
    _calls, answers = device_api
    answers["/settings"] = {"wifi_ap": {"enabled": False}, "relays": [{"default_state": "off", "schedule": True, "schedule_rules": ["0800-0123456-on"]}]}
    answers["/settings/actions"] = {"actions": {"out_on_url": [{"index": 0, "enabled": True, "urls": []}]}}
    got = await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None, extended=True)
    assert (got["ap"], got["ble"], got["schedules"], got["actions"]) == (False, "unsupported", True, False)


async def test_ap_and_ble_writes(device_api):
    """Gen2 AP sends only ap.enable; Gen1 AP and BLE are refused."""
    calls, _answers = device_api
    await shelly.write_settings(None, 2, "http://1.2.3.4", {"ap": False, "ble": True})
    assert calls == [("Wifi.SetConfig", {"config": {"ap": {"enable": False}}}), ("BLE.SetConfig", {"config": {"enable": True}})]
    with pytest.raises(ValueError):
        await shelly.write_settings(None, 1, "http://1.2.3.4", {"ap": True})


async def test_gen2_identity_from_shelly_endpoint(device_api):
    """Complete /shelly answer: no second GetDeviceInfo request."""
    calls, answers = device_api
    answers["/shelly"] = {"id": "shellyplus1-a", "mac": "aa", "model": "SNSW", "gen": 2, "fw_id": "b", "ver": "1.4.4", "app": "Plus1", "auth_en": True}
    info = await shelly.device_info(None, "http://1.2.3.4")
    assert [c[0] for c in calls] == ["/shelly"] and info["auth_enabled"] is True and info["type"] == "Plus1"


async def test_gen1_password_errors_never_contain_the_url(monkeypatch):
    """Network errors of /settings/login are re-raised without URL/query."""
    import aiohttp

    async def failing(*_args, **_kwargs):
        raise aiohttp.InvalidURL("http://1.2.3.4/settings/login?password=topsecret")

    monkeypatch.setattr(shelly, "_device_json", failing)
    with pytest.raises(shelly.ShellyError) as err:
        await shelly.set_protection(None, 1, "http://1.2.3.4", None, shelly.DeviceAuth(gen=1, password="topsecret"))
    assert "topsecret" not in str(err.value)


@pytest.fixture
async def device(mgr, net, monkeypatch):
    """Checked Gen2 device A with a fake protection state on the device."""
    await mgr.scan()
    box = {"protected": False, "ha1": None, "fail_write": None, "apply": True, "writes": 0}

    async def status(_session, _origin):
        return box["protected"]

    async def protect(_session, gen, _origin, realm, new_auth, auth=None):
        box["writes"] += 1
        if box["apply"]:
            box["protected"], box["ha1"] = (True, new_auth.ha1) if new_auth else (False, None)
        if box["fail_write"]:
            raise box["fail_write"]

    async def read(_session, gen, _origin, _type, auth=None, extended=False):
        if box["protected"] and (auth is None or auth.ha1 != box["ha1"]):
            raise shelly.AuthError("The Shelly rejected the password")
        return {}

    monkeypatch.setattr(shelly, "protection_status", status)
    monkeypatch.setattr(shelly, "set_protection", protect)
    monkeypatch.setattr(shelly, "read_settings", read)
    return box


async def test_enable_protection_verified_and_confirmed(mgr, device):
    """Confirmation and acknowledgment are required; success only after verification."""
    with pytest.raises(ValueError):
        await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=False)
    assert device["writes"] == 0
    result = await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True, user="u")
    assert result == {"protected": True} and mgr.credentials.has_device_password(A)
    assert mgr.events.entries[-1]["action"] == "enabled"


async def test_enable_with_ambiguous_answer(mgr, device):
    """Lost answer but the device is protected with the new password: success."""
    device["fail_write"] = TimeoutError()
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    assert mgr.credentials.has_device_password(A)


async def test_change_failure_keeps_previous_credential(mgr, device):
    """Device did not apply the change: old password kept, error logged."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    old = dict(mgr.credentials.entries[A])
    device["apply"] = False
    with pytest.raises(shelly.ShellyError, match="previous password still works"):
        await mgr.set_device_password(A, "change", "pw-2", confirm=True, acknowledged=True)
    assert mgr.credentials.entries[A] == old
    assert mgr.events.entries[-1]["severity"] == "error"


async def test_remove_protection_deletes_only_after_success(mgr, device):
    """Credentials are deleted only when the device reports no protection."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    device["apply"] = False
    with pytest.raises(shelly.ShellyError):
        await mgr.set_device_password(A, "remove", confirm=True)
    assert mgr.credentials.has_device_password(A)
    device["apply"] = True
    assert await mgr.set_device_password(A, "remove", confirm=True) == {"protected": False}
    assert not mgr.credentials.has_device_password(A)


async def test_store_requires_protected_device(mgr, device):
    """Storing on an unprotected device proves nothing and is refused."""
    with pytest.raises(shelly.ShellyError, match="not password protected"):
        await mgr.set_device_password(A, "store", "pw", confirm=True)
    device["protected"], device["ha1"] = True, shelly.gen2_ha1(mgr.devices[A]["shelly_id"], "right")
    with pytest.raises(shelly.AuthError):
        await mgr.set_device_password(A, "store", "wrong", confirm=True)
    await mgr.set_device_password(A, "store", "right", confirm=True)
    assert mgr.credentials.has_device_password(A)


async def test_secrets_never_in_state_or_logs(mgr, device):
    """Neither password nor HA1 appear in the log, state file or public state."""
    await mgr.set_device_password(A, "enable", "Very-Secret-1", confirm=True, acknowledged=True)
    ha1 = mgr.credentials.entries[A]["ha1"]
    blobs = [json.dumps(mgr.events.entries), manager.STATE_FILE.read_text(), json.dumps(mgr.public_state())]
    assert not any("Very-Secret-1" in b or ha1 in b for b in blobs)


async def test_public_state_fields(mgr, net):
    """Schedule replaces the hourly interval; the shared-password flag is gone."""
    await mgr.scan()
    state = mgr.public_state()
    assert "interval" not in state and "shared_password_set" not in state
    assert state["schedule"]["days"] == 7 and state["schedule"]["timezone"] == "Europe/Berlin"
    dev = next(d for d in state["devices"] if d["id"] == C)
    assert dev["ap"] is False and dev["ble"] == "unsupported" and dev["ignored"] is False
