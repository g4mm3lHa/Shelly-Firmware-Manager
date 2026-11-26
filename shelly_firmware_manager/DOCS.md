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
  - [Offer delay](#offer-delay)
  - [Keep installed versions](#keep-installed-versions)
  - [Installing updates](#installing-updates)
  - [Scheduled installation](#scheduled-installation)
  - [Beta firmware](#beta-firmware)
  - [Downgrade (Gen2+)](#downgrade-gen2)
  - [Firmware](#firmware)
  - [Schedules](#schedules)
  - [Backups](#backups)
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
2. Open the app. On a fresh installation it asks once whether to import a firmware
   store from another Home Assistant instance (see
   [Moving the firmware store](#moving-the-firmware-store)). Choose **Skip** if this is
   your first instance. No check runs until you choose **Import store** or **Skip**; a
   notice above the tabs and a Home Assistant notification remind you. Updates of the
   app never ask.
3. Go to the **Settings** tab and set the **download base URL**: the LAN IP address of
   Home Assistant with port 8098, e.g. `http://192.168.1.20:8098` (see
   [Download base URL](#download-base-url)). Installations are disabled until it is set.
   While the field is empty, the tab fills in the address of the page you are using if
   it is a private IP address – check it, then save.
4. If you use a firewall: allow the Shellys to reach Home Assistant on TCP port 8098.
5. Turn on the **Watchdog** switch on the app page (if it is off): Home Assistant then
   restarts the app if it stops unexpectedly or hangs (see [Troubleshooting](#troubleshooting)).
6. Click **Check now** in the **Devices** tab. Devices with an update are listed on
   top: tick them, click **Review & install** and approve the installation (see
   [Installing updates](#installing-updates)).

## Requirements

- Home Assistant OS or Home Assistant Supervised, version 2026.10 or newer. Apps need
  the Supervisor; Home Assistant Container and Core cannot run them.
- A 64-bit system: `aarch64` or `amd64`.
- The Home Assistant Shelly integration. The app works with the devices it knows. "Gen2+"
  in this documentation means Plus, Pro, Gen3 and Gen4 devices.
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

All options are in the app's **Settings** tab; the app stores them itself. Saving restarts
the app, except when only **Base colour** changed. The **Configuration** tab of the app page in Home Assistant only points to the
**Settings** tab: Home Assistant shows no links there, and the one field you see has no
effect. Its **Network** section shows the host port of the download server; you can
change it there or with **Host port** in the **Settings** tab.

| Option | Default | Allowed | Meaning |
|---|---|---|---|
| Check every (days) | 7 | 1–30 | Interval of the automatic check |
| Check time | 02:45 | HH:MM | Time of day of the automatic check (Home Assistant time zone) |
| Offer updates only after (days) | 0 | 0–60 | Holds a new firmware version back until it is this many days old, counted from its build date; 0 offers it at once (see [Offer delay](#offer-delay)) |
| Host port | 8098 | 1–65535 | Port of the download server on the Home Assistant host. The app sets it in Home Assistant and restarts; it refuses a port another program already uses on the host, because the app could not start then (checked only when the download base URL leads to an address in the local network). A download base URL with the old port follows the change |
| Download base URL for the Shellys | – | rules below | LAN IP of Home Assistant + the host port, e.g. `http://192.168.1.20:8098`. Required for installations |
| Serve downloads only to known Shellys | on | on / off | Firmware is only served to the device it is meant for. Switch off if downloads are rejected from a 172.30.x.x address |
| Enable Gen1 updates | on | on / off | Gen1 support: all Gen1 devices of the Shelly integration are checked (see [Gen1 firmware](#gen1-firmware)) |
| Allow downgrades | off | on / off | Installing an older stored firmware on Gen2+ devices (see [Downgrade](#downgrade-gen2)). Stays off until you switch it on, also after an update of the app |
| Offer beta firmware | off | on / off | Gen2+: also offers the beta versions of the Shelly update server; each needs an extra confirmation (see [Beta firmware](#beta-firmware)) |
| Keep installed versions | on | on / off | Gen2+: also stores the firmware a device already runs while Shelly still offers it, so a downgrade stays possible after a bad update (see [Keep installed versions](#keep-installed-versions)) |
| Max. import size (MB) | 100 | 1–2048 | Largest file accepted by **Import store** |
| Back up before every installation | on | on / off | Stores the settings of a device before every update and downgrade; if the backup fails, nothing is installed (see [Backups](#backups)) |
| Keep backups per device | 10 | 1–50 | The newest backups of each device are kept, older ones are removed automatically; a backup marked “Kept” stays |
| Max. log size (entries) | 1000 | 100–3000 | Size limit of the activity log |
| Log retention (days) | 90 | 1–365 | Age limit of the activity log |
| Max. parallel installations | 5 | 1–20 | How many installations (and password changes) run at the same time; further approvals wait |
| Resolve host names | off | on / off | Looks up each device's host name at every check and shows the **Host name** column (see [Devices](#devices)). Off: no lookup, no column |
| Pinned certificates | two Shelly hosts | host + SHA-256 | Certificate fingerprints for Shelly servers (see [Certificate pinning](#certificate-pinning)) |
| Base colour | Default | Default, Blue, Teal, Green, Violet, Orange | Colour of the surfaces, buttons and links of this app, for all browsers. **Default**: neutral grey surfaces, buttons and links in the colour of the Home Assistant theme. A colour replaces the theme colour in this app; card **Display** |

### Download base URL

The Shellys download the firmware from this address, so it must lead to the download
server of the app (port 8098) and be reachable from every Shelly.

- Form: `http://<address>:<port>` or `https://<address>:<port>`. A path may follow, e.g.
  for a reverse proxy; the file name is appended to it.
- The port is the **Host port** of the app (default 8098, same card). Changing it there
  changes the address too; after a change in the **Network** section of the app page,
  change the address yourself. A yellow line under the address warns while its port is
  not the host port.
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

**Test connection** below the field checks the saved address before the first
installation: a Shelly of Gen 2 or newer fetches a test address from the download
server (choose a device or let the app take the first reachable one). The result stays
below the field until the address or the host port changes. On failure the dialog names the error and
the common causes: a wrong IP address, a changed or switched-off host port, or a
firewall or separate network (VLAN, guest Wi-Fi) between the Shellys and Home Assistant.
Save a changed address first; Gen1 devices cannot run the test. While no test succeeded,
the installation dialog shows a yellow **Download server** line with **Test now**.

## Using the app

The app has six tabs: **Devices**, **Firmware**, **Schedules**, **Backups**, **Logs**
and **Settings**. It follows the Home Assistant theme (colours, light/dark; **Base colour** in
the Settings tab changes its colour) and works on phones; there only the active tab shows its name. The user interface is in English,
German, Dutch, French, Italian, Spanish, Polish or Portuguese (see [Settings](#settings)).

On a fresh installation the card **Getting started** above the device list leads
through three steps: set the download address, run the first check, test the
connection. Each step is ticked as soon as it is done; **Hide** removes the card for
good once all three are done.

### Devices

**Check now** checks all devices; the header shows the last check and the schedule.
Search, the **Filter** icon, sorting and a column chooser help with larger
installations; sort order and columns are kept per browser. The download icon left of
the column chooser (desktop) saves the list as shown – search and filters applied – as
a CSV file (semicolon, UTF-8): name, Shelly name, area, model, generation, IP, firmware,
available version, status and last check.

**Areas:** as soon as a device is in an area of Home Assistant, the **Area** column
(after **Device**; on a phone the first row of the details) and the area icon next to
**Filter** appear. The area filter takes several areas at once, also **Without area**;
search finds the area too. The app reads the areas at every check.

**Security:** a strip above the list counts the devices without password protection
and with the cloud, the access point or the websocket debug log switched on (unknown
states do not count). Only counts above 0 are shown; without any, the strip shows **No
findings**. On a phone a count shows its icon and number only, the name is in its
tooltip. A click on a count filters the list, a second click clears it.

The list is split into sections, each shown only when it has devices. A device appears
in the first section that fits, **Removed** and **Ignored** first:

| Section | What it holds |
|---|---|
| **Updates Available** | Devices with an offered, provided or running update. A device you just updated stays here as "Updated" (or "Unconfirmed", or "Error" with **Install** for another attempt) until you check it again or the next full check runs |
| **Beta Updates** | Devices with a beta version on offer (only while **Offer beta firmware** is on, see [Beta firmware](#beta-firmware)) |
| **Attention** | Devices whose last check ended in an error, whose installation was not confirmed, that could not be reached, or that have no usable address (see below) |
| **Not Checked** | Devices that were never checked, and Gen1 devices that are skipped while **Enable Gen1 updates** is off |
| **Up-To-Date** | Devices without a newer stable firmware (also while a new version is on hold, see [Offer delay](#offer-delay)) |
| **Asleep** | Battery devices that did not answer the last check |
| **Ignored** | Devices skipped by automatic checks; they cannot be updated until you resume them |
| **Removed** | Devices that were once listed but are no longer in the Shelly integration (see below) |
| **Other Shellys** | Shellys that Home Assistant knows but the app cannot update (see below) – display only |

The **Filter** icon next to **Sort** opens a popover with one filter per section plus
**All**, **Unprotected** (devices without password protection – set it per device from
that list), **Scheduled** (approved installations waiting for their time), **Cloud on**,
**Access point on**, **Debug log on** and **On hold** (only while **Offer updates only after (days)**
is above 0), each with its count. A dot on the icon shows that a filter other than
**All** is active. The Firmware and Logs tabs have the same icon with their own filters.

| Status | Meaning |
|---|---|
| Up-To-Date | No newer stable firmware is offered (Gen2+: by the Shelly server; Gen1: neither stored nor by the Shelly server) |
| Available | An update exists, the firmware is provided at the next check |
| Ready | Firmware provided (from the firmware store or downloaded), waiting for your approval – "Install" next to the status opens the installation dialog |
| Scheduled | Installation approved for a later time; the time is shown under the version (see [Scheduled installation](#scheduled-installation)) |
| Updated | The installation was confirmed; shown until the next check of the device. If the backup before the update shows changed settings or a script that no longer runs, a line below the firmware says so (see [Comparison after an update](#backups)) |
| Queued / step of the installation | Installation waiting for a free slot / running: the label names the step – **Backup …**, **Restarting …**, **Waiting for device …**, **Installing** (with the percentage for Gen2+), **Verifying …** – blue while it runs, yellow while a step takes longer than usual. The tooltip (phones: the device details) names the step, how long it has been running, when the app gives up and the Wi-Fi signal |
| Unconfirmed | The installation ran, but the target version was not confirmed (see the details); stays in **Updates Available** until the next check of the device |
| Error | Check, download or installation failed; after a failed installation – or with a stored version while the Shelly server could not be asked – the device stays in **Updates Available** and can be installed (again) |
| Asleep | Battery device that did not answer the last check – it sleeps; the IP is dimmed. Wake it up and check it again |
| Not checked | The device was never checked, or it is a Gen1 device skipped while Gen1 updates are off |
| Removed | Device is no longer in the Home Assistant Shelly integration; see **Removed** above |

While a check runs, the status shows what it does: **Waiting**, **Checking …**,
**Downloading …** and **Shelly server …** while the app waits for the answer of the Shelly
update server. The last one turns yellow after 5 seconds: the server answers slowly or
not at all, and the check goes on until it answers or fails. **Restarting …** shows after
**Restart** until the device answers again (at most two minutes); a device that does not
answer in time shows the error.

**Removed devices** keep their data and stored password; both are matched by MAC
address, also when Home Assistant gives the device a new ID. Set the device up in Home
Assistant again and the mark disappears with the next check. A full check does not check
removed devices for updates: it only tests whether they still answer at their last
address and logs a warning if they do, an error if not. While one is still reachable,
its actions and a single check work as usual. **Remove from list** in the row menu
deletes the device and its stored password from the app after a confirmation; it also
leaves every schedule (a schedule without devices is switched off).

**Not reachable:** the device details of a device that did not answer its last check
explain the common causes (battery device asleep, another network than Home Assistant,
an outdated address in Home Assistant). **Try again** asks Home Assistant for the
address and tests the port and the answer of the device, with the times. It changes
nothing and starts no check; the status stays until the next check.

**Details for a bug ticket** (bottom left in the device details) shows a text for a bug
report or a forum question: app and Home Assistant version, model, firmware, status,
the options that matter and the last log lines of the device. Names are replaced by
"[Name 1]", IPv4 addresses shortened to their first two numbers, IPv6 addresses by
"[IPv6]", MAC addresses and device IDs to their last four characters; passwords are never
part of it. **Copy text** only
fills the clipboard – nothing is sent.

**No usable address:** if the address of a device cannot be used – the host name does
not resolve, the IP address is outside the private networks, or the device has no
address – the tile shows **No usable address** in the IP column (on a phone only this
text); on the desktop a line below the name and the tooltip give the reason. Fix the
address in the Shelly integration (see [Troubleshooting](#troubleshooting)).

**Other Shellys** lists Shelly devices that Home Assistant knows but this app cannot
update (BLU, Zigbee, Matter, MQTT): name, kind, model, firmware and **Where to
update**. The section has no actions and is shown only with the filter **All** or
**Other Shellys**. The app recognises these devices by the manufacturer text in Home
Assistant, so a device may be missing. A listed Shelly that Home Assistant also knows
over Bluetooth is not repeated here.

**Shellys not listed:** when Home Assistant knows Shellys of the Shelly integration that
the app does not list, a line above the list says "N of M Shellys listed · Why are K
missing?". It opens a list with the reason and what to do: the device or its entry is
**Disabled in Home Assistant**, Home Assistant **Found** it **but** it is **not set
up**, or the discovery was **Ignored in Home Assistant**. Each full check updates the
list. A Shelly that Home Assistant does not know cannot be listed.

**Host name:** the column exists only while **Resolve host names** is on (Settings tab,
Advanced). It shows the host name set in the Shelly integration, else the name the
device announces in your network (e.g. `shellyplus1pm-a0dd6c29c3e0.local`), and "–"
while the name does not resolve.

A name you give the Shelly does not change the announced name. It stays the device ID
followed by `.local` and shows only while it resolves. The column is for display only:
the app always reaches the device at its address in Home Assistant. The Columns chooser
can hide it; on a phone it is a row in the details.

**NTP server:** a click on the entry in the column (desktop) opens the dialog to change it.

**Action icons** show and change power-on action, eco mode, password protection,
access point, Bluetooth, cloud, actions and schedules and the websocket debug log, and
restart the device. The popup of an icon starts with the current state; "?" means the
value is unknown (also the power-on action while the outputs of a device have never been
read). Every change is read back from the device; a value the device
accepted but did not apply is reported as a warning and logged as failed. If the Shelly
reports that a change needs a restart, the success message offers **Restart now**. The
power-on action (lightning) is green while every output restores its last state or
follows its input (**Restore last state**, **Match input**; Gen1 **Restore last mode**,
**Switch**), yellow with any other value. The lock is
green if the device is protected and its password is stored in the app, yellow if the
password is not stored here.

- **Access point** (Gen2+): one form for the access point of the Shelly – on or off,
  the network name (empty = the default name, the device ID; up to 29 characters) and
  the protection: a password, or open after you accept the risk (anyone in Wi-Fi range
  could then change the device without a device password). A running protected access
  point keeps its password unless you set a new one, because the Shelly never reveals it.
  The password needs 8 to 63 characters, because the Shelly refuses any other length:
  letters without umlauts or accents, digits and common symbols (printable ASCII, as
  WPA2 demands), because some phones and computers cannot connect otherwise.
  The password goes to the Shelly only; the app does not store or log it. The Shelly
  decides whether it accepts a password or an open access point; its refusal is shown in
  the form. **Range extender** is offered if the device has it. Only the access point is
  changed; the Wi-Fi connection to your network stays as it is. Gen1 devices cannot
  change it here: turning on their access point turns off their Wi-Fi client mode.
- **Cloud** (cloud icon): yellow while the Shelly cloud is on, with the connection in
  the tooltip. With the cloud off, the Shelly app and other Shelly cloud services no
  longer reach the device from outside your network; Home Assistant and this app keep
  working locally. Only the cloud switch is changed.
- **Bluetooth proxy:** if Home Assistant uses the Shelly as Bluetooth proxy (its script
  `aioshelly_ble_integration` runs on the device), the Bluetooth popup says so. Home
  Assistant turns Bluetooth on again after you turn it off here. To switch it off for
  good, first set **Bluetooth scanner mode** to **Disabled** in the options of the
  device in the Shelly integration.
- **Websocket debug log** (Gen2+, bug icon): turns on the debug log that the Shelly
  streams over a websocket; the Diagnostics page of the Shelly web UI (linked in the
  popup) shows it live. Turn it off afterwards – the log may contain network and
  configuration details and streaming uses device resources. Below firmware 1.0.0 the
  stream is not protected by the device password. Gen1 devices have no such log.

**Row menu** ⋮ per device: Check for updates, Install update (for a scheduled
installation: **Install now** and **Cancel scheduled installation**), Offer update now (only
while an update is on hold), Downgrade, Device details (status, firmware with build date
and previous version, model, generation, firmware ID, NTP server and history), Comparison after
update (until the next check of the device), Reload device info, Open web UI, the device settings, Ignore / Resume device and Restart. On touch
devices a tap on a disabled button shows the reason.

**Battery devices** (e.g. H&T, door/window sensors) sleep between wake-ups and answer
only while they are awake. The app recognises them from Home Assistant (the battery
sensor that the Shelly integration creates for the device) or from the device itself
when a check or Reload Device Info reaches them awake (Gen2+: wake-up period, Gen1:
sleep mode) and shows a battery icon after the name. What the device reports wins; the
tooltip says which source applied (**Detected from Home Assistant** or **Detected from
the device**). To check one, wake it up (e.g. press its button) and check it right away.

### How a check works

A check (all devices, a single device or the schedule) works in this order:

1. Every device is asked in your network for its firmware ID and installed version.
2. **Gen2+ devices: the Shelly update server decides.** It is asked with the firmware ID
   and the installed version – the way a Shelly asks itself; only these two values are
   sent. The server answers with the next allowed step for this version. Devices with
   the same firmware ID and version share one request.
   - If the firmware store already holds the file Shelly's download address names
     (same SHA-256), it is used; otherwise the file is downloaded once and stored. A
     download must match that SHA-256. An uploaded file of exactly the offered version
     is used instead, as your own choice (see Upload firmware below).
   - **Intermediate versions:** some old firmware versions must be updated to an
     intermediate version first (e.g. Plus1 1.0.7 → 1.3.3 → 1.7.x). Such an offer is
     marked as intermediate version. After its installation is confirmed, the device is
     checked again at once and the next step is provided – it is installed only after
     you approve it.
   - **Server not reachable:** the device shows the error. If a newer version is
     stored (also an imported file), the error details say so: you can install it manually (checkbox and
     **Install** stay available), but at your own risk – without the server's answer it
     cannot be verified whether an intermediate version is needed first. The dialog asks
     you to accept this risk.
3. **Gen1 devices: the firmware store comes first.** If the store holds a newer stable
   version that a check downloaded from Shelly, the **next higher** one is provided
   (step by step through the stored versions); the Shelly list is only asked for
   devices without one – one request for all Gen1 types. Imported files are not
   provided this way: only while the Shelly list cannot be asked, for a manual
   installation at your own risk like on Gen2+.

Every check that asks the Shelly update server also makes one reference request with
fixed values (Plus1 1.0.7, which must be offered 1.3.3 – no data of your devices). If
the server answers differently, the check reports a warning: the server no longer
evaluates the installed version, so required intermediate versions cannot be detected.

The status details, the installation dialog and the log name the source of an update:
Shelly update server, firmware store, an uploaded or an imported file.

The changelog of a provided version is requested once from the Shelly documentation
and kept with the stored firmware; it is not requested again afterwards.

### Offer delay

With **Offer updates only after (days)** above 0, a new version is offered only once it
is that many days old. The age counts from its build date: Gen2+ from the build ID that
the Shelly update server names, Gen1 from the version text.

Until then the device stays **Up-To-Date**, nothing is downloaded and no notification
counts it. Below the version a clock line shows the held version and the date it is
offered from; the filter **On hold** lists these devices.

The build date can lie a few days before Shelly starts to offer a version, so the
hold may end earlier than the days you set. Row menu ⋮ → **Offer update now** lifts the hold for this version on this device and
checks it at once; the update is installed only after your approval. Intermediate
versions are held back the same way. Uploaded files and versions without a build date
are offered at once.

### Keep installed versions

Shelly only offers the current version for download. With **Keep installed versions**
on (default), a check also stores the version a Gen2+ device runs while it is up to
date – only then does Shelly still offer that file. After the next update this file is
the device's previous version (see [Downgrade](#downgrade-gen2)).

A device with an open update has no source for its installed version any more; the
installation dialog then says that it cannot be downgraded to it after the update. The line below the option in
the Settings tab shows how many installed versions are stored, how many the next check
downloads, and the free disk space.

### Installing updates

1. Tick the devices under "Updates Available" and click **Review & install** (or use
   "Install update" in the row menu, or **Install** behind a device in the Firmware tab).
2. The dialog shows installed and target version, build date, size, SHA-256, source and
   the changelog, folded – the chevron opens it (security entries and entries for this
   model are highlighted). The
   section **Before installing** checks two points: **Previous version** (Gen2+: is the
   installed version stored, so a downgrade stays possible?) and **Wi-Fi signal** (read
   from the device when the dialog opens; −80 dBm and below is weak, the download may
   fail). Devices on the cable have no signal line; "unknown" means the device did not
   report a value. For several devices only the
   findings are listed, one line per check; a check that concerns more than one device
   shows their number and opens to list them.
3. **Approve installation.** The Shelly downloads the firmware from the app and restarts.

Right before the update command the app checks again that the same device runs the
checked firmware. A Shelly on weak Wi-Fi may not answer for a moment: the app then tries
again, up to 5 times within 60 seconds, and sends the update command only once (again
only if it surely never reached the device). If the Shelly answers again, its running
time tells a lost connection (the installation goes on; the log entry of the result
names how often the connection was lost) from a restart of its own (the installation
stops, nothing was installed). A refusal of the device (password, another device,
another firmware) is never tried again.

Gen2+ devices report their progress over the device connection (the status label shows
the percentage). An error reported by the Shelly ends the installation at once. Without
a download request after 2 minutes, or without new progress for 60 seconds (120 seconds
when the Wi-Fi signal was weak at the start), the installation fails. After 100 % the
Shelly must restart within 30 seconds (the label shows "Restarting …") and report exactly
the target version within 90 seconds after the restart ("Verifying …"); the old version,
another version or no answer is an error.

Devices without this connection (Gen1, or when it is not available) are checked every
10 seconds until they report exactly the target version (at most 5 minutes). If that
does not happen, the status becomes "Unconfirmed" with a diagnosis – see
[Troubleshooting](#troubleshooting). A failed installation can be retried from
**Updates Available**. The dialog of a retry shows the reason of the last attempt and offers
**Restart the device first** (ticked): the app checks that the same device still runs
the checked firmware, restarts the Shelly, waits until it has gone down and answers again
(at most two minutes) and then starts the installation (**Restart & install**). Scheduled checks create a
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

### Scheduled installation

In the installation dialog, the section **Start** offers **Now** or **Install at** with a
date and time – once, at most 7 days ahead, in the time zone of Home Assistant. **Approve
& schedule** stores your approval for exactly this file (SHA-256). The device shows
"Scheduled" with the time, and the tab **Schedules** lists it.

At that time the app installs without asking again, but only if the device still runs the
version shown in the dialog and the file is still provided. If a full check, a schedule
or work on the device itself is running then, the installation waits up to 30 minutes and
stays "Scheduled". Otherwise nothing is installed and the scheduled installation lapses:

- a check found another version or file, or beta firmware was switched off;
- the device was ignored or removed from Home Assistant;
- a check could not reach the Shelly update server, so the same file is now offered
  without its confirmation (unless you scheduled it with the risk tick for such a file);
- the running work took longer than 30 minutes;
- installations are disabled (download base URL);
- the app was not running at that time. It never installs later, also not right after a
  restart, so an installation does not start at an unexpected time.

The log names the reason. Home Assistant shows one notification for every scheduled
installation: installed, failed, lapsed or missed. Cancel it in the row menu or the tab
**Schedules**; **Install now** in the row menu opens the dialog again and replaces the
scheduled time. While an installation is scheduled, **Downgrade** is blocked for the
device; cancel the scheduled installation first.

### Beta firmware

With **Offer beta firmware** on, a check also takes the beta version the Shelly update
server names for a Gen2+ device. A device with a beta on offer is listed under **Beta
Updates**; the version has a violet "Beta" tag. The installation dialog warns that a beta
is a pre-release and needs the tick "I understand that beta firmware is a pre-release and
may contain bugs." – also for a scheduled installation. The offer delay does not apply to
betas. Shelly offers betas only now and then and not for every model; the app log names
for every checked device whether a beta was offered.

Returning to a stable version is a downgrade, so it needs that version in the firmware
store (see [Keep installed versions](#keep-installed-versions)). When you switch the
option off, offered betas are withdrawn at the next start of the app (saving the
settings restarts it); a device that already runs a beta is not changed. Gen1 devices get
no beta versions.

### Downgrade (Gen2+)

**Downgrade at your own risk.** Shelly does not officially support downgrades. A
downgrade can brick the Shelly; in the worst case the device is permanently broken and
cannot be repaired. Jumping back to a very old version will almost certainly brick it.

Downgrades are off until **Allow downgrades** is switched on in the Settings tab; ticking
it shows this warning, and you confirm it with **I understand the risk**. While the option
is off, the row menu has no **Downgrade** entry, the Firmware tab shows no downgrade icon
and the app refuses downgrades.

Row menu ⋮ → **Downgrade** lists all stored versions that are older than the installed
one (the entry is greyed out while no older version is stored). Choose a version, check
the installation dialog, then tick **I understand the risk** and choose **Downgrade**.
In the Firmware tab, the downgrade icon of an older version lists the devices of this
type with a newer version installed; choose one and confirm a second time. The
downgrade runs like a normal installation and is logged as such; meanwhile the device
stays in its group and shows the target version with the tag "Downgrade". It is
available for up-to-date and ignored devices too, but never for Gen1, and not while an
installation is scheduled for the device.

Shelly only offers the current version for download, so older versions are available
only if the app downloaded them earlier or you uploaded them (see [Firmware](#firmware)).
A device may refuse a downgrade, and settings of newer firmware may be lost.

### Firmware

- All downloaded and uploaded firmware files, grouped by device type, with size, date,
  SHA-256 and the devices they are provided for. The top shows the number of files, their
  total size and the free disk space. Filters: Provided (to at least one device),
  Installed (a device runs this version now – its previous version after the next update),
  Uploaded, Imported (not confirmed by the Shelly update server yet), Intermediate, Gen1
  and Unused (no listed device has this firmware ID; no file
  counts as unused while the ID of a device is unknown, e.g. a sleeping battery device).
  Search and sort work as in the Devices tab; versions inside a card stay newest first.
- **Files on disk:** at start the app scans the firmware folder. A file that is on the
  disk but not listed (for example after a damaged state file) is added again if its
  SHA-256 equals its file name and a device or the manifest in the file names its
  firmware ID and version; the name alone is not enough. A Gen2+ file taken over this
  way is provided only when the Shelly update server offers exactly this SHA-256 – never
  as stored fallback or for a downgrade. The log then shows a warning that files were
  read from the disk, because they could have been changed. Files that cannot be
  matched are listed in the card **Unknown Files** (filter **All** only) with name and
  size; delete them there with the bin icon (one confirmation). Nothing is deleted
  automatically.
- **Install** behind a device name opens the usual installation dialog for this device
  and this firmware (same conditions as "Install update" in the Devices tab).
- The **changelog icon** of a version shows the changelog stored with this firmware.
  It is greyed out while none is stored: the changelog is stored when a check provides
  the file to a device (see [How a check works](#how-a-check-works)).
- Firmware is **never deleted automatically**. Delete files you no longer need with the
  bin icon (two confirmations). A file that is provided to devices can be deleted too:
  the dialog names them, they lose the provided update and show "Available" until they
  are checked again. For a version marked "Installed" the dialog warns that these
  devices cannot return to it after their next update without it. A file that is being installed cannot be deleted, nor any file
  during **Check all** and the files of the checked device's type during a single check. To skip a stored version (Gen1: the store comes first),
  delete it.
- **Upload firmware** adds a version you have from another source:
  1. Choose the file (`.zip`, `.bin` or `.gbl`, max. 64 MB).
  2. Device type and version are filled in from the file where possible – check them.
  3. Optionally enter the expected SHA-256; the upload is refused if it does not match.

  Uploaded files are marked "Uploaded". A file that a check provided as an
  intermediate version is marked "Intermediate". An uploaded version is used when the Shelly update server offers
  exactly this version, or for a manual installation while the server cannot be
  reached (see [How a check works](#how-a-check-works)); an older one allows a
  downgrade. The app cannot verify where an uploaded file comes
  from, and the version is the one you confirmed – only use files you trust.

### Schedules

A schedule does one action on several Shellys at a fixed time: **Restart**, **Cloud on**,
**Cloud off**, **Bluetooth on**, **Bluetooth off** or **Backup** (see [Backups](#backups)). **Add schedule** asks for a name, the
action, the time (time zone of Home Assistant) and the repeat: **Daily**, **On weekdays**
or **Every** n days. Devices are **All Shellys** (new ones are included automatically),
**Areas** of Home Assistant (the devices in these areas at the time of each run, new ones
included; also **Without area**; offered once a device is in an area) or a list you tick. A restart runs device by device with a pause (default 3 seconds, 0 = all
at once), so the network and Home Assistant are not hit at once.

A run leaves out ignored and removed devices, battery devices (they sleep) and devices
without the switch (Gen1 has no Bluetooth switch); the dialog and the log name them. While
a check, an installation or another schedule runs, a due run waits up to 30 minutes. A run
that waited longer, or whose time passed while the app was not running, is skipped and
logged as "missed"; it is not made up for after a restart. A run cut off by a stop of the
app is logged as interrupted. **Run now** (play icon) starts a schedule at once after a
confirmation. A device removed with **Remove from list** leaves every schedule; a schedule
left without devices is switched off and shows "No device" until you choose devices again.

The card lists each schedule with its next run and the last result ("2 of 3 restarted · 1
left out" counts all chosen devices); the checkbox switches
a schedule off and on. The card **Scheduled Installations** lists the approved installations
waiting for their time (see [Scheduled installation](#scheduled-installation)). Every run is
written to the log; Home Assistant shows a notification only when a device was not
reachable or refused the change. While a schedule runs, **Check now** and installations
are refused; an automatic check that is due then waits up to 30 minutes for the end of
the run, otherwise it is skipped and logged.

If Home Assistant uses a Shelly as Bluetooth proxy, it turns Bluetooth on again after
**Bluetooth off**; the dialog warns about it.

### Backups

A backup stores the settings of one Shelly in the app: Gen2+ the configuration of all
components, the scripts with their code, the schedules and the webhooks; Gen1 the
settings and the actions. Passwords are never part of a backup – a Shelly does not give
out its Wi-Fi, device or MQTT password. Scripts, webhooks and actions are saved as they
are: a token or password written into a script or a URL is part of the backup
(encrypted). Nothing is changed on a device while it is backed up.

A backup is made

- by hand: **Back up now** in the **Backups** tab (all Shellys, areas or a selection), or for one
  device in its row there or in its **Backups** dialog (row menu and device details of the
  **Devices** tab). **Back up now** for several devices writes one log entry for the run;
  it opens to the result of each device;
- before every update and downgrade while **Back up before every installation** is on –
  if this backup fails, nothing is installed and the reason is shown;
- by a schedule with the action **Backup**. A run stores a backup only for devices whose
  settings changed since their newest backup ("3 stored · 17 unchanged · 1 asleep · 2 not
  backed up"). "Unchanged" means the settings are the same as in the newest backup, so no
  copy is stored again; battery devices are backed up when a run finds them awake;
- before a restore (reason **Before restore**), so a restore can be taken back.

A device that should answer and does not (or whose last check failed with an error) is an
error in the log, also within a run or a schedule; a battery device that sleeps is a
warning.

The tab lists every device with its newest backup; the chevron opens all its backups with
date, reason, firmware, contents and size. The card **No Backup** names the devices
without one and why (no backup yet, password not stored, asleep, not reachable). Per
device the newest **Keep backups per device** backups stay, older ones are removed
automatically. The backup you restore stays until the next backup of the device, even
if it is the oldest one. The pin marks a backup as **Kept**: it is never removed
automatically and does not count. The bin deletes a backup after a confirmation. A device removed from Home
Assistant keeps its backups but gets no new ones.

**Restore** reads the device and compares it with the backup. You choose the parts
(device settings, scripts, schedules, webhooks; Gen1: settings, actions); **Show
differences** lists what would change, with the names the Shelly uses. A chosen part is
made equal to the backup: what exists only on the device (a newer script, schedule or
webhook) is removed. **Wi-Fi and network settings** are a part of their own, not ticked
by default, and need an extra confirmation – wrong network settings can make a device
unreachable. If the backup names another Wi-Fi (SSID) than the device uses now, the
warning says so: the Wi-Fi password is not part of a backup and stays as it is on the
device, so the device can only join the Wi-Fi of the backup with the password it has
stored now. A backup can only be restored to the device it was made on (MAC address and
model), and no backup is stored while another Shelly answers at the device's address; a different firmware version is allowed, settings it does not know are skipped
and named. Afterwards the app reads the device again and shows per part what was applied;
the device restarts if a setting needs it.

The download icon saves one backup as a file (`.sfmb`). The app generates a password,
shows it once and seals the file with it – without the password the file cannot be
imported. **Import backup** in the **Backups** dialog of a device takes such a file and
its password; the file must come from this device. An imported backup is marked
**Imported** and **Kept**; restoring it is a separate step. All backups are also part of
the store export (see [Moving the firmware store](#moving-the-firmware-store)).

Stored backups are encrypted with a key of this app. Key and backups are part of every
Home Assistant backup of this app, which Home Assistant protects with its own encryption.

**Comparison after an update:** once an update with a backup **Before update** is
confirmed, the app reads the device again (about 15 seconds later, so its scripts have
started) and compares it with that backup. While the device shows "Updated", a line
below its firmware names the changed settings or the scripts that ran before and do not
run now; **Comparison after update** in the row menu shows the details: changed values,
settings that are new with the firmware (they have Shelly's default value, collapsed)
and the scripts. **Seen** in that dialog removes the line; the comparison stays in the
row menu until the next check of the device (the check right after an intermediate
version keeps it). Counters that change with every update (`sys.cfg_rev`) are not
compared. Nothing is changed on the device; the result is logged with the names of the
first changed settings (without their values, which may hold e.g. a server address) and the
stopped scripts. Downgrades and
installations without a backup **Before update** get no comparison.

### Logs

The activity log records checks, downloads, uploads, installations and downgrades,
deleted firmware, setting and password changes on devices, store exports and imports,
firmware files read back from the disk, devices removed from the list, held-back updates
offered now, scheduled installations, schedule runs (with the devices left out), backups
(stored, failed, restored, deleted, downloaded, imported, compared after an update),
connection tests of the download server and
changes of schedules and app settings – each with time, user and result (success, warning, error). A download names
the user of the check that fetched it; scheduled checks and their downloads name
"Schedule". A full check also counts the ignored devices ("2 ignored") and names them
in its details in the normal text colour – they are not contacted and never make the
entry a warning or error. Removed devices are counted too ("1 removed"); each one is a
warning if it still answers at its last address and an error if not. A device that does not
answer is an error; a battery device that is asleep is a warning. Filters and a device search help to find entries; **Hide successful entries** leaves only warnings and errors. Passwords never appear in the log.

The tab updates itself when a new entry is written. A red dot on the tab icon marks error
entries written since you last opened it in this browser; when you open the tab, the
entries that are new since then are marked by a bar on the left.

The bin icon deletes a single entry, **Delete all logs** the whole log (each after a
confirmation). After clearing, one entry remains that names who cleared the log.

### Settings

First the card **Device Passwords**, then all options from [Configuration](#configuration) in the cards **Checks**, **Download
Server** (with **Test connection**, see [Download base URL](#download-base-url)), **Firmware & Storage**, **Backups**, **Logs**, **Advanced** and **Display** (**Base colour**). **Save settings** stands above and below the
cards and saves all of them. If you leave the tab or choose another language with unsaved
changes, the app asks first: **Discard** drops them, **Save** saves them before it goes on. The
i-icon next to a setting shows its help (on touch devices with a tap). While you type, a
hint warns if the download base URL is a host name or not in your local network; while
installations are disabled because of it, a notice above the tabs explains why. The card
**Firmware & Storage** holds the export and import of the firmware store (see [Moving the
firmware store](#moving-the-firmware-store)), the card **Device Passwords** sets
passwords on several devices at once (see [Device passwords](#device-passwords)). The
card **Display** chooses the language of this page: **Same as Home Assistant** (default;
English where no translation exists), English, Deutsch, Nederlands, Français, Italiano,
Español, Polski or Português. The translations other than English are machine translated. It applies at once (the page
reloads on the Settings tab), only in this browser, and needs no save. **Base colour** below it shows the chosen colour at once and is
kept with **Save settings** for all browsers, without a restart of the app (see
[Configuration](#configuration)). The status messages of the devices and the errors the app
reports are translated as well; the activity log, texts the Shelly itself sends and this
documentation stay English.
The last line links to the app page in Home Assistant, where **Changelog** next to the
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

**1PM Mini Gen3/Gen4 and firmware 2.2.0:** 2.2.0 changed the device ID of these models,
2.2.1 restores it. A password set or changed under 2.2.0 stays bound to the 2.2.0 ID.
According to Shelly, its web UI then refuses the password. You can see this in
`http://<IP>/shelly`: `auth_domain` differs from `id`.

In that case **Store existing password** is meant to accept the correct password anyway.
Then use **Change password** once; the same password is fine. The app sets it with the
restored ID. The Shelly is then meant to bind the password to that ID again.

**Failed sign-ins:** the app sends a stored password once per request. Once the Shelly
refused it, a check, a backup or an installation sends nothing more with it. A Gen2+
Shelly that refuses the change or removal of its protection is expected to have changed
nothing, so the app stops there too. Each of these actions counts one failed attempt at
most.

Firmware 2.0 and newer delays sign-ins after repeated failed attempts. In that case the
app is meant to show "The Shelly temporarily blocks sign-ins" as an error. It is meant to
wait only for the short block of about 2 seconds that many sign-ins at once can cause,
without sending a password.

**Several devices at once:** Settings tab → **Device Passwords** → **Device passwords**. Choose the action –
**Store the existing device password in this app** or **Set password** (enables the
protection of unprotected devices, changes it on protected devices whose password is
stored) – tick the devices ("All devices that fit") and enter one password for all of
them. Devices the action does not fit are folded at the end under **n not possible**,
each with the reason (e.g. protected
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
store in the Settings tab → **Firmware & Storage** → **Export store**. The export also
holds the [backups](#backups) of your device settings, encrypted with the export
password; an import adds the ones that are not stored yet (marked **Imported**):

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

On the other instance: **Import store**, choose the file and enter the export
password. The file is checked completely – seal, every file – before anything is
stored; then the result shows how many files are new, already stored or in conflict
(each group can be expanded; conflicts are expanded). Tick "I created this export
myself or I trust its source" – only then **Import** stores the new files:

- Each file is stored as "imported", whatever the export says about its source: the
  password only proves that the file was not changed since its export, not who made
  it. It keeps its original date, changelog and intermediate marker. Gen1 files are
  imported even if Gen1 updates are off.
- An imported Gen2+ file counts as downloaded from Shelly once the Shelly update server
  offers exactly this file (same SHA-256); until then it is never provided instead of
  Shelly's file. Only import exports you made yourself or got from someone you trust.
- Already stored (same SHA-256): not stored twice; if firmware ID and version match,
  missing data is added. The source of the stored file stays.
- A changelog that came with an import is replaced by the one from the Shelly
  documentation once Shelly confirms the file.
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
| download_base_url: the IP address is not in a private network | Enter the LAN IP of Home Assistant; other addresses are refused (see [Download base URL](#download-base-url)) |
| Installations are disabled: the download base URL is not a valid address | The saved address contains characters or a host name that are not allowed – correct it in the Settings tab (see [Download base URL](#download-base-url)) |
| Installations are disabled: the IP address of the download base URL is not in a private network | The saved address was accepted by an older version of the app – correct it in the Settings tab; installations stay disabled until then |
| Download rejected: request came from 172.30.x.x | Docker hides the device address – turn off "Serve downloads only to known Shellys" |
| Not reachable (connection test) – No connection to …: timeout after 8 s (Shelly code -104) | The Shelly gets no answer from the download base URL: check the IP address, the **Host port** of the app (Settings tab) and firewalls or separate networks (VLAN, guest Wi-Fi) between the Shelly and Home Assistant |
| Not reachable (connection test) – No connection to …: connection error (Shelly code -114 / …) | The Shelly could not open a connection, usually because nothing listens on that port: check that the port of the address is the **Host port** of the app (Settings tab) and the IP address. The log entry shows the device's own answer when you open it |
| host_port: port … is already in use on … – choose another port | Another program on the Home Assistant host listens on that port; the app would not start with it. Choose another port |
| The app does not come back after a new host port | Set the port back in the **Network** section of the app page (tab **Configuration**) and start the app |
| Another web server answered at … (connection test) | The address and port lead to another server, not to the download server of this app – check the IP address and the port |
| The request reached the download server from …, not from the device address (connection test) | Network address translation (NAT, e.g. Docker or a router between networks) hides the device address – turn off **Serve downloads only to known Shellys** or route without NAT |
| No Shelly of Gen 2 or newer is known yet – run a check first (connection test) | Only Gen2+ Shellys can run the test. Run **Check now** first; Gen1 devices cannot test the connection |
| Device not reachable (connection test) | The chosen test device did not answer Home Assistant. Choose another device or **Automatic** |
| Not reachable (device details) | The device did not answer its last check. **Try again** shows whether Home Assistant still knows its address, whether the port is open and whether the device answers; check the device, its network and its address in the Shelly integration |
| The comparison could not be made (Comparison after update) | The device could not be read again after the update or its backup **Before update** is gone. The update itself is not affected; check the device's settings in its web interface if in doubt |
| The Shelly downloaded the firmware but still reports … | The device did not install it – check the log in the Shelly web UI |
| The Shelly reports an update error … It downloaded the firmware but rejected it | The installed firmware may be too old for a direct update to this version: check the device again – the Shelly update server names the required intermediate version (or install one via the web interface of the Shelly or an upload) |
| Check warning: the Shelly update server no longer answers with the next step | Shelly changed its update server: required intermediate versions cannot be detected. Prefer the web interface of the Shelly for devices on very old firmware |
| Error … Stored version X is ready and can be installed manually | The Shelly server could not be asked. Check again later, or install the stored version at your own risk |
| Download does not match the SHA-256 named by the manufacturer's URL | The download was damaged or altered and was not stored – check again |
| Many "Download rejected" lines in the app log | Only the first rejected request per address and 10 minutes is logged; the others are counted in one line |
| Connection to the Shelly lost before the update command – no answer in n attempts within n s. Nothing was installed. | The Shelly did not answer the check right before the update command, usually because of weak Wi-Fi. Improve the reception (access point, antenna position) and try the installation again |
| Connection to the Shelly lost before the update command – nothing was installed | The Shelly asked for its password, or briefly blocked sign-ins, over the WebSocket, and the connection ended before the app could send the update command again. Nothing reached the device as a command. Try the installation again |
| The Shelly restarted unexpectedly before the update command (running for n s). Nothing was installed. | The Shelly restarted on its own while the app was waiting for it (power supply, firmware fault, Wi-Fi loss). Check the device and try the installation again |
| The password contains a character the Shelly cannot use: … (access point form) | Use only letters without umlauts or accents, digits and common symbols (printable ASCII) |
| The device did not answer within 2 minutes after its restart – nothing was installed | **Restart the device first** restarted the Shelly, but it did not come back in time. Check that it is online, then try the installation again |
| No update is held back for this device | **Offer update now** found no held version any more (e.g. the device was checked meanwhile). Reload the page |
| Weak Wi-Fi signal (−80 dBm or below) in the installation dialog | The Shelly downloads the firmware over Wi-Fi and the download may fail. Improve the reception (access point, antenna position) or install anyway and retry if it fails |
| The Shelly reports X instead of the target version Y | Check the device; run "Check for updates" again |
| Certificate of … is not publicly trusted | See [Certificate pinning](#certificate-pinning) |
| The Shelly requires a password | Store the device password (see [Device passwords](#device-passwords)) |
| The Shelly rejected the password | The password entered or stored in this app is not the current one of the device, e.g. it was changed elsewhere. Store the current password with **Store existing password**. For a 1PM Mini Gen3/Gen4 whose password was set under firmware 2.2.0, see [Device passwords](#device-passwords) |
| Stored device password belongs to another device ID – enter it again | The Shelly asks for its password under another device ID than the one the stored password was tested with. Either another Shelly answers at the address, or the password was set again under another ID. That can happen on a 1PM Mini Gen3/Gen4 with firmware 2.2.0 and 2.2.1. Check the address, then use **Store existing password** again; for a 1PM Mini Gen3/Gen4, then follow [Device passwords](#device-passwords) |
| The Shelly temporarily blocks sign-ins (too many attempts) – wait a few minutes and try again | Firmware 2.0 and newer counts failed password attempts over 10 minutes. After more than 10 it answers sign-ins only with a delay between attempts (10 s, rising to 5 minutes). Every client counts: this app, the Shelly integration of Home Assistant, a browser or a script. Make sure each one uses the current device password, wait a few minutes and check again. A restart of the Shelly or the next accepted sign-in resets the count |
| The Shelly rejected the stored current password – nothing was changed | The password stored in this app no longer works, e.g. because it was changed elsewhere. The device is meant to keep its password. Store the current password with **Store existing password**, then change it again (see [Device passwords](#device-passwords)) |
| The Shelly requires HTTPS (Enhanced Security) – not supported by this app yet | The Shelly answers plain HTTP only with a redirect to HTTPS. According to Shelly, devices shipped with firmware 2.0 or later always do this. Older ones do it after Enhanced Security was switched on and an HTTPS certificate was installed. The app never follows the redirect and cannot check, update or back up this Shelly yet. Until then, update it in its own web interface (`https://<IP>`) |
| The Shelly answered with a redirect (HTTP …) – redirects are not followed | Something at the device address sends the request elsewhere, e.g. another web server or a proxy. The app never follows a redirect: check the address of the device in the Shelly integration |
| Device reports: … (code n) | The Shelly refused the request; the text and the code come from the device itself (English). Correct the entry the text names, e.g. an access point password of 8 to 63 characters; the code helps when you report the problem |
| A Shelly is missing from the list | Open the line "N of M Shellys listed · Why are K missing?" above the list: it names the reason (disabled, not set up or ignored in Home Assistant). Shellys behind a gateway and unsupported kinds are not listed as devices (see the [FAQ](#faq)) |
| No usable address (tile in the Devices tab) | The host name does not resolve, the IP address is outside the private networks or the device has no address. Set an address in the Shelly integration (IP address or a host name that Home Assistant resolves) |
| … files read from the disk – they could have been changed (Logs tab, warning) | The firmware list was rebuilt from files in the firmware folder after the state was lost. Taken-over Gen2+ files are used only when the Shelly update server offers the same SHA-256. Delete files you do not trust in the Firmware tab; unknown files are listed there and never deleted automatically |
| Session expired – reload the page | The login of Home Assistant ran out while the page was in the background. The page retries silently for about 30 seconds; if the message stays, reload the page |
| Device address … is not in a private network | Only devices in a private network are contacted (see [Download base URL](#download-base-url)). Give the Shelly an address there |
| The app stopped and did not start again | Turn on **Watchdog** and **Start on boot** on the app page. With the Watchdog on, Home Assistant restarts the app after it stopped unexpectedly |
| The app is shown as running but does not respond | The container health check (a TCP connection to port 8098 every 30 seconds) is meant to make Home Assistant restart the app after three failures if the Watchdog is on. Otherwise restart the app by hand and report the lines of the app log if it happens again |
| Beta version withdrawn – beta firmware is switched off in the settings (Logs tab) | **Offer beta firmware** was switched off; the offered beta is no longer offered. Check the device to get the stable offer |
| Shelly firmware: scheduled installation – … was not started (notification) | The reason follows in the text: the version or file changed, the device is ignored or was removed from Home Assistant, the update server could not confirm the file, a check or schedule ran longer than 30 minutes, or installations are disabled. Approve the installation again |
| Shelly firmware: scheduled installation – … was missed (notification) | The app was not running at the scheduled time; it does not install later. Approve the installation again |
| Cancel the scheduled installation first (Downgrade) | An installation is scheduled for the device. Cancel it in the row menu or the Schedules tab, then downgrade |
| Damaged schedule … dropped from the state file (app log) | A schedule in the state file could not be read (e.g. edited by hand) and was left out; the app starts with the others. Add the schedule again |
| Not possible now – … (Run now) | A check, an installation or another schedule runs. Try again when it has finished |
| Schedule “…” is running – please try again afterwards | **Check now** and installations wait for the end of a schedule run. Try again when the run has finished (Logs tab) |
| Schedule run: … not reachable / refused (notification) | Some devices did not answer or refused the change. The log entry of the run names them; check those devices |
| Backup before the installation failed – nothing was installed: … | The device could not be backed up (not reachable, password missing). Solve the reason named after the colon and install again, or switch **Back up before every installation** off in the Settings tab |
| No Backup: Protected, and its password is not stored in this app | Store the device password in the row menu → **Device password**; then **Back up now** |
| Another device (Restore backup? / Import backup) | The backup was made on another device (MAC address or model differ). A backup can only be restored or imported for the device it was made on |
| Device not reachable (Restore backup?) | The comparison needs the device. Check that it is on and reachable, then open **Restore** again |
| … not applied (result of a restore) | The device refused a value or does not know the setting (other firmware). The result names each one; set it in the Shelly's own web interface if needed. The state before is stored as backup **Before restore** |
| The device did not answer again after the restore | Usually restored network settings that no longer fit. Check the device in your network; if it stays away, reset it at the device and set it up again |
| Wrong password, or the file was changed since the download | The password does not belong to this backup file, or the file is damaged. Use the password shown at the download of exactly this file |
| Damaged backup file: its time lies in the future (Import backup) | The file names a time more than one day after now: the clock of the system that made it was wrong, or the file was built by hand. It is not imported; a store import skips such a backup and names it in the app log |
| n backup file(s) cannot be read with the key of this app and are not listed | The key file of the app was lost, damaged or replaced (e.g. app data restored only in part), or the disk reported an error while a file was read. The files stay in the app data; with the matching key file (or once the disk reads them again) they are listed again with their marks. A key file of the wrong size is kept as `key.damaged-<time>` |
| The key of the backups cannot be read – backups are off until the app is restarted | The disk reported an error while the key file was read. Nothing was changed; restart the app. If it stays, check the storage of Home Assistant. While **Back up before every installation** is on, no installation starts until then – switch it off in the Settings tab to install without a backup |
| Another device answers at the address – no backup was stored | Another Shelly answers at the address the app knows for this device (e.g. after an address change in the router). Run **Check now** so the app reads the current addresses from Home Assistant, or correct the address in the Shelly integration |
| A backup run is in progress / A backup is being restored | Wait for the end (bar in the **Backups** tab), then try again |
| Storage error: The state file was unreadable – the backup of the previous save was loaded | The app's state file was damaged (e.g. by a power failure) and the copy of the save before was used. Check the devices again; the message disappears with the next restart |
| Storage error: The settings file was unreadable (kept as settings.damaged) – the defaults apply | The app's settings file was damaged and was set aside; the app runs with the default settings. Check every setting in the **Settings** tab and save; the message disappears with the next restart |
| Storage error: Invalid values in the settings file – the defaults apply for: … | The settings file holds a value the app does not accept (e.g. after a change by hand). The named settings use their defaults; check them in the **Settings** tab and save, then the message disappears |
| Storage error: The state file was unreadable and no backup could be loaded | The device list starts empty and is filled by the next check. The firmware files stay on disk; at the start the app reads back those whose content names firmware ID and version, the others are listed as **Unknown Files** in the Firmware tab (see [Firmware](#firmware)) |

An installation interrupted by a restart of the app is shown as "Unconfirmed" – check
the device and run "Check for updates".

## FAQ

**How is this different from the update entities of the Shelly integration?** The
update entities let each Shelly fetch its firmware from Shelly's servers. This app does
it differently:

- Home Assistant downloads the firmware, the Shellys need no internet access.
- Required intermediate versions are read from Shelly's update server and offered step
  by step (see [How a check works](#how-a-check-works)).
- Every approval is bound to the SHA-256 of the exact file, and the result is verified
  afterwards.
- Older versions stay stored, so a downgrade is possible (Gen2+).
- You review and install several devices in one dialog.

**Does the app manage scripts, backups or other settings of a Shelly?** It backs up the
settings of every Shelly, scripts, schedules and webhooks included, and restores them
part by part (see [Backups](#backups)). It changes only the few device settings listed
under [Devices](#devices); scripts and other settings are edited on the Shelly or with
other tools. The main job stays safe firmware updates.

**Which Shellys does the app list?** Every device of the Home Assistant Shelly
integration that has its own address in your network, Gen1 to Gen4. Devices that are
connected through another Shelly (e.g. Bluetooth devices behind a gateway) and devices
disabled in Home Assistant are not listed. Shellys that Home Assistant knows but the
app cannot update appear under **Other Shellys**, display only (see
[Requirements](#requirements)).

**Why is a device under "Removed"?** It is no longer in the Home Assistant Shelly
integration. The app keeps it so it does not vanish unnoticed. Set it up in Home
Assistant again and the mark disappears, or use **Remove from list** in its row menu.

**Do the Shellys need internet access?** No. Home Assistant asks the Shelly servers and
downloads the firmware; the Shellys fetch it from the app on port 8098.

**Does the app install updates on its own?** No. A check only provides the firmware.
Every installation needs your approval in the installation dialog.

**Does it offer beta firmware?** Only if you switch on **Offer beta firmware** (Gen2+);
each beta needs an extra confirmation (see [Beta firmware](#beta-firmware)).

**Is there a beta version of the app?** Release candidates of the next version appear in
the `beta` branch of the repository. To try one, add
`https://github.com/g4mm3lHa/Shelly-Firmware-Manager#beta` as a second repository in the
app store (like the stable one, with `#beta` at the end). It offers **Firmware Manager
for Shelly (Beta)**, a separate app with its own data and its own firmware store. Only
one of the two apps can use host port 8098: give the beta app another host port in the
network section of its Configuration tab (e.g. 8097) and set its download base URL to
that port. The beta app shows a red **(beta)** next to its title. Report problems as
issues, with the app version.

**Why an IP address instead of a host name?** Many Shellys cannot resolve host names of
your network. The download then fails with a connection error (see
[Download base URL](#download-base-url)).

**Why has the download server on port 8098 no login?** Shellys cannot log in to a
download server. The server only serves files of a running installation and, by
default, only to the device being updated (see [Security](#security)).

**What happens if an installation is interrupted?** The device is shown as
"Unconfirmed" or "Error". Check the device again; a failed installation can be repeated
from **Updates Available**.

**How much disk space does the firmware store need?** The Firmware tab shows the number
of files, their size and the free disk space. Firmware is never deleted automatically;
the filter **Unused** lists files no device needs. The store is part of every Home
Assistant backup of the app, so backups grow with it: the size in the Firmware tab is
about what a backup of the app adds.

**Can I go back to an older firmware?** Yes, on Gen2+ devices, if the older version is
in the firmware store and **Allow downgrades** is switched on (see
[Downgrade](#downgrade-gen2)).

## Known limitations

- Only original Shelly firmware is supported; beta versions only for Gen2+ and only while
  **Offer beta firmware** is on.
- Home Assistant OS or Supervised is required. The user interface is in English and seven
  machine-translated languages; the activity log, texts of the Shelly itself and this
  documentation are English only.
- The connection test of the download server needs a Shelly of Gen 2 or newer; Gen1
  devices cannot fetch a test address.
- Scheduled installations: at most 7 days ahead, once; schedules have one action each.
  Runs the app misses (not running) are skipped, not caught up.
- Only Shellys on Wi-Fi/LAN that are set up in the Home Assistant Shelly integration are
  supported (see [Requirements](#requirements) for the devices that are not); they are
  shown under **Other Shellys** without update function.
- Only devices with their own address in a private network (see
  [Download base URL](#download-base-url)) are contacted. Devices connected through
  another Shelly (e.g. Bluetooth devices behind a gateway) are not listed on purpose.
- Battery devices can only be checked, updated and backed up while they are awake.
- Backups: passwords (Wi-Fi, device, MQTT) are never part of a backup, because a Shelly
  does not give them out – after a reset they must be entered again. Also not part: the
  key-value store, virtual components, the device profile (Gen2+) and the mode (Gen1) –
  a difference there is named, not restored – and the script Home Assistant maintains for
  its Bluetooth proxy. A backup can only be restored to the device it was made on. A
  re-created script, schedule or webhook can get another ID on the device. Gen1: actions
  are part of a backup only where the device lets them be read.
- Keep installed versions: the installed version can be stored only while Shelly still
  offers it, so a device that already has an open update has no previous version stored.
- Offer delay: the age counts from the build date, which can lie a few days before Shelly
  starts to offer the version; that start cannot be read.
- Gen1: Shelly offers the firmware only over plain HTTP and without a checksum (see
  [Gen1 firmware](#gen1-firmware)); no downgrade; no installation progress.
- A downgrade needs the older version in the firmware store – Shelly only offers the
  current version for download. A device may refuse a downgrade.
- Communication with the Shellys is unencrypted in your network. Shellys that accept only
  HTTPS (Enhanced Security, see [Troubleshooting](#troubleshooting)) are not supported yet.
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

- **Your Shellys** (private IP addresses from the Home Assistant device registry, see
  [Download base URL](#download-base-url)) – to read their state, change settings you
  approve and start installations. The connection test asks one Shelly to fetch a test
  address from the app's own download server; **Try again** opens a connection to the
  device's web port and asks the device for its identity.
- **Shelly servers** below `shelly.cloud` – version offers, firmware downloads and the
  official changelog pages. Only the firmware ID (e.g. `Plus1`) and, for Gen2+ devices,
  the installed firmware version are part of the request; no other information about
  your devices or Home Assistant is sent.
- **Home Assistant** via the Supervisor – device registry, area registry (for the
  **Area** column), the Shelly config entries and discovery flows (for **Shellys not
  listed**), notifications, the app's own settings, its slug (for the link to the app
  page) and its host port (read for the **Settings** tab, set when you change **Host
  port**), and the Home Assistant version (only for **Details for a bug ticket**).
  Before a new host port is set, the app opens a TCP connection to that port on the
  address of the download base URL and closes it at once, to see whether the port is
  in use.

While **Resolve host names** is on, the app looks up each device's host name (for
example `shellyplus1pm-a0dd6c29c3e0.local`) through the name resolution of Home
Assistant on every check, for the **Host name** column. This is a lookup only; the app does not connect to the address it gets.
Names ending in `.local` are answered by mDNS in your network (Home Assistant OS and
supervised systems with systemd-resolved). On a system without it, the DNS of Home
Assistant may pass the lookup on to your configured DNS servers.

The app stores in its private data (`/data` of the app, part of a Home Assistant
backup of the app): the settings, the device list and state, the firmware files, the
activity log, the device passwords you store, the result of the last connection test,
the comparison after an update (until the next check of the device), and the
[backups](#backups) of your device settings. A backup
holds the configuration a Shelly reports – including Wi-Fi names, addresses of servers,
scripts and webhook addresses, which can contain tokens – but no passwords. Nothing of it
is sent anywhere; a backup leaves the app only as a file you download or as part of a
store export.

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
- Backups of device settings are stored encrypted with a key that is created at the
  first start and stays in the app data (file mode 0600); the key and the backups lie in
  the same app data, so this protects single files, not a copy of the whole app data. A
  downloaded backup and the backups in a store export are encrypted with the password
  shown at the download or export instead; that password is stored nowhere. A file is
  only opened if its seal matches. Restoring needs a confirmation, network settings a
  second one; passwords are never read, stored or restored.
- The download server (port 8098) only serves files of a running installation, by
  default only to the device being updated. It has no login, because Shellys cannot
  authenticate. The one exception is the connection test you start: a test address with
  a random token, valid only during that test, fetched once by the chosen device; it
  returns a short text, never firmware.
- **Details for a bug ticket** masks names, IP and MAC addresses and never contains
  passwords; the text is only copied to the clipboard, nothing is sent.
- All Python packages are bundled and pinned by checksum; the base image is pinned.
  The app runs inside its own container (as root there) without access to your Home
  Assistant files.
- An AppArmor profile (on hosts with AppArmor, e.g. Home Assistant OS) restricts the app
  further: it may only run Python, read its program files, write its private storage and
  use the network – no other programs, no special privileges.

Remaining risks: communication with the Shellys is unencrypted; Gen1 firmware, uploaded
files and imported files that Shelly has not offered cannot be verified; Gen1 passwords are stored in plain text; Shelly's
server interfaces are not officially guaranteed.

## Upgrading from older versions

What changes when you update from an older version:

- **From 3.11.1 or older:** the options move from the **Configuration** tab of the app
  page into the app. At the first start the app takes over the values saved there, stores
  them itself and empties the **Configuration** tab. A value that is no longer valid gets
  its default; the app log names it. If Home Assistant cannot be asked, the app starts
  with the defaults and tries again at the next start. Going back to an older version of
  the app afterwards starts it with the default options. The card **Getting started**
  appears only on new installations; the areas of Home Assistant show after the next check.
- **From 3.8.0 or older:** these versions stored imported files as downloads, and a
  Gen1 file cannot be checked against Shelly. At the first start all stored Gen1 files
  are therefore marked as imported; a Gen1 update provided from them, also one whose
  installation failed, is withdrawn until the next check, which downloads Shelly's
  current file again. A Gen2+ update provided from a file the Shelly update server does
  not name is replaced by Shelly's file at the next check. Delete stored
  files you imported from an export you do not fully trust. **Keep installed versions**
  is on after the update: the next check also downloads the version each up-to-date
  Gen2+ device runs (about 1–2 MB per version). Switch it off if you do not need it.
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
- **From 3.0.0 release candidates:** the old example address `http://192.168.178.50:8098`
  no longer needs a confirmation – it is checked like any other address. If it is not the
  address of your Home Assistant, correct it in the Settings tab (**Test connection** shows
  whether the Shellys reach it).
- **From 2.x or 1.x:** update to 3.0.0 first and start it once (it removes the former
  shared Shelly password from the stored options and converts the table settings), then
  update to the current version – or remove and reinstall the app. Backups made before
  the 3.0 upgrade still contain the old shared password.

## Support

1. Look up the message in [Troubleshooting](#troubleshooting) and the [FAQ](#faq).
2. Questions and discussion: the Home Assistant community thread
   <https://community.home-assistant.io/t/firmware-manager-for-shelly-devices/1035784>.
3. Bugs, device problems and feature requests:
   <https://github.com/g4mm3lHa/Shelly-Firmware-Manager/issues/new/choose>. Pick the
   form **Device problem** if one Shelly misbehaves. A report needs the versions of the
   app and of Home Assistant, the Shelly model, generation and firmware, the entry of
   the activity log (Logs tab) and, if there is one, the lines of the app log (Log tab
   of the app page).
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
