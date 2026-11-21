#!/usr/bin/env python3
"""Set the app version for a release candidate or a release (development only).

  shelly_firmware_manager/config.yaml   version: "<version>"
  shelly_firmware_manager/app/main.py   VERSION = "<version>"
  shelly_firmware_manager/CHANGELOG.md  release candidates only: an empty block
                                        "Changes in rcN" on top of the section of the
                                        version (the section is created if it is missing)

The changelog texts are written by hand; for a release the rc blocks are merged by hand
as well. Run tools/dev/check.sh afterwards (it compares the two version lines).

Usage (repository root): python3 tools/dev/bump.py X.Y.Z[-rcN]
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "shelly_firmware_manager"
VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)(?:-rc(\d+))?")
# File -> pattern of its version line (group 1: text before the version, group 2: after it)
VERSION_LINES = {
    ROOT / "config.yaml": re.compile(r'^(version: ")[^"]+(")', re.M),
    ROOT / "app" / "main.py": re.compile(r'^(VERSION = ")[^"]+(")', re.M),
}
CHANGELOG = ROOT / "CHANGELOG.md"
SECTION_STUB = "## {release}\n\nRelease candidate. SUMMARY (at most two sentences)\n\n{block}"


def set_version(path, pattern, version):
    """Replace the one version line of a file; returns False if it already matches."""
    text = path.read_text(encoding="utf-8")
    new, count = pattern.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise SystemExit(f"{path.name}: expected one version line, found {count}")
    path.write_text(new, encoding="utf-8")
    return new != text


def add_rc_block(release, rc):
    """Put an empty "Changes in rcN" block on top of the section of the release
    (newest block first); returns what was done."""
    text = CHANGELOG.read_text(encoding="utf-8")
    block = f"Changes in rc{rc}\n\n"
    start = text.find(f"## {release}\n")
    if start < 0:  # new section above the newest one
        first = text.index("## ")
        text = text[:first] + SECTION_STUB.format(release=release, block=block) + text[first:]
        done = "section and block added"
    else:
        end = text.find("\n## ", start)
        section = text[start:end if end >= 0 else len(text)]
        if block.strip() in section.splitlines():
            return "block already there"
        older = section.find("Changes in rc")
        if older < 0:
            raise SystemExit(f"CHANGELOG.md: section {release} has no rc blocks (already released?)")
        text = text[:start + older] + block + text[start + older:]
        done = "block added"
    CHANGELOG.write_text(text, encoding="utf-8")
    return done


def main():
    """Set the version given on the command line."""
    match = VERSION_RE.fullmatch(sys.argv[1]) if len(sys.argv) == 2 else None
    if not match:
        raise SystemExit(__doc__)
    version, (release, rc) = match.group(0), match.groups()
    # Changelog first: a section that cannot take the block stops before anything is written
    changelog = f"{add_rc_block(release, rc)} – write the entries of rc{rc}" if rc else (
        "merge the rc blocks into one section by hand (Released <date>.)")
    for path, pattern in VERSION_LINES.items():
        print(f"{path.name}: {version}" + ("" if set_version(path, pattern, version) else " (unchanged)"))
    print(f"CHANGELOG.md: {changelog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
