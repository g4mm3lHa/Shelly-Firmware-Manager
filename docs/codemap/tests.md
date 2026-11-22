# Code map – tests and checks

Part of `docs/CODEMAP.md`; read when you change `tests/`, `qa/` or the check flow.

**Tests (`tests/`, all network mocked; fixtures in `conftest.py`, helpers in `helpers.py`)**

| File | Covers |
|---|---|
| `test_check.py` | full/single check, battery device asleep, reservation, progress, single flight, cancellation, ignored, Reload Device Info, log severity, unexpected error of one device, Gen2+ server first (installed version sent, stored file of the offered version, intermediate versions and their store marker, store fallback, reference request, SHA-256 of the URL: a stored file only with that SHA-256, whatever its source, uploads the exception, an imported file then confirmed), Gen1 store first and list (imported files only as store fallback), device texts in the notification (Markdown escaped), `plain`, `parse_json`, changelog kept with the firmware, manufacturer server access, user of checks and downloads, removed devices (probe, findings, back in HA, new device ID by MAC, Remove from list), other Shellys, battery hint of HA, registry entry without usable address, sections |
| `test_install.py` | target version verification, OTA WebSocket channel, HTTP fallback, download diagnosis, no check after installations except after an intermediate version, store fallback installation, "Updated" mark (also unconfirmed), failed installation and retry (also after an unexpected error), `ota_error` text, limited warnings for rejected downloads |
| `test_firmware_store.py` | store housekeeping, total size and free space, delete, downgrade (also refused while `allow_downgrade` is off), upload, files read back from the disk (adopted / unknown, only for a matching offer), deleting unknown files |
| `test_store_transfer.py` | store export/import: layout, roundtrip (date, changelog; every file stored as `import`), a sealed foreign file never replaces the manufacturer's file (also at the first start), re-packed archive, changed file / manifest / wrong password / seal that is no hex text, same (source never changes) / conflict, Gen1 always, damaged files left out, token single use and expiry, limits, refused during a check, no check while an import writes, export refused without free space, first-start question and its notification, import and upload in parts (out of order, unknown transfer, expiry) |
| `test_device_settings.py` | device identity (incomplete answers), settings/capabilities, Gen1 light endpoints, not-applied values, AP/BLE, websocket debug log, battery device detection, password workflow (also without storing), no secrets, passwords of several devices (enable/change per device, parallel limit, skipped and failed devices, store, validation) |
| `test_schedule.py` | schedule, DST, catch-up, deferral |
| `test_options.py` | option bounds, unknown keys, `download_base_url` missing/unconfirmed/public IP/invalid (also over HTTP in `test_web.py`), `allow_downgrade` optional, `import_max_mb` |
| `test_state.py` | state.json (backup, damaged or missing state file, no readable state keeps the firmware files), store housekeeping during downloads, activity log storage, entry IDs, delete/clear, credentials (write error) |
| `test_web.py` | HTTP endpoints, CSRF, Ingress, 409s, upload/downgrade/delete over HTTP, store fallback acknowledgment, log deletion, downgrade count, store export/import routes (password once, single-use download, size limit), first-start skip, bulk password validation, upload/import in parts over HTTP (headers, oversized parts and totals), Remove from list, unknown files over HTTP, beta mark only for a pre-release |
| `test_security.py` | CSP and headers (also on refusals and unexpected errors), odd requests as client errors (non-ASCII CSRF token, unknown charset, JSON nested too deeply), non-ASCII transfer tokens, 32 KB limit, IDs from the URL, download server refusals |
| `test_guards.py` | pure unit tests of the protective checks: device addresses (`LAN_NETWORKS`, `resolve_device_origin`), `DeviceAuth` (Basic for Gen1 only, Digest SHA-256 without downgrade, realm binding), certificate pinning, manufacturer URLs, version order, strict patterns, `download_base_url` (case list `download_url_cases.json` shared with the UI check, allow-list), ZIP directory limit, `AddressError` reasons, `host_name_of`, `local_host_name`, battery sensors of the entity registry |
| `test_lifecycle.py` | SIGTERM, parallel server stop, fresh installation waits for the first-start answer |
| `test_changelog.py` | page fetch limit, changelog of one version, display between versions, parsing off the event loop |
| `test_build.py` | built UI files match the sources in `ui-src` (hash over all modules) |

