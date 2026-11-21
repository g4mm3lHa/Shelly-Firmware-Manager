# Firmware Manager for Shelly – documentation

User documentation of the app (shown in the Documentation tab of the app page in Home
Assistant). Version history: `CHANGELOG.md` (Changelog tab).

## Contents

- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Configuration](#configuration)
  - [Download base URL](#download-base-url)
- [Using the app](#using-the-app)
  - [Devices](#devices)
  - [How a check works](#how-a-check-works)
  - [Installing updates](#installing-updates)
  - [Downgrade (Gen2+)](#downgrade-gen2)
  - [Firmware](#firmware)
  - [Logs](#logs)
  - [Settings](#settings)
- [Device passwords](#device-passwords)
- [Moving the firmware store](#moving-the-firmware-store)
- [Gen1 firmware](#gen1-firmware)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Known limitations](#known-limitations)
- [Certificate pinning](#certificate-pinning)
- [Privacy and network connections](#privacy-and-network-connections)
- [Security](#security)
- [Upgrading from older versions](#upgrading-from-older-versions)
- [Support](#support)
- [Credits](#credits)

## Quick start

1. Install the app from the app store. Home Assistant downloads the ready-made image
   from `ghcr.io`, which takes a few seconds. Start the app and enable "Show in sidebar".
2. Open the app, go to the **Settings** tab and set the **download base URL**: the LAN
   IP address of Home Assistant with port 8098, e.g. `http://192.168.1.20:8098` (see
   [Download base URL](#download-base-url)). Installations are disabled until it is set.
   While the field is empty, the tab fills in the address of the page you are using if
   it is a private IP address – check it, then save.
3. If you use a firewall: allow the Shellys to reach Home Assistant on TCP port 8098.
4. Turn on the **Watchdog** switch on the app page (if it is off): Home Assistant then
   restarts the app if it stops unexpectedly or hangs (see [Troubleshooting](#troubleshooting)).
5. On a fresh installation the app asks once whether to import a firmware store from
   another Home Assistant instance (see [Moving the firmware store](#moving-the-firmware-store)).
   No check runs until you choose **Import store …** or **Skip**; a notice above the
   tabs and a Home Assistant notification remind you. Updates of the app never ask.
6. Click **Check now** in the **Devices** tab. Devices with an update are listed on
   top: tick them, click **Review & install** and approve the installation (see
   [Installing updates](#installing-updates)).

## Requirements

- Home Assistant OS or Home Assistant Supervised, version 2026.10 or newer. Apps need
  the Supervisor; Home Assistant Container and Core cannot run them.
- A 64-bit system: `aarch64` or `amd64`.
- The Home Assistant Shelly integration. The app works with the devices it knows.
  Supported are Shellys that connect over Wi-Fi/LAN and are set up in this integration.
  Not supported: Shelly BLU (Bluetooth, updated in the Shelly app), Zigbee (Zigbee OTA via
  ZHA or Zigbee2MQTT), Matter (Home Assistant Matter server) and devices connected only
  through MQTT. Gen4 devices with Zigbee or Matter firmware keep Wi-Fi and are supported
  when they are also set up in the Shelly integration. The app lists the unsupported
  Shellys it finds in Home Assistant under **Other Shellys**, for information only.
- The Shellys must reach Home Assistant on TCP port 8098.
- Home Assistant must reach the Shelly servers below `shelly.cloud` (see
  [Privacy and network connections](#privacy-and-network-connections)). The Shellys
  themselves need no internet access.

Only original Shelly firmware is supported; see [Known limitations](#known-limitations).

## Configuration

All options can be changed in the app's **Settings** tab (or the Configuration tab of
the app page – same names, help texts and order). Saving restarts the app.

| Option | Default | Allowed | Meaning |
|---|---|---|---|
| Check every (days) | 7 | 1–30 | Interval of the automatic check |
| Check time | 02:45 | HH:MM | Time of day of the automatic check (Home Assistant time zone) |
| Download base URL for the Shellys | – | rules below | LAN IP of Home Assistant + port 8098, e.g. `http://192.168.1.20:8098`. Required for installations |
| Serve downloads only to known Shellys | on | on / off | Firmware is only served to the device it is meant for. Switch off if downloads are rejected from a 172.30.x.x address |
| Enable Gen1 updates | on | on / off | Gen1 support: all Gen1 devices of the Shelly integration are checked (see [Gen1 firmware](#gen1-firmware)) |
| Allow downgrades | off | on / off | Installing an older stored firmware on Gen2+ devices (see [Downgrade](#downgrade-gen2)). Stays off until you switch it on, also after an update of the app |
| Max. import size (MB) | 100 | 1–2048 | Largest file accepted by **Import store** |
| Max. log size (entries) | 1000 | 100–3000 | Size limit of the activity log |
| Log retention (days) | 90 | 1–365 | Age limit of the activity log |
| Max. parallel installations | 5 | 1–20 | How many installations (and password changes) run at the same time; further approvals wait |
| Pinned certificates | two Shelly hosts | host + SHA-256 | Certificate fingerprints for Shelly servers (see [Certificate pinning](#certificate-pinning)) |

### Download base URL

The Shellys download the firmware from this address, so it must lead to the download
server of the app (port 8098) and be reachable from every Shelly.

- Form: `http://<address>:<port>` or `https://<address>:<port>`. A path may follow, e.g.
  for a reverse proxy; the file name is appended to it.
- Use the IP address of Home Assistant, not a host name: Shellys often cannot resolve
  names, the update then fails with a connection error. A host name is accepted, but
  the Settings tab warns while you type.
- The address may contain only printable ASCII characters and no spaces. A host name
  consists of letters, digits, hyphens and dots; write international names in punycode
  (`xn--…`). IPv6 zone IDs (`%eth0`) are refused.
- An IP address must be in a private network (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16` or IPv6 `fc00::/7`); any other IP address is refused, also in an unusual form such as
  `2130706433` or `0x7f.1`.
- User name, password, query (`?…`) and fragment (`#…`) are refused: the address is
  shown in the app and in the log, and nothing may follow the path.

If the field is empty, the Settings tab suggests `http://<address>:8098` from the
address of the Home Assistant page you are using – only if that address is an IP address
in a private network, never for a host name. The suggestion is not saved until you click
**Save settings**; check the port if you changed the host port of the app.

If an address saved with an older version of the app is no longer allowed,
installations stay disabled and a notice above the tabs names the reason until you
correct the address.

## Using the app

The app has four tabs: **Devices**, **Firmware**, **Logs** and **Settings**. It follows
the Home Assistant theme (colours, light/dark) and works on phones.

### Devices

- **Check now** checks all devices; the header shows the last check and the schedule.
- The list is split into sections, each shown only when it has devices: **Updates
  available**, **Attention**, **Not checked**, **Up-To-Date**, **Asleep**, **Ignored**,
  **Removed** and, at the end, **Other Shellys**. A device appears in the first section
  that fits (see the status table below).
- Search, the **Filter** icon, sorting and a column chooser help with larger
  installations; sort order and columns are kept per browser. The **Filter** icon next to
  **Sort** opens a popover with the filters **All**, **Updates**, **Attention**, **Not
  checked**, **Asleep**, **Up-To-Date**, **Unprotected**, **Ignored**, **Removed** and
  **Other Shellys**, each with its count. A dot on the icon shows that a filter other than
  **All** is active. The Firmware and Logs tabs have the same Filter icon with their own
  filters. **Unprotected** lists devices without password protection – set it per device
  from that list.
- **Attention** collects devices whose last check ended in an error, whose installation
  was not confirmed, that could not be reached, or that have no usable address.
  **Not checked** holds devices that were never checked and Gen1 devices that are skipped
  while "Enable Gen1 updates" is off.
- **Removed** holds devices that were once listed but are no longer in the Home
  Assistant Shelly integration. Their data is kept. If the device is set up in Home
  Assistant again, the mark disappears by itself. If it is still reachable at its last
  address, all actions stay available; **Check all** skips removed devices. The tooltip
  of the **Removed** pill says whether the device is still reachable. A single check logs
  a warning if the device is reachable and an error if it is not. The row menu of a
  removed device has **Remove from list**, which deletes it from the app after a
  confirmation. Removed wins over Ignored: a removed device loses its Ignored mark.
- **No usable address:** if the address of a device cannot be used – the host name does
  not resolve, the IP address is outside the private networks, or the device has no
  address – the tile shows **No usable address** in the IP column (on a phone only this
  text); on the desktop a line below the name and the tooltip give the reason. Fix the
  address in the Shelly integration (see
  [Troubleshooting](#troubleshooting)).
- **Other Shellys** lists Shelly devices that Home Assistant knows but this app cannot
  update (BLU, Zigbee, Matter, MQTT): name, kind, model, firmware and **Where to
  update**. The section is display only, has no actions and is shown only with the
  filter **All** or **Other Shellys**; the search applies to it. The app recognises
  these devices by the manufacturer text in Home Assistant, so a device may be missing.
- **Host name** (Columns chooser, off by default; on a phone a row in the details) shows
  the host name of the device. It is checked on every single check and on **Check all**.
- A device you just updated stays on top as "Updated" (or "Unconfirmed" if the target
  version was not confirmed, or "Error" if the installation failed – then with
  **Install** for another attempt) until you check it again or the next full check runs.
- The action icons show and change power-on action, eco mode, password protection,
  access point, Bluetooth, actions and schedules and the websocket debug log, and
  restart the device. The popup of an icon starts with the current state; "?" means the
  value is unknown. Every change is read back from the device; a value the device
  accepted but did not apply is reported as a warning and logged as failed. The lock is
  green if the device is protected and its password is stored in the app, yellow if the
  password is not stored here.
- **Websocket debug log** (Gen2+, bug icon): turns on the debug log that the Shelly
  streams over a websocket; the Diagnostics page of the Shelly web UI (linked in the
  popup) shows it live. Turn it off afterwards – the log may contain network and
  configuration details and streaming uses device resources. Below firmware 1.0.0 the
  stream is not protected by the device password. Gen1 devices have no such log.
- The row menu ⋮ offers per device: Check for updates, Install update, Downgrade, Device
  Details (status, firmware, model, generation, firmware ID, NTP server and history),
  Reload device info, Open web UI, the device settings, Ignore / Resume device and
  Restart. On touch devices a tap on a disabled button shows the reason.
- **Battery devices** (e.g. H&T, door/window sensors) sleep between wake-ups and answer
  only while they are awake. The app recognises them from Home Assistant (the sleep
  period of the Shelly integration entry, otherwise a battery entity of the device) or
  from the device itself when a check or Reload Device Info reaches them awake (Gen2+:
  wake-up period, Gen1: sleep mode) and shows a battery icon after the name; its tooltip
  says which source applied (**from Home Assistant** or **from device**). To check one,
  wake it up (e.g. press its button) and check it right away.
- **Ignored** devices are skipped by automatic checks and cannot be updated until they
  are resumed.

| Status | Meaning |
|---|---|
| Up-To-Date | No newer stable firmware is offered (Gen2+: by the Shelly server; Gen1: neither stored nor by the Shelly server) |
| Available | An update exists, the firmware is provided at the next check |
| Ready | Firmware provided (from the firmware store or downloaded), waiting for your approval – "Install" next to the status opens the installation dialog |
| Updated | The installation was confirmed; shown until the next check of the device |
| Queued / Installing | Installation waiting for a free slot / running (with progress for Gen2+) |
| Unconfirmed | The installation ran, but the target version was not confirmed (see the details); stays on top until the next check of the device |
| Error | Check, download or installation failed; after a failed installation – or with a stored version while the Shelly server could not be asked – the device stays on top and can be installed (again) |
| Asleep | Battery device that did not answer the last check – it sleeps; the IP is dimmed. Wake it up and check it again |
| Not checked | The device was never checked, or it is a Gen1 device skipped while Gen1 updates are off |
| Removed | Device is no longer in the Home Assistant Shelly integration; see **Removed** above |

### How a check works

A check (all devices, a single device or the schedule) works in this order:

1. Every device is asked in your network for its firmware ID and installed version.
2. **Gen2+ devices: the Shelly update server decides.** It is asked with the firmware ID
   and the installed version – the way a Shelly asks itself; only these two values are
   sent. The server answers with the next allowed step for this version. Devices with
   the same firmware ID and version share one request.
   - If the firmware store already holds exactly the offered version (downloaded or
     uploaded, SHA-256 checked), it is used; otherwise it is downloaded once and stored.
     A download must match the SHA-256 named in Shelly's download address.
   - **Intermediate versions:** some old firmware versions must be updated to an
     intermediate version first (e.g. Plus1 1.0.7 → 1.3.3 → 1.7.x). Such an offer is
     marked as intermediate version. After its installation is confirmed, the device is
     checked again at once and the next step is provided – it is installed only after
     you approve it.
   - **Server not reachable:** the device shows the error. If a newer version is
     stored, the error details say so: you can install it manually (checkbox and
     **Install** stay available), but at your own risk – without the server's answer it
     cannot be verified whether an intermediate version is needed first. The dialog asks
     you to accept this risk.
3. **Gen1 devices: the firmware store comes first.** If the store holds a newer stable
   version for the device's type, the **next higher** one is provided (step by step
   through the stored versions); the Shelly list is only asked for devices without
   one – one request for all Gen1 types.

Every check that asks the Shelly update server also makes one reference request with
fixed values (Plus1 1.0.7, which must be offered 1.3.3 – no data of your devices). If
the server answers differently, the check reports a warning: the server no longer
evaluates the installed version, so required intermediate versions cannot be detected.

The status details, the installation dialog and the log name the source of an update:
Shelly update server, firmware store, or an uploaded file.

The changelog of a provided version is requested once from the Shelly documentation
and kept with the stored firmware; it is not requested again afterwards.

### Installing updates

1. Tick the devices under "Updates Available" and click **Review & install** (or use
   "Install update" in the row menu, or **Install** behind a device in the Firmware tab).
2. The dialog shows installed and target version, size, SHA-256, source and the
   changelog (security entries and entries for this model are highlighted).
3. **Approve installation.** The Shelly downloads the firmware from the app and restarts.

Gen2+ devices report their progress over the device connection (the status label shows
the percentage). An error reported by the Shelly ends the installation at once. Without
a download request after 2 minutes, or without new progress for 60 seconds, the
installation fails. After 100 % the Shelly must restart within 30 seconds (the label
shows "Restarting") and report exactly the target version within 30 seconds after the
restart; the old version, another version or no answer is an error.

Devices without this connection (Gen1, or when it is not available) are checked every
10 seconds until they report exactly the target version (at most 5 minutes). If that
does not happen, the status becomes "Unconfirmed" with a diagnosis – see
[Troubleshooting](#troubleshooting). A failed installation can be retried from the
updates card. Scheduled checks create a
Home Assistant notification when they find new updates.

While a device is installed or checked, other devices can be checked and installed.
The device itself cannot be checked during its installation, nor installed during its
check. **Check all** runs alone: it cannot start while single checks, installations or
Reload Device Info run, and it blocks all of them while it runs. A scheduled check that
is due while other work runs is cancelled and logged; the next date counts.

No check runs after an installation – except after an intermediate version, which is
followed by a check of that device at once (see [How a check works](#how-a-check-works)).
The next stored version – or the offer of the Shelly server – is provided when you check
the device again (row menu ⋮ → Check for updates) or at the next full check.

### Downgrade (Gen2+)

Downgrades are off until **Allow downgrades** is switched on in the Settings tab; while
it is off, the entries below are greyed out with a hint and the app refuses downgrades.

Row menu ⋮ → **Downgrade** lists all stored versions that are older than the installed
one (the entry is greyed out while no older version is stored). Choose a version, check the installation dialog and confirm a second time.
In the Firmware tab, the downgrade icon of an older version lists the devices of this
type with a newer version installed; choose one and confirm a second time. The
downgrade runs like a normal installation and is logged as such. It is available for
up-to-date and ignored devices too, but never for Gen1.

Shelly only offers the current version for download, so older versions are available
only if the app downloaded them earlier or you uploaded them (see [Firmware](#firmware)).
A device may refuse a downgrade, and settings of newer firmware may be lost.

### Firmware

- All downloaded and uploaded firmware files, grouped by device type, with size, date,
  SHA-256 and the devices they are provided for. The top shows the number of files, their
  total size and the free disk space. Filters: Provided (to at least one device),
  Uploaded, Intermediate, Gen1 and Unused (no listed device has this firmware ID; no file
  counts as unused while the ID of a device is unknown, e.g. a sleeping battery device).
  Search and sort work as in the Devices tab; versions inside a card stay newest first.
- **Files on disk:** at start the app scans the firmware folder. A file that is on the
  disk but not listed (for example after a damaged state file) is added again if its
  SHA-256 equals its file name and a device or the manifest in the file names its
  firmware ID and version; the name alone is not enough. A Gen2+ file taken over this
  way is provided only when the Shelly update server offers exactly this SHA-256 – never
  as stored fallback or for a downgrade. The log then shows a warning that files were
  read from the disk, because they could have been changed. A file that cannot be
  matched is shown as **unknown file** so you can delete it; nothing is deleted
  automatically.
- **Install** behind a device name opens the usual installation dialog for this device
  and this firmware (same conditions as "Install update" in the Devices tab).
- The **changelog icon** of a version shows the changelog stored with this firmware.
  It is greyed out while none is stored: the changelog is stored when a check provides
  the file to a device (see [How a check works](#how-a-check-works)).
- Firmware is **never deleted automatically**. Delete files you no longer need with the
  bin icon (two confirmations). A file that is provided to devices can be deleted too:
  the dialog names them, they lose the provided update and show "Available" until they
  are checked again. A file that is being installed cannot be deleted, nor every file
  during **Check all** and the files of the checked device's type during a single check. To skip a stored version (Gen1: the store comes first),
  delete it.
- **Upload firmware** adds a version you have from another source:
  1. Choose the file (`.zip`, `.bin` or `.gbl`, max. 64 MB).
  2. Device type and version are filled in from the file where possible – check them.
  3. Optionally enter the expected SHA-256; the upload is refused if it does not match.

  Uploaded files are marked "Uploaded". A file that a check provided as an
  intermediate version is marked "intermediate" (files provided before this app version
  are not marked). An uploaded version is used when the Shelly update server offers
  exactly this version, or for a manual installation while the server cannot be
  reached (see [How a check works](#how-a-check-works)); an older one allows a
  downgrade. The app cannot verify where an uploaded file comes
  from, and the version is the one you confirmed – only use files you trust.

### Logs

The activity log records checks, downloads, uploads, installations and downgrades,
deleted firmware, setting and password changes on devices, store exports and imports
and changes of the app settings – each with time, user and result (success, warning, error). A download names
the user of the check that fetched it; scheduled checks and their downloads name
"Schedule". A full check also counts the ignored devices ("2 ignored") and names them
in its details in the normal text colour – they are not contacted and never make the
entry a warning or error. Filters and a device search help to find entries. Passwords never appear in the log. Consecutive
entries of the same device are marked by a bar at the side.

The bin icon deletes a single entry, **Delete all logs** the whole log (each after a
confirmation). After clearing, one entry remains that names who cleared the log.

### Settings

All options from [Configuration](#configuration) in the cards **Checks**, **Download
Server**, **Firmware & Storage**, **Logs** and **Advanced**, with one Save button. The
i-icon next to a setting shows its help (on touch devices with a tap). While you type, a
hint warns if the download base URL is a host name or not in your local network; while
installations are disabled because of it, a notice above the tabs explains why. The card
**Firmware & Storage** holds the export and import of the firmware store (see [Moving the
firmware store](#moving-the-firmware-store)), the card **Device Passwords** sets
passwords on several devices at once (see [Device passwords](#device-passwords)). The
last line links to the app page in Home Assistant, where **Changelog** next to the
version lists what changed in each version.

## Device passwords

Each password-protected Shelly needs its own password (row menu ⋮ → **Device
password**, or the lock icon):

- **Store existing password** – tests the password and stores it in the app; the
  device is not changed.
- **Enable protection / Change password** – sets the password on the device (user
  `admin`). Requires a second confirmation; keep the password safe – losing it may
  require a factory reset. **Store in this app** (on by default) keeps the new password
  for the app; without it the password is only verified, any stored password is deleted
  once the device confirms the new one, and the app cannot reach the device (checks,
  installations, settings) until you store the password.
- **Remove protection** and **Forget stored password**.

After enabling, changing or removing the protection, change the password in the Home
Assistant Shelly integration as well.

**Several devices at once:** Settings tab → **Device Passwords** → **Device passwords …**. Choose the action –
**Store the existing device password in this app** or **Set password** (enables the
protection of unprotected devices, changes it on protected devices whose password is
stored) – tick the devices ("All devices that fit") and enter one password for all of
them. Devices the action does not fit are greyed out with the reason (e.g. protected
without stored password: store the current password first; state unknown: reload the
device info). Each device is still set, verified, stored and logged on its own, as in
the single-device dialog; up to **Max. parallel installations** devices at the same time. A
failure on one device does not stop the others; busy devices are skipped. The result
lists every device (done, failed, skipped). Removing the protection and forgetting a
password stay in the single-device dialog.

Passwords are stored only in the app's private storage. For Gen2+ only a hash bound to
the device is stored. Gen1 devices need the password itself (Basic Auth): it is stored
in plain text and is part of Home Assistant backups, and Basic Auth sends it almost
unprotected through your network.

## Moving the firmware store

To move to another Home Assistant instance (or keep a copy), export the whole firmware
store in the Settings tab → **Firmware & Storage** → **Export store …**:

- The app generates an **export password** and shows it once (copy icon; if the browser
  does not allow copying, the password is selected for copying by hand). Save it –
  it is stored nowhere, and the file cannot be imported without it. **Export** is
  enabled after "I have saved the password"; the browser downloads one ZIP file.
- The password **seals** the file: the list of all files with their SHA-256 and data
  (source, date, changelog) is protected by an HMAC-SHA256 whose key is derived from
  the password (scrypt). The file is **not encrypted**: unpacked, it shows the folders
  `Gen2+/<firmware ID>/` and `Gen1/<firmware ID>/` with readable file names (uploads
  end in `-upload`), plus `manifest.json` and `SHA256SUMS.txt`. An archive that was
  unpacked and packed again is accepted as long as no file changed.
- Stored files whose SHA-256 no longer matches are left out (warning in the log).
  Device passwords are never exported.

On the other instance: **Import store …**, choose the file and enter the export
password. The file is checked completely – seal, every file – before anything is
stored; then the result shows how many files are new, already stored or in conflict
(each group can be expanded; conflicts are expanded). **Import** stores the new files:

- Each file keeps its source ("downloaded" from Shelly or "uploaded") and its original
  date, changelog and intermediate marker. Gen1 files are imported even if Gen1 updates
  are off or the type is not allowed.
- Already stored (same SHA-256): not stored twice; missing data is added, and an upload
  becomes "downloaded" if the export holds it as a download.
- Conflict (same firmware ID and version, different file): the stored file is kept.

The largest accepted file is set by **Max. import size (MB)**. Large files are sent in
parts of 8 MB (Home Assistant refuses single requests above 16 MB); the dialog shows
the progress. The import also needs
about twice the file size of free disk space. Not possible during a check. The import is
logged with user and counts. Uninstalling the app in Home Assistant removes its private
storage including the firmware store – export it first if you want to keep it.

## Gen1 firmware

Shelly offers Gen1 firmware only over plain HTTP and without a checksum, so the app
cannot verify where a Gen1 file comes from. Gen1 installations show a warning, and
Gen1 files are marked "HTTP only". Turn off "Enable Gen1 updates" to skip Gen1 devices.
Gen2+ firmware is downloaded over HTTPS only.

## Troubleshooting

| Message | What to do |
|---|---|
| Installations are disabled: set the download base URL | Enter the LAN IP of Home Assistant with port 8098 in the Settings tab |
| The Shelly never requested the firmware / Connection error -14 | The download base URL is wrong or blocked: it must be the IP of Home Assistant, port 8098 must be reachable from the Shelly |
| download_base_url: http(s)://host:port expected, without user name, query or fragment | The address contains a user name, a password, a query or a fragment – remove them (see [Download base URL](#download-base-url)) |
| download_base_url: http(s)://host:port expected | The address has a wrong form, a space or a character that is not allowed in a host name – see [Download base URL](#download-base-url) |
| download_base_url: the IP address is not in a private network | Enter the LAN IP of Home Assistant; only addresses in `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` or `fc00::/7` are accepted (see [Download base URL](#download-base-url)) |
| Installations are disabled: the download base URL is not a valid address | The saved address contains characters or a host name that are not allowed – correct it in the Settings tab (see [Download base URL](#download-base-url)) |
| Installations are disabled: the IP address of the download base URL is not in a private network | The saved address was accepted by an older version of the app – correct it in the Settings tab; installations stay disabled until then |
| Download rejected: request came from 172.30.x.x | Docker hides the device address – turn off "Serve downloads only to known Shellys" |
| The Shelly downloaded the firmware but still reports … | The device did not install it – check the log in the Shelly web UI |
| The Shelly reports an update error … It downloaded the firmware but rejected it | The installed firmware may be too old for a direct update to this version: check the device again – the Shelly update server names the required intermediate version (or install one via the web interface of the Shelly or an upload) |
| Check warning: the Shelly update server no longer answers with the next step | Shelly changed its update server: required intermediate versions cannot be detected. Prefer the web interface of the Shelly for devices on very old firmware |
| Error … Stored version X is ready and can be installed manually | The Shelly server could not be asked. Check again later, or install the stored version at your own risk |
| Download does not match the SHA-256 named by the manufacturer's URL | The download was damaged or altered and was not stored – check again |
| Many "Download rejected" lines in the app log | Only the first rejected request per address and 10 minutes is logged; the others are counted in one line |
| The Shelly reports X instead of the target version Y | Check the device; run "Check for updates" again |
| Certificate of … is not publicly trusted | See [Certificate pinning](#certificate-pinning) |
| The Shelly requires a password | Store the device password (see [Device passwords](#device-passwords)) |
| No usable address (tile in the Devices tab) | The host name does not resolve, the IP address is outside the private networks or the device has no address. Set an address in the Shelly integration (IP address or a host name that Home Assistant resolves) |
| Warning: Firmware files read from the disk: … taken over, … unknown – they could have been changed | The firmware list was rebuilt from files in the firmware folder after the state was lost. Taken-over Gen2+ files are used only when the Shelly update server offers the same SHA-256. Delete files you do not trust in the Firmware tab; unknown files are listed there and never deleted automatically |
| Session expired – reload the page | The login of Home Assistant ran out while the page was in the background. The page retries silently for about 30 seconds; if the message stays, reload the page |
| Device address … is not in a private network | Only devices in `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` or `fc00::/7` (IPv6) are contacted. Give the Shelly an address in one of these networks |
| The app stopped and did not start again | Turn on **Watchdog** and **Start on boot** on the app page. With the Watchdog on, Home Assistant restarts the app after it stopped unexpectedly |
| The app is shown as running but does not respond | The container health check (a TCP connection to port 8098 every 30 seconds) is meant to make Home Assistant restart the app after three failures if the Watchdog is on. Otherwise restart the app by hand and report the lines of the app log if it happens again |
| Storage error: The state file was unreadable – the backup of the previous save was loaded | The app's state file was damaged (e.g. by a power failure) and the copy of the save before was used. Check the devices again; the message disappears with the next restart |
| Storage error: The state file was unreadable and no backup could be loaded | The device list starts empty and is filled by the next check. The firmware files are kept on disk but no longer listed; a file is listed again when a check downloads the same version |

An installation interrupted by a restart of the app is shown as "Unconfirmed" – check
the device and run "Check for updates".

## FAQ

**Which Shellys does the app list?** Every device of the Home Assistant Shelly
integration that has its own address in your network, Gen1 to Gen4. Devices that are
connected through another Shelly (e.g. Bluetooth devices behind a gateway) and devices
disabled in Home Assistant are not listed. Shellys that Home Assistant knows but the
app cannot update (BLU, Zigbee, Matter, MQTT) appear under **Other Shellys**, display
only. A Gen4 with Zigbee or Matter firmware is supported when it is also set up in the
Shelly integration over Wi-Fi.

**Why is a device under "Removed"?** It is no longer in the Home Assistant Shelly
integration. The app keeps it so it does not vanish unnoticed. Set it up in Home
Assistant again and the mark disappears, or use **Remove from list** in its row menu.

**Do the Shellys need internet access?** No. Home Assistant asks the Shelly servers and
downloads the firmware; the Shellys fetch it from the app on port 8098.

**Does the app install updates on its own?** No. A check only provides the firmware.
Every installation needs your approval in the installation dialog.

**Does it offer beta firmware?** No, only stable versions.

**Is there a beta version of the app?** Release candidates of the next version appear in
the `beta` branch of the repository. To try one, add
`https://github.com/g4mm3lHa/Shelly-Firmware-Manager#beta` as a second repository in the
app store (like the stable one, with `#beta` at the end). It offers **Firmware Manager
for Shelly (Beta)**, a separate app with its own data and its own firmware store. Only
one of the two apps can use host port 8098: give the beta app another host port in the
network section of its Configuration tab (e.g. 8097) and set its download base URL to
that port. Report problems as issues, with the app version.

**Why an IP address instead of a host name?** Many Shellys cannot resolve host names of
your network. The download then fails with a connection error (see
[Download base URL](#download-base-url)).

**Why has the download server on port 8098 no login?** Shellys cannot log in to a
download server. The server only serves files of a running installation and, by
default, only to the device being updated (see [Security](#security)).

**What happens if an installation is interrupted?** The device is shown as
"Unconfirmed" or "Error". Check the device again; a failed installation can be repeated
from the updates card.

**How much disk space does the firmware store need?** The Firmware tab shows the number
of files, their size and the free disk space. Firmware is never deleted automatically;
the filter **Unused** lists files no device needs. The store is part of every Home
Assistant backup of the app, so backups grow with it: the size in the Firmware tab is
about what a backup of the app adds.

**Can I go back to an older firmware?** Yes, on Gen2+ devices, if the older version is
in the firmware store and **Allow downgrades** is switched on (see
[Downgrade](#downgrade-gen2)).

## Known limitations

- Only original Shelly firmware is supported, and only stable versions.
- Home Assistant OS or Supervised is required; the user interface is in English only.
- Only Shellys on Wi-Fi/LAN that are set up in the Home Assistant Shelly integration are
  supported. Not supported: Shelly BLU (updated in the Shelly app), Zigbee (Zigbee OTA
  via ZHA or Zigbee2MQTT), Matter (Home Assistant Matter server) and MQTT-only
  devices; they are shown under **Other Shellys** without update function. Gen4 with
  Zigbee or Matter firmware is supported when it is also in the Shelly integration.
- Only devices with their own address in a private network (`10.0.0.0/8`,
  `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) are contacted. Devices connected
  through another Shelly (e.g. Bluetooth devices behind a gateway) are not listed as
  devices on purpose.
- Battery devices can only be checked and updated while they are awake.
- Gen1: Shelly offers the firmware only over plain HTTP and without a checksum (see
  [Gen1 firmware](#gen1-firmware)); no downgrade; no installation progress.
- A downgrade needs the older version in the firmware store – Shelly only offers the
  current version for download. A device may refuse a downgrade.
- Communication with the Shellys is unencrypted in your network.
- Shelly's server interfaces are not officially guaranteed; a change on their side can
  break checks until the app is updated.

## Certificate pinning

Some Shelly servers (`updates.shelly.cloud`, `fwcdn.shelly.cloud`) use certificates
from Shelly's own certificate authority, which browsers and other software do not
trust. The app therefore accepts only certificates with known fingerprints for these
hosts; the fingerprints are included.

If Shelly changes a certificate, checks fail with a message that shows the new
fingerprint. Before adding it:

1. Verify the fingerprint independently, e.g. on a phone using mobile data: open
   `https://updates.shelly.cloud/update/Plus1` and compare the certificate's SHA-256
   fingerprint.
2. Settings tab → Pinned certificates → add the host and the fingerprint → Save.

Only hosts below `shelly.cloud` can be pinned.

## Privacy and network connections

The app connects only to:

- **Your Shellys** (private IP addresses from the Home Assistant device registry:
  `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, IPv6 `fc00::/7`) – to read their
  state, change settings you approve and start installations.
- **Shelly servers** below `shelly.cloud` – version offers, firmware downloads and the
  official changelog pages. Only the firmware ID (e.g. `Plus1`) and, for Gen2+ devices,
  the installed firmware version are part of the request; no other information about
  your devices or Home Assistant is sent.
- **Home Assistant** via the Supervisor – device registry, notifications, the app's
  own settings and its slug (for the link to the app page).

The user interface loads nothing from the internet. No telemetry.

Home Assistant itself downloads the app image from `ghcr.io` when you install or update
the app. The app does not connect there and nothing about your system is sent.

## Security

- The interface is only reachable through Home Assistant (administrators only) and is
  protected against cross-site requests.
- Every approval is bound to the SHA-256 of the exact file. Downgrades, deleting
  firmware and password changes need a second confirmation.
- A store import is refused if the seal or any file does not match; the export
  password is shown once and stored nowhere.
- The download server (port 8098) only serves files of a running installation, by
  default only to the device being updated. It has no login, because Shellys cannot
  authenticate.
- All Python packages are bundled and pinned by checksum; the base image is pinned.
  The app runs inside its own container (as root there) without access to your Home
  Assistant files.
- An AppArmor profile (on hosts with AppArmor, e.g. Home Assistant OS) restricts the app
  further: it may only run Python, read its program files, write its private storage and
  use the network – no other programs, no special privileges.

Remaining risks: communication with the Shellys is unencrypted; Gen1 firmware and
uploaded files cannot be verified; Gen1 passwords are stored in plain text; Shelly's
server interfaces are not officially guaranteed.

## Upgrading from older versions

Updates from the previous version need no action. Older versions:

- **From 3.7.1 or older:** the list **Allowed Gen1 types** is gone; all Gen1 devices of
  the Shelly integration are checked. Turn off **Enable Gen1 updates** to skip them
  (they then show as **Not checked**). A saved value is ignored. Devices that disappear
  from Home Assistant now stay in the list under **Removed**.
- **From 3.6.0 or older:** an IP address outside the private networks that was saved as
  download base URL is no longer accepted. Installations stay disabled until you correct
  it; the notice above the tabs names the reason.
- **From 3.1.1 or older:** downgrades are off after the update – switch on **Allow
  downgrades** in the Settings tab if you use them. The sort order and columns of the
  Devices table start fresh and are then kept per browser. Check that the **Watchdog**
  switch on the app page is on.
- **From 3.0.0 release candidates:** if the old example address
  `http://192.168.178.50:8098` is still set, installations are blocked until you change
  it or confirm it with "This address is correct".
- **From 2.x or 1.x:** update to 3.0.0 first and start it once (it removes the former
  shared Shelly password from the stored options and converts the table settings), then
  update to the current version – or remove and reinstall the app. Backups made before
  the 3.0 upgrade still contain the old shared password.

## Support

1. Look up the message in [Troubleshooting](#troubleshooting) and the [FAQ](#faq).
2. Questions and discussion: the Home Assistant community thread
   <https://community.home-assistant.io/t/firmware-manager-for-shelly-devices/1035784>.
3. Bugs and feature requests: <https://github.com/g4mm3lHa/Shelly-Firmware-Manager/issues>.
   A bug report needs the versions of the app and of Home Assistant, the Shelly model
   and generation, the entry of the activity log (Logs tab) and, if there is one, the
   lines of the app log (Log tab of the app page).
4. Do not report security problems in a public issue; the repository's `SECURITY.md`
   describes the private way.

Problems of Home Assistant itself or of the Shelly firmware belong to those projects.

## Credits

Firmware Manager for Shelly is an unofficial community project. It is not affiliated
with, endorsed or sponsored by Allterco Robotics or Shelly. Shelly is a trademark of its
holder; the name is used only to say which devices the app works with. No Shelly
firmware or documentation is part of the app – firmware is downloaded from Shelly's
servers when you run a check.

The app itself is released under the MIT License (`LICENSE` in the repository). It uses
the following open-source components:

| Component | License |
|---|---|
| [aiohttp](https://github.com/aio-libs/aiohttp) (with llhttp) | Apache-2.0 and MIT |
| [aiohappyeyeballs](https://github.com/aio-libs/aiohappyeyeballs) | PSF-2.0 |
| [aiosignal](https://github.com/aio-libs/aiosignal) | Apache-2.0 |
| [attrs](https://github.com/python-attrs/attrs) | MIT |
| [frozenlist](https://github.com/aio-libs/frozenlist) | Apache-2.0 |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause |
| [multidict](https://github.com/aio-libs/multidict) | Apache-2.0 |
| [propcache](https://github.com/aio-libs/propcache) | Apache-2.0 |
| [typing-extensions](https://github.com/python/typing_extensions) | PSF-2.0 |
| [yarl](https://github.com/aio-libs/yarl) | Apache-2.0 |
| [Material Design Icons](https://pictogrammers.com/library/mdi/) (inlined icons) | Apache-2.0 |
| [Python](https://www.python.org/) and [Alpine Linux](https://alpinelinux.org/) (base image) | PSF-2.0 and the licenses of the Alpine packages |

The Python packages are the versions pinned in `requirements.txt`. Their license files
are part of the image (`/deps/<package>.dist-info/licenses/`), the license texts of the
app and of the icons are in `/app/licenses/`.
