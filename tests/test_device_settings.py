"""Device settings and capabilities, password workflow, no secrets in state/logs.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import json
import re

import data_paths
import pytest
import shelly
import shelly_cloud
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


async def test_settings_refused_while_full_check_waits_for_the_server(mgr, net, monkeypatch):
    """A full check (no single-device reservation) blocks settings, restart and password of
    a device also while its check waits for the Shelly update server ("server")."""
    await mgr.scan()
    server = asyncio.Event()
    original = shelly_cloud.fetch_offer

    async def fetch_offer(*args, **kwargs):
        await server.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(shelly_cloud, "fetch_offer", fetch_offer)
    task = asyncio.create_task(mgr.scan())
    for _ in range(100):
        if mgr.activity.get(B) == "server":
            break
        await asyncio.sleep(0.01)
    assert mgr.activity.get(B) == "server" and mgr.device_checks == {}
    for action in (
        lambda: mgr.change_settings(B, {"eco": True}, "u"),
        lambda: mgr.restart_device(B),
        lambda: mgr.set_device_password(B, "remove", confirm=True),
    ):
        with pytest.raises(ShellyError, match="being checked"):
            await action()
    server.set()
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


async def test_battery_device_detected_from_the_device(device_api):
    """A battery device is detected only from its own answer: Gen2+ wakeup_period above
    0 in the sys status (extended reads only: Sys.GetStatus, with the cloud on
    Shelly.GetStatus, see test_gen2_sys_and_cloud_status_in_one_request) or in the sys
    config, Gen1 sleep_mode.period above 0; missing or 0 means no battery device. An
    unreadable sys status leaves the flag out (the stored value stays)."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"sys": {"device": {"name": "smoke"}}}
    answers["Sys.GetStatus"] = {"wakeup_period": 86400, "wakeup_reason": {"boot": "deepsleep_wake"}}
    assert (await shelly.read_settings(None, 2, "http://1.2.3.4", "PlusSmoke", None, extended=True))["battery"] is True
    calls.clear()
    assert "battery" not in await shelly.read_settings(None, 2, "http://1.2.3.4", "PlusSmoke", None)
    assert ("Sys.GetStatus", None) not in calls   # no extra request outside checks and reloads
    for status in ({"wakeup_period": 0}, {}, {"wakeup_period": True}):
        answers["Sys.GetStatus"] = status
        assert (await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True))["battery"] is False
    answers["Sys.GetStatus"] = shelly.ShellyError("busy")
    assert "battery" not in await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    answers["Shelly.GetConfig"] = {"sys": {"wakeup_period": 7200}}
    assert (await shelly.read_settings(None, 2, "http://1.2.3.4", "HTG3", None))["battery"] is True
    answers["/settings"] = {"sleep_mode": {"period": 6, "unit": "h"}}
    assert (await shelly.read_settings(None, 1, "http://1.2.3.4", "SHHT-1", None))["battery"] is True
    answers["/settings"] = {}
    assert (await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None))["battery"] is False


