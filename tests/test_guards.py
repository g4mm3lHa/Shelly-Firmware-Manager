"""Unit tests of the protective checks: device addresses (private networks only),
device authentication (no downgrade), certificate pinning, manufacturer URLs, version
comparison, strict patterns and the size check of ZIP archives.

Pure functions – no network; name resolution is replaced where it is needed."""

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import zipfile
from pathlib import Path

import aiohttp
import app_options
import pytest
import shelly
import store_transfer
import tls
from conftest import OPTIONS
from shelly import DeviceAuth, ShellyError
from store_transfer import TransferError

# ---------------------------------------------------------------- Device addresses


@pytest.mark.parametrize(
    "address",
    ["10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.255.254", "192.168.0.1", "192.168.178.50", "fc00::1", "fd12:3456::1"],
)
def test_lan_addresses_accepted(address):
    """The private IPv4 ranges and the unique local IPv6 addresses are accepted."""
    assert shelly.is_lan_address(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "::1",  # loopback
        "0.0.0.0", "::",  # unspecified
        "169.254.169.254", "fe80::1",  # link-local
        "100.64.0.1",  # carrier-grade NAT
        "172.15.255.255", "172.32.0.1", "192.169.0.1", "11.0.0.1",  # just outside the private ranges
        "8.8.8.8", "2001:4860:4860::8888",  # public
        "224.0.0.1", "ff02::1", "255.255.255.255",  # multicast, broadcast
        "198.18.0.1", "192.0.2.1", "192.0.0.8", "240.0.0.1",  # benchmark, documentation, reserved
        "::ffff:192.168.1.5", "::ffff:127.0.0.1",  # IPv4 written as IPv6
        "2002:c0a8:101::", "2001::1", "64:ff9b::c0a8:101",  # 6to4, Teredo, NAT64
    ],
)
def test_other_addresses_refused(address):
    """Everything outside the fixed list is refused – whatever the Python version in
    use counts as "private"."""
    assert not shelly.is_lan_address(ipaddress.ip_address(address))


async def test_device_origin_from_a_private_address():
    """An http URL with a private address becomes the origin; the port is kept."""
    assert await shelly.resolve_device_origin("http://192.168.1.10/") == "http://192.168.1.10:80"
    assert await shelly.resolve_device_origin("http://10.0.0.7:8080/path") == "http://10.0.0.7:8080"
    assert await shelly.resolve_device_origin("http://[fd00::7]/") == "http://[fd00::7]:80"


@pytest.mark.parametrize(
    "url",
    [
        None, "", "192.168.1.10",  # no URL
        "https://192.168.1.10/",  # devices are only reached over http
        "http://admin:secret@192.168.1.10/", "http://admin@192.168.1.10/",  # no credentials in the address
        "http://127.0.0.1/", "http://169.254.169.254/", "http://8.8.8.8/", "http://[::1]/", "http://100.64.0.1/",
        "http://[::ffff:192.168.1.10]/",
    ],
)
async def test_device_origin_refused(url):
    """No usable or no private address: the device is never contacted."""
    with pytest.raises(ShellyError):
        await shelly.resolve_device_origin(url)


async def test_device_origin_needs_every_resolved_address_private(monkeypatch):
    """A host name that also resolves to a public address is refused as a whole."""
    answers = {"good.lan": ["192.168.1.20", "10.0.0.20"], "mixed.lan": ["192.168.1.20", "203.0.113.5"]}

    async def getaddrinfo(host, port, **_kwargs):
        return [(None, None, None, "", (address, port)) for address in answers[host]]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    assert await shelly.resolve_device_origin("http://good.lan/") == "http://10.0.0.20:80"
    with pytest.raises(ShellyError, match=r"203\.0\.113\.5 is not in a private network"):
        await shelly.resolve_device_origin("http://mixed.lan/")


# ---------------------------------------------------------------- Device authentication


def test_gen1_answers_basic_only():
    """Gen1: Basic Auth with the fixed user; another scheme is refused."""
    auth = DeviceAuth(gen=1, password="pw")
    token = base64.b64encode(b"admin:pw").decode()
    assert auth.authorization('Basic realm="shelly"', "GET", "/settings") == f"Basic {token}"
    with pytest.raises(ShellyError):
        auth.authorization('Digest realm="x", nonce="1"', "GET", "/settings")


