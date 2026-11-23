# UI decisions: Devices tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Devices tab, its action icons, device dialogs or the phone tiles.

## Devices table and phone tiles
- Gen1 AP read-only. Bluetooth only global on/off where supported, else disabled icon
  with explanation. Ignored: own group, yellow "Ignored" label, no install until
  resumed; name, IP, firmware and icons dimmed (70 %), status and "Ignored" at full
  strength (2026-11-22, L3).
- **Columns (2026-11-22, mockup `reports/3.8.0-design/M7`):** round icon at the right end
  of the toolbar (desktop only); popover like sort and filter: title "Columns", one entry
  per data column with ✓ while shown (a click keeps the popover open), "Reset columns"
  last; blue dot on the icon while the shown columns or their order differ from the
  default. "Reset columns" shows "Host name" while the option "Resolve host names" is on.
- **Sections (3.8.0, mockups `3.8.0/01`, `02`):** one card per section in this order,
  only sections with devices: Updates Available, Attention (error, unconfirmed
  installation, no usable address), Not Checked (never checked, Gen1 skipped while Gen1
  updates are off; status pill "Not checked" for both), Up-To-Date, Asleep, Ignored,
  Removed, then the read-only card "Other Shellys". The server names the section
  (`section`); the UI keeps its rule that a device stays in the updates card while it
  is checked. Card titles in Title Case ("Not Checked"); the header checkbox and
  "Review & install" only in the updates card.
- **Removed (3.8.0, mockup `3.8.0/04`):** yellow "Removed" pill next to the status (like
  "Ignored", which a removed device loses); tooltip also on touch: still reachable →
  "Removed from Home Assistant / the Shelly integration, but still reachable at its last
  address. All actions stay available; “Check now” only tests whether it still answers.",
  not reachable → "… and no longer reachable. “Check now” only tests whether it answers
  again.". Row menu group 4: "Remove from list"
  instead of "Ignore device", confirmation "Remove from list?" (Cancel / red Remove).
- **No usable address (3.8.0):** IP column (and the phone tile instead of the IP):
  warning triangle + "No usable address" (warning colour), tooltip with the server's
  text (also on touch); wide screens: the reason below the Shelly name with a small
  triangle – "No address in Home Assistant", "Host name does not resolve", "Address not
  allowed (public IP)".
- **Other Shellys (3.8.0):** card head "Other Shellys · n" + "Display only – no update
  function here"; columns Name, Kind, Model, Firmware, Where to update (left-aligned,
  no actions). Kinds: BLU TRV (BLU behind a gateway), BLU (Bluetooth), Zigbee (ZHA),
  Zigbee2MQTT, MQTT, Matter; "Where to update" per kind (BLU TRV names its gateway).
  Phones: name + kind, model + firmware (bold, right), where to update. Shown under the
  filter "All" (search applies) and "Other Shellys" only.
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
- **Filter (3.8.0, mockup `3.8.0/03`):** popover of the filter icon (see common.md
  "Toolbar"): All, Updates, Attention, Not checked, Up-To-Date, Unprotected, Asleep,
  Ignored, Removed, Other Shellys – the order of the sections (Unprotected after
  Up-To-Date, Asleep directly above Ignored); counts respect the search. A filter shows
  only its section(s); Unprotected keeps the rows in their sections. Tooltips: Attention,
  Not checked, Asleep, Removed ("Devices removed from Home Assistant / the Shelly
  integration"), Other Shellys. "Unprotected" = `auth_enabled` false on the device
  (unknown does not count); filter only, no action. Spelling "Up-To-Date" like the
  status label.
- **Host name column (3.8.0, mockup `3.8.0/05`):** optional column "Host name" (between
  "Last update" and "NTP" in the Columns menu); phones: row "Host name" in the details.
  Shows the host name of the HA entry, else `<device id>.local`, only when it resolves
  ("–" otherwise). Exists only while the option "Resolve host names" is on (off: no
  column, no Columns/Sort entry, no phone row); switching the option on shows the column
  once per browser, the Columns menu can hide it again.
- **Battery devices (2026-11-19, 3.5.0):** battery icon (16 px,
  secondary text colour, tooltip "Battery device: reachable only while it is awake" and
  on a second line "Detected from the device" / "Detected from Home Assistant", 3.8.0)
  right after the HA name in the table and the phone tile head (before the "Gen n"
  label). A battery device that did not answer the last check shows the grey status
  pill "Asleep" (section "Asleep", not "Attention") and its IP dimmed (opacity 0.5). No queue,
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
- Action column: `.act-strip` 170 px, two rows (5 + 5 since the cloud icon), column 190 px.
- Wide screens (2026-11-17): titles and content of all columns except "Device"
  centred (status cell and action strip too); IP and Firmware with
  `font-variant-numeric: tabular-nums`; IP in the normal font (2026-11-22, U11). Phone
  tiles unchanged.
- Popups opened by an action icon and the notes of greyed-out icons start with a
  **status strip** (32 px tinted icon circle in the action-column colour, feature name,
  state pill On/Off, Protected/Not protected/Unknown, Not available, Not supported;
  restart without pill; power-on pill = current value "Restore last state" / "Mixed"; a
  pill too long for the line moves below the name, right-aligned).
  Switch popups (eco, access point, Bluetooth, websocket debug log) ask no question:
  strip + button, the button names the action ("Turn on" / "Turn off"). Restart popup:
  no question, hint "The device will be unavailable for a few seconds.", button
  "Restart" (2026-11-17). The password popup keeps its value list under the
  strip.
- Websocket debug popup: explanation, link `<web_url>#/diagnostics` (new tab,
  noopener), warning below firmware 1.0.0.

## Cloud, Bluetooth proxy, offer delay, missing Shellys, installation (3.9.0, mockups `3.9.0`, approved 2026-11-22)
Wordings: `mockups/3.9.0/TEXTE.md`.
- **Cloud (F13, F14):** tenth icon of the action column after Bluetooth (MDI cloud),
  yellow while on, grey off, "?" unknown; tooltip "Cloud: on – connected to the Shelly
  cloud" / "… – not connected at the moment" / "Cloud: off"; phones: button "Cloud" in the
  details. Popup: status strip (yellow while on), row "Connection" while on, hint text,
  "Turn off" / "Turn on". Filter "Cloud on" after "Unprotected".
- **Bluetooth proxy (F15):** while Home Assistant's script runs on the Shelly
  (`ble_proxy`), the Bluetooth popup shows a yellow box (Home Assistant turns Bluetooth
  on again; switch "Bluetooth scanner mode" off first); "Turn off" stays possible; the
  icon tooltip adds "– used by Home Assistant as Bluetooth proxy".
- **Offer delay (F7, F8):** a held device stays in "Up-To-Date"; below the firmware ID a
  clock line "<version> from <date>" with the tooltip (build date, age, days of the
  option, date). Filter "On hold" after "Cloud on", only while the option is above 0.
  Row menu "Offer update now" after "Install update" (lifts the hold, checks the device).
  Device details: "Available: <version> · on hold until <date>".
- **Device details (F5, F22):** rows "Build date" (offered or held version, date only)
  and "Way back" ("<version> is stored" / "is not stored", Gen2+).
- **Shellys not listed (F10, F11):** line "N of M Shellys listed · Why are K missing?"
  (link style) only while at least one is missing: right in the toolbar before
  "Columns" on wide screens, above the first card on phones; opens the dialog "Shellys
  not listed" (intro, section "Not listed · K" with name, model, yellow reason tag and
  what to do; section "Still missing?").
