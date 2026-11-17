"""Device settings and capabilities, password workflow, no secrets in state/logs.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import json

import data_paths
import pytest
import shelly
from conftest import A, B, C
from shelly import ShellyError


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


async def test_websocket_debug_read_and_write(device_api):
    """Gen2+ reads sys.debug.websocket.enable from Shelly.GetConfig and writes only that
    field via Sys.SetConfig; Gen1 has no websocket debug log (unsupported, write refused)."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"sys": {"debug": {"websocket": {"enable": True}, "mqtt": {"enable": False}}}}
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None)
    assert got["debug_ws"] is True
    answers["Shelly.GetConfig"] = {"sys": {}}
    assert (await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None))["debug_ws"] == "unsupported"
    calls.clear()
    await shelly.write_settings(None, 2, "http://1.2.3.4", {"debug_ws": False})
    assert calls == [("Sys.SetConfig", {"config": {"debug": {"websocket": {"enable": False}}}})]
    answers["/settings"] = {}
    assert (await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None))["debug_ws"] == "unsupported"
    with pytest.raises(ValueError, match="websocket debug"):
        await shelly.write_settings(None, 1, "http://1.2.3.4", {"debug_ws": True})
    with pytest.raises(ValueError, match="websocket debug"):
        shelly.validate_settings({"debug_ws": "on"}, [])


async def test_websocket_debug_change_is_read_back_and_logged(client, mgr, net, monkeypatch):
    """Turning the websocket debug log on: read back like the other settings, logged
    with old -> new, not_applied if the device keeps it off; public state shows it;
    refused for a device without it."""
    await mgr.scan()
    stored = {"debug_ws": False}
    written = []

    async def write(_session, _gen, _origin, changes, _auth=None):
        written.append(changes)
        return False

    async def read(*_args, **_kwargs):
        return {"shelly_name": "p", "ntp": None, "eco": False, "ap": False, "ble": False, "debug_ws": stored["debug_ws"], "outputs": []}

    monkeypatch.setattr(shelly, "write_settings", write)
    monkeypatch.setattr(shelly, "read_settings", read)
    mgr.devices[A]["debug_ws"] = False
    settings, _restart, verified, not_applied = await mgr.change_settings(A, {"debug_ws": True}, "u")
    assert written == [{"debug_ws": True}] and verified is True and not_applied == ["Websocket debug log"]
    assert settings["debug_ws"] is False and mgr.events.entries[-1]["ok"] is False
    stored["debug_ws"] = True
    _settings, _restart, _verified, not_applied = await mgr.change_settings(A, {"debug_ws": True}, "u")
    entry = mgr.events.entries[-1]
    assert not_applied == [] and entry["ok"] is True
    assert entry["changes"] == [{"setting": "Websocket debug log", "old": "off", "new": "on"}]
    public = next(d for d in mgr.public_state()["devices"] if d["id"] == A)
    assert public["debug_ws"] is True
    mgr.devices[A]["debug_ws"] = "unsupported"
    with pytest.raises(ValueError, match="websocket debug log"):
        await mgr.change_settings(A, {"debug_ws": False}, "u")


@pytest.mark.parametrize(
    ("fw_type", "mode", "key", "with_switch"),
    [
        ("SHDM-2", "white", "light:0", True),  # Dimmer reports mode "white", has only /settings/light/0
        ("SHDM-1", "white", "light:0", True),
        ("SHBDUO-1", "white", "light:0", False),
        ("SHVIN-1", "white", "light:0", False),
        ("SHRGBW2", "white", "white:0", False),
        ("SHRGBW2", "color", "color:0", False),
        ("SHBLB-1", "color", "color:0", False),
    ],
)
async def test_gen1_light_endpoint_by_type(device_api, fw_type, mode, key, with_switch):
    """Gen1 light outputs: color/white by mode only for RGBW2/Bulbs; Dimmer, Duo and
    Vintage report mode "white" but are read and written via /settings/light/<i>."""
    calls, answers = device_api
    answers["/settings"] = {"mode": mode, "lights": [{"default_state": "switch" if with_switch else "off"}]}
    got = await shelly.read_settings(None, 1, "http://1.2.3.4", fw_type, None)
    (output,) = got["outputs"]
    assert output["key"] == key and ("switch" in output["options"]) is with_switch
    calls.clear()
    await shelly.write_settings(None, 1, "http://1.2.3.4", {"aop": {key: "last"}})
    assert calls == [(f"/settings/{key.replace(':', '/')}", {"default_state": "last"})]


