"""Home Assistant connection: Core WebSocket API and Supervisor API against a real
local server.

The other test modules replace the registry requests and the notification on the
Manager (conftest.mgr); here a Manager with a real client session talks to a local
aiohttp server that plays the Supervisor (manager_core.SUPERVISOR points to it):
greeting, token and request of the WebSocket API, registry answers of every size
the app accepts, failures of single requests, persistent notifications and the
app's own options and restart. The Shellys themselves stay FakeNet."""

import asyncio
import contextvars
import json

import aiohttp
import app_options
import data_paths
import main
import manager
import manager_core
import pytest
import shelly
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from conftest import OPTIONS, A, B, C

TOKEN = "supervisor-token-1"
REQUESTS = ("config/device_registry/list", "config/entity_registry/list", "config/area_registry/list",
            "config_entries/get", "config_entries/flow/progress")


class FakeSupervisor:
    """The Supervisor with the Core API proxy below /core.

    lists: request type -> result list (answered with success); failures: request
    type -> error object (success false); reply: request type -> raw answer
    instead (a str is sent as text, bytes as a binary frame). greeting/auth_reply
    replace the first two messages; before: messages sent ahead of every answer.
    A WebSocket connection answers requests until the app closes it. Every
    connection (its requests in order), token, request and POST is recorded; open
    counts the connections not closed yet."""

    def __init__(self):
        self.lists = {name: [] for name in REQUESTS}
        self.failures, self.reply, self.before = {}, {}, []
        self.greeting = {"type": "auth_required", "ha_version": "2026.11.1"}
        self.auth_reply = {"type": "auth_ok", "ha_version": "2026.11.1"}
        self.tokens, self.requests, self.posts, self.connections = [], [], [], []
        self.open = 0
        self.post_status, self.post_answer = 200, {"result": "ok", "data": {}}
        self.info_status, self.info, self.info_requests = 200, {"result": "ok", "data": {"options": {}}}, 0

    async def app_info(self, _request):
        self.info_requests += 1
        return web.json_response(self.info, status=self.info_status)

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.open += 1
        try:
            await self.serve(ws)
        finally:
            self.open -= 1
        return ws

    async def serve(self, ws):
        await ws.send_json(self.greeting)
        msg = await ws.receive()
        if msg.type is not WSMsgType.TEXT:  # the app closed the connection after the greeting
            return
        self.tokens.append(json.loads(msg.data).get("access_token"))
        await ws.send_json(self.auth_reply)
        if self.auth_reply.get("type") != "auth_ok":
            await ws.close()
            return
        calls = []
        self.connections.append(calls)
        async for msg in ws:  # until the app closes the connection
            if msg.type is not WSMsgType.TEXT:
                return
            call = json.loads(msg.data)
            calls.append(call)
            self.requests.append(call)
            kind = call["type"]
            for message in self.before:
                await ws.send_json(message)
            if kind in self.reply:
                raw = self.reply[kind]
                await (ws.send_bytes(raw) if isinstance(raw, bytes) else ws.send_str(raw))
            elif kind in self.failures:
                await ws.send_json({"id": call["id"], "type": "result", "success": False, "error": self.failures[kind]})
            else:
                await ws.send_json({"id": call["id"], "type": "result", "success": True, "result": self.lists[kind]})

    async def post(self, request):
        body = await request.json() if request.can_read_body else None
        self.posts.append((request.path, request.headers.get("Authorization"), body))
        return web.json_response(self.post_answer, status=self.post_status)

    async def __aenter__(self):
        app = web.Application()
        app.router.add_get("/core/websocket", self.websocket)
        app.router.add_get("/addons/self/info", self.app_info)
        app.router.add_post("/{tail:.*}", self.post)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.url = f"http://127.0.0.1:{self.server.port}"
        return self

    async def __aexit__(self, *_exc):
        await self.server.close()


