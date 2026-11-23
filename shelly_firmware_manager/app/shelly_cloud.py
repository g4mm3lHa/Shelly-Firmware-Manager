"""Access to the manufacturer's servers: firmware offers, changelog pages, downloads.

All functions are stateless and receive the shared aiohttp session. Nothing about the
devices is sent except, on a Gen2+ offer request with the installed version, the header
Shelly-Info with exactly the firmware ID and that version (the way a Shelly asks).

Security:
- Only shelly.cloud hosts (check_download_url); every redirect hop is checked again.
  Metadata and Gen2+ firmware only via HTTPS, with certificate pinning (tls.py) when
  pins are configured.
- Every body is size-limited: offers and lists MAX_CLOUD_JSON, pages by the caller,
  firmware MAX_FW_SIZE (and at least MIN_FW_SIZE).
- A Gen2+ download URL names the SHA-256 of its file (url_sha256); the downloaded file
  must match it, else it is discarded. Gen1 firmware is offered only via plain HTTP and
  without a checksum, so its integrity and origin CANNOT be verified – the SHA-256
  computed here only identifies the stored file and detects later modification on disk.
- Stored file names consist only of the SHA-256 plus a fixed extension (FW_NAME_RE);
  a download is written under a temporary ".part-" name and renamed when complete.
"""

import asyncio
import hashlib
import json
import re
import secrets
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import aiohttp
from tls import CertificatePins, explain_tls_error

from shelly import ShellyError, clip, int_field, parse_json, plain, read_limited

MAX_FW_SIZE = 64 * 1024 * 1024  # upper limit for a firmware file
MIN_FW_SIZE = 1024  # anything smaller is certainly no firmware
FW_EXTENSIONS = (".zip", ".bin", ".gbl")
FW_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(zip|bin|gbl)$")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
HASH_BLOCK = 1024 * 1024  # bytes read per step when a file is hashed
CLOUD_TIMEOUT = aiohttp.ClientTimeout(total=30)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=300, sock_read=60)
MAX_CLOUD_JSON = 2 * 1024 * 1024  # upper limit for a version offer / firmware list
GEN2_META_URL = "https://updates.shelly.cloud/update/{app}"
# Request header of a Shelly's own update check (JSON); only "app" and "ver" are sent
SHELLY_INFO_HEADER = "Shelly-Info"
GEN1_META_URL = "https://api.shelly.cloud/files/firmware"


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
    session: aiohttp.ClientSession,
    fw_type: str,
    pins: CertificatePins | None = None,
    version: str | None = None,
    beta: bool = False,
) -> dict[str, str] | None:
    """Stable firmware offer {'version', 'url', 'steps', 'build'} of a Gen2+ firmware ID,
    or None. 'build' is the build ID the server names (starts with the build date, see
    build_date; "" if none).

    version: installed version of the device. It is sent the way a Shelly asks for
    updates itself – header Shelly-Info with only the firmware ID ("app") and the
    version ("ver"), nothing else – and the server then answers with the next
    allowed step (e.g. a required intermediate version) instead of the newest
    version. Without version the answer is the newest version. 'steps' is the number
    of update steps the server reports ("1" if it reports none).

    beta: also take the optional beta offer of the same answer (no further request);
    it is added as 'beta_version', 'beta_url' and 'beta_build' when the server names
    one with a URL. Without a stable offer the answer is None, also with a beta."""
    headers = None
    if version is not None:
        headers = {SHELLY_INFO_HEADER: json.dumps({"app": fw_type, "ver": version}, separators=(",", ":"))}
    meta = await _cloud_json(session, GEN2_META_URL.format(app=quote(fw_type, safe="")), pins, headers)
    stable = meta.get("stable") if isinstance(meta, dict) else None
    if not isinstance(stable, dict) or not stable.get("url"):
        return None
    steps = max(1, int_field(stable, "nsteps", 1))
    offer = {
        "version": clip(stable.get("version"), 64),
        "url": str(stable["url"]),
        "steps": str(steps),
        "build": plain(stable.get("build_id"), 128),
    }
    pre = meta.get("beta") if beta and isinstance(meta, dict) else None
    if isinstance(pre, dict) and pre.get("url") and pre.get("version"):
        offer.update(
            beta_version=clip(pre.get("version"), 64),
            beta_url=str(pre["url"]),
            beta_build=plain(pre.get("build_id"), 128),
        )
    return offer


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


def sha256_of(path: Path) -> str:
    """SHA-256 (hex) of a file, read in blocks of HASH_BLOCK. Blocks: run it in a worker
    thread from the event loop. The one checksum function of the app for files."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(HASH_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def file_sha256(target_dir: Path, name: str) -> str:
    """Checksum of a stored firmware file; rejects foreign file names."""
    if not FW_NAME_RE.fullmatch(name or ""):
        raise ShellyError("Invalid firmware file name")
    return sha256_of(target_dir / name)
