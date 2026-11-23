"""Routes of the Firmware tab: the stored files, their changelog, download and delete,
the manual upload, the store export/import and the first-start question.

Upload and import bodies are binary (application/octet-stream, see web_ui.BINARY_ROUTES)
and streamed to disk in parts of at most PART_MAX: Home Assistant Ingress refuses
request bodies above 16 MiB. The export password is returned once and kept nowhere.
"""

import asyncio
import re

import data_paths
from aiohttp import web
from events import user_from
from manager_transfer import PART_MAX, TRANSFER_ID_RE
from shelly import ShellyError
from shelly_cloud import FW_NAME_RE, MAX_FW_SIZE
from manager import Manager
from web_base import (
    EXPORT_TOKEN_RE,
    SECURITY_HEADERS,
    UPLOAD_CHUNK,
    Background,
    Handlers,
    guarded,
    json_body,
    translated,
)


class FirmwareRoutes(Handlers):
    """Handlers of the Firmware tab, the store export/import and the first-start question."""

    def __init__(self, mgr: Manager, background: Background, app_version: str) -> None:
        """app_version: written into store exports."""
        super().__init__(mgr, background)
        self.app_version = app_version

    def firmware_name_from(self, request: web.Request, unknown: bool = False) -> str:
        """Validated firmware file name from the URL; names the store does not list
        -> 404 (unknown=True: an unknown file of the firmware folder is accepted)."""
        name = request.match_info["name"]
        listed = name in self.mgr.firmware or (unknown and name in self.mgr.unknown_files)
        if not FW_NAME_RE.fullmatch(name) or not listed:
            raise web.HTTPNotFound(text="Unknown firmware")
        return name

    async def firmware_list(self, _request: web.Request) -> web.StreamResponse:
        """Stored firmware files with metadata, the total size of the store and the free
        disk space of its volume (null if unreadable)."""
        return web.json_response({"firmware": self.mgr.firmware_list(), "total_size": self.mgr.store_size(),
                                  "free_space": self.mgr.free_space()})

    async def firmware_changelog(self, request: web.Request) -> web.StreamResponse:
        """Changelog kept with a stored firmware (plain text entries); nothing is
        requested from the manufacturer for it."""
        entries = self.mgr.firmware_changelog(self.firmware_name_from(request))
        return web.json_response({"available": entries is not None, "versions": entries or []})

    async def firmware_download(self, request: web.Request) -> web.StreamResponse:
        """Stored firmware file as download with a descriptive file name."""
        name = self.firmware_name_from(request)
        path = data_paths.FW_DIR / name
        if path.is_symlink() or not path.is_file():
            raise web.HTTPNotFound(text="File missing")
        meta = self.mgr.firmware[name]
        # Descriptive file name, harmless characters only
        nice = re.sub(r"[^A-Za-z0-9._-]", "_", f"{meta.get('type')}-{meta.get('version')}")[:100]
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{nice}.{name.rsplit(".", 1)[1]}"',
            },
        )

    async def firmware_delete(self, request: web.Request) -> web.StreamResponse:
        """Delete a stored firmware (logged with the HA user). Needs the second
        confirmation: {"confirm": true, "acknowledged": true}. Also for an unknown
        file of the firmware folder."""
        name = self.firmware_name_from(request, unknown=True)
        body = await json_body(request)
        translated(
            lambda: self.mgr.delete_firmware(
                name, user_from(request.headers), confirm=body.get("confirm"), acknowledged=body.get("acknowledged")
            )
        )
        return web.json_response({"ok": True})

    def part_headers(self, request: web.Request, limit: int, too_large: str) -> tuple[int, int, int, str | None]:
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

    async def firmware_upload(self, request: web.Request) -> web.StreamResponse:
        """Step 1 of a manual upload: the raw file (or one part of it, see
        part_headers) as body, its name in the X-Filename header; at most 64 MiB in
        all. The body is streamed to disk and never read into memory as a whole."""
        length, total, offset, transfer = self.part_headers(request, MAX_FW_SIZE, "Firmware larger than 64 MB")
        filename = request.headers.get("X-Filename", "")[:200]
        result = await guarded(
            self.mgr.stage_upload_part(
                request.content.iter_chunked(UPLOAD_CHUNK), filename, length, total, offset, transfer,
                user_from(request.headers),
            )
        )
        return web.json_response(result)

    async def firmware_upload_confirm(self, request: web.Request) -> web.StreamResponse:
        """Step 2: register the staged upload with type, version and an optional SHA-256."""
        body = await json_body(request)
        entry = translated(
            lambda: self.mgr.confirm_upload(
                body.get("token"), body.get("type"), body.get("version"), body.get("sha256"), user_from(request.headers)
            ),
            conflict=(ShellyError, OSError),
        )
        return web.json_response({"stored": entry["name"], "type": entry["type"], "version": entry["version"]})

    async def firmware_upload_cancel(self, request: web.Request) -> web.StreamResponse:
        """Discard the staged upload."""
        self.mgr.cancel_upload((await json_body(request)).get("token"))
        return web.json_response({"ok": True})

    async def store_export(self, request: web.Request) -> web.StreamResponse:
        """Step 1 of a store export: generate the export password (returned once) and
        a single-use token for the download."""
        await json_body(request)
        return web.json_response(await guarded(self.mgr.prepare_export(user_from(request.headers))))

    async def store_export_download(self, request: web.Request) -> web.StreamResponse:
        """Step 2: the export ZIP as download (token from step 1, single use). The
        temporary file is deleted after sending, also when the transfer breaks."""
        token = request.match_info["token"]
        if not EXPORT_TOKEN_RE.fullmatch(token):
            raise web.HTTPNotFound(text="Unknown export")
        try:
            path, filename = await self.mgr.build_export(token, self.app_version)
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

    async def store_import(self, request: web.Request) -> web.StreamResponse:
        """Step 1 of a store import: the export file (or one part of it, see
        part_headers) as body, streamed to disk; its size is limited by the option
        import_max_mb."""
        too_large = f"File larger than the maximum import size ({self.mgr.import_max // 1048576} MB, Settings)"
        length, total, offset, transfer = self.part_headers(request, self.mgr.import_max, too_large)
        return web.json_response(
            await guarded(
                self.mgr.stage_import_part(request.content.iter_chunked(UPLOAD_CHUNK), length, total, offset, transfer)
            )
        )

    async def store_import_check(self, request: web.Request) -> web.StreamResponse:
        """Step 2: check the staged export with its password; returns the summary."""
        body = await json_body(request)
        return web.json_response(await guarded(self.mgr.check_import(body.get("token"), body.get("password"))))

    async def store_import_confirm(self, request: web.Request) -> web.StreamResponse:
        """Step 3: store the checked import (new files, missing data) – only with the
        user's statement that they created the export or trust its source
        ("acknowledged": true): a sealed export proves no origin."""
        body = await json_body(request)
        try:
            counts = await self.mgr.confirm_import(
                body.get("token"), user_from(request.headers), body.get("acknowledged")
            )
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        except (ShellyError, OSError) as err:
            raise web.HTTPConflict(text=str(err)) from None
        return web.json_response(counts)

    async def store_import_cancel(self, request: web.Request) -> web.StreamResponse:
        """Discard the staged import."""
        self.mgr.cancel_import((await json_body(request)).get("token"))
        return web.json_response({"ok": True})

    async def first_start_skip(self, request: web.Request) -> web.StreamResponse:
        """First-start question of a fresh installation answered with "Skip"."""
        await json_body(request)
        self.mgr.skip_first_start(user_from(request.headers))
        return web.json_response({"ok": True})