@pytest.fixture
async def ha(net, monkeypatch):
    """(Manager with a real client session, FakeSupervisor) – the registry of the
    Supervisor lists the FakeNet devices."""
    async with FakeSupervisor() as sup, aiohttp.ClientSession() as session:
        monkeypatch.setattr(manager_core, "SUPERVISOR", sup.url)
        sup.lists["config/device_registry/list"] = net.registry()
        m = manager.Manager(dict(OPTIONS), session=session, token=TOKEN)
        m.first_start_open = False
        m.changelogs.refresh = net.refresh  # no changelog page from the manufacturer
        yield m, sup


def last_check(m):
    return [e for e in m.events.entries if e["kind"] == "check"][-1]


async def test_check_reads_home_assistant_over_the_websocket(ha, net):
    """A full check: all list requests over one connection with the token after the
    greeting, with IDs counted from 1; the Shelly config entries are asked for by
    domain; messages of other IDs before an answer are skipped (the battery sensor of
    the entity registry still arrives)."""
    m, sup = ha
    net.asleep = {C}
    sup.lists["config/entity_registry/list"] = [
        {"platform": "shelly", "unique_id": "aabbcc00000c-devicepower:0-battery", "entity_id": "sensor.garage_battery",
         "device_id": C}]
    sup.before = [{"id": 99, "type": "event", "event": {}}, {"type": "pong"}]
    assert await m.scan() is True
    assert set(m.devices) == {A, B, C} and m.devices[A]["name"] == "Kitchen"
    assert last_check(m)["severity"] != "error" and m.devices[A]["status"] == manager_core.STATUS_READY
    assert m.devices[C]["battery_source"] == manager_core.BATTERY_FROM_HA
    assert [r["type"] for r in sup.requests] == list(REQUESTS)
    assert [r["id"] for r in sup.requests] == [1, 2, 3, 4, 5]
    assert sup.requests[3]["domain"] == "shelly"
    assert sup.tokens == [TOKEN] and len(sup.connections) == 1
    await until(lambda: sup.open == 0)   # closed at the end of the Home Assistant part


async def until(condition):
    """Wait (at most 2 s) until condition() is true."""
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def forget_answers(m):
    """Drop every answer of Home Assistant the manager keeps (as if their time was up)."""
    for key in list(m._ha_cache):
        m._ha_forget(key)


async def test_single_checks_reuse_the_answers(ha, monkeypatch):
    """A single check within HA_CACHE_S of another check reuses its answers and opens no
    connection; afterwards it asks for device, entity and area registry over one new
    connection."""
    m, sup = ha
    await m.scan()
    asked = len(sup.requests)
    assert (await m.check_device(A))["error"] is None
    assert len(sup.requests) == asked and len(sup.connections) == 1
    monkeypatch.setattr(manager_core, "HA_CACHE_S", 0)
    assert (await m.check_device(B))["error"] is None
    assert len(sup.connections) == 2 and sup.tokens == [TOKEN, TOKEN]
    assert [(r["id"], r["type"]) for r in sup.connections[1]] == list(enumerate(REQUESTS[:3], 1))


def renamed(entries, device_id, **change):
    """Copy of the device registry with changed fields of one entry."""
    return [{**e, **change} if e["id"] == device_id else dict(e) for e in entries]


async def test_check_now_and_try_again_read_anew(ha, monkeypatch):
    """Check now and Try again ask Home Assistant anew even right after a single
    check; what they read refreshes the answers the next single check reuses."""
    m, sup = ha
    await m.scan()
    registry = "config/device_registry/list"
    sup.lists[registry] = renamed(sup.lists[registry], A, name_by_user="Pantry")
    await m.check_device(A)
    assert m.devices[A]["name"] == "Kitchen"   # the answer of the full check is reused
    await m.scan()
    assert m.devices[A]["name"] == "Pantry"
    sup.lists[registry] = renamed(sup.lists[registry], A, configuration_url="http://192.168.1.77/")

    async def port_open(_ip, _port):
        raise TimeoutError()

    monkeypatch.setattr(shelly, "port_open", port_open)
    asked = len(sup.requests)
    steps = await m.probe_device(A)
    assert [r["type"] for r in sup.requests[asked:]] == [registry]
    assert steps["address"] == {"state": "ok", "ip": "192.168.1.77", "moved": True}
    assert [e["configuration_url"] for e in await m._registry() if e["id"] == A] == ["http://192.168.1.77/"]
    assert len(sup.requests) == asked + 1   # Try again refreshed the answer single checks reuse


