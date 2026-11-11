"""Persistence: state.json, activity log (JSON Lines), credentials, UI table settings.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import json
from datetime import UTC, datetime

import events
import manager
import pytest
from credentials import Credentials
from events import EventLog

from conftest import OPTIONS, A
from helpers import chunks, firmware_zip


async def test_partial_downloads_removed_at_start(net):
    """Leftover .part files from a crash are deleted when the app starts."""
    fw = manager.FW_DIR
    fw.mkdir(parents=True)
    (fw / ".part-0123456789abcdef").write_bytes(b"half")
    (fw / ("a" * 64 + ".zip")).write_bytes(b"keep")
    manager.Manager(dict(OPTIONS), session=None, token="t")
    assert not (fw / ".part-0123456789abcdef").exists()
    assert (fw / ("a" * 64 + ".zip")).exists()


async def test_save_failure_keeps_previous_state(mgr, net, monkeypatch):
    """Write error (e.g. disk full): old state.json intact, error shown, check continues."""
    await mgr.scan()
    old = manager.STATE_FILE.read_text()
    real = type(manager.STATE_FILE).write_text

    def failing(self, *args, **kwargs):
        if self.suffix == ".tmp":
            raise OSError(28, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(manager.STATE_FILE), "write_text", failing)
    assert await mgr.scan() is True
    assert manager.STATE_FILE.read_text() == old
    assert "No space left" in mgr.storage_error
    assert mgr.public_state()["storage_error"]
    assert not manager.STATE_FILE.with_suffix(".tmp").exists()
    monkeypatch.setattr(type(manager.STATE_FILE), "write_text", real)
    assert mgr.save() and mgr.storage_error is None


async def test_state_file_compact_and_no_runtime_state(mgr, net):
    """state.json is compact JSON and does not persist activity/progress."""
    await mgr.scan()
    text = manager.STATE_FILE.read_text()
    assert "\n" not in text and "activity" not in text and "progress" not in json.loads(text)


async def test_restart_during_install_is_unconfirmed(net):
    """An installation interrupted by a restart is never shown as success."""
    manager.STATE_FILE.write_text(json.dumps({"devices": {A: {"name": "K", "status": "installing", "last_check": 5}}}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.devices[A]["status"] == manager.STATUS_UNCLEAR
    assert m.progress is None and m.activity == {}


async def test_ui_prefs_order_validated(mgr):
    """Column order is stored deduplicated; invalid names are refused."""
    mgr.set_ui({"devices": {"sort": ["ip", "desc"], "hidden": [], "order": ["ip", "gen", "ip"]}})
    assert mgr.ui["devices"]["order"] == ["ip", "gen"]
    with pytest.raises(ValueError):
        mgr.set_ui({"devices": {"sort": ["ip", "desc"], "hidden": [], "order": ["<b>"]}})


def test_no_shared_password_fallback(tmp_path):
    """Credentials only come from per-device entries."""
    creds = Credentials(tmp_path / "c.json")
    assert creds.auth_for(A, 2) is None and creds.auth_for(A, 1) is None


def test_log_limit_trims_once_and_keeps_limit(tmp_path):
    """Initial trim to the newest 1000; a configured 3000 survives a restart."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    path.write_text(json.dumps([{"ts": now - 5000 + i, "kind": "check", "ok": True, "n": i} for i in range(5000)]))
    log = EventLog(path, 90, 1000)
    assert len(log.entries) == 1000 and log.entries[-1]["n"] == 4999
    path.write_text(json.dumps([{"ts": now - 5000 + i, "kind": "check", "ok": True} for i in range(5000)]))
    assert len(EventLog(path, 90, 3000).entries) == 3000
    assert len(EventLog(path, 90, 3000).entries) == 3000


async def test_upload_log_entries_survive_a_restart(mgr, net):
    """3.0.0 final review: the log kind "upload" is kept when the log is loaded again."""
    await mgr.scan()
    data = firmware_zip()
    staged = await mgr.stage_upload(chunks(data), "fw.zip", len(data), "alice")
    mgr.confirm_upload(staged["token"], "Plus1", "1.2.0", None, "alice")
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert any(e["kind"] == "upload" and e["version"] == "1.2.0" for e in m.events.entries)


# ---------------------------------------------------------------- Activity log storage (3.0.1)


