# Code map

Orientation for changes: read this overview first, then only the detail file of the layer
you change (names, not line numbers – search for the function name). All app modules carry
type annotations (`mypy --strict`); device and store entries are plain JSON objects
(`manager_core.Device`, `manager_core.FirmwareMeta`). Keep the overview and the detail
files in sync when modules, stored fields, routes or test files change.

| Detail file | Content | Read when you change |
|---|---|---|
| `docs/codemap/modules.md` | backend modules, the eight manager layers, pitfalls; names and lines: `python3 tools/dev/symbols.py` | `shelly_firmware_manager/app/*.py` |
| `docs/codemap/data.md` | files below `/data` and their fields | stored data |
| `docs/codemap/api.md` | HTTP routes → handlers → manager | `web_ui.py` |
| `docs/codemap/ui.md` | UI modules and what they export | `ui-src/` |
| `docs/codemap/tests.md` | what each test file covers, `qa/` scripts, check flow | `tests/`, `qa/` |

## Runtime flow

`main.py` loads `/data/options.json` → `Manager(options, session, …)` (loads all
`/data` files) → starts the UI app (port 8099, Ingress only) and the download server
(port 8098) → `run_schedule` sleeps until the next scheduled date (`schedule.py`) and
calls `Manager.scan(automatic=True)`; `run_timetable` starts due scheduled installations
and schedules (`Manager.start_due`, woken early by `timetable_changed`). SIGTERM/SIGINT:
cancel both loops, clean up
both servers in parallel (cancels checks/installations, saves the state), exit 0.

## Backend modules (`shelly_firmware_manager/app/`) – module: purpose → test file

- `main.py`: start, servers, scheduler loop → `test_lifecycle.py`
- `manager_core.py` (1): state, persistence, options, check slots → `test_check.py`, `test_options.py`, `test_state.py`
- `manager_store.py` (2): firmware store, upload, downgrade, export/import → `test_firmware_store.py`, `test_store_transfer.py`
- `manager_devices.py` (3): device settings, passwords → `test_device_settings.py`
- `manager_backups.py` (4): backups of device settings: store, restore, download, import → `test_backups.py`
- `manager_checks.py` (5): full and single checks, offers → `test_check.py`
- `manager_installs.py` (6): installation, OTA, verification → `test_install.py`
- `manager_schedules.py` (7): scheduled installations, schedules and their runs → `test_timetable.py`
- `manager.py` (8): `Manager`, `public_state` → `test_web.py`
- `web_ui.py`: Ingress UI + JSON API, CSRF/size middleware → `test_web.py`, `test_security.py`
- `download_server.py`: LAN download server for the Shellys → `test_security.py`
- `shelly.py`: device and manufacturer access, SSRF/size guards → `test_check.py`, `test_device_settings.py`
- `store_transfer.py`: export/import archive (pure functions) → `test_store_transfer.py`
- `backup_store.py`: encrypted backup files and their list (no device access) → `test_backups.py`
- `shelly_backup.py`: read, compare and restore the settings of one device → `test_backups.py`
- `schedule.py`: schedule, DST → `test_schedule.py`
- `timetable.py`: schedules and "Install at": validation, next run (pure functions) → `test_timetable.py`
- `app_options.py`: option validation → `test_options.py`
- `events.py`: activity log → `test_state.py`
- `credentials.py`: per-device passwords → `test_state.py`
- `changelog.py`: changelog pages → `test_changelog.py`
- `tls.py`: certificate pinning → `test_security.py`
- `data_paths.py`: paths below `/data` (read as `data_paths.NAME`)

## UI (`ui-src/js/*.js`, `ui-src/style.css`; built into `app/static/` – never read the build)

`core.js` shared state and helpers · `i18n.js` language and `t()` (`i18n-de.js` German
texts) · `devices.js` Devices tab · `install.js` install and downgrade dialogs ·
`device-settings.js` device dialogs · `firmware.js` Firmware tab · `schedules.js`
Schedules tab · `backups.js` Backups tab and backup dialogs · `logs.js` Logs tab · `app-settings.js` Settings tab · `main.js` entry,
header, tabs, polling.
UI checks: `qa/features/<area>.py` (runner `qa/ui_features.py`), `qa/ui_test.py`; details in
`docs/codemap/tests.md`.

App definition besides `config.yaml`: `apparmor.txt`, `README.md` / `DOCS.md` (app store),
`translations/en.yaml` and `de.yaml`. Check flow: `tools/dev/check.sh [tests|ui|full|build]`; rules of
ruff, mypy and pytest in `pyproject.toml`.
