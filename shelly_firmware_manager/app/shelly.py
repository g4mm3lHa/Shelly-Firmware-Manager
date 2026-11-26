"""Access to Shelly devices: shared helpers, authentication, device requests, settings.

All functions are stateless and receive the shared aiohttp session. Device addresses
come from shelly_net (private networks only), the manufacturer's servers are reached
through shelly_cloud; both build on the errors and text helpers defined here.

Security:
- Devices are only contacted at the origin pinned by shelly_net.resolve_device_origin
  (private IP, no DNS rebinding); redirects are never followed and
  every answer is size-limited (MAX_DEVICE_BODY, MAX_WS_FRAME). A redirect to HTTPS
  (the device's Enhanced Security) raises HttpsRequiredError, any other redirect names
  its HTTP status; the redirect target is never shown or logged.
- Device authentication: Gen1 Basic Auth, Gen2+ Digest Auth (SHA-256, user
  "admin"). Credentials are only sent in the scheme matching the device's
  generation (no downgrade from Digest to Basic, no MD5, no Digest without qop
  "auth") and only after a challenge; a rejected password is not tried twice.
  Each request answers a fresh challenge (no nonce is reused).
- Sign-in block of firmware 2.0+ (HTTP 429, on the WebSocket an RPC error with code
  429) while credentials are stored: AuthBlockedError. Only a request sent without
  credentials is sent again, once, after AUTH_BLOCK_WAIT_S (the short block while the
  device's table of open sign-ins is full); a signed request answered with 429 is
  never repeated, so a retry never counts as a failed attempt. Without stored
  credentials a 429 is an ordinary refused request and is not repeated.
- Values written to a device (name, NTP server, power-on action, AP/Bluetooth
  switch, password protection) are validated against strict patterns or the
  options the device itself reported. Gen1 passwords are sent as query
  parameters (documented API); errors raised here never contain the URL.
- Gen2+ installations use the device's RPC WebSocket at the same pinned origin
  (OtaChannel): no redirects (one to HTTPS/WSS raises HttpsRequiredError), frame size
  limit, SHA-256 Digest auth only.
"""

import asyncio
import base64
import contextlib
import datetime
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal

import aiohttp
from yarl import URL

# Build date at the start of a Gen2+ build_id ("20241011-114455/1.4.4-g6d2a586") or a Gen1
# version text ("20230913-112003/v1.14.0-gcb84623"): YYYYMMDD-HHMMSS
BUILD_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-\d{6}(?:/|$)")
_ERROR_LABEL_RE = re.compile(r"\b(?:message|url)=(?=['\"])")  # labels of aiohttp's ClientResponseError
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)(\d+))?")

DEVICE_TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_DEVICE_BODY = 1024 * 1024  # upper limit for a device response
AUTH_USER = "admin"  # fixed user name of Gen2+ devices, also the Gen1 default
DIGEST_PARAM_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]*))')
# auth_domain of a Gen2+ /shelly answer accepted as a Digest realm: 1-64 letters, digits, hyphens
AUTH_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Wait before a request without credentials is sent once more after an HTTP 429: firmware
# 2.0+ answers 429 to new sign-ins for about 2 s while its table of open sign-ins is full;
# 2.5 s lies just past that window
AUTH_BLOCK_WAIT_S = 2.5
# HTTP statuses of a redirect (the set aiohttp itself follows); a device answer with one
# of them is never followed and never read as an answer
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Schemes of a redirect target that mean the device only speaks HTTPS (Enhanced Security)
SECURE_SCHEMES = ("https://", "wss://")
# Message of a device that redirects plain HTTP to HTTPS (HttpsRequiredError)
HTTPS_REQUIRED = "The Shelly requires HTTPS (Enhanced Security) – not supported by this app yet"

# Gen2+ RPC over WebSocket (used during an installation to receive the OTA events)
OTA_WS_CONNECT_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10)
OTA_WS_ANSWER_S = 15  # wait for the answer to Shelly.Update (and the auth round trip)
MAX_WS_FRAME = 64 * 1024  # upper limit for one WebSocket frame from the device
OTA_EVENTS = ("ota_begin", "ota_progress", "ota_success", "ota_error")
WS_HA2_INPUT = "dummy_method:dummy_uri"  # fixed HA2 input of the WebSocket Digest auth

# Device settings that can be edited in the UI
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,64}$")  # printable, max. 64 characters
NTP_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$")  # host[:port]
# Access point of a Gen2+ Shelly (Wifi.SetConfig "ap"): network name of up to 29
# characters (None = the default name, the device ID), protected with a password or
# open; the password is write-only on the device and only passed through by this app
AP_SSID_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,29}$")
# WPA2 passphrase: 8 to 63 printable ASCII characters (no umlauts or accents: their byte
# length differs and some phones and PCs cannot connect then)
AP_PASSWORD_RE = re.compile(r"^[\x20-\x7e]{8,63}$")
AP_PASSWORD_BAD_RE = re.compile(r"[^\x20-\x7e]")  # first character outside printable ASCII
AP_SECURITY = ("keep", "password", "open")  # keep: the protection the access point has now
# Gen2+: config key prefix -> (RPC namespace, documented "initial_state" values)
GEN2_OUTPUTS = {
    "switch": ("Switch", ("off", "on", "restore_last", "match_input")),
    "light": ("Light", ("off", "on", "restore_last")),
    "rgb": ("RGB", ("off", "on", "restore_last")),
    "rgbw": ("RGBW", ("off", "on", "restore_last")),
    "cct": ("CCT", ("off", "on", "restore_last")),
}
# Gen1: "default_state" values; "switch" (follow input) only where the device has an input
GEN1_STATES = ("off", "on", "last")
GEN1_OUTPUT_KINDS = ("relay", "light", "color", "white")  # /settings/<kind>/<index>
# Gen1 types (prefixes) that report mode "white" but have only /settings/light/<i>:
# Dimmer 1/2, Duo, Vintage. RGBW2 and the Bulbs use /settings/color|white/<i> by mode.
GEN1_LIGHT_ONLY = ("SHDM", "SHBDUO", "SHVIN")


class ShellyError(RuntimeError):
    """Expected error – shown to the user as a message."""


class AuthError(ShellyError):
    """The device requires a password or rejected the stored one (severity: error)."""


class AuthBlockedError(AuthError):
    """The device temporarily blocks sign-ins (HTTP 429: too many failed attempts or too
    many open sign-ins, firmware 2.0+); nothing was executed.

    A subclass of AuthError: every caller that stops at a refused password stops here
    as well instead of sending more requests, and the severity is an error."""


def _blocked() -> AuthBlockedError:
    """The error of a device that blocks sign-ins (the one place of its text)."""
    return AuthBlockedError(
        "The Shelly temporarily blocks sign-ins (too many attempts) – wait a few minutes and try again"
    )


class HttpsRequiredError(ShellyError):
    """The device redirects plain HTTP to HTTPS (severity: error).

    Gen2+ Enhanced Security: always on for Shellys shipped with firmware 2.0 or later;
    an older one does it once Enhanced Security is switched on and an HTTPS certificate
    is installed. The redirect is never followed and this app does not speak HTTPS to
    devices, so the device cannot be checked, updated or backed up."""


class UnsupportedError(ShellyError):
    """The device does not provide the requested method/endpoint (not an outage)."""


class OtaError(ShellyError):
    """The device reported a failed update (Gen2+ event ota_error)."""


class ChannelUnavailable(Exception):  # not an error of the device, hence no "Error" suffix
    """The WebSocket could not send the update command (nothing reached the device)."""


# Feature state values stored per device besides True (on/active) and False:
UNSUPPORTED = "unsupported"  # the device/firmware does not offer the feature
# None = unknown (not read yet, or the last read failed) – never shown as "off"


def clip(value: object, limit: int = 200) -> str:
    """Limit external values before they are stored or displayed."""
    return str(value if value is not None else "")[:limit]


