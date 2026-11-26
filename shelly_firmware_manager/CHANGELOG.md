# Changelog

## 3.13.0

Released 2026-11-26. New look with tinted cards and a choice of base colour, an access
point form, status messages in the page language and a faster start.

Added
- Access point (Gen2+): one form for on/off, network name, password (8 to 63 characters)
  or open after an accepted risk, range extender; the app never stores the password.
- Status "Shelly server …" while a check waits for the Shelly update server; it turns
  yellow after 5 s.
- Status "Restarting …" after "Restart" until the Shelly answers again (at most 2
  minutes); without an answer the device shows the error.
- Devices table (desktop): a click on the NTP server opens its dialog.
- Status messages of the devices, errors of the app and the result of the connection
  test are shown in the page language; the log entry of the test stays English.
- Device settings: if the Shelly needs a restart to apply a change, the success message
  offers to restart it right away.
- Settings tab, card "Display": base colour of the app – Default (neutral grey surfaces,
  buttons in the Home Assistant theme colour) or Blue, Teal, Green, Violet, Orange.
- Saving only the base colour keeps the app running (no restart).
- Settings tab: leaving the tab or choosing another language with unsaved changes asks
  first – discard them or save them before going on.
- Settings tab: "Save settings" with the "Unsaved changes" hint also above the cards
  (phone: full width over the first card).

Changed
- New look: cards and rows on a tint of the theme colour, softer corners, tonal buttons,
  segmented tabs, own checkboxes and radio buttons, more compact phone tiles.
- Settings tab: the options of each card stand on one inner surface, like the rows of
  the other tabs.
- Dialogs: one warning box per colour; install dialog in two columns (desktop), the
  changelog folded, several devices per finding in a folded list.
- Several devices in the password dialog: devices that cannot take part folded at the
  end; long device lists scroll inside the dialog.
- Logs: new entries marked by a bar on the left, no grouping by device.
- Faster start: backups are verified in the background; the page loads only the
  dictionary of its language.
- Errors of a Shelly show its message with the code once ("Timed out (code -104)")
  instead of the raw error object.

Fixed
- Settings: "Test connection" drops the old result after a change of the address or the
  host port.
- Activity log: a setting the change leaves as it is gets no line (e.g. "Access point:
  on → on").
- Settings tab: a new language reloads the page on the Settings tab instead of Devices.

## 3.12.1

Released 2026-11-24. A device that needs a password the app does not have is shown as
an error again.

Fixed
- Check of a device that needs a password the app does not have (or refuses the stored
  one): the row shows "Error" with the reason like the log, no longer "Up-To-Date".

## 3.12.0

Released 2026-11-24. Connection test of the download server, help for devices that do
not answer, Home Assistant areas, a security overview and six more languages.

Added
- Settings: "Test connection" lets a Shelly fetch a test address from the download
  server and names the likely cause, with the real port, when it cannot.
- Settings → Download Server: "Host port" sets the port of the download server in Home
  Assistant (app restarts); a port already in use on the host is refused.
- Card "Getting started" on a fresh installation: download address, first check,
  connection test; it can be hidden.
- Devices that did not answer: device details name the likely causes; "Try again" checks
  address, port and answer without a full check.
- Device details: "Details for a bug ticket" with app, device and settings data; names,
  IP (also IPv6) and MAC addresses masked, no passwords, nothing is sent.
- After an update with a "Before update" backup the app compares the settings and marks
  what changed and which scripts no longer run (row menu "Comparison after update").
- Comparison after an update: "Seen" removes the line below the firmware; it stays in
  the row menu until the next check (the check after an intermediate version keeps it).
- Logs: the entry of a comparison after an update opens with the changed settings and
  the scripts that stopped.
- Home Assistant areas: column Area and area filter in the Devices tab, also found by
  the search; schedules and "Back up now" can choose devices by area.
- Strip "Security" above the device list: devices without password, with cloud, access
  point or debug log on; a click filters the list (phones: symbol and number).
- Devices tab (desktop): the list as shown can be saved as CSV file.
- User interface in Dutch, French, Italian, Spanish, Polish and Portuguese (machine
  translation); the language picker offers them.

Changed
- App options are stored by the app and changed only in the Settings tab; the app's
  Configuration tab keeps a hint. The options set there before are taken over once.
- Settings file: an invalid value gets its default and is named in the status strip, so
  the app also starts with e.g. invalid certificate pins.
- Settings: after saving, the page comes back on the Settings tab as soon as the
  restarted app answers.
- The download base URL follows a new host port; help texts, "Getting started" and the
  connection test name the real host port; a yellow line warns while they differ.
- "Before installing" in the install dialog names a missing or failed connection test.
- "Way back" is now called "Previous version" in the device details, the install dialog
  and the Firmware tab.
- No status line under the page title any more; running work shows in the rows,
  dialogs and tabs. The line remains for storage and loading errors.
- Logs: "Hide successful entries" hides every successful entry, not only checks; times
  and host names in the normal font with equal-width digits.
- Times without a value read "never", other empty values "–"; warning boxes in dialogs
  with the tint of the notice bar; red trash icon at "Delete all logs".
- Firmware tab: the tooltip of "Installed" names only the devices.
- Devices: a device in "Updates available" stays there during a downgrade until the
  next check (before: it moved to its section and back after the installation).
- Devices: the "Select all" box of "Updates available" is hidden while the card has no
  installable device.
- Faster app start: the standard library is precompiled in the image.

Security
- Connection test: the test address of the download server is valid once, only for the
  chosen device, and serves no firmware.
- Logs: the comparison after an update keeps only the names of changed settings, never
  their values (they may hold e.g. a server address with a token).
- Host port check: a host name in the download base URL is resolved first; outside the
  local network nothing is checked.

## 3.11.1

Released 2026-11-23. Stronger downgrade warning with a risk tick, and no downgrade
entries while downgrades are off.

Changed
- Downgrade: red warning "Downgrade at your own risk" (can brick the Shelly) in every
  downgrade dialog, also at the bottom of the version list.
- Downgrade confirmation: the red "Downgrade" button works only after the tick "I
  understand the risk".
- While downgrades are off, the row menu has no Downgrade entry and the Firmware tab
  no downgrade icon.
- Ticking "Allow downgrades" in Settings shows the downgrade warning; it needs the tick
  "I understand the risk", Cancel takes it back.

## 3.11.0

Released 2026-11-23. Backups of the device settings by hand, before every installation
and on a schedule, with restore part by part; better keyboard use and contrast.

Added
- Backups tab: settings of every Shelly stored in the app – Gen2+ configuration,
  scripts, schedules, webhooks; Gen1 settings and actions. Never the passwords.
- Back up now for all, chosen or one device (Backups tab, row menu, device details); the
  card "No Backup" names the devices without one and why; the hint names the passwords.
- Backup before every update and downgrade (Settings, on by default); if it fails,
  nothing is installed. Also for installations scheduled with "Install at".
- Schedule action "Backup": stores a backup only for devices whose settings changed
  since their newest one; battery devices are backed up while awake, else "asleep".
- Restore: device and backup are compared, parts are chosen (settings, scripts,
  schedules, webhooks, actions); only to the device of the backup, read again after.
- Restore makes a chosen part equal to the backup (what exists only on the device is
  removed); a backup is stored first; network settings need an extra confirmation.
- Restore: the red network warning names another Wi-Fi (SSID) in the backup; the Wi-Fi
  password is not part of a backup and stays as it is.
- Backups are refused when another Shelly answers at the device's address (MAC address
  checked), like a restore.
- Keep backups per device (Settings, default 10, 1–50): older ones are removed; a
  backup marked "Kept" (pin) stays and does not count, as does the one being restored.
- Download of one backup as a file sealed with a password shown once; import for the
  same device (MAC address and model checked), marked "Imported" and "Kept".
- Store export and import also carry the backups, encrypted with the export password.
- Logs: kind "Backup" with filter; "Back up now" for several devices writes one entry
  per run with the result of each device; entries unread on opening are highlighted.
- Keyboard: the focus stays on its control when the page redraws and returns to the
  control that opened a dialog; messages in dialogs are announced.
- Keyboard: Escape closes the row menu and returns to its button; the column heads of
  the Devices table also sort with the space bar.

Changed
- Logs tab: the "!" only marks unread error entries and is red, no longer yellow for
  warnings and successes.
- A device that does not answer (not a battery device asleep, not ignored) counts as an
  error in checks, backups and schedule runs: log, notification, device list.
- Contrast: coloured text (links, new version, active tab, errors, grey tags) reaches
  4.5:1 in light and dark; warning text is one darker orange everywhere.
- Input errors stay in the dialog with everything entered: device and bulk passwords,
  upload, store import, NTP server and power-on action; a refused file is kept.
- The X and Escape do what "Cancel" or "Close" does: a staged upload or import is
  discarded.
- A dialog that holds a file, download or export is no longer cancelled by an outdated
  tab; what it holds is removed exactly when its time is up.
- Lists in dialogs (bulk passwords, schedule devices, run results) sort names like the
  tables ("Shelly 2" before "Shelly 10").
- Firmware store export: the note that the password seals the file and is stored
  nowhere is a yellow box.
- Power-on action: the values the Shelly reports ("Restore last state", "Match input",
  …) stay English in every language, as on the device.
- Tabs on phones: six tabs; the icon tabs are narrower so that all fit 360 px; the tiles
  of the Schedules tab have the strong border of the Devices and Logs tiles.
- Schedules tab: the result of the last run wraps only between its parts.
- No "Checking …" line under the page title while a device is checked; its row shows it.
- The app starts faster: the Python modules are compiled when the image is built.

Fixed
- Firmware tab (phone): the labels of a version stay in one line beside the version; no
  empty line with a dash for a version without devices.
- Dialogs: the page behind an open dialog no longer scrolls.
- Phones: a long state in the popups of the action icons moves below the name; the
  select of the power-on action stays inside the dialog.