async def test_parallel_single_checks_share_one_read(ha):
    """Single checks started together share one read of each answer: one device, one
    entity and one area registry request over one connection."""
    m, sup = ha
    await m.scan()
    forget_answers(m)
    asked, connections = len(sup.requests), len(sup.connections)
    results = await asyncio.gather(m.check_device(A), m.check_device(B), m.check_device(C))
    assert [r["error"] for r in results] == [None, None, None]
    assert sorted(r["type"] for r in sup.requests[asked:]) == sorted(REQUESTS[:3])
    assert len(sup.connections) == connections + 1


async def test_failures_are_not_reused(ha, net):
    """A failed answer is never kept: the next single check asks again at once – for the
    entity registry (battery hint left out meanwhile) and for the device registry (the
    check fails with the reason, the next one succeeds)."""
    m, sup = ha
    net.asleep = {A}   # never answered: the battery hint of Home Assistant applies
    await m.scan()
    forget_answers(m)
    entities, registry = "config/entity_registry/list", "config/device_registry/list"
    sup.failures = {entities: {"code": "unknown_error", "message": "Entity registry busy"}}
    sup.lists[entities] = [{"platform": "shelly", "unique_id": "aabbcc00000a-devicepower:0-battery",
                            "entity_id": "sensor.kitchen_battery", "device_id": A}]
    await m.check_device(A)
    assert "battery_source" not in m.devices[A]
    sup.failures = {}
    await m.check_device(A)
    assert m.devices[A]["battery_source"] == manager_core.BATTERY_FROM_HA
    assert [r["type"] for r in sup.requests].count(entities) == 3   # scan, failed, asked again
    forget_answers(m)
    sup.failures = {registry: {"code": "unknown_error", "message": "Registry busy"}}
    assert "Registry busy" in (await m.check_device(B))["error"]
    sup.failures = {}
    assert (await m.check_device(B))["error"] is None
    assert m._ha_reading == {}


async def test_concurrent_waiters_share_a_failure(ha):
    """Checks waiting for the same failing read get its failure and ask no second time;
    nothing of it is kept afterwards."""
    m, sup = ha
    await m.scan()
    forget_answers(m)
    registry = "config/device_registry/list"
    asked = [r["type"] for r in sup.requests].count(registry)
    sup.failures = {registry: {"code": "unknown_error", "message": "Registry busy"}}
    results = await asyncio.gather(m.check_device(A), m.check_device(B))
    assert all("Registry busy" in r["error"] for r in results)
    assert [r["type"] for r in sup.requests].count(registry) == asked + 1
    assert m._ha_reading == {} and "devices" not in m._ha_cache


async def test_cancelled_reads_hand_over():
    """A cancelled reader hands over to the caller waiting for it, which reads itself; a
    cancelled waiter leaves the read running, and its answer is kept."""
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    gate, reads = asyncio.Event(), []

    async def read():
        reads.append(len(reads))
        await gate.wait()
        return ["answer", len(reads)]

    first = asyncio.ensure_future(m._ha_cached("devices", read))
    await until(lambda: reads == [0])
    second = asyncio.ensure_future(m._ha_cached("devices", read))
    await asyncio.sleep(0.01)
    first.cancel()
    await until(lambda: reads == [0, 1])   # the waiting caller reads itself
    third = asyncio.ensure_future(m._ha_cached("devices", read))
    await asyncio.sleep(0.01)
    third.cancel()
    gate.set()
    assert await second == ["answer", 2]
    assert first.cancelled() and third.cancelled()
    assert m._ha_reading == {} and m._ha_cache["devices"][1] == ["answer", 2]
    assert await m._ha_cached("devices", read) == ["answer", 2] and len(reads) == 2