def plain(value: object, limit: int = 200) -> str:
    """clip for device texts that also leave the app's own UI (Home Assistant
    notification, log lines): control and format characters (line breaks, escape
    sequences, bidi overrides …) and line/paragraph separators are removed."""
    return "".join(c for c in clip(value, limit) if unicodedata.category(c)[0] != "C" and c not in "\u2028\u2029")


def device_error(error: object) -> str:
    """Readable text of the error object of a Gen2+ answer ({"code": -113, "message":
    "…"}): the device's message with its code, e.g. "Internal error: … (code -113)";
    anything else as it came. A message that starts with the same code ("-104: Timed
    out") loses that prefix, so the code is shown once. Device text, so cleaned like
    plain (it reaches log lines)."""
    if not isinstance(error, dict):
        return plain(error)
    message, code = plain(error.get("message"), 160).strip(), error.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        return message or plain(error)
    message = message.removeprefix(f"{code}:").lstrip()  # the message itself is already stripped
    return f"{message} (code {code})" if message else f"code {code}"


def parse_json(text: str | bytes) -> Any:
    """json.loads for data from outside: a value nested too deeply raises a
    RecursionError in json, which is turned into the ValueError of invalid JSON."""
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None


def same_secret(given: object, expected: str) -> bool:
    """Constant-time comparison of a value from outside (token, seal) with the expected
    ASCII text; another type or a non-ASCII text is simply not equal (hmac refuses to
    compare non-ASCII strings with a TypeError)."""
    return isinstance(given, str) and given.isascii() and hmac.compare_digest(given, expected)


def error_text(err: object, limit: int = 200) -> str:
    """Text of an error for status messages and the log: the field labels of
    aiohttp's HTTP errors ("408, message='Request Timeout', url='https://…'") are
    dropped, the values – also the URL – stay; limited like clip."""
    return clip(_ERROR_LABEL_RE.sub("", str(err if err is not None else "")), limit)


def parse_version(text: object) -> tuple[int, int, int, int, int]:
    """Comparable tuple from Shelly version strings.

    Gen1: '20230913-112003/v1.14.0-gcb84623', Gen2+: '1.4.4' or '1.5.0-beta2'.
    Final versions rank higher than pre-releases of the same number.
    """
    match = VERSION_RE.search(str(text or ""))
    if not match:
        raise ShellyError(f"Unknown version format: {clip(text, 80)!r}")
    major, minor, patch, pre, pre_num = match.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2}.get(pre, 3)
    return int(major), int(minor), int(patch), rank, int(pre_num or 0)


# --------------------------------------------------------------------------- Responses


async def read_limited(resp: aiohttp.ClientResponse, limit: int, message: str) -> bytes:
    """Complete response body, aborting as soon as it exceeds limit bytes."""
    raw = bytearray()
    async for chunk in resp.content.iter_chunked(64 * 1024):
        raw += chunk
        if len(raw) > limit:
            raise ShellyError(message)
    return bytes(raw)


# --------------------------------------------------------------------------- Authentication


def _sha256(text: str) -> str:
    """Hex SHA-256 of a UTF-8 string (Digest Auth building block)."""
    return hashlib.sha256(text.encode()).hexdigest()


def gen2_ha1(realm: str, password: str) -> str:
    """HA1 of the Gen2+ Digest Auth: SHA-256('admin:<realm>:<password>'). The realm is
    the device's auth domain, which is normally its device ID (see Protection)."""
    return _sha256(f"{AUTH_USER}:{realm}:{password}")


