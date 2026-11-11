"""Access to Shelly devices and the manufacturer's firmware servers.

All functions are stateless and receive the shared aiohttp session.

Security:
- Devices are only contacted at private IP addresses (SSRF protection), the
  resolved IP is pinned (DNS rebinding) and redirects are forbidden.
- Device authentication: Gen1 Basic Auth, Gen2+ Digest Auth (SHA-256, user
  "admin"). Credentials are only sent in the scheme matching the device's
  generation (no downgrade from Digest to Basic) and only after a challenge.
- Metadata and firmware come only from shelly.cloud; every redirect is checked
  again. Metadata and Gen2+ firmware only via HTTPS. Gen1 firmware is offered by
  Shelly only via plain HTTP and without a checksum, so its integrity and origin
  CANNOT be verified – the SHA-256 computed here only identifies the stored file
  and detects later modification on disk.
- File names consist only of the SHA-256 plus a fixed extension.
- Values written to a device (name, NTP server, power-on action, AP/Bluetooth
  switch, password protection) are validated against strict patterns or the
  options the device itself reported. Gen1 passwords are sent as query
  parameters (documented API); errors raised here never contain the URL.
- Gen2+ installations use the device's RPC WebSocket at the same pinned origin
  (OtaChannel): no redirects, frame size limit, SHA-256 Digest auth only.
"""

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import secrets
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlsplit

import aiohttp
from tls import CertificatePins, explain_tls_error
from yarl import URL

MAX_FW_SIZE = 64 * 1024 * 1024  # upper limit for a firmware file
MIN_FW_SIZE = 1024  # anything smaller is certainly no firmware
FW_EXTENSIONS = (".zip", ".bin", ".gbl")
FW_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(zip|bin|gbl)$")
_ERROR_LABEL_RE = re.compile(r"\b(?:message|url)=(?=['\"])")  # labels of aiohttp's ClientResponseError
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)(\d+))?")

DEVICE_TIMEOUT = aiohttp.ClientTimeout(total=15)
CLOUD_TIMEOUT = aiohttp.ClientTimeout(total=30)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=300, sock_read=60)
MAX_DEVICE_BODY = 1024 * 1024  # upper limit for a device response
MAX_CLOUD_JSON = 2 * 1024 * 1024  # upper limit for a version offer / firmware list
AUTH_USER = "admin"  # fixed user name of Gen2+ devices, also the Gen1 default
DIGEST_PARAM_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]*))')

# Gen2+ RPC over WebSocket (used during an installation to receive the OTA events)
OTA_WS_CONNECT_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10)
OTA_WS_ANSWER_S = 15  # wait for the answer to Shelly.Update (and the auth round trip)
MAX_WS_FRAME = 64 * 1024  # upper limit for one WebSocket frame from the device
OTA_EVENTS = ("ota_begin", "ota_progress", "ota_success", "ota_error")
WS_HA2_INPUT = "dummy_method:dummy_uri"  # fixed HA2 input of the WebSocket Digest auth

GEN2_META_URL = "https://updates.shelly.cloud/update/{app}"
GEN1_META_URL = "https://api.shelly.cloud/files/firmware"

# Device settings that can be edited in the UI
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,64}$")  # printable, max. 64 characters
NTP_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$")  # host[:port]
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


class ShellyError(RuntimeError):
    """Expected error – shown to the user as a message."""


class AuthError(ShellyError):
    """The device requires a password or rejected the stored one (severity: error)."""


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


# --------------------------------------------------------------------------- Devices


def is_lan_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for addresses of a private network (no loopback, link-local, multicast …).

    is_private alone is not enough: Python also counts loopback/link-local as private.
    """
    return ip.is_private and not (
        ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
    )


async def resolve_device_origin(configuration_url: str | None) -> str:
    """Returns 'http://<private-ip>:<port>' from the integration's configuration_url."""
    parts = urlsplit(configuration_url or "")
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
        raise ShellyError("No usable local device address in the device registry")
    port = parts.port or 80

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    addresses = sorted({str(info[4][0]).split("%")[0] for info in infos})
    for address in addresses:
        if not is_lan_address(ipaddress.ip_address(address)):
            raise ShellyError(f"Device address {address} is not in a private network")

    ip = addresses[0]
    host = f"[{ip}]" if ":" in ip else ip
    return f"http://{host}:{port}"


