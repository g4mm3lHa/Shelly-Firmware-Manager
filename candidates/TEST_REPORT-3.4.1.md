# Test report 3.4.1

Final release of 3.4.1 on branch `release-3.4.1` (base: `main` = `v3.4.0`). Contents
as 3.4.1-rc1 (live test positive: store import of about 56 MB through Home Assistant
Ingress), version set to 3.4.1 and the rc1 changelog block merged into the version
section.

## Cause of the fix

Home Assistant Ingress refuses request bodies above 16 MiB ("Maximum request body size
16777216 exceeded"). The app's own limits (store import up to `import_max_mb`, firmware
upload up to 64 MiB) were therefore never reached. The limit is outside the app.

## Scope

| Area | Change |
|---|---|
| Transfer in parts | `FirmwareStore._receive_part`: parts of at most `PART_MAX` (8 MiB) per request; the first part (offset 0) starts a transfer and returns its ID, every further part names it and must continue exactly at the received size; total size fixed per transfer; any error or `PART_TTL_S` (5 min) without a part drops the transfer and its temporary file; one incomplete transfer per kind |
| Upload / import | `stage_upload_part`, `stage_import_part` (the former one-request functions are one part); limits, free-space check, ZIP check, SHA-256 and manifest hints after the last part |
| HTTP | `part_headers`: `X-Upload-Total`, `X-Upload-Offset`, `X-Upload-Transfer` (32 hex); total above the limit or part above 8 MiB → 413; invalid headers → 400 |
| UI | `uploadFile` sends 8 MiB parts; upload and import dialogs show the progress in percent |
| Docs | DOCS, CODEMAP, UI_DECISIONS (progress; details of 3.4.0 confirmed in its live test), CHANGELOG section 3.4.1 |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.4.1, pytest | passed, 229 tests |
| UI full matrix | ui_test 210/210; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 428/428, store_transfer 86/86 (incl. a 17 MiB import in 3 parts) |
| Layout gate (`capture.py --gate`) | 28/28 |
| Live test of 3.4.1-rc1 in Home Assistant | positive |
| Clean-up | no unused UI exports, no version marks in comments, no stale references |
| Screenshots | no view changed apart from the progress text; `qa/screenshots/3.4.0/` stays the newest set |

## Security notes

- CSRF, binary-route restriction and content-type check apply to every part. The total
  size is checked against the limit before anything is written; a part can never
  extend the file beyond the announced total. Transfer IDs are random (128 bit) and
  compared in constant time.

## Security and dependencies (2026-11-19)

No package was changed since 3.4.0; the check of the same day applies (report of
3.4.0): aiohttp 3.14.5 newest release, advisories up to 3.14.4 fixed, the
scheme-spoofing report does not affect the app (no use of `request.scheme`); yarl
1.25.1, multidict 7.2.1 and idna 3.20 not affected; Python 3.12.16 newest 3.12. The
base image digest is not checkable here (no registry access); a re-pin needs approval.

## Not tested here

- Real Shelly devices and non-Chromium browsers (covered by the live tests).
