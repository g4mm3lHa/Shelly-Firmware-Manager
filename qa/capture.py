"""Screenshots of chosen UI views for test reports and mockup proposals, and the
layout gate of the final release. Not part of the app.

Starts its own demo (qa/demo.py of the chosen repository copy) on a free port,
captures every shot in every viewport, stops the demo. Every shot is checked for page
errors and horizontal overflow; the exit code is 1 if any shot failed.

Usage (repository root, pinned dependencies):
  tools/dev/run.sh python3 qa/capture.py OUT_DIR [options] [--shot NAME=STEPS ...]
  tools/dev/run.sh python3 qa/capture.py OUT_DIR --gate      (final gate, run_ui full)

Options:
  --root DIR          repository copy to serve (default: this repository) – for
                      mockups: copy the repository, change ui-src, build, pass the copy
  --scenario NAME     demo scenario: idle (default), busy, legacy, missing, features, first
  --scheme light|dark colour scheme (default light)
  --widths LIST       comma-separated widths (default 1440,412; widths < 700 use @2x
                      and touch)
  --embedded          load the app inside the HA-like wrapper (iframe, like Ingress)
  --long-data         long device names, extra versions, a Gen1 file and a pinned
                      certificate in the API answers (layout stress)
  --route PAT=FILE    answer requests matching the glob PAT with the JSON of FILE
                      (e.g. device dialogs: '**/settings/refresh=live.json'); repeatable
  --gate              layout gate: 360/430 px light/dark, 390 px embedded, 1440 px
                      light/dark with --long-data; Devices (phones: details open),
                      Firmware, Schedules, Settings and Settings with an unsaved change
  --shot NAME=STEPS   one screenshot NAME (file <case>-<NAME>.png) after the steps,
                      separated by ';':
                        tab:<devices|firmware|schedules|logs|settings>  switch tab
                        click:<css>   mouse click (first match; Playwright selectors,
                                      e.g. 'td.c-details summary >> nth=1')
                        tap:<css>     touch tap (phones; falls back to click)
                        hover:<css>   mouse hover
                        fill:<css>=<text>  fill an input
                        wait:<ms>     wait
                        full          full-page screenshot instead of the viewport
                        tall          viewport as tall as the page (sticky bars at
                                      their natural place)
                      Every shot starts on a freshly loaded page. Without --shot the
                      five tabs are captured (devices, firmware, schedules, logs,
                      settings).

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
DEFAULT_SHOTS = {tab: f"tab:{tab}" for tab in ("devices", "firmware", "schedules", "logs", "settings")}
GATE_CASES = ((360, "dark", False), (360, "light", False), (430, "dark", False), (430, "light", False),
              (390, "dark", True), (1440, "dark", False), (1440, "light", False))
GATE_SHOTS = {
    "devices": "full",
    "firmware": "tab:firmware;click:#fw-cards details summary;full",
    "schedules": "tab:schedules;full",
    "settings": "tab:settings;tall;full",
    "settings-unsaved": "tab:settings;fill:#opt-check_interval_days=5",
}
GATE_PHONE_DEVICES = "click:td.c-details summary >> nth=1;full"  # one tile with open details
LONG = "Living room ceiling light with a really long device name over the sofa"
# Horizontal overflow: the page scrolls sideways, or a visible element of the shown
# view ends right of the viewport (content of a scroll/clip container that itself fits,
# e.g. the chip bar, is fine)
OVERFLOW_JS = """() => {
  const clipped = e => { for (let p = e.parentElement; p; p = p.parentElement) {
    if (getComputedStyle(p).overflowX !== 'visible') return p.getBoundingClientRect().right <= innerWidth + 0.5; }
    return false; };
  const out = [...document.querySelectorAll('[id^="view-"]:not([hidden]) *')].filter(e => {
    const r = e.getBoundingClientRect(); return r.width > 0 && r.right > innerWidth + 0.5 && !clipped(e); });
  return {page: document.documentElement.scrollWidth > innerWidth,
          elements: out.slice(0, 5).map(e => e.tagName + '.' + e.className)}; }"""


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


def long_firmware(route):
    """Firmware list with long device names, two extra versions and a Gen1 file."""
    r = route.fetch()
    d = r.json()
    base = d["firmware"][0]

    def dev(n, name):
        return {"id": f"{n:032x}", "name": name}  # devices carry their ID (Install button)

    base["devices"] = [*base["devices"], dev(1, LONG), dev(3, "Garage-door-controller-north-entrance-2nd-floor")]
    for i, version in enumerate(["1.4.0", "1.3.2"]):
        d["firmware"].append({**base, "name": f"{i}{base['name'][1:]}", "version": version,
                              "devices": [] if i else [dev(2, "Hall")], "deletable": i == 0, "sha256": "f" * 64})
    d["firmware"].append({**base, "name": "g1" + base["name"][2:], "type": "SHSW-1", "gen": 1, "https": False,
                          "version": "20230913-112003/v1.14.0-gcb84623", "devices": [dev(8, "Garden plug"), dev(18, "Kids room")]})
    route.fulfill(response=r, body=json.dumps(d), headers={**r.headers, "etag": "x"})


def long_settings(route):
    """Settings with a pinned certificate (long fingerprint)."""
    r = route.fetch()
    d = r.json()
    d["pinned_certificates"] = [{"host": "updates.shelly.cloud", "sha256": ":".join(["AB", "CD", "EF", "01", "23", "45", "67", "89"] * 4)}]
    route.fulfill(response=r, body=json.dumps(d))


def answer_json(body):
    """Route handler answering with a fixed JSON body (one argument: Playwright passes the
    request as second argument to handlers that accept two)."""
    return lambda route: route.fulfill(json=body)


def run_steps(page, f, steps, touch):
    """Apply the steps of one shot (f = app frame); returns True for a full-page shot."""
    full = False
    for step in filter(None, (s.strip() for s in steps.split(";"))):
        kind, _, arg = step.partition(":")
        if kind == "tab":
            f.click(f"#tab-{arg}")
        elif kind == "click":
            f.locator(arg).first.click()
        elif kind == "tap":
            target = f.locator(arg).first
            target.tap() if touch else target.click()
        elif kind == "hover":
            f.locator(arg).first.hover()
        elif kind == "fill":
            selector, _, text = arg.partition("=")
            f.locator(selector).first.fill(text)
        elif kind == "wait":
            page.wait_for_timeout(int(arg))
        elif kind == "full":
            full = True
        elif kind == "tall":
            page.set_viewport_size({"width": page.viewport_size["width"], "height": f.evaluate("document.documentElement.scrollHeight")})
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
    ap.add_argument("--embedded", action="store_true")
    ap.add_argument("--long-data", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--route", action="append", default=[])
    ap.add_argument("--shot", action="append", default=[])
    args = ap.parse_args()
    if args.gate:
        cases, shots, long_data = GATE_CASES, GATE_SHOTS, True
    else:
        cases = tuple((int(w), args.scheme, args.embedded) for w in args.widths.split(","))
        shots = dict(s.split("=", 1) for s in args.shot) if args.shot else DEFAULT_SHOTS
        long_data = args.long_data
    routes = [(pat, json.loads(Path(file).read_text())) for pat, file in (r.split("=", 1) for r in args.route)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    demo = start_demo(args.root.resolve(), port, args.scenario)
    problems = []
    try:
        with sync_playwright() as pw:
            browser = launch(pw)
            for width, scheme, embedded in cases:
                touch = width < 700
                ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme,
                                          device_scale_factor=2 if touch else 1, has_touch=touch)
                tag = f"{width}" + (f"-{scheme}" if args.gate else "") + ("-embedded" if embedded else "")
                for name, steps in shots.items():
                    if args.gate and name == "devices" and touch:
                        steps = GATE_PHONE_DEVICES
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
                    if long_data:
                        page.route("**/api/firmware", long_firmware)
                        page.route("**/api/settings", long_settings)
                    for pat, body in routes:
                        page.route(pat, answer_json(body))
                    page.goto(f"http://127.0.0.1:{port}" + ("/wrap" if embedded else "/ui/"))
                    f = page.wait_for_selector("iframe").content_frame() if embedded else page
                    f.wait_for_selector("#dev-cards table")
                    full = run_steps(page, f, steps, touch) and not embedded  # an iframe scrolls inside
                    path = out / f"{tag}-{name}.png"
                    page.screenshot(path=path, full_page=full)
                    overflow = f.evaluate(OVERFLOW_JS)
                    if errors or overflow["page"] or overflow["elements"]:
                        problems.append(f"{tag}-{name}: errors {errors[:2]} overflow {overflow}")
                    print(path)
                    page.close()
                ctx.close()
            browser.close()
    finally:
        demo.terminate()
        demo.wait(timeout=10)
    for problem in problems:
        print("FAIL", problem)
    total = len(cases) * len(shots)
    print(f"capture: {total - len(problems)}/{total} passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
