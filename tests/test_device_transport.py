"""Device transport: every request to a Shelly against a real local HTTP server.

The other test modules replace the device requests (conftest.FakeNet); here the
public device functions of shelly.py talk to a local aiohttp server that plays the
Shelly: password challenge and its single answer (Gen2+ Digest SHA-256 checked by
the server, Gen1 Basic), no downgrade of the authentication, no redirects, the
size limit of an answer and the translation of HTTP and RPC errors. Last part: the
Gen1 write-back of a backup restore over the same requests."""

import asyncio
import base64
import hashlib
import inspect
import json
import re

import aiohttp
import pytest
import shelly
import shelly_backup
from aiohttp import web
from aiohttp.test_utils import TestServer
from fake_shelly import FakeGen1

REALM = "shellyplus1-a8032ab12345"  # Gen2+ device ID, also the Digest realm
PASSWORD = "pw-1"
DIGEST_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]*))')

# Answer of a Gen2+ /shelly that already is Shelly.GetDeviceInfo
GEN2_INFO = {"id": REALM, "mac": "a8032ab12345", "model": "SNSW-001X16EU", "gen": 2,
             "fw_id": "20240625-122918/1.3.3-gbdfd9b3", "ver": "1.3.3", "app": "Plus1", "auth_en": True}


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