Security
- Backups are stored encrypted (key in the app data, file mode 0600); downloaded and
  exported backups are encrypted with their password, which is stored nowhere.
- Backups: a lost, damaged or replaced key is noticed at the start; backups it cannot
  open or read are not listed and keep their marks, so a schedule stores a new one.
- Backups: the key file is never overwritten (a damaged one is kept as
  `key.damaged-<time>`; an unreadable one turns backups off until the next start).
- Imported backups: the checksum is computed from the content and a time in the future
  is refused, so an imported file cannot hide changes from a schedule.

## 3.10.0

Released 2026-11-22. Updates can be installed at a chosen time, beta firmware can be
offered, schedules restart Shellys or switch cloud and Bluetooth; the UI speaks German.

Added
- Install at (installation dialog, section "Start" above the changelog): once, at most 7
  days ahead; only for unchanged version and file, else it lapses with a notification.
- Scheduled installations: pill "Scheduled" with the time, filter "Scheduled", row menu
  "Install now" / "Cancel scheduled installation"; listed in the Schedules tab.
- Offer beta firmware (Settings, Gen2+, off by default): section "Beta Updates", violet
  "Beta" tag, extra confirmation; offered betas are withdrawn when switched off.
- Schedules tab: restart, cloud on/off or Bluetooth on/off on all or chosen Shellys,
  daily, on weekdays or every n days; restarts staggered (default 3 s); "Run now".
- Schedule runs leave out ignored, removed and battery devices and devices without the
  switch; every run is logged, HA notifies only on failures; missed runs are skipped.
- Language (Settings, card "Display"): Same as Home Assistant, English or Deutsch, per
  browser; also the Configuration tab of the app page; logs and messages stay English.
- Logs tab: a yellow "!" marks unread entries; the tab refreshes on every new entry.

Changed
- Missed jobs: a schedule run or scheduled installation whose time passed while the app
  was not running is skipped, also right after a restart; no catch-up.
- Jobs wait up to 30 minutes for a running check, installation or schedule run; an
  automatic check waits the same way for a schedule run before it is skipped.
- Scheduled installation: one notification with the result; it lapses for an ignored or
  removed device or when the update server could not confirm the file at the last check.
- Downgrade is blocked while an installation is scheduled for the device.
- Remove from list strikes the device from all schedules; a schedule left without
  devices is switched off until you choose devices again.
- Schedule result "2 of 3 restarted" counts left-out devices; a run cut off by an app
  stop is logged as interrupted.
- Settings tab: cards in independent columns, three from 1600 px; no gaps between cards.
- Devices, Schedules and Logs tabs refresh right after the time of a job.
- Power-on action icon shows "?" while the outputs of a device were never read.
- Tabs on phones: only the active tab shows its name, the others their icon.
- Check notification counts new beta updates separately; the app log names for every
  checked Gen2+ device whether a beta was offered.

Fixed
- Running downgrade: the device stays in its section and shows "installed → target" with
  the tag "Downgrade", no longer the last offered version under "Updates Available".
- Install at: the date range follows the time zone of Home Assistant, not the browser's.

## 3.9.0

Released 2026-11-22. Installed versions are kept as a way back, new versions can be held
back for some days and installations are checked first; the store import is hardened.

Added
- Keep installed versions (Settings, on by default): a check also stores the version a
  Gen2+ device runs while Shelly offers it – its way back after the next update.
- Offer updates only after (days) (Settings, 0–60, default 0): new versions (not your
  uploads) held back by build date; "On hold" filter, row menu "Offer update now".
- Devices tab: "N of M Shellys listed" with the reason why Shellys of the Shelly
  integration are missing (disabled, discovered but not set up, ignored in HA).
- Installation dialog: "Before installing" checks the way back (Gen2+) and the Wi-Fi
  signal read from the device; build date of the offered version.
- After a failed installation: "Restart the device first" checks the device, restarts
  it, waits until it was down and answers again (at most 2 minutes), then installs.
- Cloud icon in the action column (yellow while on) with on/off switch and filter
  "Cloud on" (Gen2+ `Cloud.SetConfig`, Gen1 `/settings/cloud`).
- Bluetooth popup: hint when Home Assistant uses the Shelly as Bluetooth proxy and turns
  Bluetooth on again.
- Firmware tab: labels and filters "Installed" and "Imported"; delete dialog warns when
  devices lose their way back.

Changed
- Store import: warning in step 1 and in the welcome dialog, every file stored as
  "Imported", acknowledgment required; export and log count imported files.
- Consistent look: one set of font sizes, radii, line and warning colours; clearly
  visible field borders; buttons, selects and fields share one height (44 px on phones).
- Devices tab: "Columns" is a round icon on the right with a popover; a dot shows
  columns that differ from the default.
- IP addresses in the normal font with even digits.
- Dialog "Install n updates?" on phones as two-line entries.
- Delete and downgrade dialogs carry their icon; step 1 is a blue "Continue", only the
  final step is red; error dialogs keep the title of their dialog.
- Settings tab: the card "Device Passwords" comes first; "Check now" is hidden there.
- Texts: "Device details", "Latest", "Intermediate", "Gen 2", "Test & store", buttons
  without "…"; password dialog without the row "Device protection".

Fixed
- The export password could be cut off on narrow phones.
- Installation dialog: long Gen1 version texts ran out of the dialog; they wrap now.

Security
- Store import: every imported file is stored as "imported" and never replaces Shelly's
  file; it counts as downloaded once Shelly offers exactly this file (SHA-256).
- Gen2+: a stored file is used instead of a download only if Shelly's download address
  names its SHA-256; your own upload of exactly that version stays possible.
- A provided update keeps its file only while this rule allows it: a file an older
  version provided under the old rules is replaced by Shelly's file at the next check.
- Gen1: imported files are no longer provided "store first", only for a manual
  installation at your own risk while Shelly's list cannot be reached.
- An import no longer turns an uploaded file into a downloaded one.
- Device texts in Home Assistant notifications are shown as plain text (no links or
  formatting); odd requests get a client error instead of a server error.
- From 3.8.0 or older: stored Gen1 files are marked as imported once (origin unknown);
  updates provided from them, also failed ones, are withdrawn until the next check.

## 3.8.0

Released 2026-11-22. The device list is split into sections with a filter popover, and
devices removed from Home Assistant stay listed; lost firmware files are read back.

Added
- Devices removed from Home Assistant stay listed under "Removed"; a full check only
  tests whether they still answer (log warning, or error if not); data moves by MAC.
- Devices tab: row menu "Remove from list" for removed devices (with confirmation).
- Devices tab: read-only list "Other Shellys" (BLU behind a gateway, Bluetooth, ZHA,
  Zigbee2MQTT, MQTT, Matter) with where to update; detected by the manufacturer text.
- Battery devices are also recognised by their battery sensor in Home Assistant until
  the device reports itself; the battery tooltip names the source.
- Firmware files on disk that the store does not list are read back at the start when
  their content proves ID and version (SHA-256); a log warning names them.
- Devices tab: "No usable address" for a device whose address cannot be used, with the
  reason (desktop).
- Option "Resolve host names" (Settings → Advanced, off by default): only while it is
  on, checks look up host names and the Devices tab offers the column "Host name"
  (host name of the Shelly integration, else `<device id>.local`; shown if it resolves).
- Firmware tab: card "Unknown Files" lists files the store does not know; delete there.
- Pre-release versions show a red "(beta)" next to the app title.

Changed
- Devices tab: list in sections (Updates Available, Attention, Not Checked, Up-To-Date,
  Asleep, Ignored, Removed, Other Shellys); a removed device loses its Ignored mark.
- Devices, Firmware and Logs tabs: the filters are a popover of a filter icon next to
  Sort, with counts; a dot marks an active filter.
- Devices tab: the Removed pill tooltip says that "Check now" only tests whether the
  device still answers (or answers again).
- Gen1: the list of allowed types is removed; every Gen1 type is checked while Gen1
  updates are enabled (a stored list is ignored).
- Unknown files in the firmware folder are no longer deleted automatically.

Fixed
- "Error: 401: Unauthorized" after the app was in the background: refreshes repeat
  silently while Home Assistant renews the session; only a lasting refusal is shown.
- Devices tab (desktop): the header checkbox of "Updates Available" is aligned with the
  row checkboxes.
- Other Shellys: a listed Shelly that Home Assistant also knows over Bluetooth (BLE MAC
  = Wi-Fi MAC + 2) is no longer listed a second time.

## 3.7.1

Released 2026-11-21. Shellys that are connected through a router or another integration
are listed again.

Fixed
- Devices tab: Shellys that Home Assistant shows as connected through another device
  that is not a Shelly (e.g. a FRITZ!Box) were missing; they are listed and checked
  again. Only devices behind a Shelly gateway stay hidden.
- Stored device data and passwords of such Shellys are no longer removed at each
  check. Passwords already removed must be entered again.

## 3.7.0

Released 2026-11-21. The app is installed from a prebuilt image instead of being built
on your system, and the Settings tab suggests the download base URL; new icon and logo.

Added
- Settings tab: an empty download base URL is filled in with `http://<host>:8098` from
  the address of the Home Assistant page when that host is an IP address in a private
  network; a note names the suggestion, nothing is saved until Save.

Changed
- The app is installed from a prebuilt image on ghcr.io (aarch64, amd64) instead of
  being built on your system; installation takes seconds.
- New app icon and logo in cobalt blue.
- Documentation: options listed one per row with default and range, shorter Devices,
  Firmware and Settings sections, beta channel, backup size and Watchdog rows.

## 3.6.1

Released 2026-11-21. The download base URL accepts only private IP addresses and plain
host names, and the Settings tab names the reason while you type; the documentation is
extended.

Changed
- Settings tab: the hint under the download base URL names the reason of a rejection
  while typing.
- Documentation: Quick start, Requirements, rules of the download base URL, FAQ, Known
  limitations and Support; the Watchdog note states what the switch does.

