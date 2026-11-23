# UI decisions: dialogs, toolbar, tooltips

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change a dialog, the toolbar, card heads or tooltips (all tabs).

## Building blocks (3.11.0, `reports/optimierung-analyse` F1 a, approved 2026-11-23)
- New UI builds from the building blocks of `ui-src/js/core.js`, not from its own copies:
  `confirmDialog` (confirmation; red button unless `kind` says otherwise; an error keeps
  title and icon), `gateButton` / `ackGate` (a condition or a tick enables the action
  button), `guarded` (error of an action), `input`, `checkbox`, `radio`, `field`,
  `checkLine` (ok, warn, err, run, wait), `deviceChoice` ("All" or a list of devices),
  `passwordCopy`, `filePassword`, `startDownload`, `cell`, `tn` (singular/plural),
  `fmtSize`, `modelGen`, `searchText` / `matches`.

## Dialogs (mockups approved 2026-11-12)
- Divider lines under the title and over the buttons, titled sections, value lists
  with fixed label column (34 %, min 96 px), 4 px rows and thin lines, empty rows
  hidden, "Version a → b", model with "Gen n", small SHA-256; buttons unchanged.
- **Dialog title icon** (20 px, `modal(…, titleIcon)`): changelog, delete (red), upload
  (all steps incl. errors), installation dialog (red chip), downgrade (history icon).
