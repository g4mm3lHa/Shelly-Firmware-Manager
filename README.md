# Shelly Firmware Manager 3.0.0 – Home Assistant App

Detects all Shelly main devices registered by the Shelly integration (Gen1–Gen3), checks
for new stable firmware, downloads it and provides it through its own local download
server. Every update is installed only after manual approval in the Ingress UI.
The Shellys only talk to Home Assistant (port 8098) – no internet access required.

Requires Home Assistant 2026.10 or newer. Apps were called "add-ons" before; the
slug `shelly_firmware_manager` is unchanged, so existing installations update in place.

Status: release candidate, not yet released. Tested with simulated devices only; NOT
tested on real hardware.
Test with a non-critical device first.

See `shelly_firmware_manager/CHANGELOG.md` for the version history.

## Installation

1. Copy the folder `shelly_firmware_manager` to `/addons/` (Samba/SSH).
2. Settings → Apps → App store → ⋮ → "Check for updates" → "Local apps" →
   "Shelly Firmware Manager" → Install.
3. Start the app, enable "Show in sidebar" and adjust the options in the app's
   "Settings" tab (or in the "Configuration" tab of the app page). Set
   `download_base_url` first: it has no default, and installations are refused until it
   is set.
4. Firewall: allow Shellys → Home Assistant IP, TCP 8098 (in addition to the existing
   rules of the Shelly integration).

The image is built offline: the Python base image is pinned by digest and all Python
packages are bundled as wheels in `wheels/` (installed with `--no-index` and
`--require-hashes`, every transitive version pinned in `requirements.txt`). The
Dockerfile is a plain multi-stage build: the wheels are installed in a first stage and
only the installed packages are copied into the final image.

Updating: replace the folder, "Check for updates", then "Update". An update does not
start a check; the first automatic check runs at the next scheduled time.

Stopping or restarting the app (also for updates and Home Assistant restarts) cancels
running checks and installations cleanly within a few seconds and saves the state; both
web servers are stopped in parallel (at most 5 s). An
installation interrupted this way is shown as "Unconfirmed" after the next start –
check the device and run "Check for updates" again.

## Configuration

| Option | Meaning |
|---|---|
| `check_interval_days` | Automatic check every 1–30 days (default 7) |
| `check_time` | Time of the automatic check, `HH:MM` in the Home Assistant time zone (default `02:45`) |
| `download_base_url` | LAN IP address of Home Assistant + host port ("Network" section), e.g. `http://192.168.1.20:8098`. No default: installations are refused until it is set. Use the IP, not a host name – Shellys often cannot resolve names. The Settings tab shows a hint for host names and for addresses outside the local network |
| `restrict_downloads_to_devices` | on: firmware is served only to the IP of the respective Shelly. If downloads are rejected and the log shows a 172.30.x.x address, switch it off |
| `max_parallel_installs` | Maximum number of installations running at the same time (1–20, default 5) |
| `gen1_updates_enabled` | Look up and install Gen1 firmware (default on; see "Gen1 firmware") |
| `gen1_allowed_types` | Gen1 types for which firmware is looked up |
| `log_retention_days` | Days the activity log is kept (1–365, default 90) |
| `log_entry_limit` | Maximum number of log entries (100–3000, default 1000) |
| `pinned_certificates` | Certificate pins: host + SHA-256 fingerprint (see below) |

All options can be edited in the UI ("Settings" tab, admins only). Saving writes them
through the Supervisor (`/addons/self/options`) and then restarts the app
(`/addons/self/restart`) – the UI announces the restart and reconnects automatically.
The Settings tab groups the options into cards (Checks, Firmware & storage, Logs,
Advanced) with one common "Save settings" button and an "Unsaved changes" indicator.

Upgrading from 2.x: the hourly interval becomes "every 7 days at 02:45", the log limit
starts at 1000 entries (the newest 1000 are kept once), and the former option
`shelly_password` is removed. On the first start of 3.0 the app saves its cleaned
options once through the Supervisor so the old shared password no longer remains in
the app options. It is not copied to any device entry and nothing is written to the
devices. Home Assistant backups created before the upgrade still contain the old shared
password – delete or protect them accordingly.

Upgrading from 3.0.0 rc1–rc5: these versions used `http://192.168.178.50:8098` as
example default. If that value is still stored, installations are refused and a notice
asks to change it – or to confirm with "This address is correct" that it really is the
address of Home Assistant (`192.168.178.x` is the default network of many routers).
The confirmation is stored in the app state and ends when the address is changed.

