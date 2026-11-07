"""Idle resource comparison 2.2.0 (port 8803) vs 2.2.1 candidate (port 8801).

For each version: page open and visible for 65 s, then "hidden" for 65 s (document
visibility overridden + visibilitychange dispatched, as when HA puts the panel in the
background). Counts /api requests, JS heap, server CPU time and RSS."""

import json
import threading

import psutil
from playwright.sync_api import sync_playwright

CHROME = "/home/user/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
WINDOW_MS = 65000


def server_proc(port):
    """psutil process of the demo server on a port."""
    for p in psutil.process_iter(["cmdline"]):
        if p.info["cmdline"] and "demo.py" in " ".join(p.info["cmdline"]) and str(port) in p.info["cmdline"]:
            return p
    raise RuntimeError(port)


def measure(pw, port, out):
    browser = pw.chromium.launch(executable_path=CHROME)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "/api/" in r.url else None)
    proc = server_proc(port)
    page.goto(f"http://127.0.0.1:{port}/ui/")
    page.wait_for_selector("#dev-cards table")
    page.wait_for_timeout(2000)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")
    res = {}
    for phase in ("visible", "hidden"):
        if phase == "hidden":
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
              Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
              document.dispatchEvent(new Event('visibilitychange')); }""")
        n0, c0 = len(reqs), proc.cpu_times()
        page.wait_for_timeout(WINDOW_MS)
        c1 = proc.cpu_times()
        m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
        res[phase] = {"api_requests": len(reqs) - n0,
                      "server_cpu_ms": round((c1.user + c1.system - c0.user - c0.system) * 1000),
                      "server_rss_mb": round(proc.memory_info().rss / 1048576, 1),
                      "js_heap_mb": round(m["JSHeapUsedSize"] / 1048576, 2), "dom_nodes": int(m["Nodes"]),
                      "js_listeners": int(m["JSEventListeners"])}
    resp_size = page.evaluate("async () => (await (await fetch('api/state')).text()).length")
    res["state_bytes"] = resp_size
    out[port] = res
    browser.close()


out = {}
with sync_playwright() as pw:
    # sequential per playwright instance is simplest; run both versions in threads
    pass


def runner(port):
    with sync_playwright() as pw:
        measure(pw, port, out)


threads = [threading.Thread(target=runner, args=(p,)) for p in (8803, 8801)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(json.dumps({"2.2.0": out.get(8803), "2.2.1": out.get(8801)}, indent=1))