def origin_ip(origin: str) -> str | None:
    """IP address from an origin created by resolve_device_origin."""
    return urlsplit(origin).hostname


# --------------------------------------------------------------------------- Authentication


def _sha256(text: str) -> str:
    """Hex SHA-256 of a UTF-8 string (Digest Auth building block)."""
    return hashlib.sha256(text.encode()).hexdigest()


def gen2_ha1(realm: str, password: str) -> str:
    """HA1 of the Gen2+ Digest Auth: SHA-256('admin:<device id>:<password>')."""
    return _sha256(f"{AUTH_USER}:{realm}:{password}")


@dataclass(frozen=True, slots=True)
class DeviceAuth:
    """Credentials for one device. The secrets are excluded from repr() so they
    can never end up in a log line or an error message by accident.

    gen1 uses password (Basic Auth). Gen2+ uses either a stored ha1 bound to
    realm (= Shelly device ID) or a plain password from which HA1 is derived.
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


async def _call(
    session: aiohttp.ClientSession,
    origin: str,
    path: str,
    auth: DeviceAuth | None,
    params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """One request to a device; answers an authentication challenge once.

    Returns (HTTP status, parsed JSON or None). Redirects are forbidden (they
    could lead out of the LAN) and the response size is limited.
    """
    url = URL(origin + path)
    if params:
        url = url.with_query(params)
    method = "GET" if body is None else "POST"
    headers: dict[str, str] | None = None
    for attempt in range(2):
        async with session.request(
            method,
            url,
            json=body,
            headers=headers,
            allow_redirects=False,
            timeout=DEVICE_TIMEOUT,
        ) as resp:
            if resp.status == 401:
                if attempt or auth is None:
                    raise AuthError(
                        "The Shelly rejected the password"
                        if attempt
                        else "The Shelly requires a password – store it in the device's password dialog"
                    )
                headers = {
                    "Authorization": auth.authorization(
                        resp.headers.get("WWW-Authenticate", ""), method, url.raw_path_qs
                    )
                }
                continue
            raw = await read_limited(resp, MAX_DEVICE_BODY, "Response from the device too large")
            try:
                return resp.status, json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                return resp.status, None
    raise ShellyError("Authentication with the Shelly failed")  # not reached


async def _device_json(
    session: aiohttp.ClientSession,
    origin: str,
    path: str,
    auth: DeviceAuth | None = None,
    params: dict[str, str] | None = None,
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
        raise ShellyError(f"Device reports: {clip(error)}")
    if status >= 400:
        raise ShellyError(f"Shelly rejected the request (HTTP {status})")
    if not isinstance(result, dict):
        raise ShellyError("Invalid response from the device")
    answer = result.get("result")
    return answer if isinstance(answer, dict) else {}


async def device_info(
    session: aiohttp.ClientSession,
    origin: str,
    auth_for: Callable[[int], DeviceAuth | None] | None = None,
    on_identity: Callable[[int, str | None], object] | None = None,
) -> dict[str, Any]:
    """Uniform device information for Gen1 and Gen2+.

    auth_for(gen) returns the credentials once the generation is known (/shelly
    itself never needs authentication). shelly_id is the Gen2+ device ID, which is
    also the Digest Auth realm. on_identity(gen, shelly_id) is called right after
    /shelly – so a device whose password is missing or wrong is still identified
    and a device password can be set for it.

    The Gen2+ /shelly answer already is Shelly.GetDeviceInfo (id, mac, model,
    gen, fw_id, ver, app, auth_en), so the second request is only made if a field
    is missing. auth_enabled is the protection status reported by the device
    itself (Gen2+ auth_en, Gen1 auth; None if not reported) – never derived from
    stored credentials."""
    info = await _device_json(session, origin, "/shelly")
    gen = _int_field(info, "gen", 1)
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


def _int_field(data: dict[str, Any], key: str, default: int) -> int:
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
            "version": clip(info["ver"], 64),
            "build": clip(info["fw_id"], 128),
        }
    return {
        "auth_enabled": protected,
        "gen": 1,
        "shelly_id": None,
        "type": clip(info["type"], 64),
        "model": clip(info["type"], 64),
        "mac": clip(info.get("mac"), 32).upper(),
        "version": clip(info["fw"], 128),
        "build": clip(info["fw"], 128),
    }


async def trigger_update(
    session: aiohttp.ClientSession, gen: int, origin: str, firmware_url: str, auth: DeviceAuth | None = None
) -> None:
    """Tells the device to load the firmware from firmware_url."""
    if gen == 1:
        await _device_json(session, origin, "/ota", auth, {"url": firmware_url})
    else:
        await _rpc(session, origin, "Shelly.Update", {"url": firmware_url}, auth)


async def _refuse_redirect(_session: object, _context: object, _params: object) -> None:
    """aiohttp trace hook: a redirect would lead away from the device – refuse it."""
    raise ShellyError("The Shelly answered the WebSocket request with a redirect")


class OtaChannel:
    """Gen2+ installation over the device's RPC WebSocket (ws://<ip>/rpc).

    The official Gen2 API reports the update only as notifications of the sys
    component (ota_begin, ota_progress with progress_percent, ota_success,
    ota_error); there is no pollable OTA status and HTTP gets no notifications.
    The device sends notifications over the WebSocket once the client has sent a
    request with "src" – here the Shelly.Update command itself.

    Security: the URL is built from the pinned private-IP origin (no DNS),
    redirects are refused, frames are limited to MAX_WS_FRAME, no proxy from the
    environment, Digest auth only as SHA-256 (DeviceAuth.ws_auth). Only the event
    name, a clipped message and the percentage leave this class.

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
        Raises AuthError/ShellyError if the device refused it."""
        params = {"url": firmware_url}
        ws = self._socket()
        try:
            await ws.send_json({"id": 1, "src": self.src, "method": "Shelly.Update", "params": params})
        except (aiohttp.ClientError, OSError, RuntimeError) as err:  # closed before sending
            raise ChannelUnavailable(type(err).__name__) from None
        answer = await self._answer(1)
        if answer is None:
            return None
        error = answer.get("error")
        if isinstance(error, dict) and error.get("code") == 401:
            if self.auth is None:
                raise AuthError("The Shelly requires a password – store it in the device's password dialog")
            try:
                challenge = json.loads(str(error.get("message") or ""))
            except ValueError:
                raise ShellyError("Invalid authentication request from the Shelly") from None
            frame: dict[str, Any] = {"id": 2, "src": self.src, "method": "Shelly.Update", "params": params}
            frame["auth"] = self.auth.ws_auth(challenge)
            await ws.send_json(frame)
            answer = await self._answer(2)
            if answer is None:
                return None
            error = answer.get("error")
            if isinstance(error, dict) and error.get("code") == 401:
                raise AuthError("The Shelly rejected the password")
        if error:
            raise ShellyError(f"Device reports: {clip(error)}")
        return True

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
                frame = json.loads(msg.data)
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


def _flag(value: object) -> bool | str:
    """A reported on/off switch, else UNSUPPORTED (the device lacks the field)."""
    return value if isinstance(value, bool) else UNSUPPORTED


async def _list_active(
    read: Callable[[], Awaitable[dict[str, Any]]], key: str, active: Callable[[dict[str, Any]], bool]
) -> bool | str | None:
    """True/False whether a device list has an active entry; UNSUPPORTED if the
    device lacks the endpoint; None (unknown) on any other failure – a timeout or
    an authentication error is never taken for "unsupported"."""
    try:
        data = await read()
    except UnsupportedError:
        return UNSUPPORTED
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
    """Shelly name, NTP server, eco mode, AP and Bluetooth switch and power-on action
    of all outputs – one request (Gen2+ Shelly.GetConfig, Gen1 /settings).

    Switch values are True/False or UNSUPPORTED. extended=True (checks and Reload
    Device Info only) also reads whether actions/webhooks and schedules are active:
    Gen2+ needs Webhook.List and Schedule.List (no batch RPC exists), Gen1 needs
    /settings/actions; schedules of Gen1 are part of /settings. Only the boolean
    "has an active entry" is kept – never URLs, calls or scripts."""
    outputs: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    if gen >= 2:
        config = await _rpc(session, origin, "Shelly.GetConfig", auth=auth)
        system = config.get("sys") or {}
        name = (system.get("device") or {}).get("name")
        ntp = (system.get("sntp") or {}).get("server")
        eco = (system.get("device") or {}).get("eco_mode")
        extra["ap"] = _flag(((config.get("wifi") or {}).get("ap") or {}).get("enable"))
        # Only the legacy global switch counts; firmware 2.0+ removed it (Bluetooth
        # then runs on demand) and ble.rpc.enable is no global switch.
        extra["ble"] = _flag((config.get("ble") or {}).get("enable"))
        if extended:
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
        extra["ble"] = UNSUPPORTED  # Gen1 devices have no Bluetooth switch
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
        # RGBW2/Bulb use /settings/color|white/<i>, dimmers and others /settings/light/<i>
        kind = mode if mode in ("color", "white") else "light"
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
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ValueError("Invalid name (max. 64 printable characters)")
        clean["name"] = name.strip()
    if "ntp" in changes:
        ntp = changes["ntp"]
        if not isinstance(ntp, str) or not NTP_RE.match(ntp.strip()):
            raise ValueError("Invalid NTP server (host name or IP address, optional :port)")
        clean["ntp"] = ntp.strip()
    for key, label in (("eco", "eco mode"), ("ap", "access point"), ("ble", "Bluetooth")):
        if key in changes:
            if not isinstance(changes[key], bool):
                raise ValueError(f"Invalid {label} (true or false)")
            clean[key] = changes[key]
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
        if system:
            result = await _rpc(session, origin, "Sys.SetConfig", {"config": system}, auth)
            restart |= bool(result.get("restart_required"))
        if "ap" in changes:  # only ap.enable – the device keeps all other Wi-Fi settings
            result = await _rpc(session, origin, "Wifi.SetConfig", {"config": {"ap": {"enable": changes["ap"]}}}, auth)
            restart |= bool(result.get("restart_required"))
        if "ble" in changes:  # legacy global switch only (callers check it is supported)
            result = await _rpc(session, origin, "BLE.SetConfig", {"config": {"enable": changes["ble"]}}, auth)
            restart |= bool(result.get("restart_required"))
        for key, value in changes.get("aop", {}).items():
            kind, _, index = key.partition(":")
            output = {"id": int(index), "config": {"initial_state": value}}
            result = await _rpc(session, origin, f"{GEN2_OUTPUTS[kind][0]}.SetConfig", output, auth)
            restart |= bool(result.get("restart_required"))
        return restart

    # Gen1: settings are changed via GET with query parameters
    if "ap" in changes or "ble" in changes:
        raise ValueError(GEN1_AP_NOTE if "ap" in changes else "Gen1 devices have no Bluetooth switch")
    query: dict[str, str] = {}
    if "name" in changes:
        query["name"] = changes["name"]
    if "ntp" in changes:
        query["sntp_server"] = changes["ntp"]
    if "eco" in changes:
        query["eco_mode_enabled"] = "true" if changes["eco"] else "false"
    if query:
        await _device_json(session, origin, "/settings", auth, query)
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


async def protection_status(session: aiohttp.ClientSession, origin: str) -> bool | None:
    """Password protection as reported by /shelly (no authentication needed):
    True/False, None if the device does not report it."""
    info = await _device_json(session, origin, "/shelly")
    value = info.get("auth_en" if int(info.get("gen") or 1) >= 2 else "auth")
    return value if isinstance(value, bool) else None


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

    Gen2+: Shelly.SetAuth with user admin, realm = device ID and the HA1 (null
    disables; realm = Shelly device ID). Gen1: /settings/login with user admin. The caller must verify the
    result – a lost or ambiguous answer proves nothing. Network errors are
    re-raised without any URL (Gen1 carries the password in the query)."""
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