async def test_gen2_sys_and_cloud_status_in_one_request(device_api):
    """Extended read with the cloud on and no wakeup_period in the sys config: battery flag
    and cloud connection come from one Shelly.GetStatus (no Cloud.GetStatus, no
    Sys.GetStatus, also none after a failure); only these two values are kept, never
    sys.available_updates or other status data (the update server decides). Not extended
    or with wakeup_period in the sys config, Cloud.GetStatus alone as before."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"cloud": {"enable": True}, "sys": {}}
    answers["Shelly.GetStatus"] = {
        "sys": {"wakeup_period": 86400, "uptime": 1234, "restart_required": True,
                "available_updates": {"stable": {"version": "9.9.9"}, "beta": {"version": "9.9.10-beta1"}}},
        "cloud": {"connected": True},
        "wifi": {"sta_ip": "192.168.1.10", "status": "got ip", "ssid": "HomeNet", "rssi": -60},
    }
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "PlusSmoke", None, extended=True)
    assert (got["battery"], got["cloud_connected"]) == (True, True)
    assert [c[0] for c in calls] == ["Shelly.GetConfig", "Shelly.GetStatus", "Webhook.List", "Schedule.List"]
    dumped = json.dumps(got)
    for leaked in ("available_updates", "9.9.9", "restart_required", "uptime", "1234", "HomeNet", "192.168.1.10"):
        assert leaked not in dumped
    calls.clear()
    answers["Shelly.GetStatus"] = ShellyError("busy")
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "PlusSmoke", None, extended=True)
    assert "battery" not in got and got["cloud_connected"] is None
    assert [c[0] for c in calls] == ["Shelly.GetConfig", "Shelly.GetStatus", "Webhook.List", "Schedule.List"]
    answers["Shelly.GetStatus"] = shelly.UnsupportedError("no handler")
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert "battery" not in got and got["cloud_connected"] is None
    answers["Shelly.GetStatus"] = {"sys": {"wakeup_period": 0}}
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert (got["battery"], got["cloud_connected"]) == (False, None)
    answers["Shelly.GetStatus"] = {"sys": None, "cloud": {"connected": False}}
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None, extended=True)
    assert "battery" not in got and got["cloud_connected"] is False
    answers["Cloud.GetStatus"] = {"connected": True}
    calls.clear()
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None)
    assert got["cloud_connected"] is True and [c[0] for c in calls] == ["Shelly.GetConfig", "Cloud.GetStatus"]
    answers["Shelly.GetConfig"] = {"cloud": {"enable": True}, "sys": {"wakeup_period": 7200}}
    calls.clear()
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "HTG3", None, extended=True)
    assert (got["battery"], got["cloud_connected"]) == (True, True)
    assert [c[0] for c in calls] == ["Shelly.GetConfig", "Cloud.GetStatus", "Webhook.List", "Schedule.List"]


async def test_gen1_device_id_from_the_settings(device_api):
    """Gen1 reports its device ID (also its mDNS host name) in /settings device.hostname."""
    _calls, answers = device_api
    answers["/settings"] = {"device": {"type": "SHSW-1", "hostname": "shelly1-B929CC"}}
    assert (await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None))["gen1_hostname"] == "shelly1-B929CC"
    answers["/settings"] = {}
    assert (await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None))["gen1_hostname"] is None


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


def challenge_realm(box):
    """Realm a fake protected device challenges with: its auth domain, else its ID."""
    return box["auth_domain"] or box["device_id"]


def fake_read_refused(box, auth):
    """Error of an authenticated read on a fake device, None if it is accepted. A stored
    HA1 of another realm is refused like DeviceAuth does before anything is sent."""
    if not box["protected"]:
        return None
    if auth is None or auth.ha1 != box["ha1"]:
        return shelly.AuthError("The Shelly rejected the password")
    if auth.realm != challenge_realm(box):
        return shelly.ShellyError("Stored device password belongs to another device ID – enter it again")
    return None


def fake_set_auth(box, realm, new_auth):
    """Shelly.SetAuth on a fake device: the realm becomes its auth domain."""
    if new_auth:
        assert new_auth.gen == 1 or new_auth.realm == realm  # the HA1 is derived from the realm sent
        box.update(protected=True, ha1=new_auth.ha1, auth_domain=realm)
    else:
        box.update(protected=False, ha1=None, auth_domain=None)


@pytest.fixture
async def device(mgr, net, monkeypatch):
    """Checked Gen2 device A with a fake protection state on the device: device_id is the
    ID its /shelly answer reports, auth_domain the realm its password is bound to (None
    while unprotected), realms the realms Shelly.SetAuth received."""
    await mgr.scan()
    box = {"protected": False, "ha1": None, "fail_write": None, "apply": True, "writes": 0,
           "device_id": mgr.devices[A]["shelly_id"], "auth_domain": None, "realms": []}

    async def status(_session, _origin):
        domain = box["auth_domain"] if box["protected"] else None
        return shelly.Protection(box["protected"], box["device_id"], domain)

    async def protect(_session, gen, _origin, realm, new_auth, auth=None):
        box["writes"] += 1
        box["realms"].append(realm)
        if box["apply"]:
            fake_set_auth(box, realm, new_auth)
        if box["fail_write"]:
            raise box["fail_write"]

    async def read(_session, gen, _origin, _type, auth=None, extended=False):
        if error := fake_read_refused(box, auth):
            raise error
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
    assert device["realms"] == [mgr.devices[A]["shelly_id"]] == [mgr.credentials.entries[A]["realm"]]
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


BLOCKED = "The Shelly temporarily blocks sign-ins (too many attempts) – wait a few minutes and try again"


def counted_reads(monkeypatch, blocked=False):
    """Count the verification reads of the device fixture; blocked: every read raises the
    sign-in block instead."""
    calls = []
    fake = shelly.read_settings

    async def read(*args, **kwargs):
        calls.append(1)
        if blocked:
            raise shelly.AuthBlockedError(BLOCKED)
        return await fake(*args, **kwargs)

    monkeypatch.setattr(shelly, "read_settings", read)
    return calls


@pytest.mark.parametrize("mode", ["change", "remove"])
async def test_refused_set_auth_is_not_verified(mgr, device, monkeypatch, mode):
    """Gen2+: Shelly.SetAuth refused with the stored current password was not executed –
    no verification reads (each would be one more failed sign-in), a text of its own, the
    stored password stays."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    old = dict(mgr.credentials.entries[A])
    device.update(apply=False, fail_write=shelly.AuthError("The Shelly rejected the password"))
    reads = counted_reads(monkeypatch)
    with pytest.raises(shelly.AuthError, match="^The Shelly rejected the stored current password – nothing was changed$"):
        await mgr.set_device_password(A, mode, "pw-2", confirm=True, acknowledged=True)
    assert reads == [] and device["writes"] == 2 and mgr.credentials.entries[A] == old


