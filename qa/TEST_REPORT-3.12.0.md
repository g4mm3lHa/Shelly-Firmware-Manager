# Test report 3.12.0

Final release 3.12.0 on branch `release-3.12.0` (base: `main` = `v3.11.1`), after 3.12.0-rc1
to rc6, all tested live through the beta channel. Changes after rc6: version, merged
changelog section, two module-internal UI helpers no longer exported, code map, DOCS
(masking of IPv6 addresses), screenshots; no change of the app behaviour.

## Scope

| Area | Change |
|---|---|
| Settings | options stored by the app (`/data/settings.json`), changed only in the Settings tab; one-time take-over of the options the Supervisor kept; every stored value validated at the start (invalid = default, named in the status strip); host port of the download server set through the Supervisor, port in use refused (checked only at a LAN address); page returns to the Settings tab after the restart |
| Diagnosis | connection test of the download server (test address: random token, once, chosen device only, no firmware), card "Getting started", causes and "Try again" for devices that do not answer, "Details for a bug ticket" (names, IPv4, IPv6 and MAC addresses masked) |
| Comparison after an update | device read again 15 s after an update with a "Before update" backup; changed and new settings and stopped scripts in the row menu and the log (log: names only); "Seen"; robust against unexpected answers and a new installation meanwhile |
| Overview | Home Assistant areas (column, filter, schedules and "Back up now" by area), Security strip, CSV export |
| Languages | Dutch, French, Italian, Spanish, Polish, Portuguese (machine translation) |
| UI | no status line under the title, "Previous version", log details, warning boxes, phone fixes |
| Image | standard library precompiled; health check every second while starting |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.12.0, pytest, UI, layout gate | passed: 684 pytest; ui_test 210/210; ui_features 1353/1353; layout gate 42/42 |
| Branch coverage (`coverage run --branch`, app modules) | 91 % (7701 statements, 2582 branches); lowest: `credentials.py` 80 %, `web_settings.py` 82 %, `store_transfer.py` 83 %, `shelly.py` 85 %; changed in rc6: `app_options.py` 94 %, `diagnostics.py` 94 %, `manager_backups.py` 90 %, `shelly_net.py` 91 % |
| CVE check | 2026-11-24: PyPI lists no known vulnerability for the ten pinned packages (all at their newest version, unchanged since 3.11.1); base image digest unchanged (`c5ba3dee…dd89`), 3.12.16-alpine3.24 is the newest 3.12 alpine tag |
| Docs check (seven points) | changelog bullets match `DOCS.md` (Configuration, Using the app, Troubleshooting, Privacy, Security, Upgrading); options in `config.yaml` (hint field only), `translations/en.yaml`, Settings tab and Configuration agree; feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt` (unchanged); relative links and anchors resolve; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | set of the release kept outside the repository (1440 and 412 px: Devices, Firmware, Schedules, Backups, Logs, Settings); `docs/images/` replaced: Devices desktop (Security strip, CSV export) and installation dialog (connection test, "Previous version") |

## Live tests through the beta channel

- rc1 to rc6 tested in Home Assistant by the maintainer (2026-11-24); the findings of each
  candidate were fixed in the next one; rc6 passed without findings.
- The fixes of the code review in rc6 are covered by tests; their visible parts (start,
  bug ticket text, comparison log entry, host port refused) were part of the rc6 live test.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly hardware, non-Chromium browsers;
  a device with an IPv6 address; a damaged or hand-edited settings file (covered by tests
  only).
