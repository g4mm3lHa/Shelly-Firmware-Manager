# Code map – backend modules

Part of `docs/CODEMAP.md`; read when you change `shelly_firmware_manager/app/*.py`.

**Names and line numbers are not kept here.** Generate them from the source, always current:
`python3 tools/dev/symbols.py <module>` (classes, functions, constants with line and first
docstring line), `-g <text>` filters by name, `-c` lists classes and module functions only.
Then read the symbol with `sed -n '<line>,+40p' <file>`. This file keeps only what a script
cannot know: responsibility, layering, invariants and pitfalls.

## Modules (`shelly_firmware_manager/app/`)

| Module | Responsibility and pitfalls |
|---|---|
| `main.py` | start, servers, scheduler loops (`run_schedule`, `run_timetable`), clean stop. `VERSION` must equal `config.yaml` (`bump.py` sets both). |
| `data_paths.py` | paths below `/data`; always read as `data_paths.NAME` (tests and the demo redirect them). |
| `shelly.py` | all device and manufacturer access: auth, SSRF/redirect/size guards, offers, downloads, settings read/write. Addresses only from `LAN_NETWORKS` (RFC 1918, `fc00::/7`), never extended. |
| `web_ui.py` | Ingress UI and JSON API in `build_ui_app` (handlers inside); middleware for CSRF, JSON depth/size, security headers, unexpected error → 500 without details. "(beta)" mark only on pre-release versions. |
| `download_server.py` | GET/HEAD `<sha256>.<ext>` for the Shellys, only during an active update, by default only to the target IP. |
| `schedule.py` | the automatic check: every N days at HH:MM, HA time zone, DST, catch-up. |
| `timetable.py` | schedules and the time of "Install at": validation and next run, pure functions; stored entries re-checked at start (`check_stored`, `check_plan`). |
| `app_options.py` | option validation, merge with defaults, log diff, download URL rules. |
| `events.py` | activity log (JSON Lines): severity, entry IDs, delete/clear, `latest` mark. |
| `credentials.py` | per-device passwords, file 0600, Gen2+ HA1 only; a write error raises `ShellyError`. |
| `changelog.py` | Shelly changelog pages → plain text (parsed in a worker thread). |
| `store_transfer.py` | store export/import files as pure functions run in a worker thread: ZIP with sealed `manifest.json` (HMAC-SHA256, key = scrypt of the export password), `SHA256SUMS.txt`; every entry checked, ZIP64 refused. |
| `backup_store.py` | backups of device settings as files (no device or network access): header + zlib payload, encrypted with a standard-library key stream, HMAC tag (encrypt-then-MAC); decrypted only after the tag was verified. Stored files use the app key, downloads and exports a key from their password. |
| `shelly_backup.py` | device side of a backup: `read`, `compare`, `restore` (chosen parts, device read again, only what it then reports counts as applied, restart when a part asks). Secrets (`SECRET_KEYS`) and the Home Assistant Bluetooth proxy script are never part of a backup. |
| `tls.py` | certificate pinning for `*.shelly.cloud`. |

## The manager

One object built from eight layers: each module holds one class that extends the class of
the layer before, so a layer only uses what lies below it (no imports upward). Everything
outside uses `manager.Manager` only. Constants live where they are used; tests patch them there.

| Layer / module | Class | Content |
|---|---|---|
| 1 `manager_core.py` | `ManagerCore` | state, `__init__`, options, persistence (atomic save, `state.bak`, damaged file → `state.damaged` warning), Home Assistant registry/notification/Supervisor calls, check slots (`reserve_check`: "all" is exclusive, a device refused while checked/installed/reloaded, nothing while an import stores or a schedule runs), `STATUS_*`/`SECTION_*`/`SOURCE_*` constants, `severity_of`, `unexpected_text`, first-start state. |
| 2 `manager_store.py` | `FirmwareStore` | firmware files and their metadata: registering, adopting unlisted files by content (never deleting), integrity, the rule when a stored file replaces a download (`_replaces_download`, `_stored_firmware`, `_store_offer`), downgrade candidates, upload (staged, then confirmed), store export/import in parts (`PART_MAX` 8 MiB: Ingress refuses 16 MiB), manual delete with locks. |
| 3 `manager_devices.py` | `DeviceSettings` | device settings read/write with read-back (`not_applied`), reload, restart, ignore, remove from list, device passwords (single and several). |
| 4 `manager_backups.py` | `Backups` | backup store and runs (before installation, manual, schedules), views, restore with preview and parts, password-sealed download, import per device. A failing backup before an installation stops that installation. |
| 5 `manager_checks.py` | `Checks` | full check (global lock) and single checks (parallel); scanning removed and other Shellys, Home Assistant listing, per-device offer logic: Gen1 store first then the manufacturer list; Gen2+ server decides (`_step_offer`, intermediate versions), beta, store fallback on server error, reference request, hold-back (`offer_after_days`), `keep_installed`; one shared provision per type and version (`_provide`). |
| 6 `manager_installs.py` | `Installs` | approved install (`approval_matches`), restart-and-install, Gen2+ WebSocket `Shelly.Update` / Gen1 HTTP, waiting for the exact target version via OTA events or polling, failure and retry, downgrade, step check after a confirmed intermediate version, download-server support. |
| 7 `manager_schedules.py` | `Schedules` | "Install at" plans (run, lapse, missed – never made up) and schedules (create/update/delete, runs, left-out devices with reason), start-up clean-up (withdraw betas, strike unknown device IDs), notifications. |
| 8 `manager.py` | `Manager` | `public_state`: the only fields the UI sees; add a field there when the UI needs one. |

## Pitfalls
- State fields not listed in `public_state` never reach the UI; stored fields are described in `data.md`.
- A new check or install path must respect the slot rules of layer 1.
- Anything new that reads a file or URL from outside needs a size limit and goes through the guards of `shelly.py`.
