# Changelog

## 3.0.0

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
