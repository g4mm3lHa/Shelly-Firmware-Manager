"""Screenshots of chosen UI views for test reports and mockup proposals. Not part of
the app; replaces ad-hoc capture scripts.

Starts its own demo (qa/demo.py of the chosen repository copy) on a free port,
captures every shot at 1440 px (@1x) and 412 px (@2x, touch), stops the demo.

Usage (repository root, pinned dependencies):
  tools/dev/run.sh python3 qa/capture.py OUT_DIR [options] [--shot NAME=STEPS ...]

Options:
  --root DIR          repository copy to serve (default: this repository) – for
                      mockups: copy the repository, change ui-src, build, pass the copy
  --scenario NAME     demo scenario: idle (default), busy, legacy, missing, features
  --scheme light|dark colour scheme (default light)
  --widths LIST       comma-separated widths (default 1440,412; widths < 700 use @2x
                      and touch)
  --route PAT=FILE    answer requests matching the glob PAT with the JSON of FILE
                      (e.g. device dialogs: '**/settings/refresh=live.json'); repeatable
  --shot NAME=STEPS   one screenshot NAME (file <width>-<NAME>.png) after the steps,
                      separated by ';':
                        tab:<devices|firmware|logs|settings>  switch tab
                        click:<css>   mouse click (first match)
                        tap:<css>     touch tap (phones; falls back to click)
                        hover:<css>   mouse hover
                        fill:<css>=<text>  fill an input
                        wait:<ms>     wait
                        full          full-page screenshot instead of the viewport
                      Every shot starts on a freshly loaded page. Without --shot the
                      four tabs are captured (devices, firmware, logs, settings).

Example (Firmware tab and upload dialog):
  tools/dev/run.sh python3 qa/capture.py /tmp/shots --shot firmware=tab:firmware \\
      --shot upload='tab:firmware;click:#fw-upload'
"""

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from common import launch
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHOTS = {tab: f"tab:{tab}" for tab in ("devices", "firmware", "logs", "settings")}


def free_port():
    """A free local TCP port for the demo."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_demo(root, port, scenario):
    """Start qa/demo.py of the repository copy and wait until it accepts connections."""
    proc = subprocess.Popen(
        [sys.executable, str(root / "qa" / "demo.py"), str(root / "shelly_firmware_manager" / "app"), str(port), scenario],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc
        time.sleep(0.2)
    proc.terminate()
    raise SystemExit(f"demo on port {port} did not start")


def run_steps(page, steps, touch):
    """Apply the steps of one shot; returns True if a full-page screenshot is wanted."""
    full = False
    for step in filter(None, (s.strip() for s in steps.split(";"))):
        kind, _, arg = step.partition(":")
        if kind == "tab":
            page.click(f"#tab-{arg}")
        elif kind == "click":
            page.locator(arg).first.click()
        elif kind == "tap":
            target = page.locator(arg).first
            target.tap() if touch else target.click()
        elif kind == "hover":
            page.locator(arg).first.hover()
        elif kind == "fill":
            selector, _, text = arg.partition("=")
            page.locator(selector).first.fill(text)
        elif kind == "wait":
            page.wait_for_timeout(int(arg))
        elif kind == "full":
            full = True
        else:
            raise SystemExit(f"unknown step: {step}")
        page.wait_for_timeout(300)  # let transitions and touch scroll events settle
    return full


def main():
    """Parse the arguments, start the demo, capture all shots, stop the demo."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--scenario", default="idle")
    ap.add_argument("--scheme", default="light", choices=("light", "dark"))
    ap.add_argument("--widths", default="1440,412")
    ap.add_argument("--route", action="append", default=[])
    ap.add_argument("--shot", action="append", default=[])
    args = ap.parse_args()
    shots = dict(s.split("=", 1) for s in args.shot) if args.shot else DEFAULT_SHOTS
    routes = [(pat, json.loads(Path(file).read_text())) for pat, file in (r.split("=", 1) for r in args.route)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    demo = start_demo(args.root.resolve(), port, args.scenario)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = launch(pw)
            for width in (int(w) for w in args.widths.split(",")):
                touch = width < 700
                ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=args.scheme,
                                          device_scale_factor=2 if touch else 1, has_touch=touch, is_mobile=touch)
                for name, steps in shots.items():
                    page = ctx.new_page()
                    page.on("pageerror", lambda e, n=name, w=width: errors.append(f"{w}-{n}: {e}"))
                    for pat, body in routes:
                        page.route(pat, lambda r, b=body: r.fulfill(json=b))
                    page.goto(f"http://127.0.0.1:{port}/ui/")
                    page.wait_for_timeout(800)
                    full = run_steps(page, steps, touch)
                    path = out / f"{width}-{name}.png"
                    page.screenshot(path=path, full_page=full)
                    print(path)
                    page.close()
                ctx.close()
            browser.close()
    finally:
        demo.terminate()
        demo.wait(timeout=10)
    for error in errors:
        print("PAGE ERROR", error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
