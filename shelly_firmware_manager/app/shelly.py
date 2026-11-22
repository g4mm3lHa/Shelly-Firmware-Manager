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
import datetime
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import unicodedata
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
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Build date at the start of a Gen2+ build_id ("20241011-114455/1.4.4-g6d2a586") or a Gen1
# version text ("20230913-112003/v1.14.0-gcb84623"): YYYYMMDD-HHMMSS
BUILD_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-\d{6}(?:/|$)")
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
# Request header of a Shelly's own update check (JSON); only "app" and "ver" are sent
SHELLY_INFO_HEADER = "Shelly-Info"
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
# Gen1 types (prefixes) that report mode "white" but have only /settings/light/<i>:
# Dimmer 1/2, Duo, Vintage. RGBW2 and the Bulbs use /settings/color|white/<i> by mode.
GEN1_LIGHT_ONLY = ("SHDM", "SHBDUO", "SHVIN")


class ShellyError(RuntimeError):
    """Expected error – shown to the user as a message."""


class AuthError(ShellyError):
    """The device requires a password or rejected the stored one (severity: error)."""


class UnsupportedError(ShellyError):
    """The device does not provide the requested method/endpoint (not an outage)."""


# Why the device registry gives no usable device address (AddressError.reason)
ADDRESS_MISSING = "missing"  # no configuration_url, or not plain http://host[:port]

ADDRESS_UNRESOLVED = "unresolved"  # host name does not resolve

ADDRESS_NOT_PRIVATE = "not_private"  # (resolved) IP address outside LAN_NETWORKS


class AddressError(ShellyError):
    """The device registry gives no usable local address for a device; reason is one
    of ADDRESS_MISSING, ADDRESS_UNRESOLVED, ADDRESS_NOT_PRIVATE."""

    def __init__(self, reason: str, message: str) -> None:
        """Error with the machine-readable reason and the text for the user."""
        super().__init__(message)
        self.reason = reason


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


# --------------------------------------------------------------------------- Devices


# Networks a Shelly may be in: the private IPv4 ranges (RFC 1918) and the unique local
# IPv6 addresses (RFC 4193). A fixed list on purpose: the result does not depend on what
# the Python version in use counts as "private". Everything else is refused – loopback,
# link-local, carrier-grade NAT, multicast, public addresses, and IPv4 addresses written
# as IPv6 (::ffff:a.b.c.d).
LAN_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def is_lan_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for an address inside one of LAN_NETWORKS."""
    return any(ip in network for network in LAN_NETWORKS)


def host_name_of(configuration_url: str | None) -> str | None:
    """Host name of the integration's configuration_url (e.g. shellyplus1-abc.local);
    None for an IP address or a URL without host (shown in the Host name column)."""
    try:
        host = urlsplit(configuration_url or "").hostname
    except ValueError:
        return None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return clip(host, 253)
    return None


# Device ID as a host name label (Gen2+ id, e.g. shellyplus1pm-a0dd6c29c3e0; Gen1
# /settings device.hostname, e.g. shelly1-b929cc)
HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

HOST_RESOLVE_TIMEOUT_S = 5  # longest wait for one host name lookup (mDNS may be slow)


def local_host_name(device_id: object) -> str | None:
    """Host name <device id>.local the device announces itself under (mDNS), from
    the ID the device reports; None if the ID is missing or no valid host label."""
    label = str(device_id or "").strip().lower()
    return f"{label}.local" if HOST_LABEL_RE.fullmatch(label) else None


async def host_resolves(host: str) -> bool:
    """True if the host name resolves to at least one address within
    HOST_RESOLVE_TIMEOUT_S. Only a lookup: no request is sent to the address."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, 80, type=socket.SOCK_STREAM), HOST_RESOLVE_TIMEOUT_S
        )
    except (OSError, UnicodeError, TimeoutError):  # socket.gaierror is an OSError
        return False
    return bool(infos)


async def resolve_device_origin(configuration_url: str | None) -> str:
    """Returns 'http://<private-ip>:<port>' from the integration's configuration_url.

    Raises AddressError (with its reason) if the registry gives no usable address: no
    or no plain http URL, a host name that does not resolve, or an address outside
    the private networks (LAN_NETWORKS)."""
    try:
        parts = urlsplit(configuration_url or "")
        port = parts.port or 80
    except ValueError:  # invalid port or brackets
        raise AddressError(ADDRESS_MISSING, "No usable local device address in the device registry") from None
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
        raise AddressError(ADDRESS_MISSING, "No usable local device address in the device registry")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as err:
        raise AddressError(
            ADDRESS_UNRESOLVED, f"Host name {clip(parts.hostname, 100)} could not be resolved ({error_text(err, 100)})"
        ) from None
    addresses = sorted({str(info[4][0]).split("%")[0] for info in infos})
    if not addresses:
        raise AddressError(ADDRESS_UNRESOLVED, f"Host name {clip(parts.hostname, 100)} could not be resolved")
    for address in addresses:
        if not is_lan_address(ipaddress.ip_address(address)):
            raise AddressError(ADDRESS_NOT_PRIVATE, f"Device address {address} is not in a private network")

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
                return resp.status, parse_json(raw.decode("utf-8", "replace"))
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
                challenge = parse_json(str(error.get("message") or ""))
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
    if it could not be read."""
    try:
        data = await _rpc(session, origin, "Script.List", auth=auth)
    except UnsupportedError:
        return False
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
    None if it could not be read."""
    try:
        status = await _rpc(session, origin, "Cloud.GetStatus", auth=auth)
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


