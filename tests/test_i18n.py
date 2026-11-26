"""Translations of the UI: every English text the UI translates has an entry in every
dictionary (i18n-<code>.js), and the entries keep the {placeholders} of their English text."""

import ast
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
    keys.update(server_texts())
    return keys


def server_texts() -> list[str]:
    """English texts of the server the UI translates (SERVER_TEXTS of server-texts.js)."""
    src = (JS_DIR / "server-texts.js").read_text()
    start = src.index("const SERVER_TEXTS = [")
    block = src[start : src.index("\n];", start)]
    return [json.loads(f'"{s}"') for s in STRING.findall(block)]


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
    src = (JS_DIR / "lang.js").read_text()
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


def _server_patterns() -> list[re.Pattern[str]]:
    """SERVER_TEXTS as patterns: a {placeholder} stands for any text (as serverText)."""
    out = []
    for text in server_texts():
        parts = re.split(r"\{\w+\}", text)
        out.append(re.compile("^" + "(.+?)".join(re.escape(part) for part in parts) + "$", re.S))
    return out


def _texts_of(node: ast.expr) -> list[str]:
    """Possible values of a string expression: a literal, an f-string (each value as "X"),
    both sides of a conditional; [] for anything else (a variable, a call)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):  # a conditional inside gives a text per branch
        out = [""]
        for part in node.values:
            inner = part.value if isinstance(part, ast.FormattedValue) else part
            options = [part.value] if isinstance(part, ast.Constant) else (
                _texts_of(inner) if isinstance(inner, ast.IfExp) else ["X"])
            out = [head + option for head in out for option in options]
        return out
    if isinstance(node, ast.IfExp):
        return _texts_of(node.body) + _texts_of(node.orelse)
    return []


# Errors that become a status message of a device (or the answer of an install dialog)
ERRORS = {"ShellyError", "AuthError", "AddressError", "OtaError", "UnsupportedError"}
# Functions whose returned text is a status message or the error of a connection test
MESSAGE_FUNCTIONS = {"_install_diagnosis", "_ota_error_text", "fetch_error"}
# Functions whose "error" values (keyword or ["error"]) are the result of a connection test
RESULT_FUNCTIONS = {"_test_with"}
# Sources of status messages; the errors of the other modules answer dialogs
SOURCES = ("manager_checks.py", "manager_installs.py", "manager_store.py", "manager_schedules.py",
           "shelly.py", "shelly_net.py")
MESSAGE_ONLY = ("manager_core.py", "manager_devices.py", "manager_backups.py")


def _result_errors(func: ast.AsyncFunctionDef) -> list[str]:
    """Values of "error" in a function of the connection test: error=… keywords and
    result["error"] = … assignments."""
    texts: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            texts += [text for keyword in node.keywords if keyword.arg == "error" for text in _texts_of(keyword.value)]
        elif isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and target.slice.value == "error":
                texts += _texts_of(node.value)
    return texts


def _sent_texts() -> list[str]:
    """Texts the server sends as status messages and errors: the message constants, the
    values of "message" (keyword or dev["message"]), the texts of Shelly errors, of the
    diagnosis functions and the errors of the connection test – with every value of an
    f-string as "X"."""
    import manager_checks
    import manager_core
    import manager_store

    texts = [
        *manager_checks.OFFER_MESSAGES.values(),
        *manager_core.DOWNLOAD_BASE_MESSAGES.values(),
        manager_checks.ASLEEP_MESSAGE.format(error="X"),
        manager_store.GEN1_MARKED_MESSAGE,
        *(f"X. {hint.format(version='1')}" for hint in (
            manager_checks.STORE_FALLBACK_HINT, manager_checks.IMPORT_FALLBACK_HINT, manager_checks.IMPORT_FALLBACK_HINT_GEN1)),
        manager_core.unexpected_text(KeyError("X")),
        manager_core.FIRST_START_BLOCK,
    ]
    app = ROOT / "shelly_firmware_manager" / "app"
    for name in SOURCES + MESSAGE_ONLY:
        tree = ast.parse((app / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "message":
                        texts += _texts_of(keyword.value)
                func = node.func
                called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if called in ERRORS and node.args and name in SOURCES:
                    texts += _texts_of(node.args[-1])
            elif isinstance(node, ast.Assign):
                target = node.targets[0]
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and target.slice.value == "message":
                    texts += _texts_of(node.value)
            elif isinstance(node, ast.FunctionDef) and node.name in MESSAGE_FUNCTIONS:
                texts += [text for ret in ast.walk(node) if isinstance(ret, ast.Return) and ret.value for text in _texts_of(ret.value)]
            elif isinstance(node, ast.AsyncFunctionDef) and node.name in RESULT_FUNCTIONS:
                texts += _result_errors(node)
    # Texts that are only values (e.g. "{error}. {hint}", covered by the hints above)
    return [text for text in texts if re.sub(r"[X.\s]", "", text)]


def test_server_texts_match_the_server():
    """Every status message and Shelly error the server builds is a text of SERVER_TEXTS (else
    it would stay English); a changed server text must be changed in server-texts.js too."""
    patterns = _server_patterns()
    sent = _sent_texts()
    assert len(sent) > 40
    missing = sorted({text for text in sent if not any(p.match(text) for p in patterns)})
    assert not missing, missing
