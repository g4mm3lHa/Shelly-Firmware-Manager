"""Start the demo servers, run the UI checks, stop the demos – one command.

Usage (repository root): python qa/run_ui.py [quick|full] [shots-dir]
  quick (default, per UI change): ui_test.py quick matrix + ui_features.py
  full  (final gate):             ui_test.py --full + ui_features.py + mobile_capture.py
The demos run with the same interpreter and environment (tools/dev/run.sh sets the
pinned dependencies). Prints one result line per suite; exit code 1 if any failed."""

import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "shelly_firmware_manager" / "app"
DEMOS = {8801: "idle", 8802: "busy", 8803: "legacy", 8804: "missing", 8805: "rc7"}
MOBILE_DEMO = {8711: "idle"}


def wait_for(port, timeout=30):
    """Wait until a demo accepts connections on port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise SystemExit(f"demo on port {port} did not start")


def run(script, *args):
    """Run one qa script; returns (exit code, last output line)."""
    proc = subprocess.run([sys.executable, str(ROOT / "qa" / script), *args], capture_output=True, text=True)
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    for line in lines:
        if line.startswith(("FAIL", "Traceback")) or " passed" in line:
            print("   ", line[:300])
    return proc.returncode, lines[-1] if lines else ""


def main():
    """Start demos, run the suites of the mode, stop demos."""
    mode = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if mode not in ("quick", "full"):
        raise SystemExit(__doc__)
    # An empty argument (check.sh without a folder) also means: temporary folder
    shots = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else tempfile.mkdtemp(prefix="sfm-shots-")
    demos = {**DEMOS, **(MOBILE_DEMO if mode == "full" else {})}
    procs = [
        subprocess.Popen([sys.executable, str(ROOT / "qa" / "demo.py"), str(APP), str(port), scenario],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for port, scenario in demos.items()
    ]
    failed = 0
    try:
        for port in demos:
            wait_for(port)
        suites = [("ui_test.py", shots, *(["--full"] if mode == "full" else [])), ("ui_features.py", shots)]
        if mode == "full":
            suites.append(("mobile_capture.py", shots))
        for script, *args in suites:
            print(script)
            code, _last = run(script, *args)
            failed |= code != 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=10)
    print(f"screenshots: {shots}")
    print("UI checks FAILED" if failed else "UI checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