async def test_blocked_set_auth_is_not_verified(mgr, device, monkeypatch):
    """Gen2+: Shelly.SetAuth answered with the sign-in block: the block text, no reads."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    old = dict(mgr.credentials.entries[A])
    device.update(apply=False, fail_write=shelly.AuthBlockedError(BLOCKED))
    reads = counted_reads(monkeypatch)
    with pytest.raises(shelly.AuthBlockedError, match="temporarily blocks sign-ins"):
        await mgr.set_device_password(A, "change", "pw-2", confirm=True, acknowledged=True)
    assert reads == [] and mgr.credentials.entries[A] == old


async def test_blocked_verification_is_not_a_rejected_password(mgr, device, monkeypatch):
    """A sign-in block during the verification says nothing about the password: "store"
    shows the block text; "enable" keeps the stored password and names the block, with no
    second verification read."""
    device["protected"] = True
    reads = counted_reads(monkeypatch, blocked=True)
    with pytest.raises(shelly.AuthBlockedError, match="temporarily blocks sign-ins"):
        await mgr.set_device_password(A, "store", "pw-1", confirm=True)
    assert not mgr.credentials.has_device_password(A) and reads == [1]
    device["protected"] = False
    with pytest.raises(shelly.ShellyError) as err:
        await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True)
    assert str(err.value) == f"The new password could not be confirmed – the previously stored password was kept ({BLOCKED})"
    assert not isinstance(err.value, shelly.AuthError) and not mgr.credentials.has_device_password(A) and reads == [1, 1]


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


async def test_enable_and_change_without_storing(mgr, device):
    """keep=False: the new password is verified on the device but not stored; a change
    without storing deletes the stored password only after the device confirmed it."""
    await mgr.set_device_password(A, "enable", "pw-1", confirm=True, acknowledged=True, keep=False)
    assert device["protected"] and not mgr.credentials.has_device_password(A)
    assert mgr.events.entries[-1]["action"] == "enabled" and mgr.events.entries[-1]["stored"] is False
    await mgr.set_device_password(A, "store", "pw-1", confirm=True)
    old = dict(mgr.credentials.entries[A])
    device["apply"] = False
    with pytest.raises(shelly.ShellyError):
        await mgr.set_device_password(A, "change", "pw-2", confirm=True, acknowledged=True, keep=False)
    assert mgr.credentials.entries[A] == old   # not confirmed: stored password kept
    device["apply"] = True
    await mgr.set_device_password(A, "change", "pw-2", confirm=True, acknowledged=True, keep=False)
    assert not mgr.credentials.has_device_password(A)
    for mode in ("store", "remove", "forget"):   # only enable/change can skip storing
        with pytest.raises(ValueError, match="storage choice"):
            await mgr.set_device_password(A, mode, "pw-2", confirm=True, keep=False)
    with pytest.raises(ValueError, match="storage choice"):
        await mgr.set_device_password(A, "enable", "pw-3", confirm=True, acknowledged=True, keep="no")


async def test_store_requires_protected_device(mgr, device):
    """Storing on an unprotected device proves nothing and is refused."""
    with pytest.raises(shelly.ShellyError, match="not password protected"):
        await mgr.set_device_password(A, "store", "pw", confirm=True)
    device["protected"], device["ha1"] = True, shelly.gen2_ha1(mgr.devices[A]["shelly_id"], "right")
    with pytest.raises(shelly.AuthError):
        await mgr.set_device_password(A, "store", "wrong", confirm=True)
    await mgr.set_device_password(A, "store", "right", confirm=True)
    assert mgr.credentials.has_device_password(A)


def former_id(device_id, name="shellymini1pmg3"):
    """The 2.2.0 form of a device ID: another name before the same MAC suffix."""
    return f"{name}-{device_id.rpartition('-')[2]}"


async def test_store_uses_auth_domain_after_id_change(mgr, device, caplog):
    """A password set under the renamed 2.2.0 ID stays bound to it (auth_domain differs
    from id): "store" tests and stores it against that realm, says so once in the log
    (no ID) and leaves the device untouched."""
    sid = mgr.devices[A]["shelly_id"]
    former = former_id(sid)
    device.update(protected=True, auth_domain=former, ha1=shelly.gen2_ha1(former, "right"))
    with caplog.at_level("INFO", logger="sfm.manager"):
        result = await mgr.set_device_password(A, "store", "right", confirm=True, user="u")
    assert result == {"protected": True, "realm_outdated": True}
    assert mgr.credentials.entries[A]["realm"] == former and device["writes"] == 0
    assert mgr.events.entries[-1]["action"] == "stored" and mgr.devices[A]["shelly_id"] == sid
    lines = [r.getMessage() for r in caplog.records if "former device ID" in r.getMessage()]
    assert lines == [
        "Kitchen: stored password is bound to the former device ID of the Shelly – change it once to fix this"
    ]
    blobs = [json.dumps(mgr.events.entries), json.dumps(mgr.public_state()), caplog.text]
    assert not any(former in b or device["ha1"] in b for b in blobs)


async def test_change_binds_password_to_device_id(mgr, device):
    """Change password (the same one is fine) after a store with the former realm:
    Shelly.SetAuth gets the device ID, the HA1 is derived from it and the stored entry
    moves to it; the stored former HA1 still signs the change."""
    sid = mgr.devices[A]["shelly_id"]
    former = former_id(sid)
    device.update(protected=True, auth_domain=former, ha1=shelly.gen2_ha1(former, "right"))
    await mgr.set_device_password(A, "store", "right", confirm=True)
    result = await mgr.set_device_password(A, "change", "right", confirm=True, acknowledged=True)
    assert result == {"protected": True} and device["realms"] == [sid] and device["auth_domain"] == sid
    assert mgr.credentials.entries[A]["realm"] == sid and device["ha1"] == shelly.gen2_ha1(sid, "right")
    assert await mgr.set_device_password(A, "store", "right", confirm=True) == {"protected": True}


async def test_live_device_id_replaces_an_outdated_one(mgr, device):
    """The device ID of the live /shelly answer is the SetAuth realm: a known ID from
    before the update (here the 2.2.0 one) is replaced, so a change really moves the
    password to the restored ID. An answer without ID keeps the known one."""
    sid = mgr.devices[A]["shelly_id"]
    former = former_id(sid)
    device.update(protected=True, auth_domain=former, ha1=shelly.gen2_ha1(former, "right"))
    await mgr.set_device_password(A, "store", "right", confirm=True)
    mgr.devices[A]["shelly_id"] = former   # known ID from a check under 2.2.0
    await mgr.set_device_password(A, "change", "new-pw", confirm=True, acknowledged=True)
    assert device["realms"] == [sid] and mgr.devices[A]["shelly_id"] == sid
    device["device_id"] = None
    await mgr.set_device_password(A, "store", "new-pw", confirm=True)
    assert mgr.devices[A]["shelly_id"] == sid and mgr.credentials.entries[A]["realm"] == sid


@pytest.mark.parametrize("domain", [
    "shellymini1pmg3-ffffffffffff",     # another MAC suffix: another device
    "shelly mini-{suffix}",            # a character a realm never has
    "a" * 32 + "-{suffix}",            # 65 characters: one over 64 (never shortened to fit)
    "-{suffix}",                       # no name before the suffix
    "{suffix}",                        # no hyphen at all
])
async def test_foreign_auth_domain_is_ignored(mgr, device, domain):
    """Only an auth domain of 1-64 letters, digits or hyphens with the device's MAC
    suffix is tried; any other one leaves the device ID as realm, so the password of a
    device bound to it is rejected and nothing is stored."""
    sid = mgr.devices[A]["shelly_id"]
    domain = domain.format(suffix=sid.rpartition("-")[2])
    device.update(protected=True, auth_domain=domain, ha1=shelly.gen2_ha1(domain, "right"))
    with pytest.raises(shelly.AuthError, match="^The Shelly rejected the password$"):
        await mgr.set_device_password(A, "store", "right", confirm=True)
    assert not mgr.credentials.has_device_password(A)


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


@pytest.fixture
async def several(mgr, net, monkeypatch):
    """Checked devices A, B, C, each with its own fake protection state."""
    await mgr.scan()
    boxes = {
        f"http://{d[1]}": {"protected": False, "ha1": None, "fail": None, "auth_domain": None,
                           "device_id": mgr.devices[i]["shelly_id"]}
        for i, d in net.devices.items()
    }
    running = {"now": 0, "max": 0}

    async def status(_session, origin):
        box = boxes[origin]
        return shelly.Protection(box["protected"], box["device_id"], box["auth_domain"] if box["protected"] else None)

    async def protect(_session, gen, origin, realm, new_auth, auth=None):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.02)
        running["now"] -= 1
        box = boxes[origin]
        if box["fail"]:
            raise box["fail"]
        fake_set_auth(box, realm, new_auth)

    async def read(_session, gen, origin, _type, auth=None, extended=False):
        if error := fake_read_refused(boxes[origin], auth):
            raise error
        return {}

    monkeypatch.setattr(shelly, "protection_status", status)
    monkeypatch.setattr(shelly, "set_protection", protect)
    monkeypatch.setattr(shelly, "read_settings", read)
    for dev_id in (A, B, C):
        mgr.devices[dev_id]["auth_enabled"] = False
    return {"boxes": boxes, "running": running, "origin": {i: f"http://{d[1]}" for i, d in net.devices.items()}}


async def test_bulk_set_enables_and_changes_per_device(mgr, several):
    """One password for several devices: unprotected ones get protection, protected ones
    with a stored password get the new one; each device is verified, stored and logged
    on its own; parallel up to max_parallel_installs."""
    await mgr.set_device_password(B, "enable", "old-pw", confirm=True, acknowledged=True)
    assert mgr.bulk_password_step(A, "set") == ("enable", "")
    assert mgr.bulk_password_step(B, "set") == ("change", "")
    results = await mgr.set_passwords("set", [A, B, C], "New-Pw-1", confirm=True, acknowledged=True, user="u")
    assert {r["id"]: (r["result"], r["action"]) for r in results} == {
        A: ("ok", "enable"), B: ("ok", "change"), C: ("ok", "enable"),
    }
    assert all(mgr.credentials.has_device_password(i) for i in (A, B, C))
    assert several["running"]["max"] == mgr.options["max_parallel_installs"] == 2
    actions = [e["action"] for e in mgr.events.entries if e["kind"] == "password"]
    assert sorted(actions[-3:]) == ["changed", "enabled", "enabled"]


async def test_bulk_skips_unfit_devices_and_continues_after_errors(mgr, several):
    """Protected without stored password and unknown state: skipped with a reason; a
    failing device does not stop the others."""
    several["boxes"][several["origin"][B]].update(protected=True, ha1="x")
    mgr.devices[B]["auth_enabled"] = True
    mgr.devices[C]["auth_enabled"] = None
    assert mgr.bulk_password_step(B, "set") == (None, "Store the current password first")
    assert mgr.bulk_password_step(C, "set")[0] is None
    assert mgr.bulk_password_step(A, "store") == (None, "Not password protected")
    several["boxes"][several["origin"][A]]["fail"] = shelly.ShellyError("Device refused")
    results = {r["id"]: r for r in await mgr.set_passwords(
        "set", [A, B, C], "New-Pw-1", confirm=True, acknowledged=True)}
    assert results[A]["result"] == "error" and "refused" in results[A]["message"]
    assert results[B]["result"] == "skipped" and results[C]["result"] == "skipped"
    assert not any(mgr.credentials.has_device_password(i) for i in (A, B, C))


async def test_bulk_store_existing_password(mgr, several):
    """Store: the existing password is verified per device; a wrong one is an error."""
    for dev_id in (A, B):
        mgr.devices[dev_id]["auth_enabled"] = True
        several["boxes"][several["origin"][dev_id]].update(
            protected=True, ha1=shelly.gen2_ha1(mgr.devices[dev_id]["shelly_id"], "shared"))
    several["boxes"][several["origin"][B]]["ha1"] = "other"
    results = {r["id"]: r["result"] for r in await mgr.set_passwords("store", [A, B, C], "shared", confirm=True)}
    assert results == {A: "ok", B: "error", C: "skipped"}
    assert mgr.credentials.has_device_password(A) and not mgr.credentials.has_device_password(B)


async def test_bulk_store_after_id_change(mgr, several):
    """Store on several devices also tests a password bound to the former 2.2.0 ID
    against that realm."""
    mgr.devices[A]["auth_enabled"] = True
    former = former_id(mgr.devices[A]["shelly_id"])
    several["boxes"][several["origin"][A]].update(
        protected=True, auth_domain=former, ha1=shelly.gen2_ha1(former, "shared"))
    results = await mgr.set_passwords("store", [A], "shared", confirm=True)
    assert [r["result"] for r in results] == ["ok"] and mgr.credentials.entries[A]["realm"] == former


async def test_bulk_validation(mgr, several):
    """Mode, confirmations, password, selection and storage choice are checked."""
    good = {"confirm": True, "acknowledged": True}
    for args, kwargs, match in (
        (("remove", [A], "pw"), good, "Unknown password action"),
        (("set", [A], "pw"), {"confirm": True}, "stored securely"),
        (("set", [A], "pw"), {"acknowledged": True}, "Confirmation"),
        (("set", [A], ""), good, "No password"),
        (("set", [], "pw"), good, "at least one"),
        (("set", [A, "x"], "pw"), good, "Unknown device"),
        (("store", [A], "pw"), {"confirm": True, "keep": False}, "storage choice"),
    ):
        with pytest.raises(ValueError, match=match):
            await mgr.set_passwords(*args, **kwargs)
    results = await mgr.set_passwords("set", [A, A], "pw", keep=False, **good)
    assert len(results) == 1 and results[0]["result"] == "ok" and not mgr.credentials.has_device_password(A)


async def test_gen2_cloud_and_bluetooth_proxy(device_api):
    """Cloud switch (Shelly.GetConfig) and its connection (Cloud.GetStatus, only while
    on; extended reads may take it from Shelly.GetStatus, see
    test_gen2_sys_and_cloud_status_in_one_request); Home Assistant's Bluetooth proxy
    script (Script.List, only while Bluetooth is on: running script with exactly that
    name)."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"cloud": {"enable": True}, "ble": {"enable": True}}
    answers["Cloud.GetStatus"] = {"connected": False}
    answers["Script.List"] = {"scripts": [{"id": 1, "name": "aioshelly_ble_integration", "enable": True, "running": True}]}
    result = await shelly.read_settings(None, 2, "http://192.168.1.10", "Plus1")
    assert (result["cloud"], result["cloud_connected"], result["ble_proxy"]) == (True, False, True)
    answers["Script.List"] = {"scripts": [{"id": 1, "name": "aioshelly_ble_integration", "running": False}]}
    assert (await shelly.read_settings(None, 2, "http://192.168.1.10", "Plus1"))["ble_proxy"] is False
    calls.clear()
    answers["Shelly.GetConfig"] = {"cloud": {"enable": False}, "ble": {"enable": False}}
    result = await shelly.read_settings(None, 2, "http://192.168.1.10", "Plus1")
    assert (result["cloud"], result["cloud_connected"], result["ble_proxy"]) == (False, None, False)
    assert [c[0] for c in calls] == ["Shelly.GetConfig"]  # no further request while both are off
    answers["Shelly.GetConfig"] = {}
    assert (await shelly.read_settings(None, 2, "http://192.168.1.10", "Plus1"))["cloud"] == shelly.UNSUPPORTED


