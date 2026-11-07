#!/usr/bin/env python3
"""Build the minified UI files from the readable sources (development only).

  shelly_firmware_manager/ui-src/{app.js,style.css}  readable, commented sources (edit these)
  shelly_firmware_manager/app/static/{app.js,style.css}  minified build output (shipped)

Runs esbuild locally (pinned version, via npx; nothing is uploaded, the image needs no
Node). Every output starts with a comment holding the SHA-256 of its source; the test
suite fails if an output is older than its source (tests/test_build.py).

Usage: python tools/build_ui.py [--check]
  --check  only verify that the outputs match the sources (no Node needed)
"""

import hashlib
import subprocess  # nosec B404  # runs only the pinned local esbuild
import sys
from pathlib import Path

ESBUILD = "esbuild@0.28.2"
ROOT = Path(__file__).resolve().parents[1] / "shelly_firmware_manager"
SRC = ROOT / "ui-src"
OUT = ROOT / "app" / "static"
FILES = ("app.js", "style.css")
HEADER = "/* Built from ui-src/{name} sha256:{sha} – do not edit, run tools/build_ui.py */\n"


def source_sha(name):
    """SHA-256 of a source file."""
    return hashlib.sha256((SRC / name).read_bytes()).hexdigest()


def header(name):
    """First line of the built file."""
    return HEADER.format(name=name, sha=source_sha(name))


def is_current(name):
    """True if the built file was produced from the current source."""
    out = OUT / name
    return out.exists() and out.read_text(encoding="utf-8").startswith(header(name))


def build(name):
    """Minify one file with esbuild (syntax kept as is: no target lowering)."""
    result = subprocess.run(  # nosec B603 B607  # fixed arguments, no shell, developer tool
        ["npx", "--yes", ESBUILD, str(SRC / name), "--minify", "--log-level=warning"],
        check=True,
        capture_output=True,
        text=True,
    )
    (OUT / name).write_text(header(name) + result.stdout, encoding="utf-8")
    print(f"{name}: {(SRC / name).stat().st_size} -> {(OUT / name).stat().st_size} bytes")


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
