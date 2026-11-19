# Test report 3.3.0-rc1

Release candidate of 3.3.0 on branch `release-3.3.0` (base: `main` = `v3.2.1`).
Targeted tests of the changed areas; the full review (full UI matrix, layout gate,
security/dependency/container review, CVE check) follows before the final release.

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
| `tools/dev/check.sh` (ruff, mypy --strict, build check, version 3.3.0-rc1, pytest) | passed, 204 tests |
| `tools/dev/check.sh ui` (UI quick matrix + feature checks) | passed, ui_test 75/75, features 307/307 |
| New backend tests | password enable/change without storing (incl. unconfirmed change keeps the stored password, invalid `keep`), downgrade refused while the option is off (blocker, `install`, HTTP 409, state flag), option `allow_downgrade` optional/boolean |
| New UI checks (`firmware_store`) | sort select in both toolbars, per-browser storage, coupling with Devices and Firmware column heads, Firmware-tab downgrade dialog and second confirmation, row menu disabled with hint while the option is off; one i-icon per setting (11) |
| Screenshots | `qa/screenshots/3.3.0-rc1/` (1440 px and 412 px): Devices, Firmware, Firmware downgrade dialog, password dialog (stored / not stored), Logs, row menu with downgrades off, Settings switch |

## Not tested here

- Real Home Assistant, Ingress and Supervisor (option schema `bool?` in the app
  configuration, saving the new option), real Shelly devices (password change without
  storing, downgrade from the Firmware tab).
- Browser storage inside the HA Ingress iframe on real browsers (Chromium only here).
- Success popups with status icon after real device actions (the demo has no device
  access; the dialogs were checked in the code and with the warning/error variants).