async def test_gen1_cloud_from_the_settings(device_api):
    """Gen1: cloud and its connection come with /settings; the switch is
    /settings/cloud?enabled=1|0."""
    calls, answers = device_api
    answers["/settings"] = {"cloud": {"enabled": True, "connected": True}}
    result = await shelly.read_settings(None, 1, "http://192.168.1.10", "SHSW-1")
    assert (result["cloud"], result["cloud_connected"], result["ble_proxy"]) == (True, True, False)
    await shelly.write_settings(None, 1, "http://192.168.1.10", {"cloud": False})
    assert ("/settings/cloud", {"enabled": "0"}) in calls


async def test_gen2_cloud_switch_changes_only_enable(device_api):
    """Cloud.SetConfig with config.enable only; a restart request of the device is passed on."""
    calls, answers = device_api
    answers["Cloud.SetConfig"] = {"restart_required": True}
    assert await shelly.write_settings(None, 2, "http://192.168.1.10", {"cloud": True}) is True
    assert calls == [("Cloud.SetConfig", {"config": {"enable": True}})]
    with pytest.raises(ValueError):
        shelly.validate_settings({"cloud": "on"}, [])


async def test_cloud_change_is_logged_and_read_back(mgr, net, monkeypatch):
    """The cloud switch goes through change_settings: refused if the device has none,
    logged with old and new value."""
    await mgr.scan()
    writes = []

    async def write(_session, _gen, _origin, changes, _auth=None):
        writes.append(changes)
        return False

    monkeypatch.setattr(shelly, "write_settings", write)
    mgr.devices[A]["cloud"] = shelly.UNSUPPORTED
    with pytest.raises(ValueError):
        await mgr.change_settings(A, {"cloud": False})
    mgr.devices[A]["cloud"] = True
    await mgr.change_settings(A, {"cloud": False}, user="u")
    assert writes == [{"cloud": False}]
    entry = mgr.events.list()[0]
    assert entry["kind"] == "setting" and {"setting": "Cloud", "old": "on", "new": "off"} in entry["changes"]


