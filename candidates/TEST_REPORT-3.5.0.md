# Test report 3.5.0

Final release 3.5.0 on branch `release-3.5.0` (base: `main` = `v3.4.1`), after four
release candidates tested live through the beta channel.

## Scope

| Area | Change |
|---|---|
| Toolbar (all tabs) | search and sort as round icons with a popover (`searchTool`, `sortControl`, closed by click outside or Escape); Logs: search only; phones: icons and chips in row 1, extras in row 2, "Upload firmware" as icon |
| Filters | Devices: "Unprotected" (`auth_enabled` false), "Up-To-Date"; Firmware: All, Provided, Uploaded, Intermediate, Gen1, Unused (none while a device's firmware ID is unknown; tooltip with the count) |
| Free disk space | `FirmwareStore.free_space` (None if unreadable), `GET /api/firmware` → `free_space`; Firmware tab summary (from 1024 MB on in GB) |
| Settings | cards Checks, Download Server, Firmware & Storage (store block before "Max. import size"), Logs, Advanced, Device Passwords; "Max." in front of option names; MB in all texts; card heads Title Case, weight 600; last line links to the app page in HA (`GET /api/settings/app-page`, slug from Supervisor `/addons/self/info`, fallback `/config/apps`) |
| App configuration | `config.yaml` schema/options in Settings card order, `translations/en.yaml` names and help texts from `OPTION_FIELDS` (no changelog hint: HA does not show it) |
| Single download | `_provide` single flight shared by the full check and all single-device checks (`_provisions`); waiting devices (`_provision_waiters`) show `downloading` while the download runs |
| Battery devices | `shelly.read_settings` → `battery` (Gen2+ `wakeup_period` from `Sys.GetStatus` in checks and Reload Device Info, config value as fallback; Gen1 `sleep_mode.period` > 0), stored per device; known battery device without answer → `STATUS_ASLEEP`, `holds_file` also while asleep; UI battery icon (table, tile), grey pill "Asleep" (not under "Errors", tooltip: wake-up hint and last contact), IP dimmed |
| Logs | check entry counts and names the ignored devices (not findings); the Logs tab is fetched again when a check or another device activity ends |
| Phones | header H1 (pills stacked left, "Check now" right, centred, no wrap); tiles with HA name head and "Gen n" label, IP on the Shelly-name line, status on the firmware line, outlined "Details" button, outlines in the tile line colour |
| Firmware tab | "For n devices" with one chevron |
| Store export | password copy through a helper field inside the dialog; manual-copy fallback |
| Base image | `python:3.12.16-alpine3.24` index digest `sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89` (Dockerfile, both stages) |
| Clean-up | `closeTools` no longer exported (used in `core.js` only); QA header check measures against the title block (outside HA the title is shown) |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.5.0, pytest | passed, 238 tests |
| UI full | ui_test 210/210; ui_features 705/705 (download_url_hint 18, setting_help 26, download_url_notice 45, firmware_store 526, store_transfer 90); layout gate (capture) 28/28 |
| CVE check (2026-11-19) | ten pinned Python packages and esbuild 0.28.2 are the newest versions; PyPI lists no known vulnerability for any of them; Python 3.12.16 is the newest 3.12 image tag; base image digest updated (tag rebuilt 2026-11-12) |
| Screenshots | `qa/screenshots/3.5.0/` (1440 px and 412 px: four tabs, search and sort popover; device tiles embedded at 412/360 px) |

During the RC phase one quick UI run failed once in store_transfer (360 px embedded,
"password not cut off"); not reproduced in the following runs.

## Live tests through the beta channel

- rc1 to rc4 tested in Home Assistant: toolbar, filters, settings, phone header and
  tiles, changelog link (opens the app info page), battery detection, Logs refresh,
  Asleep tooltip, app configuration.
- Not yet tested live: one download for several devices (item "single download") –
  needs a new firmware or a new device; covered by tests only.
- Not verified on a real system: why the Clipboard API is refused in Home Assistant;
  the manual-copy fallback covers it.

## Not tested here

- Real Home Assistant / Ingress, container build, real Shelly devices, non-Chromium
  browsers.