Versions before 2.0 are no longer migrated. When upgrading from 1.x, update to a 2.x
version first or remove and reinstall the app.

## Shelly passwords

There is no shared password any more: each protected Shelly needs its own entry
(row menu ⋮ → "Device password" or the lock icon). The dialog separates

- **Store existing password** – the device is already protected; the password is
  tested against the device and stored in this app. Nothing changes on the device.
- **Enable protection** / **Change password** – sets the password on the device
  (Gen2+ `Shelly.SetAuth`, Gen1 `/settings/login`, user `admin`). Requires a second red
  confirmation and ticking "I have stored the password securely". The result is
  verified with the new password and the protection state reported by `/shelly`; if
  the answer is lost or ambiguous, the previous stored password is kept.
- **Remove protection** – removes the password on the device after confirmation; the
  stored password is deleted only after the device reports no protection.
- **Forget stored password** – deletes the entry in this app only.

The dialog always warns: if the password is lost, access to the device may be locked
and recovery may require a factory reset (settings may be lost). The lock icon shows
the protection reported by the device, independent of whether a password is stored.

- Gen1 uses HTTP Basic Auth, Gen2+ HTTP Digest Auth (SHA-256), both with user `admin`.
  Gen1 devices with a different user name are not supported.
- Passwords are stored in the app's private `/data/credentials.json` (file mode 0600).
  They are never part of the state, the API, the UI or the logs.
- Gen2+: only the Digest hash `HA1 = SHA-256("admin:<device id>:<password>")` is stored.
  It is bound to the device ID (the Digest realm) and is only accepted for that realm;
  there is no fallback to weaker algorithms.
- Gen1: Basic Auth needs the password itself, so Gen1 device passwords are stored in
  plain text and are contained in Home Assistant backups of the app – protect your
  backups accordingly.
- Basic Auth (Gen1) sends the password practically in plain text (Base64) over the
  unencrypted LAN connection; anyone able to read your LAN traffic can read it. Digest
  Auth (Gen2+) does not send the password.

## Gen1 firmware

Shelly offers Gen1 firmware only via plain HTTP and without a checksum (the HTTPS
variant on `firmware.shelly.cloud` answers 502). Integrity and origin of a Gen1 download
therefore cannot be verified – the SHA-256 shown is calculated by the app after the
download and only ensures that the Shelly receives exactly that file. The approval
dialog shows a warning for Gen1, the firmware view marks such files "HTTP only". Switch
off `gen1_updates_enabled` to skip Gen1 devices completely (status "Skipped").
Gen2+ firmware is downloaded via HTTPS only.

## Check schedule

- Automatic checks run every `check_interval_days` days at `check_time` in the time
  zone of Home Assistant (passed to the app by the Supervisor; no location or internet
  lookup). A changed schedule starts at the next occurrence of the chosen time; later
  dates follow the day interval.
- No check at startup or after an update. Manual checks, single-device checks and
  "Reload device info" do not move the schedule.
- Missed dates (e.g. the app was stopped) are caught up once after the start; the
  cadence stays the same. At most one automatic check runs per scheduled date; the
  attempt is recorded at its start, so a failing check is not retried in a loop.
- Daylight saving time: a time that does not exist (spring) runs at the first valid
  time after the gap; a time that occurs twice (autumn) runs once, at the first
  occurrence.
- While installations run, a due check waits (retried every minute) instead of being
  dropped.
- Scheduled checks create a Home Assistant notification when they find updates newer
  than the installed firmware – only once per device and version.
- Up to 4 devices are checked in parallel (fixed internal limit; each Shelly answers
  one request at a time). The version offer and each firmware download are requested
  only once per firmware ID and check.

## UI

The UI copies all CSS variables of the active Home Assistant theme (colours, fonts,
radii …) and follows theme and light/dark changes live. Values that could load
resources (`url(`, `image(`, `@import`, CSS escapes) are rejected. Four tabs:
Devices · Firmware · Logs · Settings.

### Devices

- Header: "Last checked: …" and the check cycle ("Every 7 days · 02:45", next run as
  tooltip) as two pills, and "Check now". While a check runs a progress line
  shows e.g. "Checking devices · 18/25 completed · 2 unavailable · checking firmware
  information" with a progress bar.
