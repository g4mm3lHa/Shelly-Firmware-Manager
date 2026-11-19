# Test report 3.4.1-rc1

Release candidate of 3.4.1 on branch `release-3.4.1` (base: `main` = `v3.4.0`). One
fix: files above 16 MiB could not be imported or uploaded.

## Cause

Home Assistant Ingress refuses request bodies above 16 MiB ("Maximum request body size
16777216 exceeded"). The app's own limits (store import up to `import_max_mb`, firmware
upload up to 64 MiB) were therefore never reached; a store export of about 56 MB could
not be imported. The limit is outside the app and cannot be raised by it.

## Scope

| Area | Change |
|---|---|
| Transfer in parts | `FirmwareStore._receive_part`: parts of at most `PART_MAX` (8 MiB) per request; the first part (offset 0) starts a transfer and returns its ID, every further part names it and must continue exactly at the received size; total size fixed per transfer; any error or `PART_TTL_S` (5 min) without a part drops the transfer and its temporary file; one incomplete transfer per kind |
| Upload / import | `stage_upload_part`, `stage_import_part` (the former one-request functions are one part); limits (64 MiB, `import_max_mb`), free-space check (import: 2 × total at the start), ZIP check, SHA-256 and manifest hints after the last part |
| HTTP | `part_headers`: `X-Upload-Total`, `X-Upload-Offset`, `X-Upload-Transfer` (32 hex); total above the limit or part above 8 MiB → 413; invalid headers → 400; without the headers the body is the whole file |
| UI | `uploadFile` sends 8 MiB parts; upload and import dialogs show the progress in percent |
| Docs | DOCS (import: parts and progress), CODEMAP, UI_DECISIONS (progress; details of 3.4.0 confirmed in the live test), CHANGELOG `Changes in rc1` |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh ui`: ruff, mypy --strict, build check, version 3.4.1-rc1, pytest | passed, 229 tests (4 new: import and upload in parts, parts out of order / unknown / expired, HTTP headers and limits) |
| UI quick matrix and feature suites | ui_test 75/75; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 218/218, store_transfer 86/86 (new: a 17 MiB import is sent in 3 parts) |
| Screenshots | no view changed apart from the progress text; the set `qa/screenshots/3.4.0/` stays the newest |

## Security notes

- The CSRF check, the binary-route restriction and the content type check apply to
  every part. The total size is checked against the limit before anything is written;
  a part can never extend the file beyond the announced total.
- Transfer IDs are random (128 bit) and compared in constant time.

## Not tested here

- Real Home Assistant Ingress with a file above 16 MiB – live test of rc1.
