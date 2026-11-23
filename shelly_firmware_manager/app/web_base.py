"""Building blocks shared by the route handlers of the UI (web_ui and the web_* areas).

Handlers is the base class of every area: it holds the manager and the function that
runs work detached from the request (both given by web_ui.build_ui_app), and checks
the device ID of a URL. json_body, guarded and translated turn bad input and refused
actions into HTTP answers, so a handler never answers with the text of an unexpected
error (that one goes to the middleware: 500 without details, see web_ui.security).
"""

import re
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import aiohttp
from aiohttp import web
from manager import Manager
from shelly import ShellyError, parse_json

DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
UPLOAD_CHUNK = 128 * 1024  # bytes read from a request body or a file per step
EXPORT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")  # single-use token of a download

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

Background = Callable[[Coroutine[Any, Any, Any]], None]  # runs a coroutine detached from the request
Errors = tuple[type[Exception], ...]


async def json_body(request: web.Request) -> dict[str, Any]:
    """Request body as JSON object; anything else -> 400 (also a value nested too
    deeply and an unknown charset in the Content-Type)."""
    try:
        body = parse_json(await request.text())
    except (ValueError, LookupError):
        raise web.HTTPBadRequest(text="Invalid JSON") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="JSON object expected")
    return body


async def guarded[T](coro: Awaitable[T]) -> T:
    """Run a request to a Shelly/the Supervisor and translate errors into HTTP answers."""
    try:
        return await coro
    except ValueError as err:  # invalid input
        raise web.HTTPBadRequest(text=str(err)) from None
    except ShellyError as err:  # state does not allow it or device refused
        raise web.HTTPConflict(text=str(err)) from None
    except aiohttp.ClientResponseError as err:  # device answered with an HTTP error
        raise web.HTTPConflict(text=f"Shelly rejected the request (HTTP {err.status})") from None
    except (TimeoutError, aiohttp.ClientError) as err:
        raise web.HTTPBadGateway(text=f"Shelly not reachable: {err or type(err).__name__}") from None


def translated[T](call: Callable[[], T], bad: Errors = (ValueError,), conflict: Errors = (ShellyError,)) -> T:
    """Run a synchronous manager call and translate its expected errors with their text:
    bad (invalid input, default ValueError) -> 400, conflict (the state does not allow
    it, default ShellyError) -> 409. Pass () for a kind the call does not raise; any
    other error is left to the middleware."""
    try:
        return call()
    except bad as err:
        raise web.HTTPBadRequest(text=str(err)) from None
    except conflict as err:
        raise web.HTTPConflict(text=str(err)) from None


class Handlers:
    """Base of the route handlers of one area of the UI."""

    def __init__(self, mgr: Manager, background: Background) -> None:
        """mgr: the manager; background: runs a coroutine detached from the request
        (cancelled when the app stops, see web_ui.build_ui_app)."""
        self.mgr = mgr
        self.background = background

    def device_id_from(self, request: web.Request) -> str:
        """Validated HA device ID from the URL; unknown IDs -> 404."""
        device_id = request.match_info["id"]
        if not DEVICE_ID_RE.fullmatch(device_id) or device_id not in self.mgr.devices:
            raise web.HTTPNotFound(text="Unknown device")
        return device_id
