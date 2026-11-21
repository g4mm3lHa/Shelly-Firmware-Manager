"""Feature UI checks (Playwright) against the demo servers – one runner for all
feature suites; the checks live in qa/features/ (base.py: shared helpers; one module per area).

Suites, in this order (the last two change the demo state in their last case):
- download_url_hint   Settings: hints for download_base_url (LAN IP, public IP,
                      localhost, host names). Demo "idle" on 8801.
- setting_help        Settings: help texts as tooltips of the setting name and its
                      i-icon (mouse, keyboard, touch). Demo "idle" on 8801.
- download_url_notice notice for a missing / unconfirmed download base URL, device
                      progress of a Gen2+ installation. Demos "legacy" 8803, "missing" 8804.
- firmware_store      downgrade from the device menu, upload, two-step delete, the
                      Install button behind a device and the changelog icon in the
                      Firmware tab; 3.1.1: Install button and "Updated" in the device
                      list, state colours of the action icons, log grouping and
                      deleting log entries; 3.2.0: failed installation in the updates
                      card, dialog sections, phone tile layout, no tooltip after a tap,
                      user in the log tile; devices stay in the updates card while
                      checked, Ignored label size on phones; action popups with status strip,
                      websocket debug icon, tooltips of the device names and tooltips on
                      touch, Firmware-tab labels and dialog title icons; sorting per
                      browser and the Firmware-tab downgrade, downgrades switched off.
                      Demo "features" on 8805 (and "idle" 8801, "missing" 8804).
- store_transfer      3.4.0: Settings block "Firmware store" and card "Device Passwords",
                      export (password once, download), import (check, counts,
                      confirm), passwords of several devices (selection, steps,
                      confirmation), first-start question with notice bar, files
                      above the Ingress limit sent in parts. Demos
                      "idle" 8801 and "first" 8806 (Skip changes it: last case only).
Modules: setting_help, download_url (hint + notice), store_transfer; firmware_store runs the area
modules devices, firmware_tab and logs. Open only the module of the area you work on.
Usage: python qa/ui_features.py <shots-dir> [--full] [suite ...]   (qa/run_ui.py starts
the demos; --full: all viewports of firmware_store, final gate)
Every suite checks: no horizontal overflow, no console errors; screenshots go to
<shots-dir>."""

import sys
from pathlib import Path

from common import launch
from features.base import SUITE, results
from features.download_url import download_url_hint, download_url_notice
from features.firmware_store import firmware_store
from features.setting_help import setting_help
from features.store_transfer import store_transfer
from playwright.sync_api import sync_playwright

SUITES = {"download_url_hint": download_url_hint, "setting_help": setting_help, "download_url_notice": download_url_notice,
          "firmware_store": firmware_store, "store_transfer": store_transfer}


def main():
    """Run the selected suites (default: all, in the documented order)."""
    args = [a for a in sys.argv[1:] if a != "--full"]
    out = Path(args[0] if args else "shots")
    out.mkdir(parents=True, exist_ok=True)
    selected = args[1:] or list(SUITES)
    with sync_playwright() as pw:
        browser = launch(pw)
        for name in selected:
            SUITE[0] = name
            SUITES[name](browser, out)
        browser.close()
    failed = [r for r in results if not r[1]]
    for name in selected:
        mine = [r for r in results if r[0].startswith(name + ":")]
        print(f"{name}: {sum(r[1] for r in mine)}/{len(mine)} passed")
    for name, _ok, detail in failed:
        print("FAIL", name, detail)
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
