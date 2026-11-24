"""Routes of the Logs and Settings tabs: the activity log (list, delete one, delete all),
the app options (read, save with a restart of the app, confirm the download URL) and the
connection test of the download server.
"""

import asyncio
import logging

from aiohttp import web
from events import ENTRY_ID_RE, user_from
from shelly import ShellyError
from web_base import DEVICE_ID_RE, Handlers, guarded, json_body, translated

log = logging.getLogger("sfm.ui")

RESTART_DELAY_S = 1  # let the answer reach the browser before the app restarts


class SettingsRoutes(Handlers):
    """Handlers of the Logs and Settings tabs."""

    async def connection_test(self, request: web.Request) -> web.StreamResponse:
        """Connection test of the download server ({"device": ID or null = automatic});
        answers with the result once the device has tried (409 if it cannot start)."""
        device = (await json_body(request)).get("device")
        if device is not None and (not isinstance(device, str) or not DEVICE_ID_RE.fullmatch(device)):
            raise web.HTTPBadRequest(text="Unknown device")
        return web.json_response(await guarded(self.mgr.run_connection_test(device, user_from(request.headers))))

    async def logs(self, _request: web.Request) -> web.StreamResponse:
        """Activity log, newest first."""
        return web.json_response({"entries": self.mgr.events.list()})

    async def log_delete(self, request: web.Request) -> web.StreamResponse:
        """Delete one log entry. The deletion is written to the app log only."""
        entry_id = request.match_info["id"]
        if (await json_body(request)).get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        if not ENTRY_ID_RE.fullmatch(entry_id) or not self.mgr.events.remove(entry_id):
            raise web.HTTPNotFound(text="Unknown log entry")
        log.info("Log entry %s deleted by %s", entry_id, user_from(request.headers))
        return web.json_response({"deleted": True})

    async def logs_clear(self, request: web.Request) -> web.StreamResponse:
        """Delete all log entries; one entry naming the user stays."""
        if (await json_body(request)).get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        user = user_from(request.headers)
        count = self.mgr.events.clear(user)
        log.info("Activity log cleared by %s (%d entries)", user, count)
        return web.json_response({"deleted": count})

    async def options_get(self, _request: web.Request) -> web.StreamResponse:
        """App options (there are no secret options) and the host port of the download
        server, read from the Supervisor each time (it can be changed in Home Assistant)."""
        await self.mgr.read_host_port()
        # installed_versions: storage of the option "Keep installed versions" (not an option)
        return web.json_response({
            **self.mgr.options_view(), "installed_versions": self.mgr.installed_versions(),
            "host_port": self.mgr.host_port, "host_port_known": self.mgr.host_port_known,
        })  # fmt: skip

    async def app_page(self, _request: web.Request) -> web.StreamResponse:
        """Path of this app's page in the Home Assistant frontend (changelog link)."""
        return web.json_response({"url": await self.mgr.app_page()})

    async def options_save(self, request: web.Request) -> web.StreamResponse:
        """Save changed app options via the Supervisor, then restart the app."""
        changed = await guarded(self.mgr.save_options(await json_body(request), user_from(request.headers)))
        if changed:

            async def restart_later() -> None:
                """Restart after the answer has been sent; failures are logged."""
                await asyncio.sleep(RESTART_DELAY_S)
                try:
                    await self.mgr.restart_app()
                except ShellyError as err:
                    log.error("App restart failed – restart it manually: %s", err)

            self.background(restart_later())
        return web.json_response({"changed": changed, "restarting": bool(changed)})

    async def confirm_download_base(self, request: web.Request) -> web.StreamResponse:
        """Confirm that the former example download URL really is the address of Home
        Assistant (body {"url": <current value>}); stored in the state, no restart."""
        url = (await json_body(request)).get("url")
        translated(lambda: self.mgr.confirm_download_base(url, user_from(request.headers)), bad=())
        return web.json_response({"confirmed": True})
