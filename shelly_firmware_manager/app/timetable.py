"""Schedules of the Schedules tab and the installation time of "Install at": validation
and the next run (pure functions; the manager runs them, see manager_schedules).

A schedule does one action on a set of Shellys at a local clock time of Home Assistant
(time zone as in schedule.py). Stored in state.json as a list of objects:

    {"id", "name", "on", "action", "pause", "time", "repeat", "devices",
     "anchor", "next", "last"}

- action: one of ACTIONS (one action per schedule); pause: seconds between two device
  restarts (restart only, 0 = all at once).
- repeat: {"kind": "daily"} | {"kind": "weekdays", "days": [0..6]} (Monday = 0) |
  {"kind": "every", "n": days}; "every" counts from its anchor, the date of the first run
  after the schedule was saved with this repeat and time.
- devices: "all" (every listed Shelly, new ones included) or a list of HA device IDs.
- next: epoch of the next run (None while the schedule is off); last: result of the
  last run {"at", "severity", "text"} with "counts" {done, total, unreachable, refused,
  left} of a run, or severity "missed" with the "reason" for a run the app missed.

DST as for the check schedule: a clock time inside a spring-forward gap runs at the first
valid instant after the gap, a repeated autumn time at its first occurrence.
"""

import re
import secrets
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from schedule import instant
from shelly import plain

# Actions of a schedule -> label in the log and in notifications
ACTIONS = {
    "restart": "Restart",
    "cloud_on": "Cloud on",
    "cloud_off": "Cloud off",
    "ble_on": "Bluetooth on",
    "ble_off": "Bluetooth off",
}
# Device setting written by the cloud and Bluetooth actions: action -> (setting, value)
SETTING_ACTIONS = {
    "cloud_on": ("cloud", True),
    "cloud_off": ("cloud", False),
    "ble_on": ("ble", True),
    "ble_off": ("ble", False),
}
REPEATS = ("daily", "weekdays", "every")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
PAUSE_DEFAULT_S = 3  # default pause between two restarts
PAUSE_MAX_S = 600  # longest pause between two restarts
EVERY_MAX_DAYS = 365  # "Every n days": 1–365
NAME_MAX = 64  # characters of a schedule name
MAX_SCHEDULES = 50  # schedules kept at most
MAX_DEVICES = 500  # devices of one schedule
INSTALL_AHEAD_S = 7 * 86400  # "Install at": at most 7 days ahead
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")  # ID of a schedule (also checked for IDs from the URL)
DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def local_text(ts: float, tz: tzinfo) -> str:
    """"YYYY-MM-DD HH:MM" of an epoch in the time zone of Home Assistant (UI and log)."""
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")


def install_time(text: object, now: float, tz: tzinfo) -> float:
    """Epoch of an "Install at" time "YYYY-MM-DDTHH:MM" (local time of Home Assistant):
    in the future and at most INSTALL_AHEAD_S ahead; ValueError otherwise."""
    if not isinstance(text, str) or "T" not in text:
        raise ValueError("Install at: date and time expected")
    day_text, clock = text.split("T", 1)
    if not DATE_RE.fullmatch(day_text) or not TIME_RE.fullmatch(clock):
        raise ValueError("Install at: date and time expected")
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        raise ValueError("Install at: date and time expected") from None
    at = instant(day, clock, tz)
    if at <= now:
        raise ValueError("Install at: choose a time in the future")
    if at - now > INSTALL_AHEAD_S:
        raise ValueError("Install at: at most 7 days ahead")
    return at


