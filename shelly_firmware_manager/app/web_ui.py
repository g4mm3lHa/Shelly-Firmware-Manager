"""Management UI – reachable only via Home Assistant Ingress.

Protection layers:
1. Only connections from the Supervisor's Ingress proxy (172.30.32.2).
   Home Assistant has already authenticated the user; Ingress sessions and the
   panel (panel_admin) are available to administrators only.
2. Every write request – and every request that contacts a Shelly – needs a CSRF
   token (only visible in the delivered page) and Content-Type JSON. The only
   exception to JSON is the firmware upload (rc7): Content-Type
   application/octet-stream, streamed to disk with its own limit of 64 MiB; the
   32 KB body limit stays for every other request.
3. Strict Content Security Policy; the frontend uses textContent only.
4. Secrets are write-only: no endpoint ever returns a password or hash.
5. Device-side password changes, deleting stored firmware and downgrades need
   explicit confirmation flags (confirm + acknowledged), checked on the server
   (never inferred from the UI alone).
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web
from download_server import strip_server_header
from events import user_from
from manager import DOWNLOAD_BASE_MESSAGES, FW_DIR, Manager
from shelly import FW_NAME_RE, MAX_FW_SIZE, ShellyError

log = logging.getLogger("sfm.ui")

INGRESS_PROXY_IP = "172.30.32.2"
STATIC_DIR = Path(__file__).parent / "static"
DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RESTART_DELAY_S = 1  # let the answer reach the browser before the app restarts
UPLOAD_ROUTE = "firmware-upload"  # the one route that takes a binary body (rc7)
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


def asset_version():
    """Short content hash of app.js and style.css – part of their URLs in index.html."""
    digest = hashlib.sha256()
    for name in ("app.js", "style.css"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:16]


def build_ui_app(mgr: Manager, ingress_ip=INGRESS_PROXY_IP):
    """aiohttp application with the UI page, static files and the JSON API."""
    csrf_token = secrets.token_hex(32)  # new on every app start
    version = asset_version()
    index_html = (
        (STATIC_DIR / "index.html").read_text().replace("__CSRF__", csrf_token).replace("__ASSET_VERSION__", version)
    )
    tasks = set()  # keep references so tasks are not garbage collected

    def background(coro):
        """Run a coroutine detached from the request (scan, installation, restart)."""
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def cancel_background(_app):
        """On shutdown: cancel running checks/installations so they clean up
        (no device stays "checking"; an interrupted installation is reported as
        unconfirmed after the restart)."""
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @web.middleware
    async def security(request, handler):
        """Ingress-only access, CSRF + JSON for writes, security headers on every answer."""
        if request.remote != ingress_ip:
            return web.Response(status=403, text="Only reachable via Home Assistant Ingress")
        if request.method not in ("GET", "HEAD"):
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf_token):
                return web.Response(status=403, text="Invalid CSRF token")
            upload = request.match_info.route.name == UPLOAD_ROUTE
            if request.content_type != (UPLOAD_TYPE if upload else "application/json"):
                return web.Response(status=415, text="Binary file expected" if upload else "JSON expected")
        try:
            response = await handler(request)
        except web.HTTPException as exc:  # error responses get the headers too
            exc.headers.update(SECURITY_HEADERS)
            raise
        response.headers.update(SECURITY_HEADERS)
        if request.match_info.route.name == "static" and request.query.get("v") == version:
            response.headers["Cache-Control"] = STATIC_CACHE
        return response

    def device_id_from(request):
        """Validated HA device ID from the URL; unknown IDs -> 404."""
        device_id = request.match_info["id"]
        if not DEVICE_ID_RE.match(device_id) or device_id not in mgr.devices:
            raise web.HTTPNotFound(text="Unknown device")
        return device_id

    async def json_body(request):
        """Request body as JSON object; anything else -> 400."""
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid JSON") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON object expected")
        return body

    async def guarded(coro):
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

    async def page(_request):
        """The single-page UI with the CSRF token embedded."""
        return web.Response(text=index_html, content_type="text/html")

    async def state(request):
        """Device list and status (no credentials, no internal addresses).

        Conditional: the ETag is a hash of the body; a matching If-None-Match gets an
        empty 304, so an unchanged state is neither sent nor parsed by the UI."""
        body = json.dumps(mgr.public_state(), separators=(",", ":"))
        etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag})
        return web.Response(text=body, content_type="application/json", headers={"ETag": etag})

    async def scan(request):
        """Start a manual check in the background."""
        try:
            mgr.reserve_check("all")  # synchronous: a second request gets 409
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None
        background(mgr.scan(user=user_from(request.headers)))
        return web.json_response({"started": True})

    async def check_device(request):
        """Start the check of a single device in the background (no installation)."""
        device_id = device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        try:
            mgr.reserve_check("device", device_id)  # synchronous: no double start
        except ShellyError as err:
            raise web.HTTPConflict(text=str(err)) from None

        async def run():
            """Single-device check; failures are already logged by the manager."""
            with contextlib.suppress(ShellyError, KeyError):
                await mgr.check_device(device_id, user=user_from(request.headers))

        background(run())
        return web.json_response({"started": True})

    async def set_ui(request):
        """Store the shared table settings (sort order, hidden columns)."""
        try:
            mgr.set_ui(await json_body(request))
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        return web.json_response({"ok": True})

    async def install(request):
        """Start an approved installation, bound to the SHA-256 shown to the user.

        {"downgrade": true} (rc7, Gen2+): install the stored older version with that
        SHA-256; needs the second confirmation ("acknowledged": true) as well."""
        device_id = device_id_from(request)
        body = await json_body(request)
        sha = body.get("sha256")
        if body.get("confirm") is not True or not isinstance(sha, str) or not SHA_RE.match(sha):
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
        if mgr.download_base_issue:  # rc6 R1: unset or unconfirmed former example value
            raise web.HTTPConflict(text=DOWNLOAD_BASE_MESSAGES[mgr.download_base_issue])
        if device_id in mgr.installing:
            raise web.HTTPConflict(text="Installation for this device is already running")
        if not mgr.approval_matches(device_id, sha):
            raise web.HTTPConflict(text="Approval does not match the provided update (anymore)")
        background(mgr.install(device_id, sha, approved_by=user_from(request.headers)))
        return web.json_response({"started": True})

    async def downgrades(request):
        """Stored older stable versions for a downgrade (rc7; Gen1: none)."""
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

    async def changelog(request):
        """Changelog between installed and offered version (plain text entries)."""
        entries = mgr.changelog_for(device_id_from(request))
        return web.json_response({"available": entries is not None, "versions": entries or []})

    async def settings_refresh(request):
        """Read the live settings from the Shelly before editing."""
        device_id = device_id_from(request)
        return web.json_response(await guarded(mgr.refresh_settings(device_id)))

    async def settings_change(request):
        """Write validated settings to the Shelly (logged with the HA user)."""
        device_id = device_id_from(request)
        body = await json_body(request)
        user = user_from(request.headers)
        settings, restart, verified = await guarded(mgr.change_settings(device_id, body, user))
        return web.json_response({**settings, "restart_required": restart, "verified": verified})

    async def restart(request):
        """Restart the Shelly."""
        device_id = device_id_from(request)
        await guarded(mgr.restart_device(device_id))
        return web.json_response({"started": True})

    async def device_password(request):
        """Password dialog actions (mode: store | enable | change | remove | forget).

        The confirmation flags are checked again by the manager; the password is
        never returned – the answer only carries the device's protection status
        and whether credentials are stored."""
        device_id = device_id_from(request)
        body = await json_body(request)
        mode, password = body.get("mode"), body.get("password")
        if not isinstance(mode, str) or (password is not None and not isinstance(password, str)):
            raise web.HTTPBadRequest(text="Invalid password request")
        result = await guarded(
            mgr.set_device_password(
                device_id,
                mode,
                password,
                confirm=body.get("confirm"),
                acknowledged=body.get("acknowledged"),
                user=user_from(request.headers),
            )
        )
        return web.json_response(
            {"protected": result.get("protected"), "password_set": mgr.credentials.has_device_password(device_id)}
        )

    async def ignore(request):
        """Ignore or resume a device (local only, no device request)."""
        device_id = device_id_from(request)
        ignored = (await json_body(request)).get("ignored")
        try:
            mgr.set_ignored(device_id, ignored)
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        return web.json_response({"ignored": bool(mgr.devices[device_id].get("ignored"))})

    async def reload_device(request):
        """Reload Device Info: re-read identity, firmware and status from the device
        only (no manufacturer request). Answers {"warning": text or null}."""
        device_id = device_id_from(request)
        await json_body(request)  # no parameters, but the JSON/CSRF rules apply
        return web.json_response(await guarded(mgr.reload_device(device_id)))

    # ---------------------------------------------------------------- Firmware

    def firmware_name_from(request):
        """Validated firmware file name from the URL; unknown names -> 404."""
        name = request.match_info["name"]
        if not FW_NAME_RE.match(name) or name not in mgr.firmware:
            raise web.HTTPNotFound(text="Unknown firmware")
        return name

    async def firmware_list(_request):
        """Stored firmware files with metadata and the total size of the store."""
        return web.json_response({"firmware": mgr.firmware_list(), "total_size": mgr.store_size()})

    async def firmware_download(request):
        """Stored firmware file as download with a descriptive file name."""
        name = firmware_name_from(request)
        path = FW_DIR / name
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

    async def firmware_delete(request):
        """Delete a stored firmware (logged with the HA user). Needs the second
        confirmation: {"confirm": true, "acknowledged": true} (rc7)."""
        name = firmware_name_from(request)
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

    async def firmware_upload(request):
        """Step 1 of a manual upload (rc7): the raw file as body, its name in the
        X-Filename header. Needs a Content-Length of at most 64 MiB; the body is
        streamed to disk and never read into memory as a whole."""
        length = request.content_length
        if length is None:
            raise web.HTTPLengthRequired(text="Content-Length required")
        if length > MAX_FW_SIZE:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_FW_SIZE, actual_size=length, text="Firmware larger than 64 MiB"
            )
        filename = request.headers.get("X-Filename", "")[:200]
        result = await guarded(
            mgr.stage_upload(request.content.iter_chunked(UPLOAD_CHUNK), filename, length, user_from(request.headers))
        )
        return web.json_response(result)

    async def firmware_upload_confirm(request):
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

    async def firmware_upload_cancel(request):
        """Discard the staged upload."""
        mgr.cancel_upload((await json_body(request)).get("token"))
        return web.json_response({"ok": True})

    # ---------------------------------------------------------------- Logs & app settings

    async def logs(_request):
        """Activity log, newest first."""
        return web.json_response({"entries": mgr.events.list()})

    async def options_get(_request):
        """App options (v3 has no secret options)."""
        return web.json_response(mgr.options_view())

    async def options_save(request):
        """Save changed app options via the Supervisor, then restart the app."""
        changed = await guarded(mgr.save_options(await json_body(request), user_from(request.headers)))
        if changed:

            async def restart_later():
                """Restart after the answer has been sent; failures are logged."""
                await asyncio.sleep(RESTART_DELAY_S)
                try:
                    await mgr.restart_app()
                except ShellyError as err:
                    log.error("App restart failed – restart it manually: %s", err)

            background(restart_later())
        return web.json_response({"changed": changed, "restarting": bool(changed)})

    async def confirm_download_base(request):
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
    app.router.add_post("/api/ui", set_ui)
    app.router.add_post("/api/devices/{id}/check", check_device)
    app.router.add_post("/api/devices/{id}/install", install)
    app.router.add_get("/api/devices/{id}/changelog", changelog)
    app.router.add_get("/api/devices/{id}/downgrades", downgrades)
    app.router.add_post("/api/devices/{id}/settings/refresh", settings_refresh)
    app.router.add_post("/api/devices/{id}/settings", settings_change)
    app.router.add_post("/api/devices/{id}/restart", restart)
    app.router.add_post("/api/devices/{id}/password", device_password)
    app.router.add_post("/api/devices/{id}/ignore", ignore)
    app.router.add_post("/api/devices/{id}/reload", reload_device)
    app.router.add_get("/api/firmware", firmware_list)
    app.router.add_post("/api/firmware/upload", firmware_upload, name=UPLOAD_ROUTE)
    app.router.add_post("/api/firmware/upload/confirm", firmware_upload_confirm)
    app.router.add_post("/api/firmware/upload/cancel", firmware_upload_cancel)
    app.router.add_get("/api/firmware/{name}/download", firmware_download)
    app.router.add_post("/api/firmware/{name}/delete", firmware_delete)
    app.router.add_get("/api/logs", logs)
    app.router.add_get("/api/settings", options_get)
    app.router.add_post("/api/settings", options_save)
    app.router.add_post("/api/settings/download-base/confirm", confirm_download_base)
    app.router.add_static("/static/", STATIC_DIR, show_index=False, follow_symlinks=False, name="static")
    return app
