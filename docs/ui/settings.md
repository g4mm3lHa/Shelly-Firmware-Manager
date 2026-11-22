# UI decisions: Settings tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Settings tab, the firmware store dialogs, device passwords or setting help texts.

## Settings tab
- Settings tab design approved.
- **Settings cards (3.5.0; Device Passwords first since 2026-11-22):** Device Passwords, Checks, Download Server (download base URL, serve only to
  known Shellys), Firmware & Storage (Gen1 updates, Allow downgrades, block
  "Firmware store" with export/import, Max. import size), Logs, Advanced (Max.
  parallel installations, Resolve host names, Pinned certificates; 3.8.0, mockup
  `3.8.0-hostname` B). Option labels with "Max." in front. Units in texts always "MB".
- **Changelog hint (3.5.0, 2026-11-19):** last line of the Settings tab, below
  the Save area: hint "What changed in each version: open the app page in Home
  Assistant and choose “Changelog” next to the version." – the link opens the info
  page of the app in the whole window (`/config/app/<slug>/info`, slug from the
  Supervisor; the list of apps until it is known). The Configuration tab of the app
  page has the same names, help texts and order as the Settings tab, without the
  changelog hint (HA does not show it there, 2026-11-19).

## Firmware store and device passwords (approved 2026-11-19, 3.4.0)
- **Entry points (8a B, 7a B; places changed in 3.5.0):** Settings tab: block
  "Firmware store" in the card "Firmware & Storage" (buttons "Export store" /
  "Import store" with export/import icons before the text) and card "Device Passwords"
  (button "Device passwords" with lock icon; buttons without "…" since 2026-11-22).
- **Export (8b B):** dialog with value list (files with downloaded/uploaded, size, file
  name), the generated password in a read-only field with copy icon (shown only here;
  if the browser refuses copying, the field's text is selected),
  hint that it seals and is stored nowhere, checkbox "I have saved the password";
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
