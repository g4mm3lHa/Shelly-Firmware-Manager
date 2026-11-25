# Code map – HTTP API

Part of `docs/CODEMAP.md`; read when you change routes: the table and the middleware are in
`web_ui.py`, the handlers in the area modules `web_devices.py`, `web_firmware.py`, `web_backups.py`,
`web_schedules.py`, `web_settings.py` (shared checks and error translation: `web_base.py`).

**HTTP API (`web_ui.py`, Ingress only; writes need `X-CSRF-Token` + JSON ≤ 32 KB; binary bodies only on `BINARY_ROUTES` (upload, store import, backup import); every answer – also refusals of the middleware and the 500 for an unexpected error – carries `SECURITY_HEADERS`)**

| Route | Handler → Manager |
|---|---|
| `GET /` | page (CSRF token, asset version) |
| `GET /api/state` | `public_state` (ETag / 304; `start_id` is new with every app start) |
| `POST /api/scan` | `reserve_check` + `scan` (background) |
| `POST /api/devices/{id}/check` | `reserve_check("device")` + `check_device` |
| `POST /api/devices/{id}/install` | `approval_matches` / `downgrade_*` + `install` (`confirm`, `sha256`, `restart`: restart the device first; downgrade: `downgrade` + `acknowledged`; a beta needs `beta_acknowledged`; `at` "YYYY-MM-DDTHH:MM" → `plan_install` instead, answer `{scheduled, at}`; without `at` a scheduled installation is dropped – `cancel_plan("installed_now")`; 409 while a schedule runs; an update also 409 for an ignored device or while its info is reloaded) |
| `POST /api/devices/{id}/planned/cancel` | `cancel_plan` (`confirm`) |
| `POST /api/devices/{id}/offer-now` | `held_version`, `reserve_check`, then `release_hold` + check of the device (409 if nothing is held or the check cannot start; the hold stays then) |
| `GET /api/devices/{id}/signal` | `wifi_signal` (`{rssi}`, read now; None: cable; not readable → 409, shown as "unknown") |
| `GET /api/devices/{id}/downgrades` | `downgrade_versions`, `downgrade_blocker` |
| `GET /api/devices/{id}/changelog` | `changelog_for` |
| `POST /api/devices/{id}/settings/refresh` | `refresh_settings` |
| `POST /api/devices/{id}/settings` | `change_settings` (answer: `verified`, `not_applied`; `ap_config` = access point form `{enable, ssid, security: keep|password|open, password, accept_open, range_extender}` – the password (8–63 characters) goes to the device only) |
| `POST /api/devices/{id}/restart` | `restart_watched` (command; error → answer) + background `watch_restart` (activity `restarting` until the device answers, else the device error) |
| `POST /api/devices/{id}/password` | `set_device_password` (`mode`, `confirm`, `acknowledged`, `keep`) |
| `POST /api/devices/passwords` | `set_passwords` (`mode` store/set, `devices`, `password`, `confirm`, `acknowledged`, `keep`; result per device, never the password) |
| `POST /api/devices/{id}/ignore` | `set_ignored` |
| `POST /api/devices/{id}/reload` | `reload_device` |
| `POST /api/devices/{id}/probe` | `probe_device` (Try again: `{address, port, device}` steps – address in Home Assistant, device port open, device answers; no check, nothing changed) |
| `GET /api/devices/{id}/report` | `device_report` (`{text}`: Details for a bug ticket, masked; nothing sent anywhere) |
| `POST /api/getting-started/hide` | `hide_getting_started` |
| `POST /api/devices/{id}/forget` | `forget_device` (Remove from list; `confirm`; removed devices only) |
| `GET /api/firmware` | `firmware_list` (per file `devices`: `id`, `name` of the devices it is provided for; `installed`: names of the devices running it; `changelog` flag), `store_size`, `free_space` |
| `GET /api/firmware/{name}/changelog` | `firmware_changelog` (stored changelog of this file, no request) |
| `POST /api/firmware/upload` | `stage_upload_part` (octet-stream, ≤ 64 MiB in all, streamed; parts ≤ 8 MiB with `X-Upload-Total`, `X-Upload-Offset`, `X-Upload-Transfer` – `part_headers`) |
| `POST /api/firmware/export` | `prepare_export` (generated password returned once, token, `expires_in`; empty store and too little free space refused here) |
| `GET /api/firmware/export/{token}` | `build_export` → ZIP stream (single use; temporary file deleted after sending) |
| `POST /api/firmware/import` | `stage_import_part` (octet-stream, ≤ `import_max_mb` in all, parts like the upload) |
| `POST /api/firmware/import/check` · `/confirm` · `/cancel` | `check_import` (`token`, `password`) · `confirm_import` (`acknowledged` required) · `cancel_import` |
| `POST /api/first-start/skip` | `skip_first_start` |
| `POST /api/schedules` · `/api/schedules/{sid}` | `create_schedule` · `update_schedule` (all user fields, `timetable.validate`; answer: the schedule as in the state) |
| `POST /api/schedules/{sid}/delete` · `/run` | `delete_schedule` · `reserve_schedule_run` + `run_schedule_now` in the background (both `confirm`; unknown ID 404, run not possible 409) |
| `POST /api/firmware/upload/confirm` · `/cancel` | `confirm_upload` · `cancel_upload` |
| `GET /api/firmware/{name}/download` | file response |
| `POST /api/firmware/{name}/delete` | `delete_firmware` (`confirm` + `acknowledged`; also an unknown file of the folder) |
| `GET /api/backups` | `backups_view` (`{rev, keep, totals, warning, devices, orphans}`) |
| `POST /api/devices/{id}/backup` | `backup_now` (one device, answer: the new backup) |
| `POST /api/devices/{id}/after-update/seen` | `after_update_seen` ("Seen" in the comparison after an update; 409 without one) |
| `POST /api/backups/run` · `/run/clear` | `reserve_backup_run` (`devices`: "all" or IDs) + `run_backups` in the background · `clear_backup_run` |
| `POST /api/backups/{bid}/keep` · `/delete` | `keep_backup` (`kept`) · `delete_backup` (`confirm`) |
| `POST /api/backups/{bid}/preview` | `preview_restore` (reads the device, changes nothing) |
| `POST /api/backups/{bid}/restore` · `/api/backups/restore/clear` | `reserve_restore` (`parts`, `confirm`, `network_acknowledged`) + `run_restore` in the background (progress in the state field `restore`) · `clear_restore` |
| `POST /api/backups/{bid}/download` · `GET /api/backups/download/{token}` | `prepare_backup_download` (generated password returned once, token, `expires_in`) · `take_backup_download` (single use) |
| `POST /api/devices/{id}/backups/import` | `stage_backup_import` (octet-stream, ≤ `backup_store.MAX_FILE`) |
| `POST /api/backups/import/check` · `/confirm` · `/cancel` | `check_backup_import` (`token`, `password`) · `confirm_backup_import` · `cancel_backup_import` |
| `GET /api/logs` | `events.list` |
| `POST /api/logs/{id}/delete` · `/api/logs/clear` | `events.remove` · `events.clear` (`confirm`) |
| `GET` · `POST /api/settings` | `read_host_port`, `options_view` (+ `installed_versions`, `host_port`, `host_port_known`) · `save_options` + restart (`host_port`: checked free, set via `POST /addons/self/options` `network`) |
| `POST /api/settings/connection-test` | `run_connection_test` (`device`: ID or null = automatic; one test at a time, else 409; answer: the result as in `connection_test`) |
| `POST /api/settings/download-base/confirm` | `confirm_download_base` |
| `GET /api/settings/app-page` | `app_page` (link to the app page, changelog) |
| `GET /static/…` | `app.js`, `style.css` (immutable cache with `?v=`) |

Download server (port 8098): `GET|HEAD /<sha256>.<zip|bin|gbl>` → `may_download`,
`record_download` (GET only); `GET|HEAD /connection-test/<32 hex>` → `test_fetched` (only the
running test's token, from its device, GET once; answer: a short text, never a file; else 404).