- Each device row shows its own activity during a check: Waiting, Checking…,
  Downloading… – the previous status is hidden meanwhile so it never looks fresh.
- Toolbar: search (name, IP, model), filter chips with counts (All, Updates, Errors,
  Up to date, Ignored) and "Columns ▾" to show or hide columns and "Reset columns".
- Card "Updates available" on top (tick devices, "Review & install (n)"), below it
  "All other devices", ignored devices in their own card "Ignored" at the end. Rows
  are sorted by name; click a column header to sort within the cards, again to
  reverse. Data columns can be dragged to a new position (keyboard: Alt+Arrow left /
  right on the header). Column choice, order and sort are stored in the app and apply
  to all users (the app panel is admin-only). On wide screens the columns of both
  cards line up; long names are truncated (full name as tooltip).
- Columns: Device (HA name, Shelly name with a pencil to edit it), IP (opens the
  Shelly web UI), Gen, Firmware (installed → available, firmware ID; Gen1 shows only the
  version number, e.g. 1.14.0, the full string is in the tooltip and the status
  details), Status, Action; optional: Last update, NTP.
- Action icons: power-on action (lightning, opens the power-on dialog; disabled with an
  explanation for devices without switchable outputs), eco mode (leaf), password
  protection (lock), access point (Wi-Fi), Bluetooth – green when on, grey when off;
  actions (link) and schedules
  (calendar) – red if at least one is active, grey if none; restart. Actions and
  schedules open the Shelly web UI in a new tab. A device or firmware without the
  feature shows the icon disabled with an explanation; a value that is not known (not
  read yet, or the last read failed) is marked with "?" and never shown as "off".
  Access point and Bluetooth dialogs only switch on or off. Bluetooth uses only the
  global switch where the firmware offers it (Gen2+ firmware 2.0 and newer has none).
  Gen1 access point is shown read-only: switching it on turns off the Wi-Fi client
  mode (official API docs), the device would leave the network.
- Status: hover or keyboard focus shows the details, click opens them in a popup. For
  an installable device (Ready, not ignored, no check running) the popup has a red
  "Install update" button next to "Close". Ignored devices show an additional yellow
  "Ignored" label next to the status (not clickable).
- Row menu ⋮: Check for updates, Install update (only when installable, see above),
  Downgrade (Gen2+ only, see "Downgrade" below), Reload device info,
  Ignore / Resume device, Open web UI, NTP server, Power-on action,
  Eco mode, Device password, Status details, Restart. While a check or an installation
  runs, "Check for updates" is disabled and the reason is shown.
- "Check for updates" checks only this device like the full check (reuses stored
  firmware, downloads missing new firmware) – no installation, restart or settings
  change. It is written to the activity log. It also works for ignored devices.
- "Reload device info" reads identity, installed firmware and settings from the device
  only – no request to Shelly servers, no download. Known update information and its
  check time stay; the row shows "Reloading…", then a short message.
- "Ignore device" excludes the device from automatic checks, "Check now" and background
  reads; it is not reported as unregistered and cannot be installed until it is
  resumed. Resuming sends nothing to the device.
- Status details show the last attempt ("Last check") and the last successful check
  separately; a failed attempt keeps the last known firmware.
- Below 700 px width (phones) the rows become separated tiles: name, Shelly name,
  status, ⋮, firmware installed → available and IP. Under "Details": labeled buttons
  side by side and wrapping (Power-on value, Eco mode, Password protection, Access
  point, Bluetooth, Actions, Schedules, Restart), generation, model, firmware ID, NTP
  and check times. No column chooser on phones, equal-width tabs, scrollable filter
  chips, touch targets of at least 44 px. Inside Home Assistant the page title is
  hidden on phones (HA shows it already).
- The page refreshes every 30 s, every 3 s while a check or installation runs and not
  at all while it is hidden (background tab, other HA panel).

| Status | Meaning |
|---|---|
| Up-To-Date | Installed firmware is the newest stable version |
| Available | Update found, firmware not downloaded (e.g. deleted in the Firmware tab) |
| Ready | Firmware downloaded, waiting for approval |
| Queued | Approved, waiting for a free installation slot |
| Installing | Installation running |
| Unconfirmed | Installation started, target version not confirmed (with diagnosis) |
| Error | Check, download or installation failed |
| Skipped | Gen1 device while `gen1_updates_enabled` is off |
| Unregistered | Device no longer in the Home Assistant device registry |

