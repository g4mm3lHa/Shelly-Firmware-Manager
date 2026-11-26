"""Backups of device settings: the encrypted files (backup_store), reading, comparing
and restoring on the device side (shelly_backup), the manager layer (manager_backups)
with installation, schedules, download, import and store export, and the API.

No connection is opened: the Shellys are in-memory fakes (FakeShelly, FakeGen1)."""

import asyncio
import json
import time

import backup_store
import manager_backups
import manager_core
import manager_installs
import pytest
import shelly
import shelly_backup
import store_transfer
from backup_store import BackupError, BackupStore
from conftest import A, B, C
from fake_shelly import SCRIPT, FakeGen1, FakeShelly
from helpers import chunks, make_manager, stage_import, web_client
from shelly import AuthError, ShellyError


def target_of(device, gen=2):
    """A shelly_backup.Target whose requests go to the fake device."""
    return shelly_backup.Target(None, gen, "http://192.168.1.10", None)


@pytest.fixture
def shellys(monkeypatch):
    """Fake Shellys by origin; the device requests of shelly_backup go to them."""
    devices = {}

    async def rpc(_session, origin, method, params=None, _auth=None):
        return await devices[origin].rpc(method, params)

    async def get(_session, origin, path, _auth=None, params=None):
        device = devices[origin]
        if path == "/shelly":
            if device.down:
                raise TimeoutError()
            return {}
        return await device.get(path, params)

    async def restart(_session, _gen, origin, _auth=None):
        devices[origin].restart()

    monkeypatch.setattr(shelly, "device_rpc", rpc)
    monkeypatch.setattr(shelly, "device_get", get)
    monkeypatch.setattr(shelly, "restart", restart)
    monkeypatch.setattr(shelly_backup, "RESTART_FIRST_S", 0)
    monkeypatch.setattr(shelly_backup, "RESTART_POLL_S", 0.01)
    monkeypatch.setattr(shelly_backup, "RESTART_WAIT_S", 0.2)
    return devices


@pytest.fixture
def one(shellys):
    """One Gen2+ fake at the address of the targets."""
    shellys["http://192.168.1.10"] = FakeShelly()
    return shellys["http://192.168.1.10"]


@pytest.fixture
async def backed(mgr, net, shellys):
    """A checked manager whose three devices are fake Shellys."""
    for device_id, (_name, ip, _v, _o) in net.devices.items():
        shellys[f"http://{ip}"] = FakeShelly("AABBCC00000" + device_id[-1].upper())
    await mgr.scan()
    return mgr


def manager_with(net, shellys, **options):
    """A manager with other options (like the fixture mgr) whose devices are fake Shellys."""
    m = make_manager(net, **options)

    async def registry():
        await asyncio.sleep(0)
        return net.registry()

    m._registry = registry
    m.changelogs.refresh = net.refresh
    for device_id, (_name, ip, _v, _o) in net.devices.items():
        shellys[f"http://{ip}"] = FakeShelly("AABBCC00000" + device_id[-1].upper())
    return m


def fake(shellys, net, device_id):
    return shellys[f"http://{net.devices[device_id][1]}"]


def backup_events(mgr, event=None):
    return [e for e in mgr.events.entries if e["kind"] == "backup" and (event is None or e["event"] == event)]


# ---------------------------------------------------------------- Files and list (backup_store)

META = {
    "id": "0123456789abcdef",
    "mac": "aa:bb:cc:00:00:0a",
    "gen": 2,
    "at": 1_800_000_000,
    "reason": "manual",
    "device": "Kitchen",
    "model": "SNSW-001X16EU",
    "fw": "1.4.0",
    "parts": {"scripts": 2, "nonsense": 9},
    "digest": "d" * 64,
    "extra": "dropped",
}


def test_sealed_file_roundtrip_and_refusals():
    """A file opens only with its key and unchanged; the content is not readable in it."""
    key = bytes(range(32))
    blob = backup_store.seal(key, {"mac": "AABBCC00000A"}, {"config": {"wifi": {"ssid": "Home-Network"}}})
    assert blob.startswith(backup_store.MAGIC) and b"Home-Network" not in blob and b"ssid" not in blob
    assert backup_store.open_sealed(key, blob, "wrong") == (
        {"mac": "AABBCC00000A"},
        {"config": {"wifi": {"ssid": "Home-Network"}}},
    )
    with pytest.raises(BackupError, match="wrong"):
        backup_store.open_sealed(bytes(32), blob, "wrong")
    for place in (-40, -1, 12):  # ciphertext, tag, header: any change is refused
        changed = bytearray(blob)
        changed[place] ^= 1
        with pytest.raises(BackupError):
            backup_store.open_sealed(key, bytes(changed), "wrong")
    for bad in (
        b"",
        b"PK\x03\x04" + bytes(100),
        backup_store.MAGIC + (9999).to_bytes(4, "big") + bytes(100),
        bytes(backup_store.MAX_FILE + 1),
    ):
        with pytest.raises(BackupError):
            backup_store.read_header(bad)
    other_version = blob.replace(b'"format_version":1', b'"format_version":2')
    with pytest.raises(BackupError, match="not supported"):
        backup_store.read_header(other_version)
    with pytest.raises(BackupError, match="not downloaded with a password"):
        backup_store.password_salt(backup_store.read_header(blob)[0])


def test_meta_is_reduced_to_known_fields():
    clean = backup_store.clean_meta(dict(META))
    assert clean["mac"] == "AABBCC00000A" and "extra" not in clean
    assert clean["parts"] == {"settings": True, "scripts": 2, "schedules": 0, "webhooks": 0}
    gen1 = backup_store.clean_meta({**META, "gen": 1, "parts": {"actions": 3, "partial": True}, "reason": "x"})
    assert gen1["parts"] == {"settings": True, "actions": 3, "partial": True} and gen1["reason"] == "manual"
    for bad in ({"mac": "nope"}, {"at": "now"}, {"gen": 0}):
        with pytest.raises(BackupError):
            backup_store.clean_meta({**META, **bad})


def test_imported_meta_gets_its_digest_from_the_content():
    """A file from outside cannot claim "unchanged" (digest of its own) or stay the
    newest backup for good (a time in the future)."""
    data = {"config": {"switch:0": {"name": "x"}}}
    past = {**META, "at": 1_700_000_000}
    clean = backup_store.clean_imported(past, data)
    assert clean["digest"] == shelly_backup.digest(2, data) != META["digest"]
    with pytest.raises(BackupError, match="future"):
        backup_store.clean_imported({**META, "at": int(time.time()) + 2 * backup_store.MAX_AHEAD_S}, data)
    with pytest.raises(BackupError, match="incomplete"):
        backup_store.clean_imported(past, {"scripts": ["not an object"]})


async def test_import_checks_digest_and_time_of_the_file(backed):
    """Both import ways use the digest of the content and refuse a future time."""
    mgr = backed
    salt = bytes(16)
    key = backup_store.derive_key("ABCD-EFGH", salt)
    data = {"config": {"switch:0": {"name": "x"}}}
    forged = backup_store.seal(key, {**META, "at": 1_700_000_000}, data, backup_store.password_header(salt))
    staged = await mgr.stage_backup_import(A, chunks(forged), len(forged))
    await mgr.check_backup_import(staged["token"], "ABCD-EFGH")
    entry = mgr.confirm_backup_import(staged["token"], "alice")
    assert mgr.backups.get(entry["id"])["digest"] == shelly_backup.digest(2, data)
    late = backup_store.seal(
        key, {**META, "id": "e" * 16, "at": int(time.time()) + 3 * 86400}, data, backup_store.password_header(salt)
    )
    staged = await mgr.stage_backup_import(A, chunks(late), len(late))
    with pytest.raises(BackupError, match="future"):
        await mgr.check_backup_import(staged["token"], "ABCD-EFGH")


