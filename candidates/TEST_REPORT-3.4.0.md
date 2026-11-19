# Test report 3.4.0

Final release of 3.4.0 on branch `release-3.4.0` (base: `main` = `v3.3.0`). Contents
as 3.4.0-rc1 (live test positive, no findings; the interpretations of rc1 accepted),
version set to 3.4.0 and the rc1 changelog block merged into the version section.

## Scope

| Area | Change |
|---|---|
| Store export | New module `store_transfer.py`; `prepare_export` (generated password, returned once; scrypt key kept in memory 10 min) → `GET /api/firmware/export/{token}` (single use, streamed, temporary file deleted); readable ZIP layout `Gen2+\|Gen1/<type>/…`, `manifest.json` sealed with HMAC-SHA256, `SHA256SUMS.txt`; damaged stored files left out |
| Store import | `POST /api/firmware/import` (octet-stream, ≤ option `import_max_mb`, streamed, free space ≥ 2 × size) → `check` (seal, every entry and file; nothing stored) → `confirm` (new files via `.part-` + rename; same SHA-256: missing data added, upload → download; conflict: stored file kept; free space for the unpacked sizes; refused during checks) / `cancel`; log `import` |
| Option | `import_max_mb` 1–2048, default 100 (config.yaml schema, translations, Settings tab) |
| First start | No state.json at start → `first_start_open`: `reserve_check` refused, scheduler waits, one HA notification; answered by `POST /api/first-start/skip` (log `setup`) or an import; welcome dialog once per browser, notice bar, "Check now" disabled |
| Device passwords | `POST /api/devices/passwords` → `set_passwords` (store / set; set = enable or change per device; skipped with reason; parallel up to `max_parallel_installs`; each device through `set_device_password`) |
| UI | Settings cards "Firmware store" and "Device passwords"; export, import (counts, expandable groups), bulk password dialog with second confirmation and result per device; log entries `export`, `import`, `setup` |
| Docs | DOCS (first start, option, Moving the firmware store, several devices, security), READMEs, CODEMAP, UI_DECISIONS, CHANGELOG section 3.4.0 |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.4.0, pytest | passed, 225 tests |
| UI full matrix | ui_test 210/210; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 428/428, store_transfer 82/82 |
| Layout gate (`capture.py --gate`) | 28/28 |
| Live test of 3.4.0-rc1 in Home Assistant | positive, no findings |
| Clean-up | no unused UI exports, no version marks in comments, no stale references |
| Screenshots | `qa/screenshots/3.4.0/` (1440 px and 412 px): Settings cards, export, import, device passwords, first-start dialog and notice |

## Security notes

- The export password is the only secret ever sent by the app: once, in the answer of
  `POST /api/firmware/export`; it is not logged and not stored (only the derived key,
  in memory, until the download or 10 minutes).
- The seal covers the complete manifest; scrypt parameters are fixed (n = 2^15, r = 8,
  p = 1), other values are refused. Paths in the manifest are validated; every file is
  read with a size limit and hashed before anything is stored, and again while it is
  extracted.
- CSRF, CSP and the 32 KB JSON limit are unchanged; binary bodies are accepted only on
  the upload and import routes. Device passwords are not part of an export.

## Security and dependencies (2026-11-19)

| Component | Pinned | Result |
|---|---|---|
| aiohttp | 3.14.5 | newest release (2026-10-11); the advisories fixed in 3.14.0–3.14.4 (request smuggling, WebSocket, multipart, decompression) do not affect it. The scheme-spoofing report (absolute-form request) only matters for code that trusts `request.scheme` – the app does not use it |
| yarl | 1.25.1 | SSRF report affects up to 1.23.0 – not affected |
| multidict | 7.2.1 | reference-leak advisory affects 6.7.0–6.9.0 – not affected |
| idna | 3.20 | CVE-2026-45409 fixed in 3.15 – not affected |
| aiohappyeyeballs, aiosignal, attrs, frozenlist, propcache, typing-extensions | as pinned | no known advisory found |
| Python | 3.12.16 | newest 3.12 security release |
| Base image | `python:3.12.16-alpine3.24@sha256:e064671c…` | Alpine 3.24 point releases may be newer than the pinned digest; not checkable here (no registry access); a re-pin needs approval |

No package was changed.

## Not tested here

- Real Home Assistant, Ingress and Supervisor, real Shelly devices and non-Chromium
  browsers (covered by the live test of rc1 in the user's installation).
