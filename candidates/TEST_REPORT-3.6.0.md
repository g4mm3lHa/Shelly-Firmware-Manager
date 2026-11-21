# Test report 3.6.0

Final release 3.6.0 on branch `release-3.6.0` (base: `main` = `v3.5.0`), after two
release candidates; rc2 was tested live through the beta channel.

## Scope

| Area | Change |
|---|---|
| Publication | MIT license, `SECURITY.md`, CI workflow, `url` in `config.yaml`, credits with component licenses (license files in the image), app name "Firmware Manager for Shelly" with the "unofficial" notice, icon and logo |
| Devices tab | Device Details dialog (model, generation, firmware ID, NTP), row menu in five groups |
| Checks and state | one failing device no longer ends a check; state file with `state.bak` and `state.damaged`, unknown firmware files are kept when no state is readable; check refused while a store import runs |
| Store export/import | free space checked when the export is prepared (`expires_in`), expired export refused in the dialog; ZIP64 archives and oversized file lists refused |
| Security | fixed device address list (`shelly.LAN_NETWORKS`), `download_base_url` without credentials, query and fragment, security headers on refused requests and errors, trailing line breaks refused |
| Other | device registry answers up to 32 MiB, first-start notification retried, damaged activity log tolerated, password change report with reason |
| Dev tooling | `bump.py`, `.gitattributes`, CI jobs once per commit, pinned CI actions and tool versions |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.6.0, pytest | passed, 345 tests |
| UI full | ui_test 210/210; ui_features 705/705 (download_url_hint 18, setting_help 26, download_url_notice 45, firmware_store 526, store_transfer 90); layout gate (capture) 28/28 |
| CVE check (2026-11-21) | PyPI lists no known vulnerability for the ten pinned packages; base image tag `python:3.12.16-alpine3.24` still resolves to the pinned digest `sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89`; OSV not queried (blocked by the proxy) |
| Screenshots | `qa/screenshots/3.6.0/` (1440 px and 412 px: four tabs and the Device Details dialog) |

## Live tests through the beta channel

- rc2 tested in Home Assistant by the maintainer (2026-11-21): all points passed.
- Not tested live: the restart by the Watchdog when the health check fails (no Docker
  access; the code is covered by tests); item 12 (one download for several
  devices, needs new firmware).

## Not tested here

- Real Home Assistant / Ingress / Supervisor, container build, real Shelly devices,
  non-Chromium browsers.
