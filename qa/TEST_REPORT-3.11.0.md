# Test report 3.11.0

Final release 3.11.0 on branch `release-3.11.0` (base: `main` = `v3.10.0`), after 3.11.0-rc1
to rc6, all tested live through the beta channel. Changes after rc6: version, merged
changelog section, code map (`index.json` of the backups, `running` of scripts), one test
comment; no change of the app code.

## Scope

| Area | Change |
|---|---|
| Backups | Backups tab, "Back up now" (one, chosen, all), backup before every installation (option), schedule action "Backup" (only changes), comparison and restore part by part, retention per device, download and import with a one-time password, part of the store export, encryption with the app key |
| Robustness of backups | key never overwritten, seal of every file checked at the start, hidden rows keep their marks, restore spares its own backup, another Shelly at the address refused, imported digest from the content |
| Errors | an unreachable device is an error in checks, backups and schedule runs; a battery device asleep stays a warning |
| UI | Logs "!" red for unread errors, unread entries highlighted; contrast at least 4.5:1, one warning colour; keyboard basics; input errors stay in the dialog; X and Escape cancel; names sorted like the tables; power-on values in English; phone fixes (Firmware tab labels, popups, page behind dialogs) |
| Backend | split of `shelly.py`, `manager_store.py`, `web_ui.py` without behaviour change; one atomic write, one SHA-256 helper, one token rule (`Pending`) |
| Image and CI | modules compiled at build time; start test of the image before the push; pytest-asyncio in CI |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.11.0, pytest, UI, layout gate | passed: 641 pytest; ui_test 210/210; ui_features 1314/1314 (suite `backups` 341/341); layout gate 42/42 (now with the Backups tab) |
| Branch coverage (`coverage run --branch`, app modules) | 91 % (7126 statements, 2424 branches); lowest: `web_settings.py` 79 %, `credentials.py` 80 %, `app_options.py` 82 %, `store_transfer.py` 83 %; `backup_store.py` 91 %, `manager_backups.py` 90 %, `shelly_backup.py` 91 % |
| CVE check | 2026-11-23: PyPI lists no known vulnerability for the ten pinned packages (all at their newest version, unchanged since 3.10.0); base image digest unchanged, 3.12.16-alpine3.24 is the newest 3.12 alpine tag |
| Docs check (seven points) | changelog bullets match `DOCS.md` (Backups, Logs, Settings, Troubleshooting, Privacy, Security; keyboard, contrast and sorting are UI details in `docs/ui/common.md`); options in `config.yaml`, `translations/en.yaml`, Settings tab and Configuration agree (`backup_before_install`, `backup_keep` new); feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt` (unchanged); relative links and anchors resolve; section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | set of the release kept outside the repository (1440 and 412 px: Devices, Firmware, Schedules, Backups, Logs, Settings, details); `docs/images/` replaced in rc4 (Devices desktop, installation dialog, Backups on a phone) |

## Live tests through the beta channel

- rc1 to rc6 tested in Home Assistant by the maintainer (2026-11-23); the findings of each
  candidate were fixed in the next one; rc6 passed without findings.
- The fixes of the code review in rc6 are covered by tests; their visible parts were part
  of the rc6 live test.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly hardware, non-Chromium browsers;
  restore of a backup that names another Wi-Fi (no such backup in the live test); a real
  disk read error at the start (covered by tests only).