class FakeDevice:
    """A Shelly on a local test server.

    routes: path -> answer (a dict becomes JSON, a web.Response is sent as it is, a
    callable gets the request and its JSON body and may be a coroutine; "*" answers
    every other path). Every request is recorded as
    (method, path with query, Authorization header, JSON body). With a password
    every path but /shelly asks for it like the real devices: Gen2+ with a Digest
    challenge (a new nonce each time, SHA-256), Gen1 with Basic; the server checks
    the answer itself. challenge overrides the WWW-Authenticate header. blocked(signed,
    body) -> bool answers HTTP 429 before the password check (the sign-in block of
    firmware 2.0+); signed tells whether the request carries an Authorization header."""

    def __init__(self, gen=2, password=None, routes=None, challenge=None, blocked=None):
        self.gen, self.password, self.challenge, self.blocked = gen, password, challenge, blocked
        self.routes = dict(routes or {})
        self.requests = []
        self.nonces = []

    def authorized(self, request, header):
        if header is None:
            return False
        if self.gen == 1:
            return header == "Basic " + base64.b64encode(f"admin:{self.password}".encode()).decode()
        scheme, _, params = header.partition(" ")
        values = {m[0]: m[1] or m[2] for m in DIGEST_RE.findall(params)}
        ha1 = sha256(f"admin:{REALM}:{self.password}")
        ha2 = sha256(f"{request.method}:{values.get('uri')}")
        expected = sha256(f"{ha1}:{values.get('nonce')}:{values.get('nc')}:{values.get('cnonce')}:auth:{ha2}")
        return (
            scheme == "Digest"
            and values.get("uri") == request.path_qs  # signed for exactly this request
            and values.get("realm") == REALM
            and values.get("nonce") == self.nonces[-1]  # the nonce of the last challenge
            and values.get("algorithm") == "SHA-256"
            and values.get("response") == expected
        )

    def ask_password(self):
        if self.challenge is None and self.gen == 1:
            challenge = 'Basic realm="shelly1-B929CC"'
        elif self.challenge is None:
            self.nonces.append(str(1700000000 + len(self.nonces)))
            challenge = f'Digest qop="auth", realm="{REALM}", nonce="{self.nonces[-1]}", algorithm=SHA-256'
        else:
            challenge = self.challenge
        return web.Response(status=401, headers={"WWW-Authenticate": challenge})

    async def handle(self, request):
        body = await request.json() if request.can_read_body else None
        header = request.headers.get("Authorization")
        self.requests.append((request.method, request.path_qs, header, body))
        if self.blocked is not None and self.blocked(header is not None, body):
            return web.Response(status=429, text="Too Many Requests")
        if self.password is not None and request.path != "/shelly" and not self.authorized(request, header):
            return self.ask_password()
        answer = self.routes.get(request.path, self.routes.get("*"))
        if callable(answer):
            answer = answer(request, body)
        if inspect.isawaitable(answer):
            answer = await answer
        if answer is None:
            return web.Response(status=404, text="Not Found")
        return answer if isinstance(answer, web.StreamResponse) else web.json_response(answer)

    async def __aenter__(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.origin = f"http://127.0.0.1:{self.server.port}"
        return self

    async def __aexit__(self, *_exc):
        await self.server.close()


def rpc(result):
    """Route answering a Gen2+ RPC like the device (id echoed)."""
    return lambda _request, body: {"id": body["id"], "src": REALM, "result": result}


def rpc_by_method(results):
    """/rpc route answering each method with its result (unknown methods: error 404)."""
    def answer(_request, body):
        if body["method"] not in results:
            return web.json_response({"id": body["id"], "src": REALM, "error": {
                "code": 404, "message": f"No handler for {body['method']}"}}, status=404)
        return {"id": body["id"], "src": REALM, "result": results[body["method"]]}
    return answer


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def gen2_auth(password=PASSWORD):
    return shelly.DeviceAuth(gen=2, password=password)


async def test_gen2_identity_from_shelly_alone(session):
    """A complete /shelly answer needs no second request and no password."""
    async with FakeDevice(password=PASSWORD, routes={"/shelly": GEN2_INFO}) as dev:
        seen = []
        info = await shelly.device_info(session, dev.origin, lambda _gen: None, lambda *a: seen.append(a))
    assert info == {"auth_enabled": True, "gen": 2, "shelly_id": REALM, "type": "Plus1", "model": "SNSW-001X16EU",
                    "mac": "A8032AB12345", "version": "1.3.3", "build": "20240625-122918/1.3.3-gbdfd9b3"}
    assert seen == [(2, REALM)]
    assert dev.requests == [("GET", "/shelly", None, None)]


async def test_gen2_digest_challenge_answered_once(session):
    """An incomplete /shelly answer: Shelly.GetDeviceInfo is asked for with the
    password; the 401 challenge is answered once with Digest SHA-256 for exactly
    that request (the server checks the response)."""
    short = {k: v for k, v in GEN2_INFO.items() if k != "fw_id"}
    routes = {"/shelly": short, "/rpc/Shelly.GetDeviceInfo": GEN2_INFO}
    async with FakeDevice(password=PASSWORD, routes=routes) as dev:
        info = await shelly.device_info(session, dev.origin, lambda gen: gen2_auth() if gen == 2 else None)
    assert info["build"] == GEN2_INFO["fw_id"]
    assert [(m, p) for m, p, _h, _b in dev.requests] == [("GET", "/shelly"), ("GET", "/rpc/Shelly.GetDeviceInfo"),
                                                       ("GET", "/rpc/Shelly.GetDeviceInfo")]
    assert dev.requests[1][2] is None and dev.requests[2][2].startswith("Digest ")


async def test_gen2_digest_signs_the_query(session):
    """A Gen2+ GET with query parameters is signed for the path with its query."""
    async with FakeDevice(password=PASSWORD, routes={"/rpc/Script.GetCode": {"data": "let a = 1;"}}) as dev:
        answer = await shelly.device_get(session, dev.origin, "/rpc/Script.GetCode", gen2_auth(),
                                         {"id": "1", "offset": "0"})
    assert answer == {"data": "let a = 1;"}
    assert 'uri="/rpc/Script.GetCode?id=1&offset=0"' in dev.requests[1][2]


async def test_gen2_rpc_with_stored_ha1(session):
    """A stored HA1 bound to the device ID answers the challenge of a JSON-RPC POST;
    the body is the RPC frame."""
    auth = shelly.DeviceAuth(gen=2, ha1=sha256(f"admin:{REALM}:{PASSWORD}"), realm=REALM)
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}) as dev:
        await shelly.restart(session, 2, dev.origin, auth)
    assert len(dev.requests) == 2
    method, path, header, body = dev.requests[1]
    assert (method, path, body) == ("POST", "/rpc", {"id": 1, "method": "Shelly.Reboot", "params": {}})
    assert 'uri="/rpc"' in header


