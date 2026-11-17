# UI decisions (approved)

Binding detail decisions for the web UI; change only with approval, in the same
commit as the UI change. Read before a UI change; not needed for backend-only work.
Decisions that also define backend behaviour (checks, installs, store, downloads) are
not part of this file.

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
  "Downgrade" disabled without a stored older version.
- Gen1 firmware column: version only.
- Phone tiles: "Ignored" label wrap and truncated names accepted; IP ends in line with
  the ⋮ menu.

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
- Popups opened by an action icon and the notes of greyed-out icons start with a
  **status strip** (32 px tinted icon circle in the action-column colour, feature name,
  state pill On/Off, Protected/Not protected/Unknown, Not available, Not supported;
  restart without pill; power-on pill = current value "Restore last state" / "Mixed");
  question "Turn it on/off for <device>?". The password popup keeps its value list
  under the strip.
- Websocket debug popup: explanation, link `<web_url>#/diagnostics` (new tab,
  noopener), warning below firmware 1.0.0.

## Device settings dialog
- Every change is read back; a value the device accepted but did not apply → warning
  dialog naming the settings, activity log entry failed, `not_applied` in the API
  answer.
- Gen1 power-on labels as in the Gen1 web interface ("Restore last mode", "Switch"),
  Gen2+ "Restore last state", "Match input".

## Firmware tab
- "Install" (red) behind every device a file is provided for (only those); changelog,
  download and delete icon on one line, on phones in the head of the version tile;
  changelog icon shows the changelog stored with the file (no request).
- Version labels 12 px after the version, centred (`.fw-ver` flex); label
  "intermediate" outlined yellow ("Uploaded" stays filled); phones: date right-aligned
  in the size line.
- Delete dialog: both steps with red button.

## Dialogs (mockups approved 2026-11-12)
- Divider lines under the title and over the buttons, titled sections, value lists
  with fixed label column (34 %, min 96 px), 4 px rows and thin lines, empty rows
  hidden, "Version a → b", model with "Gen n", small SHA-256; buttons unchanged.
- **Dialog title icon** (20 px, `modal(…, titleIcon)`): changelog, delete (red), upload
  (all steps incl. errors), installation dialog (red chip).
- Status dialog: title = device name, message left / status pill right, sections
  Firmware and History.
- Settings tab design approved.

## Phone tiles (mockups approved 2026-11-12)
- Checkbox and ⋮ on the name line, status/Install on the Shelly-name line, 12 px
  padding top and bottom, feature buttons 36 px with natural width and every row (incl.
  the last) stretched to the full width, detail list right-aligned with lines, chevron
  right of "Details"; ⋮ and "Details" keep 44 px touch targets.
- Firmware ID only in the details; Ignored label at the status.
- Phone log tile: user top right.

## Tooltips and setting help (2026-11-17)
- Settings tab: help texts not shown under the fields; i-icon (16 px) next to the
  setting name, tooltip on mouse hover over name or icon and on keyboard focus of the
  icon; touch: tap on name or icon toggles it (tap elsewhere or scrolling hides it), a
  touch tap on the name does not focus/toggle the control (desktop click unchanged);
  the help icon has a 44 px touch target on phones. The text stays the field's
  `aria-describedby` (visually hidden); the live warning of the download base URL stays
  under the field.
- **All other tooltips:** a touch tap shows the tooltip of elements without an own
  action (`attachTip(node, text, true)`: device names, Gen1 version, schedule pill,
  SHA-256, "Uploaded") and the title of a disabled button (pointer events, no click); a
  tap that opens a popup or triggers another action is unchanged; the Firmware-tab info
  icon stays as it is. Touch is detected by the last `pointerdown` type; a second tap
  hides only a tooltip shown by a tap.
- Device names: "Home Assistant device name: …" / "Shelly device name: …".
- Inventory with touch behaviour: test report of 3.2.1-rc2
  (`docs/history/reports/` once archived).
