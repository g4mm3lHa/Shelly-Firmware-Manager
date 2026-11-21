# Security policy

## Supported versions

Security fixes are made for the latest released version of the app. Please update to
it before reporting a problem.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting instead: open the **Security** tab of this repository and
choose **Report a vulnerability**.

Describe what you found, the version of the app and how to reproduce it. Reports are
handled on a best-effort basis; a fix is released as a new version and noted in the
changelog.

## Scope

The app runs locally in Home Assistant and talks only to Shelly servers (firmware
check and download) and to Shelly devices in your network. Relevant are, for example,
flaws in the web interface, the download server, device password handling, the
firmware store or the checks of downloaded firmware. Vulnerabilities in Home
Assistant itself or in Shelly devices and their firmware are out of scope; report
them to the respective project or vendor.
