"""Management UI – reachable only via Home Assistant Ingress.

This module holds the protective middleware (security), the page, the state and the
route table; the handlers of each area are in web_devices, web_firmware, web_backups,
web_schedules and web_settings, their shared checks in web_base.

Protection layers:
1. Only connections from the Supervisor's Ingress proxy (172.30.32.2).
   Home Assistant has already authenticated the user; Ingress sessions and the
   panel (panel_admin) are available to administrators only.
2. Every write request – and every request that contacts a Shelly – needs a CSRF
   token (only visible in the delivered page) and Content-Type JSON. The only
   exceptions to JSON are the firmware upload, the store import and the import of a
   backup file: Content-Type application/octet-stream with their own limits (upload
   64 MiB, import: option import_max_mb, both streamed to disk and sent in parts of
   at most 8 MiB because Home Assistant Ingress refuses larger bodies; backup file:
   4 MiB, kept in memory until it is imported); the 32 KB body limit stays for every
   other request.
3. Strict Content Security Policy; the frontend uses textContent only.
4. Secrets are write-only: no endpoint ever returns a stored password or hash. The
   only passwords in an answer are newly generated ones, returned once and kept
   nowhere: the export password (/api/firmware/export) and the password of a
   downloaded backup (/api/backups/{bid}/download). The settings inside a backup
   leave the app only as such an encrypted file – and as the list of differences of
   the restore dialog, which holds no password fields (they are no part of a backup;
   a secret written into a script or a webhook or action URL is, see shelly_backup).
5. Device-side password changes, deleting stored firmware and downgrades need
   explicit confirmation flags (confirm + acknowledged), a beta installation (also a
   scheduled one) the flag beta_acknowledged; deleting log entries, a schedule or a
   backup, "Run now", cancelling a scheduled installation and restoring a backup the
   flag confirm, restoring network settings also network_acknowledged – checked on
   the server (never inferred from the UI alone).
"""

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.typedefs import Handler
from download_server import strip_server_header
from manager import Manager
from shelly import same_secret
from web_backups import BackupRoutes
from web_base import SECURITY_HEADERS
from web_devices import DeviceRoutes
from web_firmware import FirmwareRoutes
from web_schedules import ScheduleRoutes
from web_settings import SettingsRoutes

log = logging.getLogger("sfm.ui")

INGRESS_PROXY_IP = "172.30.32.2"
STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_ROUTE = "firmware-upload"  # routes that take a binary body: firmware upload …
IMPORT_ROUTE = "store_import"  # … store import …
BACKUP_IMPORT_ROUTE = "backup_import"  # … and the import of a backup file
BINARY_ROUTES = (UPLOAD_ROUTE, IMPORT_ROUTE, BACKUP_IMPORT_ROUTE)
UPLOAD_TYPE = "application/octet-stream"

# Static files requested with the current asset version (?v=...) may be cached by the
# browser: a new app version changes the URL. "private": only the browser, no proxy.
STATIC_CACHE = "private, max-age=31536000, immutable"