# --------------------------------------------------------------------------- Manufacturer


async def read_limited(resp: aiohttp.ClientResponse, limit: int, message: str) -> bytes:
    """Complete response body, aborting as soon as it exceeds limit bytes."""
    raw = bytearray()
    async for chunk in resp.content.iter_chunked(64 * 1024):
        raw += chunk
        if len(raw) > limit:
            raise ShellyError(message)
    return bytes(raw)


async def _cloud_get(
    session: aiohttp.ClientSession, url: str, pins: CertificatePins | None, limit: int, too_large: str
) -> tuple[bytes, str | None]:
    """Body of a GET on the manufacturer's server: HTTPS only, redirects only within
    shelly.cloud (every hop checked again), certificate pinning, size limit.
    Returns (body bytes, charset or None)."""
    for _ in range(4):
        check_download_url(url, https_only=True)
        async with _CloudRequest(session, url, pins, CLOUD_TIMEOUT) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                url = urljoin(url, resp.headers.get("Location", ""))
                continue
            resp.raise_for_status()
            return await read_limited(resp, limit, too_large), resp.charset
    raise ShellyError("Too many redirects from the manufacturer's server")


async def _cloud_json(session: aiohttp.ClientSession, url: str, pins: CertificatePins | None) -> Any:
    """JSON from the manufacturer's server (see _cloud_get)."""
    raw, _ = await _cloud_get(session, url, pins, MAX_CLOUD_JSON, "Response from the manufacturer's server too large")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as err:
        raise ShellyError("Invalid response from the manufacturer's server") from err