@dataclass(frozen=True, slots=True)
class DeviceAuth:
    """Credentials for one device. The secrets are excluded from repr() so they
    can never end up in a log line or an error message by accident.

    gen1 uses password (Basic Auth). Gen2+ uses either a stored ha1 bound to
    realm (the device's auth domain, normally its device ID) or a plain password
    from which HA1 is derived. A stored ha1 only answers a challenge of exactly
    that realm.
    """

    gen: int
    password: str | None = field(default=None, repr=False)
    ha1: str | None = field(default=None, repr=False)
    realm: str | None = None

    def authorization(self, challenge: str, method: str, uri: str) -> str:
        """Authorization header answering the device's WWW-Authenticate challenge."""
        scheme, _, params = challenge.partition(" ")
        if self.gen == 1:
            if scheme.lower() != "basic" or self.password is None:
                raise ShellyError("Unexpected authentication request from the Shelly")
            token = base64.b64encode(f"{AUTH_USER}:{self.password}".encode()).decode()
            return f"Basic {token}"
        # Gen2+: Digest with SHA-256 only – never fall back to Basic (plain password)
        values = {m[0].lower(): m[1] or m[2] for m in DIGEST_PARAM_RE.findall(params)}
        realm, nonce = values.get("realm", ""), values.get("nonce", "")
        if scheme.lower() != "digest" or values.get("algorithm", "SHA-256").upper() != "SHA-256":
            raise ShellyError("Unexpected authentication request from the Shelly")
        if not realm or not nonce or "auth" not in [q.strip() for q in values.get("qop", "auth").split(",")]:
            raise ShellyError("Incomplete authentication request from the Shelly")
        if self.ha1:
            if realm != self.realm:
                raise ShellyError("Stored device password belongs to another device ID – enter it again")
            ha1 = self.ha1
        else:
            ha1 = gen2_ha1(realm, self.password or "")
        cnonce, nc = secrets.token_hex(8), "00000001"
        response = _sha256(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{_sha256(f'{method}:{uri}')}")
        return (
            f'Digest username="{AUTH_USER}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
            f'algorithm=SHA-256, qop=auth, nc={nc}, cnonce="{cnonce}", response="{response}"'
        )

    def ws_auth(self, challenge: object) -> dict[str, Any]:
        """"auth" object for Gen2+ RPC over WebSocket, answering a 401 challenge.

        Official Gen2 docs: the challenge is the JSON in error.message (auth_type
        "digest", nonce, realm = device ID, algorithm SHA-256); the response is
        SHA-256(ha1:nonce:nc:cnonce:auth:ha2) with ha2 = SHA-256("dummy_method:dummy_uri"),
        nc = "00000001" and a numeric cnonce. The same realm check and SHA-256-only
        rule as for HTTP apply (no downgrade)."""
        if self.gen == 1 or not isinstance(challenge, dict):
            raise ShellyError("Unexpected authentication request from the Shelly")
        if challenge.get("auth_type") != "digest" or str(challenge.get("algorithm", "SHA-256")).upper() != "SHA-256":
            raise ShellyError("Unexpected authentication request from the Shelly")
        realm, nonce = str(challenge.get("realm") or ""), challenge.get("nonce")
        if not realm or nonce in (None, "") or not isinstance(nonce, (str, int)) or isinstance(nonce, bool):
            raise ShellyError("Incomplete authentication request from the Shelly")
        if self.ha1:
            if realm != self.realm:
                raise ShellyError("Stored device password belongs to another device ID – enter it again")
            ha1 = self.ha1
        else:
            ha1 = gen2_ha1(realm, self.password or "")
        cnonce, nc = secrets.randbelow(2**31), "00000001"
        response = _sha256(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{_sha256(WS_HA2_INPUT)}")
        return {
            "realm": realm,
            "username": AUTH_USER,
            "nonce": nonce,
            "cnonce": cnonce,
            "nc": nc,
            "response": response,
            "algorithm": "SHA-256",
        }


# Query of a device request: parameters by name, or pairs where a name repeats (Gen1 "urls[]")
Query = Mapping[str, str] | Sequence[tuple[str, str]]


def _redirect_error(status: int, location: str | None) -> ShellyError:
    """Error for a redirect answer of a device: HttpsRequiredError if the target is
    HTTPS/WSS (the scheme only, compared without case), else a ShellyError naming the
    status. The target itself is never part of the text (it is device-controlled and
    may name an address outside the LAN)."""
    if _secure_target(location):
        return HttpsRequiredError(HTTPS_REQUIRED)
    return ShellyError(f"The Shelly answered with a redirect (HTTP {status}) – redirects are not followed")


def _secure_target(location: str | None) -> bool:
    """True if a redirect target (Location header) starts with https:// or wss://."""
    return (location or "").strip().lower().startswith(SECURE_SCHEMES)


async def _call(
    session: aiohttp.ClientSession,
    origin: str,
    path: str,
    auth: DeviceAuth | None,
    params: Query | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """One request to a device; answers an authentication challenge once.

    Returns (HTTP status, parsed JSON or None). Redirects are forbidden (they
    could lead out of the LAN): a redirect status raises before the body is read
    (HttpsRequiredError for a redirect to HTTPS, see _redirect_error). The response
    size is limited. Order of the status checks: redirect first, then the sign-in
    block (429), then the password challenge (401).

    With credentials (auth not None) an HTTP 429 means the device blocks sign-ins:
    to a request sent without credentials (the device's short block of new sign-ins)
    the request is sent once more without credentials after AUTH_BLOCK_WAIT_S; a 429
    after that wait or to the signed request raises AuthBlockedError at once – a
    signed request is never repeated. Without credentials a 429 is returned like any
    other status (it can also be a busy endpoint, and the request may be a command).
    At most three requests: unsigned, unsigned after the wait, signed.
    """
    url = URL(origin + path)
    if params:
        url = url.with_query(params)
    method = "GET" if body is None else "POST"
    headers: dict[str, str] | None = None  # the Authorization header once credentials are sent
    waited = False  # the one wait after a 429 to a request without credentials is used
    for _attempt in range(3):
        async with session.request(
            method,
            url,
            json=body,
            headers=headers,
            allow_redirects=False,
            timeout=DEVICE_TIMEOUT,
        ) as resp:
            if resp.status in REDIRECT_STATUSES:
                raise _redirect_error(resp.status, resp.headers.get("Location"))
            if resp.status == 429 and auth is not None:
                if headers is not None or waited:
                    raise _blocked()
                waited = True  # the response is closed before the wait
            elif resp.status == 401:
                if headers is not None or auth is None:
                    raise AuthError(
                        "The Shelly rejected the password"
                        if headers is not None
                        else "The Shelly requires a password – store it in the device's password dialog"
                    )
                headers = {
                    "Authorization": auth.authorization(
                        resp.headers.get("WWW-Authenticate", ""), method, url.raw_path_qs
                    )
                }
                continue
            else:
                raw = await read_limited(resp, MAX_DEVICE_BODY, "Response from the device too large")
                try:
                    return resp.status, parse_json(raw.decode("utf-8", "replace"))
                except ValueError:
                    return resp.status, None
        await asyncio.sleep(AUTH_BLOCK_WAIT_S)
    raise ShellyError("Authentication with the Shelly failed")  # not reached


async def _device_json(
    session: aiohttp.ClientSession,
    origin: str,
    path: str,
    auth: DeviceAuth | None = None,
    params: Query | None = None,
) -> dict[str, Any]:
    """GET on the device; HTTP errors and invalid JSON become a ShellyError."""
    status, data = await _call(session, origin, path, auth, params=params)
    if status == 404:
        raise UnsupportedError(f"Not supported by this device ({clip(path, 40)})")
    if status >= 400:
        raise ShellyError(f"Shelly rejected the request (HTTP {status})")
    if not isinstance(data, dict):
        raise ShellyError("Invalid response from the device")
    return data


async def _rpc(
    session: aiohttp.ClientSession,
    origin: str,
    method: str,
    params: dict[str, Any] | None = None,
    auth: DeviceAuth | None = None,
) -> dict[str, Any]:
    """Gen2+ JSON-RPC call; device errors become a ShellyError with the device's message."""
    body = {"id": 1, "method": method, "params": params or {}}
    status, result = await _call(session, origin, "/rpc", auth, body=body)
    if isinstance(result, dict) and result.get("error"):  # also sent with HTTP errors
        error = result["error"]
        if isinstance(error, dict) and error.get("code") == 404:  # "No handler for <method>"
            raise UnsupportedError(f"Not supported by this device ({clip(method, 40)})")
        raise ShellyError(f"Device reports: {device_error(error)}")
    if status >= 400:
        raise ShellyError(f"Shelly rejected the request (HTTP {status})")
    if not isinstance(result, dict):
        raise ShellyError("Invalid response from the device")
    answer = result.get("result")
    return answer if isinstance(answer, dict) else {}


# Device requests for shelly_backup (the same limits and error handling as everything here)
device_get = _device_json
device_rpc = _rpc


async def device_info(
    session: aiohttp.ClientSession,
    origin: str,
    auth_for: Callable[[int], DeviceAuth | None] | None = None,
    on_identity: Callable[[int, str | None], object] | None = None,
) -> dict[str, Any]:
    """Uniform device information for Gen1 and Gen2+.

    auth_for(gen) returns the credentials once the generation is known (/shelly
    itself never needs authentication). shelly_id is the Gen2+ device ID, which is
    the realm Shelly.SetAuth takes and normally also the Digest Auth realm (see
    Protection for the exception). on_identity(gen, shelly_id) is called right after
    /shelly – so a device whose password is missing or wrong is still identified
    and a device password can be set for it.

    The Gen2+ /shelly answer already is Shelly.GetDeviceInfo (id, mac, model,
    gen, fw_id, ver, app, auth_en), so the second request is only made if a field
    is missing. auth_enabled is the protection status reported by the device
    itself (Gen2+ auth_en, Gen1 auth; None if not reported) – never derived from
    stored credentials."""
    info = await _device_json(session, origin, "/shelly")
    gen = int_field(info, "gen", 1)
    if on_identity:
        on_identity(gen, clip(info.get("id"), 64) if gen >= 2 else None)
    protected = info.get("auth_en" if gen >= 2 else "auth")
    protected = protected if isinstance(protected, bool) else None
    if gen >= 2 and not all(info.get(k) for k in ("app", "ver", "fw_id")):
        auth = auth_for(gen) if auth_for else None
        info = await _device_json(session, origin, "/rpc/Shelly.GetDeviceInfo", auth)
    try:
        return _identity(info, gen, protected)
    except (KeyError, TypeError, ValueError):  # a field is missing or has the wrong type
        raise ShellyError("Incomplete device information from the Shelly") from None


def int_field(data: dict[str, Any], key: str, default: int) -> int:
    """Integer field of a device or manufacturer answer; ShellyError if it is no number."""
    value = data.get(key) or default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ShellyError(f"Invalid value for {key!r} in the answer")
    try:
        return int(value)
    except ValueError:
        raise ShellyError(f"Invalid value for {key!r} in the answer") from None


def _identity(info: dict[str, Any], gen: int, protected: bool | None) -> dict[str, Any]:
    """Uniform identity fields from a /shelly or Shelly.GetDeviceInfo answer
    (raises KeyError/TypeError/ValueError if a required field is missing)."""
    if gen >= 2:
        return {
            "auth_enabled": protected,
            "gen": int(info["gen"]),
            "shelly_id": clip(info.get("id"), 64),
            "type": clip(info["app"], 64),
            "model": clip(info.get("model"), 64),
            "mac": clip(info.get("mac"), 32).upper(),
            "version": plain(info["ver"], 64),
            "build": plain(info["fw_id"], 128),
        }
    return {
        "auth_enabled": protected,
        "gen": 1,
        "shelly_id": None,
        "type": clip(info["type"], 64),
        "model": clip(info["type"], 64),
        "mac": clip(info.get("mac"), 32).upper(),
        "version": plain(info["fw"], 128),
        "build": plain(info["fw"], 128),
    }


async def trigger_update(
    session: aiohttp.ClientSession, gen: int, origin: str, firmware_url: str, auth: DeviceAuth | None = None
) -> None:
    """Tells the device to load the firmware from firmware_url."""
    if gen == 1:
        await _device_json(session, origin, "/ota", auth, {"url": firmware_url})
    else:
        await _rpc(session, origin, "Shelly.Update", {"url": firmware_url}, auth)


async def _refuse_redirect(_session: object, _context: object, params: aiohttp.TraceRequestRedirectParams) -> None:
    """aiohttp trace hook: a redirect would lead away from the device – refuse it.

    aiohttp calls it before it follows anything. A redirect to HTTPS/WSS raises
    HttpsRequiredError (defensive: such a device already fails the check over HTTP);
    the target is never part of the text."""
    if _secure_target(params.response.headers.get("Location")):
        raise HttpsRequiredError(HTTPS_REQUIRED)
    raise ShellyError("The Shelly answered the WebSocket request with a redirect")


class OtaChannel:
    """Gen2+ installation over the device's RPC WebSocket (ws://<ip>/rpc).

    The official Gen2 API reports the update only as notifications of the sys
    component (ota_begin, ota_progress with progress_percent, ota_success,
    ota_error); there is no pollable OTA status and HTTP gets no notifications.
    The device sends notifications over the WebSocket once the client has sent a
    request with "src" – here the Shelly.Update command itself.

    Security: the URL is built from the pinned private-IP origin (no DNS),
    redirects are refused (one to HTTPS/WSS as HttpsRequiredError), frames are
    limited to MAX_WS_FRAME, no proxy from the environment, Digest auth only as
    SHA-256 (DeviceAuth.ws_auth); a sign-in block (code 429) is handled as over
    HTTP (see update). Only the event name, a clipped message and the percentage
    leave this class.

    Usage: async with OtaChannel(origin, auth) as ota:
               accepted = await ota.update(url)
               async for event in ota.events(): ...
    Connection errors raise from __aenter__; the caller may then fall back to HTTP.
    """

    def __init__(self, origin: str, auth: DeviceAuth | None = None, headers: Any = None) -> None:
        """Prepare the channel; nothing is sent before 'async with'."""
        if not origin.startswith("http://"):
            raise ShellyError("Invalid device address")
        self.url = "ws://" + origin[len("http://") :] + "/rpc"
        self.auth = auth
        self.headers = headers
        self.src = f"sfm-{secrets.token_hex(4)}"
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._early: list[dict[str, Any]] = []  # OTA events received while waiting for the command answer

    async def __aenter__(self) -> "OtaChannel":
        """Open the WebSocket (own short-lived session: redirects are refused)."""
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(_refuse_redirect)
        self.session = aiohttp.ClientSession(
            headers=self.headers, trust_env=False, timeout=OTA_WS_CONNECT_TIMEOUT, trace_configs=[trace]
        )
        try:
            self.ws = await self.session.ws_connect(
                self.url, timeout=aiohttp.ClientWSTimeout(ws_close=5), max_msg_size=MAX_WS_FRAME
            )
        except BaseException:
            await self.session.close()
            raise
        return self

    async def __aexit__(
        self, _type: type[BaseException] | None, _exc: BaseException | None, _tb: TracebackType | None
    ) -> None:
        """Close the WebSocket and its session."""
        try:
            if self.ws is not None:
                await self.ws.close()
        finally:
            if self.session is not None:
                await self.session.close()

    async def update(self, firmware_url: str) -> Literal[True] | None:
        """Send Shelly.Update. True if the device accepted it, None if no answer
        arrived in time (the update may still run – never send it twice).
        Raises AuthError/ShellyError if the device refused it.

        The same sign-in rules as for HTTP (_call), with RPC error codes: code 401 to
        the unsigned frame is answered once with a signed frame, code 401 to the signed
        one raises "rejected the password". With credentials, code 429 to the unsigned
        frame is answered once with a new unsigned frame after AUTH_BLOCK_WAIT_S; code
        429 after that or to the signed frame raises AuthBlockedError. A frame refused
        with 401 or 429 was not executed, so a retry frame never runs the update twice.
        A retry frame that cannot be sent raises a ShellyError (nothing was installed);
        it never leads to the update command over HTTP (ChannelUnavailable is raised for
        the first frame only)."""
        params = {"url": firmware_url}
        ws = self._socket()
        try:
            await ws.send_json({"id": 1, "src": self.src, "method": "Shelly.Update", "params": params})
        except (aiohttp.ClientError, OSError, RuntimeError) as err:  # closed before sending
            raise ChannelUnavailable(type(err).__name__) from None
        signed = False  # a frame with credentials was sent
        waited = False  # the one wait after code 429 to an unsigned frame is used
        for frame_id in (1, 2, 3):  # unsigned, unsigned after the wait, signed
            answer = await self._answer(frame_id)
            if answer is None:
                return None
            error = answer.get("error")
            details = error if isinstance(error, dict) else {}
            code = details.get("code")
            retry: dict[str, Any] = {"id": frame_id + 1, "src": self.src, "method": "Shelly.Update", "params": params}
            if code == 429 and self.auth is not None:
                if signed or waited:
                    raise _blocked()
                waited = True
                await asyncio.sleep(AUTH_BLOCK_WAIT_S)
            elif code == 401:
                if self.auth is None:
                    raise AuthError("The Shelly requires a password – store it in the device's password dialog")
                if signed:
                    raise AuthError("The Shelly rejected the password")
                try:
                    challenge = parse_json(str(details.get("message") or ""))
                except ValueError:
                    raise ShellyError("Invalid authentication request from the Shelly") from None
                retry["auth"] = self.auth.ws_auth(challenge)
                signed = True
            elif error:
                raise ShellyError(f"Device reports: {device_error(error)}")
            else:
                return True
            try:
                await ws.send_json(retry)
            except (aiohttp.ClientError, OSError, RuntimeError):  # the connection ended: not over HTTP now
                raise ShellyError(
                    "Connection to the Shelly lost before the update command – nothing was installed"
                ) from None
        raise ShellyError("Authentication with the Shelly failed")  # not reached

    def _socket(self) -> aiohttp.ClientWebSocketResponse:
        """The open WebSocket; only valid inside 'async with'."""
        if self.ws is None:
            raise ChannelUnavailable("not connected")
        return self.ws

    async def _answer(self, frame_id: int) -> dict[str, Any] | None:
        """Answer frame with frame_id; OTA events arriving meanwhile are kept.
        None if no answer arrives within OTA_WS_ANSWER_S or the connection ends."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + OTA_WS_ANSWER_S
        while (left := deadline - loop.time()) > 0:
            try:
                frame = await self._receive(left)
            except TimeoutError:
                return None
            if frame is None:
                return None
            if frame.get("id") == frame_id and ("result" in frame or "error" in frame):
                return frame
            self._early.extend(_ota_events(frame))
        return None

    async def _receive(self, wait: float | None = None) -> dict[str, Any] | None:
        """Next JSON object frame; None when the connection is closed."""
        while True:
            msg = await self._socket().receive(timeout=wait)
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR):
                    return None
                continue  # binary, ping/pong: not part of the RPC protocol
            try:
                frame = parse_json(msg.data)
            except ValueError:
                continue
            if isinstance(frame, dict):
                return frame

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """OTA events of the device until the connection ends (device restart)."""
        while self._early:
            yield self._early.pop(0)
        while (frame := await self._receive()) is not None:
            for event in _ota_events(frame):
                yield event


def _ota_events(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """OTA events of the sys component in a NotifyEvent frame, reduced to
    {"event", "msg", "progress"} (progress: 0–100 or None)."""
    params = frame.get("params") if frame.get("method") == "NotifyEvent" else None
    events = params.get("events") if isinstance(params, dict) else None
    result: list[dict[str, Any]] = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or event.get("component") != "sys" or event.get("event") not in OTA_EVENTS:
            continue
        progress = event.get("progress_percent")
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            progress = None
        else:
            progress = max(0, min(100, int(progress)))
        result.append({"event": event["event"], "msg": clip(event.get("msg"), 200), "progress": progress})
    return result


# --------------------------------------------------------------------------- Device settings


def _output(key: str, name: object, state: object, options: Iterable[str]) -> dict[str, Any]:
    """One output for the power-on dialog. The current value is always selectable."""
    text = clip(state, 32)
    options = list(options)
    return {
        "key": key,
        "name": clip(name, 64) or key,
        "aop": text,
        "options": options + ([text] if text and text not in options else []),
    }


def _ap_config(access_point: dict[str, Any]) -> dict[str, Any]:
    """Access point details of the form (never a password: the device does not return
    it): network name (None = default), whether it is open (None if not reported), and
    the range extender switch (None if the device has none)."""
    extender = access_point.get("range_extender")
    extender_on = extender.get("enable") if isinstance(extender, dict) else None
    is_open = access_point.get("is_open")
    return {
        "ssid": clip(access_point.get("ssid"), 29) or None,
        "open": is_open if isinstance(is_open, bool) else None,
        "range_extender": extender_on if isinstance(extender_on, bool) else None,
    }


def _valid_ap_config(value: object) -> dict[str, Any]:
    """Checks the access point form: {"enable", "ssid", "security", "password",
    "accept_open", "range_extender"}. Turning it off changes nothing else. An open
    access point needs accept_open; the password needs 8 to 63 printable ASCII
    characters (WPA2; anything else the Shelly decides, its refusal is shown in the
    form)."""
    if not isinstance(value, dict) or not isinstance(value.get("enable"), bool):
        raise ValueError("Invalid access point settings")
    if not value["enable"]:
        return {"enable": False}
    clean: dict[str, Any] = {"enable": True}
    if "ssid" in value:
        ssid = value["ssid"]
        if ssid is not None and (not isinstance(ssid, str) or not AP_SSID_RE.fullmatch(ssid.strip())):
            raise ValueError("Invalid network name (max. 29 printable characters)")
        clean["ssid"] = (ssid or "").strip() or None
    security = value.get("security")
    if security not in AP_SECURITY:
        raise ValueError("Invalid access point security")
    clean["security"] = security
    if security == "password":
        password = value.get("password")
        if not isinstance(password, str) or not password:
            raise ValueError("Enter a password for the access point")
        bad = AP_PASSWORD_BAD_RE.search(password)
        if bad:
            shown = bad.group() if bad.group().isprintable() else "?"  # an invisible character is not shown
            raise ValueError(f"The password contains a character the Shelly cannot use: {shown}")
        if not AP_PASSWORD_RE.fullmatch(password):
            raise ValueError("Invalid access point password (8 to 63 characters)")
        clean["password"] = password
    if security == "open" and value.get("accept_open") is not True:
        raise ValueError("Open access point not confirmed")
    if "range_extender" in value:
        if not isinstance(value["range_extender"], bool):
            raise ValueError("Invalid range extender (true or false)")
        clean["range_extender"] = value["range_extender"]
    return clean


def _ap_payload(ap_config: dict[str, Any]) -> dict[str, Any]:
    """The "ap" object of Wifi.SetConfig for a checked form: a password protects the
    access point, an open one gets none (null restores the open default)."""
    payload: dict[str, Any] = {"enable": ap_config["enable"]}
    if "ssid" in ap_config:
        payload["ssid"] = ap_config["ssid"]
    if ap_config.get("security") == "password":
        payload["pass"], payload["is_open"] = ap_config["password"], False
    elif ap_config.get("security") == "open":
        payload["pass"], payload["is_open"] = None, True
    if "range_extender" in ap_config:
        payload["range_extender"] = {"enable": ap_config["range_extender"]}
    return payload


def _flag(value: object) -> bool | str:
    """A reported on/off switch, else UNSUPPORTED (the device lacks the field)."""
    return value if isinstance(value, bool) else UNSUPPORTED


def build_date(text: object) -> str | None:
    """Build date "YYYY-MM-DD" from the start of a Gen2+ build ID or a Gen1 version
    text (see BUILD_DATE_RE); None if the text does not start with a valid date. The
    manufacturer's servers name no release date: the build date is the only one."""
    match = BUILD_DATE_RE.match(text) if isinstance(text, str) else None
    if not match:
        return None
    try:
        return datetime.date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


# Script that the Shelly integration of Home Assistant installs on a Gen2+ Shelly to use it
# as Bluetooth proxy (aioshelly BLE_SCRIPT_NAME); Home Assistant turns Bluetooth on again
# while it runs
BLE_PROXY_SCRIPT = "aioshelly_ble_integration"


async def _ble_proxy(session: aiohttp.ClientSession, origin: str, auth: DeviceAuth | None) -> bool | None:
    """True if Home Assistant's Bluetooth proxy script runs on the device (Script.List:
    name BLE_PROXY_SCRIPT, running), False if not or the device has no scripts, None
    if it could not be read; a sign-in block (AuthBlockedError) is raised."""
    try:
        data = await _rpc(session, origin, "Script.List", auth=auth)
    except UnsupportedError:
        return False
    except AuthBlockedError:  # a blocked device gets no further request in this read
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, list):
        return None
    return any(
        isinstance(e, dict) and e.get("name") == BLE_PROXY_SCRIPT and e.get("running") is True for e in scripts
    )


async def _cloud_connected(session: aiohttp.ClientSession, origin: str, auth: DeviceAuth | None) -> bool | None:
    """Gen2+: whether the device is connected to the Shelly cloud now (Cloud.GetStatus),
    None if it could not be read; a sign-in block (AuthBlockedError) is raised."""
    try:
        status = await _rpc(session, origin, "Cloud.GetStatus", auth=auth)
    except AuthBlockedError:  # a blocked device gets no further request in this read
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError):
        return None
    connected = status.get("connected")
    return connected if isinstance(connected, bool) else None


async def wifi_signal(
    session: aiohttp.ClientSession, gen: int, origin: str, auth: DeviceAuth | None = None
) -> int | None:
    """Wi-Fi signal of the device in dBm, read now (Gen2+ Wifi.GetStatus, Gen1 /status
    wifi_sta). None if the device is not connected over Wi-Fi (e.g. a Pro device on
    the cable, or no Wi-Fi at all); ShellyError if it is connected but reports no
    usable value (the dialog shows "unknown")."""
    if gen >= 2:
        try:
            status = await _rpc(session, origin, "Wifi.GetStatus", auth=auth)
        except UnsupportedError:
            return None
        connected = status.get("status") == "got ip"
    else:
        status = (await _device_json(session, origin, "/status", auth)).get("wifi_sta") or {}
        connected = isinstance(status, dict) and status.get("connected") is True
    if not connected:
        return None
    rssi = status.get("rssi") if isinstance(status, dict) else None
    if isinstance(rssi, bool) or not isinstance(rssi, (int, float)) or not -120 <= rssi <= 0:
        raise ShellyError("The device reported no usable Wi-Fi signal")  # shown as "unknown"
    return int(rssi)


def _rssi(status: object) -> int | None:
    """dBm of a Wi-Fi status object (Gen2+ wifi, Gen1 wifi_sta) while it is connected;
    None if not connected or no usable value."""
    if not isinstance(status, dict):
        return None
    connected = status.get("status") == "got ip" or status.get("connected") is True
    rssi = status.get("rssi")
    if not connected or isinstance(rssi, bool) or not isinstance(rssi, (int, float)) or not -120 <= rssi <= 0:
        return None
    return int(rssi)


async def link_status(
    session: aiohttp.ClientSession, gen: int, origin: str, auth: DeviceAuth | None = None
) -> dict[str, int | None]:
    """Running time and Wi-Fi signal in one request, read during an installation:
    {"uptime": seconds since the device started, "rssi": dBm}; a value is None if the
    device reports none (e.g. on the cable). Gen2+ Shelly.GetStatus (sys.uptime,
    wifi.rssi), Gen1 /status (uptime, wifi_sta.rssi). Errors are raised as usual."""
    if gen >= 2:
        status = await _rpc(session, origin, "Shelly.GetStatus", auth=auth)
        sys_status = status.get("sys")
        uptime = sys_status.get("uptime") if isinstance(sys_status, dict) else None
        wifi = status.get("wifi")
    else:
        status = await _device_json(session, origin, "/status", auth)
        uptime, wifi = status.get("uptime"), status.get("wifi_sta")
    if isinstance(uptime, bool) or not isinstance(uptime, (int, float)) or not 0 <= uptime < 2**40:
        uptime = None  # also NaN and infinity (JSON allows them)
    return {"uptime": int(uptime) if uptime is not None else None, "rssi": _rssi(wifi)}


def _positive(value: object) -> bool:
    """True if a reported period is a number above 0 (a sleeping battery device)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


async def _battery_status(session: aiohttp.ClientSession, origin: str, auth: DeviceAuth | None) -> dict[str, bool]:
    """{"battery": bool} from the Gen2+ sys status (wakeup_period, reported by battery
    devices only); {} if it could not be read – the stored value then stays; a sign-in
    block (AuthBlockedError) is raised."""
    try:
        status = await _rpc(session, origin, "Sys.GetStatus", auth=auth)
    except UnsupportedError:
        return {"battery": False}
    except AuthBlockedError:  # a blocked device gets no further request in this read
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError):
        return {}
    return {"battery": _positive(status.get("wakeup_period"))}


async def _sys_and_cloud(
    session: aiohttp.ClientSession, origin: str, auth: DeviceAuth | None
) -> tuple[dict[str, bool], bool | None]:
    """Gen2+ battery flag and cloud connection from one Shelly.GetStatus, whose result
    holds the status of every component under its key ("sys", "cloud"); used by an
    extended read while the cloud is on and the sys config reports no wakeup_period.

    Returns ({"battery": bool}, connected): battery from sys.wakeup_period as in
    _battery_status, connected from cloud.connected as in _cloud_connected. battery is
    left out (stored value kept) when the request fails in any way, unsupported
    included, or the answer has no "sys" object; connected is None when it fails or
    reports no boolean; a sign-in block (AuthBlockedError) is raised. Only these two
    values are taken: the rest of the answer (for example sys.available_updates, Wi-Fi
    addresses, other component states) is neither kept nor logged."""
    try:
        status = await _rpc(session, origin, "Shelly.GetStatus", auth=auth)
    except AuthBlockedError:  # a blocked device gets no further request in this read
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError):
        return {}, None
    sys_status = status.get("sys")
    battery = {"battery": _positive(sys_status.get("wakeup_period"))} if isinstance(sys_status, dict) else {}
    cloud = status.get("cloud")
    connected = cloud.get("connected") if isinstance(cloud, dict) else None
    return battery, connected if isinstance(connected, bool) else None


async def _list_active(
    read: Callable[[], Awaitable[dict[str, Any]]], key: str, active: Callable[[dict[str, Any]], bool]
) -> bool | str | None:
    """True/False whether a device list has an active entry; UNSUPPORTED if the
    device lacks the endpoint; None (unknown) on any other failure – a timeout or
    an authentication error is never taken for "unsupported". A sign-in block
    (AuthBlockedError) is raised: the device gets no further request."""
    try:
        data = await read()
    except UnsupportedError:
        return UNSUPPORTED
    except AuthBlockedError:  # a blocked device gets no further request in this read
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError):
        return None
    entries = data.get(key)
    if isinstance(entries, dict):  # Gen1 /settings/actions: {name: [entries]}
        entries = [e for group in entries.values() if isinstance(group, list) for e in group]
    if not isinstance(entries, list):
        return None
    return any(isinstance(e, dict) and active(e) for e in entries)


async def read_settings(
    session: aiohttp.ClientSession,
    gen: int,
    origin: str,
    fw_type: str | None,
    auth: DeviceAuth | None = None,
    extended: bool = False,
) -> dict[str, Any]:
    """Shelly name, NTP server, eco mode, AP, Bluetooth, cloud and websocket debug
    switch, battery flag and power-on action of all outputs – one request (Gen2+
    Shelly.GetConfig, Gen1 /settings).

    cloud_connected: whether the device is connected to the Shelly cloud while the
    cloud is on (Gen2+ one more request, Cloud.GetStatus – when extended and the sys
    status is read too, both come from one Shelly.GetStatus; None if unknown or off).
    ble_proxy: Gen2+ with Bluetooth on – whether Home Assistant's Bluetooth proxy
    script runs (one more request, Script.List; None if unknown).

    battery is True for a battery device that sleeps between wake-ups (Gen2+
    wakeup_period > 0 in the sys status – read only when extended, with Sys.GetStatus
    or, while the cloud is on, Shelly.GetStatus – or in the sys config; Gen1
    sleep_mode.period > 0) – such a device answers only while it is awake, so it is
    only ever detected from the device itself. Gen2+ leaves battery out (stored value
    kept) when it could not be read.

    Switch values are True/False or UNSUPPORTED. extended=True (checks, Reload Device
    Info and the read after a restore) also reads whether actions/webhooks and
    schedules are active: Gen2+ needs Webhook.List and Schedule.List (no batch RPC
    exists), Gen1 needs /settings/actions; schedules of Gen1 are part of /settings.
    Only the boolean "has an active entry" is kept – never URLs, calls or scripts.
    A failed optional request leaves its value unknown, but a sign-in block
    (AuthBlockedError) ends the read at once: the device gets no further request."""
    outputs: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    if gen >= 2:
        config = await _rpc(session, origin, "Shelly.GetConfig", auth=auth)
        system = config.get("sys") or {}
        name = (system.get("device") or {}).get("name")
        ntp = (system.get("sntp") or {}).get("server")
        eco = (system.get("device") or {}).get("eco_mode")
        access_point = (config.get("wifi") or {}).get("ap") or {}
        extra["ap"] = _flag(access_point.get("enable"))
        extra["ap_config"] = _ap_config(access_point)
        # Only the legacy global switch counts; firmware 2.0+ removed it (Bluetooth
        # then runs on demand) and ble.rpc.enable is no global switch.
        extra["ble"] = _flag((config.get("ble") or {}).get("enable"))
        extra["debug_ws"] = _flag(((system.get("debug") or {}).get("websocket") or {}).get("enable"))
        extra["cloud"] = _flag((config.get("cloud") or {}).get("enable"))
        # The sys status is read only when extended and the sys config gives no wakeup_period.
        sys_wanted = extended and not _positive(system.get("wakeup_period"))
        if extra["cloud"] is True and sys_wanted:
            # Both values wanted: one Shelly.GetStatus instead of Cloud.GetStatus and Sys.GetStatus;
            # after a failure no second request (battery then stays as stored).
            battery, extra["cloud_connected"] = await _sys_and_cloud(session, origin, auth)
            sys_wanted = False
        else:
            battery = {}
            extra["cloud_connected"] = (
                await _cloud_connected(session, origin, auth) if extra["cloud"] is True else None
            )
        extra["ble_proxy"] = await _ble_proxy(session, origin, auth) if extra["ble"] is True else False
        if _positive(system.get("wakeup_period")):
            extra["battery"] = True
        if extended:
            if sys_wanted:
                battery = await _battery_status(session, origin, auth)
            extra.update(battery)
            extra["actions"] = await _list_active(
                lambda: _rpc(session, origin, "Webhook.List", auth=auth), "hooks", lambda e: e.get("enable") is True
            )
            extra["schedules"] = await _list_active(
                lambda: _rpc(session, origin, "Schedule.List", auth=auth), "jobs", lambda e: e.get("enable") is True
            )
        for key, comp in config.items():
            kind, _, index = key.partition(":")
            if (
                kind in GEN2_OUTPUTS
                and index.isdigit()
                and isinstance(comp, dict)
                and "initial_state" in comp
            ):
                outputs.append(_output(key, comp.get("name"), comp["initial_state"], GEN2_OUTPUTS[kind][1]))
        outputs.sort(key=lambda o: (o["key"].split(":")[0], int(o["key"].split(":")[1])))
    else:
        settings = await _device_json(session, origin, "/settings", auth)
        name = settings.get("name")
        ntp = (settings.get("sntp") or {}).get("server")
        eco = settings.get("eco_mode_enabled")  # not in the official Gen1 docs, reported by /settings
        # Read-only on Gen1: enabling the AP disables the station mode (official
        # docs), the device would leave the network – see GEN1_AP_NOTE.
        extra["ap"] = _flag((settings.get("wifi_ap") or {}).get("enabled"))
        extra["ap_config"] = None  # no access point form on Gen1
        extra["ble"] = UNSUPPORTED  # Gen1 devices have no Bluetooth switch
        extra["debug_ws"] = UNSUPPORTED  # Gen1 devices have no websocket debug log
        cloud = settings.get("cloud") or {}
        extra["cloud"] = _flag(cloud.get("enabled") if isinstance(cloud, dict) else None)
        connected = cloud.get("connected") if isinstance(cloud, dict) and extra["cloud"] is True else None
        extra["cloud_connected"] = connected if isinstance(connected, bool) else None
        extra["ble_proxy"] = False
        extra["battery"] = _positive((settings.get("sleep_mode") or {}).get("period"))
        # Gen1 device ID (also its mDNS host name), e.g. shelly1-B929CC
        extra["gen1_hostname"] = clip((settings.get("device") or {}).get("hostname"), 64) or None
        if extended:
            channels = [
                c for c in (settings.get("relays") or []) + (settings.get("lights") or []) if isinstance(c, dict)
            ]
            if any("schedule" in c for c in channels):
                extra["schedules"] = any(c.get("schedule") is True and c.get("schedule_rules") for c in channels)
            else:
                extra["schedules"] = UNSUPPORTED
            extra["actions"] = await _list_active(
                lambda: _device_json(session, origin, "/settings/actions", auth),
                "actions",
                lambda e: e.get("enabled") is True and bool(e.get("urls")),
            )
        mode = settings.get("mode")
        if mode != "roller":  # roller mode: relays drive a cover, no power-on action
            for i, relay in enumerate(settings.get("relays") or []):
                if isinstance(relay, dict) and "default_state" in relay:
                    states = GEN1_STATES + (("switch",) if "btn_type" in relay else ())
                    outputs.append(_output(f"relay:{i}", relay.get("name"), relay["default_state"], states))
        # RGBW2/Bulb use /settings/color|white/<i> by mode, all others /settings/light/<i>
        light_only = str(fw_type).startswith(GEN1_LIGHT_ONLY)
        kind = mode if mode in ("color", "white") and not light_only else "light"
        dimmer = str(fw_type).startswith("SHDM")  # Shelly Dimmer 1/2 also support "switch"
        for i, light in enumerate(settings.get("lights") or []):
            if isinstance(light, dict) and "default_state" in light:
                states = GEN1_STATES + (("switch",) if dimmer else ())
                outputs.append(_output(f"{kind}:{i}", light.get("name"), light["default_state"], states))
    return {
        "shelly_name": clip(name, 64) or None,
        "ntp": clip(ntp, 260) or None,
        "eco": _flag(eco),
        "outputs": outputs,
        **extra,
    }


def validate_settings(changes: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Checks requested changes; returns them cleaned or raises ValueError."""
    clean: dict[str, Any] = {}
    if "name" in changes:
        name = changes["name"]
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError("Invalid name (max. 64 printable characters)")
        clean["name"] = name.strip()
    if "ntp" in changes:
        ntp = changes["ntp"]
        if not isinstance(ntp, str) or not NTP_RE.fullmatch(ntp.strip()):
            raise ValueError("Invalid NTP server (host name or IP address, optional :port)")
        clean["ntp"] = ntp.strip()
    for key, label in (
        ("eco", "eco mode"),
        ("ap", "access point"),
        ("ble", "Bluetooth"),
        ("debug_ws", "websocket debug log"),
        ("cloud", "cloud"),
    ):
        if key in changes:
            if not isinstance(changes[key], bool):
                raise ValueError(f"Invalid {label} (true or false)")
            clean[key] = changes[key]
    if "ap_config" in changes:
        if "ap" in changes:
            raise ValueError("Access point switch and settings at once")
        clean["ap_config"] = _valid_ap_config(changes["ap_config"])
    if "aop" in changes:
        known = {o["key"]: o["options"] for o in outputs}
        aop = changes["aop"]
        if not isinstance(aop, dict) or not aop:
            raise ValueError("No power-on action given")
        for key, value in aop.items():
            if value not in known.get(key, ()):
                raise ValueError(f"Invalid power-on action for {clip(key, 32)}")
        clean["aop"] = dict(aop)
    if not clean:
        raise ValueError("Nothing to change")
    return clean


async def write_settings(
    session: aiohttp.ClientSession, gen: int, origin: str, changes: dict[str, Any], auth: DeviceAuth | None = None
) -> bool:
    """Sends validated changes to the device. Returns True if it requests a restart."""
    restart = False
    if gen >= 2:
        system: dict[str, Any] = {}
        device = {k: changes[c] for c, k in (("name", "name"), ("eco", "eco_mode")) if c in changes}
        if device:
            system["device"] = device
        if "ntp" in changes:
            system["sntp"] = {"server": changes["ntp"]}
        if "debug_ws" in changes:  # only debug.websocket.enable – other debug outputs stay
            system["debug"] = {"websocket": {"enable": changes["debug_ws"]}}
        if system:
            result = await _rpc(session, origin, "Sys.SetConfig", {"config": system}, auth)
            restart |= bool(result.get("restart_required"))
        if "ap" in changes:  # only ap.enable – the device keeps all other Wi-Fi settings
            result = await _rpc(session, origin, "Wifi.SetConfig", {"config": {"ap": {"enable": changes["ap"]}}}, auth)
            restart |= bool(result.get("restart_required"))
        if "ap_config" in changes:  # the access point form; the client mode stays as it is
            payload = {"config": {"ap": _ap_payload(changes["ap_config"])}}
            result = await _rpc(session, origin, "Wifi.SetConfig", payload, auth)
            restart |= bool(result.get("restart_required"))
        if "ble" in changes:  # legacy global switch only (callers check it is supported)
            result = await _rpc(session, origin, "BLE.SetConfig", {"config": {"enable": changes["ble"]}}, auth)
            restart |= bool(result.get("restart_required"))
        if "cloud" in changes:  # only cloud.enable – the server address stays
            result = await _rpc(session, origin, "Cloud.SetConfig", {"config": {"enable": changes["cloud"]}}, auth)
            restart |= bool(result.get("restart_required"))
        for key, value in changes.get("aop", {}).items():
            kind, _, index = key.partition(":")
            output = {"id": int(index), "config": {"initial_state": value}}
            result = await _rpc(session, origin, f"{GEN2_OUTPUTS[kind][0]}.SetConfig", output, auth)
            restart |= bool(result.get("restart_required"))
        return restart

    # Gen1: settings are changed via GET with query parameters
    if "ap" in changes or "ap_config" in changes:
        raise ValueError(GEN1_AP_NOTE)
    if "ble" in changes:
        raise ValueError("Gen1 devices have no Bluetooth switch")
    if "debug_ws" in changes:
        raise ValueError(GEN1_DEBUG_WS_NOTE)
    query: dict[str, str] = {}
    if "name" in changes:
        query["name"] = changes["name"]
    if "ntp" in changes:
        query["sntp_server"] = changes["ntp"]
    if "eco" in changes:
        query["eco_mode_enabled"] = "true" if changes["eco"] else "false"
    if query:
        await _device_json(session, origin, "/settings", auth, query)
    if "cloud" in changes:
        await _device_json(session, origin, "/settings/cloud", auth, {"enabled": "1" if changes["cloud"] else "0"})
    for key, value in changes.get("aop", {}).items():
        kind, _, index = key.partition(":")
        if kind not in GEN1_OUTPUT_KINDS or not index.isdigit():
            raise ValueError(f"Unknown output {clip(key, 32)}")
        await _device_json(session, origin, f"/settings/{kind}/{int(index)}", auth, {"default_state": value})
    return restart


GEN1_AP_NOTE = (
    "Not available on Gen1: enabling the access point turns off the Wi-Fi client mode, "
    "the device would leave your network"
)
GEN1_DEBUG_WS_NOTE = "Gen1 devices have no websocket debug log"


@dataclass(frozen=True, slots=True)
class Protection:
    """Password protection of a device as its /shelly answer reports it.

    enabled: True/False, None if the device does not report it. device_id: the Gen2+
    device ID (None for Gen1 or when the answer has none); it is the realm a new
    password is set with (Shelly.SetAuth accepts only the device ID). auth_domain:
    the Gen2+ realm the device's current password is bound to (Shelly.GetDeviceInfo,
    null without a password) exactly as the device sent it – never shortened, checked
    by realm before it is used. It normally equals device_id; firmware 2.2.0 renamed
    the ID of the 1PM Mini Gen3/Gen4, and a password set during that time stays bound
    to the 2.2.0 ID after 2.2.1 restored the former one."""

    enabled: bool | None
    device_id: str | None = None
    auth_domain: str | None = None

    @property
    def realm(self) -> str | None:
        """Realm the device's current password is bound to: auth_domain while the
        protection is on, if it is 1-64 letters, digits or hyphens and differs from
        device_id only before the last hyphen (the same MAC suffix, as the 2.2.0
        rename keeps it); otherwise device_id. A realm the device does not challenge
        with is still refused by DeviceAuth."""
        domain, device_id = self.auth_domain, self.device_id
        if self.enabled is not True or not domain or not device_id or not AUTH_DOMAIN_RE.fullmatch(domain):
            return device_id
        prefix, _, suffix = domain.rpartition("-")
        id_prefix, _, id_suffix = device_id.rpartition("-")
        # rpartition gives an empty prefix without a hyphen: both need a name and a suffix
        return domain if prefix and id_prefix and suffix and suffix == id_suffix else device_id


async def protection_status(session: aiohttp.ClientSession, origin: str) -> Protection:
    """Password protection as reported by /shelly (no authentication needed), with
    the Gen2+ device ID and auth domain of the same answer (see Protection). An empty
    device ID becomes None, so it never replaces a known one."""
    info = await _device_json(session, origin, "/shelly")
    gen = int_field(info, "gen", 1)
    value = info.get("auth_en" if gen >= 2 else "auth")
    enabled = value if isinstance(value, bool) else None
    if gen < 2:
        return Protection(enabled)
    domain = info.get("auth_domain")
    return Protection(
        enabled,
        clip(info.get("id"), 64) or None,
        domain if isinstance(domain, str) else None,
    )


async def set_protection(
    session: aiohttp.ClientSession,
    gen: int,
    origin: str,
    realm: str | None,
    new_auth: DeviceAuth | None,
    auth: DeviceAuth | None = None,
) -> None:
    """Enable/change (new_auth = DeviceAuth of the new password) or disable
    (new_auth None) the device's password protection.

    Gen2+: Shelly.SetAuth with user admin, the realm and the HA1 (null disables).
    The realm must be the device ID, never its auth domain: Shelly.SetAuth accepts
    only the device ID, and new_auth.ha1 must be derived from the same realm. Gen1:
    /settings/login with user admin. The caller must verify the result – a lost or
    ambiguous answer proves nothing. Network errors are re-raised without any URL
    (Gen1 carries the password in the query)."""
    try:
        if gen >= 2:
            if not realm:
                raise ShellyError("Device ID unknown – run a check first")
            params = {"user": AUTH_USER, "realm": realm, "ha1": new_auth.ha1 if new_auth else None}
            await _rpc(session, origin, "Shelly.SetAuth", params, auth)
        elif new_auth:
            login = {"enabled": "1", "username": AUTH_USER, "password": new_auth.password or ""}
            await _device_json(session, origin, "/settings/login", auth, login)
        else:
            await _device_json(session, origin, "/settings/login", auth, {"enabled": "0"})
    except aiohttp.ClientError as err:
        raise ShellyError(f"Device not reachable ({type(err).__name__})") from None


async def restart(session: aiohttp.ClientSession, gen: int, origin: str, auth: DeviceAuth | None = None) -> None:
    """Restarts the device (Gen2+ Shelly.Reboot, Gen1 /reboot)."""
    if gen >= 2:
        await _rpc(session, origin, "Shelly.Reboot", auth=auth)
    else:
        await _device_json(session, origin, "/reboot", auth)


# ------------------------------------------------------------------------ Diagnosis of the connections

FETCH_TEST_TIMEOUT_S = 8  # seconds the device waits for the download server (HTTP.GET timeout)

PORT_TIMEOUT_S = 5  # seconds for opening the TCP connection to a device


async def fetch_test(
    session: aiohttp.ClientSession, origin: str, url: str, auth: DeviceAuth | None = None
) -> dict[str, Any]:
    """Gen2+: let the device fetch url itself (HTTP.GET) and return its answer
    {"code": HTTP status, "message": text}. The body is not taken over. A request the
    device could not make (timeout, no connection, refused) is a ShellyError with the
    device's message and code, e.g. "Deadline Exceeded (code -104)"."""
    answer = await _rpc(session, origin, "HTTP.GET", {"url": url, "timeout": FETCH_TEST_TIMEOUT_S}, auth)
    code = answer.get("code")
    return {
        "code": code if isinstance(code, int) and not isinstance(code, bool) else None,
        "message": clip(answer.get("message"), 80),
    }


# Error code of a device answer at the end of its text (device_error): "… (code -114)"
_FETCH_CODE_RE = re.compile(r"\(code (-?\d+)\)$")
# Low-level code at the end of the device's message, e.g. "-1: Connection error: -14"
_FETCH_DETAIL_RE = re.compile(r"error:\s*(-\d+)")


def fetch_error(text: str, url: str) -> str:
    """Readable text of a failed fetch of the connection test (fetch_test): the codes of
    the device's answer instead of its message, e.g. "No connection to <url>:
    connection error (Shelly code -114 / -14)". Texts without a device code (password,
    unsupported …) stay as they are; the raw text is kept by the caller for the log."""
    match = _FETCH_CODE_RE.search(text)
    if not match:
        return text
    code = match.group(1)
    detail = _FETCH_DETAIL_RE.search(text)
    codes = f"{code} / {detail.group(1)}" if detail and detail.group(1) != code else code
    if code == "-104":
        return f"No connection to {url}: timeout after {FETCH_TEST_TIMEOUT_S} s (Shelly code {codes})"
    if code == "-114":
        return f"No connection to {url}: connection error (Shelly code {codes})"
    return f"The device could not fetch {url} (Shelly code {codes})"


async def port_open(ip: str, port: int) -> float:
    """Seconds needed to open a TCP connection to ip:port (closed at once, nothing is
    sent). TimeoutError or OSError if the device does not accept it within
    PORT_TIMEOUT_S."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), PORT_TIMEOUT_S)
    elapsed = loop.time() - started
    writer.close()
    with contextlib.suppress(OSError):  # the device closed first: the connection was made all the same
        await writer.wait_closed()
    return elapsed


async def answers(session: aiohttp.ClientSession, origin: str) -> float:
    """Seconds until the device answered GET /shelly (no authentication needed);
    ShellyError if the answer is no device information."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    status, data = await _call(session, origin, "/shelly", None)
    if status >= 400 or not isinstance(data, dict):
        raise ShellyError(f"No device information (HTTP {status})")
    return loop.time() - started