def _positive(value: object) -> bool:
    """True if a reported period is a number above 0 (a sleeping battery device)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


async def _battery_status(session: aiohttp.ClientSession, origin: str, auth: DeviceAuth | None) -> dict[str, bool]:
    """{"battery": bool} from the Gen2+ sys status (wakeup_period, reported by battery
    devices only); {} if it could not be read – the stored value then stays."""
    try:
        status = await _rpc(session, origin, "Sys.GetStatus", auth=auth)
    except UnsupportedError:
        return {"battery": False}
    except (TimeoutError, ShellyError, aiohttp.ClientError):
        return {}
    return {"battery": _positive(status.get("wakeup_period"))}


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
    """Shelly name, NTP server, eco mode, AP, Bluetooth, cloud and websocket debug
    switch, battery flag and power-on action of all outputs – one request (Gen2+
    Shelly.GetConfig, Gen1 /settings).

    cloud_connected: whether the device is connected to the Shelly cloud while the
    cloud is on (Gen2+ one more request, Cloud.GetStatus; None if unknown or off).
    ble_proxy: Gen2+ with Bluetooth on – whether Home Assistant's Bluetooth proxy
    script runs (one more request, Script.List; None if unknown).

    battery is True for a battery device that sleeps between wake-ups (Gen2+
    wakeup_period > 0 in the sys status – read with Sys.GetStatus only when extended –
    or in the sys config; Gen1 sleep_mode.period > 0) – such a device answers only
    while it is awake, so it is only ever detected from the device itself. Gen2+
    leaves battery out (stored value kept) when it could not be read.

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
        extra["debug_ws"] = _flag(((system.get("debug") or {}).get("websocket") or {}).get("enable"))
        extra["cloud"] = _flag((config.get("cloud") or {}).get("enable"))
        extra["cloud_connected"] = (
            await _cloud_connected(session, origin, auth) if extra["cloud"] is True else None
        )
        extra["ble_proxy"] = await _ble_proxy(session, origin, auth) if extra["ble"] is True else False
        if _positive(system.get("wakeup_period")):
            extra["battery"] = True
        if extended:
            if "battery" not in extra:
                extra.update(await _battery_status(session, origin, auth))
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
    if "ap" in changes:
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


async def protection_status(session: aiohttp.ClientSession, origin: str) -> bool | None:
    """Password protection as reported by /shelly (no authentication needed):
    True/False, None if the device does not report it."""
    info = await _device_json(session, origin, "/shelly")
    value = info.get("auth_en" if _int_field(info, "gen", 1) >= 2 else "auth")
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
    session: aiohttp.ClientSession,
    url: str,
    pins: CertificatePins | None,
    limit: int,
    too_large: str,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str | None]:
    """Body of a GET on the manufacturer's server: HTTPS only, redirects only within
    shelly.cloud (every hop checked again), certificate pinning, size limit.
    headers are sent with every hop (all within shelly.cloud).
    Returns (body bytes, charset or None)."""
    for _ in range(4):
        check_download_url(url, https_only=True)
        async with _CloudRequest(session, url, pins, CLOUD_TIMEOUT, headers) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                url = urljoin(url, resp.headers.get("Location", ""))
                continue
            resp.raise_for_status()
            return await read_limited(resp, limit, too_large), resp.charset
    raise ShellyError("Too many redirects from the manufacturer's server")


async def _cloud_json(
    session: aiohttp.ClientSession, url: str, pins: CertificatePins | None, headers: dict[str, str] | None = None
) -> Any:
    """JSON from the manufacturer's server (see _cloud_get)."""
    raw, _ = await _cloud_get(
        session, url, pins, MAX_CLOUD_JSON, "Response from the manufacturer's server too large", headers
    )
    try:
        return parse_json(raw.decode("utf-8", "replace"))
    except ValueError as err:
        raise ShellyError("Invalid response from the manufacturer's server") from err


class _CloudRequest:
    """session.get() for manufacturer servers: applies certificate pinning and
    translates TLS errors into a readable message including the fingerprint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        pins: CertificatePins | None,
        timeout: aiohttp.ClientTimeout,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Prepare the request; nothing is sent before 'async with'."""
        self.url = url
        ssl_param: aiohttp.Fingerprint | bool = pins.ssl_for(url) if pins else True
        self.request = session.get(url, ssl=ssl_param, allow_redirects=False, timeout=timeout, headers=headers)

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
    too_large = f"Page larger than {limit // 1024 // 1024} MB: {clip(url)}"
    raw, charset = await _cloud_get(session, url, pins, limit, too_large)
    return raw.decode(charset or "utf-8", "replace")


