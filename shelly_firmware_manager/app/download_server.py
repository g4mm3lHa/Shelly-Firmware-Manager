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
import time

import data_paths
from aiohttp import web
from aiohttp.typedefs import Handler
from manager import Manager
from shelly_cloud import FW_NAME_RE

log = logging.getLogger("sfm.download")

REJECT_WINDOW_S = 600  # one warning per client address and window (10 min)

REJECT_MAX_CLIENTS = 64  # client addresses tracked; more are counted as "other clients"

OTHER_CLIENTS = "other clients"


class RejectLog:
    """Limits the warnings for rejected requests, so a LAN client cannot fill the app
    log: per client address the first rejection of a window is logged, further ones
    are only counted and named in the next warning after the window. Bounded memory:
    at most REJECT_MAX_CLIENTS addresses, beyond that one shared counter."""

    def __init__(self) -> None:
        """Start with no tracked client."""
        self.clients: dict[str, list[float]] = {}  # address -> [window start (wall clock), suppressed]

    def rejected(self, name: str, client: str, now: float | None = None) -> None:
        """Record one rejected request; logs a warning at most once per window."""
        now = time.time() if now is None else now
        self._expire(now)
        key = client
        if key not in self.clients and len(self.clients) >= REJECT_MAX_CLIENTS:
            key = OTHER_CLIENTS
        entry = self.clients.get(key)
        if entry is not None and now - entry[0] < REJECT_WINDOW_S:
            entry[1] += 1
            return
        self.clients[key] = [now, 0]
        log.warning("Download rejected: %s from %s", name[:80], client)

    def _expire(self, now: float) -> None:
        """Drop the addresses whose window has ended; their suppressed count is
        logged once, so no rejection goes unmentioned."""
        for key, (start, count) in list(self.clients.items()):
            if now - start >= REJECT_WINDOW_S:
                del self.clients[key]
                if count:
                    since = time.strftime("%H:%M", time.localtime(start))
                    log.warning("%d more rejected download request(s) from %s since %s", count, key, since)


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
    rejects = RejectLog()

    async def firmware(request: web.Request) -> web.StreamResponse:
        """Serve a provided firmware file to the Shelly it is meant for."""
        name = request.match_info["name"]
        client = request.remote or "?"
        if not FW_NAME_RE.fullmatch(name) or not mgr.may_download(name, client):
            rejects.rejected(name, client)
            raise web.HTTPNotFound
        path = data_paths.FW_DIR / name
        if not path.is_file() or path.is_symlink():
            raise web.HTTPNotFound
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
