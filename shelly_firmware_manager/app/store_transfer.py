"""Export and import of the complete firmware store as one sealed ZIP file.

The export is a normal, uncompressed ZIP (firmware files are compressed already) that
any archive tool opens:

    manifest.json        all store entries plus the seal
    SHA256SUMS.txt       "<sha256>  <path>" per file, for a check without this app
    Gen2+/<type>/<type>-<version>[-upload].<ext>
    Gen1/<type>/<type>-v<x.y.z>[-upload].<ext>

The seal is an HMAC-SHA256 over the canonical JSON of the manifest (everything except
the seal itself) with a key derived from the export password (scrypt, standard
library). Every file is listed with its SHA-256 and size, so an import detects any
change of a file or of an entry; without the password no import is possible. The
password only seals – the archive is not encrypted (firmware is public anyway).

An import reads only the files the sealed manifest names (never extracts by archive
names), checks each against its SHA-256 and size and accepts the manifest at the top
level or in one folder (an archive that was unpacked and packed again).

Pure functions without manager state; the manager (manager_store) decides what is
stored. All functions here block (file I/O, scrypt) and run in a worker thread.
"""

import hashlib
import hmac
import json
import re
import secrets
import struct
import time
import zipfile
from pathlib import Path
from typing import Any

import shelly
from changelog import MAX_TEXT
from shelly import clip

FORMAT = "shelly-firmware-manager/store-export"  # manifest "format"

FORMAT_VERSION = 1  # manifest "format_version"; other versions are refused

MANIFEST = "manifest.json"

SUMS = "SHA256SUMS.txt"

MAX_MANIFEST = 8 * 1024 * 1024  # bytes; upper limit for the manifest of an import

MAX_ENTRIES = 5000  # upper limit for the entries of one export

# Upper limit for the central directory (the list of entries) of a ZIP that is opened:
# far above an export with MAX_ENTRIES entries, far below what could exhaust memory
MAX_DIRECTORY = 2 * 1024 * 1024  # bytes

ZIP_END = b"PK\x05\x06"  # signature of the end record of a ZIP

ZIP64_LOCATOR = b"PK\x06\x07"  # signature that precedes the end record of a ZIP64 archive

ZIP_END_SIZE = 22  # bytes of the end record without its comment (at most 65535 bytes)

SCRYPT = {"n": 2**15, "r": 8, "p": 1}  # scrypt cost; only exactly these values are accepted

SCRYPT_MAXMEM = 64 * 1024 * 1024  # bytes; memory limit of the key derivation

SALT_BYTES = 16

# Generated export password: 5 groups of 4 characters without easily confused ones
# (no 0/O, 1/I/L); about 103 bit of entropy
PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # noqa: S105 – character set, not a password

TYPE_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")  # firmware ID of an entry

SHA_RE = re.compile(r"[0-9a-f]{64}")

PATH_PART_RE = re.compile(r"[^A-Za-z0-9._+-]")  # characters replaced in archive names

READ_CHUNK = 1024 * 1024  # bytes per read when hashing or copying


class TransferError(ValueError):
    """An export file that cannot be imported (message is shown to the user)."""


def normalize_password(password: object) -> str:
    """Typed export password: spaces removed, upper case (the generated passwords use
    upper-case letters and digits only). Raises TransferError if missing."""
    text = re.sub(r"\s+", "", str(password or "")).upper()
    if not text or len(text) > 128:
        raise TransferError("Enter the export password")
    return text


def check_directory(path: Path) -> None:
    """Refuse a ZIP whose list of entries is larger than MAX_DIRECTORY – before
    zipfile reads that list into memory (a crafted archive can name millions of
    entries). ZIP64 archives are refused: their size fields live in another record,
    and no file handled by this app needs them. Raises TransferError."""
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        tail = min(size, ZIP_END_SIZE + 65535)
        handle.seek(size - tail)
        data = handle.read(tail)
    pos = data.rfind(ZIP_END)
    if pos < 0 or len(data) - pos < ZIP_END_SIZE:
        raise TransferError("The file is no ZIP archive")
    if data[max(pos - 20, 0) : pos].startswith(ZIP64_LOCATOR):
        raise TransferError("ZIP64 archives are not supported – pack the file as a normal ZIP")
    (directory_size,) = struct.unpack_from("<L", data, pos + 12)
    if directory_size > MAX_DIRECTORY:
        raise TransferError("The ZIP archive lists too many files")