class _CloudRequest:
    """session.get() for manufacturer servers: applies certificate pinning and
    translates TLS errors into a readable message including the fingerprint."""

    def __init__(
        self, session: aiohttp.ClientSession, url: str, pins: CertificatePins | None, timeout: aiohttp.ClientTimeout
    ) -> None:
        """Prepare the request; nothing is sent before 'async with'."""
        self.url = url
        ssl_param: aiohttp.Fingerprint | bool = pins.ssl_for(url) if pins else True
        self.request = session.get(url, ssl=ssl_param, allow_redirects=False, timeout=timeout)

    async def __aenter__(self) -> aiohttp.ClientResponse:
        """Send the request and return the response (TLS errors explained)."""
        try:
            return await self.request.__aenter__()
        except (aiohttp.ClientConnectorCertificateError, aiohttp.ServerFingerprintMismatch) as err:
            raise ShellyError(await explain_tls_error(self.url, err)) from None

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """Release the connection."""
        await self.request.__aexit__(exc_type, exc, tb)


async def fetch_cloud_text(
    session: aiohttp.ClientSession, url: str, pins: CertificatePins | None, limit: int
) -> str:
    """Text page (e.g. changelog) from a shelly.cloud server (see _cloud_get).
    Sends no device data."""
    too_large = f"Page larger than {limit // 1024 // 1024} MiB: {clip(url)}"
    raw, charset = await _cloud_get(session, url, pins, limit, too_large)
    return raw.decode(charset or "utf-8", "replace")


