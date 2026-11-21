# UI decisions: Devices tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Devices tab, its action icons, device dialogs or the phone tiles.

## Devices table and phone tiles
- Gen1 AP read-only. Bluetooth only global on/off where supported, else disabled icon
  with explanation. Ignored: own group, yellow "Ignored" label, no install until
  resumed.
- "Install update" only for Ready (or a failed installation or a store fallback), not
  ignored, no check running. Red "Install" button next to the status (table and phone
  tiles; on phones the size of the status pill, 32 px).
- A confirmed installation keeps the device in "Updates available" as "Updated", an
  unconfirmed one with the status "Unconfirmed" (both: no checkbox, same indentation;
  not after a downgrade); a **failed installation** stays there as "Error" **with
  checkbox and Install** (marker `install_failed`, file stays provided; not after a
  failed downgrade); the result of a single check of the device or a full check decides
  afterwards – while the check runs the device stays in the card (UI `shownUpdates`).
- Phones: "Ignored" and "Checking…" labels 32 px like the status pill. Row menu
  "Downgrade" disabled without a stored older version; while the option "Allow
  downgrades" is off it is disabled with the menu hint "Downgrades are off – enable
  “Allow downgrades” in Settings" below it (Gen2+ only, 2026-11-19).
- **Sorting:** sort tool of the toolbar (see "Toolbar") with all sortable columns, also
  hidden ones. Desktop column heads keep sorting and share the state with the sort tool.
  Sort order, hidden columns and column order are kept per browser (localStorage);
  start: device name ascending.
- **Filter chips (3.5.0):** All, Updates, Errors, Up-To-Date, Unprotected, Ignored.
  "Unprotected" = `auth_enabled` false on the device (unknown does not count); filter
  only, no action. Spelling "Up-To-Date" like the status label.
- **Battery devices (2026-11-19, 3.5.0):** battery icon (16 px,
  secondary text colour, tooltip "Battery device: reachable only while it is awake")
  right after the HA name in the table and the phone tile head (before the "Gen n"
  label). A battery device that did not answer the last check shows the grey status
  pill "Asleep" (not listed under "Errors") and its IP dimmed (opacity 0.5). No queue,
  no "Check queued" pill. Tooltip of the "Asleep" pill (2026-11-19): only the wake-up
  hint and "Last contact: <date>" (last successful check, date as elsewhere).
- Gen1 firmware column: version only.
- Phone tiles: "Ignored" label wrap and truncated names accepted.

## Action icons
- Power-on action = lightning, green if all outputs are "Restore last state" (Gen1
  "Restore last mode"), red if any differs, "?" unknown.
- Lock green = protected and password stored in the app, yellow = protected without
  stored password, else grey.
- Access point (MDI "access-point"), actions (link icon) and schedules red while
  active.
- **Websocket debug log** (MDI "bug", Gen2+, `debug_ws`: read from `Shelly.GetConfig`
  `sys.debug.websocket.enable`, written via `Sys.SetConfig` with only that field, read
  back like other settings) grey off, yellow on, "?" unknown, Gen1 greyed out ("no
  websocket debug log").
- Action column: `.act-strip` 170 px, two rows (5 + 4), column 190 px.
- Wide screens (2026-11-17): titles and content of all columns except "Device"
  centred (status cell and action strip too); IP and Firmware with
  `font-variant-numeric: tabular-nums`. Phone tiles unchanged.
- Popups opened by an action icon and the notes of greyed-out icons start with a
  **status strip** (32 px tinted icon circle in the action-column colour, feature name,
  state pill On/Off, Protected/Not protected/Unknown, Not available, Not supported;
  restart without pill; power-on pill = current value "Restore last state" / "Mixed").
  Switch popups (eco, access point, Bluetooth, websocket debug log) ask no question:
  strip + button, the button names the action ("Turn on" / "Turn off"). Restart popup:
  no question, hint "The device will be unavailable for a few seconds.", button
  "Restart" (2026-11-17). The password popup keeps its value list under the
  strip.
- Websocket debug popup: explanation, link `<web_url>#/diagnostics` (new tab,
  noopener), warning below firmware 1.0.0.

## Device settings dialog
- Every change is read back; a value the device accepted but did not apply → warning
  dialog naming the settings, activity log entry failed, `not_applied` in the API
  answer.
- Gen1 power-on labels as in the Gen1 web interface ("Restore last mode", "Switch"),
  Gen2+ "Restore last state", "Match input".

## Phone tiles (mockups approved 2026-11-12, T1 of 2026-11-19 in 3.5.0)
- Line 1 = tile head like the head of a firmware card (2026-11-19): checkbox, name
  (600, card-head size) with the "Gen n" label (omitted while the generation is unknown)
  and ⋮, divider line over the full tile width; line 2 Shelly name + pencil (lighter) left, IP
  right (grey, no underline, not monospace, ↗, link to the web UI); line 3 firmware
  left, status pill + Install right; then "Details" as outlined button (36 px, full
  content width, centred text + chevron down/up, like the feature buttons). Outline of
  "Details" and the feature buttons in the tile line colour (at least 3:1 in light and
  dark themes, 2026-11-19). 12 px
  padding top and bottom, feature buttons 36 px with natural width and every row (incl.
  the last) stretched to the full width, detail list right-aligned with lines; ⋮ keeps
  its 44 px touch target.
- Firmware ID only in the details; Ignored label at the status.
- Check entries (2026-11-19): ignored devices of a full check counted in the summary
  ("…, n ignored") and listed after the findings as "<name>: ignored – not checked" in
  the normal text colour (also inside an error entry); no influence on the severity.
- Phone log tile: user top right; one arrow only (none beside the user name when
  opened, the name keeps its place); no expansion when the details only repeat the
  summary (2026-11-19).
