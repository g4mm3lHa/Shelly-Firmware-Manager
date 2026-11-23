"""Routes of the Backups tab and the backup dialogs of a device: list, back up now,
keep, delete, restore (preview, then the chosen parts), download as a file sealed with
a new password, import of such a file.

The settings inside a backup leave the app only as such an encrypted file and as the
list of differences of the restore dialog (no password fields: they are no part of a
backup; a secret written into a script or a webhook or action URL is, see shelly_backup).
"""


from aiohttp import web
from backup_store import ID_RE as BACKUP_ID_RE
from events import user_from
from web_base import EXPORT_TOKEN_RE, UPLOAD_CHUNK, Handlers, guarded, json_body, translated


class BackupRoutes(Handlers):
    """Handlers of the Backups tab and the backup dialogs."""

    def backup_id_from(self, request: web.Request) -> str:
        """Validated ID of a stored backup from the URL; unknown IDs -> 404."""
        backup_id = request.match_info["bid"]
        if not BACKUP_ID_RE.fullmatch(backup_id) or self.mgr.backups.get(backup_id) is None:
            raise web.HTTPNotFound(text="Unknown backup")
        return backup_id

    async def backups_list(self, _request: web.Request) -> web.StreamResponse:
        """Every device with its stored backups (Backups tab, "Backups" dialog of a
        device) – the lists only, never the settings inside a backup."""
        return web.json_response(self.mgr.backups_view())

    async def backup_device(self, request: web.Request) -> web.StreamResponse:
        """"Back up now" for one device: reads its settings and stores them."""
        device_id = self.device_id_from(request)
        await json_body(request)
        return web.json_response({"backup": await guarded(self.mgr.backup_now(device_id, user_from(request.headers)))})

    async def backups_run(self, request: web.Request) -> web.StreamResponse:
        """"Back up now" for several devices ({"devices": "all" or [IDs]}): runs in
        the background, progress and result per device are part of the state."""
        devices = (await json_body(request)).get("devices")
        ids = translated(lambda: self.mgr.reserve_backup_run(devices))  # synchronous: a second request gets 409
        self.background(self.mgr.run_backups(ids, user_from(request.headers)))
        return web.json_response({"started": True, "total": len(ids)})

    async def backups_run_clear(self, request: web.Request) -> web.StreamResponse:
        """The dialog showed the result of the finished run."""
        await json_body(request)
        self.mgr.clear_backup_run()
        return web.json_response({"ok": True})

    async def backup_keep(self, request: web.Request) -> web.StreamResponse:
        """Mark a backup as kept or take the mark off ({"kept": true/false})."""
        backup_id = self.backup_id_from(request)
        kept = (await json_body(request)).get("kept")
        entry = translated(lambda: self.mgr.keep_backup(backup_id, kept), conflict=())
        return web.json_response({"backup": entry})

    async def backup_delete(self, request: web.Request) -> web.StreamResponse:
        """Delete one backup. Needs {"confirm": true}."""
        backup_id = self.backup_id_from(request)
        confirm = (await json_body(request)).get("confirm")
        translated(lambda: self.mgr.delete_backup(backup_id, confirm, user_from(request.headers)))
        return web.json_response({"deleted": True})

    async def backup_preview(self, request: web.Request) -> web.StreamResponse:
        """Step 1 of a restore: read the device and list what restoring would change."""
        backup_id = self.backup_id_from(request)
        await json_body(request)
        return web.json_response(await guarded(self.mgr.preview_restore(backup_id)))

    async def backup_restore(self, request: web.Request) -> web.StreamResponse:
        """Step 2: restore the chosen parts in the background ({"parts": [...],
        "confirm": true, and "network_acknowledged": true for the network settings});
        progress and result are part of the state."""
        backup_id = self.backup_id_from(request)
        body = await json_body(request)
        if body.get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        translated(
            lambda: self.mgr.reserve_restore(
                backup_id, body.get("parts"), body.get("network_acknowledged"), user_from(request.headers)
            )
        )  # synchronous: a second request gets 409
        self.background(self.mgr.run_restore())
        return web.json_response({"started": True})

    async def restore_clear(self, request: web.Request) -> web.StreamResponse:
        """The dialog showed the result of the finished restore."""
        await json_body(request)
        self.mgr.clear_restore()
        return web.json_response({"ok": True})

    async def backup_download(self, request: web.Request) -> web.StreamResponse:
        """Step 1 of a download: generate the password of the file (returned once)
        and a single-use token for the download."""
        backup_id = self.backup_id_from(request)
        await json_body(request)
        return web.json_response(
            await guarded(self.mgr.prepare_backup_download(backup_id, user_from(request.headers)))
        )

    async def backup_download_file(self, request: web.Request) -> web.StreamResponse:
        """Step 2: the backup as a file encrypted with the password of step 1 (token
        from step 1, single use)."""
        token = request.match_info["token"]
        if not EXPORT_TOKEN_RE.fullmatch(token):
            raise web.HTTPNotFound(text="Unknown download")
        content, filename = translated(lambda: self.mgr.take_backup_download(token), bad=())
        return web.Response(
            body=content,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    async def backup_import(self, request: web.Request) -> web.StreamResponse:
        """Step 1 of the import of a backup file for a device: the raw file as body
        (at most 4 MiB, kept in memory until it is imported or dropped)."""
        device_id = self.device_id_from(request)
        return web.json_response(
            await guarded(
                self.mgr.stage_backup_import(
                    device_id, request.content.iter_chunked(UPLOAD_CHUNK), request.content_length
                )
            )
        )

    async def backup_import_check(self, request: web.Request) -> web.StreamResponse:
        """Step 2: open the staged file with its password and check it; returns the summary."""
        body = await json_body(request)
        return web.json_response(await guarded(self.mgr.check_backup_import(body.get("token"), body.get("password"))))

    async def backup_import_confirm(self, request: web.Request) -> web.StreamResponse:
        """Step 3: store the checked file as a backup of its device."""
        body = await json_body(request)
        entry = translated(lambda: self.mgr.confirm_backup_import(body.get("token"), user_from(request.headers)))
        return web.json_response({"backup": entry})

    async def backup_import_cancel(self, request: web.Request) -> web.StreamResponse:
        """Discard the staged backup file."""
        self.mgr.cancel_backup_import((await json_body(request)).get("token"))
        return web.json_response({"ok": True})