Security
- Option `download_base_url`: an IP address outside 10.0.0.0/8, 172.16.0.0/12,
  192.168.0.0/16 and fc00::/7 is refused. The host must otherwise be a plain ASCII host
  name (hint as before); spaces, control and non-ASCII characters, IP addresses in
  disguise (e.g. 2130706433) and IPv6 zone IDs are refused.
- A saved address that is no longer allowed disables installations; the notice above
  the tabs names the reason until the address is corrected.

## 3.6.0

Released 2026-11-21. Publication preparation: MIT license, new app name, icon and logo;
the Devices tab gets a Device Details dialog; checks and the state file are more robust.

Added
- The app is released under the MIT License; the credits in the documentation list the
  components used, their license files are part of the image.

Changed
- App name is now "Firmware Manager for Shelly" (app store, app page, Home Assistant
  notification, log, heading in the app); new app icon and logo.
- Devices tab: the row menu entry "Status details" is now "Device Details" and also
  shows model, generation, firmware ID and NTP server (desktop and phone).
- Devices tab: the row menu ⋮ is ordered in five groups (updates, device, settings,
  ignore, restart).
- Firmware tab (info tooltip, upload dialog): the texts name the check rule as it is –
  Gen2+: the Shelly server decides the next version, a stored file of it is used.
- Store export: too little free disk space is reported when the dialog opens; an export
  older than 10 minutes is refused in the dialog instead of a failed download.
- Firmware upload and store import refuse ZIP64 archives and oversized file lists.
- Option `download_base_url`: user name, password, query and fragment are refused.

Fixed
- Check all: an unexpected error of one device no longer ends the whole check; it is
  shown for that device. Same for installations, Reload Device Info and passwords.
- A damaged state file no longer empties the firmware store: the previous save is
  loaded (storage warning); without a readable state no firmware file is deleted.
- Device password: a change the device confirmed but the app could not save is
  reported with the reason instead of a general error.
- Home Assistant with very many devices: device registry answers up to 32 MiB are read.
- First start: the notification is sent again at the next start if it was not accepted.
- Activity log: a damaged log file or entry no longer stops the app at start.
- No check can start while a store import writes its files.

Security
- Shellys are contacted only in 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 and fc00::/7
  (fixed list); other special ranges that counted as private are refused.
- Security headers also on refused requests and on unexpected errors; IDs, names,
  versions and times with a trailing line break are refused.

## 3.5.0

Released 2026-11-20. Clearer settings, filter chips and one toolbar in all tabs; phone
header and device tiles reworked; battery devices are recognised.

Added
- Battery devices are recognised while awake (Gen2+ `wakeup_period` from
  `Sys.GetStatus`, Gen1 `sleep_mode`): battery icon after the name (table and tile).
- Battery device not reachable at a check: grey status "Asleep" instead of Error (not
  counted under "Errors"), IP dimmed; tooltip with the wake-up hint and last contact.
- Devices tab: filter "Unprotected" lists devices without password protection set on
  the device.