def derive_key(password: str, salt: bytes) -> bytes:
    """Key of the seal: scrypt with the fixed cost SCRYPT (about 0.1 s)."""
    return hashlib.scrypt(password.encode(), salt=salt, maxmem=SCRYPT_MAXMEM, dklen=32, **SCRYPT)


def _canonical(body: dict[str, Any]) -> bytes:
    """Byte form the seal is computed over: sorted keys, no spaces, ASCII."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _seal(key: bytes, body: dict[str, Any]) -> str:
    """HMAC-SHA256 of the canonical manifest body (hex)."""
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def _sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def archive_path(meta: dict[str, Any], ext: str) -> str:
    """Readable path of a store entry inside the export (unique paths: see build)."""
    fw_type = PATH_PART_RE.sub("_", str(meta.get("type") or "unknown"))
    version = str(meta.get("version") or "unknown")
    if meta.get("gen") == 1:
        match = shelly.VERSION_RE.search(version)
        version = f"v{match.group(0)}" if match else version
        folder = "Gen1"
    else:
        folder = "Gen2+"
    upload = "-upload" if meta.get("source") == "upload" else ""
    return f"{folder}/{fw_type}/{fw_type}-{PATH_PART_RE.sub('_', version)}{upload}{ext}"


def _export_entry(meta: dict[str, Any], path: str) -> dict[str, Any]:
    """The manifest entry of a store entry: its path and the stored data that the
    import restores (never the file name inside the store)."""
    entry: dict[str, Any] = {
        "path": path,
        **{k: meta.get(k) for k in ("type", "gen", "version", "sha256", "size", "downloaded")},
        "source": "upload" if meta.get("source") == "upload" else "download",
    }
    if entry["source"] == "upload":
        entry["sha_checked"] = bool(meta.get("sha_checked"))
    else:
        entry["https"] = meta.get("https") if isinstance(meta.get("https"), bool) else None
    if meta.get("intermediate") is True:
        entry["intermediate"] = True
    if isinstance(meta.get("changelog"), dict):
        entry["changelog"] = meta["changelog"]
    return entry


def build_export(
    fw_dir: Path, firmware: dict[str, dict[str, Any]], key: bytes, salt: bytes, app_version: str, target: Path
) -> dict[str, int]:
    """Write the export of the store entries (file name -> metadata) to target.

    Every file is checked against its SHA-256 first: a damaged or missing file is
    left out (counted as "damaged"), never sealed. Returns the counts
    {"files", "downloads", "uploads", "size", "damaged"}."""
    entries: list[dict[str, Any]] = []
    sources: list[Path] = []
    used: set[str] = set()
    damaged = 0
    order = sorted(firmware.items(), key=lambda kv: (str(kv[1].get("type")), str(kv[1].get("version")), kv[0]))
    for name, meta in order:
        source = fw_dir / name
        try:
            intact = source.is_file() and _sha256_file(source) == meta.get("sha256")
        except OSError:
            intact = False
        if not intact:
            damaged += 1
            continue
        path = archive_path(meta, source.suffix.lower())
        if path in used:  # two entries with the same type/version name (e.g. upload and download)
            stem, ext = path.rsplit(".", 1)
            path = f"{stem}-{str(meta['sha256'])[:8]}.{ext}"
        used.add(path)
        entries.append(_export_entry(meta, path))
        sources.append(source)
    body = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "exported": int(time.time()),
        "kdf": {"name": "scrypt", **SCRYPT, "salt": salt.hex()},
        "entries": entries,
    }
    manifest = {**body, "seal": _seal(key, body)}
    sums = "".join(f"{e['sha256']}  {e['path']}\n" for e in entries)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
        archive.writestr(SUMS, sums)
        for entry, source in zip(entries, sources, strict=True):
            archive.write(source, entry["path"])
    uploads = sum(1 for e in entries if e["source"] == "upload")
    return {
        "files": len(entries),
        "downloads": len(entries) - uploads,
        "uploads": uploads,
        "size": sum(int(e["size"] or 0) for e in entries),
        "damaged": damaged,
    }


def generate_password() -> str:
    """A new export password in the form XXXX-XXXX-XXXX-XXXX-XXXX."""
    groups = ("".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(4)) for _ in range(5))
    return "-".join(groups)


# ------------------------------------------------------------------------------------------- Import


def _manifest_member(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, str]:
    """The manifest of an export: at the top level or in exactly one folder (an
    unpacked and packed again archive). Returns (member, folder prefix)."""
    found = [i for i in archive.infolist() if i.filename.rsplit("/", 1)[-1] == MANIFEST and i.filename.count("/") <= 1]
    if len(found) != 1:
        raise TransferError("No firmware store export: manifest.json missing")
    member = found[0]
    prefix = member.filename[: -len(MANIFEST)]
    return member, prefix


def _clean_changelog(value: object) -> dict[str, Any] | None:
    """A stored changelog of an entry, reduced to the known fields and limits."""
    if not isinstance(value, dict):
        return None
    date = value.get("date")
    entries = []
    items = value.get("entries")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        raw_models = item.get("models")
        models = raw_models if isinstance(raw_models, list) else []
        entries.append(
            {
                "category": clip(item.get("category"), 40) or None if item.get("category") else None,
                "text": clip(item["text"], MAX_TEXT),
                "models": sorted({clip(m, 64) for m in models if isinstance(m, str) and m})[:20],
            }
        )
        if len(entries) >= 200:
            break
    return {"date": clip(date, 32) if isinstance(date, str) else None, "entries": entries}


def _clean_entry(raw: object) -> dict[str, Any]:
    """Validate one manifest entry; returns the cleaned entry or raises TransferError."""
    if not isinstance(raw, dict):
        raise TransferError("Damaged manifest: entry is not an object")
    path, sha, size = raw.get("path"), raw.get("sha256"), raw.get("size")
    ext = Path(str(path or "")).suffix.lower()
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 255
        or path.startswith("/")
        or "\\" in path
        or any(part in ("", ".", "..") for part in path.split("/"))
        or ext not in shelly.FW_EXTENSIONS
    ):
        raise TransferError("Damaged manifest: invalid file path")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise TransferError("Damaged manifest: invalid SHA-256")
    if not isinstance(size, int) or isinstance(size, bool) or not shelly.MIN_FW_SIZE <= size <= shelly.MAX_FW_SIZE:
        raise TransferError("Damaged manifest: invalid file size")
    fw_type, gen, version = raw.get("type"), raw.get("gen"), raw.get("version")
    if not isinstance(fw_type, str) or not TYPE_RE.fullmatch(fw_type):
        raise TransferError("Damaged manifest: invalid firmware ID")
    if not isinstance(gen, int) or isinstance(gen, bool) or not 1 <= gen <= 9:
        raise TransferError("Damaged manifest: invalid generation")
    if not isinstance(version, str) or not version.strip() or len(version) > 64:
        raise TransferError("Damaged manifest: invalid version")
    downloaded = raw.get("downloaded")
    source = raw.get("source")
    if source not in ("download", "upload"):
        raise TransferError("Damaged manifest: invalid source")
    entry: dict[str, Any] = {
        "path": path,
        "ext": ext,
        "sha256": sha,
        "size": size,
        "type": fw_type,
        "gen": gen,
        "version": version,
        "source": source,
        "downloaded": downloaded if isinstance(downloaded, int) and not isinstance(downloaded, bool) and downloaded > 0
        else None,
    }
    if source == "upload":
        entry["sha_checked"] = raw.get("sha_checked") is True
    else:
        https = raw.get("https")
        entry["https"] = https if isinstance(https, bool) else gen != 1
    if raw.get("intermediate") is True:
        entry["intermediate"] = True
    changelog = _clean_changelog(raw.get("changelog"))
    if changelog is not None:
        entry["changelog"] = changelog
    return entry


def read_export(path: Path, password: str) -> dict[str, Any]:
    """Check an export completely: manifest format, seal (password), every entry and
    every file (size and SHA-256). Raises TransferError with a message for the user.

    Returns {"exported", "app_version", "prefix", "entries": [cleaned entries]}."""
    try:
        check_directory(path)
        with zipfile.ZipFile(path) as archive:
            member, prefix = _manifest_member(archive)
            if member.file_size > MAX_MANIFEST:
                raise TransferError("Damaged export: manifest too large")
            with archive.open(member) as handle:
                manifest = json.loads(handle.read(MAX_MANIFEST + 1)[:MAX_MANIFEST])
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                raise TransferError("No firmware store export of this app")
            if manifest.get("format_version") != FORMAT_VERSION:
                raise TransferError("Export format not supported by this version of the app")
            kdf, seal = manifest.get("kdf"), manifest.get("seal")
            salt_hex = kdf.get("salt") if isinstance(kdf, dict) else None
            if (
                not isinstance(kdf, dict)
                or kdf.get("name") != "scrypt"
                or any(kdf.get(k) != v for k, v in SCRYPT.items())
                or not isinstance(salt_hex, str)
                or not re.fullmatch(r"[0-9a-f]{32}", salt_hex)
                or not isinstance(seal, str)
            ):
                raise TransferError("Damaged export: seal missing")
            body = {k: v for k, v in manifest.items() if k != "seal"}
            if not hmac.compare_digest(_seal(derive_key(password, bytes.fromhex(salt_hex)), body), seal):
                raise TransferError("Wrong export password, or the file list was changed after the export")
            raw_entries = manifest.get("entries")
            if not isinstance(raw_entries, list) or len(raw_entries) > MAX_ENTRIES:
                raise TransferError("Damaged manifest: file list missing")
            entries = [_clean_entry(raw) for raw in raw_entries]
            if len({e["sha256"] for e in entries}) != len(entries) or len({e["path"] for e in entries}) != len(entries):
                raise TransferError("Damaged manifest: a file is listed twice")
            for entry in entries:
                _check_member(archive, prefix + entry["path"], entry)
    except TransferError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError, KeyError) as err:
        raise TransferError(f"The file is no readable firmware store export ({type(err).__name__})") from None
    exported = manifest.get("exported")
    return {
        "exported": exported if isinstance(exported, int) else None,
        "app_version": clip(manifest.get("app_version"), 32),
        "prefix": prefix,
        "entries": entries,
    }


def _check_member(archive: zipfile.ZipFile, name: str, entry: dict[str, Any]) -> None:
    """The archive file of an entry exists and has the sealed size and SHA-256."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise TransferError(f"File missing in the export: {entry['path']}") from None
    if info.file_size != entry["size"]:
        raise TransferError(f"File changed since the export: {entry['path']}")
    digest, size = hashlib.sha256(), 0
    with archive.open(info) as handle:
        while chunk := handle.read(READ_CHUNK):
            size += len(chunk)
            if size > entry["size"]:
                break
            digest.update(chunk)
    if size != entry["size"] or not hmac.compare_digest(digest.hexdigest(), entry["sha256"]):
        raise TransferError(f"File changed since the export: {entry['path']}")


def extract_entry(path: Path, prefix: str, entry: dict[str, Any], target: Path) -> None:
    """Copy the file of a checked entry to target, verifying size and SHA-256 again
    (the staged export could change between check and confirmation)."""
    digest, size = hashlib.sha256(), 0
    with zipfile.ZipFile(path) as archive, archive.open(prefix + entry["path"]) as src, target.open("wb") as dst:
        while chunk := src.read(READ_CHUNK):
            size += len(chunk)
            if size > entry["size"]:
                break
            digest.update(chunk)
            dst.write(chunk)
    if size != entry["size"] or not hmac.compare_digest(digest.hexdigest(), entry["sha256"]):
        target.unlink(missing_ok=True)
        raise TransferError(f"File changed since the check: {entry['path']}")
