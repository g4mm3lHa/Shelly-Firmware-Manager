"""Firmware changelogs from the official Shelly API documentation.

Sources (fetched without any device data in the request, at most once per check and
generation, and only when a firmware offered to a device has no changelog stored
with it yet – see Manager._store_changelogs; a page is not fetched again within
CACHE_MIN_AGE_S, because it may simply not list the version yet):
- Gen2+: https://shelly-api-docs.shelly.cloud/gen2/changelog/
  (h2 "[x.y.z] date", h3 category, list items)
- Gen1:  https://shelly-api-docs.shelly.cloud/gen1/ – table below h2#changelog
  (revision, date, "* [scope] text")

The HTML is parsed server-side with the standard library parser into plain text
(no markup, scripts or links survive), limited in size and stored in
/data/changelog.json (the last fetched page per generation). The changelog of a
stored firmware is copied from there to its store entry and is then never
requested again. The UI only ever receives plain strings.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import aiohttp
from tls import CertificatePins

import data_paths
from shelly import ShellyError, clip, parse_version
from shelly_cloud import fetch_cloud_text

log = logging.getLogger("sfm.changelog")

SOURCES = {
    1: "https://shelly-api-docs.shelly.cloud/gen1/",
    2: "https://shelly-api-docs.shelly.cloud/gen2/changelog/",
}
CACHE_MIN_AGE_S = 3600  # fetch a page for a missing version at most once per hour
MAX_PAGE = 4 * 1024 * 1024  # the Gen1 page is ~1.2 MB (complete API reference)
MAX_VERSIONS = 300
MAX_ENTRIES = 300  # per version
MAX_TEXT = 1000  # characters per entry
SECURITY_RE = re.compile(r"\b(security|vulnerab\w*|CVE-\d{4}-\d+)\b", re.IGNORECASE)
HEADING_RE = re.compile(r"\[?v?(\d+\.\d+\.\d+(?:-[a-z]+\d*)?)\]?[\s-]*(\d{4}-\d{2}-\d{2})?")
GEN1_ENTRY_RE = re.compile(r"^\*?\s*\[\s*([^\]]{1,80}?)\s*\]\s*(.*)$", re.DOTALL)
SPACE_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Collapse whitespace and drop zero-width characters of anchor links."""
    return SPACE_RE.sub(" ", text.replace("\u200b", "")).strip()


def _entry(category: str | None, text: str, models: Iterable[str] = ()) -> dict[str, Any]:
    """One normalised changelog entry."""
    return {
        "category": clip(category, 40) or None,
        "text": clip(text, MAX_TEXT),
        "models": sorted({clip(m, 64) for m in models if m})[:20],
    }


class _Gen2Parser(HTMLParser):
    """Collects versions, categories and list items of the Gen2+ changelog."""

    def __init__(self) -> None:
        """Start outside the article; only content after the <h1> is read."""
        super().__init__(convert_charrefs=True)
        self.versions: dict[str, dict[str, Any]] = {}
        self.active = False  # inside the changelog body
        self.capture: str | None = None  # "h2" | "h3" while reading a heading
        self.buffer: list[str] = []
        self.version: str | None = None  # current version or None (e.g. "Unreleased")
        self.category: str | None = None
        self.items: list[tuple[list[str], set[str]]] = []  # stack of open <li>: (text parts, models)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track headings, list items and device links."""
        if tag == "h1":
            self.active = True
        if not self.active:
            return
        if tag in ("h2", "h3"):
            self.capture, self.buffer = tag, []
        elif tag == "li":
            self.items.append(([], set()))
        elif tag == "a" and self.items:
            href = dict(attrs).get("href") or ""
            if "/Devices/" in href:  # entry refers to a specific device model
                self.items[-1][1].add(href.rstrip("/").rsplit("/", 1)[-1])

    def handle_endtag(self, tag: str) -> None:
        """Finish headings and list items."""
        if not self.active:
            return
        if tag == "article":
            self.active = False
        elif tag == self.capture:
            text = _clean("".join(self.buffer))
            if tag == "h2":
                match = HEADING_RE.match(text)
                self.version = match.group(1) if match else None
                self.category = None
                if match and self.version and len(self.versions) < MAX_VERSIONS:
                    self.versions.setdefault(self.version, {"date": match.group(2), "entries": []})
            else:
                self.category = text
            self.capture = None
        elif tag == "li" and self.items:
            parts, models = self.items.pop()
            text = _clean("".join(parts))
            if self.items:  # nested list: becomes part of the parent entry
                self.items[-1][0].append(f" ({text})")
                self.items[-1][1].update(models)
            elif text and self.version is not None and self.version in self.versions:
                entries = self.versions[self.version]["entries"]
                if len(entries) < MAX_ENTRIES:
                    entries.append(_entry(self.category, text, models))

    def handle_data(self, data: str) -> None:
        """Text of the open heading or list item."""
        if self.capture:
            self.buffer.append(data)
        elif self.items:
            self.items[-1][0].append(data)


