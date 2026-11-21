# Contributing

Thank you for your interest in the Firmware Manager for Shelly.

## Bugs and feature requests

Please use the [issue forms](https://github.com/g4mm3lHa/Shelly-Firmware-Manager/issues/new/choose).
Security problems are reported privately – see [`SECURITY.md`](SECURITY.md).

## Pull requests

Pull requests are welcome **after the change was agreed on in an issue**. Please open
or pick an issue first and wait for an answer before you start; this avoids work on
changes that do not fit the project.

Rules of the project:

- **Local only.** The app connects only to Shelly devices in the private network, to
  the Shelly servers below `shelly.cloud` and to Home Assistant. No other outbound
  connection, no CDN, no fonts or scripts from the internet, no telemetry.
- **Security is never weakened:** private-address checks, certificate pinning, CSRF
  protection, content security policy, body limits, no redirects, no secrets in the
  API, the state or the logs.
- **Everything in English:** code, comments, UI texts, documentation, commit messages.
- Code is commented and documented; comments describe the current behaviour.
- Dependencies are pinned by hash and bundled as wheels; a new dependency needs a good
  reason and an entry in the credits of `DOCS.md`.
- Every change a user notices gets an entry in
  `shelly_firmware_manager/CHANGELOG.md` and, where needed, in `DOCS.md`.
- All checks pass (see below); new behaviour comes with tests.

## Layout

| Path | Content |
|---|---|
| `shelly_firmware_manager/` | Only what Home Assistant needs: app definition, `README.md`/`DOCS.md`/`CHANGELOG.md` for the app store, Dockerfile, AppArmor profile, pinned wheels, `app/` with the Python sources and the built UI |
| `ui-src/` | UI sources (ES modules in `js/`, `style.css`) |
| `tests/` | Backend tests by area; all network access is mocked |
| `qa/` | UI checks (Playwright), demo servers with fake data, screenshots of the current version |
| `tools/` | `build_ui.py` and the development helpers in `dev/` |
| `docs/` | Code map (`CODEMAP.md`, `codemap/`), approved UI details (`UI_DECISIONS.md`), images of the README (`images/`) |
| `candidates/` | Test report of the current release |

Start with `docs/CODEMAP.md`: it maps modules, stored data, API routes and tests.
`docs/UI_DECISIONS.md` lists UI details that were decided on purpose – please do not
change them without an issue.

## Building and checking

The files in `shelly_firmware_manager/app/static/` (`app.js`, `style.css`) are
generated: `tools/build_ui.py` bundles and minifies `ui-src/` with the pinned esbuild
0.28.2. Never edit them by hand.

One command runs the checks in the efficient order (lint, type check, build check,
version match, backend tests, optionally the UI checks against the demo servers):

```
tools/dev/check.sh          # lint + build check + backend tests (seconds)
tools/dev/check.sh build    # rebuild app/static from ui-src first
tools/dev/check.sh ui       # ... + UI quick matrix and feature checks (about a minute)
tools/dev/check.sh full     # ... + full UI matrix, all feature viewports and the layout gate
```

`tools/dev/` also works offline with the bundled wheels (see `tools/dev/README.md`).
Tool settings (ruff, mypy, pytest) are in `pyproject.toml`. The same checks run in the
CI workflow on every push and pull request.