async def fetch_offer(
    session: aiohttp.ClientSession, fw_type: str, pins: CertificatePins | None = None
) -> dict[str, str] | None:
    """Stable firmware offer {'version', 'url'} of a Gen2+ firmware ID, or None."""
    meta = await _cloud_json(session, GEN2_META_URL.format(app=quote(fw_type, safe="")), pins)
    stable = meta.get("stable") if isinstance(meta, dict) else None
    if not isinstance(stable, dict) or not stable.get("url"):
        return None
    if _int_field(stable, "nsteps", 1) > 1:
        raise ShellyError("Multi-step update offered – please check manually as described by Shelly")
    return {"version": clip(stable.get("version"), 64), "url": str(stable["url"])}


async def fetch_gen1_offers(session: aiohttp.ClientSession, pins: CertificatePins | None = None) -> dict[str, Any]:
    """Firmware list of all Gen1 types {type: entry} – the manufacturer serves it as
    one document, so one request per check covers every Gen1 type (see gen1_offer)."""
    meta = await _cloud_json(session, GEN1_META_URL, pins)
    if not isinstance(meta, dict) or not meta.get("isok"):
        raise ShellyError("Gen1 firmware server reports an error")
    data = meta.get("data")
    return data if isinstance(data, dict) else {}


def gen1_offer(offers: dict[str, Any], fw_type: str) -> dict[str, str] | None:
    """Stable firmware offer {'version', 'url'} of one Gen1 type from the list of
    fetch_gen1_offers, or None."""
    entry = offers.get(fw_type)
    if not isinstance(entry, dict) or not entry.get("url"):
        return None
    return {"version": clip(entry.get("version"), 128), "url": str(entry["url"])}