async def test_every_request_of_a_settings_read_answers_its_own_challenge(session):
    """Each RPC of an extended settings read gets a fresh challenge; every answer
    uses the nonce of its own challenge."""
    results = {
        "Shelly.GetConfig": {"sys": {"device": {"name": "Kitchen"}}, "ble": {"enable": True}, "cloud": {"enable": True},
                             "switch:0": {"name": "Light", "initial_state": "off"}},
        "Shelly.GetStatus": {"sys": {"wakeup_period": 0}, "cloud": {"connected": True}},
        "Script.List": {"scripts": [{"name": "aioshelly_ble_integration", "running": True}]},
        "Webhook.List": {"hooks": [{"enable": True}]},
        "Schedule.List": {"jobs": []},
    }
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc_by_method(results)}) as dev:
        settings = await shelly.read_settings(session, 2, dev.origin, "Plus1", gen2_auth(), extended=True)
    assert settings["shelly_name"] == "Kitchen" and settings["cloud_connected"] is True
    assert settings["ble_proxy"] is True and settings["battery"] is False
    assert settings["actions"] is True and settings["schedules"] is False
    assert [o["key"] for o in settings["outputs"]] == ["switch:0"]
    methods = [b["method"] for _m, _p, h, b in dev.requests if h]
    assert methods == list(results) and len(dev.requests) == 2 * len(results) and len(dev.nonces) == len(results)


async def test_password_missing(session):
    """No stored password: the 401 is reported as such, nothing is retried."""
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}) as dev:
        with pytest.raises(shelly.AuthError, match="requires a password"):
            await shelly.restart(session, 2, dev.origin, None)
    assert len(dev.requests) == 1


async def test_wrong_password(session):
    """A wrong password is sent once; the second 401 ends the request."""
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}) as dev:
        with pytest.raises(shelly.AuthError, match="rejected the password"):
            await shelly.restart(session, 2, dev.origin, gen2_auth("wrong"))
    assert len(dev.requests) == 2 and dev.requests[1][2].startswith("Digest ")


@pytest.fixture
def short_block_wait(monkeypatch):
    """The wait after a 429 to a request without credentials, shortened for the tests."""
    monkeypatch.setattr(shelly, "AUTH_BLOCK_WAIT_S", 0.01)


async def test_429_before_credentials_waits_once(session, short_block_wait):
    """The short block of new sign-ins (429 to a request without credentials): one wait,
    the request is sent again without credentials, then signed once as usual."""
    unsigned = []

    def blocked(signed, _body):
        """Only the first request (without credentials) is blocked."""
        if not signed:
            unsigned.append(1)
        return not signed and len(unsigned) == 1

    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}, blocked=blocked) as dev:
        await shelly.restart(session, 2, dev.origin, gen2_auth())
    assert [h is not None for _m, _p, h, _b in dev.requests] == [False, False, True]


async def test_429_twice_before_credentials_raises_blocked(session, short_block_wait):
    """A 429 still there after the one wait: AuthBlockedError, and no password was sent."""
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}, blocked=lambda signed, _b: not signed) as dev:
        with pytest.raises(shelly.AuthBlockedError, match="temporarily blocks sign-ins"):
            await shelly.restart(session, 2, dev.origin, gen2_auth())
    assert [h for _m, _p, h, _b in dev.requests] == [None, None]


async def test_429_to_signed_request_is_not_repeated(session, monkeypatch):
    """A 429 to the signed request (the brute-force block): raised at once without a wait,
    the request is not repeated. The error is an AuthError (severity error, the run stops)."""
    monkeypatch.setattr(shelly, "AUTH_BLOCK_WAIT_S", 30)  # a wait would end the test by its time-out
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}, blocked=lambda signed, _b: signed) as dev:
        with pytest.raises(shelly.AuthBlockedError) as err:
            await asyncio.wait_for(shelly.restart(session, 2, dev.origin, gen2_auth()), 5)
    assert isinstance(err.value, shelly.AuthError)
    assert len(dev.requests) == 2 and dev.requests[1][2].startswith("Digest ")


async def test_429_without_credentials_is_an_ordinary_refusal(session, monkeypatch):
    """Without stored credentials a 429 is not a sign-in block (it can come from a busy
    endpoint, and the request may be a command): the general text, no second request."""
    monkeypatch.setattr(shelly, "AUTH_BLOCK_WAIT_S", 30)
    async with FakeDevice(routes={"/rpc": rpc(None)}, blocked=lambda _signed, _b: True) as dev:
        with pytest.raises(shelly.ShellyError, match=r"rejected the request \(HTTP 429\)") as err:
            await asyncio.wait_for(shelly.restart(session, 2, dev.origin, None), 5)
    assert not isinstance(err.value, shelly.AuthError) and len(dev.requests) == 1


