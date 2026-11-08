#!/bin/sh
# Workspace helper (not shipped): run a command with the app's pinned dependencies
# taken from the bundled wheels, without installing anything. The noarch wheels and
# the x86_64 musl wheels are extracted once to .devdeps/ (gitignored); their native
# .so files cannot load on a glibc host or another Python than 3.12, so they are
# removed and aiohttp, yarl, multidict, frozenlist and propcache use their pure-Python
# fallbacks. Same versions as in the image, but without the C extensions.
# Usage (repository root): tools/dev/run.sh python3 -m pytest ...
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEPS="$ROOT/.devdeps"
if [ ! -d "$DEPS/aiohttp" ]; then
  mkdir -p "$DEPS"
  for w in "$ROOT"/shelly_firmware_manager/wheels/*py3-none-any.whl \
           "$ROOT"/shelly_firmware_manager/wheels/*musllinux_1_2_x86_64.whl; do
    python3 -I -m zipfile -e "$w" "$DEPS" >/dev/null
  done
  find "$DEPS" -name '*.so' -delete
fi
export PYTHONPATH="$DEPS:$ROOT/tools/dev${PYTHONPATH:+:$PYTHONPATH}"
export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 \
       FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1 PYTHONDONTWRITEBYTECODE=1
exec "$@"
