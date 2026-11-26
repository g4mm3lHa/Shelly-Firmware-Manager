# Test report 3.13.0

Final release 3.13.0 on branch `release-3.13.0` (base: `main` = `v3.12.1`), after 3.13.0-rc1
to rc4, all tested live through the beta channel, and a code review of rc4. Changes after
rc4: settings, restart and passwords of a device are refused while its check waits for the
Shelly update server; "keep" of the access point protection is accepted only for an access
point the device reports on and protected; the language hint of the Settings tab names
what stays English; version, merged changelog section, docs, code
map, comments, README screenshots.

## Scope

| Area | Change |
|---|---|
| Design | tinted surfaces from the theme colour, tonal buttons, segmented tabs, own checkboxes and radio buttons, compact phone tiles; dialogs (one warning box per colour, install dialog in two columns, folded changelog and device lists); option `base_color` (saved without a restart) |
| Access point | form for on/off, network name, password (8 to 63 characters) or open after an accepted risk, range extender (Gen2+, `Wifi.SetConfig`); password sent to the device only, never stored or logged |
| Activity | "Shelly server …" while a check waits for the update server (yellow after 5 s), "Restarting …" until the device answers (at most 2 minutes); the device is locked for settings during both phases of its check |
| Languages | status messages, app errors and connection test results in the page language; only the dictionary of the page language is loaded |
| Settings tab | dialog "Unsaved changes" on a tab or language change, save area above the cards, a language change stays on the tab, "Restart now" after a change that needs it |
| Start | backup tags verified in the background |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.13.0, pytest, UI, layout gate | passed: 722 pytest; ui_test 214/214; ui_features 1483/1483; layout gate 42/42 (after the language hint only that text changed: pytest 722 and the UI quick run 77 + 873 passed again). A first full run ended ui_features with a Playwright error while a context closed ("Route is already handled") under heavy machine load; the suite alone and the repeated full run passed |
| Branch coverage (`coverage run --branch`, app modules) | 91 % (7890 statements, 2670 branches); lowest: `credentials.py` 80 %, `store_transfer.py` 83 %, `shelly.py` 86 %; changed in this version: `manager_devices.py` 88 %, `app_options.py` 94 %, `backup_store.py` 91 %, `manager_checks.py` 96 %, `manager_installs.py` 91 %, `web_settings.py` 97 % |
| CVE check | 2026-11-26: PyPI lists no known vulnerability for the ten pinned packages (all at their newest version, hashes match the release files); base image digest unchanged (`c5ba3dee…dd89`), 3.12.16-alpine3.24 is the newest 3.12 alpine tag |
| Docs check (seven points) | changelog bullets match `DOCS.md` (Logs, installation dialog and passwords of several devices updated in this build); options in `config.yaml` (hint field only), `translations/en.yaml`, Settings tab and Configuration agree (incl. `base_color`); feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt` (unchanged); relative links and anchors resolve; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | set of the release kept outside the repository (1440 and 412 px: Devices, Firmware, Schedules, Backups, Logs, Settings); `docs/images/` replaced (new look): Devices desktop, installation dialog, Backups phone |

## Live tests through the beta channel

- rc1 to rc4 tested in Home Assistant by the maintainer (2026-11-25 and 2026-11-26); the
  findings of each candidate were fixed in the next one; rc4 passed without findings.
- The two changes after rc4 are covered by tests (a full check waiting for the update
  server refuses settings, restart and passwords; "keep" refused for an open, switched-off
  or unreported access point, nothing written); the user interface never sends the refused
  case.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly hardware, non-Chromium browsers.
- The dialog "Restart now" after a change that needs a restart was not seen live (no tested
  setting asked for a restart); covered by UI checks.
