"""Certificate pinning and TLS error texts against a real local HTTPS server.

A self-signed certificate (made with openssl per test session) plays the in-house
certificate of a Shelly server: a pinned fingerprint accepts it, any other is
refused, an unpinned request fails the normal validation, and the error texts name
the fingerprint the server presented (read with a separate connection that sends
nothing). Requests for *.shelly.cloud are resolved to 127.0.0.1."""

import hashlib
import shutil
import socket
import ssl
import subprocess

import aiohttp
import pytest
import shelly
import tls
from aiohttp import web
from aiohttp.abc import AbstractResolver

OTHER = "AB:" * 31 + "AB"  # a fingerprint of no certificate here


@pytest.fixture(scope="session")
def certificate(tmp_path_factory):
    """(certificate file, key file, SHA-256 fingerprint 'AB:CD:…') of a fresh
    self-signed certificate."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl is needed to make the test certificate")
    folder = tmp_path_factory.mktemp("tls")
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=updates.shelly.cloud"],
        check=True, capture_output=True,
    )
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, tls.format_fingerprint(hashlib.sha256(der).digest())


@pytest.fixture
async def https(certificate):
    """Port of a local HTTPS server that answers every GET with "changelog"."""
    cert, key, _fingerprint = certificate
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    async def page(_request):
        return web.Response(text="changelog")

    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    await web.SockSite(runner, sock, ssl_context=context).start()
    yield sock.getsockname()[1]
    await runner.cleanup()


class LocalResolver(AbstractResolver):
    """Resolves every host name to 127.0.0.1 (the local HTTPS server)."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [{"hostname": host, "host": "127.0.0.1", "port": port, "family": socket.AF_INET, "proto": 0,
                 "flags": socket.AI_NUMERICHOST}]

    async def close(self):
        return None


@pytest.fixture
async def session():
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=LocalResolver())) as s:
        yield s


def pins(*fingerprints):
    return tls.CertificatePins([{"host": "updates.shelly.cloud", "sha256": f} for f in fingerprints])


async def test_pinned_certificate_is_accepted(https, certificate, session):
    """The stored fingerprint accepts the in-house certificate, also as one of
    several entries of the host."""
    url = f"https://updates.shelly.cloud:{https}/changelog"
    fingerprint = certificate[2]
    assert await shelly.fetch_cloud_text(session, url, pins(fingerprint), 1024) == "changelog"
    assert await shelly.fetch_cloud_text(session, url, pins(OTHER, fingerprint), 1024) == "changelog"


async def test_other_certificate_is_refused_with_its_fingerprint(https, certificate, session):
    """A certificate that matches none of the stored fingerprints is refused; the
    message names the presented one."""
    url = f"https://updates.shelly.cloud:{https}/changelog"
    with pytest.raises(shelly.ShellyError, match="matches NONE of the stored fingerprints") as err:
        await shelly.fetch_cloud_text(session, url, pins(OTHER), 1024)
    assert f"Presented: {certificate[2]}." in str(err.value)


async def test_unpinned_certificate_fails_the_normal_validation(https, certificate):
    """Without a pin the certificate is validated against the public authorities and
    refused; the explanation reads the presented fingerprint without sending data."""
    url = f"https://127.0.0.1:{https}/changelog"
    async with aiohttp.ClientSession() as plain:
        with pytest.raises(aiohttp.ClientConnectorCertificateError) as err:
            await plain.get(url, ssl=tls.CertificatePins([]).ssl_for(url))
    text = await tls.explain_tls_error(url, err.value)
    assert text.startswith("Certificate of 127.0.0.1 is not publicly trusted.")
    assert f"SHA-256: {certificate[2]}." in text and '"pinned_certificates"' in text
    assert await tls.peer_fingerprint("127.0.0.1", https) == certificate[2]


async def test_explanation_when_the_certificate_cannot_be_read():
    """A server that is gone: the explanation says the fingerprint is not readable."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there any more
    text = await tls.explain_tls_error(f"https://127.0.0.1:{port}/", OSError("certificate verify failed"))
    assert "SHA-256: not readable (" in text