def validate(data: object, known_devices: set[str] | None) -> dict[str, Any]:
    """The user-editable fields of a schedule, checked; ValueError with a readable
    message otherwise. Device IDs must be listed devices (known_devices; None: only
    their form is checked)."""
    if not isinstance(data, dict):
        raise ValueError("JSON object expected")
    raw = data.get("name")
    name = plain(raw, NAME_MAX + 1).strip() if isinstance(raw, str) else ""
    if not name:
        raise ValueError("Enter a name for the schedule")
    if len(name) > NAME_MAX:
        raise ValueError(f"Name: at most {NAME_MAX} characters")
    action = data.get("action")
    if action not in ACTIONS:
        raise ValueError("Action: unknown")
    clock = data.get("time")
    if not isinstance(clock, str) or not TIME_RE.fullmatch(clock):
        raise ValueError("Time: HH:MM (00:00–23:59) expected")
    on = data.get("on", True)
    if not isinstance(on, bool):
        raise ValueError("on: true or false expected")
    pause = data.get("pause", PAUSE_DEFAULT_S)
    if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= PAUSE_MAX_S:
        raise ValueError(f"Pause between devices: whole number of seconds between 0 and {PAUSE_MAX_S}")
    return {
        "name": name,
        "on": on,
        "action": action,
        "pause": pause if action == "restart" else None,
        "time": clock,
        "repeat": _repeat(data.get("repeat")),
        "devices": _devices(data.get("devices"), known_devices),
    }


def _repeat(value: object) -> dict[str, Any]:
    """Checked repeat rule (see the module docstring)."""
    kind = value.get("kind") if isinstance(value, dict) else None
    if kind not in REPEATS or not isinstance(value, dict):
        raise ValueError("Repeat: daily, on weekdays or every n days expected")
    if kind == "weekdays":
        days = value.get("days")
        valid = isinstance(days, list) and all(isinstance(d, int) and not isinstance(d, bool) for d in days)
        if not valid or not days or not all(0 <= d <= 6 for d in days):
            raise ValueError("Repeat: choose at least one weekday")
        return {"kind": kind, "days": sorted(set(days))}
    if kind == "every":
        n = value.get("n")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= EVERY_MAX_DAYS:
            raise ValueError(f"Repeat: every 1–{EVERY_MAX_DAYS} days")
        return {"kind": kind, "n": n}
    return {"kind": kind}


def _devices(value: object, known: set[str] | None) -> str | list[str]:
    """"all" or a non-empty list of listed device IDs (unknown IDs are refused; known
    None: any ID of the right form)."""
    if value == "all":
        return "all"
    if not isinstance(value, list) or not value or len(value) > MAX_DEVICES:
        raise ValueError("Devices: “All Shellys” or at least one device expected")
    ids = []
    for item in value:
        if not isinstance(item, str) or not DEVICE_ID_RE.fullmatch(item) or (known is not None and item not in known):
            raise ValueError("Devices: unknown device")
        if item not in ids:
            ids.append(item)
    return ids


