# Code map – HTTP API

Part of `docs/CODEMAP.md`; read when you change routes in `web_ui.py`.

**HTTP API (`web_ui.py`, Ingress only; writes need `X-CSRF-Token` + JSON ≤ 32 KB; binary bodies only on `BINARY_ROUTES`)**

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
| `GET /api/firmware` | `firmware_list` (per file `devices`: `id`, `name` of the devices it is provided for; `changelog` flag), `store_size`, `free_space` |
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
| `GET /api/settings/app-page` | `app_page` (link to the app page, changelog) |
| `GET /static/…` | `app.js`, `style.css` (immutable cache with `?v=`) |

Download server (port 8098): `GET|HEAD /<sha256>.<zip|bin|gbl>` → `may_download`,
`record_download` (GET only).
