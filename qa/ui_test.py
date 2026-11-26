"""Playwright UI checks of the device list, menus, dialogs and tabs in the HA default
theme (light/dark), standalone and embedded like the HA panel.

Runs against demo.py servers ("idle" on 8801, "busy" on 8802; qa/run_ui.py starts
them); writes screenshots and results.json to <shots-dir>.
Usage: python qa/ui_test.py <shots-dir> [--full]
Default (per change): quick matrix of 5 cases – 360 px light embedded, 430 px
dark, 1440 px dark, busy 360 px light and busy 1440 px dark. --full: all 13 cases
(360/430/1440 × light/dark, embedded at 360/430, busy 360/1440) – final gate only."""

import json
import re
import sys
from pathlib import Path

from common import launch
from playwright.sync_api import sync_playwright

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
FULL = "--full" in sys.argv
OUT = Path(ARGS[0] if ARGS else "shots")
OUT.mkdir(exist_ok=True)
IDLE, BUSY = "http://127.0.0.1:8801", "http://127.0.0.1:8802"
results = []


def parse(c):
    """CSS colour string -> (r, g, b, a) in 0..1 (rgb[a]() and color(srgb ...))."""
    nums = [float(x) for x in re.findall(r"[-\d.]+", c)]
    if c.startswith("color("):
        r, g, b = nums[:3]
        a = nums[3] if len(nums) > 3 else 1
        return r, g, b, a
    r, g, b = (n / 255 for n in nums[:3])
    return r, g, b, nums[3] if len(nums) > 3 else 1


def blend(top, bottom):
    """Alpha-composite top over an opaque bottom."""
    a = top[3]
    return tuple(top[i] * a + bottom[i] * (1 - a) for i in range(3)) + (1,)


def lum(c):
    """Relative luminance (WCAG)."""
    f = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c[:3]]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def contrast(fg, bg):
    """WCAG contrast ratio."""
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


PILL_JS = """() => [...document.querySelectorAll('.pill')].map(p => {
  const cs = getComputedStyle(p), card = getComputedStyle(p.closest('.card') || document.body);
  return {cls: p.className, text: p.textContent, color: cs.color, bg: cs.backgroundColor, card: card.backgroundColor};
})"""
OVERFLOW_JS = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
# The Details button of a phone tile is 36 px high like the feature buttons (approved)
TARGETS_JS = """() => [...document.querySelectorAll('#view-devices button, #view-devices summary, .tab, #scan')]
  .filter(b => b.offsetParent !== null && !b.matches('td.c-details summary')).map(b => { const r = b.getBoundingClientRect();
  return {t: (b.getAttribute('aria-label') || b.textContent || b.id).trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height)}; })
  .filter(x => x.h < 44 || x.w < 44)"""
PILL_TEXTS = {"Up-To-Date", "Available", "Ready", "Queued", "Installing", "Unconfirmed", "Error", "Not checked",
              "Updated", "Asleep"}
TABS_JS = """() => [...document.querySelectorAll('.tab')].map(t => { const r = t.getBoundingClientRect();
  return {id: t.id, left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width)}; })"""


def check(page, name, ok, detail=""):
    """Record one check."""
    results.append({"case": name, "ok": bool(ok), "detail": detail})