@pytest.mark.parametrize(("config", "extended", "method"), [
    ({"cloud": {"enable": True}}, True, "Shelly.GetStatus"),  # battery and cloud from one request
    ({"cloud": {"enable": True}}, False, "Cloud.GetStatus"),
    ({}, True, "Sys.GetStatus"),
    ({"ble": {"enable": True}}, False, "Script.List"),
    ({}, True, "Webhook.List"),
    ({}, True, "Schedule.List"),
])
async def test_block_during_an_optional_request_ends_the_settings_read(session, config, extended, method):
    """A sign-in block in an optional request of a settings read is raised (not taken
    for an unknown value): the device gets no further request in this read."""
    results = {"Shelly.GetConfig": {"sys": {"device": {"name": "Kitchen"}}, **config}, "Shelly.GetStatus": {},
               "Cloud.GetStatus": {}, "Sys.GetStatus": {}, "Script.List": {"scripts": []},
               "Webhook.List": {"hooks": []}, "Schedule.List": {"jobs": []}}
    blocked = lambda signed, body: signed and body["method"] == method
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc_by_method(results)}, blocked=blocked) as dev:
        with pytest.raises(shelly.AuthBlockedError):
            await shelly.read_settings(session, 2, dev.origin, "Plus1", gen2_auth(), extended=extended)
    assert dev.requests[-1][3]["method"] == method and dev.requests[-1][2] is not None


async def test_stored_ha1_of_another_device(session):
    """A stored HA1 of another device ID is never sent."""
    auth = shelly.DeviceAuth(gen=2, ha1=sha256(f"admin:other:{PASSWORD}"), realm="other")
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}) as dev:
        with pytest.raises(shelly.ShellyError, match="another device ID"):
            await shelly.restart(session, 2, dev.origin, auth)
    assert len(dev.requests) == 1


@pytest.mark.parametrize("challenge", [
    f'Digest qop="auth", realm="{REALM}", nonce="1", algorithm=MD5',  # weaker algorithm
    'Basic realm="shelly"',  # would carry the plain password
    f'Digest qop="auth-int", realm="{REALM}", nonce="1", algorithm=SHA-256',  # no "auth" quality of protection
    'Digest qop="auth", nonce="1", algorithm=SHA-256',  # no realm
])
async def test_gen2_no_authentication_downgrade(session, challenge):
    """A Gen2+ challenge other than Digest SHA-256 with realm, nonce and qop auth is
    refused; the password never leaves the app."""
    async with FakeDevice(password=PASSWORD, routes={"/rpc": rpc(None)}, challenge=challenge) as dev:
        with pytest.raises(shelly.ShellyError, match="authentication request"):
            await shelly.restart(session, 2, dev.origin, gen2_auth())
    assert [h for _m, _p, h, _b in dev.requests] == [None]


async def test_gen1_basic_auth_and_query(session):
    """Gen1: settings are written as query parameters (encoded), the challenge is
    answered with Basic; a Digest challenge is refused for Gen1."""
    async with FakeDevice(gen=1, password=PASSWORD, routes={"/settings": {"name": "x"}}) as dev:
        await shelly.write_settings(session, 1, dev.origin, {"name": "Küche & Flur", "eco": True},
                                    shelly.DeviceAuth(gen=1, password=PASSWORD))
    _m, path, header, _b = dev.requests[1]
    assert path == "/settings?name=K%C3%BCche+%26+Flur&eco_mode_enabled=true"
    assert header == "Basic " + base64.b64encode(b"admin:pw-1").decode()
    challenge = f'Digest qop="auth", realm="{REALM}", nonce="1", algorithm=SHA-256'
    async with FakeDevice(gen=1, password=PASSWORD, routes={"/settings": {}}, challenge=challenge) as dev:
        with pytest.raises(shelly.ShellyError, match="Unexpected authentication request"):
            await shelly.read_settings(session, 1, dev.origin, "SHSW-1", shelly.DeviceAuth(gen=1, password=PASSWORD))
    assert len(dev.requests) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirects_are_never_followed(session, status):
    """A redirect could lead out of the LAN: the target never gets a request, for
    GET (device info) and POST (RPC) alike; the error names the status, not the target."""
    async with FakeDevice(routes={"/shelly": GEN2_INFO, "/rpc": rpc(None)}) as target:
        def moved(_request, _body):
            return web.Response(status=status, headers={"Location": target.origin + "/shelly"})

        async with FakeDevice(routes={"/shelly": moved, "/rpc": moved}) as dev:
            text = rf"answered with a redirect \(HTTP {status}\) – redirects are not followed"
            with pytest.raises(shelly.ShellyError, match=text) as err:
                await shelly.device_info(session, dev.origin)
            assert type(err.value) is shelly.ShellyError and target.origin not in str(err.value)
            with pytest.raises(shelly.ShellyError, match=text):
                await shelly.restart(session, 2, dev.origin)
        assert target.requests == []
    assert len(dev.requests) == 2


