# Code map (3.1.0)

Orientation for changes: where things are, what is stored, which test covers what.
Names, not line numbers – search for the function name. All app modules carry type
annotations (checked with `mypy --strict`); device and store entries are plain JSON
objects (`manager.Device`, `manager.FirmwareMeta`). Keep this file in sync when
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
| `manager.py` | all workflows and the state (one class `Manager`) | see sections below |
| `shelly.py` | device and manufacturer access, auth, SSRF/redirect/size guards | `device_info`, `read_settings`, `write_settings`, `validate_settings`, `set_protection`, `protection_status`, `restart`, `trigger_update`, `OtaChannel`, `fetch_offer` (Gen2+), `fetch_gen1_offers` / `gen1_offer` (one list for all Gen1 types), `download_firmware`, `fetch_cloud_text`, `_cloud_get`, `check_download_url`, `resolve_device_origin`, `DeviceAuth`, `parse_version`, `clip` |
| `web_ui.py` | Ingress UI + JSON API, CSRF/JSON/size middleware | `build_ui_app` (all handlers inside), `security`, `guarded`, `SECURITY_HEADERS` |
| `download_server.py` | GET/HEAD `<sha256>.<ext>` for the Shellys | `build_download_app`, `strip_server_header` |
| `schedule.py` | every N days at HH:MM, HA time zone, DST, catch-up | `configure`, `status`, `seconds_until`, `next_run`, `local_zone` |
| `app_options.py` | option validation, merge, log diff | `with_defaults`, `merge`, `change_list`, `LEGACY_DOWNLOAD_BASE` |
| `events.py` | activity log (JSON Lines), severity | `EventLog.add/list`, `worst`, `user_from`, `KINDS` |
| `credentials.py` | per-device passwords (0600; Gen2 HA1 only) | `Credentials.auth_for/candidate/store/remove` |
| `changelog.py` | changelog pages → plain text (parsed in a worker thread), last page per generation | `Changelogs.refresh/version_entry/between`, `marked`, `parse` |
| `tls.py` | certificate pinning for `*.shelly.cloud` | `CertificatePins.ssl_for`, `explain_tls_error` |

### `Manager` by area

- **Persistence:** `_load`, `save` (atomic), `set_ui`, `_remove_partial_downloads`.
- **Check:** `reserve_check` (synchronous slot) → `scan` / `check_device` /
  `_check_after_installs` → `_scan_all` → `_check_entries` (TaskGroup,
  `CHECK_PARALLEL`) → `_check_entry` → `_scan_device`: store first – `_store_offer`
  (next higher stored version, downloads and uploads, `_intact` = SHA-256 verified
  once per file and check) → else `_manufacturer_offer` (shared request per firmware
  ID, Gen1 one list: `GEN1_OFFERS`) and `_provide` per (type, version) (single
  flight) → `_stored_firmware` or `shelly.download_firmware`; `OFFER_*` (source of
  the provided update), `_end_check`, `_log_check`, `_store_changelogs` /
  `_attach_changelogs` (changelog kept with the store entry), `changelog_for`,
  `_notify_new_updates`.
- **Install / downgrade:** `approval_matches`, `downgrade_versions/match/blocker`,
  `install` → `_install_approved` (slots, log) → `_install` (pre-check, WebSocket
  `_update_over_websocket` or HTTP `trigger_update`) → `_await_target_version`
  (`INSTALL_*` timings, `_pause` ends on `ota_error`), `_after_downgrade`,
  `_install_diagnosis`; then `_check_after_installs` (confirmed installations only,
  once no installation runs, `FOLLOWUP_*`).
- **Download server support:** `may_download`, `record_download`, `_downloaded`,
  `_reset_download`.
- **Firmware store / upload:** `_register_firmware`, `cleanup_firmware`
  (housekeeping only), `firmware_list`, `firmware_changelog`, `store_size`, `delete_firmware`
  (`_delete_lock`), `stage_upload` → `confirm_upload` / `cancel_upload`,
  `_read_manifest`, `upload_types`.
- **Device settings / passwords:** `_settings_device` (guards), `refresh_settings`,
  `change_settings`, `restart_device`, `reload_device`, `set_ignored`,
  `set_device_password` → `_password_action` (verify on the device).
- **Options:** `options_view`, `save_options` (Supervisor), `restart_app`,
  `download_base_issue`, `confirm_download_base`.
- **UI:** `public_state` (only listed fields), `busy`, `checking`.

## Stored data (`/data`, private to the app)

