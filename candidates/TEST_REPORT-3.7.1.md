# Test report 3.7.1

Final release 3.7.1 on branch `release-3.7.1` (base: `main` = `v3.7.0`), after
3.7.1-rc1; rc1 was tested live through the beta channel.

## Scope

| Area | Change |
|---|---|
| Device list | a Shelly whose `via_device_id` points to a registry entry that is not a Shelly (router, bridge of another integration) is listed and checked; only devices behind a Shelly gateway stay hidden. Stored device data and passwords of listed devices are no longer removed |
| Documentation | FAQ and Known limitations say "connected through another Shelly" |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.7.1, pytest, UI, layout gate | passed: 423 pytest; UI ui_test 210/210, ui_features 752/752; layout gate 28/28 |
| New tests | `test_shelly_via_router_is_listed_and_keeps_its_data`, `test_shelly_via_shelly_gateway_is_hidden_and_cleaned` |
| CVE check | reused from 3.7.0 (same day 2026-11-21, no package or base image change): PyPI lists no known vulnerability for the ten pinned packages; base image digest unchanged |
| Docs check (seven points) | changelog bullets match `DOCS.md` (FAQ, Known limitations); options unchanged; feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt` (unchanged); relative links and anchors unchanged; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | `qa/screenshots/3.6.0/` kept (no view changed) |

## Live tests through the beta channel

- rc1 tested in Home Assistant by the maintainer (2026-11-21): device list and password lock
  symbols unchanged, no new entries, no new log errors: passed. A Shelly connected via a router
  is not available in the maintainer's setup; this case is covered by the unit tests only.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly devices behind a router, non-Chromium
  browsers; the restart by the Watchdog when the health check fails; item 12 (one download for
  several devices).