@pytest.mark.parametrize("location", ["https://{host}/shelly", "HTTPS://{host}/rpc", " wss://{host}/rpc"])
async def test_redirect_to_https_is_named(session, location):
    """A Shelly that sends plain HTTP to HTTPS (Enhanced Security) raises
    HttpsRequiredError for GET and POST; the redirect is not followed and its target
    is not part of the message."""
    async with FakeDevice(routes={"/shelly": GEN2_INFO, "/rpc": rpc(None)}) as target:
        host = target.origin.removeprefix("http://")
        def moved(_request, _body):
            return web.Response(status=301, headers={"Location": location.format(host=host)})

        async with FakeDevice(routes={"/shelly": moved, "/rpc": moved}) as dev:
            with pytest.raises(shelly.HttpsRequiredError, match="requires HTTPS") as err:
                await shelly.device_info(session, dev.origin)
            assert str(err.value) == shelly.HTTPS_REQUIRED and host not in str(err.value)
            with pytest.raises(shelly.HttpsRequiredError):
                await shelly.restart(session, 2, dev.origin)
            with pytest.raises(shelly.HttpsRequiredError):  # Try again: before "No device information"
                await shelly.answers(session, dev.origin)
        assert target.requests == []
    assert len(dev.requests) == 3


async def test_redirect_is_never_an_answer(session):
    """A redirect without a target gets the general text; a redirect whose body is a
    JSON object is not taken as the device's answer."""
    no_target = web.Response(status=302)
    with_body = web.json_response(GEN2_INFO, status=301, headers={"Location": "/elsewhere"})
    async with FakeDevice(routes={"/shelly": no_target, "/rpc": with_body}) as dev:
        with pytest.raises(shelly.ShellyError, match=r"redirect \(HTTP 302\)") as err:
            await shelly.device_info(session, dev.origin)
        assert not isinstance(err.value, shelly.HttpsRequiredError)
        with pytest.raises(shelly.ShellyError, match=r"redirect \(HTTP 301\)"):
            await shelly.device_rpc(session, dev.origin, "Shelly.GetDeviceInfo")


async def test_answer_size_limit(session):
    """An answer up to MAX_DEVICE_BODY is read, a larger one is refused."""
    limit = shelly.MAX_DEVICE_BODY
    payload = json.dumps(GEN2_INFO)
    fits = web.Response(body=payload.encode().ljust(limit), content_type="application/json")
    async with FakeDevice(routes={"/shelly": fits}) as dev:
        assert (await shelly.device_info(session, dev.origin))["version"] == "1.3.3"
    too_large = web.Response(body=payload.encode().ljust(limit + 1), content_type="application/json")
    async with FakeDevice(routes={"/shelly": too_large}) as dev:
        with pytest.raises(shelly.ShellyError, match="too large"):
            await shelly.device_info(session, dev.origin)


@pytest.mark.parametrize(("answer", "error", "text"), [
    (web.Response(status=404, text="Not Found"), shelly.UnsupportedError, "Not supported by this device"),
    (web.Response(status=500, text="Internal error"), shelly.ShellyError, r"rejected the request \(HTTP 500\)"),
    (web.Response(text="<html>not json</html>"), shelly.ShellyError, "Invalid response"),
    (web.json_response([1, 2]), shelly.ShellyError, "Invalid response"),
])
async def test_get_errors(session, answer, error, text):
    """HTTP errors and unusable answers of a GET become clear device errors."""
    async with FakeDevice(gen=1, routes={"/settings": answer}) as dev:
        with pytest.raises(error, match=text):
            await shelly.device_get(session, dev.origin, "/settings")


