"""Device addresses: which addresses the app may contact and how they are found.

Every device request goes to an origin made by resolve_device_origin: the host of the
integration's configuration_url resolved once, every address checked against the fixed
list LAN_NETWORKS and the first one pinned as 'http://<ip>:<port>' (no DNS rebinding
between the check and the request). The list is never extended (no carrier-grade NAT,
no link-local addresses). The host name helpers only look a name up for the Host name
column; they never send a request.
"""

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from shelly import ShellyError, clip, error_text

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
