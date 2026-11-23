# Firmware Manager for Shelly

Keeps your Shelly devices (Gen1 to Gen4) up to date – locally and under your control.
The app finds all Shellys of the Shelly integration, checks for new stable firmware,
downloads it and serves it to the devices from its own download server in your network.
Nothing is installed without your approval.

- All Shellys of the Shelly integration in one list, sorted by what needs your attention,
  with search and filters.
- Automatic check on a schedule; Home Assistant notifies you about new updates.
- Shellys only talk to Home Assistant – they need no internet access.
- Every installation is approved by you, bound to the exact file (SHA-256) and verified
  afterwards; required intermediate versions are offered step by step.
- Install now or at a chosen time (up to 7 days ahead); optional beta firmware (Gen2+).
- Optional hold-back of brand-new versions for a number of days.
- The version a device runs is kept, so you can go back to it (downgrade, Gen2 and newer).
- Device settings, cloud switch and passwords for one or many devices; the firmware
  store moves to another Home Assistant instance as one sealed ZIP file.
- Schedules restart Shellys or switch their cloud or Bluetooth at fixed times.
- Backups of the device settings (scripts, schedules and webhooks included): by hand,
  before every installation and on a schedule; compare and restore part by part.
- English and German interface (German machine translated – corrections welcome).

Requires Home Assistant 2026.10 or newer and the Home Assistant Shelly integration (the
app works with the devices it knows: Shellys on Wi-Fi/LAN; not Shelly BLU, Zigbee, Matter
or MQTT-only devices – Gen4 with Zigbee/Matter firmware only when also set up in the
Shelly integration). The Shellys must reach Home Assistant on TCP port 8098. Only original
Shelly firmware is supported.

First steps: set the **download base URL** in the app's Settings tab (LAN IP address of
Home Assistant and port 8098) and turn on **Watchdog** – see "Quick start" in the
Documentation tab.

Help: "Troubleshooting" and "FAQ" in the Documentation tab; questions in the
[community thread](https://community.home-assistant.io/t/firmware-manager-for-shelly-devices/1035784);
bugs and feature requests at <https://github.com/g4mm3lHa/Shelly-Firmware-Manager/issues>.

Unofficial community project. It is not affiliated with, endorsed or sponsored by
Allterco Robotics or Shelly. Shelly is a trademark of its holder.
