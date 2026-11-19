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

## Firmware tab
- "Install" (red) behind every device a file is provided for (only those); changelog,
  download and delete icon on one line, on phones in the head of the version tile;
  changelog icon shows the changelog stored with the file (no request).
- Version labels 12 px after the version, centred (`.fw-ver` flex); label
  "intermediate" outlined yellow ("Uploaded" stays filled) with a tooltip without action
  ("Intermediate version: required step before newer firmware can be installed
  safely"); phones: date right-aligned in the size line.
- Column titles (2026-11-17, variant A): one head per card over the version rows
  (`.fw-head`, same grid, icon column 112 px without title, 82 px before the downgrade icon): Version · Size · Stored ·
  SHA-256 · Devices; "Version" left, all other titles and contents centred like the
  Devices table, size and date with tabular digits, the opened device list centred as a
  block with left-aligned lines. Phones: no head, small labels Size, Stored, SHA-256
  inside the version tile (`data-label`).
- "Upload firmware" button: upload icon of its dialog title after the text (16 px,
  `aria-hidden`, vertically centred).
- Delete dialog: both steps with red button; the note that a file may not be
  restorable is a red warning box (`warnbox danger`, text in the normal colour).
- **Filter chips (3.5.0):** All, Provided (provided to a device), Uploaded,
  Intermediate, Gen1, Unused (no known device of the firmware ID); counts respect the
  search; a card shows only its matching versions. Summary "n files · x MB stored · y
  free" (free disk space of the store volume; MB, from 1024 MB on GB).
- **Sorting (2026-11-19):** the same sort tool as in the Devices tab sorts the cards: Firmware ID, Newest version, Size (all versions), Stored (latest);
  the column titles Version, Size and Stored sort the cards too (coupled, arrow at the
  same column in every card); versions inside a card stay newest first. Kept per
  browser.
- **"For n devices" (2026-11-19, 3.5.0):** no browser marker and no arrow in the
  text; one chevron after the text (down when closed, up when open), like the Details
  button of the phone tiles but without a border.
- **Downgrade icon (2026-11-19):** history icon before the changelog icon on every
  older (not the newest) version of a Gen2+ card; disabled with the reason as title
  while "Allow downgrades" is off (same hint as the row menu) or no device of this type
  has a newer version installed. Dialog "Downgrade to <type> <version>": value list,
  section with a radio list of these devices (name · installed version), downgrade
  warning, Continue → the known second confirmation.

## Dialogs (mockups approved 2026-11-12)
- Divider lines under the title and over the buttons, titled sections, value lists
  with fixed label column (34 %, min 96 px), 4 px rows and thin lines, empty rows
  hidden, "Version a → b", model with "Gen n", small SHA-256; buttons unchanged.
- **Dialog title icon** (20 px, `modal(…, titleIcon)`): changelog, delete (red), upload
  (all steps incl. errors), installation dialog (red chip).
- Status dialog: title = device name, message left / status pill right, sections
  Firmware and History.
- **Message dialogs (2026-11-19, variant B):** large round icon (40 px circle, 24 px
  icon) left of the text, text in the normal colour: success green check, error red X,
  warning yellow "!" (e.g. not applied / not read back). Neutral notes ("Nothing
  changed.", "This device has no …") without icon. Success: device settings saved,
  restart sent, password done.
- **Password dialog (2026-11-19, variant A):** checkbox "Store in this app" (checked)
  indented directly under the selected mode "Enable protection" / "Change password".
  Storage note as box: checked → Gen2+ yellow (hash), Gen1 red (plain text), each plus
  the reminder for the HA Shelly integration; unchecked → red box "Not stored in this
  app: …" plus the reminder. Result of enable/change/remove: success icon plus a yellow
  box to update the password in the HA Shelly integration. The yellow "Keep the
  password safe …" warning stays.
- Phones (2026-11-19): `#dlg-body` top padding 12 px; warning toast as wide as its
  text, at most the width minus 16 px on each side.
- Settings tab design approved.
- **Settings cards (3.5.0):** Checks, Download Server (download base URL, serve only to
  known Shellys), Firmware & Storage (Gen1 updates, Gen1 types, Allow downgrades, block
  "Firmware store" with export/import, Max. import size), Logs, Advanced, Device
  Passwords. Option labels with "Max." in front. Units in texts always "MB".
- **Changelog hint (3.5.0, 2026-11-19):** last line of the Settings tab, below
  the Save area: hint "What changed in each version: open the app page in Home
  Assistant and choose “Changelog” next to the version." – the link opens the info
  page of the app in the whole window (`/config/app/<slug>/info`, slug from the
  Supervisor; the list of apps until it is known). The Configuration tab of the app
  page has the same names, help texts and order as the Settings tab; its last help
  text ends with the same hint (HA shows no links there).

## Toolbar and card heads (3.5.0, variant B)
- Same toolbar in all tabs: search and sort only as small round icons (36 px desktop,
  44 px phones); a click opens a popover under the icon (search field; sort: list of
  fields + Ascending/Descending, checked entries marked); click outside or Escape
  closes; Enter in the search field closes it; dot on the search icon while a search
  text is set. Logs: search icon only.
- Desktop order: icons, chips, tab extras right. Phones: icons and chips share row 1
  (chips scroll), tab extras in row 2, popover full width under row 1; "Upload
  firmware" as round icon at the right end of row 2; the Devices row 2 (column
  chooser) is not shown.
- Card heads (device groups, firmware IDs, settings cards) in Title Case and the same
  weight (600).
- Phones, title bar (H1): inside HA the app title is hidden; the pills "Last checked …"
  and "Every n days · HH:MM" stacked on the left (ellipsis if too long), "Check now"
  right, vertically centred, never wraps.

## Firmware store and device passwords (approved 2026-11-19, 3.4.0)
- **Entry points (8a B, 7a B; places changed in 3.5.0):** Settings tab: block
  "Firmware store" in the card "Firmware & Storage" (buttons "Export store …" /
  "Import store …" with export/import icons) and card "Device Passwords" (button
  "Device passwords …" with lock icon).
- **Export (8b B):** dialog with value list (files with downloaded/uploaded, size, file
  name), the generated password in a read-only field with copy icon (shown only here),
  hint that it seals and is stored nowhere, checkbox "I have saved the password";
  "Export" is enabled only after it and starts the browser download.
- **Import (8c A):** step 1 file + password + "Check file" (the hint names the maximum
  import size). Step 2: success icon, value list (file, exported, size), section
  "Result" with counts new / already stored – data added if missing / conflict – stored
  file kept; each group expandable, conflicts expanded; button "Import N files"
  ("Finish import" without new files). Errors as error message.
- **First start (8d both):** dialog "Welcome – import a firmware store?" (Skip /
  Import store …) once per browser; then the yellow notice bar with the same buttons
  until answered (it takes precedence over the download base URL notice). "Check now"
  is disabled with a tooltip; empty device list: "they are found by the first check".
- **Device passwords for several devices (7b, mockup `Passwoerter_Dialog_final`):**
  sections Action (store existing / set password, set preselected), Devices ("All
  devices that fit (n of m)", one row per device with name, "Gen · protection state"
  and the step "→ enable protection" / "→ change password"; devices that do not fit are
  greyed out with the reason), Password (+ repeat for set), "Store in this app"
  (unchecked → red box), yellow "Keep the password safe" box. Red button "Set on n
  devices …" → second confirmation with "I have stored the password securely" →
  result per device (green check / red X / yellow "!" skipped) plus the yellow HA
  Shelly integration reminder.
- **Details confirmed with the live test of 3.4.0-rc1:** "Store the existing password"
  for several devices also gets a second confirmation (without "stored securely"),
  button "Store for n devices …" (blue); busy devices (installing, being checked, no
  IP) are greyed out with "busy or not reachable – try again later"; a password over 50
  characters is refused in the dialog while a Gen1 device is selected; the log entries
  of export, import and the skipped first start appear under the filter "All" only.
- **Upload progress (3.4.1):** firmware upload and store import send the file in parts
  and show "Uploading <file> … n %" in the dialog, then "Checking <file> …" (import).

## Phone tiles (mockups approved 2026-11-12, T1 of 2026-11-19 in 3.5.0)
- Line 1 checkbox, name (600) and ⋮; line 2 Shelly name + pencil (lighter) left, IP
  right (grey, no underline, not monospace, ↗, link to the web UI); line 3 firmware
  left, status pill + Install right; then "Details" as outlined button (36 px, full
  content width, centred text + chevron down/up, like the feature buttons). 12 px
  padding top and bottom, feature buttons 36 px with natural width and every row (incl.
  the last) stretched to the full width, detail list right-aligned with lines; ⋮ keeps
  its 44 px touch target.
- Firmware ID only in the details; Ignored label at the status.
- Phone log tile: user top right; one arrow only (none beside the user name when
  opened, the name keeps its place); no expansion when the details only repeat the
  summary (2026-11-19).

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
  SHA-256, "Uploaded", "intermediate") and the title of a disabled button (pointer events, no click); a
  tap that opens a popup or triggers another action is unchanged; the Firmware-tab info
  icon stays as it is. Touch is detected by the last `pointerdown` type; a second tap
  hides only a tooltip shown by a tap.
- Device names: "Home Assistant device name: …" / "Shelly device name: …".
