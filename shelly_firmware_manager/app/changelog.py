"""Firmware changelogs from the official Shelly API documentation.

Sources (fetched only when a check found an update, at most once per check and
generation, without any device data in the request). A stored changelog is reused
for CACHE_MAX_AGE_S if it already lists every offered version; a version it does not
list yet causes a new fetch, but not more often than CACHE_MIN_AGE_S:
- Gen2+: https://shelly-api-docs.shelly.cloud/gen2/changelog/
  (h2 "[x.y.z] date", h3 category, list items)
- Gen1:  https://shelly-api-docs.shelly.cloud/gen1/ – table below h2#changelog
  (revision, date, "* [scope] text")

The HTML is parsed server-side with the standard library parser into plain text
(no markup, scripts or links survive), limited in size and stored in
/data/changelog.json. The UI only ever receives plain strings.
"""

import asyncio
import json
import logging
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from shelly import ShellyError, clip, fetch_cloud_text, parse_version

log = logging.getLogger("sfm.changelog")

SOURCES = {
    1: "https://shelly-api-docs.shelly.cloud/gen1/",
    2: "https://shelly-api-docs.shelly.cloud/gen2/changelog/",
}
CACHE_MAX_AGE_S = 24 * 3600  # reuse a complete stored changelog for one day
CACHE_MIN_AGE_S = 3600  # refetch for a missing version at most once per hour
MAX_PAGE = 4 * 1024 * 1024  # the Gen1 page is ~1.2 MB (complete API reference)
MAX_VERSIONS = 300
MAX_ENTRIES = 300  # per version
MAX_TEXT = 1000  # characters per entry
SECURITY_RE = re.compile(r"\b(security|vulnerab\w*|CVE-\d{4}-\d+)\b", re.IGNORECASE)
HEADING_RE = re.compile(r"\[?v?(\d+\.\d+\.\d+(?:-[a-z]+\d*)?)\]?[\s-]*(\d{4}-\d{2}-\d{2})?")
GEN1_ENTRY_RE = re.compile(r"^\*?\s*\[\s*([^\]]{1,80}?)\s*\]\s*(.*)$", re.DOTALL)
SPACE_RE = re.compile(r"\s+")


def _clean(text):
    """Collapse whitespace and drop zero-width characters of anchor links."""
    return SPACE_RE.sub(" ", text.replace("\u200b", "")).strip()


def _entry(category, text, models=()):
    """One normalised changelog entry."""
    return {
        "category": clip(category, 40) or None,
        "text": clip(text, MAX_TEXT),
        "models": sorted({clip(m, 64) for m in models if m})[:20],
    }


class _Gen2Parser(HTMLParser):
    """Collects versions, categories and list items of the Gen2+ changelog."""

    def __init__(self):
        """Start outside the article; only content after the <h1> is read."""
        super().__init__(convert_charrefs=True)
        self.versions = {}
        self.active = False  # inside the changelog body
        self.capture = None  # "h2" | "h3" while reading a heading
        self.buffer = []
        self.version = None  # current version or None (e.g. "Unreleased")
        self.category = None
        self.items = []  # stack of open <li>: [text parts, models]

    def handle_starttag(self, tag, attrs):
        """Track headings, list items and device links."""
        if tag == "h1":
            self.active = True
        if not self.active:
            return
        if tag in ("h2", "h3"):
            self.capture, self.buffer = tag, []
        elif tag == "li":
            self.items.append([[], set()])
        elif tag == "a" and self.items:
            href = dict(attrs).get("href") or ""
            if "/Devices/" in href:  # entry refers to a specific device model
                self.items[-1][1].add(href.rstrip("/").rsplit("/", 1)[-1])

    def handle_endtag(self, tag):
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
                if self.version and len(self.versions) < MAX_VERSIONS:
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
            elif text and self.version in self.versions:
                entries = self.versions[self.version]["entries"]
                if len(entries) < MAX_ENTRIES:
                    entries.append(_entry(self.category, text, models))

    def handle_data(self, data):
        """Text of the open heading or list item."""
        if self.capture:
            self.buffer.append(data)
        elif self.items:
            self.items[-1][0].append(data)


