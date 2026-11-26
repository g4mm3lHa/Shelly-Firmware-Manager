# UI decisions: Settings tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Settings tab, the firmware store dialogs, device passwords or setting help texts.

## Settings tab
- Settings tab design approved.
- **Settings cards (3.5.0; Device Passwords first since 2026-11-22):** Device Passwords, Checks, Download Server (download base URL, serve only to
  known Shellys), Firmware & Storage (Gen1 updates, Allow downgrades – ticking it opens the
  popup "Allow downgrades?", see `devices.md`, block
  "Firmware store" with export/import, Max. import size), Backups (3.11.0, F16: "Back up
  before every installation", on; "Keep backups per device", 10, 1–50, with the note how
  many backups are stored; the firmware store hint, the export dialog and the import
  summary name the backups as part of the export), Logs, Advanced (Max.
  parallel installations, Resolve host names, Pinned certificates; 3.8.0, mockup
  `3.8.0-hostname` B). Option labels with "Max." in front. Units in texts always "MB".
- **Inner surface (live test 3.13.0-rc1, variant B, approved 2026-11-25):** the options of
  each settings card stand on one inner surface in the card background (radius 12, 8 px
  from the card edge, inside 14 / 12 px, phones 12 px), like the rows of the other tabs on
  their tinted card; the card head stays on the tint.
- **Columns (live test 3.10.0-rc1, approved 2026-11-22):** the cards fill independent column stacks
  (no shared row heights): three columns from 1600 px, two from 1168 px, else one; card n
  goes to column n mod columns, so the order reads left to right as before.
- **Changelog hint (3.5.0, 2026-11-19):** last line of the Settings tab, below
  the Save area: hint "What changed in each version: open the app page in Home
  Assistant and choose “Changelog” next to the version." – the link opens the info
  page of the app in the whole window (`/config/app/<slug>/info`, slug from the
  Supervisor; the list of apps until it is known). The Configuration tab of the app page holds
  only the hint field "settings" (name "All settings: open the app → tab “Settings”",
  help text that this page has no further options; no effect – HA shows no links there,
  3.11.1, approved 2026-11-23); all options are in the Settings tab.

## Firmware store and device passwords (approved 2026-11-19, 3.4.0)
- **Entry points (8a B, 7a B; places changed in 3.5.0):** Settings tab: block
  "Firmware store" in the card "Firmware & Storage" (buttons "Export store" /
  "Import store" with export/import icons before the text) and card "Device Passwords"
  (button "Device passwords" with lock icon; buttons without "…" since 2026-11-22).
