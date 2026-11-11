"""Download server for the Shellys (port 8098, no authentication).

Deliberately minimal:
- Exactly one route: GET/HEAD /<sha256>.<zip|bin|gbl>. Everything else -> 404/405.
- Only a file currently provided for an update is served, and (by default)
  only to the IP address of the Shelly concerned.
- The path is checked against the strict file name regex instead of being built
  from user input -> no path traversal, no directory listing.
- No request body, no cookies, no redirects, no server banner.
"""

import logging

from aiohttp import web
from aiohttp.typedefs import Handler
from manager import FW_DIR, Manager
from shelly import FW_NAME_RE

log = logging.getLogger("sfm.download")

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


async def strip_server_header(_request: web.Request, response: web.StreamResponse) -> None:
    """aiohttp adds the Server header only when sending – remove it here."""
    response.headers.pop("Server", None)


def build_download_app(mgr: Manager) -> web.Application:
    """aiohttp application of the download server (one read-only route)."""

    async def firmware(request: web.Request) -> web.StreamResponse:
        """Serve a provided firmware file to the Shelly it is meant for."""
        name = request.match_info["name"]
        client = request.remote or "?"
        if not FW_NAME_RE.match(name) or not mgr.may_download(name, client):
            log.warning("Download rejected: %s from %s", name[:80], client)
            raise web.HTTPNotFound()
        path = FW_DIR / name
        if not path.is_file() or path.is_symlink():
            raise web.HTTPNotFound()
        headers = {  # security headers are set by harden()
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{name}"',
        }
        if request.method != "GET":  # HEAD: no transfer, not counted
            return web.FileResponse(path, headers=headers)
        mgr.record_download(name, client)  # the transfer starts now (diagnosis per device)
        log.info("Download %s to %s", name, client)
        return web.FileResponse(path, headers=headers)

    @web.middleware
    async def harden(request: web.Request, handler: Handler) -> web.StreamResponse:
        """Security headers for every response, errors included."""
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            exc.headers.update(HEADERS)
            raise
        response.headers.update(HEADERS)
        return response

    # client_max_size=1: requests with a body are rejected immediately
    app = web.Application(middlewares=[harden], client_max_size=1)
    app.on_response_prepare.append(strip_server_header)
    # add_get registers HEAD as well
    app.router.add_get(r"/{name:[0-9a-f]{64}\.(?:zip|bin|gbl)}", firmware)
    return app