def test_gen2_digest_response_is_sha256():
    """Gen2+: Digest with SHA-256, computed from the stored HA1 of this device."""
    realm, nonce = "shellyplus1-aabbcc", "1700000000"
    auth = DeviceAuth(gen=2, ha1=shelly.gen2_ha1(realm, "pw"), realm=realm)
    header = auth.authorization(f'Digest qop="auth", realm="{realm}", nonce="{nonce}", algorithm=SHA-256', "POST", "/rpc")
    fields = dict(part.strip().split("=", 1) for part in header.removeprefix("Digest ").split(","))
    cnonce = fields["cnonce"].strip('"')
    ha1 = hashlib.sha256(f"admin:{realm}:pw".encode()).hexdigest()
    ha2 = hashlib.sha256(b"POST:/rpc").hexdigest()
    expected = hashlib.sha256(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}".encode()).hexdigest()
    assert fields["response"].strip('"') == expected and fields["algorithm"] == "SHA-256"
    assert fields["username"] == '"admin"' and fields["realm"] == f'"{realm}"'


@pytest.mark.parametrize(
    "challenge",
    [
        'Basic realm="shellyplus1-aabbcc"',  # would send the password almost in plain text
        'Digest realm="shellyplus1-aabbcc", nonce="1", algorithm=MD5',  # weaker hash
        'Digest realm="shellyplus1-aabbcc", algorithm=SHA-256',  # no nonce
        'Digest nonce="1", algorithm=SHA-256',  # no realm
        'Digest realm="shellyplus1-aabbcc", nonce="1", algorithm=SHA-256, qop="auth-int"',
        'Digest realm="another-device", nonce="1", algorithm=SHA-256',  # HA1 is bound to the device ID
    ],
)
def test_gen2_never_downgrades(challenge):
    """Gen2+: no Basic Auth, no other hash, and a stored HA1 only for its own device."""
    auth = DeviceAuth(gen=2, ha1=shelly.gen2_ha1("shellyplus1-aabbcc", "pw"), realm="shellyplus1-aabbcc")
    with pytest.raises(ShellyError):
        auth.authorization(challenge, "GET", "/rpc/Shelly.GetConfig")


def test_device_auth_hides_its_secrets():
    """Neither password nor HA1 can end up in a log line through repr()."""
    assert "pw" not in repr(DeviceAuth(gen=1, password="pw"))
    assert "abc123" not in repr(DeviceAuth(gen=2, ha1="abc123", realm="r"))


# ---------------------------------------------------------------- Certificate pinning

FINGERPRINT = hashlib.sha256(b"certificate").digest()


def test_fingerprint_notations():
    """Colons, spaces or plain hex, upper or lower case; anything else is refused."""
    plain = FINGERPRINT.hex()
    for text in (plain, plain.upper(), tls.format_fingerprint(FINGERPRINT), " ".join(plain[i : i + 2] for i in range(0, 64, 2))):
        assert tls.parse_fingerprint(text) == FINGERPRINT
    for text in ("", None, plain[:-2], plain + "00", "zz" * 32):
        with pytest.raises(ValueError):
            tls.parse_fingerprint(text)


@pytest.mark.parametrize("host", ["shelly.cloud", "updates.shelly.cloud", "a.b.shelly.cloud", "UPDATES.Shelly.Cloud"])
def test_pin_hosts_accepted(host):
    """Hosts below shelly.cloud may be pinned."""
    assert tls.is_shelly_host(host)


@pytest.mark.parametrize(
    "host", ["", None, "example.com", "shelly.cloud.example.com", "notshelly.cloud", "shelly.cloud\n", "updates.shelly.cloud/x"]
)
def test_pin_hosts_refused(host):
    """No other host – also no look-alike and no trailing line break."""
    assert not tls.is_shelly_host(host)