- **Export (8b B):** dialog with value list (files with downloaded/uploaded, size, file
  name), the generated password in a read-only field with copy icon (shown only here;
  if the browser refuses copying, the field's text is selected),
  yellow box that it seals and is stored nowhere (live test rc5), checkbox "I have saved the password";
  "Export" is enabled only after it and starts the browser download. A prepared export
  that is older than its download period is refused in the dialog ("Export expired –
  please start it again.", 2026-11-21); too little free disk space is reported when the
  dialog opens.
- **Import (8c A):** step 1 file + password + "Check file" (the hint names the maximum
  import size). Step 2: success icon, value list (file, exported, size), section
  "Result" with counts new / already stored – data added if missing / conflict – stored
  file kept; each group expandable, conflicts expanded; button "Import N files"
  ("Finish import" without new files). Errors as error message.
- **First start (8d both):** dialog "Welcome – import a firmware store?" (Skip /
  Import store) once per browser; then the yellow notice bar with the same buttons
  until answered (it takes precedence over the download base URL notice). "Check now"
  is disabled with a tooltip; empty device list: "they are found by the first check".
- **Device passwords for several devices (7b, mockup `Passwoerter_Dialog_final`):**
  sections Action (store existing / set password, set preselected), Devices ("All
  devices that fit (n of m)", one row per device with name, "Gen n · protection state"
  and the step "→ enable protection" / "→ change password" right; devices that do not fit are
  greyed out with the reason, also right (2026-11-22, U13)), Password (+ repeat for set), "Store in this app"
  (unchecked → red box), yellow "Keep the password safe" box. Blue button "Continue"
  (dialog rule b, 2026-11-22) → second confirmation with "I have stored the password
  securely" and the red button "Set on n devices" →
  result per device (green check / red X / yellow "!" skipped) plus the yellow HA
  Shelly integration reminder.
- **Details confirmed with the live test of 3.4.0-rc1:** "Store the existing password"
  for several devices also gets a second confirmation (without "stored securely"),
  button "Test & store" (blue); busy devices (installing, being checked, no
  IP) are greyed out with "busy or not reachable – try again later"; a password over 50
  characters is refused in the dialog while a Gen1 device is selected; the log entries
  of export, import and the skipped first start appear under the filter "All" only.
- **Upload progress (3.4.1):** firmware upload and store import send the file in parts
  and show "Uploading <file> … n %" in the dialog, then "Checking <file> …" (import).

## Offer delay, previous version, import (3.9.0, mockups `3.9.0`, approved 2026-11-22)
- **Options (F1):** "Offer updates only after (days)" in "Checks" after "Check time";
  "Keep installed versions" in "Firmware & Storage" after "Allow downgrades", below it
  the line "n installed versions: x are stored (MB), y are downloaded at the next check
  (about z MB each). Free: …" (hidden while no version counts).
- **Import (F16):** yellow box "Import only a store you exported yourself …" in step 1 and
  in the first-start dialog; step 2 without the source per file, group "new – will be
  stored as “Imported”", hint that every file is stored as "Imported", checkbox "I created
  this export myself or I trust its source" – the import button is enabled only then;
  result "n new files stored as “Imported”". Export and its log entry count
  "imported" too.

## Setting help and download base URL hints (2026-11-17)
- Settings tab: help texts not shown under the fields; i-icon (16 px) next to the
  setting name, tooltip on mouse hover over name or icon and on keyboard focus of the
  icon; touch: tap on name or icon toggles it (tap elsewhere or scrolling hides it), a
  touch tap on the name does not focus/toggle the control (desktop click unchanged);
  the help icon has a 44 px touch target on phones. The text stays the field's
  `aria-describedby` (visually hidden); the live warning of the download base URL stays
  under the field. It names the reason while typing: "Not accepted: this IP address is
  not in a private network (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7). Use
  the LAN IP of Home Assistant.", "Not accepted: the address must not contain a user
  name or password." and "… a query (?…) or a fragment (#…)."; host names and
  localhost keep the advice to use the LAN IP. The hint judges the typed text (number forms
  such as 3232235778 count as non-private IP addresses, as on the server); other format
  errors (spaces, non-ASCII, bad host name) get no hint, the server names them on save. A stored address with a non-private IP
  shows the notice above the tabs (message of the server, only "Open settings", no
  "This address is correct") and installations stay disabled. A stored address with odd
  characters or a bad host name shows the same notice with the message "Installations are
  disabled: the download base URL is not a valid address (http(s)://host:port, letters,
  digits, dots and hyphens in the host). Correct it in the Settings tab." (approved).
- **Suggested download base URL (2026-11-21):** an empty download base URL field is filled with
  `http://<host>:8098`, the host being the address the page was opened with, only if that host is an
  IP address in a private network (never a host name, public address or localhost). It counts as an
  unsaved change ("Unsaved changes" shows); nothing is saved until the user clicks Save. Grey note under
  the field while it holds the suggestion: "Suggestion from the address of this page – please check it
  before saving." (approved 2026-11-21); it disappears when the user edits the field.

## Connection test of the download server (3.12.0, mockups `3.12.0-ui` A1, approved 2026-11-24)
- **Host port (2026-11-24, `reports/3.12.0/FRAGEN-rc3.md` 1a 2a 3 4 5):** first field of the
  card "Download Server" (number, default 8098), read from Home Assistant each time the tab
  loads. Line below: the port check on saving; "switched off in Home Assistant" (empty field)
  or "could not be read" (field disabled, pointer to the Configuration tab). Typing a new
  port changes the port of the download base URL while it uses the previous port. The save
  dialog adds a yellow box "The app restarts with port …"; a port in use is refused.
- **Port warning (live test 3.12.0-rc3, 2a):** yellow line under the download base URL
  (with the other live hints) while its port – typed, or 80/443 by the scheme – differs from
  the host port field; live while typing in either field, saving stays possible; none while
  the host port is unknown or empty.
- **After saving (live test 3.12.0-rc3, 1a):** the page reloads after the app restart on the
  Settings tab (marker `#settings` in the address, removed at the start); a normal reload
  opens Devices.
- **Texts with the port:** help of the download base URL ("+ the host port above"), Getting
  started and the confirmation of the former example address name the real host port; the
  connection test names the port cause only while the address port differs from it.
- **Row (1 a):** below the download base URL: "Test connection" (play icon) and the last
  result: green "Reachable · last tested <time> with <device>", red "Not reachable · …" or
  grey "Not tested yet"; shown while the result belongs to the saved address. The button is
  disabled with the tooltip "Save first" while the field holds an unsaved change, and with
  the notice text while installations are disabled by the address.
- **Dialog (play icon):** intro, section Test (Download base URL, select "Test device":
  "Automatic (first reachable device of Gen 2 or newer)" and the devices that can test),
  hint about Gen 1 and the one-time test address; Cancel / blue "Start test". Running and
  result: check lines Download base URL, Download server, "Fetch by <device> (<ip>)";
  reachable: green message; not reachable: red message, the error (server text shown
  translated in the page language; a device error as readable text with its codes, e.g.
  "connection error (Shelly code -114 / -14)"; the log entry and the bug ticket text stay
  English, the raw answer only there), "Common causes" (the
  port cause names the port of the saved address) and the hint about the port; Close / blue
  "Test again". Without a Gen2+
  device: yellow message "No Shelly of Gen 2 or newer is known yet …".
- **Installation dialog (1 a):** yellow check line "Download server" with "Test now" at the
  top of "Before installing" while no test with the current address succeeded.
