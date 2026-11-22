# UI decisions: dialogs, toolbar, tooltips

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change a dialog, the toolbar, card heads or tooltips (all tabs).

## Dialogs (mockups approved 2026-11-12)
- Divider lines under the title and over the buttons, titled sections, value lists
  with fixed label column (34 %, min 96 px), 4 px rows and thin lines, empty rows
  hidden, "Version a → b", model with "Gen n", small SHA-256; buttons unchanged.
- **Dialog title icon** (20 px, `modal(…, titleIcon)`): changelog, delete (red), upload
  (all steps incl. errors), installation dialog (red chip).
- Status dialog (row menu entry "Device Details"): title = device name, message left /
  status pill right, sections Firmware, Device (Model with "Gen n", Firmware ID, NTP;
  empty rows hidden) and History. The phone list ("Details" in the tile) shows the same
  values.
- **Row menu (⋮), five groups with separators:** 1 Check for updates, Install update,
  Downgrade (hint below it) | 2 Device Details, Reload device info ("Not available" hint
  below it), Open web UI | 3 NTP server, Power-on action, Eco mode, Device password |
  4 Ignore / Resume device (removed devices: Remove from list) | 5 Restart (red).
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

## Toolbar and card heads (3.5.0, variant B)
- Same toolbar in all tabs: search, sort and filter only as small round icons (36 px
  desktop, 44 px phones); a click opens a popover under the icon (search field; sort:
  list of fields + Ascending/Descending; filter (3.8.0, replaces the chips): title
  "Filter", one entry per filter with its count right, the chosen one in the primary
  colour with ✓ after the count, optional tooltip); a choice keeps the popover open;
  click outside, a second click on the icon or Escape closes; Enter in the search field
  closes it; dot on the search icon while a search text is set and on the filter icon
  while a filter other than "All" is chosen. Logs: search and filter icons. Start:
  "All", popover closed.
- Desktop order: icons, tab extras right. Phones: icons in row 1, tab extras in row 2,
  popover full width under row 1; "Upload firmware" as round icon at the right end of
  row 2; the Devices row 2 (column chooser) is not shown.
- **Header checkbox (3.8.0, desktop):** the checkbox of the updates card head stands
  exactly above the row checkboxes (same left edge); the title keeps its place.
- Card heads (device groups, firmware IDs, settings cards) in Title Case and the same
  weight (600).
- App title (H1, 2026-11-21): "Firmware Manager for Shelly"; the sidebar entry
  (`panel_title`) stays "Shelly Firmware".
- **Beta mark (3.8.0):** a pre-release (version with a suffix, e.g. `-rc1`) shows a small
  red superscript "(beta)" right of the app title (desktop and phones); inside HA on
  phones (title hidden) it stands on its own line above the header pills, left. A
  final version never shows it (set by the server, `web_ui.is_beta`, test in
  `test_web.py`).
- Phones, title bar (H1): inside HA the app title is hidden; the pills "Last checked …"
  and "Every n days · HH:MM" stacked on the left (ellipsis if too long), "Check now"
  right, vertically centred, never wraps.

## Tooltips on touch devices
- **All other tooltips:** a touch tap shows the tooltip of elements without an own
  action (`attachTip(node, text, true)`: device names, Gen1 version, schedule pill,
  SHA-256, "Uploaded", "intermediate") and the title of a disabled button (pointer events, no click); a
  tap that opens a popup or triggers another action is unchanged; the Firmware-tab info
  icon stays as it is. Touch is detected by the last `pointerdown` type; a second tap
  hides only a tooltip shown by a tap.
- Device names: "Home Assistant device name: …" / "Shelly device name: …".
