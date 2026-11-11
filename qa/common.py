"""Shared helpers of the Playwright UI checks (qa/*.py). Not part of the app.

Browser lookup: SFM_CHROME (path to a chrome / chrome-headless-shell binary)
wins; otherwise the newest Chromium found in the usual Playwright locations is used;
if none is found, Playwright's own default browser is launched."""

import glob
import os
import re

SEARCH = (
    os.path.expanduser("~/.cache/ms-playwright"),
    "/home/user/.cache/ms-playwright",
    "/opt/pw-browsers",
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
)
PATTERNS = {
    "chrome": ("chromium-*/chrome-linux*/chrome",),
    "shell": ("chromium_headless_shell-*/*/chrome-headless-shell", "chromium-*/chrome-linux*/chrome"),
}


def _revision(path):
    """Browser revision number in a Playwright path (0 if unknown), for sorting."""
    match = re.search(r"-(\d+)/", path)
    return int(match.group(1)) if match else 0


def find_browser(kind="chrome"):
    """Executable of the newest installed Chromium of this kind, or None."""
    if os.environ.get("SFM_CHROME"):
        return os.environ["SFM_CHROME"]
    found = [p for root in SEARCH if root for pattern in PATTERNS[kind] for p in glob.glob(os.path.join(root, pattern))]
    return max(found, key=_revision) if found else None


def launch(pw, kind="chrome"):
    """Launch Chromium headless with the found executable (or Playwright's default)."""
    path = find_browser(kind)
    return pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