- Firmware tab: filters Provided, Uploaded, Intermediate, Gen1 and Unused (no known
  device of the firmware ID; none while a device's firmware ID is unknown).
- Firmware tab: free disk space of the store shown next to the number of files and the
  stored size.
- Settings tab, last line: link to the app page in Home Assistant, where "Changelog"
  next to the version shows the changes of every version.

Changed
- All tabs: search and sort as small round icons that open a popup (search field; sort
  fields and direction); a dot marks an active search. Logs: search only.
- Phones: icons and filter chips share the first toolbar row; "Upload firmware" is an
  icon at the right end of the second row.
- Settings tab: new card "Download Server" (download base URL, serve only to known
  Shellys); "Firmware & Storage" holds Gen1, downgrades, the firmware store and the
  import size.
- Settings tab: export/import of the firmware store moved into "Firmware & Storage"; the
  separate card "Firmware store" is gone.
- App configuration in Home Assistant: same order, names and help texts as the Settings
  tab.
- Option names start with "Max." ("Max. log size (entries)", "Max. parallel
  installations"); sizes are always given in MB.
- Card heads in Title Case and the same bold weight (firmware ID more prominent).
- Devices filter "Up to date" is now spelled "Up-To-Date" like the status.
- Devices waiting for a firmware download started by another check show "Downloading…"
  (full and single-device checks; still one download per firmware ID and version).
- Logs: a check entry counts the ignored devices and names them in its details (normal
  text colour, not as findings); the device search finds them.
- Firmware tab: "For n devices" with one chevron after the text (down closed, up open)
  instead of two arrows.
- Phones, header: last check and schedule stacked on the left, "Check now" right on the
  same line (it no longer wraps below them).
- Phones, device tiles: Home Assistant name as head with the "Gen n" label and a divider
  over the full width; IP right of the Shelly name (grey, ↗).
- Phones, device tiles: status and Install right of the firmware; "Details" is an
  outlined button over the full width; stronger outlines, also in dark mode.

Fixed
- Logs tab: the entry of a check appears as soon as the check has finished, without
  switching tabs.
- Store export: the copy button did not copy the password; if copying is refused, the
  password field is selected for a manual copy.

## 3.4.1

Released 2026-11-19. Store imports and firmware uploads above 16 MiB work through Home
Assistant.

Changed
- Store import and firmware upload dialogs show the upload progress in percent.

Fixed
- Store import and firmware upload: files above 16 MiB were refused by Home Assistant
  ("Maximum request body size … exceeded"); they are now sent in parts of 8 MiB.

## 3.4.0

Released 2026-11-19. Move the complete firmware store to another Home Assistant
instance; store or set the device password on several devices in one step.

Added
- Settings tab, card "Firmware store": export the whole store as one ZIP file (folders
  per generation and firmware ID, readable when unpacked) and import it elsewhere.
- Export password generated by the app and shown once; it seals the file (HMAC-SHA256
  over the file list with every SHA-256, key via scrypt). It is stored nowhere.
- Import in two steps: the file is checked completely (seal, every file) before
  anything is stored; result as counts: new, already stored, conflicts.
- Imported files keep their source (downloaded / uploaded) and their original date;
  changelog and intermediate marker are kept. Gen1 files are imported in any case.
- Already stored file (same SHA-256): not stored twice; missing data is added, an
  upload becomes "downloaded" if the export holds it as a sealed download.
- Conflict (same firmware ID and version, other file): the stored file is kept and the
  import is listed as conflict in the result and in the log.
- New option "Max. import size (MB)" (Settings tab, Firmware & storage): 1–2048,
  default 100. Larger files are refused before they are stored.
- Fresh installation (no stored state, also after a reinstallation): no check runs
  until the question "import a firmware store?" is answered with Import or Skip.
- First start: welcome dialog once, then a yellow notice bar; "Check now" is disabled
  until then. One Home Assistant notification. Not shown after updates.
- Settings tab, card "Device passwords": store the existing password or set a new one
  on several devices in one step; each device is set, verified and stored on its own.
- Several devices: "set" enables protection on unprotected devices and changes it on
  protected ones with a stored password; other devices are greyed out with the reason.
- Several devices: runs in parallel up to "Parallel installations (max.)"; a failure
  does not stop the others. Result per device; one log entry per device.
- Logs: entries for store export (files, size, damaged files left out), store import
  (counts, conflicts) and a skipped first-start question.

Security
- Device passwords are never exported. Damaged stored files (SHA-256 mismatch) are left
  out of an export. The import body limit applies only to the import route.

## 3.3.0

Released 2026-11-19. Optional downgrades, sorting per browser in both tabs, password
changes without storing, status icons in messages.

Added
- New option "Allow downgrades" (Settings tab, Firmware & storage): off by default, also
  after an update; while off, downgrades are refused and shown greyed out with a hint.
- Firmware tab: downgrade icon on older Gen2+ versions; choose a device with a newer
  version installed, then the usual second confirmation.
- Sort select with direction arrow in the Devices and Firmware tab (desktop and phone);
  column titles sort too. Firmware cards: by ID, newest version, size or date.
- Password dialog: "Store in this app" for enabling or changing the protection; off =
  verified only, a stored password is deleted once the device confirms the new one.

Changed
- Sort order, hidden and moved columns are kept per browser instead of for all users;
  the stored shared settings are dropped (start: device name ascending).
- Messages show a round status icon: success, warning or error; neutral notes ("Nothing
  changed.") without icon. Password results remind to update the HA Shelly integration.
- Password dialog: storage note as coloured box (Gen2+ yellow, Gen1 plain text or not
  stored red), including the reminder for the HA Shelly integration.
- Delete firmware dialog: the note that a file may not be restorable is a red box.
- Phones: warning toast as wide as its text; more space above the content of popups.

Fixed
- Logs (phone): no second arrow next to the user name of an opened entry, the name no
  longer moves; entries whose details only repeat the title do not expand.

## 3.2.1

Released 2026-11-17. Power-on action of Gen1 dimmers, Duo and Vintage can be changed
again; settings the Shelly accepts but does not apply are reported. rc2: setting help
as tooltips, tooltips on touch devices, websocket debug log switch, clearer action
popups and Firmware tab. rc3: shorter action popups, centred table columns, column
titles in the Firmware tab.

Final release (same functions as rc3)
- Version 3.2.1. No functional change compared with rc3 (live-tested).
  rc1 to rc3 were never released; their changes are listed below.
- Clean-up without visible effect: two UI module variables are no longer exported
  (only used inside their own module).
- Development (not part of the image): the screenshots of the release are in
  `qa/screenshots/3.2.1/`.

Changes in rc3
- Popups of the action icons eco mode, access point, Bluetooth and websocket debug log:
  no question any more – the status strip shows the current state, the button names
  the action ("Turn on" / "Turn off"). Restart popup: no question, the hint reads "The
  device will be unavailable for a few seconds.", button "Restart".
- Devices table (wide screens): titles and content of all columns except "Device" are
  centred; IP and firmware versions use digits of equal width. Phone tiles unchanged.
- Firmware tab: column titles (Version, Size, Stored, SHA-256, Devices) over the
  version rows of every card, centred except "Version" like the Devices table; on
  phones small labels (Size, Stored, SHA-256) inside the version tile instead. The
  label "intermediate" explains itself as tooltip (mouse hover, touch tap). The
  "Upload firmware" button shows the upload icon of its dialog after the text.
- Development (not part of the image): older test reports, session handovers and
  backlogs in `docs/history/`; approved UI decisions in `docs/UI_DECISIONS.md`; new
  `qa/capture.py` (screenshots of chosen views at 1440 px and 412 px for reports and
  mockups, every shot checked for page errors and horizontal overflow; `--gate`
  replaces `qa/mobile_capture.py` in the final gate); UI quick check about 40 s instead
  of 75 s (`qa/ui_features.py` checks the Firmware/Devices/Logs flows in two viewports,
  all four with `--full`; fixed waits replaced by waiting for the expected state).

Changes in rc2
- Settings tab: the help texts are no longer shown under the fields. A small i-icon
  next to each setting name shows the help as tooltip (mouse over the name or the
  icon, keyboard focus of the icon; on touch devices a tap on the name or the icon
  shows it, a tap elsewhere or scrolling hides it – a tap on the name then does not
  change the setting). Screen readers still read the help as description of the field.
  The live warning for the download base URL stays visible under the field.
- Tooltips on touch devices: a tap shows the tooltip of an element that has no action
  of its own (e.g. device names, the Gen1 version, the check schedule, the SHA-256 and
  the "Uploaded" label in the Firmware tab) and of a disabled button (the reason why it
  is disabled). A tap that opens a popup or does something else is unchanged.
- Device names: tooltips "Home Assistant device name: …" and "Shelly device name: …"
  (table and phone tiles) instead of the browser's own tooltip.
- New action icon "Websocket debug log" (bug icon, Gen2+): shows whether the Shelly
  streams its debug log over a websocket (grey off, yellow on, "?" unknown) and turns it
  on or off (`Sys.SetConfig` `debug.websocket.enable`, read back and logged like other
  settings). The popup explains the debug log, links the Diagnostics page of the Shelly
  web UI (`/#/diagnostics`) and warns for firmware below 1.0.0 (the stream is not
  protected by the device password). Gen1 devices have no websocket debug log (icon
  greyed out with explanation). The action column shows two rows of five icons.
- Access point icon: access-point symbol instead of the Wi-Fi fan.
- Popups of the action icons (power-on action, eco mode, password, access point,
  Bluetooth, websocket debug log, restart) and the notes of greyed-out icons start with
  a status strip: the icon in the colour of the action column, the feature name and the
  state (On/Off, Protected, Not available, Not supported). The question is shorter
  ("Turn it off for …?").
- Dialog titles with an icon: changelog, delete (red) and upload in the Firmware tab,
  the installation dialog (red firmware chip).
- Firmware tab: labels 12 px after the version and vertically centred; new outlined
  label "intermediate" for a file a check provided as an intermediate version (stored
  with the file from now on, not determined for older files); on phones the date is
  right-aligned in the size line. The first step of the delete dialog has a red
  "Continue" like "Delete".

Changes in rc1
- Fix: Shelly Dimmer 1/2, Duo and Vintage (Gen1) report the mode "white" but have only
  `/settings/light/<i>`. The app derived the output `white:<i>` from the mode and sent
  the power-on action to `/settings/white/<i>`; the device answered without applying
  it, the icon stayed red. These devices now use `light:<i>` and
  `/settings/light/<i>`; RGBW2 and the Bulbs keep `color`/`white` by their mode.
- Every settings change is compared with the value read back from the Shelly. A value
  the device accepted but did not apply is shown as a warning naming the setting, and
  the activity log entry is marked as failed ("Not applied by the Shelly: …").
- Gen1 power-on values use the names of the Gen1 web interface: "Restore last mode"
  (`last`) and "Switch" (`switch`). Gen2+ keeps "Restore last state" and "Match input".

## 3.2.0

Released 2026-11-16. Clearer dialogs and phone tiles, failed installations can be
retried from the updates card, a clearer message when a Shelly rejects a firmware, and
fewer warnings in the app log for rejected download requests. rc2: Gen2+ devices get
the update step the Shelly update server intends for their installed version, required
intermediate versions included. rc3: provided firmware can be deleted. rc4: single
checks and installations of different devices run in parallel, a new wait logic after
the installation.

Final release (same app code as rc4)
- Version 3.2.0. No functional change compared with rc4 (live-tested).
  rc1 to rc4 were never released; their changes are listed below.
- Repository clean-up: the UI sources (`ui-src/`) moved from the app folder to the
  repository root, so the app folder `shelly_firmware_manager` holds only what Home
  Assistant needs for the installation. The built UI files are byte-identical.
- Security review before the release: no known vulnerability (CVE/GHSA) in the pinned
  packages (aiohttp 3.14.5 – the advisories published on 2026-11-13 are fixed since
  3.14.4 – and aiosignal, attrs, frozenlist, idna, multidict, propcache, yarl,
  aiohappyeyeballs, typing-extensions) or in Python 3.12.16. Dependencies, wheels and
  base image are unchanged.

Changes in rc4
- Parallel work: an installation or a single-device check no longer blocks single
  checks or installations of other devices. A device that is being installed cannot be
  checked, and a device that is being checked cannot be installed or reloaded. Single
  checks have no limit. "Check all" stays exclusive: it locks everything while it runs
  and cannot start while single checks, installations or Reload Device Info run (the
  button stays disabled, the reason is shown).
- Scheduled check: if it is due while other work runs, it is cancelled and written to
  the log ("Scheduled check cancelled" with the reason); it is not repeated, the next
  date counts. Before, it waited and was retried every minute.
- The check after a confirmed intermediate version starts at once, even while other
  installations run.
- Firmware tab: during a single check only the files of the checked device's firmware
  ID are locked for deletion (during "Check all" all files, as before).
- Installation wait (Gen2+ with OTA events over the device WebSocket): an ota_error is
  an error at once. Without a download request after 2 min, or without new progress
  for 60 s, the installation fails. After 100 % or ota_success the Shelly must restart
  within 30 s (WebSocket closed or no answer); the status label then shows
  "Restarting". Within 30 s after the restart it must report the new version; the old
  version, another version or no answer is an error. The device is polled every 5 s.
  Such errors behave like a failed installation (retry from the updates card).
- Installation wait without OTA events (Gen1, HTTP fallback, WebSocket lost before the
  update was applied): polling as before, limit 5 min instead of 3 min.
- Devices: a device in "Updates available" (e.g. failed installation, "Unconfirmed" or
  "Updated") stays there while it is checked again; the result of the check decides
  afterwards. Before, it moved to "All other devices" as soon as the check started.
- Phone tiles: the yellow "Ignored" label (and the "Checking…" label) is as high as
  the status label.

Changes in rc3
- Firmware tab: a stored file that is provided to devices can now be deleted (two
  confirmations as before). The dialog names the devices; they lose the provided update
  and show "Available" until they are checked again (a retry of a failed installation,
  a manual installation option or an intermediate-version mark ends as well). The log
  entry names them. Deleting stays locked during a check and while the file is being
  installed. Before, a provided file could not be deleted – with "firmware store first"
  (Gen1) a check provided it again, so it could not be removed at all.
- Code comment of the device check updated (no change of behaviour).

Changes in rc2
- Gen2+ checks: the Shelly update server is asked per device with its firmware ID and
  installed version – the way a Shelly asks itself (header `Shelly-Info` with only
  `app` and `ver`, no other device data). The server answers with the next allowed
  step, e.g. a required intermediate version (Plus1 1.0.7 → 1.3.3) instead of the
  newest version, which such a device would reject. Requests are shared by devices
  with the same firmware ID and installed version.
- Gen2+: the server decides, the firmware store serves. A stored file (download or
  upload) of exactly the offered version is used instead of a download; a newer stored
  version is no longer provided on its own ("firmware store first" now applies to Gen1
  only).
- Gen2+: if the server cannot be asked, the device shows the error as before; if a
  newer version is stored, the error details say that it can be installed manually,
  at your own risk (whether an intermediate version is needed first cannot be
  verified). The device stays in "Updates available" with checkbox and "Install"; the
  installation dialog requires an acknowledgment of the risk, and the log marks the
  installation as not verified by the update server.
- Intermediate versions: an offer below the newest version (or with several steps) is
  marked as intermediate version (status details, installation dialog). After its
  confirmed installation the device is checked again at once and the next step is
  provided – installed only after a new approval. Log: "After-intermediate-version
  check". Otherwise no check follows an installation (unchanged).
- Downloads: a firmware file whose checksum differs from the SHA-256 named in the
  manufacturer's download URL is not stored.
- Every check that asks the server makes one reference request with fixed values
  (Plus1 1.0.7, which must be offered 1.3.3) – no data of your devices. If the server
  answers differently, the check reports a warning: the server no longer evaluates the
  installed version, so required intermediate versions cannot be detected. A failed
  reference request gives no warning.
- Offers with several steps (`nsteps`) are no longer refused.
- No repetition of failed server requests (unchanged).

Changes in rc1
- Devices: a device whose installation failed ("Error") stays in the "Updates
  available" card – with checkbox and "Install" for a retry, as long as its firmware is
  still provided – until the device is checked again (single check or full check). A
  failed downgrade is not offered again.
- Installation (Gen2+): if the Shelly reports an update error after it downloaded the
  firmware, the message now says that the Shelly rejected the file and that an
  intermediate version may be needed (e.g. a device on a very old firmware). The hint
  to check the download base URL is only shown when the Shelly did not download the
  file.
- Download server: a rejected request is logged as a warning only once per client
  address and 10 minutes; further rejections are counted and named in one line when
  the 10 minutes are over. At most 64 addresses are tracked, more are counted together
  ("other clients"). Accepted downloads are logged as before.
- Dialogs: title and buttons are separated by lines; the content is grouped in titled
  sections (e.g. Device, Firmware, Changelog); value lists have a fixed label column,
  thin row lines and no empty rows; installed and target version are on one line
  ("1.4.0 → 1.5.0"), the generation is shown with the model; the SHA-256 is smaller.
  The buttons are unchanged.
- Status dialog: titled with the device name; the message and the status label are on
  one line (label right), followed by "Firmware" and "History".
- Phone tiles: checkbox and ⋮ on the line of the device name, status and "Install" on
  the line of the Shelly name, the same space at the top and the bottom. The feature
  buttons are smaller and every row fills the width of the tile. The detail list shows
  the values right-aligned with thin row lines. "Details" has an arrow on its right.
- Logs (phones): the user stands top right, beside the first line of an entry.
- Phones: tapping a status label (or another element with a tooltip) no longer leaves
  the tooltip over the tile; tooltips are shown for mouse and keyboard only.
- AppArmor profile: the version mark in the header comment is removed.

## 3.1.1

Released 2026-11-11. Contains everything of 3.1.0 (which was never released) plus the
changes below: clearer states in the device list, log entries can be deleted, shorter
waiting time after an installation, no check after installations anymore. rc2:
internal restructuring only. rc3: findings of the live test of rc2.

Final release (same code as rc3)
- Version 3.1.1. No functional change compared with rc3 (live-tested).
- Security review before the release: no known vulnerability (CVE/GHSA) in the
  pinned packages (aiohttp 3.14.5 and its dependencies), Python 3.12.16 or the
  pinned base image `python:3.12.16-alpine3.24`; dependencies, wheels and base image
  are unchanged since 3.0.0.

Changes in rc3
- No check after installations anymore: the automatic check of the installed devices
  (3.1.0, "After-installation check") is removed. An error of the Shelly server in that
  check could later show "Error" on a device whose installation had succeeded. A device
  keeps the result of its installation until it is checked again (single check or full
  check); the next stored version or the server's offer is provided at that check.
  Older "After-installation check" entries stay in the log.
- Devices: a device whose installation was not confirmed ("Unconfirmed") also stays in
  the "Updates available" card (no checkbox) until the device is checked again.
- Phone tiles: the red "Install" button has the size of the status label "Ready" next
  to it.
- Logs: downloads name the user of the check that fetched them; scheduled checks and
  their downloads name "Schedule" (new entries only; before: no user).
- Logs: the user name and the delete icon of an entry are on one line.

Changes in rc2
- No change of behaviour, no new function: the code was restructured for easier
  maintenance.
  - Backend: the former `manager.py` (about 2200 lines, one class) is split into six
    modules, one layer each (`manager_core`, `manager_store`, `manager_devices`,
    `manager_checks`, `manager_installs`, `manager`); the paths of the app's storage
    are in `data_paths`. The class `Manager` and all its methods are unchanged.
  - UI: the former `ui-src/app.js` is split into eight ES modules in `ui-src/js/`
    (core, devices, install, device-settings, firmware, logs, app-settings, main);
    `tools/build_ui.py` bundles them into the one shipped `app/static/app.js`.
  - Development (not part of the image): extended lint rules in `pyproject.toml`
    (comprehensions, performance, returns, pathlib, refurb, unused arguments, logging,
    print statements, ruff's own rules) with the few findings fixed; the installation
    timings of the tests come from one fixture; the UI feature checks of the Firmware
    suite are split into one helper per area.

Changes in rc1
- Devices: red "Install" button next to the status "Ready" (also on phones). It opens
  the usual installation dialog and is greyed out with the reason while an installation
  is not possible. The "Install" buttons of the Firmware tab are red as well.
- Devices: a device whose installation was confirmed stays in the "Updates available"
  card with the status "Updated" (no checkbox). The automatic check after the
  installation leaves it there; a check of this device or the next full check (manual
  or scheduled) moves it to the other devices. (rc3: the check after installations is
  removed.)
- Action icons with clearer states:
  - Power-on action: green if every output restores its last state, red if an output
    has another value, "?" if the settings could not be read. The tooltip names the
    value of every output.
  - Password protection: green if the device is protected and its password is stored
    in the app, yellow if it is protected but the password is not stored (the app
    cannot read or update the device then), grey if it is not protected.
  - Access point: red while the access point is on.
- Row menu: "Downgrade" is greyed out while no older version is stored for the device
  or something blocks it (check, installation, missing download base URL); the former
  dialog "No stored version …" no longer appears.
- Logs: every entry has a delete icon (one confirmation), and "Delete all logs" clears
  the whole log (one confirmation, regardless of the filter). After clearing, one entry
  remains that names who cleared the log; single deletions are written to the app log
  of Home Assistant only. Entries of older versions get an internal ID at the first
  start (the log file is rewritten once).
- Logs: directly consecutive entries of the same device are marked as a group by a bar
  at the side. The lines of a check are coloured by their own result (update green,
  warning yellow, error red) also inside a check that failed as a whole, and the
  Success / Warning / Error labels have one size.
- Installation: the app waits at most 3 minutes for the target version (before: 5) and
  gives up after 2 minutes (before: 3) if the Shelly is reachable, still reports the
  old version and never requested the firmware.
- Messages (new entries only): HTTP errors of the Shelly servers no longer contain the
  words "message=" and "url=" (`408, 'Request Timeout', 'https://…'`); the hint for a
  Shelly that downloaded the firmware but kept its version adds "and try again with
  enabled websocket debugging".
- Firmware tab: the changelog, download and delete icons of a version are on one line
  (the download icon was shifted); on phones they are in the head of the version tile.
- Phones: the IP address in a device tile ends in line with the ⋮ menu.
- API: `POST /api/logs/{id}/delete`, `POST /api/logs/clear`; `/api/state` has
  `updated` and `downgrades` per device; log entries carry `id`.
- Clean-up (no change of behaviour): the unused field `scanned_version` is no longer
  written to `state.json` (older files are read as before); unused style rules and an
  unused parameter removed; version and ticket marks removed from code comments (the
  history is in this changelog). Development: screenshots of earlier versions removed
  from the repository (`qa/screenshots/` keeps the current version only; older ones
  remain in the git history).

## 3.1.0

Never released (release candidates rc1 to rc3 only); all its changes are part of 3.1.1.
rc1: store integration, robustness and code quality. rc2: the firmware store comes
first in every check, fewer requests to the Shelly servers, Install button in the
Firmware tab. rc3: changelog in the Firmware tab.

Changes in rc3
- Firmware tab: changelog icon in every version row. It opens a dialog with the
  changelog kept with this stored firmware (security entries and entries for the
  firmware's model are highlighted, as in the installation dialog). Nothing is
  requested from Shelly for it. The icon is disabled while no changelog is stored –
  a changelog is stored by a check that provides the file to a device, or when a
  changelog page is fetched for another version of the same generation.
  API: `GET /api/firmware` has the flag `changelog` per file, the text comes from
  `GET /api/firmware/{name}/changelog`.

Changes in rc2
- Checks – firmware store first: before the Shelly update server is asked for a
  device, the app looks for a newer stable version of the device's firmware ID in its
  firmware store. One stored newer version is provided; of several, the next higher
  one to the installed version. Only without a newer stored version the server is
  asked, as before. Applies to the full check, the single-device check and the
  schedule, for all generations (Gen1: allowed types only, as before).
  - Downloaded **and uploaded** files count (changed decision: until rc1 an upload was
    never provided as an update). Of two files with the same version the download is
    preferred. Every file is verified by its SHA-256 before it is provided; a damaged
    file is dropped from the store and the next one is taken.
  - A device that is already "Ready" gets the stored version at the next check if it is
    closer to the installed one; the approval of the former file is void.
  - Consequences: devices are updated step by step through the stored versions, and a
    newer version on the Shelly server is found only once a device has reached the
    newest stored version. To skip a version, delete its file in the Firmware tab.
- Fewer requests to the Shelly servers: no request for a firmware ID whose devices all
  get a stored version; the Gen1 firmware list is requested once per check for all
  Gen1 types (before: once per type); still one request and one download per firmware
  ID for any number of devices.
- Check after installations: after a confirmed installation the installed devices are
  checked automatically as soon as no installation is running anymore (one check for
  all of them), so the next stored version or the server's offer is provided at once.
  Not after a downgrade, an unconfirmed or a failed installation. Logged as
  "After-installation check"; the time of the last full check and the schedule are not
  changed. Changed decision: until rc1 no check ran after an update. (Removed again in
  3.1.1-rc3.)
- Source of an update is shown: status message ("Provided from the firmware store, not
  installed", with "(uploaded file)" for uploads), status details, installation dialog
  (with the upload warning) and the findings of a check in the log.
- Changelogs are kept with the stored firmware: the changelog of a provided version is
  stored with its file and never requested again. A changelog page is requested only
  if a provided firmware has none yet (at most once per check and generation, and not
  again within an hour if the page does not list the version); a fetched page also
  fills the other stored versions. The daily refresh of the pages is gone, so later
  corrections by Shelly to a stored changelog are not picked up. Versions between the
  installed and the offered one are shown as far as the last fetched page lists them.
- Firmware tab: "Install" button behind every device a firmware is provided for. It
  opens the usual installation dialog and is disabled under the same conditions as
  "Install update" (check running, device ignored or installing, download base URL
  missing). The API field `used_by` of `GET /api/firmware` is replaced by `devices`
  (`id`, `name`).
- After a downgrade the status message says "a newer version is provided again at the
  next check" (the next check may provide a stored version in between).
- Code: `shelly.fetch_offer` (Gen2+) and `shelly.fetch_gen1_offers`/`gen1_offer`
  replace the former combined function; new device field `offer_source`, new store
  field `changelog`. No conversion needed: missing fields are filled by the next check.

Changes in rc1
- App store: the user documentation is shown in the Documentation tab of the app page
  (`DOCS.md`), a short introduction in the Info tab (`README.md`). The README of the
  repository keeps installation and development and links to it.
- Watchdog: Home Assistant restarts the app if it stops unexpectedly (TCP check of the
  download server port 8098; nothing is requested or logged). Turn on the Watchdog
  switch on the app page if it is off.
- AppArmor profile (hosts with AppArmor, e.g. Home Assistant OS): the app may only run
  Python, read its program files, write its private storage (`/data`) and use the
  network; no other programs, no special privileges.
- Changelog pages are parsed in a worker thread: a large page no longer delays the UI
  and the download server during a check.
- Incomplete or malformed answers of a Shelly or of the manufacturer's server are
  reported with a readable message (e.g. "Incomplete device information from the
  Shelly") instead of a bare internal error text.
- Code: type annotations for all modules, checked strictly (mypy); a few defensive
  checks the types required (unexpected RPC result, `options.json` not an object).
- Development (not part of the image): tool settings of ruff, mypy and pytest in
  `pyproject.toml` (`tests/pytest.ini` removed), mypy step in `tools/dev/check.sh`;
  Fix:
  `check.sh ui|full` wrote the screenshots of the UI checks into the current folder
  instead of a temporary one.

## 3.0.1

Maintenance release: clean-up based on 3.0.0, no new functions.

- Activity log: stored as JSON Lines (one entry per line). A new entry is appended
  instead of rewriting the whole log (up to 3000 entries); the file is rewritten only
  after 200 dropped entries. The 3.0.0 log is converted once at the first start;
  entries stay unchanged. A line cut off by a power loss is skipped.
- Firmware downloads and uploads write to disk in a worker thread, so a slow disk
  (e.g. SD card) no longer delays the UI and other requests during a transfer.
- Manufacturer server access: JSON and changelog pages share one code path for
  redirects (only within shelly.cloud, HTTPS), certificate pinning and size limits –
  behaviour unchanged.
- Removed the conversion code for versions before 3.0 (2.x status names and table
  settings, the one-time cleanup of the former shared password in the stored options).
  Upgrades from 2.x or 1.x go through 3.0.0 first (see README).
- Removed unused code: a duplicate upload constant and the style rules of the former
  AOP column. Code style findings fixed (long lines, an invisible character literal).
- Development (not part of the image): tests grouped by area instead of by release
  candidate, the former workspace smoke test is part of the test suite
  (`tests/test_security.py`), test runtime about 3 s instead of 5 s; workspace helpers
  and one check script in `tools/dev/`; UI checks: quick matrix per change, full matrix
  only for the final gate, feature checks in one file (`qa/ui_features.py`), one runner
  that starts and stops the demo servers (`qa/run_ui.py`), browser found automatically;
  obsolete scripts (`qa/resources.py`, `qa/cache_test.py`) and the finished rc6/rc7
  backlogs removed; `docs/CODEMAP.md` added.

## 3.0.0

Released 2026-11-07 (final release after rc1–rc7).

Changes in the final release (after rc7)
- Fix: firmware uploads are kept in the activity log after a restart (the log dropped
  the entry type "upload" when it was loaded).
- Fix: uploading a file whose name contains non-ASCII characters (e.g. umlauts) failed
  in the browser; such characters are now replaced in the transferred name (display
  only).
- README rewritten for users: shorter, with a table of contents, current state only.
- Final review: complete test run, code security review of all modules, check of the
  pinned packages and the base image for known vulnerabilities (see the test report).

Changes in rc7
- Firmware is no longer deleted automatically. All downloaded versions stay in the store
  (before: only the two highest versions per firmware ID were kept). Housekeeping only
  removes leftovers of interrupted downloads/uploads, files that are not in the store
  and store entries whose file is missing.
- Delete in the Firmware tab asks twice: the second step needs the checkbox "I
  understand that this file cannot be restored by the app" (checked by the server as
  well). Delete is locked during a check and for a file that is provided as pending
  update or being installed (before, a provided file could be deleted and the device
  went back to "Available"); the button tooltip names the reason. The log records the
  source of a deleted file.
- Downgrade for Gen2+ devices: "Downgrade" in the row menu lists all
  stored stable versions of the firmware ID that are older than the installed one,
  newest first; the installation dialog shows the selected version as target with a
  warning, and a second confirmation with acknowledgment (as for device passwords)
  starts it. Same installation path as an update (download server, SHA-256 check, OTA
  events, verification of the exact target version); logged as downgrade; HA
  notification "Shelly downgraded". Also offered for up-to-date and ignored devices,
  never for Gen1. A newer version known from the last check is shown as available
  again afterwards and provided again at the next check (from the store).
- Manual upload of firmware files in the Firmware tab ("Upload firmware"): `.zip`,
  `.bin` or `.gbl`, max. 64 MiB. Two steps: the file is transferred and its SHA-256
  shown, firmware ID and version are pre-filled from a `manifest.json` in a ZIP where
  present; the user confirms or corrects them (firmware ID of a known Gen2+ device,
  stable version X.Y.Z) and may enter the expected SHA-256 (must match if given). Files
  or versions already stored are refused. Uploads are marked "Uploaded", logged, and
  used only for downgrades – never as the manufacturer's update. New endpoint
  `POST /api/firmware/upload` with its own limit (raw body, Content-Length required,
  streamed to disk); the 32 KB JSON limit stays for all other requests.
- The ⓘ of the Firmware tab describes the new storage rules; the total size of the
  store comes from the server.
- Tests: `tests/test_rc7.py` (no automatic deletion, housekeeping, delete lock and
  second confirmation, downgrade list/gating/installation/failure/restart, upload with
  manifest, SHA-256 check, refusals, expiry, HTTP guards incl. body limits);
  `tests/test_rc4.py` adapted (a provided file can no longer be deleted);
  `qa/rc7_ui_test.py` and demo scenario `rc7`.

Changes in rc6
- `download_base_url` has no default any more (schema `url?`): a fresh installation
  starts with an empty field, and installations are refused with a clear message until
  it is set. Installations that still have the former example value
  `http://192.168.178.50:8098` are treated the same, unless an admin confirms with "This
  address is correct" that it really is the address of Home Assistant (stored in the
  app state, no restart; the confirmation ends when the address changes; logged).
  A notice above the tabs explains the blocked installations with the buttons "Open
  settings" and, for the former example value, "This address is correct". Clearing the
  field in the Settings tab unsets the option. Background: in a live test of rc4 an
  update failed because the example value was still set.
- Settings: the download URL hint also appears for every host name (also `.local`,
  `.lan`, single-label names) and recommends the IP address of Home Assistant, because
  Shellys often cannot resolve host names. Public IP addresses and localhost keep the
  "not in your local network" hint. Still only a hint.
- Gen2+ installations send `Shelly.Update` over the device's local RPC WebSocket
  (`ws://<device IP>/rpc`) and receive the device's update events: the status shows the
  download progress ("Installing 55 %"), and `ota_error` (e.g. "Connection error: -14")
  ends the installation at once as an error with the device's message and a hint to
  check `download_base_url` and port 8098 – instead of waiting 3 minutes. Same private
  IP as before, no redirects, frames limited to 64 KB, SHA-256 Digest auth with the
  stored hash only. If the WebSocket is not available, the command is sent over HTTP as
  before; the command is never sent twice. Version polling unchanged. Gen1 unchanged.
- Download diagnosis per device: parallel installations of the same firmware share one
  file; a download by one device no longer counts for another one (requests from an
  address that belongs to no device – Docker hiding the sender IP – still count for all
  devices of the file, as before).
- Only a GET that starts the transfer counts as served download (HEAD requests and
  rejected requests did or could count before).
- Stop: both web servers are cleaned up in parallel, so a stop takes at most 5 s for
  open connections (was up to 2 × 5 s).
- Image: pip, setuptools and wheel are removed from the runtime image (not used by the
  app). The pinned base image is unchanged.
- Checked without change: shared offer/download tasks of a cancelled check are cancelled
  with it (rc4 result confirmed for the offer task as well, `tests/test_rc6.py`).
- Documented: the app runs as root inside its container; a non-root user is checked
  before the final release.
- Tests: `tests/test_rc6.py` (download URL states and confirmation, WebSocket update
  with a local Gen2 test server incl. Digest auth, redirect refusal, immediate
  ota_error, progress, HTTP fallback, diagnosis per device, GET-only counting, parallel
  stop, cancelled shared tasks); `qa/rc6_ui_test.py` (notice, confirmation, progress)
  and `qa/lan_hint_test.py` extended; demo scenarios `legacy` and `missing`.

Changes in rc5
- Clean stop: the app now handles SIGTERM (stop/restart by the Supervisor) and SIGINT.
  It cancels the check schedule, running checks and installations, saves the state
  and exits with code 0 within a few seconds. Before, the default signal handler
  killed it (exit code 143, Supervisor warning "did not handle SIGTERM") without
  saving the state of a running check. An installation interrupted by a stop is still
  shown as "Unconfirmed" after the next start, never as success.
- The web servers give open connections at most 5 s to finish when stopping (was the
  aiohttp default of 60 s, longer than the Supervisor's 10 s stop timeout).
- The log shows "Stopping – cancelling running work and saving the state" and
  "Stopped".
- Tests: SIGTERM while idle and during a running automatic check, and that a
  scheduler error is still reported (`tests/test_rc5.py`).

Changes in rc4
- Settings: the field "Download base URL for the Shellys" shows a hint while typing if
  its host is not a local address (private IPv4, IPv6 unique-local/link-local,
  single-label names, `.local`, `.lan`, `.home.arpa`, `.internal`). Only a hint – saving
  is not blocked.
- Upgrades from versions before 2.0 are no longer migrated: the translation of the
  German 1.x status values, the removal of the 1.x approval mode and the registration
  of firmware provided by versions before 1.6 were removed. Update from 1.x to a 2.x
  version first (or reinstall). The 2.0 status names are still translated.
- Tests: a cancelled full or single-device check leaves no shared offer or download
  task running (regression guard, `tests/test_rc4.py`); browser check of the
  download URL hint (`qa/lan_hint_test.py`, screenshots in `qa/screenshots/rc4/`).
  `qa/ui_test.py` cleaned up (unused variable removed, a redundant second Escape key
  press removed, conditional expressions used as statements replaced); checks unchanged.
  `qa/mobile_capture.py`: the page-error handler binds the error list of its own case.
- Code: missing docstring added (`tls.FingerprintSet.__init__`).
- README: title and status updated to 3.0.0 release candidate (was "2.2.1" and
  "example code"), test command now uses `-c tests/pytest.ini`, `schedule.py` and `qa/`
  added to the structure, `app_options.py` no longer described with a write-only
  password, note on upgrades from 1.x, line wraps cleaned up; Settings section
  describes groups, unsaved indicator and the download URL hint; Tests section lists
  the backend tests and the qa UI checks.

Changes in rc3
- Installation: success only if the Shelly reports exactly the target version (Gen1:
  the vX.Y.Z part); any other version, also a newer one, ends as "Unconfirmed" with
  the hint "The Shelly reports X instead of the target version Y". The early stop
  (reachable, old version, firmware never requested) now happens after 3 minutes
  instead of 60 seconds. Polling unchanged (first after 30 s, every 10 s, at most
  5 minutes).
- Devices: the power-on action is a lightning icon in the action column (restart
  colour, no state) that opens the power-on dialog; disabled with an explanation for
  devices without switchable outputs. The AOP column is removed (stored column
  settings drop it). The actions icon is now a link icon.
- Phone details: the power-on button ("Power-on: …") carries the lightning icon; all
  buttons sit side by side and wrap.
- Gen1 firmware column shows only the version number (e.g. 1.14.0); the full string
  stays in the tooltip and the status details. Gen2+ unchanged.
- "Install update" in the row menu and as a red button in the status popup, only for
  installable devices (Ready, not ignored, no check running).
- Ignored devices show an additional yellow "Ignored" label next to the status (on
  phones in the tile header).
- Phone logs: entries are separate tiles with a 5 px gap.

Corrections in rc2 (phone layout only; no functional, API or backend change)
- Devices: each device tile now sits on the page background with its own card
  background and a border mixed from the text colour (computed about 4.2:1 against
  the tile in the dark and 3.2:1 in the light default theme). rc1 used the theme
  divider colour, which is about 1.4:1 and barely visible; opened
  details stay inside their tile.
- Firmware: per version the cells are placed in a fixed order – version with tags,
  size and date on one line, checksum with its copy button, device list in its own
  area with a separator, then download and delete side by side in one right-aligned
  row below. rc1 stretched the buttons as a column over all metadata rows.
- Settings: every option is one block (label, control, help text directly below)
  separated from the next one, controls use the same full width, checkboxes sit on
  the label line, pinned certificates show host above fingerprint + remove. The
  common save area with "Unsaved changes" stays visible at the bottom while
  scrolling. The time field now uses the same input style as the other fields (also
  on desktop). Desktop layout otherwise unchanged (the new option blocks are
  transparent to the existing grid).

Upgrade notes (one-time migrations at the first start of 3.0)
- Option `check_interval_hours` replaced by `check_interval_days` (1–30, default 7)
  and `check_time` (`HH:MM`, default `02:45`); every install starts with "every 7 days
  at 02:45".
- Option `log_max_entries` (max. 20000) replaced by `log_entry_limit` (100–3000,
  default 1000); every install starts once at 1000 and the log is trimmed to the newest
  1000 entries. Retention days are unchanged.
- Shared password option `shelly_password` removed completely (option, schema, UI and
  authentication fallback). The app saves its cleaned options once through the
  Supervisor (`/addons/self/options`) before any device check, so the old value no
  longer remains in the stored options; retried at the next start if that fails. The
  value is never read, copied to a device entry, written to a device or logged.
  Devices that only used the shared password need their own device password. Backups
  made before the upgrade still contain the old value.
- Stored column preferences (v2) migrate to the new layout: "Eco" and "Restart" become
  the "Action" column, unknown column IDs are discarded, AOP is hidden by default.
- Stored logs are not rewritten; old entries are shown as success or error.
- No check runs after the update or a fresh install (2.x checked once after a version
  change); the first automatic check runs at the next scheduled time.

Check schedule
- Automatic checks every N days at a clock time in the Home Assistant time zone
  (`TZ` from the Supervisor, `zoneinfo`; no location or internet lookup). A changed
  schedule anchors at the next occurrence of the chosen time.
- Manual checks, single-device checks and "Reload device info" no longer move the
  schedule. Missed dates are caught up once after a restart with the cadence kept; at
  most one automatic check per scheduled date, the attempt is recorded at its start (no
  retry loop on failures). Due checks wait while installations run.
- DST: a missing local time runs at the first valid time after the gap, a repeated
  time runs once at its first occurrence.
- Header: "Last checked" and the check cycle as two separate pills; tooltip with the
  next run.

Device checks
- Full checks query up to 4 devices in parallel (fixed internal limit). Version offers
  and firmware downloads are single-flight per firmware ID and check, so parallelism
  never duplicates requests or files. Check reservation, install exclusion, per-device
  activity and cancellation cleanup are unchanged; all devices show "Checking…" while
  they are being processed.
- The complete `/shelly` identity response is reused; `Shelly.GetDeviceInfo` is only
  requested when needed. Feature states come from the existing combined settings read
  (Gen2+ `Shelly.GetConfig`, Gen1 `/settings`) plus `Webhook.List` / `Schedule.List`
  (Gen2+) or `/settings/actions` (Gen1); only on/off flags are stored, never action
  URLs or scripts.

Ignored devices
- Row menu "Ignore device" / "Resume device" (`POST /api/devices/{id}/ignore`).
  Ignored devices are excluded from automatic checks, "Check now" and background reads,
  shown in their own card "Ignored" at the end, have their own filter and cannot be
  installed. Known update information stays visible. They are still counted as
  registered. A single-device check remains possible. Resuming sends nothing to the
  device.

Reload device info
- Row menu "Reload device info" (`POST /api/devices/{id}/reload`), also for ignored
  devices: reads identity, installed firmware and settings from the device only – no
  offer lookup, changelog or download. Known update information and its timestamp are
  kept; a separate info time is stored. The row shows "Reloading…", then a short
  message.

Device table and actions
- New "Gen" column after IP; the Device cell shows the Shelly name with a pencil that
  opens the name dialog ("Edit Shelly name" removed from the row menu).
- "Action" column replaces "Eco" and "Restart": eco mode, password protection, access
  point, Bluetooth (green on / grey off), actions and schedules (red if at least one is
  active, grey if none; open the Shelly web UI in a new tab with noopener/noreferrer)
  and restart. Local inline SVG icons with accessible names, tooltips and keyboard
  support; unsupported features are disabled with an explanation, unknown values are
  marked "?" and never shown as off.
- Access point (Gen2+ `Wifi.SetConfig` `ap.enable`) and Bluetooth (legacy global
  `BLE.SetConfig` `enable`, only where the firmware offers it) dialogs only switch on or
  off. Firmware without the global Bluetooth switch (Gen2+ 2.0 and newer) shows the icon
  disabled. Gen1 access point is read-only: enabling it disables the Wi-Fi client mode
  per the official API docs.
- AOP is an optional column (hidden by default) with the current values on hover or
  focus; on phones it is a labeled button with the value.
- Data columns can be reordered by drag and drop or Alt+Arrow on the header; "Reset
  columns" in the column chooser. Order, visibility and sort are stored in the app for
  all users.
- Phones: device tiles with border and spacing, labeled action buttons, firmware
  download/delete fixed side by side.
- Editing with a failed read-back now warns that the value could not be verified.

Device passwords
- One dialog with explicit modes: store existing password (tests and stores, no device
  change), enable protection, change password, remove protection and forget stored
  password. Device-side changes use Gen2+ `Shelly.SetAuth` (user `admin`, realm =
  device ID, HA1) and Gen1 `/settings/login`; enable/change require a second red
  confirmation and the acknowledgment "I have stored the password securely", also
  checked by the backend. The warning about loss of access and factory reset is always
  shown.
- The result is verified with the candidate password and the protection state
  reported by `/shelly`; on an ambiguous answer the previous credential is kept and no
  success is claimed. Local credentials are deleted on "Remove protection" only after
  the device confirms. The lock icon shows the device's protection state, independent
  of a stored password. Secrets never appear in state, API responses or logs.

Logs
- Severity per entry: success (green), warning (yellow: unreachable, timeout,
  temporarily missing data) or error (red: failed writes, installations,
  authentication, internal errors); checks get the highest severity of their devices.
  New "Warnings" filter. The desktop layout is unchanged apart from the severity tag.
- Phones: compact entries (time, type, severity, short summary) with collapsed details.

Settings
- Grouped cards (Checks, Firmware & storage, Logs, Advanced), stacked on phones, with
  one "Save settings" button and an "Unsaved changes" indicator. The shared password
  field is removed. Time input for the check time.

Unchanged
- Python 3.12.16, Alpine 3.24 base image, requirements and wheels; architectures and
  minimum Home Assistant 2026.10.

## 2.2.1

Devices
- Single-device check: row menu ⋮ → "Check for updates" (`POST
  /api/devices/{id}/check`). Same steps as the full check for this device (reuses
  stored firmware, downloads missing new firmware), never installs, restarts or changes
  settings. Updates only the device's check time, not "Last check" of the full check and
  not the schedule. Logged as check with trigger "device".
- Progress of a running check: global line with completed/total, unavailable devices
  and phase (device list, devices, download, firmware information) plus progress bar;
  per device row "Waiting", "Checking…" (spinner) or "Downloading…" instead of the
  previous status.
- Last attempt and last successful check are stored separately (`last_success`);
  status details show both. Existing devices without error adopt their last check as
  last success.

Mobile layout (≤ 700 px)
- Compact device cards: name, Shelly name · generation, status, ⋮ menu, firmware
  installed → available, IP; AOP, Eco, model, firmware ID, NTP and check times in an
  expandable "Details". Restart stays in the ⋮ menu.
- No column chooser on phones (column choice applies to wide screens only), equal-width
  tabs (all four reachable), full-width search, horizontally scrollable filter chips,
  touch targets ≥ 44 px, page title hidden inside the HA panel.

Desktop / theme
- Status pills: text colour mixed half towards the theme text colour – contrast ≥ 4.5:1
  in the HA default theme, light and dark (2.2.0: down to 1.75:1 for "Ready" in light
  mode); subtle row hover. Layout unchanged.
- Fixed column widths: the columns of "Updates available" and "All other devices" line
  up; long device names are truncated with the full name as tooltip.
- Single-device checks are labelled "Single-device check" in the Logs tab.

Robustness
- Checks are reserved synchronously on the server: a second "Check now" / "Check for
  updates" (double tap, second browser) gets 409 instead of starting twice; approvals,
  firmware deletion and settings of the device being checked are refused meanwhile.
- Interrupted downloads (`.part` files) are removed at startup; they are never offered.
- Saving the state is atomic as before; a failed write (e.g. disk full) keeps the
  previous file, is shown in the header ("Storage error") and does not abort the check.
- On app stop running checks and installations are cancelled cleanly; no device stays
  "Checking…" (activity and progress live in memory only). An interrupted installation
  remains "Unconfirmed" as before, never "Up-To-Date".

Resources
- UI polling: 30 s when idle, 3 s while a check or installation runs, none while the
  page is hidden; refresh at once when it becomes visible again; never two requests in
  parallel (was: every 5 s, always).
- Scheduler: event-driven wait until the next due check (max. 1 h per wait) instead of
  waking up every minute.
- State file written compactly (no indentation).
- Mobile details are built only when opened.
- Changelog cache: a stored changelog is reused for 24 h if it already lists every
  offered version; a version it does not list yet triggers a new fetch, at most once
  per hour (the Gen1 page alone is about 1.2 MB). A clock jump backwards forces a
  fetch. A failed write of the changelog file keeps the old file.
- Browser cache for the UI files: `app.js` and `style.css` are loaded with a content
  hash (`?v=…`) and may be cached by the browser (`private, max-age=1 year,
  immutable`); a new version changes the URL. The page itself and all API answers
  stay `no-store`.
- `/api/state` is conditional: ETag = hash of the answer, an unchanged state gets an
  empty 304 and is not parsed or rendered again.
- Minified UI files: readable, commented sources in `ui-src/`, minified build output
  in `app/static/` (app.js 52 → 35 KB, style.css 14.5 → 13 KB), built locally with the
  pinned esbuild 0.28.2 by `tools/build_ui.py`. Each output records the SHA-256 of
  its source; a test fails if the build is outdated. The image needs no Node.

Tests
- New test suite (`tests/`, pytest + pytest-asyncio, all network access mocked):
  single-device check, progress, reservation/409, cancellation, partial downloads,
  write errors, state migration, CSRF/Ingress rules of the new endpoint, changelog
  cache, ETag/304, static caching, up-to-date UI build.

## 2.2.0

Platform
- Requires Home Assistant 2026.10 or newer. Wording "add-on" → "app" in UI, docs and
  comments (slug unchanged).
- Offline, reproducible build: base image pinned by digest
  (`python:3.12.16-alpine3.24`), all Python packages bundled as musllinux wheels for
  amd64/aarch64 and installed with `--no-index --require-hashes`; all transitive
  versions pinned (aiohttp 3.14.5). Multi-stage build: only the installed packages
  are copied into the final image, the wheels are not kept (no BuildKit needed).
- Docstrings for every function, class and nested handler.

Security
- A device is identified from `/shelly` before authentication, so a device password
  can be set even if the shared password is missing or wrong.
- Shelly authentication: shared password (option `shelly_password`) and per-device
  password (row menu → "Device password", tested before saving). Gen1 Basic Auth,
  Gen2+ Digest Auth SHA-256 only (user `admin`, no downgrade, realm checked).
- Per-device passwords in `/data/credentials.json` (0600), never in state, API, UI or
  logs; Gen2+ stores only HA1 = SHA-256(admin:<device id>:<password>). Gen1 and shared
  passwords are plain text (also in backups) – documented in the README.
- Gen2+ firmware is downloaded via HTTPS only; Gen1 downloads are marked "HTTP only".
- Corrected the misleading checksum comment: Gen1 firmware is offered via HTTP only and
  without a checksum. Warning in the approval dialog for Gen1.
- New option `gen1_updates_enabled` (default on); when off, Gen1 devices are skipped
  (new status "Skipped") and Gen1 approvals are refused.
- Neutral User-Agent for all outgoing requests; device responses limited to 1 MB,
  manufacturer JSON to 2 MB (bodies are now read completely in chunks – previously a
  single read could return a truncated body).
- Error responses of both servers keep their security headers without the deprecated
  "return HTTPException" pattern (aiohttp 3.14).
- Theme adoption rejects values containing `url(`, `image(`, `@import` or CSS escapes.
- README documents the Gen2+ firmware ID in the `updates.shelly.cloud` URL as the only
  device information sent to Shelly.

UI
- New layout with tabs Devices · Firmware · Logs · Settings and inline icons.
- Devices: search (name/IP/model), filter chips with counts, "Columns ▾" dropdown;
  card "Updates available" with selection and "Review & install (n)" above "All other
  devices"; sorted by name, column sort within the cards. New device column (HA name +
  Shelly name · generation), firmware column (installed → available + firmware ID),
  status pill with tooltip (hover/keyboard) and popup, Eco as dot + On/Off, Restart icon.
  Optional columns: Last update, NTP. Row menu ⋮ with web UI, name, NTP, power-on
  action, eco mode, device password, status details and restart. Compact cards below
  700 px. Column settings of earlier versions are reset once.
- Install dialog shows the changelog of all versions between installed and target
  (categories as tags, security highlighted, model-specific entries marked); fetched from
  `shelly-api-docs.shelly.cloud` (HTTPS, 4 MB limit, pinning) once per check when updates
  were found. Fallback "Changelog not available"; installation stays possible.
- Firmware: search, storage summary, retention hint behind ⓘ; one card per firmware ID
  with versions as rows ("latest", "HTTP only"), expandable device list, icon buttons
  for download and delete, shortened SHA-256 with copy button.
- Unified dialogs: title with ✕, two-column value list, buttons bottom right, danger
  actions in red.
- The UI copies all CSS variables of the active HA theme and follows light/dark live.
- Settings tab: all app options incl. shared password (write-only, set/not set) and
  certificate pins; saving via the Supervisor (`/addons/self/options`) with announced
  app restart (`/addons/self/restart`).

Logs
- New activity log in `/data/events.json` (default 90 days / 2000 entries, options
  `log_retention_days`, `log_max_entries`): checks (trigger, user, devices, duration,
  result, per-device findings), firmware downloads, started installations
  (confirmed/unconfirmed/failed), deleted firmware and Shelly setting changes (old →
  new, user, result), per-device password changes (set / removed / test failed) and app
  option changes from the Settings tab (changed options with old → new; the shared
  password only as "changed"). Passwords and hashes are never logged. User from the
  Ingress `X-Remote-User-*` headers.
- Logs tab grouped by day with filters (All, Checks, Installations, Settings – incl.
  passwords and app settings –, Errors),
  device search and "Hide checks without findings".

## 2.1.0

- New columns: Restart (restart the Shelly after one confirmation) and Eco Mode
  (green/grey indicator, switch on/off after one confirmation).
- NTP server and AOP are saved directly; a message appears only for errors or if
  nothing changed. The Shelly name keeps the old → new confirmation.
- Uniform one-word statuses: Up-To-Date, Available, Ready, Queued, Installing,
  Unconfirmed, Error, Unregistered. Details open in a popup via the info icon.
- Column chooser visible by default; columns renamed to "HA Name", "Shelly Name" and
  "Update".

## 2.0.1

- Default certificate pins for `updates.shelly.cloud` and `fwcdn.shelly.cloud`.

## 2.0.0

- No check at add-on start any more – except for the first start and after an add-on
  update. An overdue check (e.g. after downtime) runs shortly after the start.
- Scheduled checks notify in Home Assistant about newly found updates (once per device
  and version).
- Approval modes (default / always manual / ignore) and automatic installation removed:
  every update is installed after manual approval only. Option `require_manual_approval`
  removed.
- Only Shelly main devices are listed; sub-devices are skipped.
- New columns: HA name, Shelly name (editable), NTP server (editable), AOP – action on
  power on of all outputs (editable), last update.
- Settings changes read the live values first and are only sent after a confirmation
  showing old and new values; the result is read back from the device.
- Sortable columns and a column chooser on both tabs, stored in the add-on for all users.
- Fine row lines, cleaned-up header, tab icons; status no longer shows who approved.
- New add-on icon and logo; sidebar icon `mdi:download-circle`.
- Everything in English (code, UI, logs, notifications, documentation); German
  translation removed.

## 1.6.0

- Firmware tab: stored firmware with download and delete; the two highest versions per
  identifier are kept and reused after a SHA-256 check.
- One offer request per firmware identifier and check; metadata via HTTPS only.
- Code review and clean-up.

## 1.5.0

- Parallel installations (configurable maximum) and multi-select.

## 1.4.0

- Installation check: wait 30 seconds, then poll every 10 seconds; stop early with a
  diagnosis if the Shelly never requested the firmware.

## 1.3.0

- UI adopts the Home Assistant theme; IP column links to the Shelly web UI.
- Installation diagnosis (firmware never requested, download rejected, not installed).

## 1.2.0 – 1.2.2

- Certificate pinning for Shelly servers with the in-house Allterco CA, several
  fingerprints per host; version number in the log.

## 1.1.0 – 1.1.1

- Own download server instead of the Home Assistant `www` folder; firmware in the
  add-on's private `/data`; automatic clean-up; log of the approving user; 32-bit ARM dropped.

## 1.0.0

- First version: detects Shellys from the Home Assistant device registry, checks for
  stable firmware, provides it locally and installs it after approval.
