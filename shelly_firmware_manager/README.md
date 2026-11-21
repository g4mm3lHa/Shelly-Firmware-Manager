# Firmware Manager for Shelly

Keeps your Shelly devices (Gen1 to Gen4) up to date – locally and under your control.
The app finds all Shellys of the Shelly integration, checks for new stable firmware,
downloads it and serves it to the devices from its own download server in your network.
Nothing is installed without your approval.

- Shellys only talk to Home Assistant – they need no internet access.
- Every installation is approved manually and verified afterwards.
- Gen2+: the Shelly update server names the next step (also required intermediate
  versions); a stored file of that version is used instead of a download. Gen1: stored
  firmware comes first.
- Older firmware versions stay stored and allow a downgrade (Gen2+, once switched on).
- Device settings such as name, NTP server, power-on action, eco mode, password
  protection and the websocket debug log can be edited directly.
- The firmware store moves to another Home Assistant instance as one sealed ZIP file;
  device passwords can be stored or set on several devices in one step.

Requires Home Assistant 2026.10 or newer and the Home Assistant Shelly integration (the
app works with the devices it knows). The Shellys must reach Home Assistant on TCP port
8098. Only original Shelly firmware is supported.

After the installation set the **download base URL** in the app's Settings tab (LAN IP
address of Home Assistant and port 8098) – see "Quick start" in the Documentation tab.

Help: "Troubleshooting" and "FAQ" in the Documentation tab; bugs and feature requests at
<https://github.com/g4mm3lHa/Shelly-Firmware-Manager/issues>.

Unofficial community project. It is not affiliated with, endorsed or sponsored by
Allterco Robotics or Shelly. Shelly is a trademark of its holder.
