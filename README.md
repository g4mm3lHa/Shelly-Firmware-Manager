# Shelly Firmware Manager 3.5.0-rc4

Home Assistant app that keeps your Shelly devices (Gen1 to Gen4) up to date – locally
and under your control. It finds all Shellys of the Shelly integration, checks for new
stable firmware, downloads it and serves it to the devices from its own download server
in your network. Nothing is installed without your approval.

- Shellys only talk to Home Assistant – they need no internet access.
- Every installation is approved manually and verified afterwards.
- Stored firmware comes first: a check provides the next higher stored version before
  the Shelly update server is asked.
- Older firmware versions stay stored and allow a downgrade (Gen2+, once switched on).
- Device settings such as name, NTP server, power-on action, eco mode, password
  protection and the websocket debug log can be edited directly.
- The firmware store moves to another Home Assistant instance as one sealed ZIP file;
  device passwords can be stored or set on several devices in one step.

Requires Home Assistant 2026.10 or newer and the Home Assistant Shelly integration (the
app works with the devices it knows). Only original Shelly firmware is supported for now.

- **User documentation:** [`shelly_firmware_manager/DOCS.md`](shelly_firmware_manager/DOCS.md)
  (also the Documentation tab of the app page in Home Assistant)
- **Version history:** [`shelly_firmware_manager/CHANGELOG.md`](shelly_firmware_manager/CHANGELOG.md)

## Contents

- [Installation](#installation)
- [Development](#development)

Credits: see [`DOCS.md`](shelly_firmware_manager/DOCS.md#credits).

## Installation

1. Copy the folder `shelly_firmware_manager` to `/addons/` on your Home Assistant
   (e.g. with the Samba or SSH app).
2. Settings → Apps → App store → ⋮ → "Check for updates" → "Local apps" →
   "Shelly Firmware Manager" → Install.
3. Continue with [First start](shelly_firmware_manager/DOCS.md#first-start): start the
   app and set the **download base URL** in its Settings tab (installations are
   disabled until it is set).

To update the app, replace the folder, run "Check for updates" and then "Update".

## Development

Everything outside `shelly_firmware_manager/` is for development only and is not
needed for the installation. The UI sources are in `ui-src/` (ES modules in `js/`,
`style.css`); `tools/build_ui.py` bundles and minifies them into the shipped files in
`shelly_firmware_manager/app/static/` (pinned esbuild 0.28.2, `--check` verifies them).
Tests mock all network access. One command runs the checks in the efficient order
(lint, type check, build check, version match, backend tests, optionally the Playwright
UI checks against demo servers with fake data):

```
tools/dev/check.sh          # lint + build check + backend tests (seconds)
tools/dev/check.sh ui       # ... + UI quick matrix and feature checks (under a minute)
tools/dev/check.sh full     # ... + full UI matrix, all feature viewports and the layout gate (final gate)
tools/dev/check.sh build    # rebuild app/static from ui-src first
```

`tools/dev/` also works offline with the bundled wheels (see `tools/dev/README.md`).
A map of modules, stored data, API routes and tests is in `docs/CODEMAP.md`. Tool
settings (ruff, mypy, pytest) are in `pyproject.toml`.

Layout: `shelly_firmware_manager/` (only what Home Assistant needs: app definition,
`README.md`/`DOCS.md`/`CHANGELOG.md` for the app store, Dockerfile, AppArmor profile,
pinned wheels, `app/` with the Python sources and the built UI), `ui-src/` (UI sources),
`tests/` (by area), `qa/` (UI checks, demo server, screenshots of the current version),
`tools/`, `docs/` (code map, UI decisions), `candidates/` (current test report).