`conftest.py`: `net` (FakeNet: registry, devices A/B/C, offers, downloads; all `/data`
paths in tmp via `data_paths`), `mgr`, `client` (UI test client with CSRF token),
`quick_install` (short installation timings).

# UI checks (`qa/`, Playwright against `demo.py`)

| Script | Demo(s) | Use |
|---|---|---|
| `run_ui.py quick\|full` | starts all demos | one command; prints one line per suite |
| `ui_test.py [--full]` | 8801 idle, 8802 busy | layout, menus, dialogs, contrast; quick = 5 cases, full = 13 |
| `ui_features.py [--full] [suite]` (runner; checks in `qa/features/`: `base.py` shared helpers, `setting_help.py`, `download_url.py`, `store_transfer.py`; suite firmware_store = `firmware_store.py` + areas `devices.py`, `firmware_tab.py`, `logs.py`) | 8801, 8803 legacy, 8804 missing, 8805 features, 8806 first | firmware_store in 2 viewports (360 px embedded, 1440 px dark), `--full` 4; toolbar tools (search/sort/filter popovers, Escape, click outside, dot), filter entries of Devices and Firmware (Unused blocked by an unknown firmware ID; helpers `filter_entries`, `pick_filter`), device sections in order, Other Shellys card and filter, Removed pill tooltips and "Remove from list" (removes only in the last case), "No usable address", hidden Host name column, beta mark of the pre-release demo, header checkbox alignment, free space, phone header and tile lines; download URL hints/notice/suggestion (empty field filled from a private LAN browser address, via a route that serves demo 8804 under that host), setting help tooltips (mouse, keyboard, touch), downgrade, upload, delete, Install button, changelog icon, labels (intermediate tooltip), table head / phone labels, upload button icon and dialog title icons in the Firmware tab, centred Devices table columns, failed installation in the updates card, dialog sections, phone tile layout, tooltips on touch (tap with/without action, disabled buttons), action popups with status strip (no question, button names the action), websocket debug icon, device name tooltips, battery devices (icon after the name, tooltip with the source, Asleep pill, dimmed IP, not under Attention), user in the log tile; store_transfer: Settings cards, export (password, copy also without Clipboard API, ack, download), import (wrong password, counts, finish), device passwords dialog (steps, selection, red box, confirmation), first-start dialog / notice / Skip, a 17 MiB import sent in 3 parts |
| `tools/dev/mockup.py new\|sheet` | own demo per variant | scratch copy, build, capture 1440 / 412 / 360 embedded and one labelled contact sheet (`tools/dev/README.md`) |
| `capture.py OUT [--shot NAME=STEPS]` | own demo, free port | screenshots of chosen views at 1440 px and 412 px @2x (touch) for test reports and mockups (`--root` = scratch copy); steps tab/click/tap/hover/fill/wait/full/tall, `--route` for fake device answers, `--long-data`, `--embedded`; every shot checked for page errors and horizontal overflow. `--gate` (full only): 7 viewports × Devices/Firmware/Settings/unsaved with long data |

`tools/dev/check.sh [tests|ui|full|build]` runs everything in order: ruff → mypy (strict,
app modules) → build check → version match → pytest → UI. Rules of ruff, mypy and pytest:
`pyproject.toml`. `tools/dev/bump.py X.Y.Z[-rcN]` sets the version (`config.yaml`,
`main.py`) and, for a release candidate, the empty changelog block.

App definition besides `config.yaml`: `apparmor.txt` (AppArmor profile: Python, read-only
image, writable `/data`, network), `README.md` / `DOCS.md` (Info and Documentation tab of
the app store), `translations/en.yaml`.
