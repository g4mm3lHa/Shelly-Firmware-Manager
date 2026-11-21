# Test report 3.6.0-rc1

Release candidate 1 of 3.6.0 on branch `release-3.6.0` (base: `main` = `v3.5.0`).

## Scope

| Area | Change |
|---|---|
| Devices tab | "Device Details" dialog with a device section (model, generation, firmware ID, NTP); row menu in five groups |
| Publication | MIT license, SECURITY.md, CI workflow (`tools/dev/check.sh` and the app definition linter), new app name "Firmware Manager for Shelly" (`config.yaml`, `repository.yaml`), icon and logo, credits with component licenses |
| App definition | Redundant default keys removed from `config.yaml`; Supervisor watchdog replaced by a Dockerfile `HEALTHCHECK` (TCP 8098) |
| Tests | `test_import_size_option` uses the patched data paths (failed on the CI runner without write access to `/data`) |
| Dev tooling | CODEMAP split, UI feature checks split, mockup script |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.6.0-rc1, pytest | passed, 238 tests |
| UI full | ui_test 210/210; download_url_hint 18/18, setting_help 26/26, download_url_notice 45/45, firmware_store 526/526, store_transfer 90/90 (705/705); capture 28/28 |
| GitHub CI | first run (0df1064) red: test and linter findings, fixed; run on badb201 green (both jobs) |

First full run: store_transfer failed once (360 px embedded: "password not cut off", the
known intermittent check); the second complete run passed.

## Open questions

- Docker `HEALTHCHECK` as restart monitoring: not tested in a real Supervisor (container
  build and health handling are not tested here).
- Ingress, container build, real hardware: not tested (see test limits).