def without_id(entries):
    """Entries without the "id" every entry carries since 3.1.1."""
    return [{k: v for k, v in e.items() if k != "id"} for e in entries]


def test_log_array_of_3_0_0_is_converted_once(tmp_path):
    """The JSON array written by 3.0.0 is read and rewritten as JSON Lines; historic
    entries (also without severity) stay unchanged apart from the ID added by 3.1.1."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    old = [{"ts": now, "kind": "check", "ok": False, "result": "x"}, {"ts": now, "kind": "upload", "ok": True}]
    path.write_text(json.dumps(old))
    log = EventLog(path, 90, 1000)
    assert without_id(log.entries) == old
    assert without_id([json.loads(line) for line in path.read_text().splitlines()]) == old
    log.add("delete", type="t")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert without_id(lines[:2]) == old and lines[2]["severity"] == "success"
    assert EventLog(path, 90, 1000).entries == lines


def test_log_entries_get_an_id_once(tmp_path, monkeypatch):
    """3.1.1: entries of older versions get an ID at the first start (one rewrite);
    afterwards the file is left alone and new entries carry their own ID."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    path.write_text("".join(json.dumps({"ts": now, "kind": "check", "ok": True, "n": n}) + "\n" for n in range(3)))
    log = EventLog(path, 90, 1000)
    ids = [e["id"] for e in log.entries]
    assert len(set(ids)) == 3 and all(events.ENTRY_ID_RE.match(i) for i in ids)
    stored = path.read_text()
    assert [json.loads(line)["id"] for line in stored.splitlines()] == ids
    monkeypatch.setattr(EventLog, "_compact", lambda _self: pytest.fail("rewritten again"))
    again = EventLog(path, 90, 1000)
    assert [e["id"] for e in again.entries] == ids and path.read_text() == stored
    assert events.ENTRY_ID_RE.match(again.add("check")["id"])


def test_log_entry_can_be_deleted_and_the_log_cleared(tmp_path):
    """3.1.1: a single entry is removed from memory and file; clearing removes all
    entries and leaves one entry that names the user."""
    path = tmp_path / "events.json"
    log = EventLog(path, 90, 1000)
    first, second, third = (log.add("check", n=n) for n in range(3))
    assert log.remove(second["id"]) is True and log.remove(second["id"]) is False
    assert [e["n"] for e in EventLog(path, 90, 1000).entries] == [0, 2]
    assert log.clear("alice") == 2
    [entry] = EventLog(path, 90, 1000).entries
    assert (entry["kind"], entry["action"], entry["entries"], entry["user"]) == ("log", "cleared", 2, "alice")
    assert first["id"] != third["id"] != entry["id"]


def test_log_add_appends_one_line(tmp_path, monkeypatch):
    """Adding an entry appends one line; the file is not rewritten."""
    path = tmp_path / "events.json"
    log = EventLog(path, 90, 1000)
    log.add("check", result="first")
    rewrites = []
    monkeypatch.setattr(log, "_compact", lambda: rewrites.append(1))
    before = path.read_text()
    log.add("check", result="second")
    assert rewrites == [] and path.read_text().startswith(before) and len(path.read_text().splitlines()) == 2


def test_log_is_compacted_after_the_slack(tmp_path, monkeypatch):
    """At the size limit dropped entries stay in the file until COMPACT_SLACK is exceeded;
    then the file is rewritten with the kept entries only."""
    import events

    monkeypatch.setattr(events, "COMPACT_SLACK", 3)
    path = tmp_path / "events.json"
    log = EventLog(path, 90, 100)
    for i in range(104):
        log.add("check", n=i)
        assert len(path.read_text().splitlines()) <= 100 + 3
    assert len(log.entries) == 100 and log.entries[0]["n"] == 4
    assert [json.loads(line)["n"] for line in path.read_text().splitlines()][-100:] == list(range(4, 104))
    assert [e["n"] for e in EventLog(path, 90, 100).entries] == list(range(4, 104))


def test_log_skips_a_damaged_line(tmp_path):
    """A line cut off by a power loss is skipped and dropped by the next rewrite."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    good = {"ts": now, "kind": "check", "ok": True, "severity": "success"}
    path.write_text(json.dumps(good) + "\n" + '{"ts": 1, "kind": "che')
    log = EventLog(path, 90, 1000)
    assert without_id(log.entries) == [good]
    assert without_id([json.loads(line) for line in path.read_text().splitlines()]) == [good]
