# Test report 3.4.0-rc1

Release candidate of 3.4.0 on branch `release-3.4.0` (base: `main` = `v3.3.0`). Two
features only: moving the firmware store between Home Assistant instances, and device
passwords for several devices in one step.

## Scope

| Area | Change |
|---|---|
| Store export | New module `store_transfer.py`; `prepare_export` (generated password, returned once; scrypt key kept in memory 10 min) → `GET /api/firmware/export/{token}` (single use, streamed, temporary file deleted); readable ZIP layout `Gen2+\|Gen1/<type>/…`, `manifest.json` sealed with HMAC-SHA256, `SHA256SUMS.txt`; damaged stored files left out |
| Store import | `POST /api/firmware/import` (octet-stream, ≤ option `import_max_mb`, streamed, free space ≥ 2 × size) → `check` (seal, every entry and file; nothing stored) → `confirm` (new files via `.part-` + rename; same SHA-256: missing data added, upload → download; conflict: stored file kept; free space for the unpacked sizes) / `cancel`; log `import` |
| Option | `import_max_mb` 1–2048, default 100 (config.yaml schema, translations, Settings tab) |
| First start | No state.json at start → `first_start_open`: `reserve_check` refused, scheduler waits, one HA notification; answered by `POST /api/first-start/skip` (log `setup`) or an import; UI welcome dialog once per browser, notice bar, "Check now" disabled |
| Device passwords | `POST /api/devices/passwords` → `set_passwords` (store / set; set = enable or change per device; skipped with reason; parallel up to `max_parallel_installs`; each device through `set_device_password`) |
| UI | Settings cards "Firmware store" and "Device passwords"; export, import (counts, expandable groups), bulk password dialog with second confirmation and result per device; log entries `export`, `import`, `setup` |
| Docs | DOCS (first start, option, Moving the firmware store, several devices, security), READMEs, CODEMAP, UI_DECISIONS, CHANGELOG `Changes in rc1` |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.4.0-rc1, pytest | passed, 225 tests (21 new) |
| UI full matrix | ui_test 210/210; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 428/428, store_transfer 82/82 (new suite) |
| Layout gate (`capture.py --gate`) | 28/28 |
| Neutrality scan of the branch | no findings |
| Screenshots | `qa/screenshots/3.4.0-rc1/` (1440 px and 412 px): Settings cards, export, import, device passwords, first-start dialog and notice |

## Security notes

- The export password is the only secret ever sent by the app: once, in the answer of
  `POST /api/firmware/export`; it is not logged and not stored (only the derived key,
  in memory, until the download or 10 minutes).
- The seal covers the complete manifest (every entry with SHA-256, size, source, date,
  changelog); scrypt parameters are fixed (n = 2^15, r = 8, p = 1), other values are
  refused. Paths in the manifest are validated; every file is read with a size limit
  and hashed before anything is stored, and again while it is extracted.
- CSRF, CSP and the 32 KB JSON limit are unchanged; binary bodies are accepted only on
  the upload and import routes. Device passwords are not part of an export.
- No package was changed.

## Not tested here

- Real Home Assistant, Ingress (download of large exports through Ingress), Supervisor
  notification, real Shelly devices and non-Chromium browsers – live test of rc1.
