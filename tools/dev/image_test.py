#!/usr/bin/env python3
"""Start test of a built app image (development helper, not shipped).

Usage (repository root, Docker running): tools/dev/image_test.py <image>

The backend tests run on the development machine: another C library, the pure Python
fallbacks of the packages, no image. This test starts the image itself the way the
Supervisor does – /data with an options.json holding the defaults of config.yaml and a
SUPERVISOR_TOKEN – but without Home Assistant, and checks what only the image shows:

- the app starts and reports "ready", both servers answer, nothing is logged as an error
- the C extensions of the bundled packages are loaded (wheels fit the image)
- the bytecode compiled at build time is used, and no package tooling (pip) is left
- a stop ends the app cleanly with exit code 0 within the stop time of the Supervisor

Nothing leaves the machine: the container gets no published port and the app only
tries to reach the Supervisor, which does not exist here. Exit code 0 if every check
passed; the log of the container is printed if one failed.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "shelly_firmware_manager" / "config.yaml"
NAME = "sfm-image-test"
START_TIMEOUT_S = 40  # the image must report "ready" within this time
STOP_TIMEOUT_S = 10  # stop time the Supervisor grants an app

# Runs inside the container: the compiled modules of the packages must be importable
# (a wheel for another platform or Python falls back to pure Python or fails here)
EXTENSIONS = """
import aiohttp._http_parser, aiohttp._http_writer, aiohttp._websocket.mask, aiohttp._websocket.reader_c
import frozenlist._frozenlist, multidict._multidict, propcache._helpers_c, yarl._quoting_c
"""

# Runs inside the container: both servers answer (any HTTP status: the UI refuses every
# address but the one of Ingress, the download server serves nothing without an update)
SERVERS = """
import urllib.error, urllib.request
for port in (8099, 8098):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
    except urllib.error.HTTPError:
        pass
"""


def scalar(text: str) -> object:
    """A value of the options block: true/false, a whole number or a string."""
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text.strip("\"'")


def default_options() -> dict[str, object]:
    """The block "options:" of config.yaml as the Supervisor writes it to
    /data/options.json. It reads only the forms that block uses (key: value and a list of
    small objects) and stops with an error at anything else."""
    options: dict[str, object] = {}
    items: list[dict[str, object]] | None = None
    block = CONFIG.read_text().split("\noptions:\n", 1)[1].split("\nschema:", 1)[0]
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if m := re.fullmatch(r"  (\w+): (.+)", line):
            options[m[1]] = scalar(m[2])
            items = None
        elif m := re.fullmatch(r"  (\w+):", line):
            items = []
            options[m[1]] = items
        elif items is not None and (m := re.fullmatch(r"    - (\w+): (.+)", line)):
            items.append({m[1]: scalar(m[2])})
        elif items and (m := re.fullmatch(r"      (\w+): (.+)", line)):
            items[-1][m[1]] = scalar(m[2])
        else:
            raise SystemExit(f"config.yaml: this line of the options is not understood: {line!r}")
    return options


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a docker command and return its result with the output as text."""
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def in_container(*command: str) -> subprocess.CompletedProcess[str]:
    """Run a command inside the started container."""
    return docker("exec", NAME, *command, check=False)


def logs() -> str:
    """Everything the app has logged so far."""
    result = docker("logs", NAME, check=False)
    return result.stdout + result.stderr


def main() -> int:
    """Start the image, run the checks, stop it. Returns the exit code."""
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    image, failed = sys.argv[1], []

    def check(name: str, ok: bool, detail: str = "") -> None:
        """Print one result and remember a failure."""
        print(f"{'ok  ' if ok else 'FAIL'}  {name}{'' if ok or not detail else ': ' + detail.strip()[-600:]}")
        if not ok:
            failed.append(name)

    with tempfile.TemporaryDirectory() as data:
        Path(data).chmod(0o755)
        (Path(data) / "options.json").write_text(json.dumps(default_options()))
        docker("rm", "-f", NAME, check=False)
        docker("run", "-d", "--name", NAME, "-e", "SUPERVISOR_TOKEN=image-test", "-v", f"{data}:/data", image)
        try:
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline and " ready " not in logs():
                if docker("inspect", "-f", "{{.State.Running}}", NAME).stdout.strip() != "true":
                    break
                time.sleep(0.5)
            check("the app starts and reports ready", " ready " in logs())
            result = in_container("python3", "-c", SERVERS)
            check("UI server and download server answer", result.returncode == 0, result.stderr)
            result = in_container("python3", "-c", EXTENSIONS)
            check("C extensions of the packages are loaded", result.returncode == 0, result.stderr)
            result = in_container("python3", "-v", "-c", "import aiohttp, manager")
            compiled = ("/app/__pycache__/manager.cpython-312.pyc", "/deps/aiohttp/__pycache__/__init__.cpython-312.pyc")
            used = all(f"{file} matches" in result.stderr for file in compiled)
            check("bytecode compiled at build time is used", result.returncode == 0 and used)
            check("no package tooling in the image", in_container("python3", "-c", "import pip").returncode != 0)
            started = time.monotonic()
            docker("stop", "-t", str(STOP_TIMEOUT_S), NAME)
            code = docker("inspect", "-f", "{{.State.ExitCode}}", NAME).stdout.strip()
            check("a stop ends the app cleanly", code == "0" and time.monotonic() - started < STOP_TIMEOUT_S, f"exit code {code}")
            log = logs()
            check("nothing logged as an error", not re.search(r"Traceback| ERROR | CRITICAL ", log))
            if failed:
                print("---- log of the container ----\n" + log)
        finally:
            docker("rm", "-f", NAME, check=False)
    print("image test passed" if not failed else f"image test FAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
