"""Firmware store export/import: seal and password, unchanged files, re-packed
archives, new / already stored / conflicting entries, limits, first-start question.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet); no network is used."""

import hashlib
import io
import json
import zipfile

import data_paths
import manager_core
import manager_store
import pytest
import store_transfer
from conftest import A
from helpers import chunks, store
from shelly import ShellyError
from store_transfer import TransferError


async def export_bytes(mgr):
    """Export the store; returns (ZIP bytes, password)."""
    prepared = await mgr.prepare_export("tester")
    path, filename = await mgr.build_export(prepared["token"], "3.4.0")
    data = path.read_bytes()
    path.unlink()
    assert filename.startswith("shelly-firmware-store-") and filename.endswith(".zip")
    return data, prepared["password"]


async def stage(mgr, data):
    """Stage an export file like the upload route."""
    return (await mgr.stage_import(chunks(data), len(data)))["token"]


def forget_store(mgr):
    """Empty the store (files and entries), like a fresh installation."""
    for name in list(mgr.firmware):
        (data_paths.FW_DIR / name).unlink(missing_ok=True)
    mgr.firmware.clear()


def rewrite(data, change):
    """Re-pack an export; change(name, bytes) returns the new bytes of a member."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info.filename, change(info.filename, src.read(info.filename)))
    return out.getvalue()


async def test_export_layout_and_roundtrip_keeps_date_and_marks_files_imported(mgr, net):
    """Readable layout with manifest and checksums (the source in the manifest and the
    file name); an import into an empty store restores every file with its date and
    changelog, but as unverified imported file (source import, no HTTPS marker): the
    seal does not prove who made the export."""
    down, _ = store(mgr, "Plus1", "1.3.0")
    up, _ = store(mgr, "Plus1", "1.2.0", source=manager_core.SOURCE_UPLOAD)
    gen1, _ = store(mgr, "SHSW-1", "1.13.0", gen=1)
    imp, _ = store(mgr, "Plus1", "1.1.0", source=manager_core.SOURCE_IMPORT)
    mgr.firmware[down]["downloaded"] = 1735787045
    mgr.firmware[down]["changelog"] = {"date": "2025-01-01", "entries": [{"text": "Fix", "models": ["Plus1"]}]}
    before = {n: dict(m) for n, m in mgr.firmware.items()}
    data, password = await export_bytes(mgr)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
    assert {"manifest.json", "SHA256SUMS.txt"} <= names
    assert "Gen2+/Plus1/Plus1-1.3.0.zip" in names
    assert "Gen2+/Plus1/Plus1-1.2.0-upload.zip" in names
    assert "Gen2+/Plus1/Plus1-1.1.0-import.zip" in names
    assert any(n.startswith("Gen1/SHSW-1/SHSW-1-v1.13.0") for n in names)
    assert all(not n.startswith(manager_store.EXPORT_PREFIX) for n in os_listdir())

    forget_store(mgr)
    token = await stage(mgr, data)
    summary = await mgr.check_import(token, password.lower())  # case and spaces do not matter
    assert summary["files"] == 4 and len(summary["new"]) == 4 and not summary["same"] and not summary["conflict"]
    assert {e["version"]: e["source"] for e in summary["new"]} == {
        "1.3.0": "download", "1.2.0": "upload", "1.13.0": "download", "1.1.0": "import"
    }
    assert not mgr.firmware  # nothing stored before the confirmation
    counts = await mgr.confirm_import(token, "tester")
    assert counts["stored"] == 4 and counts["conflicts"] == 0
    for name in (down, up, gen1, imp):
        meta = mgr.firmware[name]
        assert (data_paths.FW_DIR / name).is_file()
        for key in ("type", "gen", "version", "sha256", "size", "downloaded"):
            assert meta.get(key) == before[name].get(key), key
        assert meta["source"] == manager_core.SOURCE_IMPORT and meta["https"] is None and "sha_checked" not in meta
    assert mgr.firmware[down]["changelog"]["entries"][0]["text"] == "Fix"
    assert [e for e in mgr.events.list() if e["kind"] == "import"][0]["user"] == "tester"
    assert not any(n.startswith((manager_store.IMPORT_PREFIX, manager_store.EXPORT_PREFIX)) for n in os_listdir())


def os_listdir():
    return [p.name for p in data_paths.FW_DIR.iterdir()]


async def test_repacked_archive_without_changes_is_accepted(mgr, net):
    """Unpacked and packed again (other compression): accepted, files unchanged."""
    store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    token = await stage(mgr, rewrite(data, lambda _n, b: b))
    assert len((await mgr.check_import(token, password))["new"]) == 1


async def test_changed_file_or_manifest_or_password_is_refused(mgr, net):
    """A changed firmware file, a changed manifest or a wrong password: refused
    before anything is stored."""
    store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    forget_store(mgr)

    def flip(name, content):
        return content[:-1] + bytes([content[-1] ^ 1]) if name.endswith("1.3.0.zip") else content

    token = await stage(mgr, rewrite(data, flip))
    with pytest.raises(TransferError, match="changed since the export"):
        await mgr.check_import(token, password)

    def edit_manifest(name, content):
        if name != "manifest.json":
            return content
        body = json.loads(content)
        body["entries"][0]["source"] = "upload" if body["entries"][0]["source"] == "download" else "download"
        return json.dumps(body).encode()

    token = await stage(mgr, rewrite(data, edit_manifest))
    with pytest.raises(TransferError, match="Wrong export password"):
        await mgr.check_import(token, password)

    token = await stage(mgr, data)
    with pytest.raises(TransferError, match="Wrong export password"):
        await mgr.check_import(token, "AAAA-AAAA-AAAA-AAAA-AAAA")
    with pytest.raises(TransferError, match="Enter the export password"):
        await mgr.check_import(token, "  ")
    with pytest.raises(ShellyError, match="not been checked"):
        await mgr.confirm_import(token)
    assert not mgr.firmware


async def test_same_and_conflicting_files(mgr, net):
    """Same SHA-256: not stored twice, missing data is added, the source stays (an
    upload never becomes a download). Same firmware ID and version with another file:
    the stored one is kept."""
    keep, _ = store(mgr, "Plus1", "1.3.0")
    other, _ = store(mgr, "Plus1", "1.4.0")
    mgr.firmware[keep]["changelog"] = {"date": "2025-01-01", "entries": [{"text": "Fix", "models": []}]}
    data, password = await export_bytes(mgr)

    # Receiving store: 1.3.0 as an upload without changelog, 1.4.0 as a different file
    forget_store(mgr)
    store(mgr, "Plus1", "1.3.0", source=manager_core.SOURCE_UPLOAD)
    mgr.firmware[keep]["sha_checked"] = True
    conflict, _ = store(mgr, "Plus1", "1.4.0", content=b"another file" * 500)
    token = await stage(mgr, data)
    summary = await mgr.check_import(token, password)
    assert not summary["new"]
    assert [(e["version"], e["note"]) for e in summary["same"]] == [
        ("1.3.0", "changelog added")
    ]
    assert [e["version"] for e in summary["conflict"]] == ["1.4.0"]
    counts = await mgr.confirm_import(token)
    assert counts == {"stored": 0, "updated": 1, "same": 1, "conflicts": 1, "backups": 0}
    assert mgr.firmware[keep]["source"] == manager_core.SOURCE_UPLOAD and mgr.firmware[keep]["sha_checked"] is True
    assert mgr.firmware[keep]["changelog"]["entries"][0]["text"] == "Fix"
    assert other not in mgr.firmware and conflict in mgr.firmware  # stored file kept
    event = [e for e in mgr.events.list() if e["kind"] == "import"][0]
    assert event["severity"] == "warning" and event["conflicts_list"] == ["Plus1 1.4.0"]


async def test_sealed_foreign_file_never_replaces_the_manufacturer_file(mgr, net):
    """A correctly sealed export of a stranger (own password, own file labelled as a
    download over HTTPS of the version the server will offer) – also at the first
    start: the file is stored as unverified import, the check downloads the
    manufacturer's file the offer URL names, and the import is not offered."""
    foreign, foreign_sha = store(mgr, "Plus1", "1.5.0", content=b"foreign firmware" * 5000)
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    mgr.first_start_open = True
    token = await stage(mgr, data)
    summary = await mgr.check_import(token, password)
    assert summary["new"][0]["source"] == "download"  # what the creator claims
    assert (await mgr.confirm_import(token))["stored"] == 1 and not mgr.first_start_open
    assert mgr.firmware[foreign]["source"] == manager_core.SOURCE_IMPORT and mgr.firmware[foreign]["https"] is None
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    dev = mgr.devices[A]
    assert net.downloads == 1 and dev["sha256"] != foreign_sha and dev["offer_source"] == "manufacturer"
    assert mgr.firmware[foreign]["source"] == manager_core.SOURCE_IMPORT  # still unverified