def run_case(pw, width, scheme, embedded, base=IDLE, tag="idle"):
    """All checks for one viewport/scheme/mode."""
    browser = launch(pw)
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 700 else 780},
                              color_scheme=scheme, device_scale_factor=1, has_touch=width <= 700)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
    case = f"{tag}-{width}-{scheme}-{'embedded' if embedded else 'standalone'}"
    page.goto(base + ("/wrap" if embedded else "/ui/"))
    f = page  # page or, when embedded, the UI iframe (available after a short wait)
    if embedded:
        page.wait_for_timeout(300)
        f = page.frames[1]
    f.wait_for_selector("#dev-cards table")
    page.screenshot(path=OUT / f"{case}-devices.png", full_page=not embedded)
    ov = f.evaluate(OVERFLOW_JS)
    check(page, f"{case}: no horizontal overflow (devices)", ov <= 0, f"overflow {ov}px")
    tabs = f.evaluate(TABS_JS)
    check(page, f"{case}: all {len(tabs)} tabs inside viewport", all(t["right"] <= width and t["left"] >= 0 for t in tabs), str(tabs))
    if width <= 700:
        small = f.evaluate(TARGETS_JS)
        # status pills and the Install button next to them (same size, compact tile): >= 26 px
        small = [x for x in small if x["h"] < 26 or (x["t"] not in PILL_TEXTS and not x["t"].startswith("Install update on"))]
        check(page, f"{case}: touch targets >= 44 px (status pills, Install >= 26)", not small, json.dumps(small[:8]))
        # Inside HA the app title is hidden (a pre-release keeps only its "(beta)" mark)
        h1 = f.evaluate("() => getComputedStyle(document.querySelector('h1 .app-title')).display")
        check(page, f"{case}: app title {'hidden' if embedded else 'shown'}", (h1 == "none") == embedded, h1)
        cols = f.evaluate("() => getComputedStyle(document.getElementById('cols')).display")
        check(page, f"{case}: no column chooser", cols == "none", cols)
        hidden_cells = f.evaluate("""() => ['aop','eco','restart'].map(k => [...document.querySelectorAll('td.c-'+k)].every(td => getComputedStyle(td).display === 'none'))""")
        check(page, f"{case}: AOP/Eco/Restart not in card", all(hidden_cells), str(hidden_cells))
        # Expand details of the first device card
        f.locator("td.c-details summary").first.click()
        page.wait_for_timeout(150)
        vis = f.locator("td.c-details details[open] dl").first.is_visible()
        check(page, f"{case}: details expandable", vis)
        page.screenshot(path=OUT / f"{case}-details.png", full_page=not embedded)
    else:
        det = f.evaluate("() => [...document.querySelectorAll('.c-details')].every(e => getComputedStyle(e).display === 'none')")
        check(page, f"{case}: details column hidden on desktop", det)
        head = f.evaluate("() => [...document.querySelectorAll('thead th')].filter(t => getComputedStyle(t).display !== 'none').map(t => t.textContent)")
        check(page, f"{case}: desktop columns", head[1:7] == ["Device", "IP", "Gen", "Firmware", "Status", "Action"], str(head))  # default columns (the power-on action is an icon in Action)
    # Pill contrast (WCAG AA 4.5:1 for small text)
    worst = []
    for p in f.evaluate(PILL_JS):
        card = parse(p["card"])
        bg = blend(parse(p["bg"]), card)
        fg = blend(parse(p["color"]), bg)
        worst.append((round(contrast(fg, bg), 2), p["text"]))
    worst.sort()
    check(page, f"{case}: pill contrast >= 4.5", worst[0][0] >= 4.5, str(sorted(set(worst))[:6]))
    # Row menu
    f.locator("td.c-menu button").first.click()
    page.wait_for_timeout(150)
    items = f.evaluate("() => [...document.querySelectorAll('#rowmenu button, #rowmenu a, #rowmenu .menu-hint')].map(b => [b.textContent, !!b.disabled])")
    box = f.evaluate("() => { const r = document.getElementById('rowmenu').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom]; }")
    check(page, f"{case}: row menu has Check for updates first", items and items[0][0] == "Check for updates", str(items[:3]))
    check(page, f"{case}: row menu inside viewport", box[0] >= 0 and box[1] <= width, str(box))
    if tag == "busy":
        check(page, f"{case}: check disabled with reason while busy", items[0][1] and any("Not available" in i[0] for i in items), str(items[:3]))
    page.screenshot(path=OUT / f"{case}-menu.png")
    page.keyboard.press("Escape")  # keyboard belongs to the page, also for the iframe
    # Status dialog
    f.locator("button.pill").first.click()
    page.wait_for_timeout(150)
    dlg = f.evaluate("() => document.getElementById('dlg').open && document.getElementById('dlg-body').textContent")
    check(page, f"{case}: status dialog with last successful check", dlg and "Last success" in dlg and "History" in dlg)
    page.screenshot(path=OUT / f"{case}-status.png")
    if f.locator("#dlg-x").count():
        f.locator("#dlg-x").click()
    page.keyboard.press("Escape")
    if tag == "busy":
        prog = f.evaluate("() => !document.getElementById('progress').hidden && document.getElementById('progress-text').textContent")
        check(page, f"{case}: global progress line", prog == "Checking devices · 18/25 completed · 2 unavailable · checking devices", str(prog))
        acts = f.evaluate("() => [...document.querySelectorAll('.p-activity')].map(p => p.textContent)")
        check(page, f"{case}: per-row activity", {"Waiting", "Checking …", "Downloading …", "Shelly server …", "Restarting …"} <= set(acts),
              str(sorted(set(acts))))
        # Waiting for the Shelly update server: blue, yellow once 5 s have passed
        delay = f.evaluate("() => parseFloat(document.querySelector('.p-server').style.animationDelay)")
        check(page, f"{case}: server pill turns yellow after 5 s", 0 <= delay <= 5000, str(delay))
    # Other tabs
    for tab in ("firmware", "logs", "settings"):
        f.locator(f"#tab-{tab}").click()
        page.wait_for_timeout(400)
        ov = f.evaluate(OVERFLOW_JS)
        check(page, f"{case}: no horizontal overflow ({tab})", ov <= 0, f"overflow {ov}px")
        page.screenshot(path=OUT / f"{case}-{tab}.png", full_page=not embedded)
    errs = [e for e in errors if "favicon" not in e and not e.startswith("Failed to load resource")]
    check(page, f"{case}: no console errors / CSP violations", not errs, str(errs[:3]))
    browser.close()


with sync_playwright() as pw:
    if FULL:
        for width in (360, 430, 1440):
            for scheme in ("light", "dark"):
                run_case(pw, width, scheme, embedded=False)
                if width <= 430:
                    run_case(pw, width, scheme, embedded=True)
        for width in (360, 1440):
            for scheme in ("light", "dark"):
                run_case(pw, width, scheme, embedded=False, base=BUSY, tag="busy")
    else:
        run_case(pw, 360, "light", embedded=True)
        run_case(pw, 430, "dark", embedded=False)
        run_case(pw, 1440, "dark", embedded=False)
        run_case(pw, 360, "light", embedded=False, base=BUSY, tag="busy")
        run_case(pw, 1440, "dark", embedded=False, base=BUSY, tag="busy")

Path(OUT / "results.json").write_text(json.dumps(results, indent=1))
bad = [r for r in results if not r["ok"]]
print(f"ui_test ({'full' if FULL else 'quick'}): {len(results) - len(bad)}/{len(results)} passed")
for r in bad:
    print("FAIL", r["case"], r["detail"][:300])