async def test_wifi_signal_only_for_a_device_on_wifi(device_api):
    """Wi-Fi signal: Gen2+ Wifi.GetStatus with status "got ip", Gen1 /status wifi_sta;
    a device on the cable (no Wi-Fi connection) has none; connected without a usable
    value is an error (the dialog shows "unknown")."""
    _calls, answers = device_api
    answers["Wifi.GetStatus"] = {"status": "got ip", "rssi": -83}
    assert await shelly.wifi_signal(None, 2, "http://192.168.1.10") == -83
    answers["Wifi.GetStatus"] = {"status": "disconnected", "rssi": 0}
    assert await shelly.wifi_signal(None, 2, "http://192.168.1.10") is None
    answers["Wifi.GetStatus"] = shelly.UnsupportedError("no Wi-Fi")
    assert await shelly.wifi_signal(None, 2, "http://192.168.1.10") is None
    answers["/status"] = {"wifi_sta": {"connected": True, "rssi": -58}}
    assert await shelly.wifi_signal(None, 1, "http://192.168.1.10") == -58
    answers["/status"] = {"wifi_sta": {"connected": True, "rssi": "strong"}}
    with pytest.raises(ShellyError):
        await shelly.wifi_signal(None, 1, "http://192.168.1.10")
    answers["Wifi.GetStatus"] = {"status": "got ip"}
    with pytest.raises(ShellyError):
        await shelly.wifi_signal(None, 2, "http://192.168.1.10")