async def test_a_finished_scope_keeps_no_connection(ha):
    """A task that inherited the scope of a check after it ended does not reopen its
    connection: its request uses a connection of its own that is closed right after."""
    m, sup = ha
    sup.lists["config/area_registry/list"] = [{"area_id": "kitchen", "name": "Kitchen"}]
    async with m._ha_reads():
        inherited = contextvars.copy_context()
    task = asyncio.get_running_loop().create_task(m._ha_list("config/area_registry/list"), context=inherited)
    assert await task == [{"area_id": "kitchen", "name": "Kitchen"}]
    assert len(sup.connections) == 1
    await until(lambda: sup.open == 0)


async def test_answers_are_dropped_after_their_time(ha, monkeypatch):
    """The answers kept for single checks are freed by a timer once HA_CACHE_S is up."""
    m, _sup = ha
    monkeypatch.setattr(manager_core, "HA_CACHE_S", 0.05)
    await m.scan()
    await until(lambda: m._ha_cache == {} and m._ha_drop == {})


async def test_entity_registry_and_config_entries_reach_the_check(ha, net):
    """The battery sensor of the entity registry marks a device that never answered;
    an ignored discovery and a discovery in progress appear in the listing."""
    m, sup = ha
    net.asleep = {C}
    sup.lists["config/entity_registry/list"] = [
        {"platform": "shelly", "unique_id": "aabbcc00000c-devicepower:0-battery", "entity_id": "sensor.garage_battery",
         "device_id": C},
        {"platform": "zha", "unique_id": "x-battery", "entity_id": "sensor.other_battery", "device_id": A},
    ]
    sup.lists["config_entries/get"] = [{"domain": "shelly", "source": "ignore", "title": "Shelly Plug S"}]
    sup.lists["config_entries/flow/progress"] = [
        {"handler": "shelly", "context": {"source": "zeroconf", "title_placeholders": {"name": "shellyplus2pm"}}}]
    await m.scan()
    assert m.devices[C]["battery_source"] == manager_core.BATTERY_FROM_HA
    assert "battery_source" not in m.devices[A] or m.devices[A]["battery_source"] != manager_core.BATTERY_FROM_HA
    assert [(e["name"], e["reason"]) for e in m.listing["missing"]] == [("shellyplus2pm", "discovered"),
                                                                       ("Shelly Plug S", "ignored")]


async def test_registry_larger_than_the_aiohttp_default(ha):
    """The device registry of a large installation arrives as one message above
    aiohttp's default limit of 4 MiB; it is read."""
    m, sup = ha
    filler = [{"id": f"{i:032x}", "name": "x" * 200, "manufacturer": "Acme", "identifiers": [["acme", str(i)]]}
              for i in range(25000)]
    sup.lists["config/device_registry/list"] = sup.lists["config/device_registry/list"] + filler
    assert len(json.dumps(sup.lists["config/device_registry/list"])) > 4 * 1024 * 1024
    await m.scan()
    assert set(m.devices) == {A, B, C} and last_check(m)["severity"] != "error"


@pytest.mark.parametrize(("change", "text"), [
    ({"auth_reply": {"type": "auth_invalid", "message": "Invalid access token"}},
     "Authentication with Home Assistant failed"),
    ({"failures": {"config/device_registry/list": {"code": "unauthorized", "message": "Unauthorized"}}},
     "Unauthorized"),
    ({"reply": {"config/device_registry/list": b"\x00\x01"}}, "did not answer the device registry request (BINARY)"),
    ({"reply": {"config/device_registry/list": "not json"}}, "Unreadable answer of Home Assistant"),
    ({"reply": {"config/device_registry/list": "[1, 2]"}}, "Unexpected answer of Home Assistant"),
])
async def test_registry_failure_fails_the_check(ha, change, text):
    """A failed registry request ends the check as an error with the reason, and Home
    Assistant gets the notification of the failed check."""
    m, sup = ha
    for name, value in change.items():
        setattr(sup, name, value)
    assert await m.scan() is True
    entry = last_check(m)
    assert entry["severity"] == "error" and text in entry["result"]
    assert m.devices == {}
    path, header, body = sup.posts[-1]
    assert path == "/core/api/services/persistent_notification/create" and header == f"Bearer {TOKEN}"
    assert body["title"] == "Shelly firmware check failed" and body["notification_id"] == "sfm_summary"


