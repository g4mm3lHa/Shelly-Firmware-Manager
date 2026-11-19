# Test report 3.5.0-rc1

Release candidate 1 of 3.5.0 on branch `release-3.5.0` (base: `main` = `v3.4.1`).
Texts, settings and layout; backend only for the free disk space.

## Scope

| Area | Change |
|---|---|
| Toolbar (all tabs) | search and sort as round icons (36 px desktop, 44 px phones) with a popover: `searchTool` (search input moved into the popover, dot while set, Enter closes), `sortControl` (fields + Ascending/Descending), `closeTools` (click outside, Escape); Logs: search only; phones: icons and chips in row 1, extras in row 2, popover full width, "Upload firmware" as icon |
| Filters | Devices: "Unprotected" (`auth_enabled` false), "Up-To-Date"; Firmware: All, Provided, Uploaded, Intermediate, Gen1, Unused |
| Free disk space | `FirmwareStore.free_space` (None if unreadable), `GET /api/firmware` → `free_space`; Firmware tab summary |
| Settings | cards Checks, Download Server, Firmware & Storage (store block before "Max. import size"), Logs, Advanced, Device Passwords; "Max." in front of option names (UI and `translations/en.yaml`) |
| Texts | MB instead of MiB in all messages (upload dialog, server errors, DOCS); card heads Title Case, weight 600 |
| Phones | header H1 (pills stacked left with ellipsis, "Check now" right, centred, no wrap); tiles T1 with outlined "Details" button (36 px, full width) |
| Docs | DOCS, CODEMAP, UI_DECISIONS (toolbar, filters, settings cards, phone header and tiles), CHANGELOG 3.5.0 rc1 |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh ui`: ruff, mypy --strict, build check, version 3.5.0-rc1, pytest | passed, 230 tests (new: free space, unreadable volume) |
| UI quick | ui_test 75/75; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 240/240, store_transfer 88/88 |
| New UI checks | toolbar icons (size), sort popover (open, full width on phones, direction kept per browser, Escape, click outside, coupled column heads), search (filter, dot), Devices/Firmware chips, Unprotected count, Uploaded filter, free space text, phone header, tile lines and Details button, upload icon on phones, settings card order and store block position |
| Screenshots | `qa/screenshots/3.5.0-rc1/` (1440 px and 412 px; tiles and header embedded at 412/360 px) |

One `check.sh ui` run failed with a traceback right after leftover demo servers had
been stopped; the following complete runs passed – not reproduced.

## Found and fixed during the work

- Enter in the search field reopened the popover at once (the key press reached the
  focused icon): `preventDefault` in the key handler.
- Phones (embedded): the pills sat 3 px lower than "Check now" (top margin of the meta
  line without a title above it).

## Open for the owner

- Free disk space from 1024 MB on is shown in GB (e.g. "29.9 GB free"); strictly "MB
  everywhere" would read "30617.6 MB free".
- Item 15 note (Firmware tab "For n devices" shows ▶ and ▾): not changed – no decision.
- The Details button is 36 px high as decided (before: 44 px touch target).

## Not tested here

- Real Home Assistant / Ingress, real Shelly devices, non-Chromium browsers.
