# Code map

Orientation for changes: read this overview first, then only the detail file of the layer
you change (names, not line numbers – search for the function name). All app modules carry
type annotations (`mypy --strict`); device and store entries are plain JSON objects
(`manager_core.Device`, `manager_core.FirmwareMeta`). Keep the overview and the detail
files in sync when modules, stored fields, routes or test files change.

| Detail file | Content | Read when you change |
|---|---|---|
| `docs/codemap/modules.md` | backend modules, key names, the six manager layers | `shelly_firmware_manager/app/*.py` |
| `docs/codemap/data.md` | files below `/data` and their fields | stored data |
| `docs/codemap/api.md` | HTTP routes → handlers → manager | `web_ui.py` |
| `docs/codemap/ui.md` | UI modules and what they export | `ui-src/` |
| `docs/codemap/tests.md` | what each test file covers, `qa/` scripts, check flow | `tests/`, `qa/` |

## Runtime flow

`main.py` loads `/data/options.json` → `Manager(options, session, …)` (loads all
`/data` files) → starts the UI app (port 8099, Ingress only) and the download server
(port 8098) → `run_schedule` sleeps until the next scheduled date (`schedule.py`) and
calls `Manager.scan(automatic=True)`. SIGTERM/SIGINT: cancel the scheduler, clean up
both servers in parallel (cancels checks/installations, saves the state), exit 0.

## Backend modules (`shelly_firmware_manager/app/`) – module: purpose → test file

- `main.py`: start, servers, scheduler loop → `test_lifecycle.py`
- `manager_core.py` (1): state, persistence, options, check slots → `test_check.py`, `test_options.py`, `test_state.py`
- `manager_store.py` (2): firmware store, upload, downgrade, export/import → `test_firmware_store.py`, `test_store_transfer.py`
- `manager_devices.py` (3): device settings, passwords → `test_device_settings.py`
- `manager_checks.py` (4): full and single checks, offers → `test_check.py`
- `manager_installs.py` (5): installation, OTA, verification → `test_install.py`
- `manager.py` (6): `Manager`, `public_state` → `test_web.py`
- `web_ui.py`: Ingress UI + JSON API, CSRF/size middleware → `test_web.py`, `test_security.py`
- `download_server.py`: LAN download server for the Shellys → `test_security.py`
- `shelly.py`: device and manufacturer access, SSRF/size guards → `test_check.py`, `test_device_settings.py`
- `store_transfer.py`: export/import archive (pure functions) → `test_store_transfer.py`
- `schedule.py`: schedule, DST → `test_schedule.py`
- `app_options.py`: option validation → `test_options.py`
- `events.py`: activity log → `test_state.py`
- `credentials.py`: per-device passwords → `test_state.py`
- `changelog.py`: changelog pages → `test_changelog.py`
- `tls.py`: certificate pinning → `test_security.py`
- `data_paths.py`: paths below `/data` (read as `data_paths.NAME`)

## UI (`ui-src/js/*.js`, `ui-src/style.css`; built into `app/static/` – never read the build)

`core.js` shared state and helpers · `devices.js` Devices tab · `install.js` install and
downgrade dialogs · `device-settings.js` device dialogs · `firmware.js` Firmware tab ·
`logs.js` Logs tab · `app-settings.js` Settings tab · `main.js` entry, header, tabs, polling.
UI checks: `qa/features/<area>.py` (runner `qa/ui_features.py`), `qa/ui_test.py`; details in
`docs/codemap/tests.md`.

App definition besides `config.yaml`: `apparmor.txt`, `README.md` / `DOCS.md` (app store),
`translations/en.yaml`. Check flow: `tools/dev/check.sh [tests|ui|full|build]`; rules of
ruff, mypy and pytest in `pyproject.toml`.