async def test_import_never_turns_an_upload_into_a_download(mgr, net):
    """An export listing the SHA-256 of a stored upload as a download changes nothing
    of the upload's source or SHA-256 marker."""
    name, _ = store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    mgr.firmware[name].update(source=manager_core.SOURCE_UPLOAD, https=None, sha_checked=False)
    token = await stage(mgr, data)
    summary = await mgr.check_import(token, password)
    assert summary["same"][0]["note"] == ""
    assert (await mgr.confirm_import(token))["updated"] == 0
    assert mgr.firmware[name]["source"] == manager_core.SOURCE_UPLOAD and mgr.firmware[name]["sha_checked"] is False
    assert mgr.firmware_list()[0]["source"] == "upload" and mgr.firmware_list()[0]["https"] is None


async def test_import_adds_data_only_to_the_same_firmware_id_and_version(mgr, net):
    """Same SHA-256 but another ID or version in the export: counted as already stored,
    nothing is taken over (no foreign changelog or marker for a genuine file)."""
    name, _ = store(mgr, "Plus1", "1.3.0")
    mgr.firmware[name].update(changelog={"date": "2025-01-01", "entries": [{"text": "Foreign", "models": []}]},
                              intermediate=True)
    data, password = await export_bytes(mgr)
    mgr.firmware[name] = {**mgr.firmware[name], "version": "1.3.1"}  # receiving store: other version
    mgr.firmware[name].pop("changelog")
    mgr.firmware[name].pop("intermediate")
    token = await stage(mgr, data)
    summary = await mgr.check_import(token, password)
    assert len(summary["same"]) == 1 and summary["same"][0]["note"] == ""
    assert (await mgr.confirm_import(token))["updated"] == 0
    assert "changelog" not in mgr.firmware[name] and "intermediate" not in mgr.firmware[name]


