"""Persistence: state.json, activity log (JSON Lines), credentials, UI table settings.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import json
import os
from datetime import UTC, datetime

import data_paths
import events
import manager
import manager_core
import pytest
from conftest import OPTIONS, A
from credentials import Credentials
from events import EventLog
from helpers import chunks, firmware_zip


async def test_partial_downloads_removed_at_start(net):
    """Leftover .part files from a crash are deleted when the app starts."""
    fw = data_paths.FW_DIR
    fw.mkdir(parents=True)
    (fw / ".part-0123456789abcdef").write_bytes(b"half")
    (fw / ("a" * 64 + ".zip")).write_bytes(b"keep")
    manager.Manager(dict(OPTIONS), session=None, token="t")
    assert not (fw / ".part-0123456789abcdef").exists()
    assert (fw / ("a" * 64 + ".zip")).exists()


async def test_save_failure_keeps_previous_state(mgr, net, monkeypatch):
    """Write error (e.g. disk full): old state.json intact, error shown, check continues."""
    await mgr.scan()
    old = data_paths.STATE_FILE.read_text()
    backup = data_paths.STATE_FILE.with_suffix(".bak").read_text()
    real = os.fsync

    def failing(_fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", failing)
    assert await mgr.scan() is True
    assert data_paths.STATE_FILE.read_text() == old
    assert data_paths.STATE_FILE.with_suffix(".bak").read_text() == backup
    assert "No space left" in mgr.storage_error
    assert mgr.public_state()["storage_error"]
    assert not data_paths.STATE_FILE.with_suffix(".tmp").exists()
    monkeypatch.setattr(os, "fsync", real)
    assert mgr.save() and mgr.storage_error is None


async def test_state_file_compact_and_no_runtime_state(mgr, net):
    """state.json is compact JSON and does not persist activity/progress."""
    await mgr.scan()
    text = data_paths.STATE_FILE.read_text()
    assert "\n" not in text and "activity" not in text and "progress" not in json.loads(text)


async def test_restart_during_install_is_unconfirmed(net):
    """An installation interrupted by a restart is never shown as success."""
    data_paths.STATE_FILE.write_text(json.dumps({"devices": {A: {"name": "K", "status": "installing", "last_check": 5}}}))
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert m.devices[A]["status"] == manager_core.STATUS_UNCLEAR
    assert m.progress is None and m.activity == {}


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


# ---------------------------------------------------------------- Activity log storage


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
    """Entries of older versions get an ID at the first start (one rewrite);
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
    """A single entry is removed from memory and file; clearing removes all
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


# ---------------------------------------------------------------- State file: backup and recovery


def firmware_files():
    """Names of the files in the firmware store."""
    return sorted(p.name for p in data_paths.FW_DIR.iterdir())


async def test_every_save_keeps_the_previous_state_as_backup(mgr, net):
    """state.bak is the state of the save before the last one; no temporary file stays."""
    await mgr.scan()
    before = data_paths.STATE_FILE.read_text()
    mgr.devices[A]["name"] = "Renamed"
    assert mgr.save()
    assert data_paths.STATE_FILE.with_suffix(".bak").read_text() == before
    assert json.loads(data_paths.STATE_FILE.read_text())["devices"][A]["name"] == "Renamed"
    assert not data_paths.STATE_FILE.with_suffix(".tmp").exists()


@pytest.mark.parametrize("damage", ["{ truncated", "", "[]", '{"devices": [], "firmware": {}}', '{"firmware": {"x": 1}}'])
async def test_damaged_state_loads_the_backup_and_keeps_the_store(mgr, net, damage):
    """An unreadable state file – also one whose device list or store registry is
    not usable – is set aside; the backup is loaded and the firmware files stay."""
    await mgr.scan()
    mgr.save()  # the backup now holds the checked devices and the store registry
    files = firmware_files()
    assert files
    data_paths.STATE_FILE.write_text(damage)
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert set(m.devices) == set(mgr.devices) and set(m.firmware) == set(mgr.firmware)
    assert not m.first_start_open and not m.unknown_files
    assert "backup of the previous save was loaded" in m.public_state()["storage_error"]
    assert data_paths.STATE_FILE.with_suffix(".damaged").read_text() == damage
    m.cleanup_firmware()
    assert firmware_files() == files
    # The next save writes a state file again and leaves the good backup untouched
    backup = data_paths.STATE_FILE.with_suffix(".bak").read_text()
    assert m.save() and data_paths.STATE_FILE.with_suffix(".bak").read_text() == backup
    assert set(json.loads(data_paths.STATE_FILE.read_text())["firmware"]) == set(files)


async def test_missing_state_with_backup_is_no_fresh_installation(mgr, net):
    """Stopped between the two renames of a save: the backup is loaded, the
    first-start question is not asked."""
    await mgr.scan()
    mgr.save()
    data_paths.STATE_FILE.unlink()
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert set(m.devices) == set(mgr.devices) and not m.first_start_open
    assert m.public_state()["storage_error"]


async def test_no_readable_state_never_deletes_firmware(mgr, net):
    """State file and backup unreadable: the app starts without devices, but the
    firmware files – unknown to the empty registry – are kept and listed as unknown
    files (their content proves no firmware ID here), also after restarts."""
    await mgr.scan()
    mgr.save()
    files = firmware_files()
    data_paths.STATE_FILE.write_text("{ truncated")
    data_paths.STATE_FILE.with_suffix(".bak").write_text("")
    m = manager.Manager(dict(OPTIONS), session=None, token="t")
    await m.adopt_files()
    assert m.devices == {} and m.firmware == {} and set(m.unknown_files) == set(files) and not m.first_start_open
    assert "no backup could be loaded" in m.public_state()["storage_error"]
    m.cleanup_firmware()
    assert firmware_files() == files
    m._registry, m.notify, m.changelogs.refresh = mgr._registry, mgr.notify, net.refresh
    m._ha_setup_state = mgr._ha_setup_state
    m._battery_devices = mgr._battery_devices
    assert await m.scan() is True  # downloads the offered version again: listed once more
    assert firmware_files() == files and set(m.firmware) == set(files) and not m.unknown_files
    restarted = manager.Manager(dict(OPTIONS), session=None, token="t")
    assert restarted.public_state()["storage_error"] is None
    (data_paths.FW_DIR / ("b" * 64 + ".zip")).write_bytes(b"unlisted")
    restarted.cleanup_firmware()
    assert ("b" * 64 + ".zip") in firmware_files() and ("b" * 64 + ".zip") in restarted.unknown_files


async def test_unknown_files_are_kept_and_listed_when_no_download_runs(mgr, net):
    """Housekeeping: a file the registry does not know is never removed; it is listed
    as unknown file – but not while a check downloads, when a finished file may not be
    registered yet."""
    await mgr.scan()
    stray = data_paths.FW_DIR / ("c" * 64 + ".zip")
    stray.write_bytes(b"x")
    mgr._downloads_running = 1
    mgr.cleanup_firmware()
    assert stray.exists() and stray.name not in mgr.unknown_files
    mgr._downloads_running = 0
    mgr.cleanup_firmware()
    assert stray.exists() and mgr.unknown_files == {stray.name: 1}
    [entry] = [f for f in mgr.firmware_list() if f.get("unknown")]
    assert entry == {"name": stray.name, "unknown": True, "size": 1, "deletable": True, "locked": None}


def test_log_skips_entries_without_a_numeric_time(tmp_path):
    """A log line whose time is no number, and a file that is no text, do not stop
    the start; the usable entries are kept."""
    path = tmp_path / "events.json"
    now = int(datetime.now(UTC).timestamp())
    lines = [{"ts": now, "kind": "check", "ok": True}, {"ts": "yesterday", "kind": "check"}, {"ts": True, "kind": "check"},
             {"kind": "check"}, {"ts": now + 0.5, "kind": "upload", "ok": True}]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert [e["kind"] for e in EventLog(path, 90, 1000).entries] == ["check", "upload"]
    path.write_bytes(b"\xff\xfe\x00binary")
    assert EventLog(path, 90, 1000).entries == []


def test_credentials_write_error_is_reported(tmp_path):
    """A password file that cannot be written raises an error with a readable text
    (never silently); the previous file stays as it was."""
    from shelly import DeviceAuth, ShellyError

    path = tmp_path / "c.json"
    creds = Credentials(path)
    creds.store(A, DeviceAuth(gen=1, password="first"))
    saved = path.read_text()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    path.unlink()
    path.mkdir()  # a directory in its place: the rename fails
    with pytest.raises(ShellyError, match="could not be saved"):
        creds.store(A, DeviceAuth(gen=1, password="second"))
    assert not path.with_suffix(".tmp").exists() and "first" in saved


def test_log_mark_grows_within_one_second(tmp_path, monkeypatch):
    """The mark of the newest entry (Logs tab: reload and unread "!") grows with every
    entry, also for two entries written in the same second."""
    log = events.EventLog(tmp_path / "events.json")
    assert log.latest() == 0
    monkeypatch.setattr(events.time, "time", lambda: 1000.0)
    log.add("check")
    first = log.latest()
    log.add("check")
    assert log.latest() == first + 1 == 1000 * 1000 + 2
    monkeypatch.setattr(events.time, "time", lambda: 1001.0)
    log.add("check")
    assert log.latest() == 1001 * 1000 + 1
