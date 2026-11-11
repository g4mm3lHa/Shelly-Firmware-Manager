# tools/dev – workspace helpers (not part of the app)

Only `shelly_firmware_manager/` is built into the image; nothing here is shipped.

| File | Purpose |
|---|---|
| `check.sh [tests\|ui\|full\|build]` | all checks in the efficient order: ruff → mypy (if installed; required for a release) → build check → version match (config.yaml = main.py) → pytest → UI (`ui`: quick, `full`: final gate); `build` rebuilds `app/static` first |
| `run.sh <cmd>` | runs a command with the pinned dependencies from the bundled wheels (extracted once to `.devdeps/`, pure-Python mode, no install) |
| `asyncio_shim.py` | stand-in for pytest-asyncio's auto mode where it is not installed (`-p asyncio_shim`, used by `check.sh` automatically) |
| `bin/npx` | offline stand-in for `npx --yes esbuild@0.28.2`: runs a local esbuild of exactly that version (`ESBUILD_BIN` overrides the path) |

Typical use (repository root):

```
tools/dev/check.sh            # after any backend change (seconds)
tools/dev/check.sh build      # after a ui-src change: rebuild, then check
tools/dev/check.sh ui         # after a UI change: + quick UI matrix and feature checks
tools/dev/check.sh full       # final gate: + full UI matrix and mobile captures
```

Limits of the offline mode: Python of the host instead of 3.12.16 and no C
extensions of aiohttp/yarl/multidict/frozenlist/propcache (same versions otherwise).
The UI checks need Playwright for Python and a Chromium; `qa/common.py` finds it
(`SFM_CHROME` overrides).
