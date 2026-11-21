"""Shared helpers and constants of the feature suites (qa/ui_features.py). Not part of the app."""

import io
import json
import sys
import time
import zipfile




IDLE, LEGACY, MISSING, FEATURES, FIRST, PUBLIC = (
    f"http://127.0.0.1:{p}" for p in (8801, 8803, 8804, 8805, 8806, 8807))
OVERFLOW_JS = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
results = []  # (suite: name, ok, detail)
SUITE = [""]  # name of the running suite (prefix of the result names)
FULL = "--full" in sys.argv  # final gate: all viewports


def wait_until(predicate, timeout=5.0):
    """Poll predicate every 50 ms until it is true; returns its last result (no fixed
    waits: a check fails only if the state is not reached within timeout)."""
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(0.05)


def ui_frame(page, embedded):
    """The frame of the app: the iframe of the HA-like wrapper or the page itself."""
    return page.wait_for_selector("iframe").content_frame() if embedded else page


def refresh_state(page, f):
    """Make the app request /api/state now (as on returning to the tab) and wait for the
    answer and the re-render."""
    with page.expect_response("**/api/state"):
        f.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(100)  # render after the answer (same task queue, no event to wait for)


def check(name, ok, detail=""):
    """Record one check."""
    results.append((f"{SUITE[0]}: {name}", bool(ok), detail))


def open_page(browser, base, width, scheme, embedded, ignore=()):
    """New page on the demo; returns (context, page, frame, errors). HTTP errors of
    URLs containing one of ignore (expected refusals) are not counted."""
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, has_touch=width <= 700)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
    page.on("response", lambda r, errs=errors: errs.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and not r.url.endswith("/favicon.ico") and not any(i in r.url for i in ignore)
            else None)
    page.on("console", lambda m, errs=errors: errs.append(m.text)
            if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.goto(base + ("/wrap" if embedded else "/ui/"))
    frame = ui_frame(page, embedded)
    frame.wait_for_selector("#dev-cards table")
    return ctx, page, frame, errors


def firmware_zip():
    """Upload test file: ZIP with a manifest naming Plus1 1.1.0."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"name": "Plus1", "build_id": "20240101-000000/1.1.0-gabc"}))
        archive.writestr("app.bin", b"\x01" * 20000)
    return buf.getvalue()


def menu_items(f, name):
    """Open the row menu of a device; returns [(text, disabled)]."""
    button = f.locator(f'button[aria-label="Actions for {name}"]').first
    # Scroll first and let the scroll event pass: it closes the menu, so one delivered after
    # the click (the browser sends it with the next frame) would close the menu again
    button.scroll_into_view_if_needed()
    getattr(f, "page", f).wait_for_timeout(300)  # f is a frame (embedded) or the page
    button.click()
    f.wait_for_selector("#rowmenu button")
    return f.evaluate("() => [...document.querySelectorAll('#rowmenu button')].map(b => [b.textContent, b.disabled])")


def dialog(f):
    """Title, body text and buttons [(text, disabled)] of the open dialog."""
    return f.evaluate("""() => ({title: document.getElementById('dlg-title').textContent,
      body: document.getElementById('dlg-body').innerText,
      buttons: [...document.querySelectorAll('#dlg-btns button')].map(b => [b.textContent, b.disabled])})""")


def tip_text(f):
    """Text of the shown tooltip, None if hidden."""
    return f.evaluate("() => { const t = document.getElementById('tip'); return t.hidden ? null : t.textContent; }")


def filter_entries(f, view):
    """Entries of the filter popover of a tab (devices, firmware, logs) without opening
    it: [(label, count, checked)]."""
    return f.evaluate("""view => [...document.querySelectorAll(`#view-${view} .filter-item`)].map(b =>
      [b.firstChild.textContent, b.lastChild.textContent, b.getAttribute('aria-checked') === 'true'])""", view)


def pick_filter(f, view, label):
    """Choose a filter in the popover of a tab (opens it, clicks the entry, closes it)."""
    tool = f.locator(f'#view-{view} .tool-btn[aria-label^="Filter"]')
    if not f.locator(f"#view-{view} .tool.open .filter-item").count():
        tool.click()
    f.locator(f"#view-{view} .filter-item", has=f.locator(f'span:text-is("{label}")')).click()
    tool.click()   # a second click on the icon closes the popover
