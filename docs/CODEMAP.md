# Code map

Orientation for changes: where things are, what is stored, which test covers what.
Names, not line numbers – search for the function name. All app modules carry type
annotations (checked with `mypy --strict`); device and store entries are plain JSON
objects (`manager_core.Device`, `manager_core.FirmwareMeta`). Keep this file in sync when
modules, stored fields, routes or test files change.

## Runtime flow

`main.py` loads `/data/options.json` → `Manager(options, session, …)` (loads all
`/data` files) → starts the UI app (port 8099, Ingress only) and the download server
(port 8098) → `run_schedule` sleeps until the next scheduled date (`schedule.py`) and
calls `Manager.scan(automatic=True)`. SIGTERM/SIGINT: cancel the scheduler, clean up
both servers in parallel (cancels checks/installations, saves the state), exit 0.

## Modules (`shelly_firmware_manager/app/`)

| Module | Responsibility | Key names |
|---|---|---|
| `main.py` | start, servers, scheduler loop, clean stop | `main`, `run_schedule`, `start_site`, `VERSION` (= config.yaml) |
| `manager*.py` | all workflows and the state: one object in six layers | see "The manager" below |
| `data_paths.py` | paths below `/data` – always read as `data_paths.NAME` (tests and the demo redirect them) | `DATA_DIR`, `STATE_FILE`, `FW_DIR`, `CREDENTIALS_FILE`, `CHANGELOG_FILE`, `EVENTS_FILE` |
| `shelly.py` | device and manufacturer access, auth, SSRF/redirect/size guards | `device_info`, `read_settings` (incl. `debug_ws`), `write_settings`, `validate_settings`, `set_protection`, `protection_status`, `restart`, `trigger_update`, `OtaChannel`, `fetch_offer` (Gen2+; with the installed version: header `Shelly-Info` {app, ver} → next step, `steps`), `url_sha256` (SHA-256 named by a download URL, checked by `download_firmware`), `fetch_gen1_offers` / `gen1_offer` (one list for all Gen1 types), `download_firmware`, `fetch_cloud_text`, `_cloud_get`, `check_download_url`, `resolve_device_origin`, `DeviceAuth`, `parse_version`, `clip`, `error_text` |
| `web_ui.py` | Ingress UI + JSON API, CSRF/JSON/size middleware | `build_ui_app` (all handlers inside), `security`, `guarded`, `SECURITY_HEADERS` |
| `download_server.py` | GET/HEAD `<sha256>.<ext>` for the Shellys; warnings for rejected requests limited per client and 10 min | `build_download_app`, `strip_server_header`, `RejectLog` (`REJECT_WINDOW_S`, `REJECT_MAX_CLIENTS`) |
| `schedule.py` | every N days at HH:MM, HA time zone, DST, catch-up | `configure`, `status`, `seconds_until`, `next_run`, `local_zone` |
| `app_options.py` | option validation, merge, log diff | `with_defaults`, `merge`, `change_list`, `LEGACY_DOWNLOAD_BASE` |
| `events.py` | activity log (JSON Lines), severity, entry IDs, delete/clear | `EventLog.add/list/remove/clear`, `worst`, `user_from`, `KINDS`, `ENTRY_ID_RE` |
| `credentials.py` | per-device passwords (0600; Gen2 HA1 only) | `Credentials.auth_for/candidate/store/remove` |
| `changelog.py` | changelog pages → plain text (parsed in a worker thread), last page per generation | `Changelogs.refresh/version_entry/between`, `marked`, `parse` |
| `store_transfer.py` | store export / import files (pure functions, run in a worker thread): ZIP layout `Gen2+\|Gen1/<type>/<file>`, `manifest.json` sealed with HMAC-SHA256 (key: scrypt of the export password), `SHA256SUMS.txt`; every entry and file checked; re-packed archives accepted | `build_export`, `read_export`, `extract_entry`, `generate_password`, `normalize_password`, `derive_key`, `archive_path`, `TransferError` (= ValueError), `FORMAT`, `SCRYPT`, `MAX_MANIFEST`, `MAX_ENTRIES` |
| `tls.py` | certificate pinning for `*.shelly.cloud` | `CertificatePins.ssl_for`, `explain_tls_error` |

### The manager