def asset_version() -> str:
    """Short content hash of the scripts (loader, app, dictionaries) and style.css – part
    of their URLs (index.html; the loader passes it on to the scripts it adds)."""
    digest = hashlib.sha256()
    for path in [*sorted(STATIC_DIR.glob("*.js")), STATIC_DIR / "style.css"]:
        digest.update(path.read_bytes())
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

    devices = DeviceRoutes(mgr, background, app_version)
    schedules = ScheduleRoutes(mgr, background)
    firmware = FirmwareRoutes(mgr, background, app_version)
    backups = BackupRoutes(mgr, background)
    settings = SettingsRoutes(mgr, background)

    app = web.Application(middlewares=[security], client_max_size=32 * 1024)
    app.on_response_prepare.append(strip_server_header)
    app.on_shutdown.append(cancel_background)
    app.router.add_get("/", page)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/scan", devices.scan)
    app.router.add_post("/api/devices/{id}/check", devices.check_device)
    app.router.add_post("/api/devices/{id}/install", devices.install)
    app.router.add_post("/api/devices/{id}/planned/cancel", devices.planned_cancel)
    app.router.add_post("/api/schedules", schedules.schedule_create)
    app.router.add_post("/api/schedules/{sid}", schedules.schedule_update)
    app.router.add_post("/api/schedules/{sid}/delete", schedules.schedule_delete)
    app.router.add_post("/api/schedules/{sid}/run", schedules.schedule_run)
    app.router.add_post("/api/devices/{id}/offer-now", devices.offer_now)
    app.router.add_get("/api/devices/{id}/signal", devices.wifi_signal)
    app.router.add_get("/api/devices/{id}/changelog", devices.changelog)
    app.router.add_get("/api/devices/{id}/downgrades", devices.downgrades)
    app.router.add_post("/api/devices/{id}/settings/refresh", devices.settings_refresh)
    app.router.add_post("/api/devices/{id}/settings", devices.settings_change)
    app.router.add_post("/api/devices/{id}/restart", devices.restart)
    app.router.add_post("/api/devices/{id}/password", devices.device_password)
    app.router.add_post("/api/devices/{id}/ignore", devices.ignore)
    app.router.add_post("/api/devices/{id}/reload", devices.reload_device)
    app.router.add_post("/api/devices/{id}/forget", devices.forget_device)
    app.router.add_post("/api/devices/{id}/probe", devices.probe)
    app.router.add_get("/api/devices/{id}/report", devices.report)
    app.router.add_post("/api/getting-started/hide", devices.hide_getting_started)
    app.router.add_get("/api/firmware", firmware.firmware_list)
    app.router.add_post("/api/firmware/upload", firmware.firmware_upload, name=UPLOAD_ROUTE)
    app.router.add_post("/api/firmware/upload/confirm", firmware.firmware_upload_confirm)
    app.router.add_post("/api/firmware/upload/cancel", firmware.firmware_upload_cancel)
    app.router.add_post("/api/firmware/export", firmware.store_export)
    app.router.add_get("/api/firmware/export/{token}", firmware.store_export_download)
    app.router.add_post("/api/firmware/import", firmware.store_import, name=IMPORT_ROUTE)
    app.router.add_post("/api/firmware/import/check", firmware.store_import_check)
    app.router.add_post("/api/firmware/import/confirm", firmware.store_import_confirm)
    app.router.add_post("/api/firmware/import/cancel", firmware.store_import_cancel)
    app.router.add_post("/api/first-start/skip", firmware.first_start_skip)
    app.router.add_post("/api/devices/passwords", devices.devices_passwords)
    app.router.add_get("/api/firmware/{name}/changelog", firmware.firmware_changelog)
    app.router.add_get("/api/firmware/{name}/download", firmware.firmware_download)
    app.router.add_post("/api/firmware/{name}/delete", firmware.firmware_delete)
    app.router.add_get("/api/backups", backups.backups_list)
    app.router.add_post("/api/backups/run", backups.backups_run)
    app.router.add_post("/api/backups/run/clear", backups.backups_run_clear)
    app.router.add_post("/api/backups/restore/clear", backups.restore_clear)
    app.router.add_get("/api/backups/download/{token}", backups.backup_download_file)
    app.router.add_post("/api/backups/import/check", backups.backup_import_check)
    app.router.add_post("/api/backups/import/confirm", backups.backup_import_confirm)
    app.router.add_post("/api/backups/import/cancel", backups.backup_import_cancel)
    app.router.add_post("/api/backups/{bid}/keep", backups.backup_keep)
    app.router.add_post("/api/backups/{bid}/delete", backups.backup_delete)
    app.router.add_post("/api/backups/{bid}/preview", backups.backup_preview)
    app.router.add_post("/api/backups/{bid}/restore", backups.backup_restore)
    app.router.add_post("/api/backups/{bid}/download", backups.backup_download)
    app.router.add_post("/api/devices/{id}/backup", backups.backup_device)
    app.router.add_post("/api/devices/{id}/after-update/seen", backups.after_update_seen)
    app.router.add_post("/api/devices/{id}/backups/import", backups.backup_import, name=BACKUP_IMPORT_ROUTE)
    app.router.add_get("/api/logs", settings.logs)
    app.router.add_post("/api/logs/clear", settings.logs_clear)
    app.router.add_post("/api/logs/{id}/delete", settings.log_delete)
    app.router.add_get("/api/settings", settings.options_get)
    app.router.add_get("/api/settings/app-page", settings.app_page)
    app.router.add_post("/api/settings", settings.options_save)
    app.router.add_post("/api/settings/download-base/confirm", settings.confirm_download_base)
    app.router.add_post("/api/settings/connection-test", settings.connection_test)
    app.router.add_static("/static/", STATIC_DIR, show_index=False, follow_symlinks=False, name="static")
    return app