@pytest.mark.parametrize(("status", "answer", "error", "text"), [
    (404, {"id": 1, "error": {"code": 404, "message": "No handler for Foo.Bar"}}, shelly.UnsupportedError,
     r"Not supported by this device \(Foo.Bar\)"),
    (500, {"id": 1, "error": {"code": -103, "message": "Invalid argument 'id'"}}, shelly.ShellyError,
     r"^Device reports: Invalid argument 'id' \(code -103\)$"),
    (200, {"id": 1, "error": {"code": -114, "message": "Busy"}}, shelly.ShellyError, r"^Device reports: Busy \(code -114\)$"),
    (200, {"id": 1, "error": {"code": -113}}, shelly.ShellyError, r"^Device reports: code -113$"),
    (200, {"id": 1, "error": "out of memory"}, shelly.ShellyError, "^Device reports: out of memory$"),
    (200, {"id": 1, "error": {"code": -103, "message": "bad\nforged line\x1b[31m"}}, shelly.ShellyError,
     r"^Device reports: badforged line\[31m \(code -103\)$"),
    (200, {"id": 1, "error": {"code": -104, "message": "-104: Timed out"}}, shelly.ShellyError,
     r"^Device reports: Timed out \(code -104\)$"),
    (500, None, shelly.ShellyError, r"rejected the request \(HTTP 500\)"),
    (200, None, shelly.ShellyError, "Invalid response"),
])
async def test_rpc_errors(session, status, answer, error, text):
    """RPC errors carry the device's message with its code, not the raw error object (also
    with an HTTP error status); a missing handler means the device lacks the feature."""
    response = web.json_response(answer, status=status) if answer else web.Response(status=status, text="oops")
    async with FakeDevice(routes={"/rpc": response}) as dev:
        with pytest.raises(error, match=text):
            await shelly.device_rpc(session, dev.origin, "Foo.Bar")


@pytest.mark.parametrize(("error", "text"), [
    ({"code": -104, "message": "-104: Timed out"}, "Timed out (code -104)"),
    ({"code": -104, "message": "-104:Timed out"}, "Timed out (code -104)"),
    ({"code": -104, "message": "-104:"}, "code -104"),
    ({"code": -114, "message": "-1: Connection error: -14"}, "-1: Connection error: -14 (code -114)"),
    ({"code": -104, "message": "-1040: Timed out"}, "-1040: Timed out (code -104)"),
    ({"code": -104, "message": "Timed out -104: x"}, "Timed out -104: x (code -104)"),
    ({"code": 104, "message": "-104: Timed out"}, "-104: Timed out (code 104)"),
])
def test_device_error_shows_the_code_once(error, text):
    """A message that starts with its own code loses that prefix; another number or a code
    elsewhere in the text stays."""
    assert shelly.device_error(error) == text


async def test_rpc_result_shapes(session):
    """A result that is no object is an empty answer; a missing feature is
    "unsupported" for the callers (Wi-Fi signal of a device without Wi-Fi)."""
    async with FakeDevice(routes={"/rpc": rpc(None)}) as dev:
        assert await shelly.device_rpc(session, dev.origin, "Shelly.Reboot") == {}
    async with FakeDevice(routes={"/rpc": rpc_by_method({})}) as dev:
        assert await shelly.wifi_signal(session, 2, dev.origin) is None


async def test_network_error_of_a_password_change_carries_no_secret(session):
    """Gen1 sends the new password in the query: a broken answer (aiohttp names the
    URL in such errors) is reported without the URL."""
    def garbage(request, _body):
        request.transport.write(b"HTTP/1.1 999999 Broken\r\nContent-Length: x\r\n\r\n")  # no valid HTTP answer
        request.transport.close()
        return web.Response()

    async with FakeDevice(gen=1, routes={"/settings/login": garbage}) as dev:
        with pytest.raises(shelly.ShellyError, match="Device not reachable") as err:
            await shelly.set_protection(session, 1, dev.origin, None, shelly.DeviceAuth(gen=1, password="Secret-77"))
    assert "Secret-77" not in str(err.value) and "login" not in str(err.value)
    assert dev.requests[0][1].endswith("password=Secret-77")  # the device got it, the message does not


# ---------------------------------------------------------------- Backups: Gen1 write-back over HTTP


