# Test report 3.9.0

Final release 3.9.0 on branch `release-3.9.0` (base: `main` = `v3.8.0`), after 3.9.0-rc1,
which was tested live through the beta channel. Changes after rc1: "Check now" hidden in
the Settings tab, docs, and the fixes of the code review before the release.

## Scope

| Area | Change |
|---|---|
| Firmware | keep installed versions (way back), offer delay with "Offer update now", Installed and Imported labels and filters |
| Devices | "N of M Shellys listed" with reasons, cloud icon with switch and filter, Bluetooth proxy hint |
| Installation | way back and Wi-Fi signal checked before installing, build date, "Restart the device first" after a failed installation |
| Security | store import: files stored as imported, acknowledgment; stored files replace a download only with the SHA-256 of the offer URL or as the user's upload (also for an update provided before); Gen1 imports only as store fallback; plain device texts in notifications; client errors instead of server errors |
| Design | tokens, field borders, control heights, Columns popover, dialog rules, texts; "Check now" hidden in Settings |
| Review fixes | provided file re-checked against the offer, Gen1 marking of failed installations, uploads not held back (Gen2+), restart first (identity before the restart, slow shutdown, only after a failure), keep installed without SHA-256 in the URL, "Offer update now" reserves the check first and is logged, robust state load |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.9.0, pytest, UI, layout gate | passed: 501 pytest; ui_test 210/210; ui_features 847/847; layout gate 28/28 |
| CVE check | 2026-11-22: PyPI lists no known vulnerability for the ten pinned packages (all at their newest version); base image digest unchanged, 3.12.16-alpine3.24 is the newest 3.12 alpine tag |
| Docs check (seven points) | changelog bullets match `DOCS.md`; options in `config.yaml`, `translations/en.yaml`, Settings tab and Configuration agree; feature list identical in both READMEs (the app README names no architectures, as before – the app store shows them); minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt`; relative links and anchors resolve; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | `qa/screenshots/3.9.0/` (1440 and 412 px: Devices, Firmware, Logs, Settings, details); `docs/images/` replaced in the docs commit (Devices desktop and phone, installation dialog) |

## Live tests through the beta channel

- rc1 (10 steps) tested in Home Assistant by the maintainer (2026-11-22): passed. Cloud on/off on
  Gen2+ without a restart; Gen1 cloud switch works; Shellys not listed checked with a disabled
  device. Step 9 (restart and install) skipped: no failed installation available.
- The review fixes and "Check now" hidden in Settings are covered by tests only.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly hardware, non-Chromium browsers; the
  restart-and-install flow on a real device; Watchdog restart; item 12 (one download for
  parallel checks); beta channel with a missing image.