- **Installation dialog (F4, F6, F12):** section "Before installing" after "Firmware" as a
  check list (green check / yellow "!"): "Way back" (Gen2+) and "Wi-Fi signal" (read when
  the dialog opens, "Reading …"; −80 dBm and below weak; devices on the cable: no line;
  not readable: yellow line "Wi-Fi signal: unknown", a finding, approved 2026-11-22). Several devices: only the findings per device, else one line
  "every device". Row "Build date" (date · age) in the section "Firmware" (one device).
  After a failed installation: section "Last attempt" with the reason, pre-ticked
  "Restart the device first" and the button "Restart & install".
- **Backups (3.11.0, mockups `3.11.0-backups`, approved 2026-11-22):** row menu group 3:
  "Backups" last, after "Device password" (F15). Device details: section "Backups" (Last
  backup, Stored, small button "Show backups"). Dialog "Backups" of a device: name, model,
  pill "n of N stored", its backups (rows as in the Backups tab, see `backups.md`),
  "Import backup" / "Close" / blue "Back up now". Installation and downgrade dialog: check
  line "Backup" in "Before installing" while "Back up before every installation" is on; a
  failed backup stops the installation with the reason, also for "Install at" (F6).
- **Imported files (F18, F21):** source "Firmware store (imported file)" (table: "Import");
  offered only while Shelly cannot be asked: yellow box (unconfirmed origin, no server;
  Gen1 text without the intermediate-version sentence) and the checkbox "I accept the risk
  of installing a file that Shelly has not confirmed". Downgrade to an imported file: box in
  the dialog and the last confirmation, its checkbox also names the unconfirmed file.

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

## Beta and scheduled installations (3.10.0, mockups `3.10.0`, approved 2026-11-22)
- **Beta:** section "Beta Updates" after "Updates Available" with its own
  select-all and "Review & install"; violet "Beta" tag beside the firmware ID; filter "Beta
  updates"; dialog "Install beta update?" with "(beta)" after the target version, the
  yellow beta box and the tick (button disabled until ticked).
- **Scheduled installation (F5):** blue pill "Scheduled" in place of "Ready",
  clock line "Install at <date time>" under the version, no checkbox and no "Install";
  row menu group 1 adds "Install now" and "Cancel scheduled installation"; filter
  "Scheduled"; row "Scheduled" in the device details.
- **Downgrade while scheduled (review 3.10.0, F5 a, approved 2026-11-22):** row menu
  "Downgrade" disabled with the menu hint "Cancel the scheduled installation first" (same
  rule as "Install").
- **Running downgrade (review 3.10.0, F1 a, approved 2026-11-22):** the device stays in the
  section it had before (usually "Up-To-Date"); Firmware column "installed → target" with
  the tag "Downgrade" beside the firmware ID; status pill "Downgrading n %".