async def test_access_point_form_read_and_write(device_api):
    """Gen2+ reads the access point details (network name, open, range extender – never a
    password, the device does not return one) and writes the form in one Wifi.SetConfig:
    a password protects it, open sends no password; turning it off sends only enable."""
    calls, answers = device_api
    answers["Shelly.GetConfig"] = {"wifi": {"ap": {"enable": False, "ssid": "ShellyPlus1-A8", "is_open": True,
                                                   "range_extender": {"enable": False}}}}
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None)
    assert got["ap_config"] == {"ssid": "ShellyPlus1-A8", "open": True, "range_extender": False}
    answers["Shelly.GetConfig"] = {"wifi": {"ap": {"enable": True}}}
    got = await shelly.read_settings(None, 2, "http://1.2.3.4", "Plus1", None)
    assert got["ap_config"] == {"ssid": None, "open": None, "range_extender": None}
    answers["/settings"] = {}
    assert (await shelly.read_settings(None, 1, "http://1.2.3.4", "SHSW-1", None))["ap_config"] is None

    calls.clear()
    form = {"enable": True, "ssid": " Garden ", "security": "password", "password": "secret 1", "range_extender": True}
    await shelly.write_settings(None, 2, "http://1.2.3.4", shelly.validate_settings({"ap_config": form}, []))
    assert calls == [("Wifi.SetConfig", {"config": {"ap": {"enable": True, "ssid": "Garden", "pass": "secret 1",
                                                           "is_open": False, "range_extender": {"enable": True}}}})]
    calls.clear()
    form = {"enable": True, "ssid": "", "security": "open", "accept_open": True}
    await shelly.write_settings(None, 2, "http://1.2.3.4", shelly.validate_settings({"ap_config": form}, []))
    assert calls == [("Wifi.SetConfig", {"config": {"ap": {"enable": True, "ssid": None, "pass": None, "is_open": True}}})]
    calls.clear()
    form = {"enable": False, "ssid": "x", "security": "open"}
    await shelly.write_settings(None, 2, "http://1.2.3.4", shelly.validate_settings({"ap_config": form}, []))
    assert calls == [("Wifi.SetConfig", {"config": {"ap": {"enable": False}}})]
    calls.clear()
    keep = shelly.validate_settings({"ap_config": {"enable": True, "security": "keep"}}, [])
    await shelly.write_settings(None, 2, "http://1.2.3.4", keep)
    assert calls == [("Wifi.SetConfig", {"config": {"ap": {"enable": True}}})]
    with pytest.raises(ValueError):
        await shelly.write_settings(None, 1, "http://1.2.3.4", keep)


