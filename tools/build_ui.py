#!/usr/bin/env python3
"""Build the minified UI files from the readable sources (development only).

  ui-src/js/*.js                            readable, commented ES modules (edit these;
                                            entry module: main.js)
  ui-src/js/i18n-<code>.js                  dictionaries, one file each (not part of app.js)
  ui-src/start.js                           loader: language, its dictionary, then app.js
  ui-src/style.css                          readable, commented styles (edit this)
  shelly_firmware_manager/app/static/{start.js,app.js,i18n-<code>.js,style.css}
                                            minified build output (shipped)

Runs esbuild locally (pinned version, via npx; nothing is uploaded, the image needs no
Node): the modules are bundled into one classic script (IIFE), every dictionary into a
classic script of its own (global SFM_TEXTS) that the loader adds only for the language
in use, and the styles are minified.
Every output starts with a comment holding the SHA-256 of its sources; the test suite
fails if an output is older than its sources (tests/test_build.py).

Usage: python tools/build_ui.py [--check]
  --check  only verify that the outputs match the sources (no Node needed)
"""

import hashlib
import subprocess  # nosec B404  # runs only the pinned local esbuild
import sys
from pathlib import Path

ESBUILD = "esbuild@0.28.2"
REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "shelly_firmware_manager"
SRC = REPO / "ui-src"
OUT = ROOT / "app" / "static"
JS_DIR = SRC / "js"  # ES modules bundled into app.js
JS_ENTRY = JS_DIR / "main.js"
LOADER = SRC / "start.js"  # entry of start.js (imports js/lang.js)
DICTIONARIES = tuple(path.name for path in sorted(JS_DIR.glob("i18n-*.js")))  # i18n-<code>.js
FILES = ("start.js", "app.js", *DICTIONARIES, "style.css")
HEADER = "/* Built from ui-src/{source} sha256:{sha} – do not edit, run tools/build_ui.py */\n"


def sources(name):
    """Source files of an output: the modules of app.js (all but the dictionaries), the
    loader with the language module, or the one file (dictionary, style sheet)."""
    if name == "app.js":
        return sorted(path for path in JS_DIR.glob("*.js") if path.name not in DICTIONARIES)
    if name == "start.js":
        return [LOADER, JS_DIR / "lang.js"]
    return [JS_DIR / name] if name in DICTIONARIES else [SRC / name]


def source_sha(name):
    """SHA-256 over the names and contents of the source files of an output."""
    digest = hashlib.sha256()
    for path in sources(name):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def header(name):
    """First line of the built file."""
    label = "js/*.js" if name == "app.js" else f"js/{name}" if name in DICTIONARIES else name
    return HEADER.format(source=label, sha=source_sha(name))


def is_current(name):
    """True if the built file was produced from the current source."""
    out = OUT / name
    return out.exists() and out.read_text(encoding="utf-8").startswith(header(name))


def build(name):
    """Bundle the modules / minify the styles with esbuild (syntax kept as is: no
    target lowering)."""
    if name == "app.js":
        source = [str(JS_ENTRY), "--bundle", "--format=iife"]
    elif name == "start.js":
        source = [str(LOADER), "--bundle", "--format=iife"]
    elif name in DICTIONARIES:  # var SFM_TEXTS = {default: {...}}
        source = [str(JS_DIR / name), "--format=iife", "--global-name=SFM_TEXTS"]
    else:
        source = [str(SRC / name)]
    result = subprocess.run(  # nosec B603 B607  # fixed arguments, no shell, developer tool
        ["npx", "--yes", ESBUILD, *source, "--minify", "--log-level=warning"],
        check=True,
        capture_output=True,
        text=True,
    )
    (OUT / name).write_text(header(name) + result.stdout, encoding="utf-8")
    size = sum(path.stat().st_size for path in sources(name))
    print(f"{name}: {size} -> {(OUT / name).stat().st_size} bytes")


def main():
    """Build all files or, with --check, report outdated ones (exit code 1)."""
    if "--check" in sys.argv:
        outdated = [n for n in FILES if not is_current(n)]
        for name in outdated:
            print(f"outdated: app/static/{name} – run tools/build_ui.py")
        return 1 if outdated else 0
    for name in FILES:
        build(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