| File | Content |
|---|---|
| `state.json` | `{devices, last_scan, scanned_version, firmware, ui, ui_version (3), schedule, download_base_confirmed}` |
| `firmware/` | `<sha256>.<zip\|bin\|gbl>`; temporary `.part-*` (downloads, staged uploads) |
| `events.json` | activity log, one JSON object per line (3.0.0: one JSON array, converted once) |
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
`eco`, `ap`, `ble`, `outputs`, `actions`, `schedules`, `auth_enabled`,
`settings_error`; flags `ignored`; runtime only (not trusted after a restart)
`ota_progress`, `downgrade`. Memory only: `Manager.activity`, `progress`,
`installing`, `reloading`, `downloads`, `_upload`, `_followup` (devices to check
after their installation), `_verifying` / `_fresh` (running check only).

Firmware entry (`state.firmware[<file name>]`): `type`, `gen`, `version`, `sha256`,
`size`, `downloaded`, `https` (downloads), `source` (`download|upload`),
`sha_checked` (uploads), `changelog` (`{"date", "entries"}` of this version, set once
the changelog page lists it; never requested again).

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
| `POST /api/devices/{id}/settings` | `change_settings` |
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
| `GET` · `POST /api/settings` | `options_view` · `save_options` + restart |
| `POST /api/settings/download-base/confirm` | `confirm_download_base` |
| `GET /static/…` | `app.js`, `style.css` (immutable cache with `?v=`) |

Download server (port 8098): `GET|HEAD /<sha256>.<zip|bin|gbl>` → `may_download`,
`record_download` (GET only).

## UI (`ui-src/app.js`, `ui-src/style.css` → built into `app/static/`)

Single page, `textContent` only. Polls `api/state` (30 s idle, 3 s busy, paused when
hidden, ETag). Tabs: devices (`COLUMNS`, row menu, dialogs), firmware (store, upload, `firmwareDevice` = Install button per device, `openFirmwareChangelog`,
delete), logs, settings (`OPTION_FIELDS`). Build: `tools/build_ui.py`; never edit
`app/static/app.js|style.css` by hand.

## Tests (`tests/`, all network mocked; fixtures in `conftest.py`, helpers in `helpers.py`)

| File | Covers |
|---|---|
| `test_check.py` | full/single check, reservation, progress, single flight, cancellation, ignored, Reload Device Info, log severity, firmware store first, Gen1 list, changelog kept with the firmware, manufacturer server access |
| `test_install.py` | target version verification, OTA WebSocket channel, HTTP fallback, download diagnosis, check after installations |
| `test_firmware_store.py` | store housekeeping, delete, downgrade, upload |
| `test_device_settings.py` | device identity (incomplete answers), settings/capabilities, AP/BLE, password workflow, no secrets |
| `test_schedule.py` | schedule, DST, catch-up, deferral |
| `test_options.py` | option bounds, unknown keys, `download_base_url` missing/unconfirmed |
| `test_state.py` | state.json, activity log storage, credentials, UI settings |
| `test_web.py` | HTTP endpoints, CSRF, Ingress, 409s, upload/downgrade/delete over HTTP |
| `test_security.py` | CSP and headers, 32 KB limit, download server refusals |
| `test_lifecycle.py` | SIGTERM, parallel server stop |
| `test_changelog.py` | page fetch limit, changelog of one version, display between versions, parsing off the event loop |
| `test_build.py` | built UI files match `ui-src` |

`conftest.py`: `net` (FakeNet: registry, devices A/B/C, offers, downloads; all `/data`
paths in tmp), `mgr`, `client` (UI test client with CSRF token).

## UI checks (`qa/`, Playwright against `demo.py`)

| Script | Demo(s) | Use |
|---|---|---|
| `run_ui.py quick\|full` | starts all demos | one command; prints one line per suite |
| `ui_test.py [--full]` | 8801 idle, 8802 busy | layout, menus, dialogs, contrast; quick = 5 cases, full = 13 |
| `ui_features.py` | 8801, 8803 legacy, 8804 missing, 8805 rc7 | download URL hints/notice, downgrade, upload, delete, Install button and changelog icon in the Firmware tab |
| `mobile_capture.py` | 8711 idle | screenshots + overflow, full only |

`tools/dev/check.sh [tests|ui|full|build]` runs everything in order: ruff → mypy (strict,
app modules) → build check → version match → pytest → UI. Rules of ruff, mypy and pytest:
`pyproject.toml`.

App definition besides `config.yaml`: `apparmor.txt` (AppArmor profile: Python, read-only
image, writable `/data`, network), `README.md` / `DOCS.md` (Info and Documentation tab of
the app store), `translations/en.yaml`.
