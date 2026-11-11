# Shelly Firmware Manager

Keeps your Shelly devices (Gen1 to Gen4) up to date – locally and under your control.
The app finds all Shellys of the Shelly integration, checks for new stable firmware,
downloads it and serves it to the devices from its own download server in your network.
Nothing is installed without your approval.

- Shellys only talk to Home Assistant – they need no internet access.
- Every installation is approved manually and verified afterwards.
- Stored firmware comes first: a check provides the next higher stored version before
  the Shelly update server is asked.
- Older firmware versions stay stored and allow a downgrade (Gen2+).
- Device settings such as name, NTP server, power-on action, eco mode and password
  protection can be edited directly.

After the installation set the **download base URL** in the app's Settings tab (LAN IP
address of Home Assistant and port 8098) – see the Documentation tab.