@pytest.mark.parametrize(
    "form",
    [
        None,
        {"enable": "yes"},
        {"enable": True},  # no security
        {"enable": True, "security": "password"},  # no password
        {"enable": True, "security": "password", "password": ""},
        {"enable": True, "security": "password", "password": "x" * 64},  # the Shelly takes 8 to 63
        {"enable": True, "security": "password", "password": "x" * 7},
        {"enable": True, "security": "password", "password": "secret\n12"},
        {"enable": True, "security": "open"},  # open not confirmed
        {"enable": True, "security": "keep", "ssid": "x" * 30},
        {"enable": True, "security": "keep", "ssid": "a\nb"},
        {"enable": True, "security": "keep", "range_extender": "on"},
    ],
)
def test_access_point_form_is_checked(form):
    with pytest.raises(ValueError):
        shelly.validate_settings({"ap_config": form}, [])


@pytest.mark.parametrize("password", ["x" * 8, "x" * 63, "with space", "!~#{}<>|\\'\""])
def test_access_point_password_lengths_the_shelly_takes(password):
    """8 to 63 printable ASCII characters pass (the Shelly refuses other lengths with -113)."""
    form = {"enable": True, "security": "password", "password": password}
    assert shelly.validate_settings({"ap_config": form}, [])["ap_config"]["password"] == password


