"""Home Assistant connection: Core WebSocket API and Supervisor API against a real
local server.

The other test modules replace the registry requests and the notification on the
Manager (conftest.mgr); here a Manager with a real client session talks to a local
aiohttp server that plays the Supervisor (manager_core.SUPERVISOR points to it):
greeting, token and request of the WebSocket API, registry answers of every size
the app accepts, failures of single requests, persistent notifications and the
app's own options and restart. The Shellys themselves stay FakeNet."""

import json

import aiohttp
import manager
import manager_core
import pytest
import shelly
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from conftest import OPTIONS, A, B, C

TOKEN = "supervisor-token-1"
REQUESTS = ("config/device_registry/list", "config/entity_registry/list", "config_entries/get",
            "config_entries/flow/progress")


class FakeSupervisor:
    """The Supervisor with the Core API proxy below /core.

    lists: request type -> result list (answered with success); failures: request
    type -> error object (success false); reply: request type -> raw answer
    instead (a str is sent as text, bytes as a binary frame). greeting/auth_reply
    replace the first two messages; before: messages sent ahead of every answer.
    Every WebSocket connection, request and POST is recorded."""

    def __init__(self):
        self.lists = {name: [] for name in REQUESTS}
        self.failures, self.reply, self.before = {}, {}, []
        self.greeting = {"type": "auth_required", "ha_version": "2026.11.1"}
        self.auth_reply = {"type": "auth_ok", "ha_version": "2026.11.1"}
        self.tokens, self.requests, self.posts = [], [], []
        self.post_status, self.post_answer = 200, {"result": "ok", "data": {}}

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(self.greeting)
        msg = await ws.receive()
        if msg.type is not WSMsgType.TEXT:  # the app closed the connection after the greeting
            return ws
        self.tokens.append(json.loads(msg.data).get("access_token"))
        await ws.send_json(self.auth_reply)
        if self.auth_reply.get("type") != "auth_ok":
            await ws.close()
            return ws
        msg = await ws.receive()
        if msg.type is not WSMsgType.TEXT:
            return ws
        call = json.loads(msg.data)
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
        await ws.receive()  # until the app closes the connection
        return ws

    async def post(self, request):
        body = await request.json() if request.can_read_body else None
        self.posts.append((request.path, request.headers.get("Authorization"), body))
        return web.json_response(self.post_answer, status=self.post_status)

    async def __aenter__(self):
        app = web.Application()
        app.router.add_get("/core/websocket", self.websocket)
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


async def test_check_reads_home_assistant_over_the_websocket(ha):
    """A full check: one connection per list request, each with the token after the
    greeting, one request with ID 1; the Shelly config entries are asked for by
    domain; answers of other IDs before the answer are skipped."""
    m, sup = ha
    sup.before = [{"id": 2, "type": "event", "event": {}}, {"type": "pong"}]
    assert await m.scan() is True
    assert set(m.devices) == {A, B, C} and m.devices[A]["name"] == "Kitchen"
    assert last_check(m)["severity"] != "error" and m.devices[A]["status"] == manager_core.STATUS_READY
    assert [r["type"] for r in sup.requests] == list(REQUESTS)
    assert {r["id"] for r in sup.requests} == {1}
    assert sup.requests[2]["domain"] == "shelly"
    assert sup.tokens == [TOKEN] * len(REQUESTS)


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
    """Entity registry, config entries and flows are hints: their failure leaves the
    check as it is."""
    m, sup = ha
    sup.failures = {name: {"code": "unknown_command", "message": "Unknown command."} for name in REQUESTS[1:]}
    await m.scan()
    assert set(m.devices) == {A, B, C}
    assert last_check(m)["severity"] != "error" and m.listing["missing"] == []


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


async def test_options_and_restart_through_the_supervisor(ha):
    """Saving options sends the complete new options; a refusal carries the
    Supervisor's message and is logged as failed; restart asks the Supervisor and an
    unreachable Supervisor is a clear error."""
    m, sup = ha
    assert await m.save_options({"log_entry_limit": 500}, user="Admin") == ["log_entry_limit"]
    path, header, body = sup.posts[-1]
    assert (path, header) == ("/addons/self/options", f"Bearer {TOKEN}")
    assert body == {"options": m.options} and m.options["log_entry_limit"] == 500
    assert {k: v for k, v in m.options.items() if k in OPTIONS and k != "log_entry_limit"} == {
        k: v for k, v in OPTIONS.items() if k != "log_entry_limit"}
    sup.post_status, sup.post_answer = 400, {"result": "error", "message": "Invalid option 'x'"}
    with pytest.raises(shelly.ShellyError, match="Supervisor rejected the request: Invalid option 'x'"):
        await m.save_options({"log_entry_limit": 600}, user="Admin")
    assert [e for e in m.events.entries if e["kind"] == "options"][-1]["ok"] is False
    assert m.options["log_entry_limit"] == 500
    sup.post_status, sup.post_answer = 200, {"result": "ok"}
    await m.restart_app()
    assert sup.posts[-1] == ("/addons/self/restart", f"Bearer {TOKEN}", None)
    await sup.server.close()
    with pytest.raises(shelly.ShellyError, match="Supervisor not reachable"):
        await m.restart_app()