class _Gen1Parser(HTMLParser):
    """Reads the changelog table below <h2 id="changelog"> of the Gen1 docs."""

    def __init__(self):
        """Wait for the changelog heading, then read the first table."""
        super().__init__(convert_charrefs=True)
        self.versions = {}
        self.state = "search"  # search -> heading -> table -> done
        self.cells = None  # cells of the current row
        self.cell = None  # text parts of the current cell
        self.version = None

    def handle_starttag(self, tag, attrs):
        """Find the heading, then track rows and cells of the table."""
        if self.state == "search" and tag == "h2" and dict(attrs).get("id") == "changelog":
            self.state = "heading"
        elif self.state == "heading" and tag == "table":
            self.state = "table"
        elif self.state == "table" and tag == "tr":
            self.cells = []
        elif self.state == "table" and tag == "td" and self.cells is not None:
            self.cell = []

    def handle_endtag(self, tag):
        """Turn finished rows into entries; stop after the table."""
        if self.state != "table":
            return
        if tag == "td" and self.cell is not None:
            self.cells.append(_clean("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.cells:
            self._row(self.cells)
            self.cells = None
        elif tag == "table":
            self.state = "done"

    def _row(self, cells):
        """Revision, date, description; an empty revision continues the previous one."""
        if len(cells) < 3:
            return
        revision, date, text = cells[:3]
        match = HEADING_RE.match(revision) if revision else None
        if match:
            self.version = match.group(1)
            if len(self.versions) < MAX_VERSIONS:
                self.versions.setdefault(self.version, {"date": clip(date, 20) or None, "entries": []})
        if not text or self.version not in self.versions:
            return
        entry = GEN1_ENTRY_RE.match(text)
        scope, body = (entry.group(1), entry.group(2)) if entry else (None, text)
        # "[Shelly 1PM]" refers to a model, "[All devices]"/"[MQTT]" do not
        models = [scope] if scope and "shelly" in scope.lower() else []
        entries = self.versions[self.version]["entries"]
        if len(entries) < MAX_ENTRIES:
            entries.append(_entry(scope, body, models))

    def handle_data(self, data):
        """Text of the open cell."""
        if self.cell is not None:
            self.cell.append(data)


_NO_CODE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)


def parse(gen, html):
    """Plain-text changelog {version: {"date", "entries"}} from the HTML page."""
    parser = _Gen1Parser() if gen == 1 else _Gen2Parser()
    parser.feed(_NO_CODE.sub("", html))  # script/style content is not changelog text
    parser.close()
    return parser.versions


def _model_key(text):
    """Comparable model name: 'ShellyPlus1PM', 'Plus 1PM' and 'Plus1PM' are equal."""
    key = re.sub(r"[^a-z0-9]", "", str(text).lower())
    return key.removeprefix("shelly")


class Changelogs:
    """Stored changelogs per generation, refreshed by the firmware check."""

    def __init__(self, path: Path):
        """Load stored changelogs; missing or broken file = none."""
        self.path = path
        self.data = {}  # "1" | "2" -> {"fetched": ts, "versions": {...}}
        try:
            data = json.loads(path.read_text())
            self.data = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as err:
            log.warning("Changelog file unreadable, ignoring it: %s", err)

    def _is_fresh(self, gen, wanted, now):
        """True if the stored changelog of gen can be reused instead of a fetch.

        wanted: offered versions that should be listed. Complete and younger than
        CACHE_MAX_AGE_S -> reuse; a missing version -> fetch, unless the last fetch
        is younger than CACHE_MIN_AGE_S (the page may not list it yet)."""
        stored = self.data.get(str(gen))
        if not stored or not isinstance(stored.get("fetched"), int):
            return False
        age = now - stored["fetched"]
        if age < 0:  # clock jumped back: do not trust the timestamp
            return False
        known = set()
        for version in stored.get("versions", {}):
            try:
                known.add(parse_version(version)[:3])
            except ShellyError:
                continue
        missing = False
        for version in wanted or ():
            try:
                missing |= parse_version(version)[:3] not in known
            except ShellyError:
                continue
        return age < (CACHE_MIN_AGE_S if missing else CACHE_MAX_AGE_S)

    async def refresh(self, session, gens, pins, wanted=None):
        """Fetch and parse the changelog pages for the given generations (1 / 2).

        wanted maps a generation to the offered versions; a fresh stored changelog
        that lists them is reused without any request (see _is_fresh). Failures
        keep the previously stored version and are only logged."""
        changed = False
        now = int(time.time())
        for gen in sorted(gens):
            if self._is_fresh(gen, (wanted or {}).get(gen), now):
                log.debug("Changelog Gen%s reused from the cache", gen)
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
            temp = self.path.with_suffix(".tmp")
            try:
                temp.write_text(json.dumps(self.data))
                temp.replace(self.path)  # atomic: never a half-written file
            except OSError as err:  # e.g. disk full: keep the old file, the check goes on
                temp.unlink(missing_ok=True)
                log.warning("Changelog not saved: %s", err)

    def between(self, gen, installed, target, model=None):
        """All versions with installed < version <= target, newest first.

        Each entry is marked: security (category "Security" or security keywords),
        model_specific (refers to a device model) and this_model (Gen2+: refers to
        the device's own model). Returns None if no changelog is stored."""
        stored = self.data.get("1" if gen == 1 else "2")
        if not stored:
            return None
        try:
            low, high = parse_version(installed), parse_version(target)
        except ShellyError:
            return None
        own = _model_key(model) if model else None
        result = []
        for version, info in stored.get("versions", {}).items():
            try:
                if not low < parse_version(version) <= high:
                    continue
            except ShellyError:
                continue
            entries = []
            for entry in info.get("entries", []):
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
            result.append({"version": version, "date": info.get("date"), "entries": entries})
        result.sort(key=lambda v: parse_version(v["version"]), reverse=True)
        return result