def test_store_keeps_lists_and_prunes(tmp_path):
    """Files are private and encrypted; the newest N that are not kept stay; the list
    survives a restart and is built again from the files if it is lost."""
    store = BackupStore(tmp_path / "b")
    for n in range(5):
        store.add({**META, "id": f"{n:016x}", "at": META["at"] + n}, {"config": {"n": n}})
    assert [e["at"] - META["at"] for e in store.of("AA-BB-CC-00-00-0A")] == [4, 3, 2, 1, 0]
    assert (tmp_path / "b" / "key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "b" / f"{0:016x}.sfmb").stat().st_mode & 0o777 == 0o600
    store.set_kept(f"{0:016x}", True)
    removed = store.prune("AABBCC00000A", 2)
    assert sorted(e["at"] - META["at"] for e in removed) == [1, 2]
    assert [e["at"] - META["at"] for e in store.entries] == [4, 3, 0] and store.totals()["count"] == 3
    assert not (tmp_path / "b" / f"{1:016x}.sfmb").exists()
    with pytest.raises(BackupError, match="stored already"):
        store.add({**META, "id": f"{4:016x}"}, {})
    again = BackupStore(tmp_path / "b")
    assert again.entries == store.entries and again.load(f"{4:016x}")[1] == {"config": {"n": 4}}
    (tmp_path / "b" / "index.json").write_text("{broken")
    rebuilt = BackupStore(tmp_path / "b")
    assert [e["id"] for e in rebuilt.entries] == [e["id"] for e in store.entries]
    assert not any(e["kept"] for e in rebuilt.entries)  # the marks lived in the list only
    assert rebuilt.known({"id": "f" * 16, "mac": "AABBCC00000A", "at": META["at"] + 4, "digest": "d" * 64})
    assert not rebuilt.known({"id": "f" * 16, "mac": "AABBCC00000A", "at": 1, "digest": "d" * 64})
    with pytest.raises(BackupError):
        rebuilt.load("../../etc/passwd")


def test_many_backups_are_listed_with_one_write_of_the_list(tmp_path):
    """The store import writes the files first (worker thread), then lists all of them
    and writes the index once; an ID that is listed by then is skipped."""
    store = BackupStore(tmp_path / "b")
    store.add({**META, "id": "f" * 16}, {"config": {}})
    rev = store.rev
    items = [({**META, "id": f"{n:016x}", "at": META["at"] + n}, {"config": {"n": n}}, n == 1) for n in range(3)]
    written = store.write_many([*items, ({**META, "id": "f" * 16}, {"config": {}}, False)])
    assert len(store.entries) == 1 and len(written) == 4
    listed = store.list_many(written)
    assert len(listed) == 3 and store.rev == rev + 1 and all(e["imported"] for e in listed)
    assert [e["at"] - META["at"] for e in store.entries[:3]] == [2, 1, 0] and store.get(f"{1:016x}")["kept"]
    assert BackupStore(tmp_path / "b").entries == store.entries


def test_lost_key_is_reported_and_files_stay(tmp_path):
    store = BackupStore(tmp_path / "b")
    store.add(dict(META), {"config": {}})
    (tmp_path / "b" / "key").unlink()
    (tmp_path / "b" / "index.json").unlink()
    fresh = BackupStore(tmp_path / "b")
    assert fresh.entries == [] and "cannot be read" in fresh.warning
    assert (tmp_path / "b" / f"{META['id']}.sfmb").exists()


def test_listed_backups_the_key_cannot_open_are_not_listed(tmp_path):
    """A replaced key with the list intact: the warning names the file, it is not listed
    and stays; a key file of the wrong size is kept under another name, never overwritten."""
    folder = tmp_path / "b"
    store = BackupStore(folder)
    store.add(dict(META), {"config": {}}, kept=True)
    (folder / "key").write_bytes(bytes(32))
    replaced = BackupStore(folder)
    assert replaced.entries == [] and "1 backup file(s) cannot be read" in replaced.warning
    assert (folder / f"{META['id']}.sfmb").exists()
    replaced.add({**META, "id": "f" * 16}, {"config": {}})  # the index keeps the row of the hidden one
    original = store.key
    (folder / "key").write_bytes(original[:31])
    shortened = BackupStore(folder)
    damaged = list(folder.glob("key.damaged-*"))
    assert [p.read_bytes() for p in damaged] == [original[:31]] and len(shortened.key) == 32
    assert shortened.entries == [] and "cannot be read" in shortened.warning
    (folder / "key").write_bytes(original)  # the matching key is back: the file is listed again, still kept
    back = BackupStore(folder)
    assert [e["id"] for e in back.entries] == [META["id"]] and back.entries[0]["kept"]
    assert "1 backup file(s)" in back.warning  # now the one stored with the other key
    back.add({**META, "id": "f" * 16}, {"config": {}})  # imported again: a readable file replaces it
    assert len(json.loads((folder / "index.json").read_text())["backups"]) == 2 and BackupStore(folder).warning is None


def test_a_read_error_at_the_start_keeps_the_row_and_its_marks(tmp_path, monkeypatch):
    """A listed file that cannot be read at the start (disk error) is not listed but
    keeps its row: once it can be read again it is back with its mark "kept"."""
    folder = tmp_path / "b"
    store = BackupStore(folder)
    store.add(dict(META), {"config": {}}, kept=True)
    broken = folder / f"{META['id']}.sfmb"
    real_read = type(broken).read_bytes

    def failing_read(path):
        if path == broken:
            raise OSError(5, "Input/output error")
        return real_read(path)

    monkeypatch.setattr(type(broken), "read_bytes", failing_read)
    failed = BackupStore(folder)
    assert failed.entries == [] and "1 backup file(s)" in failed.warning
    assert [row["id"] for row in json.loads((folder / "index.json").read_text())["backups"]] == [META["id"]]
    monkeypatch.setattr(type(broken), "read_bytes", real_read)
    back = BackupStore(folder)
    assert [e["id"] for e in back.entries] == [META["id"]] and back.entries[0]["kept"] and back.warning is None


async def test_the_files_are_checked_after_the_start(tmp_path):
    """The tags of the listed files are checked after the start, in steps (the servers do not
    wait for it); a file the key does not fit leaves the list then, keeps its row and is
    counted in the warning; the first read of the list finishes the check before it answers."""
    folder = tmp_path / "b"
    store = BackupStore(folder)
    for i in range(3):
        store.add({**META, "id": f"{i:016x}", "at": META["at"] + i}, {"config": {"n": i}})
    (folder / f"{1:016x}.sfmb").write_bytes((folder / f"{1:016x}.sfmb").read_bytes()[:-1] + b"x")   # changed file
    started = BackupStore(folder)
    assert len(started._pending) == 3 and [e["id"] for e in started._entries] == [f"{i:016x}" for i in (2, 1, 0)]
    rev = started.rev
    started.verify_pending(1)   # one step: the newest file is fine
    assert len(started._pending) == 2 and len(started._entries) == 3 and started._warning is None
    await started.verify_in_background()
    assert not started._pending and [e["id"] for e in started._entries] == [f"{i:016x}" for i in (2, 0)]
    assert "1 backup file(s) cannot be read" in started._warning and started.rev == rev + 1
    assert [row["id"] for row in json.loads((folder / "index.json").read_text())["backups"]][-1] == f"{1:016x}"
    # Without the background step: any read of the list finishes the check first
    again = BackupStore(folder)
    assert [e["id"] for e in again.entries] == [f"{i:016x}" for i in (2, 0)] and not again._pending
    assert "1 backup file(s)" in again.warning


def test_an_unreadable_key_turns_the_backups_off(tmp_path):
    """A key file that cannot be read (here: a folder) is never replaced: no backup is
    listed, stored or read until the next start, and the list stays as it is."""
    folder = tmp_path / "b"
    BackupStore(folder).add(dict(META), {"config": {}})
    index = (folder / "index.json").read_bytes()
    (folder / "key").rename(folder / "saved")
    (folder / "key").mkdir()
    off = BackupStore(folder)
    assert off.key is None and off.entries == [] and off.warning == backup_store.KEY_UNREADABLE
    with pytest.raises(BackupError, match="backups are off"):
        off.add({**META, "id": "f" * 16}, {"config": {}})
    with pytest.raises(BackupError):
        off.load(META["id"])
    assert (folder / "index.json").read_bytes() == index and (folder / "key").is_dir()
    (folder / "key").rmdir()
    (folder / "saved").rename(folder / "key")
    assert [e["id"] for e in BackupStore(folder).entries] == [META["id"]]


async def test_a_schedule_stores_again_when_the_newest_backup_cannot_be_read(backed, net):
    """With the key replaced the newest backup is not listed, so a run that stores only
    changes stores a new backup instead of comparing with one that cannot be restored."""
    mgr = backed
    first = await mgr.backup_now(A, "alice")
    dev = mgr.devices[A]
    assert await mgr._backup(A, dev, "schedule", only_changed=True) is None  # unchanged
    (net.data / "backups" / "key").write_bytes(bytes(32))
    mgr.backups = BackupStore(net.data / "backups")
    assert mgr.backups_view()["warning"] and mgr.backups.get(first["id"]) is None
    entry = await mgr._backup(A, dev, "schedule", only_changed=True)
    assert entry is not None and mgr.backups.of("AABBCC00000A") == [entry]


def test_password_copy_opens_with_the_password_only(tmp_path):
    store = BackupStore(tmp_path / "b")
    store.add(dict(META), {"config": {"a": 1}}, kept=True)
    salt = bytes(16)
    key = backup_store.derive_key("ABCD-EFGH", salt)
    blob = store.sealed_copy(store.get(META["id"]), key, salt)
    header, _ = backup_store.read_header(blob)
    assert backup_store.password_salt(header) == salt
    meta, data = backup_store.open_sealed(key, blob, "wrong")
    assert data == {"config": {"a": 1}} and meta["kept"] is True and meta["id"] == META["id"]
    with pytest.raises(BackupError, match="wrong"):
        backup_store.open_sealed(store.key, blob, "wrong")


# ---------------------------------------------------------------- Fixed files of the format

# Files written by an earlier version of the app (format "SFMB1" of 3.11.0, format_version 1).
# They must stay readable: a change of the construction, the header or the key derivation
# fails here. FORMAT_KEY is the key of a stored file, FORMAT_PASSWORD the password of the
# downloaded file and of the store export that carries it.
FORMAT_KEY = bytes(range(32))

FORMAT_PASSWORD = "ABCD-EFGH-JKMN-PQRS-TUVW"

FORMAT_META = {
    "id": "0123456789abcdef", "mac": "AABBCC00000A", "gen": 2, "at": 1_800_000_000, "reason": "manual",
    "parts": {"settings": True, "scripts": 0, "schedules": 0, "webhooks": 0}, "digest": "d" * 64,
    "device": "Kitchen", "model": "SNSW-001X16EU", "type": None, "fw": "1.4.0", "note": None,
}

FORMAT_DATA = {"config": {"sys": {"device": {"name": "Kitchen"}}}}

STORED_FILE = bytes.fromhex(
    "53464d42310a000000697b22666f726d6174223a227368656c6c792d6669726d776172652d6d616e616765722f6261636b757022"
    "2c22666f726d61745f76657273696f6e223a312c226e6f6e6365223a223364353933663430663939656466613862353237343938"
    "386633396337663463227d9eeaec4be6f7a4b97dd48fc878da4b65cd514ea1a586e5f46bb53e48fcaba50e1ce1a2e672f56a8dad"
    "475558cd375449813da4e7b89a83d56dd7525e79ba5da7c3a12d849d4ac3c2497bc0d8743c01836f22dfb7202755413a6695a248"
    "7335fe74e8bcfc46c67b6e9897a84a5dab95830be1825267bd63a95f2ae5e9edaeeab727a03262a166d5bb69d51b05b71d4cec8b"
    "0b6f4c7ddf7c20a42fb8b82ce8e46326819529688a9036cc063af76870faf74d18e5a6f39381d82abbd1abf19fe448952e73cd76"
    "3705afa6add46333bc7d66f41f1a7cfbd70d4e8f554d8654021e5245e57b79937f0f6c16ffd4041c4de4f41470fd3d162519f976"
    "f37e75ad41217d"
)

DOWNLOADED_FILE = bytes.fromhex(
    "53464d42310a000000c17b22666f726d6174223a227368656c6c792d6669726d776172652d6d616e616765722f6261636b757022"
    "2c22666f726d61745f76657273696f6e223a312c226e6f6e6365223a223364343130613732393633363864393534663564643730"
    "643939356165623739222c226b6466223a7b226e616d65223a22736372797074222c226e223a33323736382c2272223a382c2270"
    "223a312c2273616c74223a223030313132323333343435353636373738383939616162626363646465656666227d7d2f32387d58"
    "6993884b65b01ce1f914bca7fbe2b6451d526002529f5327fa3300248e9d6186fbb0b6c8eceacacbd6c54eaf08454f4229009545"
    "16d589379442d49b74f741c747bba4187bf3994d87955221b4d6292a0f0b80b06905655d6d2b9a413fa05525fcde82f1e81f1c5a"
    "40aae35f15f203d8fd11c1a9b6a9283fda02bc89222f62ac45c506920707536b3faab93df3ef1d3685e6577fd37bbf67fad2c8f6"
    "39d91c6ddbbccc064d8b00cc545d615c1a903afc1752cecdf9084c252684c9ecd3435e7be276c2431b440aea5d9ac886c67143f3"
    "028c565ab8ef7361fd2477cdd475807e72285f87f0d21745aacfe8f2aeb1c3e4672853ab1f1c1fc792a28ef991c64453c2bfe36e"
    "179bacde93"
)

EXPORT_FILE = bytes.fromhex(
    "504b03041400000000006481775d06f995fdff010000ff0100000d0000006d616e69666573742e6a736f6e7b0a2022666f726d61"
    "74223a20227368656c6c792d6669726d776172652d6d616e616765722f73746f72652d6578706f7274222c0a2022666f726d6174"
    "5f76657273696f6e223a20312c0a20226170705f76657273696f6e223a2022332e31312e30222c0a20226578706f72746564223a"
    "20313739353435303236392c0a20226b6466223a207b0a2020226e616d65223a2022736372797074222c0a2020226e223a203332"
    "3736382c0a20202272223a20382c0a20202270223a20312c0a20202273616c74223a202230303131323233333434353536363737"
    "38383939616162626363646465656666220a207d2c0a2022656e7472696573223a205b5d2c0a20226261636b757073223a205b0a"
    "20207b0a2020202270617468223a20224261636b7570732f4141424243433030303030412f303132333435363738396162636465"
    "662e73666d62222c0a20202022736861323536223a20223966353432653764626132613134636435613437396161363464356663"
    "3361616230373964643265626137636235363234623230316338343365653961653536222c0a2020202273697a65223a20343733"
    "0a20207d0a205d2c0a20227365616c223a2022353735303332366232326463663038383931663066323863386565313465616535"
    "35303735373961623538633061636537633336346331656165633733343164220a7d504b03041400000000006481775dc277f34c"
    "6d0000006d0000000e00000053484132353653554d532e7478743966353432653764626132613134636435613437396161363464"
    "356663336161623037396464326562613763623536323462323031633834336565396165353620204261636b7570732f41414242"
    "43433030303030412f303132333435363738396162636465662e73666d620a504b03041400000000006481775dc06a73c1d90100"
    "00d90100002a0000004261636b7570732f4141424243433030303030412f303132333435363738396162636465662e73666d6253"
    "464d42310a000000c17b22666f726d6174223a227368656c6c792d6669726d776172652d6d616e616765722f6261636b7570222c"
    "22666f726d61745f76657273696f6e223a312c226e6f6e6365223a22336434313061373239363336386439353466356464373064"
    "3939356165623739222c226b6466223a7b226e616d65223a22736372797074222c226e223a33323736382c2272223a382c227022"
    "3a312c2273616c74223a223030313132323333343435353636373738383939616162626363646465656666227d7d2f32387d5869"
    "93884b65b01ce1f914bca7fbe2b6451d526002529f5327fa3300248e9d6186fbb0b6c8eceacacbd6c54eaf08454f422900954516"
    "d589379442d49b74f741c747bba4187bf3994d87955221b4d6292a0f0b80b06905655d6d2b9a413fa05525fcde82f1e81f1c5a40"
    "aae35f15f203d8fd11c1a9b6a9283fda02bc89222f62ac45c506920707536b3faab93df3ef1d3685e6577fd37bbf67fad2c8f639"
    "d91c6ddbbccc064d8b00cc545d615c1a903afc1752cecdf9084c252684c9ecd3435e7be276c2431b440aea5d9ac886c67143f302"
    "8c565ab8ef7361fd2477cdd475807e72285f87f0d21745aacfe8f2aeb1c3e4672853ab1f1c1fc792a28ef991c64453c2bfe36e17"
    "9bacde93504b010214031400000000006481775d06f995fdff010000ff0100000d00000000000000000000008001000000006d61"
    "6e69666573742e6a736f6e504b010214031400000000006481775dc277f34c6d0000006d0000000e000000000000000000000080"
    "012a02000053484132353653554d532e747874504b010214031400000000006481775dc06a73c1d9010000d90100002a00000000"
    "000000000000008001c30200004261636b7570732f4141424243433030303030412f303132333435363738396162636465662e73"
    "666d62504b05060000000003000300cf000000e40400000000"
)


def test_files_of_earlier_versions_stay_readable(tmp_path):
    """A stored file opens with the key of the app, a downloaded file and a store export
    with their password – byte for byte as an earlier version wrote them."""
    assert backup_store.open_sealed(FORMAT_KEY, STORED_FILE, "wrong") == (FORMAT_META, FORMAT_DATA)
    header, _offset = backup_store.read_header(DOWNLOADED_FILE)
    salt = backup_store.password_salt(header)
    key = backup_store.derive_key(FORMAT_PASSWORD, salt)
    meta, data = backup_store.open_sealed(key, DOWNLOADED_FILE, "wrong")
    assert meta == {**FORMAT_META, "kept": True, "exported": 1_800_000_100} and data == FORMAT_DATA
    path = tmp_path / "export.zip"
    path.write_bytes(EXPORT_FILE)
    checked = store_transfer.read_export(path, FORMAT_PASSWORD)
    assert checked["entries"] == [] and checked["key"] == key
    assert [b["id"] for b in checked["backups"]] == [FORMAT_META["id"]]
    content = store_transfer.read_backup(path, checked["prefix"], checked["backups"][0])
    assert content == DOWNLOADED_FILE
    with pytest.raises(store_transfer.TransferError, match="Wrong export password"):
        store_transfer.read_export(path, "ABCD-EFGH-JKMN-PQRS-TUVX")


# ---------------------------------------------------------------- Device side, Gen2+ (shelly_backup)


async def test_read_leaves_out_secrets_and_the_ha_script(one):
    """Passwords are removed, the Bluetooth proxy script of Home Assistant is no part,
    and code read in parts that cut a character is complete."""
    data = await shelly_backup.read(target_of(one))
    assert "pass" not in data["config"]["mqtt"] and "hunter2" not in json.dumps(data)
    assert [s["name"] for s in data["scripts"]] == ["auto-light.js"]
    assert data["scripts"][0]["code"] == SCRIPT and data["scripts"][0]["running"] is True
    assert len(data["schedules"]) == 1 and len(data["webhooks"]) == 1
    assert shelly_backup.summary(2, data) == {"settings": True, "scripts": 1, "schedules": 1, "webhooks": 1}


async def test_digest_ignores_what_changes_by_itself(one):
    before = await shelly_backup.read(target_of(one))
    one.config["sys"]["cfg_rev"] += 5
    one.config["sys"]["device"]["fw_id"] = "20260601-1/1.5.0"
    one.scripts[1]["running"] = False
    same = await shelly_backup.read(target_of(one))
    assert shelly_backup.digest(2, before) == shelly_backup.digest(2, same)
    one.config["switch:0"]["initial_state"] = "on"
    assert shelly_backup.digest(2, before) != shelly_backup.digest(2, await shelly_backup.read(target_of(one)))


async def test_compare_lists_differences_per_part(one):
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"].update(initial_state="restore_last", auto_off_delay=300.0)
    one.config["wifi"]["sta"].update(ssid="Guest", is_open=True)
    one.config["sys"]["device"]["profile"] = "cover"
    del one.config["input:0"]
    one.scripts[1]["code"] = SCRIPT + "// more\n"
    one.scripts[5] = {"name": "test.js", "enable": False, "running": False, "code": "x"}
    one.jobs[0]["timespec"] = "0 0 23 * * *"
    one.hooks.clear()
    diff = shelly_backup.compare(2, backup, await shelly_backup.read(target_of(one)))
    assert [(r["key"], r["device"], r["backup"]) for r in diff["settings"]["rows"]] == [
        ("switch:0 · initial_state", "restore_last", "off"),
        ("switch:0 · auto_off_delay", "300.0", "60.0"),
    ]
    assert diff["settings"]["skipped"] == [
        {"key": "sys · device.profile", "reason": "fixed"},
        {"key": "input:0 · type", "reason": "unknown"},
    ]
    assert shelly_backup.public_rows(diff["network"]["rows"]) == [
        {"key": "wifi · sta.ssid", "device": "Guest", "backup": "Home", "wifi_name": True}
    ]
    assert not any("wifi_name" in r for r in diff["settings"]["rows"])
    scripts = {r["key"]: r for r in shelly_backup.public_rows(diff["scripts"]["rows"])}
    assert scripts["auto-light.js"] == {
        "key": "auto-light.js",
        "state": "changed",
        "device_lines": 6,
        "backup_lines": 5,
        "autostart": True,
    }
    assert scripts["test.js"] == {"key": "test.js", "state": "extra", "device_lines": 1}
    assert [(r["state"], r["key"]) for r in diff["schedules"]["rows"]] == [("changed", "0 0 22 * * *")]
    assert [(r["state"], r["key"], r["backup"]) for r in diff["webhooks"]["rows"]] == [
        ("missing", "Motion", "input.toggle_on · 0")
    ]
    assert "item" not in shelly_backup.public_rows(diff["webhooks"]["rows"])[0]


async def test_restore_makes_the_chosen_parts_equal(one):
    """Settings, scripts, schedules and webhooks are written and read back; the network
    settings stay as they are unless named; a restart starts the scripts again."""
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"].update(initial_state="restore_last", auto_off_delay=300.0)
    one.config["wifi"]["sta"]["ssid"] = "Guest"
    one.scripts[1].update(code="broken", running=False)
    one.scripts[5] = {"name": "test.js", "enable": True, "running": True, "code": "x"}
    one.jobs.append({"id": 9, "enable": True, "timespec": "@sunset", "calls": []})
    one.hooks[0]["urls"] = ["http://192.168.1.9/x"]
    one.restart_for.add("switch:0")
    steps = []
    result = await shelly_backup.restore(
        target_of(one), backup, ["webhooks", "scripts", "settings", "schedules"], steps.append
    )
    assert steps == ["settings", "scripts", "schedules", "webhooks", "restart", "verify"]
    assert [(p["part"], p["total"], p["open"]) for p in result["parts"]] == [
        ("settings", 2, []),
        ("scripts", 2, []),
        ("schedules", 1, []),
        ("webhooks", 1, []),
    ]
    assert result["restart"] is True and result["confirmed"] and result["running"] == 1 and result["lost"] is None
    assert one.config["switch:0"]["initial_state"] == "off" and one.config["wifi"]["sta"]["ssid"] == "Guest"
    assert one.scripts[1]["code"] == SCRIPT and one.scripts[1]["running"] and 5 not in one.scripts
    assert shelly.BLE_PROXY_SCRIPT in [s["name"] for s in one.scripts.values()]  # never removed
    assert [j["id"] for j in one.jobs] == [1] and one.hooks[0]["urls"] == backup["webhooks"][0]["urls"]
    assert one.restarts == 1 and "WiFi.SetConfig" not in one.calls
    again = await shelly_backup.restore(target_of(one), backup, ["network"], steps.append)
    assert one.config["wifi"]["sta"]["ssid"] == "Home" and again["parts"][0]["total"] == 1
    assert "WiFi.SetConfig" in one.calls


async def test_refused_values_are_named_and_the_rest_is_written(one):
    """One refused value does not keep the others of its component from being
    restored; what the device does not report as in the backup stays open."""
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"].update(initial_state="on", auto_off=True, auto_off_delay=1.0)
    one.hooks.clear()
    one.refused |= {"auto_off", "input.toggle_on"}
    result = await shelly_backup.restore(target_of(one), backup, ["settings", "webhooks"], lambda _s: None)
    settings, webhooks = result["parts"]
    assert settings["total"] == 3 and [o["key"] for o in settings["open"]] == ["switch:0 · auto_off"]
    assert "invalid value" in settings["open"][0]["error"]
    assert one.config["switch:0"]["initial_state"] == "off" and one.config["switch:0"]["auto_off_delay"] == 60.0
    assert webhooks["open"][0]["key"] == "Motion" and "unknown event" in webhooks["open"][0]["error"]
    assert result["restart"] is None and result["confirmed"]


async def test_restore_skips_settings_a_newer_firmware_removed(one):
    """A backup made before an update that removed fields (ble.enable since Gen2+ 2.0.0):
    the preview and the restore skip them as unknown, write the rest and report no error."""
    one.config["ble"] = {"enable": True, "rpc": {"enable": True}, "observer": {"enable": False}}
    backup = await shelly_backup.read(target_of(one))
    one.config["ble"] = {"rpc": {"enable": False}}
    skipped = [
        {"key": "ble · enable", "reason": "unknown"},
        {"key": "ble · observer.enable", "reason": "unknown"},
    ]
    preview = shelly_backup.compare(2, backup, await shelly_backup.read(target_of(one)))
    assert preview["settings"]["skipped"] == skipped
    assert [row["key"] for row in preview["settings"]["rows"]] == ["ble · rpc.enable"]
    result = await shelly_backup.restore(target_of(one), backup, ["settings"], lambda _s: None)
    settings = result["parts"][0]
    assert (settings["total"], settings["open"], settings["skipped"]) == (1, [], skipped)
    assert one.config["ble"] == {"rpc": {"enable": True}} and result["confirmed"]


async def test_restore_reports_a_device_that_stops_answering(one):
    backup = await shelly_backup.read(target_of(one))
    one.config["wifi"]["sta"]["ssid"] = "Guest"
    real = one.rpc

    async def rpc(method, params=None):
        answer = await real(method, params)
        if method == "WiFi.SetConfig":
            one.down = True  # the device left for the other network
        return answer

    one.rpc = rpc
    result = await shelly_backup.restore(target_of(one), backup, ["network"], lambda _s: None)
    assert result["confirmed"] is False and result["lost"] == "TimeoutError"
    assert [o["key"] for o in result["parts"][0]["open"]] == ["wifi · sta.ssid"]
    one.down = False
    with pytest.raises(AuthError):
        one.rpc = lambda *_a: (_ for _ in ()).throw(AuthError("The Shelly rejected the password"))
        await shelly_backup.restore(target_of(one), backup, ["settings"], lambda _s: None)


def failing(device, method, error):
    """Let one RPC method of the fake raise error; every other method still answers."""
    real = device.rpc

    async def rpc(name, params=None):
        if name == method:
            raise error
        return await real(name, params)

    device.rpc = rpc


async def test_refused_script_is_named_and_the_rest_is_restored(one):
    """A script the device refuses to write is named under its key; the other scripts are
    still made equal to the backup and the restore goes on."""
    backup = await shelly_backup.read(target_of(one))
    one.scripts[1].update(code="broken")
    one.scripts[5] = {"name": "test.js", "enable": True, "running": True, "code": "x"}
    failing(one, "Script.PutCode", ShellyError("Device reports: out of memory"))
    result = await shelly_backup.restore(target_of(one), backup, ["scripts"], lambda _s: None)
    scripts = result["parts"][0]
    assert [o["key"] for o in scripts["open"]] == ["auto-light.js"]
    assert "out of memory" in scripts["open"][0]["error"]
    assert 5 not in one.scripts and result["lost"] is None


@pytest.mark.parametrize(
    ("part", "method"),
    [("settings", "Switch.SetConfig"), ("scripts", "Script.PutCode"), ("webhooks", "Webhook.Create")],
)
async def test_a_password_refused_while_writing_ends_the_restore(one, part, method):
    """A sign-in refusal of a write (after the read succeeded) is no refused value of one
    row: it ends the restore with the AuthError."""
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"]["initial_state"] = "on"
    one.scripts[1].update(code="broken")
    one.hooks.clear()
    failing(one, method, AuthError("The Shelly rejected the password"))
    with pytest.raises(AuthError):
        await shelly_backup.restore(target_of(one), backup, [part], lambda _s: None)


async def test_a_write_without_an_answer_is_a_lost_device(one):
    """A timeout while writing one row is no refusal of that row: the writing stops, the
    device is named as lost and the row stays open without a device answer."""
    backup = await shelly_backup.read(target_of(one))
    one.config["switch:0"]["initial_state"] = "on"
    one.hooks.clear()
    failing(one, "Switch.SetConfig", TimeoutError())
    result = await shelly_backup.restore(target_of(one), backup, ["settings", "webhooks"], lambda _s: None)
    assert result["lost"] == "TimeoutError" and "Webhook.Create" not in one.calls
    assert result["parts"][0]["open"] == [{"key": "switch:0 · initial_state", "error": None}]


async def test_new_scripts_are_created_in_small_parts(one):
    backup = await shelly_backup.read(target_of(one))
    long_code = "// ☀\n" + "let x = 'ä';\n" * 400
    backup["scripts"].append({"id": 7, "name": "long.js", "enable": False, "running": True, "code": long_code})
    result = await shelly_backup.restore(target_of(one), backup, ["scripts"], lambda _s: None)
    created = next(s for s in one.scripts.values() if s["name"] == "long.js")
    assert created["code"] == long_code and created["running"] and not created["enable"]
    assert one.calls.count("Script.PutCode") > 4 and result["running"] == 2 and result["parts"][0]["open"] == []


# ---------------------------------------------------------------- Device side, Gen1


async def test_gen1_backup_and_restore(shellys):
    """Gen1: /settings without secrets and state, the actions, and the endpoints that
    write them back; what the device ignores is named."""
    device = shellys["http://192.168.1.10"] = FakeGen1()
    target = target_of(device, gen=1)
    backup = await shelly_backup.read(target)
    assert "password" not in backup["settings"]["login"] and "key" not in backup["settings"]["wifi_ap"]
    assert shelly_backup.summary(1, backup) == {"settings": True, "actions": 0}
    first = shelly_backup.digest(1, backup)
    device.settings.update(time="12:01", unixtime=1700000060)
    device.settings["relays"][0]["ison"] = True
    assert shelly_backup.digest(1, await shelly_backup.read(target)) == first
    device.settings.update(led_status_disable=True, mode="roller")
    device.settings["relays"][0].update(default_state="last", auto_off=300.0, schedule_rules=[])
    device.settings["mqtt"]["enable"] = True
    device.settings["coiot"]["update_period"] = 60
    device.settings["sntp"]["enabled"] = False
    device.settings["wifi_sta"]["ssid"] = "Guest"
    device.settings["wifi_ap"]["enabled"] = True
    device.actions["out_on_url"][0] = {"index": 0, "urls": ["http://192.168.1.2/on"], "enabled": True}
    diff = shelly_backup.compare(1, backup, await shelly_backup.read(target))
    assert {r["key"] for r in diff["settings"]["rows"]} == {
        "led_status_disable",
        "relays.0 · default_state",
        "relays.0 · auto_off",
        "relays.0 · schedule_rules",
        "mqtt · enable",
        "coiot · update_period",
        "sntp · enabled",
    }
    assert diff["settings"]["skipped"] == [{"key": "mode", "reason": "fixed"}]
    assert [(r["key"], r.get("wifi_name")) for r in diff["network"]["rows"]] == [("wifi_sta · ssid", True)]
    assert [(r["key"], r["device"], r["backup"]) for r in diff["actions"]["rows"]] == [
        ("out_on_url (0)", "http://192.168.1.2/on", "–")
    ]
    device.calls.clear()
    result = await shelly_backup.restore(target, backup, ["settings", "actions"], lambda _s: None)
    settings, actions = result["parts"]
    assert [o["key"] for o in settings["open"]] == ["sntp · enabled"] and actions["open"] == []
    assert device.settings["relays"][0]["schedule_rules"] == ["0700-0123456-on"]
    assert device.settings["wifi_sta"]["ssid"] == "Guest" and device.settings["wifi_ap"]["enabled"] is True
    writes = {path: dict(pairs) for path, pairs in device.calls if pairs}
    assert writes["/settings/relay/0"] == {
        "default_state": "off",
        "auto_off": "0.0",
        "schedule_rules": "0700-0123456-on",
    }
    assert writes["/settings"]["mqtt_enable"] == "false" and writes["/settings"]["coiot_update_period"] == "15"
    assert ("urls[]", "") in next(pairs for path, pairs in device.calls if path == "/settings/actions" and pairs)
    assert result["restart"] is None and result["running"] is None


async def test_gen1_without_readable_actions_is_partial(shellys):
    device = shellys["http://192.168.1.10"] = FakeGen1(actions=False)
    backup = await shelly_backup.read(target_of(device, gen=1))
    assert backup["actions"] is None
    assert shelly_backup.summary(1, backup) == {"settings": True, "actions": 0, "partial": True}
    assert shelly_backup.compare(1, backup, backup)["actions"] == {"rows": [], "skipped": []}


# ---------------------------------------------------------------- Manager: storing


async def test_backup_now_stores_logs_and_applies_the_retention(net, shellys):
    """A manual backup is stored encrypted and logged; more than backup_keep are
    removed oldest first (logged with the user "App"); a kept one does not count."""
    mgr = manager_with(net, shellys, backup_keep=2)
    await mgr.scan()
    first = await mgr.backup_now(A, "alice")
    assert first["reason"] == "manual" and first["fw"] == "1.4.0" and first["size"] > 100
    assert first["parts"] == {"settings": True, "scripts": 1, "schedules": 1, "webhooks": 1}
    stored = backup_events(mgr, "stored")[-1]
    assert stored["device"] == "Kitchen" and stored["user"] == "alice" and stored["size"] == first["size"]
    raw = b"".join(p.read_bytes() for p in (net.data / "backups").glob("*.sfmb"))
    assert b"auto-light" not in raw and b"Kitchen" not in raw
    mgr.keep_backup(first["id"], True)
    for _ in range(3):
        await mgr.backup_now(A, "alice")
    entries = mgr.backups.of("AABBCC00000A")
    assert len(entries) == 3 and sum(e["kept"] for e in entries) == 1
    pruned = backup_events(mgr, "pruned")
    assert len(pruned) == 1 and pruned[0]["user"] == "App" and pruned[0]["keep"] == 2
    state = next(d for d in mgr.public_state()["devices"] if d["id"] == A)
    assert state["backups"]["count"] == 3 and state["backups"]["kept"] == 1
    assert mgr.public_state()["backup_totals"]["devices"] == 1
    view = mgr.backups_view()
    kitchen = next(d for d in view["devices"] if d["id"] == A)
    assert len(kitchen["backups"]) == 3 and kitchen["none"] is None and "digest" not in kitchen["backups"][0]
    assert next(d for d in view["devices"] if d["id"] == B)["none"] == {"reason": None}


async def test_failed_backups_name_the_reason(backed, net, shellys):
    """Not reachable, asleep (battery device), a missing password and a sign-in block are
    told apart; a removed device is not backed up; nothing is stored."""
    mgr = backed
    fake(shellys, net, A).down = True
    with pytest.raises(ShellyError, match="Device not reachable"):
        await mgr.backup_now(A, "alice")
    assert mgr.devices[A]["backup_failed"]["reason"] == "unreachable"
    assert backup_events(mgr, "failed")[-1]["severity"] == "error"  # it should answer
    mgr.devices[A]["battery"] = True
    with pytest.raises(ShellyError):
        await mgr.backup_now(A, "alice")
    assert next(d for d in mgr.backups_view()["devices"] if d["id"] == A)["none"]["reason"] == "asleep"
    assert backup_events(mgr, "failed")[-1]["severity"] == "warning"  # it sleeps
    fake(shellys, net, A).down = False

    async def locked(_method, _params=None):
        raise AuthError("The Shelly requires a password – store it in the device's password dialog")

    fake(shellys, net, B).rpc = locked
    with pytest.raises(ShellyError, match="requires a password"):
        await mgr.backup_now(B, "alice")
    assert mgr.devices[B]["backup_failed"]["reason"] == "password"
    assert backup_events(mgr, "failed")[-1]["severity"] == "error"

    async def blocked(_method, _params=None):
        raise shelly.AuthBlockedError("The Shelly temporarily blocks sign-ins (too many attempts) – wait a few "
                                      "minutes and try again")

    # A sign-in block is no password problem: noted as failed ("Back up now" stays usable)
    fake(shellys, net, B).rpc = blocked
    with pytest.raises(ShellyError, match="temporarily blocks sign-ins"):
        await mgr.backup_now(B, "alice")
    assert mgr.devices[B]["backup_failed"]["reason"] == "failed"
    assert backup_events(mgr, "failed")[-1]["severity"] == "error"
    mgr.devices[C]["removed"] = 1
    with pytest.raises(ShellyError, match="Removed from Home Assistant"):
        await mgr.backup_now(C, "alice")
    assert mgr.backups.entries == []
    await mgr.backup_now(A, "alice")
    assert "backup_failed" not in mgr.devices[A]


async def test_back_up_now_for_several_devices(backed, net, shellys):
    mgr = backed
    fake(shellys, net, B).down = True
    with pytest.raises(ValueError):
        mgr.reserve_backup_run([A, A])
    with pytest.raises(ValueError):
        mgr.reserve_backup_run(["f" * 32])
    ids = mgr.reserve_backup_run("all")
    with pytest.raises(ShellyError, match="in progress"):
        mgr.reserve_backup_run([A])
    assert sorted(ids) == [A, B, C] and mgr.public_state()["backup_job"]["total"] == 3
    await mgr.run_backups(ids, "alice")
    job = mgr.public_state()["backup_job"]
    assert job["finished"] and job["done"] == 3
    results = {r["id"]: r for r in job["results"]}
    assert results[A]["result"] == "stored" and results[A]["size"] and results[B]["result"] == "unreachable"
    assert "Device not reachable" in results[B]["detail"] and len(mgr.backups.entries) == 2
    mgr.clear_backup_run()
    assert mgr.backup_job is None
    # One log entry for the run names every device; none per device
    assert not backup_events(mgr, "stored") and not backup_events(mgr, "failed")
    (entry,) = backup_events(mgr, "run")
    assert entry["severity"] == "error" and entry["user"] == "alice" and entry["devices"] == 3
    assert entry["stored"] == 2 and entry["result"] == "2 stored · 1 not backed up"
    findings = {f["device"]: f for f in entry["findings"]}
    assert [f["device"] for f in entry["findings"]] == sorted(findings, key=str.casefold)
    kitchen = findings[mgr.devices[A]["name"]]
    assert kitchen["result"] == "stored" and kitchen["severity"] == "success" and kitchen["size"] == results[A]["size"]
    assert kitchen["parts"] == results[A]["parts"] and "id" not in kitchen and "detail" not in kitchen
    down = findings[mgr.devices[B]["name"]]
    assert down["result"] == "unreachable" and down["severity"] == "error" and "Device not reachable" in down["detail"]


async def test_backup_run_rates_never_checked_devices_like_their_check(backed):
    """A device without a successful check is not backed up: an error if its check
    rated it an error (e.g. it did not answer), a warning otherwise (a battery device
    asleep)."""
    mgr = backed
    for device_id in (B, C):
        mgr.devices[device_id].pop("origin")
    mgr.devices[B].update(status=manager_core.STATUS_ERROR, error_level="error")
    mgr.devices[C].update(status=manager_core.STATUS_ASLEEP, error_level="warning", battery=True)
    await mgr.run_backups(mgr.reserve_backup_run([A, B, C]), "alice")
    (entry,) = backup_events(mgr, "run")
    findings = {f["device"]: f for f in entry["findings"]}
    assert findings[mgr.devices[B]["name"]]["severity"] == "error"
    assert findings[mgr.devices[C]["name"]]["severity"] == "warning"
    assert "not been checked" in findings[mgr.devices[C]["name"]]["detail"] and entry["severity"] == "error"


async def test_backup_run_names_sleeping_devices_and_an_interruption(backed, net, shellys):
    """A battery device that does not answer is counted as asleep; a run the app stops
    is logged with what was done."""
    mgr = backed
    fake(shellys, net, B).down = True
    mgr.devices[B]["battery"] = True
    await mgr.run_backups(mgr.reserve_backup_run([A, B]), "alice")
    (entry,) = backup_events(mgr, "run")
    assert entry["result"] == "1 stored · 1 asleep" and entry["severity"] == "warning"
    assert {f["device"]: f["result"] for f in entry["findings"]} == {
        mgr.devices[A]["name"]: "stored",
        mgr.devices[B]["name"]: "asleep",
    }
    mgr.clear_backup_run()
    fake(shellys, net, B).down = False
    mgr.devices[B]["battery"] = False
    await mgr.run_backups(mgr.reserve_backup_run([A, B]), "alice")
    assert backup_events(mgr, "run")[-1]["severity"] == "success"
    mgr.clear_backup_run()

    async def stopped(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise asyncio.CancelledError

    mgr._backup_result = stopped
    with pytest.raises(asyncio.CancelledError):
        await mgr.run_backups(mgr.reserve_backup_run([A]), "alice")
    last = backup_events(mgr, "run")[-1]
    assert last["interrupted"] is True and last["severity"] == "warning" and mgr.backup_job["finished"]
    assert last["result"] == "0 stored · interrupted – the app stopped" and "findings" not in last


# ---------------------------------------------------------------- Manager: installation and schedules


async def test_backup_before_the_installation(net, shellys, monkeypatch, quick_install):
    """With the option on, an installation stores a backup first; if that fails,
    nothing is installed and the reason is shown and logged."""
    mgr = manager_with(net, shellys, backup_before_install=True)
    triggered = []

    async def trigger(_session, _gen, origin, url, _auth=None):
        triggered.append(origin)
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    await mgr.scan()
    assert mgr.public_state()["backup_before_install"] is True
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    entry = mgr.backups.of("AABBCC00000A")[0]
    assert entry["reason"] == "update" and entry["note"] == "1.4.0 → 1.5.0" and entry["fw"] == "1.4.0"
    kinds = [(e["kind"], e.get("event")) for e in mgr.events.entries if e["kind"] in ("backup", "install")]
    assert kinds == [("backup", "stored"), ("install", "started"), ("install", "confirmed"), ("backup", "compared")]
    fake(shellys, net, C).down = True
    assert await mgr.install(C, mgr.devices[C]["sha256"], "alice") is False
    assert triggered == ["http://192.168.1.10"]
    assert "Backup before the installation failed – nothing was installed" in mgr.devices[C]["message"]
    failed = mgr.events.entries[-1]
    assert (failed["kind"], failed["event"], failed["severity"], failed["reason"]) == (
        "backup",
        "failed",
        "error",
        "update",
    )
    assert mgr.devices[C]["install_failed"] is True  # the file stays provided for a retry


async def test_schedule_backup_stores_only_changes(backed, net, shellys):
    """A backup run stores what changed since the newest backup; a sleeping battery
    device is counted as asleep (not failed, not left out, the run stays a success); a
    device that does not answer makes the run an error; one log entry per run."""
    mgr = backed
    data = {
        "name": "Weekly backup",
        "action": "backup",
        "time": "03:30",
        "repeat": {"kind": "daily"},
        "devices": "all",
        "pause": 0,
    }
    item = mgr.create_schedule(data, "alice")
    mgr.devices[C].update(battery=True)
    fake(shellys, net, C).down = True
    mgr.start_due(item["next"])
    await asyncio.gather(*list(mgr._timetable_tasks))
    run = mgr.events.entries[-1]
    assert run["kind"] == "schedule" and run["action"] == "Backup"
    assert run["result"] == "2 stored · 0 unchanged · 1 asleep · 0 not backed up" and run["severity"] == "success"
    assert "left_out" not in run and mgr.schedules[0]["last"]["counts"]["asleep"] == 1
    assert {f["device"]: (f["result"], f["severity"]) for f in run["findings"]}["Garage"] == ("asleep", "warning")
    assert not backup_events(mgr) and not mgr.notified
    entry = mgr.backups.of("AABBCC00000A")[0]
    assert entry["reason"] == "schedule" and entry["note"] == "Weekly backup"
    fake(shellys, net, A).config["switch:0"]["initial_state"] = "on"
    fake(shellys, net, B).down = True
    mgr.start_due(mgr.schedules[0]["next"])
    await asyncio.gather(*list(mgr._timetable_tasks))
    run = mgr.events.entries[-1]
    assert run["result"] == "1 stored · 0 unchanged · 1 asleep · 1 not backed up" and run["severity"] == "error"
    assert mgr.schedules[0]["last"]["severity"] == "error"  # a device that should answer did not
    assert len(mgr.backups.of("AABBCC00000A")) == 2 and "Hall" in mgr.notified[-1][1]
    fake(shellys, net, B).down = False
    mgr.start_due(mgr.schedules[0]["next"])
    await asyncio.gather(*list(mgr._timetable_tasks))
    assert mgr.events.entries[-1]["result"] == "0 stored · 2 unchanged · 1 asleep · 0 not backed up"
    assert mgr.schedules[0]["last"]["counts"]["unchanged"] == 2 and len(mgr.backups.entries) == 3


# ---------------------------------------------------------------- Manager: restoring


async def test_preview_and_restore(backed, net, shellys):
    """The preview lists the differences without changing anything; the restore stores
    a backup "Before restore" first, writes the chosen parts and logs the result."""
    mgr = backed
    device = fake(shellys, net, A)
    backup = await mgr.backup_now(A, "alice")
    device.config["switch:0"]["initial_state"] = "on"
    device.config["wifi"]["sta"]["ssid"] = "Guest"
    device.scripts[1]["code"] = "changed"
    calls = len(device.calls)
    preview = await mgr.preview_restore(backup["id"])
    assert preview["reachable"] and preview["other"] is None and preview["fw_differs"] is False
    assert preview["protection"] == "same" and preview["device"]["name"] == "Kitchen"
    counts = {p["part"]: len(p["rows"]) for p in preview["parts"]}
    assert counts == {"settings": 1, "scripts": 1, "schedules": 0, "webhooks": 0, "network": 1}
    assert preview["parts"][0]["rows"] == [{"key": "switch:0 · initial_state", "device": "on", "backup": "off"}]
    assert preview["parts"][-1]["rows"] == [
        {"key": "wifi · sta.ssid", "device": "Guest", "backup": "Home", "wifi_name": True}
    ]
    assert not any("SetConfig" in c or "Put" in c for c in device.calls[calls:])
    with pytest.raises(ValueError, match="not confirmed"):
        mgr.reserve_restore(backup["id"], ["network"], False, "alice")
    with pytest.raises(ValueError):
        mgr.reserve_restore(backup["id"], ["actions"], False, "alice")
    mgr.reserve_restore(backup["id"], ["scripts", "settings"], False, "alice")
    state = mgr.public_state()
    assert state["restore"]["steps"] == ["backup", "settings", "scripts", "verify"] and "user" not in state["restore"]
    assert state["busy"] == "Restoring a backup"
    for blocked in (
        lambda: mgr.reserve_check("all"),
        lambda: mgr.reserve_check("device", A),
        lambda: mgr._settings_device(A),
        lambda: mgr.reserve_restore(backup["id"], ["settings"], False, "a"),
        lambda: mgr.delete_backup(backup["id"], True),
    ):
        with pytest.raises(ShellyError):
            blocked()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is False
    await mgr.run_restore()
    job = mgr.public_state()["restore"]
    assert job["finished"] and job["error"] is None and job["safety"]
    assert [(p["part"], p["total"], p["open"]) for p in job["result"]["parts"]] == [
        ("settings", 1, []),
        ("scripts", 1, []),
    ]
    assert device.config["switch:0"]["initial_state"] == "off" and device.config["wifi"]["sta"]["ssid"] == "Guest"
    newest = mgr.backups.of("AABBCC00000A")[0]
    assert newest["reason"] == "restore" and len(mgr.backups.entries) == 2
    entry = backup_events(mgr, "restored")[-1]
    assert entry["severity"] == "success" and entry["changes"] == 2 and entry["not_applied"] == 0
    assert entry["restored"] == ["settings", "scripts"] and entry["user"] == "alice"
    assert not mgr.restoring and mgr.public_state()["busy"] is None
    mgr.clear_restore()
    assert mgr.public_state()["restore"] is None
    mgr.reserve_restore(backup["id"], ["network"], True, "alice")
    await mgr.run_restore()
    assert device.config["wifi"]["sta"]["ssid"] == "Home"


async def test_restore_only_to_the_device_of_the_backup(backed, net, shellys, monkeypatch):
    """Another device at the address, a device that does not answer and a failing
    backup "Before restore": nothing is written."""
    mgr = backed
    device = fake(shellys, net, A)
    backup = await mgr.backup_now(A, "alice")
    device.config["switch:0"]["initial_state"] = "on"
    real_info = shelly.device_info

    async def other_info(*args, **kwargs):
        return {**await real_info(*args, **kwargs), "mac": "AABBCC0000FF"}

    monkeypatch.setattr(shelly, "device_info", other_info)
    preview = await mgr.preview_restore(backup["id"])
    assert preview["other"] == {
        "backup_mac": "AABBCC00000A",
        "backup_model": "SNSW-001X16EU",
        "device_mac": "AABBCC0000FF",
        "device_model": "SNSW-001X16EU",
    }
    assert "parts" not in preview
    with pytest.raises(ShellyError, match="Another device answers at the address – no backup was stored"):
        await mgr.backup_now(A, "alice")
    assert [e["id"] for e in mgr.backups.entries] == [backup["id"]]
    mgr.reserve_restore(backup["id"], ["settings"], False, "alice")
    await mgr.run_restore()
    assert "Another device" in mgr.restore_job["error"] and device.config["switch:0"]["initial_state"] == "on"
    assert backup_events(mgr, "restore_failed")[-1]["severity"] == "error" and not mgr.restoring
    monkeypatch.setattr(shelly, "device_info", real_info)
    device.down = True
    net.asleep.add(A)
    preview = await mgr.preview_restore(backup["id"])
    assert preview["reachable"] is False and "Device not reachable" in preview["error"]
    net.asleep.clear()
    mgr.reserve_restore(backup["id"], ["settings"], False, "alice")
    await mgr.run_restore()
    assert "backup before the restore failed – nothing was restored" in mgr.restore_job["error"]
    device.down = False
    mgr.devices.pop(A)
    with pytest.raises(ShellyError, match="no longer listed"):
        await mgr.preview_restore(backup["id"])
    orphan = mgr.backups_view()["devices"][-1]
    assert orphan["id"] is None and orphan["name"] == "Kitchen" and len(orphan["backups"]) == 1


async def test_restore_of_the_oldest_backup_keeps_it(net, shellys):
    """The retention after the backup "Before restore" leaves the backup being restored;
    it still counts and goes with the next backup of the device."""
    mgr = manager_with(net, shellys, backup_keep=2)
    await mgr.scan()
    fake(shellys, net, A)
    oldest = await mgr.backup_now(A, "alice")
    await mgr.backup_now(A, "alice")
    mgr.reserve_restore(oldest["id"], ["settings"], False, "alice")
    await mgr.run_restore()
    assert mgr.restore_job["error"] is None
    entries = mgr.backups.of("AABBCC00000A")
    assert len(entries) == 3 and entries[0]["reason"] == "restore" and entries[-1]["id"] == oldest["id"]
    assert backup_events(mgr, "pruned") == []
    await mgr.backup_now(A, "alice")
    assert oldest["id"] not in [e["id"] for e in mgr.backups.of("AABBCC00000A")]
    assert len(mgr.backups.of("AABBCC00000A")) == 2 and len(backup_events(mgr, "pruned")) == 2


# ---------------------------------------------------------------- Manager: download, import, export


async def test_download_and_import_roundtrip(backed, net, shellys):
    """A downloaded file opens only with its password; the import checks the device
    and whether the backup is stored already, and marks it as imported and kept."""
    mgr = backed
    backup = await mgr.backup_now(A, "alice")
    prepared = await mgr.prepare_backup_download(backup["id"], "alice")
    assert prepared["filename"].startswith("backup-plus1-") and prepared["filename"].endswith(".sfmb")
    with pytest.raises(ShellyError, match="expired"):
        mgr.take_backup_download("0" * 32)  # an outdated tab: refused, the download stays
    content, filename = mgr.take_backup_download(prepared["token"])
    assert filename == prepared["filename"] and b"auto-light" not in content
    assert backup_events(mgr, "downloaded")[-1]["user"] == "alice"
    with pytest.raises(ShellyError, match="expired"):
        mgr.take_backup_download(prepared["token"])
    with pytest.raises(BackupError, match="no backup of this app"):
        await mgr.stage_backup_import(A, chunks(b"PK\x03\x04" + bytes(200)), 204)
    with pytest.raises(ValueError, match="too large"):
        await mgr.stage_backup_import(A, chunks(bytes(10)), backup_store.MAX_FILE + 1)
    stored_file = next((net.data / "backups").glob("*.sfmb")).read_bytes()
    with pytest.raises(BackupError, match="not downloaded with a password"):
        await mgr.stage_backup_import(A, chunks(stored_file), len(stored_file))
    staged = await mgr.stage_backup_import(A, chunks(content), len(content))
    with pytest.raises(ShellyError, match="no longer available"):
        await mgr.check_backup_import("0" * 32, prepared["password"])  # refused, the file stays
    with pytest.raises(BackupError, match="Wrong password"):
        await mgr.check_backup_import(staged["token"], "AAAA-BBBB-CCCC-DDDD-EEEE")
    checked = await mgr.check_backup_import(staged["token"], prepared["password"].lower())
    assert checked["known"] is True and checked["other"] is None
    with pytest.raises(ShellyError, match="cannot be imported"):
        mgr.confirm_backup_import(staged["token"], "alice")
    staged = await mgr.stage_backup_import(B, chunks(content), len(content))
    checked = await mgr.check_backup_import(staged["token"], prepared["password"])
    assert checked["other"]["backup_mac"] == "AABBCC00000A" and checked["other"]["device_mac"] == "AABBCC00000B"
    mgr.delete_backup(backup["id"], True, "alice")
    assert backup_events(mgr, "deleted") and mgr.backups.entries == []
    staged = await mgr.stage_backup_import(A, chunks(content), len(content))
    checked = await mgr.check_backup_import(staged["token"], prepared["password"])
    assert checked == {
        "backup": {**backup, "size": len(content), "kept": True, "imported": True},
        "other": None,
        "known": False,
    }
    entry = mgr.confirm_backup_import(staged["token"], "bob")
    assert entry["imported"] and entry["kept"] and entry["at"] == backup["at"] and entry["id"] == backup["id"]
    assert backup_events(mgr, "imported")[-1]["user"] == "bob"
    with pytest.raises(ShellyError):
        mgr.confirm_backup_import(staged["token"], "bob")
    device = fake(shellys, net, A)
    device.config["switch:0"]["initial_state"] = "on"
    mgr.reserve_restore(entry["id"], ["settings"], False, "bob")
    await mgr.run_restore()
    assert device.config["switch:0"]["initial_state"] == "off"


async def test_store_export_carries_the_backups(backed, net, shellys, tmp_path):
    """The store export holds the backups encrypted with the key of the export
    password; the import stores the ones that are missing (the "Kept" mark comes along)."""
    mgr = backed
    first = await mgr.backup_now(A, "alice")
    second = await mgr.backup_now(B, "alice")
    mgr.keep_backup(first["id"], True)
    prepared = await mgr.prepare_export("alice")
    assert prepared["backups"] == 2
    path, _name = await mgr.build_export(prepared["token"], "3.11.0")
    raw = path.read_bytes()
    assert b"Backups/AABBCC00000A/" in raw and b"auto-light" not in raw
    assert mgr.events.entries[-1]["kind"] == "export" and mgr.events.entries[-1]["backups"] == 2
    mgr.delete_backup(first["id"], True, "alice")
    staged = await stage_import(mgr, chunks(raw), len(raw))
    path.unlink()
    summary = await mgr.check_import(staged["token"], prepared["password"])
    assert summary["backups"] == 2 and summary["backups_new"] == 1
    counts = await mgr.confirm_import(staged["token"], "bob")
    assert counts["backups"] == 1 and mgr.events.entries[-1]["backups"] == 1
    restored = mgr.backups.get(first["id"])
    assert restored["kept"] and restored["imported"] and restored["at"] == first["at"]
    assert not mgr.backups.get(second["id"])["imported"]
    assert mgr.backups.load(first["id"])[1]["scripts"][0]["code"] == SCRIPT
    checked = store_transfer.read_export
    with pytest.raises(store_transfer.TransferError):
        target = tmp_path / "x.zip"
        target.write_bytes(raw.replace(b"Backups/AABBCC00000A/", b"Backups/AABBCC00000X/"))
        checked(target, store_transfer.normalize_password(prepared["password"]))


# ---------------------------------------------------------------- API


async def test_http_backups(backed, net, shellys):
    """The API of the backups: lists, backup, keep, download, import, preview, restore
    and delete – with the CSRF and confirmation rules of the other write requests."""
    mgr = backed
    c = await web_client(mgr)
    hdr = {"X-CSRF-Token": c.csrf}
    try:
        assert (await c.post(f"/api/devices/{A}/backup", json={})).status == 403
        r = await c.post(f"/api/devices/{A}/backup", json={}, headers=hdr)
        backup = (await r.json())["backup"]
        assert r.status == 200 and backup["reason"] == "manual"
        listed = await (await c.get("/api/backups")).json()
        kitchen = next(d for d in listed["devices"] if d["id"] == A)
        assert listed["keep"] == 10 and kitchen["backups"][0]["id"] == backup["id"]
        assert "AABBCC" not in json.dumps(listed)
        bid = backup["id"]
        assert (await c.post(f"/api/backups/{'0' * 16}/keep", json={"kept": True}, headers=hdr)).status == 404
        assert (await c.post("/api/backups/nope/delete", json={"confirm": True}, headers=hdr)).status == 404
        assert (await c.post(f"/api/backups/{bid}/keep", json={"kept": "yes"}, headers=hdr)).status == 400
        r = await c.post(f"/api/backups/{bid}/keep", json={"kept": True}, headers=hdr)
        assert (await r.json())["backup"]["kept"] is True
        r = await c.post(f"/api/backups/{bid}/download", json={}, headers=hdr)
        prepared = await r.json()
        assert set(prepared) == {"token", "password", "filename", "size", "expires_in"}
        r = await c.get(f"/api/backups/download/{prepared['token']}")
        content = await r.read()
        assert r.status == 200 and prepared["filename"] in r.headers["Content-Disposition"]
        assert r.headers["Content-Security-Policy"] and len(content) == prepared["size"]
        assert (await c.get(f"/api/backups/download/{prepared['token']}")).status == 409
        assert (await c.get("/api/backups/download/x")).status == 404
        binary = {**hdr, "Content-Type": "application/octet-stream"}
        assert (await c.post(f"/api/devices/{A}/backups/import", json={}, headers=hdr)).status == 415
        r = await c.post(f"/api/devices/{A}/backups/import", data=b"junk" * 50, headers=binary)
        assert r.status == 400 and "no backup of this app" in await r.text()
        r = await c.post(f"/api/devices/{A}/backups/import", data=content, headers=binary)
        token = (await r.json())["token"]
        r = await c.post("/api/backups/import/check", json={"token": token, "password": "WRONG"}, headers=hdr)
        assert r.status == 400 and "Wrong password" in await r.text()
        r = await c.post(
            "/api/backups/import/check", json={"token": token, "password": prepared["password"]}, headers=hdr
        )
        assert (await r.json())["known"] is True
        assert (await c.post("/api/backups/import/confirm", json={"token": token}, headers=hdr)).status == 409
        assert (await c.post("/api/backups/import/cancel", json={"token": token}, headers=hdr)).status == 200
        fake(shellys, net, A).config["switch:0"]["initial_state"] = "on"
        r = await c.post(f"/api/backups/{bid}/preview", json={}, headers=hdr)
        assert [len(p["rows"]) for p in (await r.json())["parts"]] == [1, 0, 0, 0, 0]
        body = {"parts": ["settings"]}
        r = await c.post(f"/api/backups/{bid}/restore", json=body, headers=hdr)
        assert r.status == 400 and await r.text() == "Confirmation incomplete"
        r = await c.post(f"/api/backups/{bid}/restore", json={"parts": ["network"], "confirm": True}, headers=hdr)
        assert r.status == 400 and "not confirmed" in await r.text()
        r = await c.post(f"/api/backups/{bid}/restore", json={**body, "confirm": True}, headers=hdr)
        assert (await r.json()) == {"started": True}
        for _ in range(200):
            await asyncio.sleep(0.01)
            if mgr.restore_job["finished"]:
                break
        state = await (await c.get("/api/state")).json()
        assert state["restore"]["result"]["parts"][0]["open"] == [] and state["backups_rev"] >= 2
        assert (await c.post("/api/backups/restore/clear", json={}, headers=hdr)).status == 200
        r = await c.post("/api/backups/run", json={"devices": [B, C]}, headers=hdr)
        assert (await r.json()) == {"started": True, "total": 2}
        for _ in range(200):
            await asyncio.sleep(0.01)
            if mgr.backup_job["finished"]:
                break
        assert (await c.post("/api/backups/run", json={"devices": []}, headers=hdr)).status == 400
        assert (await c.post("/api/backups/run/clear", json={}, headers=hdr)).status == 200
        assert (await c.post(f"/api/backups/{bid}/delete", json={}, headers=hdr)).status == 400
        assert (await c.post(f"/api/backups/{bid}/delete", json={"confirm": True}, headers=hdr)).status == 200
        assert (await c.post(f"/api/backups/{bid}/preview", json={}, headers=hdr)).status == 404
    finally:
        await c.close()


# ---------------------------------------------------------------- Log mark and check severity


async def test_error_mark_of_the_log_and_unreachable_devices(mgr, net):
    """The mark of the newest error entry (the red dot of the Logs tab) moves only with an
    error; a device that should answer and does not is an error of the check, a
    sleeping battery device a warning."""
    assert mgr.events.latest("error") == 0
    await mgr.scan()
    assert mgr.events.latest() > 0 and mgr.public_state()["log_latest_error"] == 0
    net.asleep.update({A, C})
    mgr.devices[C]["battery"] = True
    await mgr.scan()
    findings = {f["device"]: f["severity"] for f in mgr.events.entries[-1]["findings"]}
    assert findings["Kitchen"] == "error" and findings["Garage"] == "warning"
    assert mgr.events.entries[-1]["severity"] == "error" and mgr.devices[A]["error_level"] == "error"
    mark = mgr.events.latest("error")
    assert mark == mgr.events.latest() == mgr.public_state()["log_latest_error"]
    mgr.events.add("setting", device="x")
    assert mgr.events.latest("error") == mark < mgr.events.latest()
    assert manager_backups.NONE_ASLEEP == "asleep"


# ---------------------------------------------------------------- Comparison after an update


def test_after_update_splits_changed_and_new_settings():
    """Changed values, settings the backup did not have and settings the firmware removed
    are listed apart; counters such as sys.cfg_rev are never compared; scripts that ran
    before and stopped are named."""
    before = FakeShelly()
    now = FakeShelly()
    now.config["switch:0"]["initial_state"] = "restore_last"
    now.config["switch:0"]["reverse"] = False  # new field of the firmware
    now.config["sys"]["cfg_rev"] = 9
    del now.config["mqtt"]["server"]
    data_before = {"config": before.config, "scripts": [{"id": 1, "name": "a", "running": True}, {"id": 2, "name": "b", "running": True}]}
    data_now = {"config": now.config, "scripts": [{"id": 1, "name": "a", "running": True}, {"id": 2, "name": "b", "running": False}]}
    result = shelly_backup.after_update(2, data_before, data_now)
    assert result["changed"] == [{"key": "switch:0 · initial_state", "before": "off", "now": "restore_last"}]
    assert result["new"] == [{"key": "switch:0 · reverse", "value": "false"}]
    # mqtt is still reported, only its field is not: the firmware removed it
    assert result["gone"] == [{"key": "mqtt · server", "before": "–"}]
    assert (result["changed_count"], result["new_count"], result["gone_count"]) == (1, 1, 1)
    assert result["scripts"] == {"before": 2, "again": 1, "stopped": ["b"]}


def test_after_update_names_fields_the_firmware_removed():
    """A field missing after the update while its component is still reported counts as
    removed by the firmware (ble.enable and ble.observer since Gen2+ 1.5.0/2.0.0, access of
    virtual components) and not as a change; a whole missing component does."""
    before = FakeShelly()
    before.config["ble"] = {"enable": True, "rpc": {"enable": True}, "observer": {"enable": False}}
    before.config["boolean:200"] = {"id": 200, "name": "x", "access": "*"}
    now = FakeShelly()
    now.config["ble"] = {"rpc": {"enable": True}}
    now.config["boolean:200"] = {"id": 200, "name": "x"}
    del now.config["input:0"]
    result = shelly_backup.after_update(2, {"config": before.config}, {"config": now.config})
    assert result["gone"] == [
        {"key": "ble · enable", "before": "true"},
        {"key": "ble · observer.enable", "before": "false"},
        {"key": "boolean:200 · access", "before": "*"},
    ]
    assert result["changed"] == [
        {"key": "input:0 · id", "before": "0", "now": "–"},
        {"key": "input:0 · type", "before": "switch", "now": "–"},
    ]
    assert (result["changed_count"], result["new_count"], result["gone_count"]) == (2, 0, 3)


def test_after_update_of_a_gen1_device():
    """Gen1: the settings are compared, there are no scripts. A field missing in a channel
    or in /settings that is still reported was removed by the firmware; a missing channel
    is a change."""
    before = {
        "settings": {"relays": [{"default_state": "off", "auto_on": 0}, {"default_state": "off"}],
                     "mode": "relay", "led_status_disable": False},
        "actions": None,
    }  # fmt: skip
    now = {"settings": {"relays": [{"default_state": "on", "schedule": False}], "mode": "relay"}, "actions": None}
    result = shelly_backup.after_update(1, before, now)
    assert result["changed"] == [
        {"key": "relays.0 · default_state", "before": "off", "now": "on"},
        {"key": "relays.1 · default_state", "before": "off", "now": "–"},
    ]
    assert result["new"] == [{"key": "relays.0 · schedule", "value": "false"}]
    assert result["gone"] == [
        {"key": "led_status_disable", "before": "false"},
        {"key": "relays.0 · auto_on", "before": "0"},
    ]
    assert result["scripts"] == {"before": 0, "again": 0, "stopped": []}


def test_after_update_of_a_gen1_device_that_dropped_an_object():
    """Gen1: a whole object of /settings the device no longer reports (here mqtt) while it still
    reports /settings was removed by the firmware – removed fields, no change (only a channel that
    is no longer reported counts as changed)."""
    before = {"settings": {"mqtt": {"enable": False, "keep_alive": 60}, "mode": "relay"}, "actions": None}
    now = {"settings": {"mode": "relay"}, "actions": None}
    result = shelly_backup.after_update(1, before, now)
    assert result["changed"] == [] and result["changed_count"] == 0
    assert result["gone"] == [{"key": "mqtt · enable", "before": "false"}, {"key": "mqtt · keep_alive", "before": "60"}]


async def test_comparison_of_an_update_that_only_removed_fields(net, shellys, monkeypatch, quick_install):
    """Fields the firmware removed are kept and counted, but raise no warning; the log
    names neither them nor their values."""
    mgr = manager_with(net, shellys, backup_before_install=True)
    fake(shellys, net, A).config["mqtt"]["user"] = "user-x7"

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")
        shellys[origin].config["mqtt"].pop("user")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    await mgr.scan()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    after = mgr.devices[A]["after_update"]
    assert after["gone"] == [{"key": "mqtt · user", "before": "user-x7"}] and after["gone_count"] == 1
    assert after["changed"] == [] and after["changed_count"] == 0
    entry = backup_events(mgr, "compared")[-1]
    assert (entry["severity"], entry["gone_fields"], entry["changed"]) == ("success", 1, 0)
    assert "changed_keys" not in entry and "user-x7" not in json.dumps(entry)


async def test_comparison_after_a_confirmed_update(net, shellys, monkeypatch, quick_install):
    """A confirmed update with a backup right before it: the device is read again and the
    result is kept on the device (state, log) until its next check; without the option
    there is no comparison."""
    mgr = manager_with(net, shellys, backup_before_install=True)

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")
        fake_device = shellys[origin]
        fake_device.config["switch:0"]["initial_state"] = "on"
        fake_device.config["switch:0"]["reverse"] = False
        fake_device.scripts[1]["running"] = False

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    await mgr.scan()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    after = mgr.devices[A]["after_update"]
    assert after["changed"] == [{"key": "switch:0 · initial_state", "before": "off", "now": "on"}]
    assert after["new"] == [{"key": "switch:0 · reverse", "value": "false"}]
    assert after["scripts"] == {"before": 1, "again": 0, "stopped": ["auto-light.js"]}
    assert after["from_version"] == "1.4.0" and after["to_version"] == mgr.devices[A]["version"] and after["backup_at"]
    view = next(d for d in mgr.public_state()["devices"] if d["id"] == A)
    assert view["after_update"]["changed_count"] == 1
    entry = backup_events(mgr, "compared")[-1]
    assert (entry["severity"], entry["changed"], entry["scripts_stopped"]) == ("warning", 1, ["auto-light.js"])
    # The log names the changed settings only, never their values
    assert entry["changed_keys"] == ["switch:0 · initial_state"]
    assert "changed_rows" not in entry and "off" not in json.dumps(entry)
    # "Seen" removes the line below the firmware; the comparison stays until the next check
    assert mgr.after_update_seen(A) is True and mgr.devices[A]["after_update"]["seen"] is True
    assert mgr.after_update_seen(B) is False
    # The check after an intermediate version keeps the comparison, any other check ends it
    await mgr.check_device(A, trigger="step")
    assert mgr.devices[A]["after_update"]["changed_count"] == 1
    await mgr.check_device(A)
    assert "after_update" not in mgr.devices[A]
    plain_mgr = manager_with(net, shellys, backup_before_install=False)
    await plain_mgr.scan()
    assert await plain_mgr.install(C, plain_mgr.devices[C]["sha256"], "alice") is True
    assert "after_update" not in plain_mgr.devices[C]


async def test_comparison_that_cannot_read_the_device(net, shellys, monkeypatch, quick_install):
    """The device does not answer after the update: the result names the error."""
    mgr = manager_with(net, shellys, backup_before_install=True)
    compare = mgr.compare_after_update

    async def down_first(device_id, backup_id, user):
        fake(shellys, net, device_id).down = True
        await compare(device_id, backup_id, user)

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    mgr.compare_after_update = down_first
    await mgr.scan()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    assert "error" in mgr.devices[A]["after_update"]
    assert backup_events(mgr, "compared")[-1]["severity"] == "warning"


async def test_no_comparison_after_a_refused_sign_in(net, shellys, monkeypatch, quick_install):
    """The device refused or blocked a sign-in after the update command: no further signed
    request in this action, so the comparison is not made and names that reason instead."""
    mgr = manager_with(net, shellys, backup_before_install=True)
    compared = []

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")
        mgr._update_step(A, signin_refused=True)  # as a refused read of the running time does

    async def compare(*args):
        compared.append(args)

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    mgr.compare_after_update = compare
    await mgr.scan()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    assert not compared
    assert mgr.devices[A]["after_update"]["error"] == manager_installs.SIGNIN_REFUSED_COMPARISON
    assert backup_events(mgr, "compared")[-1]["severity"] == "warning"


async def test_comparison_with_an_unexpected_error(net, shellys, monkeypatch, quick_install):
    """An unexpected error in the comparison: the update stays confirmed (install returns
    True, the check after an intermediate version still runs) and the error is kept."""
    mgr = manager_with(net, shellys, backup_before_install=True)

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")

    def broken(*_args):
        raise TypeError("odd answer")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    monkeypatch.setattr(shelly_backup, "after_update", broken)
    await mgr.scan()
    assert await mgr.install(A, mgr.devices[A]["sha256"], "alice") is True
    assert "Unexpected error (TypeError" in mgr.devices[A]["after_update"]["error"]
    assert backup_events(mgr, "compared")[-1]["severity"] == "warning"


async def test_comparison_skipped_when_a_new_installation_starts(net, shellys, monkeypatch, quick_install):
    """A new installation of the device starts while the comparison reads it: the old
    comparison writes nothing (no result, no log entry)."""
    mgr = manager_with(net, shellys, backup_before_install=True)
    read = mgr._read_backup

    async def read_then_install(device_id, dev):
        result = await read(device_id, dev)
        mgr.installing.add(device_id)  # the user starts the next installation meanwhile
        return result

    async def trigger(_session, _gen, origin, url, _auth=None):
        device = net.by_origin(origin)
        net.devices[device] = (*net.devices[device][:2], "1.5.0", "1.5.0")

    monkeypatch.setattr(shelly, "trigger_update", trigger)
    await mgr.scan()
    ok = mgr.install(A, mgr.devices[A]["sha256"], "alice")
    mgr._read_backup = read_then_install
    assert await ok is True
    assert "after_update" not in mgr.devices[A]
    assert backup_events(mgr, "compared") == []
