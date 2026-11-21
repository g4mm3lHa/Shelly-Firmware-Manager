# Test report 3.7.0

Final release 3.7.0 on branch `release-3.7.0` (base: `main` = `v3.6.1`), after
3.7.0-rc1; rc1 was tested live through the beta channel.

## Scope

| Area | Change |
|---|---|
| Image | the app is installed from a prebuilt image on `ghcr.io` (aarch64, amd64), built by `.github/workflows/images.yml` |
| Settings tab | an empty download base URL is filled in with `http://<host>:8098` from a private LAN IP of the Home Assistant page, with a note; nothing is saved before Save |
| Icon and logo | new cobalt-blue icon and logo (app folder, README) |
| Documentation | options one per row, shorter Devices, Firmware and Settings sections, beta channel, backup size, Watchdog rows, Home Assistant community thread under Support; `docs/UI_DECISIONS.md` split into `docs/ui/` by area |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.7.0, pytest | passed, 421 tests |
| UI full | ui_test 210/210; ui_features 752/752 (download_url_hint 26, setting_help 26, download_url_notice 65, download_url_suggestion 19, firmware_store 526, store_transfer 90); layout gate (capture) 28/28 |
| Test helper | `menu_items` (row menu) scrolls the button into view and waits before the click: the scroll event of the browser arrived after the click and closed the menu again (the full run failed three times, also on the rc1 commit; the app behaves as intended) |
| CVE check (2026-11-21) | PyPI lists no known vulnerability for the ten pinned packages (unchanged since 3.6.1); base image tag `python:3.12.16-alpine3.24` resolves to the pinned digest `sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89`; OSV not queried (blocked by the proxy) |
| Docs check (seven points) | changelog bullets match `DOCS.md` (Quick start, Download base URL suggestion, Privacy); options unchanged in `config.yaml`, `translations/en.yaml` and Configuration; feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt` (unchanged); all relative links and anchors resolve (script); section order of `DOCS.md` and fixed content of `README.md` unchanged |
| Screenshots | `qa/screenshots/3.6.0/` kept (the pictured views did not change; the suggestion note shows only for an empty address); `docs/images/` logos are the new ones |
| Image build | Images workflow for rc1 built and pushed aarch64 and amd64; CI and Images green on `release-3.7.0` and `beta` |

## Live tests through the beta channel

- rc1 tested in Home Assistant by the maintainer (2026-11-21): installation from the image (fast),
  new icon and logo, address suggestion with note, README badge "open app page", documentation: passed.

## Not tested here

- Real Home Assistant / Ingress / Supervisor, real Shelly devices, non-Chromium browsers; the
  restart by the Watchdog when the health check fails; item 12 (one download for several devices).