- **Dialog rules (2026-11-22, `reports/3.8.0-design` U8, U9, T4):** (a) every delete dialog
  has the red trash icon before the title ("Delete firmware?", "Delete unknown file?",
  "Delete log entry?", "Delete all logs?", "Remove from list?"), every downgrade dialog the
  downgrade (history) icon, in all steps incl. errors. (b) Multi-step dialogs: step 1 is
  always "Continue" in blue; only the last button that triggers the action is red. (c) An
  error dialog keeps the title (and icon) of the dialog it comes from (e.g. "Install
  update?", "Delete firmware?", "Save settings?"); errors without a dialog keep their
  short title ("Check", "Ignore", "Settings").
- **Input errors (3.11.0, `reports/optimierung-analyse` F3 a, approved 2026-11-23):** an
  error about the entries of a form step stays in that step, below its content (red text,
  announced), and every entry stays as typed or chosen: a check before sending (no file,
  no password, passwords differ, no device) and a refusal of the sent entries (upload,
  store import, backup import, schedule, upload values, NTP server, power-on action).
  An upload or import that was refused shows the step again with the chosen file and
  the password. Errors after a confirmation step keep the error dialog of rule (c).
- **Tables in dialogs (2026-11-22, F1, U7):** plain tables, left-aligned, on every width
  (not the Devices table or phone tile layout). Phones: "Install n updates?" shows two-line
  entries (name; below it IP · installed → target · source, mockup `M9`); desktop keeps
  the table; "Power-on action" stays a table.
- Status dialog (row menu entry "Device details"): title = device name, message left /
  status pill right, sections Firmware, Device (Model with "Gen n", Firmware ID, NTP;
  empty rows hidden) and History. The phone list ("Details" in the tile) shows the same
  values.
- **Keyboard (3.11.0, `reports/optimierung-analyse` F5 a, approved 2026-11-23):** a redraw
  of a tab keeps the keyboard focus on the same control (rows and tiles are matched by
  their `data-key`, the control by its label). A dialog is named by its title and starts
  with the focus on its first control (the close icon) unless it focuses a field itself
  (`reports/3.11.0/FRAGEN_rc2.md` 1 b, approved 2026-11-23); a step that removes the
  focused control moves the focus there too; closing returns it to the control that
  opened the dialog. The X and Escape do what "Cancel" / "Close" of the step does
  (discard a staged upload or import, return to the list of the device's backups).
  Escape closes the row menu and returns the focus to its ⋮ button.
  Column heads sort with Enter and Space and carry `aria-sort`; the tabs carry
  `aria-selected` and name their panel. Error and warning messages in dialogs are
  announced (`role="alert"`), successes as status. No arrow keys in menus and tabs.
- **Row menu (⋮), five groups with separators:** 1 Check for updates, Install update,
  Offer update now (only while an update is on hold), Downgrade (hint below it) | 2 Device details, Reload device info ("Not available" hint
  below it), Open web UI | 3 NTP server, Power-on action, Eco mode, Device password,
  Backups (3.11.0) |
  4 Ignore / Resume device (removed devices: Remove from list) | 5 Restart (red). Phones:
  every entry incl. "Open web UI" 44 px high.
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
  password safe …" warning stays. Value list under the strip: only "Stored in this app"
  (the protection state stands in the strip, 2026-11-22). Storing an existing password:
  button "Test & store".
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
- **Line under the header pills (live test rc2, approved 2026-11-23):** only for
  installations, a device info reload, a restore and storage errors; never for a check
  (a single check shows "Checking …" in its row, a full check its progress line) or a
  schedule run.
- **"Check now" in the Settings tab (2026-11-22):** hidden there (it belongs to the
  device data), its place is kept so the header does not move when the tab changes; all
  other tabs show it.

## Texts, controls and design basics (2026-11-22, `reports/3.8.0-design`)
- **Spelling (T1–T3, T6):** card titles and status labels in Title Case; menus, buttons,
  filters and tags in sentence case with a capital start ("Device details", "Latest",
  "Intermediate"). "Gen 2" for one device; "Gen1"/"Gen2+" only as a group in running text.
  One ellipsis form with a space ("Checking …", "Loading changelog …"); a shortened value
  keeps "…" without space (SHA). Buttons carry no "…" (a dialog may follow). "Save" for
  settings, "Store" for keeping something in this app ("Test & store").
- **Icons in buttons (T8):** before the text ("Upload firmware", "Export store", "Import
  store", "Device passwords").
- **Controls:** fields and selects with the strong line colour on every width (at least
  3:1, mockup `M6`); buttons, cards and checkboxes keep the fine line; phone tiles of
  all tabs (Devices, Logs, Schedules, Backups) carry the strong line. Heights (U10): desktop
  33 px for text buttons (also "Check now" and dialog buttons), fields, selects and the
  time field (mockup `M8`; icons, pills, "Install" and tabs unchanged); phones 44 px for
  all of them and the file chooser. Checkboxes and radios in the theme's primary colour;
  the file chooser's button like a normal button.
- **One disclosure sign (U3):** a chevron after the text (down when closed, up when open):
  "Details" of the phone tiles, "For n devices", log entries (desktop and phone tiles),
  changelogs and groups in dialogs.
- **IP addresses (U11):** normal font with equal-width digits everywhere (table, tiles,
  dialogs); the link of the table stays blue and underlined.
- **Design tokens (`ui-src/style.css` `:root`):** four font sizes (11 tags and section
  titles, 12 secondary and hint text, 14 text, 15 card titles; app and dialog titles
  apart), three radii (8 fields/menus/popovers/notices/tooltips, 12 pills/tags/tiles/
  cards/dialogs, the theme's button radius for buttons and outlined tile buttons), two
  lines (fine = theme divider, strong = at least 3:1), one warning text colour
  (`--warn-text`), control height (33 / 44 px). New styles use these tokens.
- **Warning text (3.11.0, live test rc3, 2026-11-23):** every yellow text uses
  `--warn-text`: log lines, tags, pills, "Unsaved changes", the header status and hints.
  It is the warning colour turned towards orange (70 % warning, 30 % error colour), then
  mixed 55 % towards the text colour: at least 5:1 on cards and unread log rows in the
  light fallback colours, at least 8:1 in the dark ones, and clearly apart from the green
  success text. The pure warning colour stays for lines, fills and icons.
- **Contrast of coloured text (3.11.0, mockups `3.11.0-kontrast` variant A, approved
  2026-11-23):** coloured text (links, new version, active tab, chosen entry, error text,
  grey tags) is mixed towards the text colour (`--link-text`, `--error-text`,
  `--tag-text`) and reaches at least 4.5:1 on cards and unread log rows in the light and
  dark fallback colours; the theme colour is used pure only for lines, fills and icons.
  Icons that are links or mark a chosen entry (link icons, tick of the chosen filter,
  "Kept" pin, opened toolbar icon) use the link text colour.
- **CSS building blocks (3.11.0, `reports/optimierung-analyse` F1 a, approved 2026-11-23):**
  `ui-src/style.css` is one file in the order tokens, base, building blocks, page head
  and tabs, then one section per tab with its own phone block. A coloured state reads
  `--tone` (pills, action icons, status strips, message and check-list icons), set by
  `.tone-success|warning|error|primary` or a state class mapped there; neutral is the
  default. Rows of a list share one line rule (fine line, none under the last row);
  phone tiles have the card background, the strong line and the medium radius (one rule
  for Devices and Schedules; Logs and Backups set it in their tab, after their own rules
  for the last row). The
  Devices table is `:where(.dev-table)`, so its rules never reach a table in a dialog.
  A button with an icon before its text is `.icon-text` (`.sm`: 16 px icon). Tokens
  also cover the monospace font, shadows, layers, icon sizes, the beta colour and the
  success text; `[hidden]` always hides. New UI uses these blocks and adds CSS only for
  its own grid or layout.

## Tooltips
- **One tooltip (2026-11-22, U4):** every tooltip is the app's own dark tooltip, none
  uses the browser's `title` (icon buttons, toolbar tools, "Install", IP link, column
  heads, "Check now"): `setTip` (text in `data-tip`) or `attachTip`.
- **Touch devices:** a touch tap shows the tooltip of elements without an own
  action (`attachTip(node, text, true)`: device names, Gen1 version, schedule pill,
  SHA-256, "Uploaded", "Intermediate") and the tooltip of a disabled button (pointer events, no click); a
  tap that opens a popup or triggers another action is unchanged; the Firmware-tab info
  icon stays as it is. Touch is detected by the last `pointerdown` type; a second tap
  hides only a tooltip shown by a tap.
- Device names: "Home Assistant device name: …" / "Shelly device name: …".

## Tabs, start of an installation, language (3.10.0, mockups `3.10.0`, approved 2026-11-22)
- **Tabs (F8, F9, F11; 3.11.0 F1):** Devices, Firmware, Schedules, Backups, Logs, Settings;
  "Schedules" with the calendar-clock icon, "Backups" with the backup-restore icon.
  Phones: only the active tab shows its name, the others only their icon (variant B; icon
  tabs 44 px wide, so that six tabs and the longest label fit 360 px in both languages).
  The device action icon for the Shelly's own schedules is named "Device schedules".
- **Page behind a dialog (3.11.0, approved 2026-11-22):** while a dialog is open the page
  behind it does not scroll (the dialog scrolls inside); a click outside does not close
  a dialog.
- **Installation dialog, section "Start" (F1, F2, F6):** radios "Now" / "Install
  at" with date and time (once, at most 7 days ahead, time zone of Home Assistant); with
  "Install at" the red button reads "Approve & schedule", the title stays "Install
  update?", the hint under the fields names the conditions (F3) and where to cancel. A
  beta needs its tick also when scheduling. The section stands above the changelog
  (live test rc1, approved 2026-11-22).
- **Logs tab, unread entries (live test rc1, approved 2026-11-22; 3.11.0, red: F10 b,
  approved 2026-11-23):** a small red round "!" (white sign) after the tab name (phones:
  after the icon) while the log holds an error entry
  newer than the newest entry this browser has shown in the Logs tab – warnings and
  successes never set it; opening the tab clears it. The entries that were unread at that
  moment are lightly highlighted (primary colour, 8 %) until the tab is left.
- **Language (F20–F25):** card "Display" at the end of the Settings tab with "Language":
  "Same as Home Assistant" (default, fallback English), English, Deutsch; applies at once,
  per browser, not part of "Save settings". German uses "du" (without address where
  possible). Logs tab: controls, filter and weekday translated, the entries stay English
  including their type and outcome labels. Dates and times keep the form
  "2026-11-23 03:00" in every language. Texts from the server or the Shelly stay English.
  No in-app note on machine translation (README only). German terms:
  Geräte, Zeitpläne, Einstellungen, Jetzt prüfen, Updates verfügbar, Beta-Updates, Achtung,
  Aktuell, Bereit, Verfügbar, Geplant, Fehler, Installieren, Prüfen & installieren,
  Freigeben & einplanen, Rückweg, Firmware-Ablage, Prüfungen, Gerätepasswörter, Erweitert,
  Anzeige; unchanged: Firmware, Logs, Changelog, Downgrade, Beta, Cloud, Gen 2. The Status
  column of the Devices table is wider in German, so "Installieren" stays beside its
  pill (final build 3.10.0).
