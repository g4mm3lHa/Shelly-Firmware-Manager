"""Schedule of the automatic check: every N calendar days at a local clock time.

The time zone is the one configured in Home Assistant: the Supervisor passes it
to the container as the TZ environment variable (no location or internet
lookup). The schedule state is a small dict persisted in state.json:

    {"days": 7, "time": "02:45", "anchor": "2026-11-07", "last_date": "2026-11-07"}

- anchor: first scheduled date after the schedule was (re)configured – the next
  occurrence of the clock time. Later dates are anchor + k * days (calendar
  days, so the local clock time stays the same across DST changes).
- last_date: scheduled date of the last automatic attempt. At most one automatic
  check per scheduled date; an attempt is recorded when it starts, so a failing
  check never causes a tight retry loop.
- Missed dates (app stopped) collapse into one catch-up run; the cadence stays
  anchored. Manual checks and Reload Device Info never touch this state.
- DST: a clock time inside a spring-forward gap runs once at the first valid
  instant after the gap; a repeated autumn time runs at its first occurrence.
"""

import logging
import os
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("sfm.schedule")


def local_zone() -> tuple[tzinfo, str]:
    """(tzinfo, name) of the Home Assistant time zone; UTC if TZ is missing/invalid."""
    name = os.environ.get("TZ", "").strip()
    if name:
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("Unknown time zone %r – the schedule uses UTC", name[:64])
    else:
        log.warning("No time zone (TZ) provided – the schedule uses UTC")
    return ZoneInfo("UTC"), "UTC"


def instant(day: date, clock: str, tz: tzinfo) -> float:
    """Epoch seconds of the local clock time "HH:MM" on day.

    Repeated time (autumn): first occurrence (fold=0). Nonexistent time (spring
    gap): the first valid instant after the gap, i.e. the transition itself."""
    hour, minute = (int(x) for x in clock.split(":"))
    local = datetime.combine(day, time(hour, minute), tzinfo=tz)
    ts = local.timestamp()
    if datetime.fromtimestamp(ts, tz).replace(tzinfo=None) == local.replace(tzinfo=None):
        return ts
    # Inside the gap: fold=1 maps with the offset after the transition (an instant
    # before the gap), fold=0 with the offset before it (an instant after the gap).
    # Binary search the first second that already has the new offset.
    low, high = int(local.replace(fold=1).timestamp()), int(ts)
    after = datetime.fromtimestamp(high, tz).utcoffset()
    while low < high:
        mid = (low + high) // 2
        if datetime.fromtimestamp(mid, tz).utcoffset() == after:
            high = mid
        else:
            low = mid + 1
    return float(high)


def configure(
    state: dict[str, Any] | None, days: int, clock: str, now: float, tz: tzinfo
) -> tuple[dict[str, Any], bool]:
    """Schedule state for the configured interval/time.

    An unchanged configuration keeps the state (anchor and last attempt); a new
    or changed one is anchored at the next occurrence of the clock time and has
    no attempt yet. Returns (state, changed)."""
    unchanged = isinstance(state, dict) and state.get("days") == days and state.get("time") == clock
    if unchanged and state is not None and _date(state.get("anchor")):
        return state, False
    today = datetime.fromtimestamp(now, tz).date()
    anchor = today if instant(today, clock, tz) > now else today + timedelta(days=1)
    return {"days": days, "time": clock, "anchor": anchor.isoformat(), "last_date": None}, True


def _date(text: object) -> date | None:
    """date from an ISO string, None if missing or invalid."""
    try:
        return date.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def status(state: dict[str, Any], now: float, tz: tzinfo) -> tuple[date | None, float]:
    """(due date or None, epoch of the next run after the due one).

    The due date is the latest scheduled date whose instant has passed and that
    has not been attempted yet."""
    anchor, days, clock = _date(state["anchor"]), int(state["days"]), str(state["time"])
    if anchor is None:  # configure() always stores a valid anchor
        raise ValueError("Schedule without a valid anchor date")
    today = datetime.fromtimestamp(now, tz).date()
    k = (today - anchor).days // days  # index of the last scheduled date <= today
    if k >= 0 and instant(anchor + timedelta(days=k * days), clock, tz) > now:
        k -= 1  # today's run has not been reached yet
    last = _date(state.get("last_date"))
    due = anchor + timedelta(days=k * days) if k >= 0 else None
    if due and last and due <= last:
        due = None
    following = anchor + timedelta(days=max(k + 1, 0) * days)
    return due, instant(following, clock, tz)


def seconds_until(state: dict[str, Any], now: float, tz: tzinfo) -> float:
    """Seconds until the next automatic check; 0 = due now."""
    due, following = status(state, now, tz)
    return 0 if due else max(0.0, following - now)


def next_run(state: dict[str, Any], now: float, tz: tzinfo) -> float:
    """Epoch of the next automatic check (now if one is due) – for the UI."""
    due, following = status(state, now, tz)
    return now if due else following


def iso_utc(ts: float) -> str:
    """ISO timestamp for log lines (UTC, seconds)."""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")
