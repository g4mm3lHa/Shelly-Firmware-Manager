# Test report 3.5.0-rc2

Release candidate 2 of 3.5.0 on branch `release-3.5.0` (base: 3.5.0-rc1, `main` =
`v3.4.1`). Feedback from the rc1 live test, single download display, app configuration
alike the Settings tab, battery devices (detection and display only).

## Scope

| Area | Change |
|---|---|
| Single download | `_provide` single flight shared by the full check and all single-device checks (`_provisions`); waiting devices (`_provision_waiters`) show `downloading` while the download runs |
| Battery devices | `shelly.read_settings` → `battery` (Gen2+ `sys.wakeup_period` > 0, Gen1 `sleep_mode.period` > 0), stored per device; known battery device without answer → `STATUS_ASLEEP` (`ASLEEP_MESSAGE`), `holds_file` also while asleep; UI battery icon (table, tile), grey pill "Asleep" (not under "Errors"), IP dimmed |
| App configuration | `config.yaml` schema/options in Settings card order, `translations/en.yaml` names and help texts from `OPTION_FIELDS`; changelog hint at the end of the Settings tab (`GET /api/settings/app-page`, slug from Supervisor `/addons/self/info`, fallback `/config/apps`) |
| rc1 feedback | Unused chip while firmware IDs are unknown (tooltip with count); export password copy (helper field inside the dialog, manual-copy fallback); phone tile head with "Gen n" label and full-width divider; Details/feature button outline in the tile line colour |
| Firmware tab | "For n devices" with one chevron (variant B) |
| Docs | DOCS, CODEMAP, UI_DECISIONS, CHANGELOG 3.5.0 rc2 |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh ui`: ruff, mypy --strict, build check, version 3.5.0-rc2, pytest | passed, 238 tests (new: parallel single checks = one download, shared download failure, battery detection and Asleep, app configuration = Settings tab) |
| UI quick | ui_test 75/75; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 260/260, store_transfer 90/90 |
| New UI checks | Unused chip with unknown firmware IDs, export password copy, tile head and outlines, "For n devices" chevron, battery icon, Asleep pill and dimmed IP |
| Screenshots | `qa/screenshots/3.5.0-rc1/` kept |

## Open questions

- Changelog link in the Settings tab: route `/config/app/<slug>/info` in HA 2026.10 and
  the Supervisor `/addons/self/info` access for the slug not verified on a real system.
- Why the Clipboard API is refused in Home Assistant (certificate / Ingress frame) not
  verified on a real system; the manual-copy fallback covers it.
- Battery detection only verified with mocked device answers.

## Not tested here

- Real Home Assistant / Ingress, real Shelly devices, non-Chromium browsers.
