# Test report 3.6.1

Final release 3.6.1 on branch `release-3.6.1` (base: `main` = `v3.6.0`), after two
release candidates; rc2 was tested live through the beta channel.

## Scope

| Area | Change |
|---|---|
| Download base URL | allow-list: a private IP address (`shelly.LAN_NETWORKS`) or a plain ASCII host name; other IP addresses, IP addresses in disguise (number forms), spaces, control and non-ASCII characters and IPv6 zone IDs are refused; a stored address that fails the rule disables installations (notice names the reason) |
| Settings tab | hint under the download base URL names the reason while typing and judges the typed text like the server |
| Documentation | README for the public repository, app README, Quick start, Requirements, Download base URL, FAQ, Known limitations, Support, `CONTRIBUTING.md`, issue forms, PR template, screenshots in `docs/images/` |

## Results

| Check | Result |
|---|---|
| `tools/dev/check.sh full`: ruff, mypy --strict, build check, version 3.6.1, pytest | passed, 421 tests |
| UI full | ui_test 210/210; ui_features 733/733 (download_url_hint 26, setting_help 26, download_url_notice 65, firmware_store 526, store_transfer 90); layout gate (capture) 28/28 |
| Address rule | 71 shared cases (`tests/download_url_cases.json`) checked by pytest (server) and the UI check (hint); HTTP tests: saving refused, install / downgrade / confirm answer 409 with a stored bad address |
| Security review | rc1 reviewed: bypass by whitespace, control and Unicode characters found and fixed in rc2 (allow-list); re-review of rc2 without new findings |
| CVE check (2026-11-21) | PyPI lists no known vulnerability for the ten pinned packages; base image tag `python:3.12.16-alpine3.24` still resolves to the pinned digest `sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89`; OSV not queried (blocked by the proxy) |
| Docs check (seven points) | changelog bullets match `DOCS.md`; options in `config.yaml`, `translations/en.yaml`, Settings tab and Configuration agree; feature list identical in both READMEs; minimum HA 2026.10 and architectures agree; Credits table matches `requirements.txt`; all relative links and anchors resolve; fixed content and section order unchanged |
| Screenshots | `qa/screenshots/3.6.0/` kept (no pictured view changed); `docs/images/` current |

## Live tests through the beta channel

- rc2 tested in Home Assistant by the maintainer (2026-11-21): update, rejected
  addresses (also with spaces), LAN IP and host name accepted, documentation visible: passed.
- Not tested live: the restart by the Watchdog when the health check fails (no Docker
  access; DOCS word it as intended); item 12 (one download for several devices,
  needs new firmware).

## Not tested here

- Real Home Assistant / Ingress / Supervisor, container build, real Shelly devices,
  non-Chromium browsers; the "add repository" badge on a real instance (after the
  repository is public).