@pytest.mark.parametrize(("password", "shown"), [("pässwörd", "ä"), ("password€", "€"), ("tab\tpassword", "?"),
                                                 ("x" * 7 + "é", "é")])
def test_access_point_password_ascii_only(password, shown):
    """WPA2: printable ASCII only – the error names the first character the Shelly cannot use."""
    form = {"enable": True, "security": "password", "password": password}
    with pytest.raises(ValueError, match=f"cannot use: {re.escape(shown)}$"):
        shelly.validate_settings({"ap_config": form}, [])


async def test_access_point_form_logs_no_password(mgr, net, monkeypatch, caplog):
    """The access point password goes to the device only: not in the log entry, the app
    log, the answer or the stored state; the entry names the changes."""
    await mgr.scan()
    written = []

    async def write(_session, _gen, _origin, changes, _auth=None):
        written.append(changes)
        return False

    async def read(*_args, **_kwargs):
        return {"ap": True, "ap_config": {"ssid": "Garden", "open": False, "range_extender": None}, "outputs": []}

    monkeypatch.setattr(shelly, "write_settings", write)
    monkeypatch.setattr(shelly, "read_settings", read)
    mgr.devices[A].update(ap=False, ap_config={"ssid": None, "open": True, "range_extender": None})
    form = {"enable": True, "ssid": "Garden", "security": "password", "password": "hunter2-secret"}
    caplog.set_level("DEBUG")
    settings, _restart, verified, not_applied = await mgr.change_settings(A, {"ap_config": form}, "u")
    assert written[0]["ap_config"]["password"] == "hunter2-secret" and verified and not_applied == []
    entry = mgr.events.entries[-1]
    assert entry["changes"] == [{"setting": "Access point", "old": "off", "new": "on"},
                                {"setting": "Access point name", "old": "default", "new": "Garden"},
                                {"setting": "Access point security", "old": "open", "new": "new password"}]
    mgr.save()
    for text in (json.dumps(mgr.events.entries), caplog.text, json.dumps(settings), json.dumps(mgr.devices),
                 data_paths.STATE_FILE.read_text()):
        assert "hunter2" not in text
    # A running access point that only gets a new password: no "Access point: on -> on" line
    await mgr.change_settings(A, {"ap_config": {"enable": True, "security": "password", "password": "hunter2-other"}}, "u")
    assert mgr.events.entries[-1]["changes"] == [{"setting": "Access point security", "old": "password", "new": "new password"}]
    # Open again while it is open: nothing changed, so no line at all
    mgr.devices[A].update(ap=True, ap_config={"ssid": "Garden", "open": True, "range_extender": None})
    await mgr.change_settings(A, {"ap_config": {"enable": True, "security": "open", "accept_open": True}}, "u")
    assert mgr.events.entries[-1]["changes"] == []
    with pytest.raises(ValueError, match="range extender"):
        await mgr.change_settings(A, {"ap_config": {"enable": True, "security": "keep", "range_extender": True}}, "u")
    # "keep" only for a running access point with a password: an open one, one that is
    # off (open by default) or one whose protection is not reported is refused unwritten
    count = len(written)
    for ap, is_open in ((True, True), (False, True), (False, False), (True, None)):
        mgr.devices[A].update(ap=ap, ap_config={"ssid": "Garden", "open": is_open, "range_extender": None})
        with pytest.raises(ValueError, match="Choose a password or an open access point"):
            await mgr.change_settings(A, {"ap_config": {"enable": True, "security": "keep"}}, "u")
    assert len(written) == count
    mgr.devices[A].update(ap=True, ap_config={"ssid": "Garden", "open": False, "range_extender": None})
    await mgr.change_settings(A, {"ap_config": {"enable": True, "security": "keep", "ssid": "Yard"}}, "u")
    assert written[-1]["ap_config"] == {"enable": True, "ssid": "Yard", "security": "keep"}
    mgr.devices[A]["gen"] = 1
    with pytest.raises(ValueError):
        await mgr.change_settings(A, {"ap_config": {"enable": False}}, "u")