def check_download_url(url: str, https_only: bool = False) -> None:
    """Allow only the manufacturer's servers (shelly.cloud) for metadata and firmware."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    trusted = host == "shelly.cloud" or host.endswith(".shelly.cloud")
    schemes = ("https",) if https_only else ("http", "https")
    if parts.scheme not in schemes or parts.username or not trusted:
        raise ShellyError(f"Download source not allowed: {clip(url)}")


async def download_firmware(
    session: aiohttp.ClientSession,
    url: str,
    target_dir: Path,
    pins: CertificatePins | None = None,
    https_only: bool = False,
) -> tuple[str, str, int, bool]:
    """Downloads the firmware to target_dir. Returns (file name, sha256, size, https).

    https_only: required for Gen2+ (every hop must be HTTPS). https in the result
    tells whether the whole transfer was protected by TLS."""
    secure = True
    for _ in range(5):  # at most 4 redirects
        check_download_url(url, https_only=https_only)  # check every hop again
        secure &= urlsplit(url).scheme == "https"
        async with _CloudRequest(session, url, pins, DOWNLOAD_TIMEOUT) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                url = urljoin(url, resp.headers.get("Location", ""))
                continue
            resp.raise_for_status()
            return (*await _store(resp, url, target_dir), secure)
    raise ShellyError("Too many redirects during download")


async def _store(resp: aiohttp.ClientResponse, url: str, target_dir: Path) -> tuple[str, str, int]:
    """Stores the response size-limited and atomically as <sha256>.<ext>."""
    ext = Path(urlsplit(url).path).suffix.lower()
    ext = ext if ext in FW_EXTENSIONS else ".bin"
    temp = target_dir / f".part-{secrets.token_hex(8)}"
    digest, size = hashlib.sha256(), 0
    try:
        with temp.open("wb") as handle:
            async for chunk in resp.content.iter_chunked(128 * 1024):
                size += len(chunk)
                if size > MAX_FW_SIZE:
                    raise ShellyError("Firmware larger than 64 MiB")
                digest.update(chunk)
                # In a worker thread: a slow disk (SD card) never blocks the event loop
                await asyncio.to_thread(handle.write, chunk)
        if size < MIN_FW_SIZE:
            raise ShellyError("Firmware file implausibly small")
        name = digest.hexdigest() + ext
        temp.replace(target_dir / name)  # atomic: never a half-written file visible
        return name, digest.hexdigest(), size
    finally:
        temp.unlink(missing_ok=True)


def file_sha256(target_dir: Path, name: str) -> str:
    """Checksum of a stored file; rejects foreign file names."""
    if not FW_NAME_RE.match(name or ""):
        raise ShellyError("Invalid firmware file name")
    digest = hashlib.sha256()
    with (target_dir / name).open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
