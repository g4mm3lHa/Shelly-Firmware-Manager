"""Translations of the UI: every English text the UI translates has a German entry, and
the German entries keep the {placeholders} of their English text."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "ui-src" / "js"
INDEX = ROOT / "shelly_firmware_manager" / "app" / "static" / "index.html"
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _argument(src: str, start: int) -> str:
    """Source of the first argument of a call whose "(" is at start - 1: up to the first
    comma or closing parenthesis outside strings and brackets."""
    depth, i = 0, start
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            end = src.index(c, i + 1)
            while src[end - 1] == "\\":
                end = src.index(c, end + 1)
            i = end
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return src[start:i]
            depth -= 1
        elif c == "," and depth == 0:
            return src[start:i]
        i += 1
    raise AssertionError("unterminated call")


def js_keys() -> set[str]:
    """English texts of t("…") calls (also both texts of t(a ? "x" : "y")) and of the
    option fields of the Settings tab (translated when shown)."""
    keys: set[str] = set()
    for path in JS_DIR.glob("*.js"):
        if path.name.startswith("i18n"):
            continue
        src = path.read_text()
        for m in re.finditer(r"(?<![\w.$])t\(", src):
            for s in STRING.findall(_argument(src, m.end())):
                keys.add(json.loads(f'"{s}"'))
    settings = (JS_DIR / "app-settings.js").read_text()
    block = settings[settings.index("export const OPTION_FIELDS = [") : settings.index("\n];", settings.index("OPTION_FIELDS"))]
    fields = re.findall(r'^  \["\w+", "([^"]+)", "\w+", "([^"]*)", (?:"[^"]+"|STORAGE)\],$', block, re.M)
    assert len(fields) == block.count("\n  [")
    for label, desc in fields:
        keys.update((label, desc))
    groups = re.search(r"^const OPTION_GROUPS = \[(.*)\];", settings, re.M)
    assert groups
    keys.update(STRING.findall(groups.group(1)))
    return keys


class _Texts(HTMLParser):
    """Text nodes and the translated attributes of index.html (as translatePage)."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: set[str] = set()
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "title"):
            self.skip += 1
        for name, value in attrs:
            if name in ("placeholder", "aria-label", "data-tip") and value:
                self.texts.add(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "title"):
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.texts.add(data.strip())


def html_keys() -> set[str]:
    parser = _Texts()
    parser.feed(INDEX.read_text())
    return parser.texts


def german() -> dict[str, str]:
    src = (JS_DIR / "i18n-de.js").read_text()
    body = src[src.index("export default ") + len("export default ") :].strip().rstrip(";")
    data = json.loads(body)
    assert isinstance(data, dict)
    return data


# Texts of index.html that are names, not words (the same in every language)
NAMES = {"Firmware Manager for Shelly", "__BETA__"}  # __BETA__: filled by the server


def test_every_ui_text_has_a_german_translation():
    de = german()
    missing = sorted((js_keys() | html_keys()) - NAMES - set(de))
    assert not missing, missing


def test_german_texts_keep_the_placeholders():
    for en, text in german().items():
        assert sorted(re.findall(r"\{\w+\}", en)) == sorted(re.findall(r"\{\w+\}", text)), en
        assert text.strip(), en


def test_dictionary_has_no_unused_entries():
    """Every entry belongs to an English text the UI still uses."""
    unused = sorted(set(german()) - js_keys() - html_keys())
    assert not unused, unused
