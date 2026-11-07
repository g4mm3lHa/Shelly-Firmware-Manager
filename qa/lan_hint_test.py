"""rc4/rc6 UI check: hints for download_base_url in the Settings tab.

Needs demo.py on port 8801 (idle). Usage: python qa/lan_hint_test.py <shots-dir>
Checks at 360 px (light, embedded) and 1440 px (dark): no hint for the demo's LAN
IP, the "not local" hint for a public IP and for localhost, the host-name hint (rc6,
R2) for a public name and for a .local name, no horizontal overflow, no console
errors. Saves screenshots of the hints."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8801"
CHROME = "/home/user/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
results = []


def check(name, ok, detail=""):
    """Record one check."""
    results.append((name, bool(ok), detail))


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME)
    for width, scheme, embedded in ((360, "light", True), (1440, "dark", False)):
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
        # failed requests are reported with their URL; the demo has no favicon (not part of the app)
        page.on("response", lambda r, errs=errors: errs.append(f"HTTP {r.status} {r.url}")
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
        page.on("console", lambda m, errs=errors: errs.append(m.text)
                if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
        page.goto(BASE + ("/wrap" if embedded else "/ui/"))
        page.wait_for_timeout(500)
        f = page.frames[1] if embedded else page
        f.click("#tab-settings")
        f.wait_for_selector("#opt-download_base_url")
        case = f"{width}-{scheme}{'-embedded' if embedded else ''}"
        warn = f.locator("#opt-download_base_url-warn")
        check(f"{case}: no hint for the LAN URL", warn.is_hidden())
        f.fill("#opt-download_base_url", "http://203.0.113.10:8098")
        check(f"{case}: hint for a public IP", warn.is_visible() and "local network" in warn.text_content())
        described = f.get_attribute("#opt-download_base_url", "aria-describedby") or ""
        check(f"{case}: hint linked for screen readers", "opt-download_base_url-warn" in described, described)
        overflow = f.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(f"{case}: no horizontal overflow", overflow <= 0, f"{overflow}px")
        f.locator("#opt-download_base_url").scroll_into_view_if_needed()
        page.screenshot(path=OUT / f"lan-hint-{case}.png")
        f.fill("#opt-download_base_url", "http://localhost:8098")
        check(f"{case}: hint for localhost", warn.is_visible())
        f.fill("#opt-download_base_url", "http://homeassistant.local:8098")
        check(f"{case}: host-name hint for a .local name", warn.is_visible() and "host name" in warn.text_content())
        f.locator("#opt-download_base_url").scroll_into_view_if_needed()
        page.screenshot(path=OUT / f"host-name-hint-{case}.png")
        f.fill("#opt-download_base_url", "https://example.com:8098")
        check(f"{case}: host-name hint for a public name", warn.is_visible() and "host name" in warn.text_content())
        f.fill("#opt-download_base_url", "http://192.168.1.20:8098")
        check(f"{case}: no hint for a LAN IP", warn.is_hidden())
        check(f"{case}: no console errors", not errors, str(errors[:2]))
        ctx.close()
    browser.close()

bad = [r for r in results if not r[1]]
print(f"{len(results) - len(bad)}/{len(results)} passed")
for name, _ok, detail in bad:
    print("FAIL", name, detail)
sys.exit(1 if bad else 0)
