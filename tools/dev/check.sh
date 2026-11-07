#!/bin/sh
# Workspace helper (not shipped): all checks in the efficient order, fastest first;
# stops at the first failing step. Usage (repository root):
#   tools/dev/check.sh            lint + build check + all backend tests
#   tools/dev/check.sh ui         ... + UI quick matrix and feature suites
#   tools/dev/check.sh full       ... + full UI matrix and mobile captures (final gate)
#   tools/dev/check.sh build      rebuild app/static from ui-src first, then as default
# Uses the installed pytest/ruff/playwright and, offline, the helpers next to this
# file (pinned wheels, asyncio shim if pytest-asyncio is missing, esbuild stand-in).
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEV="$ROOT/tools/dev"
cd "$ROOT"
MODE=${1:-tests}
# Offline: use the local esbuild through the npx stand-in when it is installed
[ -x "${ESBUILD_BIN:-/opt/npm-tools/node_modules/esbuild/bin/esbuild}" ] && export PATH="$DEV/bin:$PATH"
PYTEST_PLUGIN=""
python3 -c "import pytest_asyncio" 2>/dev/null || PYTEST_PLUGIN="-p asyncio_shim"

step() { printf '== %s\n' "$1"; }
if [ "$MODE" = "build" ]; then
  step "build UI"; python3 tools/build_ui.py; MODE=tests
fi
step "ruff"
ruff check --select E,F,W,B,S,ASYNC,UP,SIM,BLE --line-length 120 shelly_firmware_manager/app
ruff check --select F,B --line-length 120 tests tools qa
step "build check"; python3 tools/build_ui.py --check
step "version"
python3 - <<'PY'
import re, pathlib
cfg = re.search(r'^version: "([^"]+)"', pathlib.Path("shelly_firmware_manager/config.yaml").read_text(), re.M).group(1)
code = re.search(r'^VERSION = "([^"]+)"', pathlib.Path("shelly_firmware_manager/app/main.py").read_text(), re.M).group(1)
assert cfg == code, f"config.yaml {cfg} != main.py {code}"
print(f"{cfg} (config.yaml = main.py)")
PY
step "pytest"
"$DEV/run.sh" python3 -m pytest -c tests/pytest.ini $PYTEST_PLUGIN tests -q -p no:cacheprovider
case "$MODE" in
  ui) step "UI quick"; "$DEV/run.sh" python3 qa/run_ui.py quick "${2:-}" ;;
  full) step "UI full"; "$DEV/run.sh" python3 qa/run_ui.py full "${2:-}" ;;
esac
step "all checks passed"