def gen1_http(device, refuse_writes=False):
    """Route of every path: the in-memory FakeGen1 behind real HTTP (query pairs in
    their order, repeated names kept); a refused value is HTTP 400, a missing
    endpoint 404. refuse_writes: every request with parameters is answered 401 –
    the password works for reading only."""
    async def answer(request, _body):
        pairs = list(request.query.items())
        if refuse_writes and pairs:
            return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="shelly1"'})
        try:
            return await device.get(request.path, pairs)
        except shelly.UnsupportedError:
            return web.Response(status=404, text="Not Found")
        except shelly.ShellyError:
            return web.Response(status=400, text="Bad request")
    return answer


async def test_gen1_restore_over_http(session):
    """A Gen1 backup read and written back with the password over HTTP: endpoint by
    endpoint, lists as comma text, the action addresses as repeated urls[]."""
    device = FakeGen1()
    async with FakeDevice(gen=1, password=PASSWORD, routes={"*": gen1_http(device)}) as dev:
        target = shelly_backup.Target(session, 1, dev.origin, shelly.DeviceAuth(gen=1, password=PASSWORD))
        backup = await shelly_backup.read(target)
        device.settings["relays"][0].update(default_state="on", schedule_rules=[])
        device.settings["led_status_disable"] = True
        device.actions["out_on_url"][0] = {"index": 0, "urls": ["http://192.168.1.2/on"], "enabled": True}
        backup["actions"]["out_on_url"][0] = {"index": 0, "urls": ["http://192.168.1.3/a", "http://192.168.1.3/b"],
                                              "enabled": True}
        dev.requests.clear()
        result = await shelly_backup.restore(target, backup, ["settings", "actions"], lambda _s: None)
    assert [p["open"] for p in result["parts"]] == [[], []] and result["confirmed"] is True
    assert device.settings["relays"][0]["schedule_rules"] == ["0700-0123456-on"]
    assert device.actions["out_on_url"][0]["urls"] == ["http://192.168.1.3/a", "http://192.168.1.3/b"]
    written = [path for _m, path, header, _b in dev.requests if header and "?" in path]
    assert "/settings/relay/0?default_state=off&schedule_rules=0700-0123456-on" in written
    assert "/settings?led_status_disable=false" in written
    assert ("/settings/actions?index=0&name=out_on_url&enabled=true"
            "&urls%5B%5D=http://192.168.1.3/a&urls%5B%5D=http://192.168.1.3/b") in written


async def test_gen1_refused_values_are_written_one_by_one(session):
    """An endpoint that refuses its values is written again value by value: the
    others are applied, the refused ones are named with the device's answer, a
    value without a query form is never sent."""
    device = FakeGen1()
    device.settings.update(bad=1, grid=[[1, 2]])
    device.settings["relays"][0]["bad"] = 1
    async with FakeDevice(gen=1, routes={"*": gen1_http(device)}) as dev:
        target = shelly_backup.Target(session, 1, dev.origin, None)
        backup = await shelly_backup.read(target)
        device.settings.update(bad=2, grid=[[3]], led_status_disable=True)
        device.settings["relays"][0]["bad"] = 2
        dev.requests.clear()
        result = await shelly_backup.restore(target, backup, ["settings"], lambda _s: None)
    errors = {o["key"]: o["error"] for o in result["parts"][0]["open"]}
    assert errors == {"bad": "Shelly rejected the request (HTTP 400)",
                      "relays.0 · bad": "Shelly rejected the request (HTTP 400)",
                      "grid": "This value cannot be written to a Gen1 device"}
    assert device.settings["led_status_disable"] is False
    written = [path for _m, path, _h, _b in dev.requests if "?" in path]
    assert written == ["/settings?led_status_disable=false&bad=1", "/settings?led_status_disable=false",
                       "/settings?bad=1", "/settings/relay/0?bad=1"]


async def test_gen1_restore_stops_when_the_password_is_refused(session):
    """A password refused while writing ends the restore at once (no value by value)."""
    device = FakeGen1()
    routes = {"*": gen1_http(device, refuse_writes=True)}
    async with FakeDevice(gen=1, password=PASSWORD, routes=routes) as dev:
        target = shelly_backup.Target(session, 1, dev.origin, shelly.DeviceAuth(gen=1, password=PASSWORD))
        backup = await shelly_backup.read(target)
        device.settings["led_status_disable"] = True
        device.settings["relays"][0]["default_state"] = "on"
        with pytest.raises(shelly.AuthError, match="rejected the password"):
            await shelly_backup.restore(target, backup, ["settings"], lambda _s: None)
    assert len([path for _m, path, _h, _b in dev.requests if "?" in path]) == 2  # one write and its retry
