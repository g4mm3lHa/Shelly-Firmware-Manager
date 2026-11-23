"""Routes of the Schedules tab: create, change, delete and run a schedule.

A run is reserved synchronously (a second request gets 409); deleting and "Run now"
need the flag confirm.
"""

from typing import Any

from aiohttp import web
from events import user_from
from timetable import ID_RE as SCHEDULE_ID_RE
from web_base import Handlers, json_body, translated


class ScheduleRoutes(Handlers):
    """Handlers of the Schedules tab."""

    def schedule_id_from(self, request: web.Request) -> str:
        """Validated schedule ID from the URL; unknown IDs -> 404."""
        sid = request.match_info["sid"]
        if not SCHEDULE_ID_RE.fullmatch(sid) or not any(s.get("id") == sid for s in self.mgr.schedules):
            raise web.HTTPNotFound(text="Unknown schedule")
        return sid

    def schedule_answer(self, item: dict[str, Any]) -> web.Response:
        """The saved schedule in the form of the state (see Manager.schedules_view)."""
        view = next((v for v in self.mgr.schedules_view() if v["id"] == item.get("id")), None)
        return web.json_response(view)

    async def schedule_create(self, request: web.Request) -> web.StreamResponse:
        """Add a schedule."""
        body = await json_body(request)
        item = translated(lambda: self.mgr.create_schedule(body, user_from(request.headers)), conflict=())
        return self.schedule_answer(item)

    async def schedule_update(self, request: web.Request) -> web.StreamResponse:
        """Change a schedule (all user-editable fields)."""
        sid = self.schedule_id_from(request)
        body = await json_body(request)
        item = translated(lambda: self.mgr.update_schedule(sid, body, user_from(request.headers)), conflict=())
        return self.schedule_answer(item)

    async def schedule_delete(self, request: web.Request) -> web.StreamResponse:
        """Delete a schedule (after a confirmation)."""
        sid = self.schedule_id_from(request)
        if (await json_body(request)).get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        self.mgr.delete_schedule(sid, user_from(request.headers))
        return web.json_response({"deleted": True})

    async def schedule_run(self, request: web.Request) -> web.StreamResponse:
        """"Run now" (after a confirmation): refused while a check, an installation or
        another run is going on."""
        sid = self.schedule_id_from(request)
        if (await json_body(request)).get("confirm") is not True:
            raise web.HTTPBadRequest(text="Confirmation incomplete")
        translated(lambda: self.mgr.reserve_schedule_run(sid), bad=())  # synchronous: a second request gets 409
        self.background(self.mgr.run_schedule_now(sid, user_from(request.headers)))
        return web.json_response({"started": True})
