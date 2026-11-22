"""Management UI – reachable only via Home Assistant Ingress.

Protection layers:
1. Only connections from the Supervisor's Ingress proxy (172.30.32.2).
   Home Assistant has already authenticated the user; Ingress sessions and the
   panel (panel_admin) are available to administrators only.
2. Every write request – and every request that contacts a Shelly – needs a CSRF
   token (only visible in the delivered page) and Content-Type JSON. The only
   exceptions to JSON are the firmware upload and the store import: Content-Type
   application/octet-stream, streamed to disk with their own limits (upload 64 MiB,
   import: option import_max_mb), sent in parts of at most 8 MiB because Home
   Assistant Ingress refuses larger bodies; the 32 KB body limit stays for every
   other request.
3. Strict Content Security Policy; the frontend uses textContent only.
4. Secrets are write-only: no endpoint ever returns a stored password or hash. The
   only password in an answer is a newly generated export password, returned once
   by /api/firmware/export and kept nowhere.
5. Device-side password changes, deleting stored firmware and downgrades need
   explicit confirmation flags (confirm + acknowledged), deleting log entries the
   flag confirm – checked on the server (never inferred from the UI alone).
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

import aiohttp
import data_paths
from aiohttp import web
from aiohttp.typedefs import Handler
from download_server import strip_server_header
from events import ENTRY_ID_RE, user_from
from manager import Manager
from manager_core import DOWNLOAD_BASE_MESSAGES
from manager_store import PART_MAX, TRANSFER_ID_RE
from shelly import FW_NAME_RE, MAX_FW_SIZE, ShellyError, parse_json, same_secret

log = logging.getLogger("sfm.ui")

T = TypeVar("T")

INGRESS_PROXY_IP = "172.30.32.2"
STATIC_DIR = Path(__file__).parent / "static"
DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RESTART_DELAY_S = 1  # let the answer reach the browser before the app restarts
UPLOAD_ROUTE = "firmware-upload"  # routes that take a binary body: firmware upload …
IMPORT_ROUTE = "store_import"  # … and store import
BINARY_ROUTES = (UPLOAD_ROUTE, IMPORT_ROUTE)
EXPORT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
UPLOAD_TYPE = "application/octet-stream"
UPLOAD_CHUNK = 128 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
# Static files requested with the current asset version (?v=...) may be cached by the
# browser: a new app version changes the URL. "private": only the browser, no proxy.
STATIC_CACHE = "private, max-age=31536000, immutable"


def asset_version() -> str:
    """Short content hash of app.js and style.css – part of their URLs in index.html."""
    digest = hashlib.sha256()
    for name in ("app.js", "style.css"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:16]


# Red "(beta)" next to the app title (phones inside Home Assistant: above the header
# pills) for a pre-release version only; a final version never shows it
BETA_MARK = '<sup class="beta">(beta)</sup>'


def is_beta(app_version: str) -> bool:
    """True for a pre-release version (X.Y.Z with a suffix, e.g. "3.8.0-rc1" of the beta
    channel); False for a final version X.Y.Z."""
    return "-" in app_version


def build_ui_app(mgr: Manager, ingress_ip: str = INGRESS_PROXY_IP, app_version: str = "") -> web.Application:
    """aiohttp application with the UI page, static files and the JSON API
    (app_version: written into store exports; a pre-release shows the beta mark)."""
    csrf_token = secrets.token_hex(32)  # new on every app start
    version = asset_version()
    index_html = (
        (STATIC_DIR / "index.html").read_text().replace("__CSRF__", csrf_token).replace("__ASSET_VERSION__", version)
        .replace("__BETA__", BETA_MARK if is_beta(app_version) else "")
    )
    tasks: set[asyncio.Task[Any]] = set()  # keep references so tasks are not garbage collected

    def background(coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine detached from the request (scan, installation, restart)."""
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def cancel_background(_app: web.Application) -> None:
        """On shutdown: cancel running checks/installations so they clean up
        (no device stays "checking"; an interrupted installation is reported as
        unconfirmed after the restart)."""
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def refusal(status: int, text: str) -> web.Response:
        """Plain-text answer of the middleware itself, with the security headers."""
        return web.Response(status=status, text=text, headers=SECURITY_HEADERS)

    @web.middleware
    async def security(request: web.Request, handler: Handler) -> web.StreamResponse:
        """Ingress-only access, CSRF + JSON for writes, security headers on every
        answer – also on refusals and on the answer to an unexpected error."""
        if request.remote != ingress_ip:
            return refusal(403, "Only reachable via Home Assistant Ingress")
        try:
            if request.method not in ("GET", "HEAD"):
                if not same_secret(request.headers.get("X-CSRF-Token", ""), csrf_token):
                    return refusal(403, "Invalid CSRF token")
                upload = request.match_info.route.name in BINARY_ROUTES
                if request.content_type != (UPLOAD_TYPE if upload else "application/json"):
                    return refusal(415, "Binary file expected" if upload else "JSON expected")
            response = await handler(request)
        except web.HTTPException as exc:  # error responses get the headers too
            exc.headers.update(SECURITY_HEADERS)
            raise
        except Exception as err:
            # An error no handler expects: details only in the app log
            log.exception("Unexpected error in %s %s", request.method, request.path)
            return refusal(500, f"Unexpected error ({type(err).__name__}) – see the app log")
        response.headers.update(SECURITY_HEADERS)
        if request.match_info.route.name == "static" and request.query.get("v") == version:
            response.headers["Cache-Control"] = STATIC_CACHE
        return response

    def device_id_from(request: web.Request) -> str:
        """Validated HA device ID from the URL; unknown IDs -> 404."""
        device_id = request.match_info["id"]
        if not DEVICE_ID_RE.fullmatch(device_id) or device_id not in mgr.devices:
            raise web.HTTPNotFound(text="Unknown device")
        return device_id

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

    async def guarded(coro: Awaitable[T]) -> T:
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

    # ---------------------------------------------------------------- Routes

    async def page(_request: web.Request) -> web.StreamResponse:
        """The single-page UI with the CSRF token embedded."""
        return web.Response(text=index_html, content_type="text/html")

    async def state(request: web.Request) -> web.StreamResponse:
        """Device list and status – no credentials. The devices' LAN addresses are
        part of it on purpose (IP column, link to the web interface of the device).

        Conditional: the ETag is a hash of the body; a matching If-None-Match gets an
        empty 304, so an unchanged state is neither sent nor parsed by the UI."""
        body = json.dumps(mgr.public_state(), separators=(",", ":"))
        etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag})
        return web.Response(text=body, content_type="application/json", headers={"ETag": etag})

    async def scan(request: web.Request) -> web.StreamResponse:
        """Start a manual check in the background."""
        try:
            mgr.reserve_check("all")  # synchronous: a second request gets 409
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        background(mgr.scan(user=user_from(request.headers)))
        return web.json_response({"started": True})

    async def check_device(request: web.Request) -> web.StreamResponse:
        """Start the check of a single device in the background (no installation)."""
        device_id = device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        start_device_check(device_id, user_from(request.headers))
        return web.json_response({"started": True})

    async def offer_now(request: web.Request) -> web.StreamResponse:
        """"Offer update now": lift the hold of the version held back for this device
        (option offer_after_days) and check the device in the background, which then
        provides it (installed only after an approval)."""
        device_id = device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        user = user_from(request.headers)
        try:
            version = mgr.release_hold(device_id)  # a check that cannot start now provides it later
        except ValueError as err:
            raise web.HTTPConflict(text=str(err)) from None
        log.info("%s: held update %s offered now by %s", mgr.devices[device_id].get("name"), version, user)
        start_device_check(device_id, user)
        return web.json_response({"started": True})

    def start_device_check(device_id: str, user: str) -> None:
        """Reserve and start the check of one device in the background (409 if it
        cannot start now)."""
        try:
            mgr.reserve_check("device", device_id)  # synchronous: no double start
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        run_device_check(device_id, user)

    def run_device_check(device_id: str, user: str) -> None:
        """Run the reserved check of one device in the background."""

        async def run() -> None:
            """Single-device check; failures are already logged by the manager."""
            with contextlib.suppress(ShellyError, KeyError):
                await mgr.check_device(device_id, user=user)

        background(run())

    async def install(request: web.Request) -> web.StreamResponse:
        """Start an approved installation, bound to the SHA-256 shown to the user.

        {"downgrade": true} (Gen2+): install the stored older version with that
        SHA-256; needs the second confirmation ("acknowledged": true) as well."""
        device_id = device_id_from(request)
        body = await json_body(request)
        sha = body.get("sha256")
        if body.get("confirm") is not True or not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        if body.get("downgrade") is True:
            if body.get("acknowledged") is not True:
                raise web.HTTPBadRequest(text="Confirmation incomplete")
            blocker = mgr.downgrade_blocker(device_id)
            if blocker:
                raise web.HTTPConflict(text=blocker)
            if mgr.downgrade_match(device_id, sha) is None:
                raise web.HTTPConflict(text="The selected version is not available for a downgrade (anymore)")
            background(mgr.install(device_id, sha, approved_by=user_from(request.headers), downgrade=True))
            return web.json_response({"started": True})
        if mgr.checking:
            raise web.HTTPConflict(text="Check in progress – please try again afterwards")
        if mgr.download_base_issue:  # Unset or unconfirmed former example value
            raise web.HTTPConflict(text=DOWNLOAD_BASE_MESSAGES[mgr.download_base_issue])
        if device_id in mgr.installing:
            raise web.HTTPConflict(text="Installation for this device is already running")
        if device_id in mgr.device_checks:
            raise web.HTTPConflict(text="Device is being checked – please try again afterwards")
        if not mgr.approval_matches(device_id, sha):
            raise web.HTTPConflict(text="Approval does not match the provided update (anymore)")
        # A stored version offered without the server's answer needs the risk acknowledgment
        if mgr.devices[device_id].get("store_fallback") and body.get("acknowledged") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        restart_first = body.get("restart", False)  # "Restart the device first"
        if not isinstance(restart_first, bool):
            raise web.HTTPBadRequest(text="restart: true or false expected")
        background(mgr.install(device_id, sha, approved_by=user_from(request.headers), restart_first=restart_first))
        return web.json_response({"started": True})

    async def wifi_signal(request: web.Request) -> web.StreamResponse:
        """Wi-Fi signal of the device, read now (installation dialog)."""
        device_id = device_id_from(request)
        return web.json_response(await guarded(mgr.wifi_signal(device_id)))

    async def downgrades(request: web.Request) -> web.StreamResponse:
        """Stored older stable versions for a downgrade (Gen1: none)."""
        device_id = device_id_from(request)
        dev = mgr.devices[device_id]
        return web.json_response(
            {
                "installed": dev.get("version"),
                "type": dev.get("type"),
                "versions": mgr.downgrade_versions(device_id),
                "blocked": mgr.downgrade_blocker(device_id),
            }
        )

    async def changelog(request: web.Request) -> web.StreamResponse:
        """Changelog between installed and offered version (plain text entries)."""
        entries = mgr.changelog_for(device_id_from(request))
        return web.json_response({"available": entries is not None, "versions": entries or []})

    async def settings_refresh(request: web.Request) -> web.StreamResponse:
        """Read the live settings from the Shelly before editing."""
        device_id = device_id_from(request)
        return web.json_response(await guarded(mgr.refresh_settings(device_id)))

    async def settings_change(request: web.Request) -> web.StreamResponse:
        """Write validated settings to the Shelly (logged with the HA user)."""
        device_id = device_id_from(request)
        body = await json_body(request)
        user = user_from(request.headers)
        settings, restart, verified, not_applied = await guarded(mgr.change_settings(device_id, body, user))
        return web.json_response(
            {**settings, "restart_required": restart, "verified": verified, "not_applied": not_applied}
        )

    async def restart(request: web.Request) -> web.StreamResponse:
        """Restart the Shelly."""
        device_id = device_id_from(request)
        await guarded(mgr.restart_device(device_id))
        return web.json_response({"started": True})

    async def device_password(request: web.Request) -> web.StreamResponse:
        """Password dialog actions (mode: store | enable | change | remove | forget).

        The confirmation flags are checked again by the manager; keep=false
        (enable/change) verifies the new password without storing it. The password is
        never returned – the answer only carries the device's protection status
        and whether credentials are stored."""
        device_id = device_id_from(request)
        body = await json_body(request)
        mode, password = body.get("mode"), body.get("password")
        keep = body.get("keep", True)   # enable/change: store the new password in this app
        if (
            not isinstance(mode, str)
            or (password is not None and not isinstance(password, str))
            or not isinstance(keep, bool)
        ):
            raise web.HTTPBadRequest(text="Invalid password request")
        result = await guarded(
            mgr.set_device_password(
                device_id,
                mode,
                password,
                confirm=body.get("confirm"),
                acknowledged=body.get("acknowledged"),
                user=user_from(request.headers),
                keep=keep,
            )
        )
        return web.json_response(
            {"protected": result.get("protected"), "password_set": mgr.credentials.has_device_password(device_id)}
        )

    async def ignore(request: web.Request) -> web.StreamResponse:
        """Ignore or resume a device (local only, no device request)."""
        device_id = device_id_from(request)
        ignored = (await json_body(request)).get("ignored")
        try:
            mgr.set_ignored(device_id, ignored)
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        return web.json_response({"ignored": bool(mgr.devices[device_id].get("ignored"))})

    async def forget_device(request: web.Request) -> web.StreamResponse:
        """Remove from list: forget a device removed from Home Assistant (its stored
        data and password; logged with the HA user). Needs {"confirm": true}."""
        device_id = device_id_from(request)
        body = await json_body(request)
        try:
            mgr.forget_device(device_id, confirm=body.get("confirm"), user=user_from(request.headers))
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        return web.json_response({"ok": True})

    async def reload_device(request: web.Request) -> web.StreamResponse:
        """Reload Device Info: re-read identity, firmware and status from the device
        only (no manufacturer request). Answers {"warning": text or null}."""
        device_id = device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        return web.json_response(await guarded(mgr.reload_device(device_id)))

    # ---------------------------------------------------------------- Firmware

    def firmware_name_from(request: web.Request, unknown: bool = False) -> str:
        """Validated firmware file name from the URL; names the store does not list
        -> 404 (unknown=True: an unknown file of the firmware folder is accepted)."""
        name = request.match_info["name"]
        listed = name in mgr.firmware or (unknown and name in mgr.unknown_files)
        if not FW_NAME_RE.fullmatch(name) or not listed:
            raise web.HTTPNotFound(text="Unknown firmware")
        return name

    async def firmware_list(_request: web.Request) -> web.StreamResponse:
        """Stored firmware files with metadata, the total size of the store and the free
        disk space of its volume (null if unreadable)."""
        return web.json_response({"firmware": mgr.firmware_list(), "total_size": mgr.store_size(),
                                  "free_space": mgr.free_space()})

    async def firmware_changelog(request: web.Request) -> web.StreamResponse:
        """Changelog kept with a stored firmware (plain text entries); nothing is
        requested from the manufacturer for it."""
        entries = mgr.firmware_changelog(firmware_name_from(request))
        return web.json_response({"available": entries is not None, "versions": entries or []})

    async def firmware_download(request: web.Request) -> web.StreamResponse:
        """Stored firmware file as download with a descriptive file name."""
        name = firmware_name_from(request)
        path = data_paths.FW_DIR / name
        if path.is_symlink() or not path.is_file():
            raise web.HTTPNotFound(text="File missing")
        meta = mgr.firmware[name]
        # Descriptive file name, harmless characters only
        nice = re.sub(r"[^A-Za-z0-9._-]", "_", f"{meta.get('type')}-{meta.get('version')}")[:100]
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{nice}.{name.rsplit(".", 1)[1]}"',
            },
        )

    async def firmware_delete(request: web.Request) -> web.StreamResponse:
        """Delete a stored firmware (logged with the HA user). Needs the second
        confirmation: {"confirm": true, "acknowledged": true}. Also for an unknown
        file of the firmware folder."""
        name = firmware_name_from(request, unknown=True)
        body = await json_body(request)
        try:
            mgr.delete_firmware(
                name, user_from(request.headers), confirm=body.get("confirm"), acknowledged=body.get("acknowledged")
            )
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        return web.json_response({"ok": True})

    def part_headers(request: web.Request, limit: int, too_large: str) -> tuple[int, int, int, str | None]:
        """Length, total size, offset and transfer ID of one part of a file sent in
        parts (headers X-Upload-Total, X-Upload-Offset, X-Upload-Transfer; without
        them the body is the whole file). Home Assistant Ingress refuses bodies above
        16 MiB, so the UI sends larger files in parts of at most PART_MAX."""
        length = request.content_length
        if length is None:
            raise web.HTTPLengthRequired(text="Content-Length required")
        try:
            total = int(request.headers.get("X-Upload-Total", length))
            offset = int(request.headers.get("X-Upload-Offset", 0))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid part of the file transfer") from None
        transfer = request.headers.get("X-Upload-Transfer")
        if transfer is not None and not TRANSFER_ID_RE.fullmatch(transfer):
            raise web.HTTPBadRequest(text="Invalid part of the file transfer")
        if total > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=total, text=too_large)
        if length > PART_MAX:
            raise web.HTTPRequestEntityTooLarge(
                max_size=PART_MAX, actual_size=length, text="Part of the file transfer too large"
            )
        return length, total, offset, transfer

    async def firmware_upload(request: web.Request) -> web.StreamResponse:
        """Step 1 of a manual upload: the raw file (or one part of it, see
        part_headers) as body, its name in the X-Filename header; at most 64 MiB in
        all. The body is streamed to disk and never read into memory as a whole."""
        length, total, offset, transfer = part_headers(request, MAX_FW_SIZE, "Firmware larger than 64 MB")
        filename = request.headers.get("X-Filename", "")[:200]
        result = await guarded(
            mgr.stage_upload_part(
                request.content.iter_chunked(UPLOAD_CHUNK), filename, length, total, offset, transfer,
                user_from(request.headers),
            )
        )
        return web.json_response(result)

    async def firmware_upload_confirm(request: web.Request) -> web.StreamResponse:
        """Step 2: register the staged upload with type, version and an optional SHA-256."""
        body = await json_body(request)
        try:
            entry = mgr.confirm_upload(
                body.get("token"), body.get("type"), body.get("version"), body.get("sha256"), user_from(request.headers)
            )
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        except (ShellyError, OSError) as err:
            raise web.HTTPConflict(text=str(err)) from None
        return web.json_response({"stored": entry["name"], "type": entry["type"], "version": entry["version"]})

    async def store_export(request: web.Request) -> web.StreamResponse:
        """Step 1 of a store export: generate the export password (returned once) and
        a single-use token for the download."""
        await json_body(request)
        return web.json_response(await guarded(mgr.prepare_export(user_from(request.headers))))

    async def store_export_download(request: web.Request) -> web.StreamResponse:
        """Step 2: the export ZIP as download (token from step 1, single use). The
        temporary file is deleted after sending, also when the transfer breaks."""
        token = request.match_info["token"]
        if not EXPORT_TOKEN_RE.fullmatch(token):
            raise web.HTTPNotFound(text="Unknown export")
        try:
            path, filename = await mgr.build_export(token, app_version)
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        try:
            response = web.StreamResponse(
                headers={
                    "Content-Type": "application/zip",
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(path.stat().st_size),
                    **SECURITY_HEADERS,
                }
            )
            await response.prepare(request)
            with path.open("rb") as handle:
                while chunk := await asyncio.to_thread(handle.read, UPLOAD_CHUNK * 8):
                    await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            path.unlink(missing_ok=True)

    async def store_import(request: web.Request) -> web.StreamResponse:
        """Step 1 of a store import: the export file (or one part of it, see
        part_headers) as body, streamed to disk; its size is limited by the option
        import_max_mb."""
        too_large = f"File larger than the maximum import size ({mgr.import_max // 1048576} MB, Settings)"
        length, total, offset, transfer = part_headers(request, mgr.import_max, too_large)
        return web.json_response(
            await guarded(
                mgr.stage_import_part(request.content.iter_chunked(UPLOAD_CHUNK), length, total, offset, transfer)
            )
        )

    async def store_import_check(request: web.Request) -> web.StreamResponse:
        """Step 2: check the staged export with its password; returns the summary."""
        body = await json_body(request)
        return web.json_response(await guarded(mgr.check_import(body.get("token"), body.get("password"))))

    async def store_import_confirm(request: web.Request) -> web.StreamResponse:
        """Step 3: store the checked import (new files, missing data) – only with the
        user's statement that they created the export or trust its source
        ("acknowledged": true): a sealed export proves no origin."""
        body = await json_body(request)
        try:
            counts = await mgr.confirm_import(body.get("token"), user_from(request.headers), body.get("acknowledged"))
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        except (ShellyError, OSError) as err:
            raise web.HTTPConflict(text=str(err)) from None
        return web.json_response(counts)

    async def store_import_cancel(request: web.Request) -> web.StreamResponse:
        """Discard the staged import."""
        mgr.cancel_import((await json_body(request)).get("token"))
        return web.json_response({"ok": True})

    async def first_start_skip(request: web.Request) -> web.StreamResponse:
        """First-start question of a fresh installation answered with "Skip"."""
        await json_body(request)
        mgr.skip_first_start(user_from(request.headers))
        return web.json_response({"ok": True})

    async def devices_passwords(request: web.Request) -> web.StreamResponse:
        """Password action for several devices (mode: store | set), each device on
        its own; returns the result per device. The password is never returned."""
        body = await json_body(request)
        password = body.get("password")
        keep = body.get("keep", True)
        if (password is not None and not isinstance(password, str)) or not isinstance(keep, bool):
            raise web.HTTPBadRequest(text="Invalid password request")
        results = await guarded(
            mgr.set_passwords(
                str(body.get("mode")), body.get("devices"), password, confirm=body.get("confirm"),
                acknowledged=body.get("acknowledged"), user=user_from(request.headers), keep=keep,
            )
        )
        return web.json_response({"results": results})

    async def firmware_upload_cancel(request: web.Request) -> web.StreamResponse:
        """Discard the staged upload."""
        mgr.cancel_upload((await json_body(request)).get("token"))
        return web.json_response({"ok": True})

    # ---------------------------------------------------------------- Logs & app settings

    async def logs(_request: web.Request) -> web.StreamResponse:
        """Activity log, newest first."""
        return web.json_response({"entries": mgr.events.list()})

    async def log_delete(request: web.Request) -> web.StreamResponse:
        """Delete one log entry. The deletion is written to the app log only."""
        entry_id = request.match_info["id"]
        if (await json_body(request)).get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        if not ENTRY_ID_RE.fullmatch(entry_id) or not mgr.events.remove(entry_id):
            raise web.HTTPNotFound(text="Unknown log entry")
        log.info("Log entry %s deleted by %s", entry_id, user_from(request.headers))
        return web.json_response({"deleted": True})

    async def logs_clear(request: web.Request) -> web.StreamResponse:
        """Delete all log entries; one entry naming the user stays."""
        if (await json_body(request)).get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        user = user_from(request.headers)
        count = mgr.events.clear(user)
        log.info("Activity log cleared by %s (%d entries)", user, count)
        return web.json_response({"deleted": count})

    async def options_get(_request: web.Request) -> web.StreamResponse:
        """App options (there are no secret options)."""
        # installed_versions: storage of the option "Keep installed versions" (not an option)
        return web.json_response({**mgr.options_view(), "installed_versions": mgr.installed_versions()})

    async def app_page(_request: web.Request) -> web.StreamResponse:
        """Path of this app's page in the Home Assistant frontend (changelog link)."""
        return web.json_response({"url": await mgr.app_page()})

    async def options_save(request: web.Request) -> web.StreamResponse:
        """Save changed app options via the Supervisor, then restart the app."""
        changed = await guarded(mgr.save_options(await json_body(request), user_from(request.headers)))
        if changed:

            async def restart_later() -> None:
                """Restart after the answer has been sent; failures are logged."""
                await asyncio.sleep(RESTART_DELAY_S)
                try:
                    await mgr.restart_app()
                except ShellyError as err:
                    log.error("App restart failed – restart it manually: %s", err)

            background(restart_later())
        return web.json_response({"changed": changed, "restarting": bool(changed)})

    async def confirm_download_base(request: web.Request) -> web.StreamResponse:
        """Confirm that the former example download URL really is the address of Home
        Assistant (body {"url": <current value>}); stored in the state, no restart."""
        url = (await json_body(request)).get("url")
        try:
            mgr.confirm_download_base(url, user_from(request.headers))
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        return web.json_response({"confirmed": True})

    app = web.Application(middlewares=[security], client_max_size=32 * 1024)
    app.on_response_prepare.append(strip_server_header)
    app.on_shutdown.append(cancel_background)
    app.router.add_get("/", page)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/scan", scan)
    app.router.add_post("/api/devices/{id}/check", check_device)
    app.router.add_post("/api/devices/{id}/install", install)
    app.router.add_post("/api/devices/{id}/offer-now", offer_now)
    app.router.add_get("/api/devices/{id}/signal", wifi_signal)
    app.router.add_get("/api/devices/{id}/changelog", changelog)
    app.router.add_get("/api/devices/{id}/downgrades", downgrades)
    app.router.add_post("/api/devices/{id}/settings/refresh", settings_refresh)
    app.router.add_post("/api/devices/{id}/settings", settings_change)
    app.router.add_post("/api/devices/{id}/restart", restart)
    app.router.add_post("/api/devices/{id}/password", device_password)
    app.router.add_post("/api/devices/{id}/ignore", ignore)
    app.router.add_post("/api/devices/{id}/reload", reload_device)
    app.router.add_post("/api/devices/{id}/forget", forget_device)
    app.router.add_get("/api/firmware", firmware_list)
    app.router.add_post("/api/firmware/upload", firmware_upload, name=UPLOAD_ROUTE)
    app.router.add_post("/api/firmware/upload/confirm", firmware_upload_confirm)
    app.router.add_post("/api/firmware/upload/cancel", firmware_upload_cancel)
    app.router.add_post("/api/firmware/export", store_export)
    app.router.add_get("/api/firmware/export/{token}", store_export_download)
    app.router.add_post("/api/firmware/import", store_import, name=IMPORT_ROUTE)
    app.router.add_post("/api/firmware/import/check", store_import_check)
    app.router.add_post("/api/firmware/import/confirm", store_import_confirm)
    app.router.add_post("/api/firmware/import/cancel", store_import_cancel)
    app.router.add_post("/api/first-start/skip", first_start_skip)
    app.router.add_post("/api/devices/passwords", devices_passwords)
    app.router.add_get("/api/firmware/{name}/changelog", firmware_changelog)
    app.router.add_get("/api/firmware/{name}/download", firmware_download)
    app.router.add_post("/api/firmware/{name}/delete", firmware_delete)
    app.router.add_get("/api/logs", logs)
    app.router.add_post("/api/logs/clear", logs_clear)
    app.router.add_post("/api/logs/{id}/delete", log_delete)
    app.router.add_get("/api/settings", options_get)
    app.router.add_get("/api/settings/app-page", app_page)
    app.router.add_post("/api/settings", options_save)
    app.router.add_post("/api/settings/download-base/confirm", confirm_download_base)
    app.router.add_static("/static/", STATIC_DIR, show_index=False, follow_symlinks=False, name="static")
    return app