def _epoch(value: object) -> bool:
    """True for a number usable as epoch (not a bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_stored(item: object) -> dict[str, Any]:
    """A schedule read from state.json, checked like a saved one (validate; device IDs
    only by their form, an empty device list only on a switched-off schedule – see
    manager_schedules._strike_devices) plus its timetable fields: id, anchor (ISO
    date or None), next (epoch or None) and last (object or None). ValueError for a
    damaged entry."""
    if not isinstance(item, dict) or not ID_RE.fullmatch(str(item.get("id") or "")):
        raise ValueError("no valid ID")
    emptied = item.get("devices") == [] and item.get("on") is False
    # pause is stored as None for actions other than restart
    pause = item.get("pause") if item.get("action") == "restart" else 0
    clean = validate({**item, "pause": pause, **({"devices": "all"} if emptied else {})}, None)
    if emptied:
        clean["devices"] = []
    anchor, upcoming, last = item.get("anchor"), item.get("next"), item.get("last")
    if anchor is not None:
        if not isinstance(anchor, str) or not DATE_RE.fullmatch(anchor):
            raise ValueError("anchor: date expected")
        date.fromisoformat(anchor)  # ValueError for an impossible date
    if upcoming is not None and not _epoch(upcoming):
        raise ValueError("next: time expected")
    if last is not None and not (isinstance(last, dict) and (last.get("at") is None or _epoch(last.get("at")))):
        raise ValueError("last: object expected")
    return {"id": item["id"], **clean, "anchor": anchor, "next": upcoming, "last": last}


def check_plan(plan: object) -> bool:
    """True for a usable scheduled installation read from state.json ("planned" of a
    device: time "at" as epoch, SHA-256 as text)."""
    return isinstance(plan, dict) and _epoch(plan.get("at")) and isinstance(plan.get("sha256"), str)


def new_id(taken: set[str]) -> str:
    """Random ID of a new schedule that no other schedule has."""
    while True:
        sid = secrets.token_hex(6)
        if sid not in taken:
            return sid


def anchor_for(clock: str, now: float, tz: tzinfo) -> str:
    """Anchor of "every n days": the next date the clock time is reached (today if it
    is still ahead, else tomorrow)."""
    today = datetime.fromtimestamp(now, tz).date()
    first = today if instant(today, clock, tz) > now else today + timedelta(days=1)
    return first.isoformat()


def next_run(item: dict[str, Any], after: float, tz: tzinfo) -> float | None:
    """Epoch of the first run of a schedule strictly after `after`; None while it is off."""
    if not item.get("on"):
        return None
    clock, repeat = str(item["time"]), item["repeat"]
    start = datetime.fromtimestamp(after, tz).date()
    if repeat["kind"] == "every":
        anchor = date.fromisoformat(str(item.get("anchor") or anchor_for(clock, after, tz)))
        n = int(repeat["n"])
        k = max(0, (start - anchor).days // n)
        while True:
            day = anchor + timedelta(days=k * n)
            ts = instant(day, clock, tz)
            if ts > after:
                return ts
            k += 1
    days = set(range(7)) if repeat["kind"] == "daily" else set(repeat["days"])
    for offset in range(8):
        day = start + timedelta(days=offset)
        if day.weekday() in days:
            ts = instant(day, clock, tz)
            if ts > after:
                return ts
    return None  # unreachable: every weekday occurs within 8 days


def repeat_text(item: dict[str, Any]) -> str:
    """Repeat rule and time for the log ("Daily · 04:00", "Mon, Thu · 05:00")."""
    repeat = item["repeat"]
    if repeat["kind"] == "daily":
        rule = "Daily"
    elif repeat["kind"] == "every":
        rule = f"Every {repeat['n']} days"
    else:
        rule = ", ".join(WEEKDAYS[d] for d in repeat["days"])
    return f"{rule} · {item['time']}"


def changes(old: dict[str, Any], new: dict[str, Any], names: dict[str, str]) -> list[str]:
    """Readable list of the changed fields of a schedule (log entry "changed")."""
    found = []
    if old.get("name") != new["name"]:
        found.append(f"Name: {old.get('name')} → {new['name']}")
    if old.get("on") != new["on"]:
        found.append("Switched on" if new["on"] else "Switched off")
    if old.get("action") != new["action"]:
        found.append(f"Action: {ACTIONS.get(old.get('action') or '', '–')} → {ACTIONS[new['action']]}")
    if old.get("pause") != new["pause"] and new["action"] == "restart":
        found.append(f"Pause: {old.get('pause')} s → {new['pause']} s")
    if old.get("time") != new["time"] or old.get("repeat") != new["repeat"]:
        found.append(f"Repeat: {repeat_text(old)} → {repeat_text(new)}")
    if old.get("devices") != new["devices"]:
        found.append(f"Devices: {devices_text(old.get('devices'), names)} → {devices_text(new['devices'], names)}")
    return found


def devices_text(devices: object, names: dict[str, str]) -> str:
    """"All Shellys", the device names (at most three, then the count) or "No device"."""
    if devices == "all":
        return "All Shellys"
    ids = devices if isinstance(devices, list) else []
    if not ids:
        return "No device"  # all devices of the schedule were removed from the list
    shown = [names.get(i, "?") for i in ids[:3]]
    more = len(ids) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")