def test_pins_apply_to_their_host_only():
    """A pinned host is checked against its fingerprints; every other host keeps the
    normal certificate validation. A pin for a foreign host is a configuration error."""
    pins = tls.CertificatePins([{"host": "Updates.Shelly.Cloud", "sha256": tls.format_fingerprint(FINGERPRINT)}])
    pinned = pins.ssl_for("https://updates.shelly.cloud/update/Plus1")
    assert isinstance(pinned, tls.FingerprintSet) and pinned.digests == {FINGERPRINT}
    assert pins.ssl_for("https://fwcdn.shelly.cloud/x") is True
    with pytest.raises(ValueError):
        tls.CertificatePins([{"host": "example.com", "sha256": FINGERPRINT.hex()}])


class FakeTransport:
    """Transport of a TLS connection that presented the given certificate."""

    def __init__(self, certificate):
        self.certificate = certificate

    def get_extra_info(self, name):
        """What aiohttp asks for after the handshake."""
        if name == "sslcontext":
            return True
        if name == "ssl_object":
            return self
        return ("203.0.113.1", 443)

    def getpeercert(self, binary_form=False):
        """The certificate as sent by the server."""
        return self.certificate


def test_fingerprint_set_accepts_only_its_certificates():
    """One of several pinned certificates is accepted, any other is refused."""
    pins = tls.FingerprintSet([FINGERPRINT, hashlib.sha256(b"second").digest()])
    pins.check(FakeTransport(b"certificate"))
    pins.check(FakeTransport(b"second"))
    with pytest.raises(aiohttp.ServerFingerprintMismatch):
        pins.check(FakeTransport(b"another certificate"))


# ---------------------------------------------------------------- Manufacturer URLs and versions


@pytest.mark.parametrize(
    "url", ["https://updates.shelly.cloud/update/Plus1", "https://fwcdn.shelly.cloud/a/b", "http://archive.shelly.cloud/x"]
)
def test_download_sources_accepted(url):
    """Only servers below shelly.cloud."""
    shelly.check_download_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/fw.zip", "https://shelly.cloud.example.com/fw.zip", "https://notshelly.cloud/fw.zip",
        "https://user@updates.shelly.cloud/fw.zip", "ftp://updates.shelly.cloud/fw.zip", "file:///data/firmware/x",
        "https://192.168.1.2/fw.zip",
    ],
)
def test_download_sources_refused(url):
    """No other host, no credentials, no other scheme."""
    with pytest.raises(ShellyError):
        shelly.check_download_url(url)


def test_gen2_downloads_are_https_only():
    """https_only (Gen2+ firmware) refuses plain http even from the manufacturer."""
    shelly.check_download_url("https://fwcdn.shelly.cloud/x", https_only=True)
    with pytest.raises(ShellyError):
        shelly.check_download_url("http://fwcdn.shelly.cloud/x", https_only=True)


def test_version_order():
    """Gen1 and Gen2+ notations compare by number; a final version ranks above its
    pre-releases; a text without a version is an error."""
    parse = shelly.parse_version
    assert parse("1.4.4") < parse("1.5.0") < parse("1.10.0") < parse("2.0.0")
    assert parse("1.5.0-alpha1") < parse("1.5.0-beta1") < parse("1.5.0-beta2") < parse("1.5.0-rc1") < parse("1.5.0")
    assert parse("20230913-112003/v1.14.0-gcb84623") == parse("1.14.0")
    for text in ("", None, "latest", "1.4"):
        with pytest.raises(ShellyError):
            parse(text)


# ---------------------------------------------------------------- Strict patterns


def test_file_names_and_options_take_no_trailing_line_break(tmp_path):
    """A value followed by a line break is not the value: file names, check time
    and Gen1 types are compared as a whole."""
    name = "a" * 64 + ".zip"
    (tmp_path / name).write_bytes(b"x")
    assert shelly.file_sha256(tmp_path, name) == hashlib.sha256(b"x").hexdigest()
    with pytest.raises(ShellyError):
        shelly.file_sha256(tmp_path, name + "\n")
    for changes in ({"check_time": "02:45\n"}, {"gen1_allowed_types": ["SHSW-1\n"]}):
        with pytest.raises(ValueError):
            app_options.merge(OPTIONS, changes)