class _Gen1Parser(HTMLParser):
    """Reads the changelog table below <h2 id="changelog"> of the Gen1 docs."""

    def __init__(self) -> None:
        """Wait for the changelog heading, then read the first table."""
        super().__init__(convert_charrefs=True)
        self.versions: dict[str, dict[str, Any]] = {}
        self.state = "search"  # search -> heading -> table -> done
        self.cells: list[str] | None = None  # cells of the current row
        self.cell: list[str] | None = None  # text parts of the current cell
        self.version: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Find the heading, then track rows and cells of the table."""
        if self.state == "search" and tag == "h2" and dict(attrs).get("id") == "changelog":
            self.state = "heading"
        elif self.state == "heading" and tag == "table":
            self.state = "table"
        elif self.state == "table" and tag == "tr":
            self.cells = []
        elif self.state == "table" and tag == "td" and self.cells is not None:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        """Turn finished rows into entries; stop after the table."""
        if self.state != "table":
            return
        if tag == "td" and self.cell is not None and self.cells is not None:
            self.cells.append(_clean("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.cells:
            self._row(self.cells)
            self.cells = None
        elif tag == "table":
            self.state = "done"

    def _row(self, cells: list[str]) -> None:
        """Revision, date, description; an empty revision continues the previous one."""
        if len(cells) < 3:
            return
        revision, date, text = cells[:3]
        match = HEADING_RE.match(revision) if revision else None
        if match:
            self.version = match.group(1)
            if len(self.versions) < MAX_VERSIONS:
                self.versions.setdefault(self.version, {"date": clip(date, 20) or None, "entries": []})
        if not text or self.version is None or self.version not in self.versions:
            return
        entry = GEN1_ENTRY_RE.match(text)
        scope, body = (entry.group(1), entry.group(2)) if entry else (None, text)
        # "[Shelly 1PM]" refers to a model, "[All devices]"/"[MQTT]" do not
        models = [scope] if scope and "shelly" in scope.lower() else []
        entries = self.versions[self.version]["entries"]
        if len(entries) < MAX_ENTRIES:
            entries.append(_entry(scope, body, models))

    def handle_data(self, data: str) -> None:
        """Text of the open cell."""
        if self.cell is not None:
            self.cell.append(data)


_NO_CODE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)


def parse(gen: int, html: str) -> dict[str, dict[str, Any]]:
    """Plain-text changelog {version: {"date", "entries"}} from the HTML page."""
    parser: _Gen1Parser | _Gen2Parser = _Gen1Parser() if gen == 1 else _Gen2Parser()
    parser.feed(_NO_CODE.sub("", html))  # script/style content is not changelog text
    parser.close()
    return parser.versions


def _model_key(text: object) -> str:
    """Comparable model name: 'ShellyPlus1PM', 'Plus 1PM' and 'Plus1PM' are equal."""
    key = re.sub(r"[^a-z0-9]", "", str(text).lower())
    return key.removeprefix("shelly")


def marked(version: str, info: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """One version {"version", "date", "entries"} for the UI. Each entry is marked:
    security (category "Security" or security keywords), model_specific (refers to a
    device model) and this_model (Gen2+: refers to the given model)."""
    own = _model_key(model) if model else None
    entries: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        models = entry.get("models") or []
        entries.append(
            {
                **entry,
                "security": (entry.get("category") or "").lower() == "security"
                or bool(SECURITY_RE.search(entry.get("text", ""))),
                "model_specific": bool(models),
                "this_model": bool(own) and any(_model_key(m) == own for m in models),
            }
        )
    return {"version": version, "date": info.get("date"), "entries": entries}


class Changelogs:
    """Stored changelogs per generation, refreshed by the firmware check."""

    def __init__(self, path: Path) -> None:
        """Load stored changelogs; missing or broken file = none."""
        self.path = path
        self.data: dict[str, Any] = {}  # "1" | "2" -> {"fetched": ts, "versions": {...}}
        try:
            data = json.loads(path.read_text())
            self.data = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as err:
            log.warning("Changelog file unreadable, ignoring it: %s", err)

    def _recently_fetched(self, gen: int, now: int) -> bool:
        """True if the page of gen was fetched less than CACHE_MIN_AGE_S ago: a
        version it does not list is not asked for again that soon."""
        stored = self.data.get(str(gen))
        if not stored or not isinstance(stored.get("fetched"), int):
            return False
        age = now - int(stored["fetched"])
        return 0 <= age < CACHE_MIN_AGE_S  # negative: clock jumped back, do not trust it

    async def refresh(
        self, session: aiohttp.ClientSession, gens: Iterable[int], pins: CertificatePins | None
    ) -> None:
        """Fetch and parse the changelog pages for the given generations (1 / 2).

        Called only for generations with a version whose changelog is still missing
        (see Manager._store_changelogs); a page fetched within CACHE_MIN_AGE_S is
        not requested again. Failures keep the previously stored page and are only
        logged."""
        changed = False
        now = int(time.time())
        for gen in sorted(gens):
            if self._recently_fetched(gen, now):
                log.debug("Changelog Gen%s was fetched recently, not requested again", gen)
                continue
            try:
                html = await fetch_cloud_text(session, SOURCES[gen], pins, MAX_PAGE)
                # Worker thread: parsing the ~1.2 MB Gen1 page must not block the event loop
                versions = await asyncio.to_thread(parse, gen, html)
                if not versions:
                    raise ShellyError("no versions found – page layout changed?")
                self.data[str(gen)] = {"fetched": int(time.time()), "versions": versions}
                changed = True
                log.info("Changelog Gen%s updated (%d versions)", gen, len(versions))
            except Exception as err:  # noqa: BLE001 – never let the changelog break a check
                log.warning("Changelog Gen%s not available: %s", gen, err)
        if changed:
            try:
                data_paths.write_atomic(self.path, json.dumps(self.data).encode(), durable=False)
            except OSError as err:  # e.g. disk full: keep the old file, the check goes on
                log.warning("Changelog not saved: %s", err)

    def version_entry(self, gen: int | None, version: object) -> dict[str, Any] | None:
        """Changelog {"date", "entries"} of exactly this version from the last fetched
        page of its generation, else None. Versions are compared as numbers, so the
        Gen1 form '20230913-112003/v1.14.0-g…' matches the page's '1.14.0'."""
        stored = self.data.get("1" if gen == 1 else "2")
        if not stored:
            return None
        try:
            wanted = parse_version(version)
        except ShellyError:
            return None
        for listed, info in stored.get("versions", {}).items():
            try:
                if parse_version(listed) == wanted and isinstance(info, dict):
                    return {"date": info.get("date"), "entries": list(info.get("entries") or [])}
            except ShellyError:
                continue
        return None

    def between(
        self,
        gen: int | None,
        installed: str,
        target: str,
        model: str | None = None,
        target_changelog: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """All versions with installed < version <= target, newest first.

        target_changelog is the changelog kept with the offered firmware in the
        store: it is used for the target version. Versions in between come from the
        last fetched page as far as it lists them – nothing is requested for them.
        Entries are marked for the device's model (see marked). Returns None if
        nothing is stored at all."""
        stored = self.data.get("1" if gen == 1 else "2")
        if not stored and not target_changelog:
            return None
        try:
            low, high = parse_version(installed), parse_version(target)
        except ShellyError:
            return None
        found: dict[tuple[int, ...], tuple[str, dict[str, Any]]] = {}  # parsed version -> (label, changelog)
        for version, info in (stored or {}).get("versions", {}).items():
            try:
                parsed = parse_version(version)
            except ShellyError:
                continue
            if low < parsed <= high and isinstance(info, dict):
                found[parsed] = (version, info)
        if target_changelog and low < high:
            label = found[high][0] if high in found else ".".join(str(n) for n in high[:3])
            found[high] = (label, target_changelog)
        return [marked(version, info, model) for _, (version, info) in sorted(found.items(), reverse=True)]