async def fetch_offer(
    session: aiohttp.ClientSession, fw_type: str, pins: CertificatePins | None = None, version: str | None = None
) -> dict[str, str] | None:
    """Stable firmware offer {'version', 'url', 'steps', 'build'} of a Gen2+ firmware ID,
    or None. 'build' is the build ID the server names (starts with the build date, see
    build_date; "" if none).

    version: installed version of the device. It is sent the way a Shelly asks for
    updates itself – header Shelly-Info with only the firmware ID ("app") and the
    version ("ver"), nothing else – and the server then answers with the next
    allowed step (e.g. a required intermediate version) instead of the newest
    version. Without version the answer is the newest version. 'steps' is the number
    of update steps the server reports ("1" if it reports none)."""
    headers = None
    if version is not None:
        headers = {SHELLY_INFO_HEADER: json.dumps({"app": fw_type, "ver": version}, separators=(",", ":"))}
    meta = await _cloud_json(session, GEN2_META_URL.format(app=quote(fw_type, safe="")), pins, headers)
    stable = meta.get("stable") if isinstance(meta, dict) else None
    if not isinstance(stable, dict) or not stable.get("url"):
        return None
    steps = max(1, _int_field(stable, "nsteps", 1))
    return {
        "version": clip(stable.get("version"), 64),
        "url": str(stable["url"]),
        "steps": str(steps),
        "build": plain(stable.get("build_id"), 128),
    }


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


def url_sha256(url: str) -> str | None:
    """SHA-256 named by a firmware URL: the manufacturer's Gen2+ download URLs end in
    the SHA-256 of the file (fwcdn.shelly.cloud/…/<sha256>). None for other URLs."""
    last = urlsplit(url).path.rsplit("/", 1)[-1].lower()
    return last if SHA256_RE.fullmatch(last) else None


async def download_firmware(
    session: aiohttp.ClientSession,
    url: str,
    target_dir: Path,
    pins: CertificatePins | None = None,
    https_only: bool = False,
) -> tuple[str, str, int, bool]:
    """Downloads the firmware to target_dir. Returns (file name, sha256, size, https).

    https_only: required for Gen2+ (every hop must be HTTPS). https in the result
    tells whether the whole transfer was protected by TLS. If the offered URL names
    a SHA-256 (see url_sha256), the file must have exactly this checksum, else it is
    not stored."""
    expected = url_sha256(url)
    secure = True
    for _ in range(5):  # at most 4 redirects
        check_download_url(url, https_only=https_only)  # check every hop again
        secure &= urlsplit(url).scheme == "https"
        async with _CloudRequest(session, url, pins, DOWNLOAD_TIMEOUT) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                url = urljoin(url, resp.headers.get("Location", ""))
                continue
            resp.raise_for_status()
            return (*await _store(resp, url, target_dir, expected), secure)
    raise ShellyError("Too many redirects during download")


async def _store(
    resp: aiohttp.ClientResponse, url: str, target_dir: Path, expected: str | None = None
) -> tuple[str, str, int]:
    """Stores the response size-limited and atomically as <sha256>.<ext>.
    expected: required SHA-256 (from the offered URL); a different file is discarded."""
    ext = Path(urlsplit(url).path).suffix.lower()
    ext = ext if ext in FW_EXTENSIONS else ".bin"
    temp = target_dir / f".part-{secrets.token_hex(8)}"
    digest, size = hashlib.sha256(), 0
    try:
        with temp.open("wb") as handle:
            async for chunk in resp.content.iter_chunked(128 * 1024):
                size += len(chunk)
                if size > MAX_FW_SIZE:
                    raise ShellyError("Firmware larger than 64 MB")
                digest.update(chunk)
                # In a worker thread: a slow disk (SD card) never blocks the event loop
                await asyncio.to_thread(handle.write, chunk)
        if size < MIN_FW_SIZE:
            raise ShellyError("Firmware file implausibly small")
        if expected and digest.hexdigest() != expected:
            raise ShellyError("Download does not match the SHA-256 named by the manufacturer's URL – not stored")
        name = digest.hexdigest() + ext
        temp.replace(target_dir / name)  # atomic: never a half-written file visible
        return name, digest.hexdigest(), size
    finally:
        temp.unlink(missing_ok=True)


def file_sha256(target_dir: Path, name: str) -> str:
    """Checksum of a stored file; rejects foreign file names."""
    if not FW_NAME_RE.fullmatch(name or ""):
        raise ShellyError("Invalid firmware file name")
    digest = hashlib.sha256()
    with (target_dir / name).open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