- Sub-devices (e.g. channels of a Pro 3EM, plugs of a Strip) are not listed; the main
  device covers them.
- "Review & install" shows versions, size, SHA-256, source and the changelog: all
  versions between the installed and the target version, categories as tags, security
  entries highlighted, model-specific entries marked ("This model" if they name the
  device's model). If the changelog is not available, the installation is still possible.
  At most `max_parallel_installs` run at the same time, further ones wait. File checksum,
  MAC and source build are verified again before the installation.
- Editing always reads the current values from the Shelly first. The Shelly name then
  shows a confirmation with old and new value; NTP server and AOP are saved directly,
  eco mode after one confirmation. A message appears only for errors, if nothing
  changed or if the Shelly requires a restart. The app reads the values back.
- Settings, restart and eco mode are locked while the device is installing.

#### Downgrade (Gen2+)

"Downgrade" in the row menu lists all stored stable versions of the
device's firmware ID that are older than the installed one, newest first (uploaded
files are marked). After choosing one, the installation dialog shows the older version
as target with a warning; a second confirmation with the checkbox "I understand that
the downgrade may fail or reset settings" starts it (as for device passwords). The
installation runs exactly like an update (download server, SHA-256, OTA events,
verification of the exact target version) and is logged as "downgrade". It is also
offered for devices that are up to date and for ignored devices; never for Gen1. If a
newer version was known from the last check, the device shows "Available" afterwards
and the newer version is provided again at the next check.

Shelly's servers only offer the current version. Older versions are therefore only
available if the app downloaded them earlier (nothing is deleted automatically) or if
they were uploaded in the Firmware tab. A device may refuse a downgrade, and settings
added by newer firmware may be lost.

Gen2+ devices receive the update command over their local RPC WebSocket
(`ws://<device IP>/rpc`), so the app gets the device's own update events: the status
shows the download progress ("Installing 55 %"), and an update error reported by the
device (e.g. "Connection error: -14") ends the installation at once with that message
and a hint to check `download_base_url` and port 8098. If the WebSocket is not
available, the command is sent over HTTP as before. Gen1 is unchanged.

After the update command the app waits 30 seconds and then checks every 10 seconds
whether the Shelly is reachable again and reports exactly the target version (Gen1:
the version number). Any other version – also a newer one – ends as "Unconfirmed" with
the hint "The Shelly reports X instead of the target version Y". If the Shelly is
reachable, still reports the old version and has not requested the firmware after 3
minutes, it stops with a diagnosis; the wait ends after 5 minutes at the latest.
Possible diagnoses:
- "The Shelly never requested the firmware" – `download_base_url` must be the LAN IP of
  Home Assistant with port 8098 and reachable from the Shelly. The download diagnosis
  is kept per device: parallel installations of the same firmware do not affect each
  other (unless Docker hides the sender IP, see the next point).
- "Download rejected: request came from 172.30.x.x …" – Docker does not pass the real
  sender IP; switch off `restrict_downloads_to_devices`.
- "The Shelly downloaded the firmware but still reports …" – the device loaded the file
  but did not install it; check the log in the Shelly web UI.

### Firmware

- Search, "n files · x MB stored" (total size of the store); ⓘ explains the storage.
- One card per firmware ID (generation tag, "HTTP only" for Gen1), versions as rows,
  the newest marked "latest", uploaded files marked "Uploaded"; "For n devices ▾"
  lists the devices it is provided for.
