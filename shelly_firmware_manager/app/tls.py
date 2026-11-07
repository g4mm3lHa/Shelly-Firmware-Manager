"""Certificate pinning for Shelly manufacturer servers.

Background: some shelly.cloud servers (e.g. updates.shelly.cloud) use certificates
of the in-house certificate authority "O=Allterco". It is not part of any public
trust store; every standard client (curl included) rejects it with
"unable to get local issuer certificate".

Solution: for explicitly configured hosts only certificates whose SHA-256
fingerprint is stored are accepted. A host may have several entries because
Shelly runs several servers with different certificates behind one name. Any
other certificate – replaced or forged – is rejected. All other hosts keep the
normal validation against public certificate authorities.
"""

import asyncio
import hashlib
import re
import ssl
from urllib.parse import urlsplit

import aiohttp

PIN_HOST_RE = re.compile(r"^([a-z0-9-]+\.)*shelly\.cloud$")


def is_shelly_host(host):
    """Only hosts below shelly.cloud may be pinned."""
    return bool(PIN_HOST_RE.match((host or "").lower()))


def format_fingerprint(digest: bytes):
    """Same notation as in browsers: 'AB:CD:…'."""
    return ":".join(f"{b:02X}" for b in digest)


def parse_fingerprint(text):
    """Accepts 'AB:CD…', 'ab cd…' or 'abcd…'; returns 32 bytes."""
    hex_only = re.sub(r"[\s:]", "", str(text or ""))
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", hex_only):
        raise ValueError(f"Invalid SHA-256 fingerprint: {str(text)[:100]!r}")
    return bytes.fromhex(hex_only)


class FingerprintSet(aiohttp.Fingerprint):
    """aiohttp.Fingerprint that accepts one of several SHA-256 values."""

    def __init__(self, digests):
        """digests: SHA-256 values (32 bytes each) of the accepted certificates."""
        self.digests = frozenset(digests)
        super().__init__(next(iter(self.digests)))  # validates length/hash algorithm

    def check(self, transport):
        """Called by aiohttp after the TLS handshake."""
        if not transport.get_extra_info("sslcontext"):
            return
        cert = transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
        got = hashlib.sha256(cert).digest()
        if got not in self.digests:
            host, port, *_ = transport.get_extra_info("peername")
            raise aiohttp.ServerFingerprintMismatch(self.fingerprint, got, host, port)


class CertificatePins:
    """Configured certificate pins, grouped by host."""

    def __init__(self, entries):
        """entries: list of {'host': ..., 'sha256': ...} from the app configuration."""
        collected = {}  # host -> set of allowed fingerprints
        for entry in entries or []:
            host = str(entry.get("host", "")).strip().lower()
            if not is_shelly_host(host):
                raise ValueError(f"Certificate pinning is only allowed for *.shelly.cloud: {host!r}")
            collected.setdefault(host, set()).add(parse_fingerprint(entry.get("sha256")))
        self.pins = {host: FingerprintSet(digests) for host, digests in collected.items()}

    def ssl_for(self, url):
        """ssl parameter for a request: fingerprint for a pinned host, otherwise
        True (= normal validation of host name and public certificate chain)."""
        return self.pins.get((urlsplit(url).hostname or "").lower(), True)


async def peer_fingerprint(host, port=443):
    """SHA-256 of the certificate sent by the server – for the error message only.

    The connection is opened without validation, but no data is sent or
    received; it is used solely to read the certificate.
    """
    probe = ssl.create_default_context()
    probe.check_hostname = False
    # Deliberately unverified: only reads the certificate, no payload
    probe.verify_mode = ssl.CERT_NONE  # nosec B501
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=probe, server_hostname=host), 15
    )
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
    return format_fingerprint(hashlib.sha256(der).digest())


async def explain_tls_error(url, err):
    """Understandable error message including the fingerprint of the presented certificate."""
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 443
    if isinstance(err, aiohttp.ServerFingerprintMismatch):
        return (
            f"Certificate of {host} matches NONE of the stored fingerprints. "
            f"Presented: {format_fingerprint(err.got)}. Either Shelly uses an "
            "additional certificate or the connection is being tampered with – "
            "verify independently first, then add it as an additional entry."
        )
    try:
        fingerprint = await peer_fingerprint(host, port)
    except (TimeoutError, OSError) as probe_err:
        fingerprint = f"not readable ({probe_err})"
    return (
        f"Certificate of {host} is not publicly trusted. "
        f"SHA-256: {fingerprint}. After independent verification add it under "
        f'"pinned_certificates" for {host}.'
    )
