# Shelly Firmware Manager – documentation

User documentation of the app (shown in the Documentation tab of the app page in Home
Assistant). Version history: `CHANGELOG.md` (Changelog tab).

## Contents

- [First start](#first-start)
- [Configuration](#configuration)
- [Using the app](#using-the-app)
  - [Devices](#devices)
  - [How a check works](#how-a-check-works)
  - [Installing updates](#installing-updates)
  - [Downgrade (Gen2+)](#downgrade-gen2)
  - [Firmware](#firmware)
  - [Logs](#logs)
  - [Settings](#settings)
- [Device passwords](#device-passwords)
- [Gen1 firmware](#gen1-firmware)
- [Troubleshooting](#troubleshooting)
- [Certificate pinning](#certificate-pinning)
- [Privacy and network connections](#privacy-and-network-connections)
- [Security](#security)
- [Upgrading from older versions](#upgrading-from-older-versions)
- [Credits](#credits)

## First start

Requires the Home Assistant Shelly integration (the app works with the devices it
knows). Only original Shelly firmware is supported for now.

1. Start the app and enable "Show in sidebar".
2. Open the app, go to the **Settings** tab and set the **download base URL**: the LAN
   IP address of Home Assistant with port 8098, e.g. `http://192.168.1.20:8098`.
   Installations are disabled until it is set.
3. If you use a firewall: allow the Shellys to reach Home Assistant on TCP port 8098.
4. Turn on the **Watchdog** switch on the app page (if it is off): Home Assistant then restarts
   the app if it stops unexpectedly (it only checks that port 8098 accepts connections).

## Configuration

All options can be changed in the app's **Settings** tab (or the Configuration tab of
the app page). Saving restarts the app.

| Option | Meaning |
|---|---|
| Check every (days) / Check time | Automatic check every 1–30 days at a time of day (default: every 7 days at 02:45, Home Assistant time zone) |
| Download base URL | LAN IP of Home Assistant + port 8098. Use the IP address, not a host name – Shellys often cannot resolve names. Required for installations |
| Serve downloads only to known Shellys | Firmware is only served to the device it is meant for (default on). Switch off if downloads are rejected from a 172.30.x.x address |
| Parallel installations | How many installations run at the same time (1–20, default 5) |
| Enable Gen1 updates / Allowed Gen1 types | Gen1 support and the Gen1 models to check (see [Gen1 firmware](#gen1-firmware)) |
| Allow downgrades | Installing an older stored firmware on Gen2+ devices (see [Downgrade](#downgrade-gen2)). Off unless switched on, also after an update of the app |
| Log size / Log retention | Activity log limits (100–3000 entries, 1–365 days) |
| Pinned certificates | Certificate fingerprints for Shelly servers (see [Certificate pinning](#certificate-pinning)) |

## Using the app

The app has four tabs: **Devices**, **Firmware**, **Logs** and **Settings**. It follows
the Home Assistant theme (colours, light/dark) and works on phones.

### Devices

- **Check now** checks all devices; the header shows the last check and the schedule.
- Devices with updates are listed on top, then all other devices, ignored devices at
  the end. Search, filters (Updates, Errors, Up to date, Ignored), sorting and a column
  chooser help with larger installations. Sort with the "Sort" select and the arrow
  next to it, or (desktop) with the column titles. Sort order, hidden columns and column
  order are kept in the browser of each device you use (phone and PC can differ); a new
  device starts sorted by device name. A device you just updated stays on top as "Updated"
  (or "Unconfirmed" if the target version was not confirmed, or "Error" if the
  installation failed – then with checkbox and **Install** for another attempt) until
  you check it again or the next full check runs (it stays there while it is checked).
- The action icons show and change power-on action, eco mode, password protection,
  access point, Bluetooth, actions and schedules and the websocket debug log, and
  restart the device. Their popups start with a strip that shows the current state. A "?" means
  the value is currently unknown. Colours: power-on action green = every output
  restores its last state, red = another value; lock green = protected and the password
  is stored in the app, yellow = protected but the password is not stored here; access
  point, actions and schedules red = active; websocket debug log yellow = on. Gen1 devices show the power-on values with
  the names of their own web interface ("Restore last mode", "Switch"). Every change is
  read back from the device; a value the device accepted but did not apply is reported
  as a warning and logged as failed.
- **Websocket debug log** (Gen2+, bug icon): turns on the debug log that the Shelly
  streams over a websocket; the Diagnostics page of the Shelly web UI (linked in the
  popup) shows it live. Turn it off afterwards – the log may contain network and
  configuration details and streaming uses device resources. Below firmware 1.0.0 the
  stream is not protected by the device password. Gen1 devices have no such log.
- Hovering over a device name shows the Home Assistant and the Shelly device name. On
  touch devices a tap shows a tooltip where the tap has no other function, and the
  reason of a disabled button.
- The row menu ⋮ offers: Check for updates (this device only), Install update,
  Downgrade, Reload device info, Ignore / Resume device, Open web UI, device settings,
  Status details and Restart.
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
| Skipped | Gen1 device while Gen1 updates are disabled |
| Unregistered | Device is no longer in the Home Assistant device registry |

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

1. Tick the devices under "Updates available" and click **Review & install** (or use
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

No check runs after an installation. The next stored version – or the offer of the
Shelly server – is provided when you check the device again (row menu ⋮ → Check for
updates) or at the next full check.

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
  SHA-256 and the devices they are provided for. The total size is shown at the top.
  The cards can be sorted by firmware ID, newest version, size or storage date (select
  and arrow, or the column titles; kept per device like in the Devices tab); the
  versions inside a card stay newest first.
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
  are not marked). A version newer than the installed one is
  provided as update by the next check (see [How a check works](#how-a-check-works)),
  an older one allows a downgrade. The app cannot verify where an uploaded file comes
  from, and the version is the one you confirmed – only use files you trust.

### Logs

The activity log records checks, downloads, uploads, installations and downgrades,
deleted firmware, setting and password changes on devices and changes of the app
settings – each with time, user and result (success, warning, error). A download names
the user of the check that fetched it; scheduled checks and their downloads name
"Schedule". Filters and a
device search help to find entries. Passwords never appear in the log. Consecutive
entries of the same device are marked by a bar at the side.

The bin icon deletes a single entry, **Delete all logs** the whole log (each after a
confirmation). After clearing, one entry remains that names who cleared the log.

### Settings

All options from [Configuration](#configuration), grouped into cards with one Save
button. The i-icon next to a setting (or the setting name) shows its help – on touch
devices with a tap. A hint warns while typing if the download base URL is a host name or not in
your local network. While installations are disabled because of the download base URL,
a notice above the tabs explains why.

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

Passwords are stored only in the app's private storage. For Gen2+ only a hash bound to
the device is stored. Gen1 devices need the password itself (Basic Auth): it is stored
in plain text and is part of Home Assistant backups, and Basic Auth sends it almost
unprotected through your network.

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

An installation interrupted by a restart of the app is shown as "Unconfirmed" – check
the device and run "Check for updates".

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

- **Your Shellys** (private IP addresses from the Home Assistant device registry) – to
  read their state, change settings you approve and start installations.
- **Shelly servers** below `shelly.cloud` – version offers, firmware downloads and the
  official changelog pages. Only the device type (e.g. `Plus1`) is part of the request;
  no other information about your devices or Home Assistant is sent.
- **Home Assistant** via the Supervisor – device registry, notifications and the app's
  own settings.

The user interface loads nothing from the internet. No telemetry.

## Security

- The interface is only reachable through Home Assistant (administrators only) and is
  protected against cross-site requests.
- Every approval is bound to the SHA-256 of the exact file. Downgrades, deleting
  firmware and password changes need a second confirmation.
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

- **From 3.0.0 release candidates:** if the old example address
  `http://192.168.178.50:8098` is still set, installations are blocked until you change
  it or confirm it with "This address is correct".
- **From 3.0.0 or 3.0.1:** nothing to do. From 3.0.0 the activity log is converted once
  to its new storage format at the first start. 3.1.1 adds a watchdog: check that the
  **Watchdog** switch on the app page is on. Log entries get an internal ID at the
  first start of 3.1.1 (needed to delete single entries).
- **From 3.1.1 to 3.2.1:** downgrades are off after the update – switch on **Allow
  downgrades** in the Settings tab if you use them. The sort order and column settings
  of the Devices table start fresh (device name ascending) and are then kept per
  browser.
- **From 2.x or 1.x:** since 3.0.1 the conversion code of these versions is no longer
  included. Update to 3.0.0 first and start it once (it removes the former shared Shelly
  password from the stored options and converts the table settings), then update to
  the current version – or
  remove and reinstall the app. Backups made before the 3.0 upgrade still contain the
  old shared password.

## Credits

- Icons: [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache 2.0),
  inlined.
- Shelly mark in the app icon: [Simple Icons](https://simpleicons.org/) (CC0 1.0).
