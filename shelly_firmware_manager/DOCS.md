# Shelly Firmware Manager – documentation

User documentation of the app (shown in the Documentation tab of the app page in Home
Assistant). Version history: `CHANGELOG.md` (Changelog tab).

## Contents

- [First start](#first-start)
- [Configuration](#configuration)
- [Using the app](#using-the-app)
  - [Devices](#devices)
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
| Log size / Log retention | Activity log limits (100–3000 entries, 1–365 days) |
| Pinned certificates | Certificate fingerprints for Shelly servers (see [Certificate pinning](#certificate-pinning)) |

## Using the app

The app has four tabs: **Devices**, **Firmware**, **Logs** and **Settings**. It follows
the Home Assistant theme (colours, light/dark) and works on phones.

### Devices

- **Check now** checks all devices; the header shows the last check and the schedule.
- Devices with updates are listed on top, then all other devices, ignored devices at
  the end. Search, filters (Updates, Errors, Up to date, Ignored) and a column chooser
  help with larger installations.
- The action icons show and change power-on action, eco mode, password protection,
  access point, Bluetooth, actions and schedules, and restart the device. A "?" means
  the value is currently unknown.
- The row menu ⋮ offers: Check for updates (this device only), Install update,
  Downgrade, Reload device info, Ignore / Resume device, Open web UI, device settings,
  Status details and Restart.
- **Ignored** devices are skipped by automatic checks and cannot be updated until they
  are resumed.

| Status | Meaning |
|---|---|
| Up-To-Date | The newest stable firmware is installed |
| Available | An update exists, the firmware is not downloaded yet |
| Ready | Firmware downloaded, waiting for your approval |
| Queued / Installing | Installation waiting for a free slot / running (with progress for Gen2+) |
| Unconfirmed | The installation ran, but the target version was not confirmed (see the details) |
| Error | Check, download or installation failed |
| Skipped | Gen1 device while Gen1 updates are disabled |
| Unregistered | Device is no longer in the Home Assistant device registry |

### Installing updates

1. Tick the devices under "Updates available" and click **Review & install** (or use
   "Install update" in the row menu).
2. The dialog shows installed and target version, size, SHA-256, source and the
   changelog (security entries and entries for this model are highlighted).
3. **Approve installation.** The Shelly downloads the firmware from the app and restarts.

The app then checks every 10 seconds until the device reports exactly the target
version (at most 5 minutes). If that does not happen, the status becomes "Unconfirmed"
with a diagnosis – see [Troubleshooting](#troubleshooting). Scheduled checks create a
Home Assistant notification when they find new updates.

### Downgrade (Gen2+)

Row menu ⋮ → **Downgrade** lists all stored versions that are older than the installed
one. Choose a version, check the installation dialog and confirm a second time. The
downgrade runs like a normal installation and is logged as such. It is available for
up-to-date and ignored devices too, but never for Gen1.

Shelly only offers the current version for download, so older versions are available
only if the app downloaded them earlier or you uploaded them (see [Firmware](#firmware)).
A device may refuse a downgrade, and settings of newer firmware may be lost.

### Firmware

- All downloaded and uploaded firmware files, grouped by device type, with size, date,
  SHA-256 and the devices they are provided for. The total size is shown at the top.
- Firmware is **never deleted automatically**. Delete files you no longer need with the
  bin icon (two confirmations). Files that are waiting to be installed cannot be deleted.
- **Upload firmware** adds an older version you have from another source:
  1. Choose the file (`.zip`, `.bin` or `.gbl`, max. 64 MB).
  2. Device type and version are filled in from the file where possible – check them.
  3. Optionally enter the expected SHA-256; the upload is refused if it does not match.

  Uploaded files are marked "Uploaded" and are only used for downgrades. The app cannot
  verify where an uploaded file comes from – only use files you trust.

### Logs

The activity log records checks, downloads, uploads, installations and downgrades,
deleted firmware, setting and password changes on devices and changes of the app
settings – each with time, user and result (success, warning, error). Filters and a
device search help to find entries. Passwords never appear in the log.

### Settings

All options from [Configuration](#configuration), grouped into cards with one Save
button. A hint warns while typing if the download base URL is a host name or not in
your local network. While installations are disabled because of the download base URL,
a notice above the tabs explains why.

## Device passwords

Each password-protected Shelly needs its own password (row menu ⋮ → **Device
password**, or the lock icon):

- **Store existing password** – tests the password and stores it in the app; the
  device is not changed.
- **Enable protection / Change password** – sets the password on the device (user
  `admin`). Requires a second confirmation; keep the password safe – losing it may
  require a factory reset.
- **Remove protection** and **Forget stored password**.

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
- **From 3.0.0:** nothing to do. The activity log is converted once to its new storage
  format at the first start.
- **From 2.x or 1.x:** 3.0.1 no longer contains the conversion code of these versions.
  Update to 3.0.0 first and start it once (it removes the former shared Shelly password
  from the stored options and converts the table settings), then update to 3.0.1 – or
  remove and reinstall the app. Backups made before the 3.0 upgrade still contain the
  old shared password.

## Credits

- Icons: [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache 2.0),
  inlined.
- Shelly mark in the app icon: [Simple Icons](https://simpleicons.org/) (CC0 1.0).
