"""Translations of the UI: every English text the UI translates has an entry in every
dictionary (i18n-<code>.js), and the entries keep the {placeholders} of their English text."""

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
    """English texts of t("…") calls (also both texts of t(a ? "x" : "y")), of tn(n, "…", "…") and of the
    option fields of the Settings tab (translated when shown)."""
    keys: set[str] = set()
    for path in JS_DIR.glob("*.js"):
        if path.name.startswith("i18n"):
            continue
        src = path.read_text()
        for m in re.finditer(r"(?<![\w.$])t\(", src):
            for s in STRING.findall(_argument(src, m.end())):
                keys.add(json.loads(f'"{s}"'))
        # tn(n, "one", "many", vars): the second and the third argument
        for m in re.finditer(r"(?<![\w.$])tn\(", src):
            start = m.end() + len(_argument(src, m.end())) + 1
            for _ in range(2):
                text = _argument(src, start)
                keys.update(json.loads(f'"{s}"') for s in STRING.findall(text))
                start += len(text) + 1
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


def dictionaries() -> dict[str, dict[str, str]]:
    """{language code: dictionary} of every ui-src/js/i18n-<code>.js."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(JS_DIR.glob("i18n-*.js")):
        src = path.read_text()
        body = src[src.index("export default ") + len("export default ") :].strip().rstrip(";")
        data = json.loads(body)
        assert isinstance(data, dict)
        out[path.stem.removeprefix("i18n-")] = data
    return out


def german() -> dict[str, str]:
    return dictionaries()["de"]


# Texts of index.html that are names, not words (the same in every language)
NAMES = {"Firmware Manager for Shelly", "__BETA__"}  # __BETA__: filled by the server


def test_languages_match_the_dictionaries():
    """The language field offers exactly the dictionaries (plus English and "auto")."""
    src = (JS_DIR / "i18n.js").read_text()
    offered = set(re.findall(r'\["(\w+)", "[^"]+"\]', src[src.index("export const LANGUAGES") :].split("\n\n")[0]))
    assert offered == {"auto", "en"} | set(dictionaries())


def test_every_ui_text_has_a_translation():
    needed = (js_keys() | html_keys()) - NAMES
    for code, dictionary in dictionaries().items():
        missing = sorted(needed - set(dictionary))
        assert not missing, (code, missing)


def test_translations_keep_the_placeholders():
    for code, dictionary in dictionaries().items():
        for en, text in dictionary.items():
            assert sorted(re.findall(r"\{\w+\}", en)) == sorted(re.findall(r"\{\w+\}", text)), (code, en)
            assert text.strip(), (code, en)


def test_dictionaries_have_no_unused_entries():
    """Every entry belongs to an English text the UI still uses."""
    used = js_keys() | html_keys()
    for code, dictionary in dictionaries().items():
        unused = sorted(set(dictionary) - used)
        assert not unused, (code, unused)