async def test_value_not_applied_is_reported_and_logged(client, mgr, net, monkeypatch):
    """The device accepts a change but reads back another value: not_applied names it,
    the activity log entry is failed; an applied change reports nothing."""
    await mgr.scan()
    stored = {"aop": "switch", "ntp": "time.local"}

    async def write(*_args, **_kwargs):
        return False

    async def read(*_args, **_kwargs):
        outputs = [{"key": "light:0", "name": "light:0", "aop": stored["aop"], "options": ["off", "on", "last", "switch"]}]
        return {"shelly_name": "dimmer", "ntp": stored["ntp"], "eco": False, "ap": False, "ble": "unsupported", "outputs": outputs}

    monkeypatch.setattr(shelly, "write_settings", write)
    monkeypatch.setattr(shelly, "read_settings", read)
    mgr.devices[A]["outputs"] = (await read())["outputs"]
    settings, restart, verified, not_applied = await mgr.change_settings(A, {"aop": {"light:0": "last"}}, "u")
    assert (restart, verified, not_applied) == (False, True, ["Power-on action light:0"])
    assert settings["outputs"][0]["aop"] == "switch"
    entry = mgr.events.entries[-1]
    assert entry["kind"] == "setting" and entry["ok"] is False and "Not applied by the Shelly" in entry["error"]
    stored["ntp"] = "pool.ntp.org"
    _settings, _restart, verified, not_applied = await mgr.change_settings(A, {"ntp": "pool.ntp.org"}, "u")
    assert (verified, not_applied) == (True, []) and mgr.events.entries[-1]["ok"] is True
    r = await client.post(f"/api/devices/{A}/settings", json={"aop": {"light:0": "off"}}, headers={"X-CSRF-Token": client.csrf})
    assert r.status == 200 and (await r.json())["not_applied"] == ["Power-on action light:0"]


async def test_gen2_identity_from_shelly_endpoint(device_api):
    """Complete /shelly answer: no second GetDeviceInfo request."""
    calls, answers = device_api
    answers["/shelly"] = {"id": "shellyplus1-a", "mac": "aa", "model": "SNSW", "gen": 2, "fw_id": "b", "ver": "1.4.4", "app": "Plus1", "auth_en": True}
    info = await shelly.device_info(None, "http://1.2.3.4")
    assert [c[0] for c in calls] == ["/shelly"] and info["auth_enabled"] is True and info["type"] == "Plus1"


@pytest.mark.parametrize(
    "answer",
    [
        {"gen": 2, "id": "x", "ver": "1.4.4", "fw_id": "b"},  # Gen2+ without "app"
        {"type": "SHSW-1", "mac": "aa"},  # Gen1 without "fw"
        {"gen": "two"},  # no number
        {"gen": {"x": 1}},  # wrong type
    ],
)
async def test_incomplete_identity_is_a_readable_device_error(device_api, answer):
    """A missing or wrongly typed field becomes a ShellyError with a readable
    message instead of a bare KeyError/ValueError text such as "'fw'"."""
    calls, answers = device_api
    answers["/shelly"] = answer
    answers["/rpc/Shelly.GetDeviceInfo"] = answer
    with pytest.raises(ShellyError, match="device information|Invalid value"):
        await shelly.device_info(None, "http://1.2.3.4")


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
    blobs = [json.dumps(mgr.events.entries), data_paths.STATE_FILE.read_text(), json.dumps(mgr.public_state())]
    assert not any("Very-Secret-1" in b or ha1 in b for b in blobs)


async def test_public_state_fields(mgr, net):
    """Schedule replaces the hourly interval; the shared-password flag is gone."""
    await mgr.scan()
    state = mgr.public_state()
    assert "interval" not in state and "shared_password_set" not in state
    assert state["schedule"]["days"] == 7 and state["schedule"]["timezone"] == "Europe/Berlin"
    dev = next(d for d in state["devices"] if d["id"] == C)
    assert dev["ap"] is False and dev["ble"] == "unsupported" and dev["ignored"] is False
