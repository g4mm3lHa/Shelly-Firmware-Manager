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
| `tls.py` | certificate pinning for `*.shelly.cloud` | `CertificatePins.ssl_for`, `explain_tls_error` |

### The manager

One object, built from layers: each module holds one class that extends the class of
the layer before, so a layer only uses what lies below it. Everything outside uses
`manager.Manager` only. Module constants live where they are used (tests patch them
there, e.g. `manager_installs.INSTALL_WAIT_S`).

| Layer / module | Class | Content |
|---|---|---|
| 1 `manager_core.py` | `ManagerCore` | state and `__init__`; persistence `_load`, `save` (atomic), `set_ui`, `_remove_partial_downloads`; Home Assistant `_registry`, `notify`, `_supervisor_post`; options `options_view`, `save_options`, `restart_app`, `download_base_issue`, `confirm_download_base`; check slots `checking` (full check only), `device_checks` (single checks: id → queued/running), `reserve_check` (synchronous; "all" exclusive, "device" refused for the full check and for a device being checked, installed or reloaded), `_reserved`, `busy`, `_progress`, `_end_check`, `_end_device_check`, `_release_check_state` (shared caches end with the last check); credentials `_auth`, `_auth_for`. Constants: `STATUS_*`, `ACTIVITY_*`, `SOURCE_*`, `DOWNLOAD_BASE_MESSAGES`, `severity_of`; `PENDING`, `holds_file` (ready, installing, failed installation to retry or `store_fallback`); runtime `_scan_user` / `_check_users` (user of the full / single checks and their downloads), `_header_check`, `_header_got`, `_step_checks` |
| 2 `manager_store.py` | `FirmwareStore` | `_register_firmware`, `cleanup_firmware` (housekeeping only), `_intact` (SHA-256 verified once per file and check), `_stored_firmware` (exactly the offered version; uploads too for Gen2+), `_store_offer` (next higher stored version, downloads and uploads: Gen1 store first, Gen2+ store fallback), `firmware_list`, `firmware_changelog`, `store_size`, `delete_firmware` (`_delete_lock`: all files during the full check, files of a checked device's type during a single check, a file being installed; `_release_file`: holding devices lose the provision, log `released`); downgrade `downgrade_versions/match/blocker`, `_downgrade_candidates`, `_downgrade_count`; upload `stage_upload` → `confirm_upload` / `cancel_upload`, `_read_manifest`, `upload_types` |
| 3 `manager_devices.py` | `DeviceSettings` | `_apply_info`, `_read_settings`, `_settings_device` (guards), `refresh_settings`, `reload_device`, `set_ignored`, `change_settings` (read-back, `_not_applied` → warning and failed log entry), `restart_device`, `set_device_password` → `_password_action` (verify on the device) |
| 4 `manager_checks.py` | `Checks` | `scan` (full check under `lock`; scheduled and not startable → `_skip_scheduled`: logged, not repeated) / `check_device` (no lock: parallel to other devices; `SCHEDULE_USER`) → `_scan_all` → `_check_entries` (TaskGroup, `CHECK_PARALLEL`) → `_check_entry` → `_scan_device`: Gen1 store first (`_store_offer`, `_provide_stored`), else `_manufacturer_offer` (one list: `GEN1_OFFERS`); Gen2+ `_step_offer` (shared request per firmware ID and installed version; `_newest_version` → `step_target` for intermediate versions; server error → `_store_fallback`: `store_fallback` + `STORE_FALLBACK_HINT`), reference request `_start_header_check` / `_finish_header_check` (shared by running checks; returns the ignored flag for `_log_check`; `HEADER_CHECK`, `HEADER_WARNING`); `_provide` per (type, version) (single flight) → `_stored_firmware` or `shelly.download_firmware`; `_offer_source`, `OFFER_*` (source of the provided update); `_log_check`, `_store_changelogs` / `_attach_changelogs`, `changelog_for`, `_notify_new_updates`, `scan_due_in` |
| 5 `manager_installs.py` | `Installs` | `approval_matches`, `install` → `_install_approved` (slots, log) → `_install` (pre-check, WebSocket `_update_over_websocket` or HTTP `trigger_update`) → `_await_target_version`: OTA events `_await_over_events` (`OTA_*` timings: stall, restart, boot; `ota_phase` "restarting"; `_wait_ota`, `_poll_version`, `_confirm`) or `_await_by_polling` (`INSTALL_*` timings, 5 min; also when the WebSocket ends before applied); `_ota_error_text`: rejected after a download or not reachable, failed installation → `install_failed` (retry via `approval_matches`; also for `store_fallback`, entry flag `unverified`), `_after_downgrade`; confirmed intermediate version → `_step_checks` → `_check_step` at once (`check_device(trigger="step")`, otherwise no check follows an installation); refused while the device itself is checked; download server support `may_download`, `record_download`, `_downloaded`, `_reset_download`, `_install_diagnosis` |
| 6 `manager.py` | `Manager` | `public_state` (only listed fields; per device `downgrades`) |

## Stored data (`/data`, private to the app)

| File | Content |
|---|---|
| `state.json` | `{devices, last_scan, firmware, ui, ui_version (3), schedule, download_base_confirmed}` |
| `firmware/` | `<sha256>.<zip\|bin\|gbl>`; temporary `.part-*` (downloads, staged uploads) |
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

## HTTP API (`web_ui.py`, Ingress only; writes need `X-CSRF-Token` + JSON ≤ 32 KB)

| Route | Handler → Manager |
|---|---|
| `GET /` | page (CSRF token, asset version) |
| `GET /api/state` | `public_state` (ETag / 304) |
| `POST /api/scan` | `reserve_check` + `scan` (background) |
| `POST /api/ui` | `set_ui` |
| `POST /api/devices/{id}/check` | `reserve_check("device")` + `check_device` |
| `POST /api/devices/{id}/install` | `approval_matches` / `downgrade_*` + `install` (`confirm`, `sha256`, downgrade: `downgrade` + `acknowledged`) |
| `GET /api/devices/{id}/downgrades` | `downgrade_versions`, `downgrade_blocker` |
| `GET /api/devices/{id}/changelog` | `changelog_for` |
| `POST /api/devices/{id}/settings/refresh` | `refresh_settings` |
| `POST /api/devices/{id}/settings` | `change_settings` (answer: `verified`, `not_applied`) |
| `POST /api/devices/{id}/restart` | `restart_device` |
| `POST /api/devices/{id}/password` | `set_device_password` (`mode`, `confirm`, `acknowledged`) |
| `POST /api/devices/{id}/ignore` | `set_ignored` |
| `POST /api/devices/{id}/reload` | `reload_device` |
| `GET /api/firmware` | `firmware_list` (per file `devices`: `id`, `name` of the devices it is provided for; `changelog` flag), `store_size` |
| `GET /api/firmware/{name}/changelog` | `firmware_changelog` (stored changelog of this file, no request) |
| `POST /api/firmware/upload` | `stage_upload` (octet-stream, ≤ 64 MiB, streamed – the only non-JSON route) |
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
| `core.js` | shared state `ui`, `selected`, `openDetails`; HA theme; `api`, `getState`, `uploadFile`; DOM helpers `$`, `el`, `box`, `icon`, `iconButton`, `valueList` (no empty rows), `section` (titled dialog block), `shaRow`, `fmt`; tooltip (`attachTip`: mouse hover and keyboard focus; `tap` = touch/pen tap toggles it for nodes without an action, touch detected by the last `pointerdown` type; a tap on a disabled button shows its title; `hideTip`); `webUrl`; `modal` (optional title icon), `message`, `toast` |
| `devices.js` | status (`STATUS_LABELS`, `shownStatus`, `installedRecently`, `failedInstall`, `installable`, `installBlocker`, `statusCell`, `installButton`, `showStatus`), action icons (`aopButton`, `ACTION_ITEMS` incl. websocket debug log, `actionButton`, `notePopup` of greyed-out icons), table / phone tiles (`COLUMNS`, `renderDevices`, column chooser), row menu (`openRowMenu`: blocked by the full check or work on this device); `shownUpdates` keeps devices in the updates card while checked |
| `install.js` | `openInstall` (source of the update, changelog), `openDowngrade`, `changelogNodes`, `OFFER_SOURCES` |
| `device-settings.js` | dialogs `editValue`, `editAop`, `editEco`, `editSwitch`, `editDebug`, `editPassword`, `restartDevice`; `featureStrip` (status strip of the action popups), `aopTone`, `aopSummary` |
| `firmware.js` | `renderFirmware`, `firmwareRow`, `firmwareDevice` (Install button per device), `openFirmwareChangelog`, `deleteFirmware`, `openUpload` |
| `logs.js` | `renderLogs` (grouping per device), `logContent` (cancelled scheduled check: `skipped`), `checkFindings`, `deleteLogEntry`, "Delete all logs" |
| `app-settings.js` | `renderSettings`, `OPTION_FIELDS` (help text as tooltip of the name and its i-icon, visually hidden description), hints for the download base URL |
| `main.js` | entry: `render`, header (`renderMeta`, `renderProgress`), download base URL notice (`renderNotice`), tabs (`showView`), polling (`load`) |

## Tests (`tests/`, all network mocked; fixtures in `conftest.py`, helpers in `helpers.py`)

| File | Covers |
|---|---|
| `test_check.py` | full/single check, reservation, progress, single flight, cancellation, ignored, Reload Device Info, log severity, Gen2+ server first (installed version sent, stored file of the offered version, intermediate versions and their store marker, store fallback, reference request, SHA-256 of the URL), Gen1 store first and list, changelog kept with the firmware, manufacturer server access, user of checks and downloads |
| `test_install.py` | target version verification, OTA WebSocket channel, HTTP fallback, download diagnosis, no check after installations except after an intermediate version, store fallback installation, "Updated" mark (also unconfirmed), failed installation and retry, `ota_error` text, limited warnings for rejected downloads |
| `test_firmware_store.py` | store housekeeping, delete, downgrade, upload |
| `test_device_settings.py` | device identity (incomplete answers), settings/capabilities, Gen1 light endpoints, not-applied values, AP/BLE, websocket debug log, password workflow, no secrets |
| `test_schedule.py` | schedule, DST, catch-up, deferral |
| `test_options.py` | option bounds, unknown keys, `download_base_url` missing/unconfirmed |
| `test_state.py` | state.json, activity log storage, entry IDs, delete/clear, credentials, UI settings |
| `test_web.py` | HTTP endpoints, CSRF, Ingress, 409s, upload/downgrade/delete over HTTP, store fallback acknowledgment, log deletion, downgrade count |
| `test_security.py` | CSP and headers, 32 KB limit, download server refusals |
| `test_lifecycle.py` | SIGTERM, parallel server stop |
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
| `ui_features.py` | 8801, 8803 legacy, 8804 missing, 8805 features | download URL hints/notice, setting help tooltips (mouse, keyboard, touch), downgrade, upload, delete, Install button, changelog icon, labels and dialog title icons in the Firmware tab, failed installation in the updates card, dialog sections, phone tile layout, tooltips on touch (tap with/without action, disabled buttons), action popups with status strip, websocket debug icon, device name tooltips, user in the log tile |
| `mobile_capture.py` | 8711 idle | screenshots + overflow, full only |

`tools/dev/check.sh [tests|ui|full|build]` runs everything in order: ruff → mypy (strict,
app modules) → build check → version match → pytest → UI. Rules of ruff, mypy and pytest:
`pyproject.toml`.

App definition besides `config.yaml`: `apparmor.txt` (AppArmor profile: Python, read-only
image, writable `/data`, network), `README.md` / `DOCS.md` (Info and Documentation tab of
the app store), `translations/en.yaml`.