async def test_registry_above_the_limit_fails_the_check(ha, monkeypatch):
    """An answer above REGISTRY_MAX closes the connection: the check fails."""
    m, _sup = ha
    monkeypatch.setattr(manager_core, "REGISTRY_MAX", 256)  # the three devices need about 600 bytes
    await m.scan()
    assert "did not answer the device registry request" in last_check(m)["result"]


async def test_token_only_after_the_greeting(ha):
    """The token is sent only to a server that greets with auth_required."""
    m, sup = ha
    sup.greeting = {"type": "auth_ok"}
    await m.scan()
    assert "Unexpected WebSocket greeting" in last_check(m)["result"]
    assert sup.tokens == [] and sup.requests == []


async def test_failed_side_requests_do_not_stop_the_check(ha):
    """Entity registry, area registry, config entries and flows are hints: their failure
    leaves the check as it is. A refused request keeps the connection; a broken one
    (binary frame) is replaced for the next request, which still gets its answer."""
    m, sup = ha
    sup.failures = {name: {"code": "unknown_command", "message": "Unknown command."} for name in REQUESTS[1:]}
    await m.scan()
    assert set(m.devices) == {A, B, C}
    assert last_check(m)["severity"] != "error" and m.listing["missing"] == []
    assert sup.tokens == [TOKEN]
    registry = "config/device_registry/list"
    sup.failures = {}
    sup.reply = {"config/entity_registry/list": b"\x00\x01"}
    sup.lists[registry] = renamed(sup.lists[registry], A, area_id="kitchen")
    sup.lists["config/area_registry/list"] = [{"area_id": "kitchen", "name": "Kitchen"}]
    await m.scan()
    assert last_check(m)["severity"] != "error" and m.devices[A]["area"] == "Kitchen"
    assert sup.tokens == [TOKEN] * 3
    assert [[r["type"] for r in c] for c in sup.connections[1:]] == [list(REQUESTS[:2]), list(REQUESTS[2:])]


async def test_notification(ha):
    """A persistent notification: title, text and ID with the token; refused or
    unreachable is only False, never an exception."""
    m, sup = ha
    assert await m.notify("Title", "Text", "sfm_x") is True
    assert sup.posts == [("/core/api/services/persistent_notification/create", f"Bearer {TOKEN}",
                          {"title": "Title", "message": "Text", "notification_id": "sfm_x"})]
    sup.post_status = 500
    assert await m.notify("Title", "Text", "sfm_x") is False
    await sup.server.close()
    assert await m.notify("Title", "Text", "sfm_x") is False


async def test_options_are_stored_by_the_app_and_restart_through_the_supervisor(ha):
    """Saving options writes the complete new set to settings.json (nothing goes to the
    Supervisor); a write error is a clear error and logged as failed; restart asks the
    Supervisor and an unreachable Supervisor is a clear error."""
    m, sup = ha
    assert await m.save_options({"log_entry_limit": 500}, user="Admin") == ["log_entry_limit"]
    assert sup.posts == []
    assert json.loads(data_paths.SETTINGS_FILE.read_text()) == m.options and m.options["log_entry_limit"] == 500
    assert {k: v for k, v in m.options.items() if k in OPTIONS and k != "log_entry_limit"} == {
        k: v for k, v in OPTIONS.items() if k != "log_entry_limit"}
    data_paths.SETTINGS_FILE.unlink()
    data_paths.SETTINGS_FILE.mkdir()   # cannot be replaced by a file
    with pytest.raises(shelly.ShellyError, match="Settings could not be stored"):
        await m.save_options({"log_entry_limit": 600}, user="Admin")
    assert [e for e in m.events.entries if e["kind"] == "options"][-1]["ok"] is False
    assert m.options["log_entry_limit"] == 500
    sup.post_status, sup.post_answer = 200, {"result": "ok"}
    await m.restart_app()
    assert sup.posts[-1] == ("/addons/self/restart", f"Bearer {TOKEN}", None)
    await sup.server.close()
    with pytest.raises(shelly.ShellyError, match="Supervisor not reachable"):
        await m.restart_app()


