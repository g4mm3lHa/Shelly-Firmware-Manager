# Test report 3.10.0

Final release 3.10.0 on branch `release-3.10.0` (base: `main` = `v3.9.0`), after 3.10.0-rc1
and rc2, both tested live through the beta channel. Changes after rc2: the fixes of the
code review before the release (including the display of a running downgrade), docs.

## Scope

| Area | Change |
|---|---|
| Installation | "Install at" (once, at most 7 days ahead, HA time zone), scheduled installation in the device list, lapse rules, one notification; downgrade blocked while scheduled |
| Beta firmware | option (Gen2+, off by default), section "Beta Updates", tick, withdrawal at start |
| Schedules | tab, dialog, runs (restart, cloud, Bluetooth), left-out devices, missed runs, "Run now", log and notification |
| Language | English and German UI per browser, German Configuration tab |
| Review fixes | running downgrade keeps its section and names its target; scheduled installation lapses for ignored or removed devices and for an unconfirmed store fallback; removed devices struck from schedules, new device IDs followed; damaged schedules dropped at load; runs log unexpected errors and an app stop; automatic check waits for a schedule run; log mark per entry; Install at dates in the HA time zone; install refused for ignored or reloading devices; German Status column wide enough for "Installieren" |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.10.0, pytest, UI, layout gate | passed: 553 pytest; ui_test 210/210; ui_features 959/959 (new suite `schedules` 112/112: Schedules tab, Install at, scheduled device, running downgrade, German layout at 360–1440 px); layout gate 35/35 (now with the Schedules tab) |
| CVE check | 2026-11-22: PyPI lists no known vulnerability for the ten pinned packages (all at their newest version, unchanged since 3.9.0); base image digest unchanged, 3.12.16-alpine3.24 is the newest 3.12 alpine tag |
| Docs check (seven points) | changelog bullets match `DOCS.md` (Scheduled installation, Beta firmware, Downgrade, Schedules, Logs, Settings, Troubleshooting, Known limitations); options in `config.yaml`, `translations/en.yaml`, Settings tab and Configuration agree (no option change after rc2); feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt` (unchanged); relative links and anchors resolve; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | `qa/screenshots/3.10.0/` (1440 and 412 px: Devices, Firmware, Schedules, Logs, Settings, details); `docs/images/` replaced (Devices desktop and phone with the Schedules tab, installation dialog with "Start") |

## Live tests through the beta channel

- rc1 and rc2 tested in Home Assistant by the maintainer (2026-11-22): passed, except the
  display of a running downgrade (fixed in the final, covered by tests and the UI suite).
- The other review fixes are covered by tests only.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly hardware, non-Chromium browsers;
  installation of a real beta firmware (no beta offered by Shelly during the tests);
  Watchdog restart; item 12 (one download for parallel checks).