# Cases shared with the Settings tab check (qa/features/download_url.py): url, the server's verdict
# (ok, private, format, parts) and the hint the Settings tab shows while typing
URL_CASES = json.loads((Path(__file__).parent / "download_url_cases.json").read_text())
URL_ERRORS = {"private": app_options.DOWNLOAD_BASE_NOT_PRIVATE, "format": app_options.DOWNLOAD_BASE_FORMAT,
              "parts": app_options.DOWNLOAD_BASE_PARTS}


@pytest.mark.parametrize("case", URL_CASES, ids=lambda c: ascii(c["url"]))
def test_download_base_url_cases(case):
    """Allow-list: a private IP address or an ASCII host name, no credentials, query or fragment,
    nothing but printable ASCII without spaces. IP addresses in disguise (number forms) are refused."""
    if case["server"] == "ok":
        assert app_options.merge(OPTIONS, {"download_base_url": case["url"]})[0]["download_base_url"] == case["url"]
        assert app_options.download_base_problem(case["url"]) is None
    else:
        with pytest.raises(ValueError) as err:
            app_options.merge(OPTIONS, {"download_base_url": case["url"]})
        assert str(err.value) == URL_ERRORS[case["server"]]


def test_empty_download_base_url_removes_the_option():
    """An empty input unsets the optional option instead of being validated."""
    new, changed = app_options.merge(OPTIONS, {"download_base_url": ""})
    assert changed == ["download_base_url"] and "download_base_url" not in new


def test_download_base_url_wrong_types_and_length():
    """Only a string of at most 200 characters is an address."""
    for value in (5, ["http://192.168.1.2"], True, "http://192.168.1.2/" + "a" * 200):
        with pytest.raises(ValueError):
            app_options.merge(OPTIONS, {"download_base_url": value})


def test_stored_download_base_problem():
    """A stored value is judged by the same address rules, even if the Supervisor accepted it."""
    assert app_options.download_base_problem("http://203.0.113.10:8098") == app_options.DOWNLOAD_BASE_NOT_PRIVATE
    assert app_options.download_base_problem("http://8.8.8.8\u00a0") == app_options.DOWNLOAD_BASE_FORMAT
    assert app_options.download_base_problem("http://[fe80::1%eth0]") == app_options.DOWNLOAD_BASE_FORMAT
    assert app_options.download_base_problem("http://[fd00::1") == app_options.DOWNLOAD_BASE_FORMAT  # urlsplit raises
    assert app_options.download_base_problem("http://192.168.1.2:8098") is None
    assert app_options.download_base_problem("http://ha.example.com") is None


# ---------------------------------------------------------------- ZIP archives


def zip_bytes(entries=3):
    """A small ZIP with the given number of entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for i in range(entries):
            archive.writestr(f"file-{i}.bin", b"x")
    return buf.getvalue()


def test_zip_directory_limit(tmp_path, monkeypatch):
    """The list of entries is measured before zipfile reads it: an oversized list, a
    ZIP64 archive and a file that is no ZIP are refused."""
    path = tmp_path / "a.zip"
    path.write_bytes(zip_bytes())
    store_transfer.check_directory(path)
    data = zip_bytes(200)
    path.write_bytes(data)
    monkeypatch.setattr(store_transfer, "MAX_DIRECTORY", 1000)
    with pytest.raises(TransferError, match="too many files"):
        store_transfer.check_directory(path)
    with pytest.raises(TransferError, match="too many files"):
        store_transfer.read_export(path, "PASSWORD")
    monkeypatch.undo()
    end = data.rfind(store_transfer.ZIP_END)
    path.write_bytes(data[:end] + store_transfer.ZIP64_LOCATOR + bytes(16) + data[end:])
    with pytest.raises(TransferError, match="ZIP64"):
        store_transfer.check_directory(path)
    for content in (b"", b"no archive at all", store_transfer.ZIP_END + b"\0" * 5):
        path.write_bytes(content)
        with pytest.raises(TransferError, match="no ZIP"):
            store_transfer.check_directory(path)