async def test_settings_are_taken_over_once_from_the_supervisor(net, monkeypatch, tmp_path):
    """First start with settings.json missing: the options the Supervisor still keeps from
    older versions are validated (invalid and unknown ones dropped), stored, and the
    Supervisor's copy is emptied; later starts read only the file."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"settings": ""}))
    monkeypatch.setattr(main, "OPTIONS_FILE", options_file)
    async with FakeSupervisor() as sup, aiohttp.ClientSession() as session:
        monkeypatch.setattr(manager_core, "SUPERVISOR", sup.url)
        kept = {**OPTIONS, "log_entry_limit": 500, "check_time": "25:00", "gen1_allowed_types": ["SHSW-1"],
                "download_base_url": "http://203.0.113.10:8098", "settings": "typed"}
        sup.info = {"result": "ok", "data": {"slug": "x", "options": kept}}
        options, warning = await main.load_settings(session, TOKEN)
        assert warning is None
        assert options["log_entry_limit"] == 500 and options["check_time"] == "02:45"
        assert options["download_base_url"] == "http://203.0.113.10:8098"   # kept; the app shows its notice
        assert "gen1_allowed_types" not in options and "settings" not in options
        assert options["pinned_certificates"] == app_options.DEFAULTS["pinned_certificates"]
        assert json.loads(data_paths.SETTINGS_FILE.read_text()) == options
        assert sup.posts == [("/addons/self/options", f"Bearer {TOKEN}", {"options": {}})]
        assert await main.load_settings(session, TOKEN) == (options, None)
        assert sup.info_requests == 1 and len(sup.posts) == 1


async def test_settings_take_over_waits_for_the_supervisor(net, monkeypatch, tmp_path):
    """Supervisor not readable: the defaults apply and nothing is stored or emptied, so
    the next start tries again; a damaged settings file is kept aside."""
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps({"settings": ""}))
    monkeypatch.setattr(main, "OPTIONS_FILE", options_file)
    async with FakeSupervisor() as sup, aiohttp.ClientSession() as session:
        monkeypatch.setattr(manager_core, "SUPERVISOR", sup.url)
        sup.info_status = 500
        assert await main.load_settings(session, TOKEN) == (app_options.with_defaults({}), None)
        assert not data_paths.SETTINGS_FILE.exists() and sup.posts == []
        data_paths.SETTINGS_FILE.write_text("[1")
        sup.info_status, sup.info = 200, {"result": "ok", "data": {"options": {"backup_keep": 20}}}
        options, warning = await main.load_settings(session, TOKEN)
        assert options["backup_keep"] == 20 and warning == app_options.SETTINGS_DAMAGED
        assert data_paths.SETTINGS_FILE.with_suffix(".damaged").read_text() == "[1"


async def test_invalid_values_in_the_settings_file(net, monkeypatch, tmp_path):
    """Every value of settings.json is checked at the start: an invalid one (e.g. edited by
    hand) gets its default and is named in a warning for the UI; invalid certificate pins
    no longer stop the start; unknown keys are ignored silently."""
    stored = {**app_options.with_defaults({}), "backup_keep": 20, "check_time": "25:00", "pinned_certificates": "x",
              "download_base_url": "http://192.168.1.5:8098", "removed_option": True}
    data_paths.SETTINGS_FILE.write_text(json.dumps(stored))
    async with aiohttp.ClientSession() as session:
        options, warning = await main.load_settings(session, TOKEN)
    assert options["backup_keep"] == 20 and options["download_base_url"] == "http://192.168.1.5:8098"
    assert options["check_time"] == "02:45"
    assert options["pinned_certificates"] == app_options.DEFAULTS["pinned_certificates"]
    assert "removed_option" not in options
    assert warning == app_options.SETTINGS_INVALID.format(keys="check_time, pinned_certificates")
    m = manager.Manager(options, None, TOKEN)
    m.settings_warning = warning
    assert m.public_state()["storage_error"] == warning


async def test_host_port_is_read_and_set_through_the_supervisor(ha, monkeypatch):
    """The host port of the download server comes from /addons/self/info ("network");
    a new one is set with POST /addons/self/options {"network": …} and logged; unknown
    (Supervisor not readable), invalid ports and ports in use are refused."""
    m, sup = ha
    sup.info = {"result": "ok", "data": {"network": {"8098/tcp": 8097}}}
    await m.read_host_port()
    assert (m.host_port, m.host_port_known) == (8097, True)
    assert await m.save_options({"host_port": 8097}) == []   # unchanged: nothing saved, no restart
    with pytest.raises(ValueError, match="host_port"):
        await m.save_options({"host_port": 70000})
    # A port that accepts a connection on the address of Home Assistant is in use: refused
    asked = []

    async def port_open(host, port):
        asked.append((host, port))
        if port == 8123:
            return 0.01
        raise ConnectionRefusedError

    monkeypatch.setattr(manager_core, "port_open", port_open)
    with pytest.raises(ValueError, match="already in use"):
        await m.save_options({"host_port": 8123})
    assert asked == [("192.168.1.2", 8123)] and not any(p[0] == "/addons/self/options" for p in sup.posts)
    # A host name in the download base URL: checked at its LAN address, never outside the LAN
    lan = {"ha.lan": "192.168.1.3", "ha.example": None}

    async def resolve(host):
        return lan.get(host, host)   # an IP address stays

    monkeypatch.setattr(manager_core, "resolve_lan_address", resolve)
    asked.clear()
    await m._check_port_free(8123, "http://ha.example:8123")   # public name: not checked, not refused
    assert asked == []
    with pytest.raises(ValueError, match="already in use on ha.lan"):
        await m._check_port_free(8123, "http://ha.lan:8123")
    assert asked == [("192.168.1.3", 8123)]
    m.connection_test = {"ok": False, "url": str(m.download_base), "at": 1, "device": "Hall", "error": "timeout"}
    assert await m.save_options({"host_port": 8099, "log_entry_limit": 600}, user="Admin") == ["log_entry_limit", "host_port"]
    assert sup.posts[-1] == ("/addons/self/options", f"Bearer {TOKEN}", {"network": {"8098/tcp": 8099}})
    assert m.host_port == 8099 and m.options["log_entry_limit"] == 600
    assert m.connection_test is None   # the last test result belongs to the old port
    entry = [e for e in m.events.entries if e["kind"] == "options"][-1]
    assert {"option": "host_port", "old": "8097", "new": "8099"} in entry["changes"]
    sup.post_status, sup.post_answer = 400, {"result": "error", "message": "bad"}
    with pytest.raises(shelly.ShellyError, match="Supervisor rejected"):
        await m.save_options({"host_port": 8100})
    assert m.host_port == 8099 and [e for e in m.events.entries if e["kind"] == "options"][-1]["ok"] is False
    sup.info = {"result": "ok", "data": {"network": {"8098/tcp": None}}}   # switched off in Home Assistant
    await m.read_host_port()
    assert (m.host_port, m.host_port_known) == (None, True)
    sup.info_status = 500
    await m.read_host_port()
    assert m.host_port_known is False
    with pytest.raises(ValueError, match="Configuration tab"):
        await m.save_options({"host_port": 8098})