async def test_confirmed_import_drops_its_changelog(mgr, net):
    """An imported file confirmed by the offer URL loses the changelog of the import:
    the one of the Shelly documentation is requested instead."""
    from conftest import vendor_content

    name, _ = store(mgr, "Plus1", "1.5.0", source=manager_core.SOURCE_IMPORT, content=vendor_content("1.5.0"))
    mgr.firmware[name]["changelog"] = {"date": "2025-01-01", "entries": [{"text": "Foreign", "models": []}]}
    mgr.devices[A] = {"name": "Kitchen"}
    await mgr.check_device(A)
    assert mgr.firmware[name]["source"] == manager_core.SOURCE_DOWNLOAD and "changelog" not in mgr.firmware[name]


def test_seal_that_is_no_hex_text_is_a_wrong_password(tmp_path):
    """A seal with other characters (e.g. non-ASCII) is refused like a wrong one."""
    archive = tmp_path / "x.zip"
    body = {
        "format": store_transfer.FORMAT, "format_version": store_transfer.FORMAT_VERSION, "app_version": "1",
        "exported": 1, "kdf": {"name": "scrypt", **store_transfer.SCRYPT, "salt": "0" * 32}, "entries": [],
    }
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("manifest.json", json.dumps({**body, "seal": "ü" * 64}))
    with pytest.raises(TransferError, match="Wrong export password"):
        store_transfer.read_export(archive, "AAAA")


async def test_gen1_is_imported_even_when_gen1_is_off(mgr, net):
    """Gen1 files are imported independent of the Gen1 options."""
    store(mgr, "SHSW-25", "1.14.0", gen=1)
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    mgr.gen1_enabled = False
    token = await stage(mgr, data)
    await mgr.check_import(token, password)
    assert (await mgr.confirm_import(token))["stored"] == 1


async def test_damaged_files_are_left_out_of_the_export(mgr, net):
    """A stored file that no longer matches its SHA-256 is not exported (warning)."""
    good, _ = store(mgr, "Plus1", "1.3.0")
    bad, _ = store(mgr, "Plus1", "1.2.0")
    (data_paths.FW_DIR / bad).write_bytes(b"damaged")
    data, password = await export_bytes(mgr)
    event = [e for e in mgr.events.list() if e["kind"] == "export"][0]
    assert event["damaged"] == 1 and event["files"] == 1 and event["severity"] == "warning"
    forget_store(mgr)
    token = await stage(mgr, data)
    assert [e["version"] for e in (await mgr.check_import(token, password))["new"]] == ["1.3.0"]


async def test_export_token_is_single_use_and_expires(mgr, net, monkeypatch):
    """The download works once; an expired or unknown token is refused; an empty
    store has nothing to export."""
    with pytest.raises(ShellyError, match="empty"):
        await mgr.prepare_export()
    store(mgr, "Plus1", "1.3.0")
    prepared = await mgr.prepare_export()
    path, _ = await mgr.build_export(prepared["token"], "3.4.0")
    path.unlink()
    with pytest.raises(ShellyError, match="expired"):
        await mgr.build_export(prepared["token"], "3.4.0")
    prepared = await mgr.prepare_export()
    monkeypatch.setattr(manager_store, "EXPORT_TTL_S", -1)
    with pytest.raises(ShellyError, match="expired"):
        await mgr.build_export(prepared["token"], "3.4.0")