One object, built from layers: each module holds one class that extends the class of
the layer before, so a layer only uses what lies below it. Everything outside uses
`manager.Manager` only. Module constants live where they are used (tests patch them
there, e.g. `manager_installs.INSTALL_WAIT_S`).

| Layer / module | Class | Content |
|---|---|---|
| 1 `manager_core.py` | `ManagerCore` | state and `__init__` (options incl. `allow_downgrade`, missing = off); persistence `_load`, `save` (atomic), `_remove_partial_downloads`; Home Assistant `_registry`, `notify`, `_supervisor_post`; options `options_view`, `save_options`, `restart_app`, `download_base_issue`, `confirm_download_base`; check slots `checking` (full check only), `device_checks` (single checks: id → queued/running), `reserve_check` (synchronous; "all" exclusive, "device" refused for the full check and for a device being checked, installed or reloaded), `_reserved`, `busy`, `_progress`, `_end_check`, `_end_device_check`, `_release_check_state` (shared caches end with the last check); credentials `_auth`, `_auth_for`. Constants: `STATUS_*`, `ACTIVITY_*`, `SOURCE_*`, `DOWNLOAD_BASE_MESSAGES`, `severity_of`; `PENDING`, `holds_file` (ready, installing, failed installation to retry or `store_fallback`); runtime `_scan_user` / `_check_users` (user of the full / single checks and their downloads), `_header_check`, `_header_got`, `_step_checks`; store transfer `import_max` (bytes of `import_max_mb`), `_export`, `_import`, `_import_lock`; first start `first_start_open` (no state.json at start = fresh installation; `reserve_check` raises `FIRST_START_BLOCK` while open), `first_start_notified` |
| 2 `manager_store.py` | `FirmwareStore` | `_register_firmware`, `cleanup_firmware` (housekeeping only), `_intact` (SHA-256 verified once per file and check), `_stored_firmware` (exactly the offered version; uploads too for Gen2+), `_store_offer` (next higher stored version, downloads and uploads: Gen1 store first, Gen2+ store fallback), `firmware_list`, `firmware_changelog`, `store_size`, `delete_firmware` (`_delete_lock`: all files during the full check, files of a checked device's type during a single check, a file being installed; `_release_file`: holding devices lose the provision, log `released`); downgrade `downgrade_versions/match/blocker` (`DOWNGRADE_OFF` while `allow_downgrade` is off), `_downgrade_candidates`, `_downgrade_count`; transfers in parts `_receive_part` (`PART_MAX` 8 MiB per request – Ingress refuses bodies above 16 MiB; first part starts a transfer, further parts name it and continue at the received size; `PART_TTL_S`; one incomplete transfer per kind in `_receiving`); upload `stage_upload_part` (`stage_upload` = one part) → `confirm_upload` / `cancel_upload`, `_read_manifest`, `_sha256_of`, `upload_types`; store export `prepare_export` (password once, key in memory, `EXPORT_TTL_S`) → `build_export` (single-use token, temporary `EXPORT_PREFIX` file, log `export`); store import `stage_import_part` (`stage_import` = one part; ≤ `import_max`, free space `SPACE_MARGIN`) → `check_import` (`_classify`: new / same / conflict, `_merge_note`) → `confirm_import` (refused during checks; `.part-` + rename; merges missing data; answers the first-start question; log `import`) / `cancel_import`; `skip_first_start` (log `setup`), `announce_first_start` (one HA notification) |
| 3 `manager_devices.py` | `DeviceSettings` | `_apply_info`, `_read_settings`, `_settings_device` (guards), `refresh_settings`, `reload_device`, `set_ignored`, `change_settings` (read-back, `_not_applied` → warning and failed log entry), `restart_device`, `set_device_password` → `_password_action` (verify on the device; `keep=False`: enable/change without storing, a stored password is removed after the device confirmed); several devices `set_passwords` (`BULK_MODES` store/set, `bulk_password_step`: set → enable or change, skipped with a reason; parallel up to `max_parallel_installs`, each device through `set_device_password`) |
| 4 `manager_checks.py` | `Checks` | `scan` (full check under `lock`; scheduled and not startable → `_skip_scheduled`: logged, not repeated) / `check_device` (no lock: parallel to other devices; `SCHEDULE_USER`) → `_scan_all` → `_check_entries` (TaskGroup, `CHECK_PARALLEL`) → `_check_entry` → `_scan_device`: Gen1 store first (`_store_offer`, `_provide_stored`), else `_manufacturer_offer` (one list: `GEN1_OFFERS`); Gen2+ `_step_offer` (shared request per firmware ID and installed version; `_newest_version` → `step_target` for intermediate versions; server error → `_store_fallback`: `store_fallback` + `STORE_FALLBACK_HINT`), reference request `_start_header_check` / `_finish_header_check` (shared by running checks; returns the ignored flag for `_log_check`; `HEADER_CHECK`, `HEADER_WARNING`); `_provide` per (type, version) (single flight) → `_stored_firmware` or `shelly.download_firmware`; `_offer_source`, `OFFER_*` (source of the provided update); `_log_check`, `_store_changelogs` / `_attach_changelogs`, `changelog_for`, `_notify_new_updates`, `scan_due_in` |
| 5 `manager_installs.py` | `Installs` | `approval_matches`, `install` → `_install_approved` (slots, log) → `_install` (pre-check, WebSocket `_update_over_websocket` or HTTP `trigger_update`) → `_await_target_version`: OTA events `_await_over_events` (`OTA_*` timings: stall, restart, boot; `ota_phase` "restarting"; `_wait_ota`, `_poll_version`, `_confirm`) or `_await_by_polling` (`INSTALL_*` timings, 5 min; also when the WebSocket ends before applied); `_ota_error_text`: rejected after a download or not reachable, failed installation → `install_failed` (retry via `approval_matches`; also for `store_fallback`, entry flag `unverified`), `_after_downgrade`; confirmed intermediate version → `_step_checks` → `_check_step` at once (`check_device(trigger="step")`, otherwise no check follows an installation); refused while the device itself is checked; download server support `may_download`, `record_download`, `_downloaded`, `_reset_download`, `_install_diagnosis` |
| 6 `manager.py` | `Manager` | `public_state` (only listed fields; per device `downgrades`; `allow_downgrade`, `first_start_open`, `import_max_mb`) |

## Stored data (`/data`, private to the app)

| File | Content |
|---|---|
| `state.json` | `{devices, last_scan, firmware, schedule, download_base_confirmed, first_start_open, first_start_notified}` (`ui`/`ui_version` of versions before 3.3.0 are ignored and dropped at the next save) |
| `firmware/` | `<sha256>.<zip\|bin\|gbl>`; temporary `.part-*` (downloads, staged uploads, staged store imports and exports; removed at start) |
| `events.json` | activity log, one JSON object per line with `id` (3.0.0: one JSON array, converted once; IDs added once by 3.1.1) |
| `credentials.json` | `{device id: {"gen": 1, "password"} \| {"gen": 2, "realm", "ha1"}}` |
| `changelog.json` | last fetched page per generation: `{"1"\|"2": {"fetched", "versions": {version: {"date", "entries"}}}}` |

Device entry (`state.devices[<HA device id>]`): identity `name`, `shelly_name`,
`gen`, `type` (firmware ID / app), `model`, `mac`, `shelly_id` (Gen2+ realm),
`origin` (`http://<private ip>:<port>`), `version`, `build`; check `status`
(`up-to-date|available|ready|installing|unconfirmed|error|unregistered|skipped`),
`message`, `error_level`, `last_check`, `last_success`, `last_update`,
`info_refreshed`, `offer_version`, `notified_version`; provided update `file`,
`sha256`, `size`, `pending_version`, `pending_build`, `source_url`, `offer_source`
(`manufacturer|store|upload`); settings `ntp`,
`eco`, `ap`, `ble`, `debug_ws` (websocket debug log, Gen1 unsupported), `outputs`, `actions`, `schedules`, `auth_enabled`,
`settings_error`; flags `ignored`; runtime only (not trusted after a restart)
`ota_progress`, `ota_phase` ("restarting"), `downgrade`; `updated` (confirmed or unconfirmed installation, until the next full
check or single check of the device), `install_failed` (failed installation, not a downgrade: file stays
provided for a retry, same lifetime as `updated`), `store_fallback` (Gen2+: server not reachable,
newer stored version offered for a manual installation; cleared by the next check of the device),
`step_target` (Gen2+: provided version is an intermediate version; newest version or ""). Memory only: `Manager.activity`, `progress`,
`installing`, `reloading`, `device_checks`, `downloads`, `_upload`, `_scan_user` / `_check_users` / `_verifying` /
`_fresh` (running checks only).

Firmware entry (`state.firmware[<file name>]`): `type`, `gen`, `version`, `sha256`,
`size`, `downloaded`, `https` (downloads), `source` (`download|upload`),
`sha_checked` (uploads), `changelog` (`{"date", "entries"}` of this version, set once
the changelog page lists it; never requested again), `intermediate` (true once a check
provided the file as an intermediate version; never set for older files).

## HTTP API (`web_ui.py`, Ingress only; writes need `X-CSRF-Token` + JSON ≤ 32 KB; binary bodies only on `BINARY_ROUTES`)

| Route | Handler → Manager |
|---|---|
| `GET /` | page (CSRF token, asset version) |
| `GET /api/state` | `public_state` (ETag / 304) |
| `POST /api/scan` | `reserve_check` + `scan` (background) |
| `POST /api/devices/{id}/check` | `reserve_check("device")` + `check_device` |
| `POST /api/devices/{id}/install` | `approval_matches` / `downgrade_*` + `install` (`confirm`, `sha256`, downgrade: `downgrade` + `acknowledged`) |
| `GET /api/devices/{id}/downgrades` | `downgrade_versions`, `downgrade_blocker` |
| `GET /api/devices/{id}/changelog` | `changelog_for` |
| `POST /api/devices/{id}/settings/refresh` | `refresh_settings` |
| `POST /api/devices/{id}/settings` | `change_settings` (answer: `verified`, `not_applied`) |
| `POST /api/devices/{id}/restart` | `restart_device` |
| `POST /api/devices/{id}/password` | `set_device_password` (`mode`, `confirm`, `acknowledged`, `keep`) |
| `POST /api/devices/passwords` | `set_passwords` (`mode` store/set, `devices`, `password`, `confirm`, `acknowledged`, `keep`; result per device, never the password) |
| `POST /api/devices/{id}/ignore` | `set_ignored` |
| `POST /api/devices/{id}/reload` | `reload_device` |
| `GET /api/firmware` | `firmware_list` (per file `devices`: `id`, `name` of the devices it is provided for; `changelog` flag), `store_size` |
| `GET /api/firmware/{name}/changelog` | `firmware_changelog` (stored changelog of this file, no request) |
| `POST /api/firmware/upload` | `stage_upload_part` (octet-stream, ≤ 64 MiB in all, streamed; parts ≤ 8 MiB with `X-Upload-Total`, `X-Upload-Offset`, `X-Upload-Transfer` – `part_headers`) |
| `POST /api/firmware/export` | `prepare_export` (generated password returned once, token) |
| `GET /api/firmware/export/{token}` | `build_export` → ZIP stream (single use; temporary file deleted after sending) |
| `POST /api/firmware/import` | `stage_import_part` (octet-stream, ≤ `import_max_mb` in all, parts like the upload) |
| `POST /api/firmware/import/check` · `/confirm` · `/cancel` | `check_import` (`token`, `password`) · `confirm_import` · `cancel_import` |
| `POST /api/first-start/skip` | `skip_first_start` |
| `POST /api/firmware/upload/confirm` · `/cancel` | `confirm_upload` · `cancel_upload` |
| `GET /api/firmware/{name}/download` | file response |
| `POST /api/firmware/{name}/delete` | `delete_firmware` (`confirm` + `acknowledged`) |
| `GET /api/logs` | `events.list` |
| `POST /api/logs/{id}/delete` · `/api/logs/clear` | `events.remove` · `events.clear` (`confirm`) |
| `GET` · `POST /api/settings` | `options_view` · `save_options` + restart |
| `POST /api/settings/download-base/confirm` | `confirm_download_base` |
| `GET /static/…` | `app.js`, `style.css` (immutable cache with `?v=`) |

Download server (port 8098): `GET|HEAD /<sha256>.<zip|bin|gbl>` → `may_download`,
`record_download` (GET only).

## UI (`ui-src/js/*.js`, `ui-src/style.css` in the repository root → built into `shelly_firmware_manager/app/static/`)

Single page, `textContent` only. Polls `api/state` (30 s idle, 3 s busy, paused when
hidden, ETag). ES modules, bundled by `tools/build_ui.py` (esbuild, entry `main.js`)
into the one classic script `app/static/app.js`; never edit `app/static/app.js|style.css`
by hand. A module exports only what another module imports.

| Module | Content |
|---|---|
| `core.js` | shared state `ui`, `selected`, `openDetails`; HA theme; `api`, `getState`, `uploadFile` (path: upload or store import; sends parts of `UPLOAD_PART` 8 MiB with progress callback); per-browser settings `loadLocal`/`saveLocal` (localStorage, prefix `shelly-firmware-manager:`); `sortControl` (toolbar select + direction button); DOM helpers `$`, `el`, `box`, `icon`, `iconButton`, `valueList` (no empty rows), `section` (titled dialog block), `shaRow`, `fmt`; tooltip (`attachTip`: mouse hover and keyboard focus; `tap` = touch/pen tap toggles it for nodes without an action, touch detected by the last `pointerdown` type; a tap on a disabled button shows its title; `hideTip`); `webUrl`; `modal` (optional title icon), `message` / `messageBody` (kinds info, success, warning, error: round status icon except info), `toast` |
| `devices.js` | status (`STATUS_LABELS`, `shownStatus`, `installedRecently`, `failedInstall`, `installable`, `installBlocker`, `statusCell`, `installButton`, `showStatus`), action icons (`aopButton`, `ACTION_ITEMS` incl. websocket debug log, `actionButton`, `notePopup` of greyed-out icons), table / phone tiles (`COLUMNS`, `renderDevices`, column chooser, `loadPrefs`: sort order / hidden / column order per browser, toolbar `devSort` coupled with the column heads), row menu (`openRowMenu`: blocked by the full check or work on this device; Downgrade disabled with `DOWNGRADE_OFF` while the option is off); `shownUpdates` keeps devices in the updates card while checked |
| `install.js` | `openInstall` (source of the update, changelog), `openDowngrade`, `downgradeConfirm` (also used by the Firmware tab), `DOWNGRADE_WARNING`, `DOWNGRADE_OFF`, `changelogNodes`, `OFFER_SOURCES` |
| `device-settings.js` | dialogs `editValue`, `editAop`, `editEco`, `editSwitch`, `editDebug`, `editPassword` ("Store in this app" for enable/change, storage note, HA reminder in the result), `restartDevice`; `bulkPasswords` → `bulkConfirm` → `bulkResult` (several devices, `bulkStep` mirrors the server check); `featureStrip` (status strip of the action popups), `aopTone`, `aopSummary` |
| `firmware.js` | `renderFirmware` (cards sorted by `fwSort`, per browser; column heads Version/Size/Stored coupled with the toolbar select), `firmwareRow`, `firmwareDevice` (Install button per device), `downgradeButton` / `openFirmwareDowngrade` (older Gen2+ versions → device choice → `downgradeConfirm`), `openFirmwareChangelog`, `deleteFirmware`, `openUpload` |
| `logs.js` | `renderLogs` (grouping per device; phone tiles without expansion when the details only repeat the summary), `logContent` (cancelled scheduled check: `skipped`), `checkFindings`, `deleteLogEntry`, "Delete all logs" |
| `app-settings.js` | `renderSettings`, `OPTION_FIELDS` (help text as tooltip of the name and its i-icon, visually hidden description), hints for the download base URL; cards `storeCard` (`exportDialog`: password once, ack, download link; `importDialog` → `checkImport` → `importSummary`: counts, groups expandable, conflicts open) and `passwordsCard` |
| `main.js` | entry: `render`, header (`renderMeta`, `renderProgress`), notice bar (`renderNotice`: first-start question before the download base URL), first start (`askFirstStart`: welcome dialog once per browser, `skipFirstStart`; "Check now" disabled), tabs (`showView`), polling (`load`) |

## Tests (`tests/`, all network mocked; fixtures in `conftest.py`, helpers in `helpers.py`)

| File | Covers |
|---|---|
| `test_check.py` | full/single check, reservation, progress, single flight, cancellation, ignored, Reload Device Info, log severity, Gen2+ server first (installed version sent, stored file of the offered version, intermediate versions and their store marker, store fallback, reference request, SHA-256 of the URL), Gen1 store first and list, changelog kept with the firmware, manufacturer server access, user of checks and downloads |
| `test_install.py` | target version verification, OTA WebSocket channel, HTTP fallback, download diagnosis, no check after installations except after an intermediate version, store fallback installation, "Updated" mark (also unconfirmed), failed installation and retry, `ota_error` text, limited warnings for rejected downloads |
| `test_firmware_store.py` | store housekeeping, delete, downgrade (also refused while `allow_downgrade` is off), upload |
| `test_store_transfer.py` | store export/import: layout, roundtrip (source, date, changelog), re-packed archive, changed file / manifest / wrong password, same / conflict / upload → download, Gen1 always, damaged files left out, token single use and expiry, limits, refused during a check, first-start question, import and upload in parts (out of order, unknown transfer, expiry) |
| `test_device_settings.py` | device identity (incomplete answers), settings/capabilities, Gen1 light endpoints, not-applied values, AP/BLE, websocket debug log, password workflow (also without storing), no secrets, passwords of several devices (enable/change per device, parallel limit, skipped and failed devices, store, validation) |
| `test_schedule.py` | schedule, DST, catch-up, deferral |
| `test_options.py` | option bounds, unknown keys, `download_base_url` missing/unconfirmed, `allow_downgrade` optional, `import_max_mb` |
| `test_state.py` | state.json, activity log storage, entry IDs, delete/clear, credentials |
| `test_web.py` | HTTP endpoints, CSRF, Ingress, 409s, upload/downgrade/delete over HTTP, store fallback acknowledgment, log deletion, downgrade count, store export/import routes (password once, single-use download, size limit), first-start skip, bulk password validation, upload/import in parts over HTTP (headers, oversized parts and totals) |
| `test_security.py` | CSP and headers, 32 KB limit, download server refusals |
| `test_lifecycle.py` | SIGTERM, parallel server stop, fresh installation waits for the first-start answer |
| `test_changelog.py` | page fetch limit, changelog of one version, display between versions, parsing off the event loop |
| `test_build.py` | built UI files match the sources in `ui-src` (hash over all modules) |

`conftest.py`: `net` (FakeNet: registry, devices A/B/C, offers, downloads; all `/data`
paths in tmp via `data_paths`), `mgr`, `client` (UI test client with CSRF token),
`quick_install` (short installation timings).

## UI checks (`qa/`, Playwright against `demo.py`)

| Script | Demo(s) | Use |
|---|---|---|
| `run_ui.py quick\|full` | starts all demos | one command; prints one line per suite |
| `ui_test.py [--full]` | 8801 idle, 8802 busy | layout, menus, dialogs, contrast; quick = 5 cases, full = 13 |
| `ui_features.py [--full]` | 8801, 8803 legacy, 8804 missing, 8805 features, 8806 first | firmware_store in 2 viewports (360 px embedded, 1440 px dark), `--full` 4; download URL hints/notice, setting help tooltips (mouse, keyboard, touch), downgrade, upload, delete, Install button, changelog icon, labels (intermediate tooltip), table head / phone labels, upload button icon and dialog title icons in the Firmware tab, centred Devices table columns, failed installation in the updates card, dialog sections, phone tile layout, tooltips on touch (tap with/without action, disabled buttons), action popups with status strip (no question, button names the action), websocket debug icon, device name tooltips, user in the log tile; store_transfer: Settings cards, export (password, copy, ack, download), import (wrong password, counts, finish), device passwords dialog (steps, selection, red box, confirmation), first-start dialog / notice / Skip, a 17 MiB import sent in 3 parts |
| `capture.py OUT [--shot NAME=STEPS]` | own demo, free port | screenshots of chosen views at 1440 px and 412 px @2x (touch) for test reports and mockups (`--root` = scratch copy); steps tab/click/tap/hover/fill/wait/full/tall, `--route` for fake device answers, `--long-data`, `--embedded`; every shot checked for page errors and horizontal overflow. `--gate` (full only): 7 viewports × Devices/Firmware/Settings/unsaved with long data |

`tools/dev/check.sh [tests|ui|full|build]` runs everything in order: ruff → mypy (strict,
app modules) → build check → version match → pytest → UI. Rules of ruff, mypy and pytest:
`pyproject.toml`.

App definition besides `config.yaml`: `apparmor.txt` (AppArmor profile: Python, read-only
image, writable `/data`, network), `README.md` / `DOCS.md` (Info and Documentation tab of
the app store), `translations/en.yaml`.
