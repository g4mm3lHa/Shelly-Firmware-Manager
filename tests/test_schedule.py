"""Automatic check schedule: N days at HH:MM in the HA time zone, DST, catch-up, deferral.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import data_paths
import manager
import pytest
import schedule
import shelly
from conftest import OPTIONS, A

BERLIN = ZoneInfo("Europe/Berlin")


def ts(text):
    """Epoch of an ISO timestamp with offset."""
    return datetime.fromisoformat(text).timestamp()


def test_anchor_is_next_clock_occurrence():
    """Before the clock time today -> today, after it -> tomorrow."""
    state, changed = schedule.configure(None, 7, "02:45", ts("2026-11-06T01:00:00+01:00"), BERLIN)
    assert changed and state["anchor"] == "2026-11-06" and state["last_date"] is None
    state, _ = schedule.configure(None, 7, "02:45", ts("2026-11-06T03:00:00+01:00"), BERLIN)
    assert state["anchor"] == "2026-11-07"
    same, changed = schedule.configure(state, 7, "02:45", ts("2026-12-01T00:00:00+01:00"), BERLIN)
    assert same is state and not changed
    moved, changed = schedule.configure(state, 3, "02:45", ts("2026-12-01T00:00:00+01:00"), BERLIN)
    assert changed and moved["anchor"] == "2026-12-01"


def test_catch_up_collapses_and_keeps_cadence():
    """Missed dates run once; the cadence stays anchored."""
    state = {"days": 7, "time": "02:45", "anchor": "2026-11-07", "last_date": None}
    now = ts("2026-11-29T12:00:00+01:00")  # 07, 14, 21, 28 missed
    due, following = schedule.status(state, now, BERLIN)
    assert due == date(2026, 11, 28) and following == ts("2026-12-05T02:45:00+01:00")
    state["last_date"] = due.isoformat()
    assert schedule.seconds_until(state, now, BERLIN) == pytest.approx(following - now)
    # Before the anchor nothing is due
    assert schedule.status(state | {"last_date": None}, ts("2026-11-06T12:00:00+01:00"), BERLIN)[0] is None


def test_dst_gap_runs_at_first_valid_instant():
    """2026-03-29 02:30 does not exist in Berlin: run at 03:00 CEST, next day normal."""
    assert schedule.instant(date(2026, 3, 29), "02:30", BERLIN) == ts("2026-03-29T03:00:00+02:00")
    assert schedule.instant(date(2026, 3, 30), "02:30", BERLIN) == ts("2026-03-30T02:30:00+02:00")
    state = {"days": 1, "time": "02:30", "anchor": "2026-03-28", "last_date": "2026-03-28"}
    assert schedule.status(state, ts("2026-03-29T02:59:59+02:00"), BERLIN)[0] is None
    assert schedule.status(state, ts("2026-03-29T03:00:00+02:00"), BERLIN)[0] == date(2026, 3, 29)


def test_dst_repeated_hour_runs_once_at_first_occurrence():
    """2026-10-25 02:30 happens twice: first occurrence, deduplicated by date."""
    first = ts("2026-10-25T02:30:00+02:00")
    assert schedule.instant(date(2026, 10, 25), "02:30", BERLIN) == first
    state = {"days": 1, "time": "02:30", "anchor": "2026-10-24", "last_date": "2026-10-24"}
    due, _ = schedule.status(state, first, BERLIN)
    assert due == date(2026, 10, 25)
    state["last_date"] = due.isoformat()
    assert schedule.status(state, ts("2026-10-25T02:30:00+01:00"), BERLIN)[0] is None


def test_local_zone_from_environment(monkeypatch):
    """The HA time zone comes from TZ; unknown/missing values fall back to UTC."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert schedule.local_zone()[1] == "Europe/Berlin"
    monkeypatch.setenv("TZ", "Nowhere/Invalid")
    assert schedule.local_zone()[1] == "UTC"


async def test_automatic_check_records_attempt_once(mgr, net):
    """The due date is recorded at the start – also when the check fails."""
    mgr.schedule = {"days": 7, "time": "02:45", "anchor": "2020-01-01", "last_date": None}
    assert mgr.scan_due_in(datetime.now(UTC).timestamp()) == 0

    async def broken():
        raise shelly.ShellyError("registry down")

    mgr._registry = broken
    assert await mgr.scan(automatic=True)
    assert mgr.schedule["last_date"] is not None
    assert mgr.scan_due_in(datetime.now(UTC).timestamp()) > 0  # no tight retry loop
    assert json.loads(data_paths.STATE_FILE.read_text())["schedule"] == mgr.schedule


async def test_due_check_waits_for_installations(mgr, net):
    """A running installation postpones the due check instead of consuming it."""
    mgr.schedule = {"days": 7, "time": "02:45", "anchor": "2020-01-01", "last_date": None}
    mgr.installing.add(A)
    assert not await mgr.scan(automatic=True)
    assert mgr.schedule["last_date"] is None
    mgr.installing.clear()
    assert await mgr.scan(automatic=True)


async def test_no_immediate_check_without_schedule(net):
    """A state without schedule (e.g. from an older version) is not due at once: next clock time."""
    data_paths.STATE_FILE.write_text(json.dumps({"devices": {}, "last_scan": 1, "scanned_version": "2.2.1"}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.scan_due_in(datetime.now(UTC).timestamp()) > 0
    assert m.schedule["days"] == 7 and m.schedule["time"] == "02:45"


async def test_manual_and_single_checks_do_not_shift_schedule(mgr, net):
    """Only the configured schedule counts – manual and single-device checks
    leave it unchanged (replaces the 2.x interval-from-last-check test)."""
    before = dict(mgr.schedule)
    await mgr.scan(user="u")
    await mgr.check_device(A)
    assert mgr.schedule == before and mgr.schedule["last_date"] is None