async def test_import_limits(mgr, net, monkeypatch):
    """Size limit of the option, announced size, no ZIP, free disk space, token."""
    mgr.import_max = 1000
    with pytest.raises(ValueError, match="maximum import size"):
        await mgr.stage_import(chunks(b"x" * 1001), 1001)
    with pytest.raises(ValueError, match="larger than announced"):
        await mgr.stage_import(chunks(b"x" * 600), 500)
    with pytest.raises(ValueError, match="no ZIP"):
        await mgr.stage_import(chunks(b"x" * 500), 500)
    with pytest.raises(ValueError, match="Empty"):
        await mgr.stage_import(chunks(b""), 0)
    monkeypatch.setattr(type(mgr), "_free_space", lambda _self: 0)
    with pytest.raises(ShellyError, match="free disk space"):
        await mgr.stage_import(chunks(b"x" * 500), 500)
    with pytest.raises(ShellyError, match="No pending import"):
        await mgr.check_import("0" * 32, "x")
    assert not any(n.startswith(manager_store.IMPORT_PREFIX) for n in os_listdir())


async def test_no_zip_export_or_foreign_zip(mgr, net):
    """A ZIP without manifest is refused with a clear message."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("a.txt", "x")
    token = await stage(mgr, buf.getvalue())
    with pytest.raises(TransferError, match="manifest.json missing"):
        await mgr.check_import(token, "AAAA")


async def test_import_refused_during_a_check(mgr, net, monkeypatch):
    """The store must not change while a check runs."""
    store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    token = await stage(mgr, data)
    await mgr.check_import(token, password)
    monkeypatch.setattr(type(mgr), "checking", property(lambda self: getattr(self, "busy_for_test", False)))
    mgr.busy_for_test = True
    with pytest.raises(ShellyError, match="during a check"):
        await mgr.confirm_import(token)
    mgr.busy_for_test = False
    real = type(mgr)._free_space
    monkeypatch.setattr(type(mgr), "_free_space", lambda _self: 0)
    with pytest.raises(ShellyError, match="free disk space"):
        await mgr.confirm_import(token)
    monkeypatch.setattr(type(mgr), "_free_space", real)
    assert (await mgr.confirm_import(token))["stored"] == 1


async def test_first_start_question(mgr, net):
    """While open: checks are blocked, one notification; answered by Skip or by an
    import, and kept in state.json."""
    mgr.first_start_open = True
    for args in (("all",), ("device", A)):
        with pytest.raises(ShellyError, match="first-start question"):
            mgr.reserve_check(*args)
    await mgr.announce_first_start()
    await mgr.announce_first_start()
    assert [a[2] for a in mgr.notified] == ["shelly_firmware_manager_first_start"]
    mgr.skip_first_start("tester")
    assert not mgr.first_start_open and mgr.schedule_changed.is_set()
    saved = json.loads(data_paths.STATE_FILE.read_text())
    assert saved["first_start_open"] is False and saved["first_start_notified"] is True
    assert [e for e in mgr.events.list() if e["kind"] == "setup"][0]["action"] == "skipped"

    store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    mgr.first_start_open = True
    mgr.schedule_changed.clear()
    token = await stage(mgr, data)
    await mgr.check_import(token, password)
    await mgr.confirm_import(token)
    assert not mgr.first_start_open and mgr.schedule_changed.is_set()


def test_password_format_and_normalization():
    """Generated: 5 groups of 4 characters; spaces and case do not matter."""
    password = store_transfer.generate_password()
    assert len(password.split("-")) == 5 and all(len(p) == 4 for p in password.split("-"))
    assert store_transfer.normalize_password(f"  {password.lower()} ") == password


async def send_parts(stage, data, size, **kwargs):
    """Send data in parts of size through stage(chunks, length, total, offset,
    transfer, ...); returns the answers of all parts."""
    answers, transfer = [], None
    for offset in range(0, len(data), size):
        part = data[offset : offset + size]
        answer = await stage(chunks(part), len(part), len(data), offset, transfer, **kwargs)
        transfer = answer.get("transfer")
        answers.append(answer)
    return answers


async def test_import_in_parts(mgr, net, monkeypatch):
    """Files above the Ingress body limit arrive in parts: the import continues at the
    received size and is staged after the last part."""
    monkeypatch.setattr(manager_store, "PART_MAX", 4096)
    store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    answers = await send_parts(mgr.stage_import_part, data, 4096)
    assert len(answers) > 2 and answers[0]["received"] == 4096 and "token" not in answers[-2]
    token = answers[-1]["token"]
    assert len((await mgr.check_import(token, password))["new"]) == 1
    assert not mgr._receiving


async def test_upload_in_parts(mgr, net, monkeypatch):
    """A firmware upload in parts gets the same SHA-256 and manifest data as in one piece."""
    from helpers import firmware_zip

    monkeypatch.setattr(manager_store, "PART_MAX", 8192)
    await mgr.scan()
    data = firmware_zip({"name": "Plus1", "version": "1.3.0"}, size=30000)

    async def stage(chunks_, length, total, offset, transfer):
        return await mgr.stage_upload_part(chunks_, "fw.zip", length, total, offset, transfer, "u")

    answers = await send_parts(stage, data, 8192)
    final = answers[-1]
    assert final["sha256"] == hashlib.sha256(data).hexdigest() and final["detected"]["version"] == "1.3.0"


async def test_parts_out_of_order_or_unknown(mgr, net, monkeypatch):
    """A part must name the transfer and continue exactly at the received size; any
    error drops the transfer and its temporary file."""
    monkeypatch.setattr(manager_store, "PART_MAX", 100)
    data = b"x" * 300
    first = await mgr.stage_import_part(chunks(data[:100]), 100, 300, 0, None)
    with pytest.raises(ShellyError, match="interrupted"):
        await mgr.stage_import_part(chunks(data[100:200]), 100, 300, 100, "0" * 32)
    with pytest.raises(ShellyError, match="interrupted"):
        await mgr.stage_import_part(chunks(data[100:200]), 100, 400, 100, first["transfer"])
    with pytest.raises(ShellyError, match="out of order"):
        await mgr.stage_import_part(chunks(data[200:]), 100, 300, 200, first["transfer"])
    assert not mgr._receiving and not any(n.startswith(manager_store.IMPORT_PREFIX) for n in os_listdir())
    with pytest.raises(ValueError, match="Invalid part"):
        await mgr.stage_import_part(chunks(b"x" * 101), 101, 300, 0, None)
    first = await mgr.stage_import_part(chunks(data[:100]), 100, 300, 0, None)
    monkeypatch.setattr(manager_store, "PART_TTL_S", -1)
    with pytest.raises(ShellyError, match="interrupted"):
        await mgr.stage_import_part(chunks(data[100:200]), 100, 300, 100, first["transfer"])
    assert not any(n.startswith(manager_store.IMPORT_PREFIX) for n in os_listdir())


async def test_no_check_starts_while_an_import_writes_its_files(mgr, net):
    """The store must not change during a check – so no check may start while an
    import is storing its files."""
    mgr._import_storing = True
    for args in (("all",), ("device", A)):
        with pytest.raises(ShellyError, match="Store import in progress"):
            mgr.reserve_check(*args)
    mgr._import_storing = False
    mgr.reserve_check("all")


async def test_import_releases_the_check_block(mgr, net):
    """After an import – successful or refused – checks can start again."""
    store(mgr, "Plus1", "1.3.0")
    data, password = await export_bytes(mgr)
    forget_store(mgr)
    token = (await mgr.stage_import(chunks(data), len(data)))["token"]
    with pytest.raises(ShellyError, match="not been checked"):
        await mgr.confirm_import(token)
    assert mgr._import_storing is False
    await mgr.check_import(token, password)
    assert (await mgr.confirm_import(token))["stored"] == 1 and mgr._import_storing is False


async def test_export_is_refused_where_the_dialog_can_show_it(mgr, net, monkeypatch):
    """Too little free disk space is reported when the export is prepared; the
    answer names how long the download stays possible."""
    store(mgr, "Plus1", "1.3.0")
    prepared = await mgr.prepare_export()
    assert prepared["expires_in"] == manager_store.EXPORT_TTL_S
    monkeypatch.setattr(mgr, "_free_space", lambda: 0)
    with pytest.raises(ShellyError, match="Not enough free disk space"):
        await mgr.prepare_export()


async def test_first_start_notification_is_retried_until_accepted(mgr, net):
    """A notification Home Assistant did not accept does not count as sent."""
    mgr.first_start_open = True
    accepted = []

    async def notify(*_args):
        accepted.append(len(accepted) > 0)
        return accepted[-1]

    mgr.notify = notify
    await mgr.announce_first_start()
    assert mgr.first_start_notified is False
    await mgr.announce_first_start()
    assert mgr.first_start_notified is True
    await mgr.announce_first_start()
    assert len(accepted) == 2