- SHA-256 shortened with a copy button; icon buttons to download and delete.
- Firmware is **never deleted automatically** – all downloaded versions stay until they
  are deleted here. Delete asks twice (second step with the checkbox "I understand that
  this file cannot be restored by the app"). It is locked during a check and for a file
  that is provided as pending update or being installed; the button tooltip names the
  reason. Leftovers of interrupted downloads/uploads and files that are not in the store
  are cleaned up automatically.
- A stored file with matching SHA-256 is reused instead of being downloaded again; the
  offer is requested only once per firmware ID and check. Uploaded files are never used
  as the manufacturer's update, only for downgrades.
- "Upload firmware": choose a `.zip`, `.bin` or `.gbl` file (max. 64 MiB). The file is
  transferred, its SHA-256 shown, and firmware ID and version are pre-filled from a
  `manifest.json` in a ZIP if present. Confirm or correct them (firmware ID of a known
  Gen2+ device, stable version X.Y.Z) and optionally enter the expected SHA-256: if
  given, it must match, otherwise the upload is refused (it can be corrected without a
  new transfer). Files or versions that are already stored are refused. Uploads are
  logged with user and SHA-256. The origin of an uploaded file cannot be verified by the
  app – use files from a source you trust.

### Logs

The activity log is stored only in the app's `/data` (`events.json`) and kept for
`log_retention_days` / at most `log_entry_limit` entries. Every entry has a severity:
success (green), warning (yellow: device unreachable, timeout, data temporarily
missing) or error (red: failed writes or installations, authentication failures,
internal errors); a check gets the highest severity of its devices. Entries written
before 3.0 are not changed and are shown as success or error. It contains:
- checks: time, scheduled/manual, user (manual), number of devices, duration, result,
  expandable per device: new version or error;
- firmware downloads: firmware ID, version, size, SHA-256;
- firmware uploads: firmware ID, version, size, SHA-256, whether a SHA-256 was checked,
  user;
- installations and downgrades that actually started: start, confirmed (old → new),
  unconfirmed, failed – with the approving user;
- deleted firmware with user;
- Shelly setting changes (name, NTP, power-on action, eco mode): device, old → new,
  user, result;
- device passwords: stored, enabled, changed, removed, forgotten or failed – with device
  and user;
- app settings saved in the Settings tab: changed options with old → new and user.

Not logged: approvals that never started and restarts. Passwords and password hashes
never appear. The user is taken from the Ingress headers `X-Remote-User-Display-Name` /
`X-Remote-User-Name`. The view groups entries by day and offers the filters All,
Checks, Installations, Settings (incl. passwords and app settings), Warnings and
Errors, a device search and "Hide checks without findings". On phones each entry is a
compact tile (time, type, severity, short summary); details are collapsed until tapped.

### Settings

All options of the configuration table above, grouped into the cards Checks, Firmware &
storage, Logs and Advanced (stacked on phones, with the save area staying visible at
the bottom). "Unsaved changes" appears as soon as a value differs from the stored one.
While typing the download base URL, a hint appears for every host name (also `.local`
names: Shellys often cannot resolve them – use the IP) and for IP addresses outside the
local network (no hint for private IPv4, IPv6 unique-local or link-local addresses); it
does not block saving. Clearing the field unsets the option. Saving shows which options
changed, saves them and restarts the app.

While installations are disabled by the download base URL (not set, or the former
example value not yet confirmed) a notice above the tabs explains it, with the buttons
"Open settings" and, for the former example value, "This address is correct".

## Certificate pinning

Some Shelly servers (at least `updates.shelly.cloud` and `fwcdn.shelly.cloud`) use
certificates of the in-house CA `O=Allterco` that no standard client knows. Pins for
these two hosts are included as defaults. Without a matching pin the device shows
"error" with a message like:

> Certificate of updates.shelly.cloud is not publicly trusted. SHA-256: AB:CD:… After
> independent verification add it under "pinned_certificates" for updates.shelly.cloud.

1. Copy the fingerprint from the message (UI or log).
2. Verify independently: on a phone via mobile data (not Wi-Fi) open
   `https://updates.shelly.cloud/update/Plus1` → certificate warning → certificate details
   → compare the SHA-256 fingerprint. Continue only if it matches.
3. "Settings" tab → "Pinned certificates" → "Add entry" (host + fingerprint) → Save.
4. After the restart: "Check now". The log shows
   "Certificate pinning active for updates.shelly.cloud (1 fingerprint(s))".
5. Several servers behind one name: add further entries with the same host. If another
   host (e.g. `fwcdn.shelly.cloud`) reports the same error, add it the same way.

Only hosts below `shelly.cloud` are allowed. For pinned hosts only the stored
certificates are accepted; all other hosts are validated normally (this includes
`shelly-api-docs.shelly.cloud`, which can be pinned the same way). If Shelly replaces
the certificate, the message "matches NONE of the stored fingerprints" shows the new
value – verify it independently first, then add it as an additional entry.

## Network connections

The app only opens these connections:
- Shellys in the LAN (only private IPs from the HA device registry), with the stored
  device password if the device requires authentication:
  - device info: `/shelly`, `/rpc/Shelly.GetDeviceInfo`
  - settings, read at every check, on "Reload device info" and before editing: Gen2+
    `Shelly.GetConfig`, `Webhook.List`, `Schedule.List`; Gen1 `/settings`,
    `/settings/actions` (only whether actions/schedules are active is kept)
  - password protection, only on your action: Gen2+ `Shelly.SetAuth`, Gen1
    `/settings/login`
  - access point / Bluetooth, only on your action: Gen2+ `Wifi.SetConfig` (`ap.enable`),
    `BLE.SetConfig` (`enable`, only where the firmware offers it)
  - settings, written only on your action: Gen2+ `Sys.SetConfig` (name, NTP, eco mode)
    and `Switch|Light|RGB|RGBW|CCT.SetConfig` (`initial_state`); Gen1 `/settings`
    (`name`, `sntp_server`, `eco_mode_enabled`) and `/settings/relay|light|color|white/<n>`
    (`default_state`). Gen1 `eco_mode_enabled` is not in the official API docs.
  - restart, only after confirmation: Gen2+ `Shelly.Reboot`, Gen1 `/reboot`
  - update command: Gen1 `/ota`; Gen2+ `Shelly.Update` over the device WebSocket
    `ws://<device IP>/rpc` (same private IP, no redirects, frames limited to 64 KB,
    Digest auth with the stored hash), which also delivers the device's update events
    (`ota_begin`, `ota_progress`, `ota_success`, `ota_error`); HTTP `/rpc` as fallback
- Shelly manufacturer servers (neutral User-Agent, no device data):
  - version offer via HTTPS: Gen2+ `updates.shelly.cloud/update/<firmware ID>`, Gen1
    `api.shelly.cloud/files/firmware`. Documented exception: for Gen2+ the firmware ID
    (the device's model app name, e.g. `Plus1`) is part of the URL and thus known to
    Shelly; nothing else about your devices or Home Assistant is sent.
  - firmware download from `*.shelly.cloud`: Gen2+ HTTPS only, Gen1 only available via
    HTTP (see "Gen1 firmware").
  - changelog: `https://shelly-api-docs.shelly.cloud/gen2/changelog/` and
    `https://shelly-api-docs.shelly.cloud/gen1/` – HTTPS only, max. 4 MB, certificate
    pinning like the other Shelly hosts; fetched at most once per check, only if updates
    were found and only for the generations concerned. A stored changelog that lists all
    offered versions is reused for 24 h; a missing version is refetched at most hourly.
  - on a TLS error one additional connection, only to read the fingerprint for the error
    message.
- Home Assistant via the Supervisor: device registry (WebSocket), notifications
  (`persistent_notification.create`), and – only when saving the Settings tab – the
  app's own options and restart (`/addons/self/options`, `/addons/self/restart`).
- DNS resolution only if a `configuration_url` contains a host name instead of an IP.
- No proxies from environment variables. The UI loads nothing external (CSP `'self'`);
  all icons are part of the app. Building the image downloads only the pinned base
  image; Python packages come from the bundled wheels.

## UI build

The UI sources are `shelly_firmware_manager/ui-src/app.js` and `style.css` (readable,
commented – edit these). The shipped files in `app/static/` are minified from them:

```
python tools/build_ui.py          # needs Node/npx locally, uses the pinned esbuild 0.28.2
python tools/build_ui.py --check  # verifies that app/static matches ui-src (no Node needed)
```

`index.html` is not minified. The test suite fails if the build is outdated.

## Tests

Development only, not part of the image. All device, Supervisor and Shelly server
access is mocked; no test contacts a real device.

Backend tests (repository root):

```
pip install pytest pytest-asyncio aiohttp
python -m pytest -c tests/pytest.ini tests -q
```

UI checks with Playwright (Chromium) against a local demo server with fake data
(`qa/demo.py <app dir> <port> <idle|busy>`, UI below `/ui/`, embedded view below
`/wrap` like the Home Assistant panel):

```
python qa/demo.py shelly_firmware_manager/app 8801 idle &
python qa/demo.py shelly_firmware_manager/app 8802 busy &
python qa/ui_test.py <shots dir>        # 360/430/1440 px, light/dark, embedded, idle/busy
python qa/lan_hint_test.py <shots dir>  # download URL hints in the Settings tab (port 8801)
python qa/demo.py shelly_firmware_manager/app 8803 legacy &
python qa/demo.py shelly_firmware_manager/app 8804 missing &
python qa/rc6_ui_test.py <shots dir>    # download URL notice and device progress
python qa/rc7_ui_test.py <shots dir>    # downgrade, upload, two-step delete (demo rc7 on 8805)
```

Further scripts: `qa/mobile_capture.py` (phone screenshots, demo on port 8711),
`qa/cache_test.py` (browser cache and 304 answers, port 8802) and `qa/resources.py`
(idle resource comparison of two versions). Screenshots are kept in
`qa/screenshots/`.

## Structure

```
README.md                  this file
shelly_firmware_manager/
  config.yaml              app definition (no "map", port 8098, Ingress 8099)
  CHANGELOG.md             version history
  icon.png, logo.png       app icon and logo
  Dockerfile               pinned base image, multi-stage offline installation of the wheels
  requirements.txt         pinned versions with hashes
  wheels/                  bundled Python wheels (musllinux, amd64 + aarch64)
  translations/en.yaml     labels of the configuration tab
  app/main.py              start, both servers, check schedule
  app/schedule.py          check schedule: every N days at HH:MM, HA time zone, DST, catch-up
  app/manager.py           flow: check, provide, install, settings, state
  app/shelly.py            device and manufacturer access, Basic/Digest auth (SSRF/download protection)
  app/credentials.py       per-device passwords (/data/credentials.json)
  app/changelog.py         changelog fetch and parser (shelly-api-docs.shelly.cloud)
  app/events.py            activity log (/data/events.json)
  app/app_options.py       Settings tab: validation, saving via the Supervisor
  app/tls.py               certificate pinning for shelly.cloud
  app/web_ui.py            Ingress UI + API
  app/download_server.py   download server for the Shellys
  app/static/              UI as shipped: index.html, minified app.js/style.css (build output)
  ui-src/                  readable sources of app.js and style.css
tests/                     pytest suite (development only, mocked network)
tools/build_ui.py          builds app/static from ui-src (development only)
qa/                        UI checks with Playwright against a local demo server (development only)
```

## Security notes

- The UI page and all API answers are sent with `Cache-Control: no-store`; only
  `app.js` and `style.css` (no data) are cached by the browser, keyed by a content hash.
- Download server (port 8098, no authentication): one route, GET/HEAD only, file name
  must be `<sha256>.<zip|bin|gbl>`; only files of a currently provided update, by default
  only to the IP of the target device. No directory listing, no server banner. Only a
  GET that starts the transfer counts as download for the diagnosis (not HEAD).
- Image: no pip/setuptools/wheel in the runtime image. The app process runs as root
  inside its container (the Supervisor mounts `/data`); running it as a non-root user
  is checked before the final release.
- Ingress UI (HA admins only): only the Ingress proxy IP, CSRF token for every write
  request and every request that contacts a Shelly, JSON only, 32 KB body limit, strict
  CSP, no innerHTML – all values are inserted as text. Only exception to JSON: the
  firmware upload (raw file, `application/octet-stream`, Content-Length required, at
  most 64 MiB, streamed to disk under a temporary name, never read into memory as a
  whole; CSRF as for every write). An approval is bound to the SHA-256 of the file;
  downgrades and deleting firmware need a second confirmation checked by the server. Input is validated (name ≤ 64 printable characters, NTP
  host[:port], eco mode true/false, power-on action only from the values the device
  offers, app options against the schema).
- Device responses are limited to 1 MB, changelog pages to 4 MB; the changelog is parsed
  with the standard HTML parser and displayed as plain text only.
- Remaining risks: the download server cannot authenticate the Shellys; the IP binding
  only works if Docker passes the sender IP; the SHA-256 is calculated by the app and
  proves integrity between app and Shelly, not origin – Gen2+ downloads are protected by
  HTTPS, Gen1 firmware cannot be verified at all (HTTP only, no checksum); the origin of
  an uploaded file cannot be verified (an optional SHA-256 only proves it is the file the
  user expected); there is no signature check; communication with the Shellys is unencrypted (Basic Auth ≈ plain
  text); plain-text passwords are part of backups; manufacturer endpoints are not
  officially guaranteed.

## Credits

- Tab and UI icons: [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache 2.0), inlined.
- Shelly mark in the app icon: [Simple Icons](https://simpleicons.org/) (CC0 1.0).
