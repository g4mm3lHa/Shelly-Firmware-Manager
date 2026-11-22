# Test report 3.8.0

Final release 3.8.0 on branch `release-3.8.0` (base: `main` = `v3.7.1`), after 3.8.0-rc1 to rc3;
all three candidates were tested live through the beta channel.

## Scope

| Area | Change |
|---|---|
| Device list | sections, filter popover (Devices, Firmware, Logs), Removed devices with "Remove from list", "No usable address", read-only "Other Shellys", battery source, optional column "Host name" |
| Settings | option "Resolve host names" (Advanced, off by default); Gen1 type list removed |
| Firmware store | files on disk read back at the start (SHA-256); card "Unknown Files"; unknown files are no longer deleted automatically |
| Fixes | 401 after the app was in the background; header checkbox alignment; Bluetooth duplicates of listed Shellys |
| Beta mark | pre-release versions show "(beta)" next to the title |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.8.0, pytest, UI, layout gate | passed except one check: 447 pytest; ui_test 210/210; ui_features 810/811; layout gate 28/28 |
| Failing check | `store_transfer`, 360-light-embedded, "password not cut off": fails in the full run (3 runs), passes alone and in the quick run; known flake, kept as is by the maintainer; not narrowed down |
| CVE check | 2026-11-22: PyPI lists no known vulnerability for the ten pinned packages; base image digest unchanged, 3.12.16 is the newest 3.12 alpine tag |
| Docs check (seven points) | changelog bullets match `DOCS.md`; options in `config.yaml`, `translations/en.yaml`, Settings tab and Configuration agree; feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt`; relative links and anchors resolve; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | `qa/screenshots/3.8.0/` (1440 and 412 px: Devices, Firmware, Logs, Settings, details); `docs/images/` Devices and Firmware replaced |

## Live tests through the beta channel

- rc1 (steps 1-7), rc2 and rc3 tested in Home Assistant by the maintainer (2026-11-21/22): passed.
  The card "Unknown Files" was not tested live.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly hardware, non-Chromium browsers; HA battery
  sensor detection and manufacturer texts of other Shelly kinds; Watchdog restart; item 12 (one download
  for parallel checks); beta channel with a missing image.
