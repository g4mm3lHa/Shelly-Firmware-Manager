"""rc6 UI check: notice for a missing / unconfirmed download base URL and the device
progress of a Gen2+ installation.

Needs demo.py on port 8803 (legacy) and 8804 (missing).
Usage: python qa/rc6_ui_test.py <shots-dir>
Cases: 360 px light embedded, 430 px dark, 1440 px dark and light. Checks: notice with
message and buttons, no install selection while installations are disabled, the
progress in the status pill, confirming the former example value removes the notice
(last case only, it changes the demo state), no horizontal overflow, no console
errors. Saves screenshots."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
OUT.mkdir(parents=True, exist_ok=True)
LEGACY, MISSING = "http://127.0.0.1:8803", "http://127.0.0.1:8804"
CHROME = "/home/user/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
results = []


def check(name, ok, detail=""):
    """Record one check."""
    results.append((name, bool(ok), detail))


def open_page(browser, base, width, scheme, embedded):
    """New page on the demo; returns (context, page, frame, errors)."""
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme,
                              has_touch=width <= 700)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
    page.on("response", lambda r, errs=errors: errs.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
    page.on("console", lambda m, errs=errors: errs.append(m.text)
            if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.goto(base + ("/wrap" if embedded else "/ui/"))
    page.wait_for_timeout(500)
    frame = page.frames[1] if embedded else page
    frame.wait_for_selector("#dev-cards table")
    return ctx, page, frame, errors


OVERFLOW_JS = "document.documentElement.scrollWidth - document.documentElement.clientWidth"

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME)
    cases = ((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False))
    for index, (width, scheme, embedded) in enumerate(cases):
        case = f"{width}-{scheme}{'-embedded' if embedded else ''}"
        # Former example value, not confirmed
        ctx, page, f, errors = open_page(browser, LEGACY, width, scheme, embedded)
        notice = f.locator("#notice")
        check(f"{case}: notice shown", notice.is_visible() and "former example value" in notice.text_content())
        buttons = f.locator("#notice button").all_text_contents()
        check(f"{case}: confirm and settings buttons", buttons == ["This address is correct", "Open settings"], str(buttons))
        check(f"{case}: no install selection", f.locator("td.c-select input").count() == 0)
        pills = f.locator("button.pill").all_text_contents()
        check(f"{case}: device progress in the status pill", "Installing 55 %" in pills, str(sorted(set(pills))))
        check(f"{case}: no horizontal overflow", f.evaluate(OVERFLOW_JS) <= 0)
        page.screenshot(path=OUT / f"notice-legacy-{case}.png")
        f.locator("#notice button", has_text="Open settings").click()
        page.wait_for_timeout(300)
        check(f"{case}: settings opened", f.locator("#view-settings").is_visible())
        if index == len(cases) - 1:  # changes the demo state: last case only
            f.locator("#notice button", has_text="This address is correct").click()
            page.wait_for_timeout(200)
            f.locator("#dlg-btns button", has_text="Yes, this is correct").click()
            page.wait_for_timeout(800)
            check(f"{case}: confirmation removes the notice", notice.is_hidden())
        check(f"{case}: no console errors (legacy)", not errors, str(errors[:2]))
        ctx.close()
        # Not set at all
        ctx, page, f, errors = open_page(browser, MISSING, width, scheme, embedded)
        notice = f.locator("#notice")
        check(f"{case}: notice when not set", notice.is_visible() and "set the download base URL" in notice.text_content())
        buttons = f.locator("#notice button").all_text_contents()
        check(f"{case}: only the settings button", buttons == ["Open settings"], str(buttons))
        f.click("#tab-settings")
        f.wait_for_selector("#opt-download_base_url")
        check(f"{case}: empty URL field", f.input_value("#opt-download_base_url") == "")
        page.screenshot(path=OUT / f"notice-missing-{case}.png")
        check(f"{case}: no console errors (missing)", not errors, str(errors[:2]))
        ctx.close()
    browser.close()

bad = [r for r in results if not r[1]]
print(f"{len(results) - len(bad)}/{len(results)} passed")
for name, _ok, detail in bad:
    print("FAIL", name, detail)
sys.exit(1 if bad else 0)
