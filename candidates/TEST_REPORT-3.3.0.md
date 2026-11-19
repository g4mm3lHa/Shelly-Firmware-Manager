# Test report 3.3.0

Final release of 3.3.0 on branch `release-3.3.0` (base: `main` = `v3.2.1`). Contents
as 3.3.0-rc1 (live test positive, no findings), version set to 3.3.0 and the rc1
changelog block merged into the version section.

## Scope

| Area | Change |
|---|---|
| Downgrade option | New option `allow_downgrade` (`bool?`, missing = off); `downgrade_blocker` refuses while off; state field `allow_downgrade`; row menu entry disabled with hint; Settings tab switch |
| Firmware tab downgrade | History icon on older Gen2+ versions; dialog with the devices of the type that have a newer version installed; then the known second confirmation |
| Sorting | Sort select + direction button in the Devices and Firmware toolbars (desktop and phones); column heads coupled; settings per browser (localStorage); shared table settings (`POST /api/ui`, `set_ui`, `ui`/`ui_version` in `state.json`) removed |
| Device password | `keep` parameter (enable/change): verified without storing, stored password removed after the device confirmed; dialog checkbox "Store in this app", storage notes, HA reminder in the result |
| Messages | Status icons (success, warning, error) in message dialogs; neutral notes without icon |
| Small UI points | Red box in the delete dialog; phone toast width; phone popup spacing; log tile arrow and expansion |
| Docs | Requirement of the HA Shelly integration, original Shelly firmware only; option, sorting, password and upgrade notes |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.3.0, pytest | passed, 204 tests |
| UI full matrix | ui_test 210/210; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 428/428 (all four feature viewports) |
| Layout gate (`capture.py --gate`) | 28/28 |
| Live test of 3.3.0-rc1 in Home Assistant | positive, no findings |
| Clean-up | no unused UI exports, no version marks in comments (only where entries of older versions are read), no stale references |
| Screenshots | `qa/screenshots/3.3.0/` (1440 px and 412 px) |

## Security and dependencies (2026-11-19)

| Component | Pinned | Result |
|---|---|---|
| aiohttp | 3.14.5 | newest release; the advisories of 2026-11-13/16 (upgrade handling, compressed streams, form fields, multipart, WebSocket) are fixed since 3.14.4. The PyPI advisory records of 2026-11-17 (PYSEC-2026-4366 to 4369, medium) name 3.14.4 as fixed version – not affected |
| yarl | 1.25.1 | the SSRF report about malformed authority/host strings affects up to 1.23.0 – not affected |
| multidict | 7.2.1 | the 2026 reference-leak advisory affects 6.7.0–6.9.0 – not affected |
| idna | 3.20 | CVE-2026-45409 fixed in 3.15 – not affected |
| aiohappyeyeballs, aiosignal, attrs, frozenlist, propcache, typing-extensions | as pinned | no known advisory found |
| Python | 3.12.16 | newest 3.12 security release (2026-10-25) |
| Base image | `python:3.12.16-alpine3.24@sha256:e064671c…` | Alpine 3.24.4 (2026-11-05, apk-tools; openssh-server is not in the image) may be newer than the pinned digest; not checkable here (no registry access); a re-pin needs approval |

No package was changed.

## Not tested here

- Real Home Assistant, Ingress and Supervisor, real Shelly devices and non-Chromium
  browsers (covered by the live test of rc1 in the user's installation).
