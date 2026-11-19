"""Feature UI checks (Playwright) against the demo servers – one file for all
feature suites.

Suites, in this order (the last two change the demo state in their last case):
- download_url_hint   Settings: hints for download_base_url (LAN IP, public IP,
                      localhost, host names). Demo "idle" on 8801.
- setting_help        Settings: help texts as tooltips of the setting name and its
                      i-icon (mouse, keyboard, touch). Demo "idle" on 8801.
- download_url_notice notice for a missing / unconfirmed download base URL, device
                      progress of a Gen2+ installation. Demos "legacy" 8803, "missing" 8804.
- firmware_store      downgrade from the device menu, upload, two-step delete, the
                      Install button behind a device and the changelog icon in the
                      Firmware tab; 3.1.1: Install button and "Updated" in the device
                      list, state colours of the action icons, log grouping and
                      deleting log entries; 3.2.0: failed installation in the updates
                      card, dialog sections, phone tile layout, no tooltip after a tap,
                      user in the log tile; devices stay in the updates card while
                      checked, Ignored label size on phones; action popups with status strip,
                      websocket debug icon, tooltips of the device names and tooltips on
                      touch, Firmware-tab labels and dialog title icons; sorting per
                      browser and the Firmware-tab downgrade, downgrades switched off.
                      Demo "features" on 8805 (and "idle" 8801, "missing" 8804).
- store_transfer      3.4.0: Settings block "Firmware store" and card "Device Passwords",
                      export (password once, download), import (check, counts,
                      confirm), passwords of several devices (selection, steps,
                      confirmation), first-start question with notice bar, files
                      above the Ingress limit sent in parts. Demos
                      "idle" 8801 and "first" 8806 (Skip changes it: last case only).
Usage: python qa/ui_features.py <shots-dir> [--full] [suite ...]   (qa/run_ui.py starts
the demos; --full: all viewports of firmware_store, final gate)
Every suite checks: no horizontal overflow, no console errors; screenshots go to
<shots-dir>."""

import io
import json
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

from common import launch
from playwright.sync_api import sync_playwright

IDLE, LEGACY, MISSING, FEATURES, FIRST = (f"http://127.0.0.1:{p}" for p in (8801, 8803, 8804, 8805, 8806))
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
    f.locator(f'button[aria-label="Actions for {name}"]').first.click()
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


def setting_help(browser, out):
    """Settings tab: help texts only as tooltip of the name and the i-icon; touch tap on
    the name toggles it without focusing the control; the live URL warning stays."""
    for width, scheme, embedded in ((360, "light", True), (1440, "dark", False)):
        case = f"{width}-{scheme}{'-embedded' if embedded else ''}"
        ctx, page, f, errors = open_page(browser, IDLE, width, scheme, embedded)
        f.click("#tab-settings")
        f.wait_for_selector("#opt-check_interval_days")
        help_box = f.locator("#opt-check_interval_days-help")
        check(f"{case}: help text not shown under the field", help_box.evaluate("e => e.getBoundingClientRect().height") <= 1
              and help_box.text_content() == "1–30 days")
        described = f.get_attribute("#opt-check_interval_days", "aria-describedby")
        check(f"{case}: help text stays the description of the field", described == "opt-check_interval_days-help", str(described))
        icons = f.locator(".set-name button.help")
        check(f"{case}: one i-icon per setting", icons.count() == 12, str(icons.count()))
        name = f.locator("label[for=opt-check_interval_days]")
        info = f.locator(".set-name", has=name).locator("button.help")
        geo = f.evaluate("""() => { const l = document.querySelector('label[for=opt-check_interval_days]'), i = l.nextElementSibling;
          const r = e => e.getBoundingClientRect(); return {dy: Math.abs((r(l).top + r(l).height / 2) - (r(i).top + r(i).height / 2)),
          right: r(i).left >= r(l).right, size: r(i.querySelector('svg')).width}; }""")
        check(f"{case}: i-icon (16 px) next to the name", geo["dy"] < 2 and geo["right"] and geo["size"] == 16, str(geo))
        if width > 700:
            name.hover()
            check(f"{case}: hover over the name shows the help", tip_text(f) == "1–30 days", str(tip_text(f)))
            page.mouse.move(5, 5)
            check(f"{case}: leaving hides it", tip_text(f) is None)
            info.hover()
            check(f"{case}: hover over the icon shows the help", tip_text(f) == "1–30 days")
            page.mouse.move(5, 5)
            name.click()
            check(f"{case}: desktop click on the name focuses the control (unchanged)",
                  f.evaluate("document.activeElement.id") == "opt-check_interval_days")
            info.focus()
            f.locator("#opt-check_interval_days").focus()
            page.keyboard.press("Shift+Tab")
            check(f"{case}: keyboard focus of the icon shows the help", tip_text(f) == "1–30 days", str(tip_text(f)))
            page.keyboard.press("Tab")
        else:
            bool_name = f.locator("label[for=opt-gen1_updates_enabled]")
            before = f.is_checked("#opt-gen1_updates_enabled")
            bool_name.tap()
            check(f"{case}: tap on the name shows the help", tip_text(f) and tip_text(f).startswith("Gen1 firmware"), str(tip_text(f)))
            check(f"{case}: tap on the name does not toggle the control", f.is_checked("#opt-gen1_updates_enabled") == before
                  and f.evaluate("document.activeElement.id") != "opt-gen1_updates_enabled")
            page.screenshot(path=out / f"setting-help-tap-{case}.png")
            bool_name.tap()
            check(f"{case}: second tap hides it", tip_text(f) is None)
            name.tap()
            check(f"{case}: tap on the name does not focus the field", f.evaluate("document.activeElement.id") != "opt-check_interval_days")
            f.locator("#view-settings h2").first.tap()
            check(f"{case}: tap elsewhere hides it", tip_text(f) is None)
            info.tap()
            check(f"{case}: tap on the icon shows the help", tip_text(f) == "1–30 days")
            f.evaluate("() => window.dispatchEvent(new Event('scroll'))")
            check(f"{case}: scrolling hides it", tip_text(f) is None)
        f.fill("#opt-download_base_url", "http://203.0.113.10:8098")
        check(f"{case}: live URL warning stays visible under the field", f.locator("#opt-download_base_url-warn").is_visible())
        f.fill("#opt-download_base_url", "http://192.168.1.2:8098")
        page.mouse.move(5, 5)
        page.screenshot(path=out / f"setting-help-{case}.png", full_page=True)
        check(f"{case}: no horizontal overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: no console errors", not errors, str(errors[:2]))
        ctx.close()


def download_url_hint(browser, out):
    """Hints for download_base_url in the Settings tab (360 light embedded, 1440 dark)."""
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
        page.goto(IDLE + ("/wrap" if embedded else "/ui/"))
        f = ui_frame(page, embedded)
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
        page.screenshot(path=out / f"lan-hint-{case}.png")
        f.fill("#opt-download_base_url", "http://localhost:8098")
        check(f"{case}: hint for localhost", warn.is_visible())
        f.fill("#opt-download_base_url", "http://homeassistant.local:8098")
        check(f"{case}: host-name hint for a .local name", warn.is_visible() and "host name" in warn.text_content())
        f.locator("#opt-download_base_url").scroll_into_view_if_needed()
        page.screenshot(path=out / f"host-name-hint-{case}.png")
        f.fill("#opt-download_base_url", "https://example.com:8098")
        check(f"{case}: host-name hint for a public name", warn.is_visible() and "host name" in warn.text_content())
        f.fill("#opt-download_base_url", "http://192.168.1.20:8098")
        check(f"{case}: no hint for a LAN IP", warn.is_hidden())
        check(f"{case}: no console errors", not errors, str(errors[:2]))
        ctx.close()


def download_url_notice(browser, out):
    """Notice for a missing / unconfirmed download base URL; device progress in the pill."""
    cases = ((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False))
    for index, (width, scheme, embedded) in enumerate(cases):
        case = f"{width}-{scheme}{'-embedded' if embedded else ''}"
        # Former example value, not confirmed
        ctx, page, f, errors = open_page(browser, LEGACY, width, scheme, embedded)
        notice = f.locator("#notice")
        check(f"{case}: notice shown", notice.is_visible() and "former example value" in notice.text_content())
        buttons = f.locator("#notice button").all_text_contents()
        check(f"{case}: confirm and settings buttons", buttons == ["This address is correct", "Open settings"], str(buttons))
        check(f"{case}: no install selection", f.locator("td.c-select input:not(.ghost)").count() == 0)
        pills = f.locator("button.pill").all_text_contents()
        check(f"{case}: device progress in the status pill", "Installing 55 %" in pills, str(sorted(set(pills))))
        check(f"{case}: no horizontal overflow", f.evaluate(OVERFLOW_JS) <= 0)
        page.screenshot(path=out / f"notice-legacy-{case}.png")
        f.locator("#notice button", has_text="Open settings").click()
        check(f"{case}: settings opened", wait_until(f.locator("#view-settings").is_visible))
        if index == len(cases) - 1:  # changes the demo state: last case only
            f.locator("#notice button", has_text="This address is correct").click()
            f.locator("#dlg-btns button", has_text="Yes, this is correct").click()
            check(f"{case}: confirmation removes the notice", wait_until(notice.is_hidden))
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
        page.screenshot(path=out / f"notice-missing-{case}.png")
        check(f"{case}: no console errors (missing)", not errors, str(errors[:2]))
        ctx.close()


def _device_list(c):
    """Devices tab: Install button next to "Ready", "Updated" stays on top, icon colours."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    # Device list – Install button next to "Ready", "Updated" stays on top, icon colours
    card = f.locator("#dev-cards .card").first
    install = card.locator(".status-cell button.install")
    check(f"{case}: Install button for every ready device, the failed installation and the store fallback", install.count() == 7
          and install.first.is_enabled(), str(install.count()))
    check(f"{case}: Install button is red", "danger" in install.first.get_attribute("class"))
    updated = card.locator("tr", has_text="Bedroom shutter")
    check(f"{case}: updated device stays in the updates card", updated.count() == 1
          and updated.locator(".pill").first.inner_text() == "Updated" and updated.locator("input.ghost").count() == 1)
    unconfirmed = card.locator("tr", has_text="Office desk")
    check(f"{case}: unconfirmed installation stays in the updates card", unconfirmed.count() == 1
          and unconfirmed.locator(".pill").first.inner_text() == "Unconfirmed" and unconfirmed.locator("input.ghost").count() == 1)
    if width < 700:   # phone tiles: Install button as large as the status pill next to it
        sizes = f.evaluate("""() => { const b = document.querySelector('#dev-cards .status-cell button.install');
          const p = b.parentElement.querySelector('.pill'); const r = e => e.getBoundingClientRect();
          return {button: r(b).height, pill: r(p).height, dy: Math.abs(r(b).top - r(p).top)}; }""")
        check(f"{case}: Install button as high as the status pill", abs(sizes["button"] - sizes["pill"]) < 1 and sizes["dy"] < 1,
              str(sizes))
    if width > 700:   # every column except "Device": title and content centred; IP and firmware with tabular digits
        align = f.evaluate("""() => { const t = document.querySelector('#dev-cards table'), cs = e => getComputedStyle(e);
          const cells = [...t.querySelectorAll('th, tbody tr:first-child td')].filter(e => cs(e).display !== 'none');
          const off = cells.filter(e => (cs(e).textAlign === 'center') === e.classList.contains('c-device')).map(e => e.className);
          const mid = e => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; };
          const strip = t.querySelector('td.c-action .act-strip'), status = t.querySelector('td.c-status .status-cell > *');
          return {off, strip: Math.abs(mid(strip) - mid(strip.parentElement)),
            status: cs(status.parentElement).justifyContent,
            nums: ['td.c-ip', 'td.c-firmware'].map(q => cs(t.querySelector(q)).fontVariantNumeric)}; }""")
        check(f"{case}: devices table – columns except Device centred, tabular digits", not align["off"] and align["strip"] < 1
              and align["status"] == "center" and align["nums"] == ["tabular-nums"] * 2, str(align))
    check(f"{case}: no overflow (devices)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-devices.png")
    install.first.click()
    check(f"{case}: Install opens the installation dialog (devices)", dialog(f)["title"] == "Install update?")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    if width > 700:   # the icon strip is part of the table on wide screens
        counts = f.evaluate("""() => ({good: document.querySelectorAll('.act.c-good').length,
          alert: document.querySelectorAll('.act.c-alert').length, warn: document.querySelectorAll('.act.c-warn').length,
          ap: document.querySelectorAll('.act.alert.s-on[aria-label^="Access point"]').length})""")
        check(f"{case}: power-on action green and red, lock yellow, access point red",
              all(counts[k] > 0 for k in ("good", "alert", "warn", "ap")), str(counts))


LIVE = {"shelly_name": "shelly-2", "ntp": "time.local", "eco": True, "ap": False, "ble": True, "debug_ws": False,
        "outputs": [{"key": "switch:0", "name": "switch:0", "aop": "restore_last", "options": ["off", "on", "restore_last"]}]}


def _popup(f, opener):
    """Open an action popup; returns (title, strip, body, buttons)."""
    opener.click(force=True)   # greyed-out icons are aria-disabled but open their note
    f.wait_for_selector("#dlg-body .fstrip")   # after "Reading current values …"
    d = dialog(f)
    strip = f.evaluate("""() => { const s = document.querySelector('#dlg-body .fstrip'); if (!s) return null;
      const p = s.querySelector('.fs-pill'), i = s.querySelector('.fs-icon');
      return {name: s.querySelector('.fs-name').textContent, pill: p ? p.textContent : null, tone: s.className,
        size: Math.round(i.getBoundingClientRect().width), first: s === document.getElementById('dlg-body').firstElementChild}; }""")
    return d["title"], strip, d["body"], d["buttons"]


def _action_popups(c):
    """Action icons: websocket debug icon (colours, Gen1 unsupported), access-point icon,
    status strip in every popup opened by an action icon, two icon rows of five."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    page.route("**/settings/refresh", lambda r: r.fulfill(json=LIVE))
    opened = []   # phone tiles whose details were opened here (closed again at the end)

    def icon_of(name, label):
        row = f.locator("#dev-cards tr", has_text=name).first
        if width < 700:
            det = row.locator("td.c-details details")
            if not det.evaluate("d => d.open"):
                row.locator("td.c-details summary").click()
                opened.append(row)
            return row.locator(f".act-strip.labeled [aria-label^='{label}']")
        return row.locator(f"td.c-action [aria-label^='{label}']")

    living = icon_of("Living room", "Websocket debug log")
    check(f"{case}: debug icon on = yellow", "c-warn" in living.get_attribute("class") and "s-on" in living.get_attribute("class"),
          living.get_attribute("class"))
    hall = icon_of("Hall", "Websocket debug log")
    check(f"{case}: debug icon off = grey", "s-off" in hall.get_attribute("class"), hall.get_attribute("class"))
    gen1 = icon_of("Garden plug", "Websocket debug log")
    check(f"{case}: Gen1 debug icon greyed out with explanation", gen1.get_attribute("aria-disabled") == "true"
          and "Gen1 devices have no websocket debug log" in gen1.get_attribute("aria-label"))
    ap_path = icon_of("Hall", "Access point").locator("path").get_attribute("d")
    check(f"{case}: access-point icon shape", ap_path.startswith("M4.93,4.93"), ap_path[:20])
    if width > 700:
        rows = f.evaluate("""() => { const s = document.querySelector('#dev-cards td.c-action .act-strip'); const tops = {};
          for (const b of s.children) { const t = Math.round(b.getBoundingClientRect().top); tops[t] = (tops[t] || 0) + 1; }
          return {width: s.getBoundingClientRect().width, rows: Object.values(tops)}; }""")
        check(f"{case}: action strip 170 px, two rows of 5 + 4 icons", rows["width"] == 170 and rows["rows"] == [5, 4], str(rows))
    else:
        labeled = f.locator("#dev-cards tr", has_text="Hall").first.locator(".act-strip.labeled > *")
        check(f"{case}: phone tile has the debug button", labeled.count() == 9
              and labeled.nth(7).inner_text() == "Websocket debug log", str(labeled.all_inner_texts()))
        f.locator("#dev-cards tr", has_text="Hall").first.screenshot(path=out / f"{case}-tile-debug.png")
    # Popups opened by an action icon: strip under the title (icon circle 32 px, name, state)
    # Switch and restart popups: no question; the button names the action
    expect = (("Hall", "Eco mode", "Eco mode – Hall", "On", "t-good", "Turn off"),
              ("Hall", "Bluetooth", "Bluetooth – Hall", "On", "t-good", "Turn off"),
              ("Hall", "Access point", "Access point – Hall", "Off", "t-off", "Turn on"),
              ("Hall", "Power-on action", "Power-on action – Hall", "Restore last state", "t-good", None),
              ("Hall", "Websocket debug log", "Websocket debug log – Hall", "Off", "t-off", "Turn on"),
              ("Living room", "Password protection", "Device password – Living room ceiling with a really long device name",
               "Protected", "t-good", None),
              ("Hall", "Restart", "Restart – Hall", None, "t-neutral", "Restart"))
    for name, label, title, pill, tone, button in expect:
        t, strip, body, buttons = _popup(f, icon_of(name, label))
        check(f"{case}: {label} popup with status strip", t == title and strip and strip["first"] and strip["pill"] == pill
              and tone in strip["tone"] and strip["size"] == 32, f"{t} {strip} {body[:120]}")
        if button:
            check(f"{case}: {label} popup without question, button \"{button}\"", "?" not in body and buttons[-1][0] == button
                  and (label != "Restart" or "The device will be unavailable for a few seconds." in body), f"{body[:160]} {buttons}")
        if label == "Websocket debug log":
            link = f.locator("#dlg-body a", has_text="Open Diagnostics in the Shelly web UI")
            check(f"{case}: debug popup links the Diagnostics page", link.get_attribute("href") == "http://192.168.1.12/#/diagnostics"
                  and link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel")
                  and "Turn it off afterwards" in body and "below 1.0.0" not in body, str(link.get_attribute("href")))
            page.screenshot(path=out / f"{case}-popup-debug.png")
        if label == "Eco mode":
            page.screenshot(path=out / f"{case}-popup-eco.png")
        f.locator("#dlg-btns button", has_text="Cancel").click()
    # Notes of greyed-out icons: strip with the reason
    for name, label, pill, text in (("Garden plug", "Websocket debug log", "Not supported", "no websocket debug log"),
                                    ("Garden plug", "Access point", "Not available", "Not available on Gen1"),
                                    ("Kitchen light", "Power-on action", "Not available", "no switchable output"),
                                    ("Terrace", "Access point", "Not supported", "not supported")):
        t, strip, body, _buttons = _popup(f, icon_of(name, label))
        check(f"{case}: note of {label} ({name}) with status strip", t == label and strip and strip["pill"] == pill and text in body,
              f"{t} {strip} {body[:120]}")
        if label == "Websocket debug log":
            page.screenshot(path=out / f"{case}-popup-debug-gen1.png")
        f.locator("#dlg-btns button", has_text="Close").click()
    page.unroute("**/settings/refresh")

    # Firmware below 1.0.0: warning that the stream is not protected by the device password
    def old_firmware(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        state = route.fetch(headers=headers).json()
        for d in state["devices"]:
            if d["name"] == "Hall":
                d["version"] = "0.14.1"
        route.fulfill(json=state)
    page.route("**/api/state", old_firmware)
    page.route("**/settings/refresh", lambda r: r.fulfill(json=LIVE))
    refresh_state(page, f)
    _t, _strip, body, _buttons = _popup(f, icon_of("Hall", "Websocket debug log"))
    check(f"{case}: debug popup warns below firmware 1.0.0", "not protected by the device password" in body, body[-200:])
    f.locator("#dlg-btns button", has_text="Cancel").click()
    page.wait_for_timeout(400)   # the state request of closing the dialog is answered first
    page.unroute("**/settings/refresh")
    page.unroute("**/api/state")
    refresh_state(page, f)
    for row in opened:
        row.locator("td.c-details summary").click()
    f.evaluate("() => scrollTo(0, 0)")
    page.wait_for_timeout(300)   # the scroll event (hides a touch tooltip) arrives before the next tap


def _name_tips(c):
    """Tooltips of the HA name and the Shelly name (mouse hover, touch tap) and the
    touch behaviour of tooltips: shown on a tap without action, not on a tap with one."""
    case, f, page, width = c.case, c.f, c.page, c.width
    row = f.locator("#dev-cards tr", has_text="Hall").first
    ha, shelly_name = row.locator("td.c-device .name"), row.locator(".shelly-name > span")
    check(f"{case}: no native title on the names", ha.get_attribute("title") is None)
    if width > 700:
        ha.hover()
        check(f"{case}: HA name tooltip on hover", tip_text(f) == "Home Assistant device name: Hall", str(tip_text(f)))
        shelly_name.hover()
        check(f"{case}: Shelly name tooltip on hover", tip_text(f) == "Shelly device name: shelly-2", str(tip_text(f)))
        page.screenshot(path=c.out / f"{case}-name-tip.png")
        page.mouse.move(5, 5)
        return
    page.wait_for_timeout(300)   # a re-render after the previous state request hides every tooltip
    ha.tap()
    check(f"{case}: HA name tooltip on tap", tip_text(f) == "Home Assistant device name: Hall", str(tip_text(f)))
    page.screenshot(path=c.out / f"{case}-name-tip.png")
    shelly_name.tap()
    check(f"{case}: Shelly name tooltip on tap", tip_text(f) == "Shelly device name: shelly-2", str(tip_text(f)))
    shelly_name.tap()
    check(f"{case}: second tap hides it", tip_text(f) is None)
    page.route("**/settings/refresh", lambda r: r.fulfill(json=LIVE))
    row.locator(".pen").tap()
    check(f"{case}: pencil unchanged (opens the dialog, no tooltip)", tip_text(f) is None and f.locator("#dlg").is_visible())
    f.locator("#dlg-x").tap()
    page.wait_for_timeout(300)
    page.unroute("**/settings/refresh")
    ha.tap()
    f.locator("#dev-chips .chip").first.tap()
    check(f"{case}: tap elsewhere hides the tooltip", tip_text(f) is None)
    page.keyboard.press("Escape")


def _keep_listed(c):
    """Devices of the updates card stay there while they are checked again (the check
    resets their markers); phone tiles: the Ignored label as high as the status pill."""
    case, f, page = c.case, c.f, c.page
    checked = ("Fridge", "Office desk")

    def checking(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}  # no 304
        state = route.fetch(headers=headers).json()
        for d in state["devices"]:
            if d["name"] in checked:   # what a running single check reports
                d.update(activity="checking", install_failed=None, updated=None, last_update=None)
        route.fulfill(json=state)

    page.route("**/api/state", checking)
    refresh_state(page, f)
    card = f.locator("#dev-cards .card").first
    for name in checked:
        row = card.locator("tr", has_text=name)
        check(f"{case}: {name} stays in the updates card while checked", row.count() == 1
              and row.locator(".pill").first.inner_text().startswith("Checking"))
    card.locator("tr", has_text="Fridge").screenshot(path=c.out / f"{case}-checking-kept.png")
    page.unroute("**/api/state")
    refresh_state(page, f)
    if c.width < 700:
        sizes = f.evaluate("""() => { const t = [...document.querySelectorAll('#dev-cards .status-cell')]
          .find(e => e.querySelector('.p-ignored') && e.querySelector('button.pill'));
          if (!t) return null; const r = e => e.getBoundingClientRect().height;
          return {ignored: r(t.querySelector('.p-ignored')), pill: r(t.querySelector('button.pill'))}; }""")
        f.locator("#dev-cards tr.ignored").first.screenshot(path=c.out / f"{case}-ignored-tile.png")
        check(f"{case}: Ignored label as high as the status pill", sizes and abs(sizes["ignored"] - sizes["pill"]) < 1,
              str(sizes))


def _next_steps(c):
    """Stored version offered on a server error (risk acknowledgment) and the hint of
    an intermediate version in the installation dialog."""
    case, f = c.case, c.f
    card = f.locator("#dev-cards .card").first
    fallback = card.locator("tr", has_text="Entrance")
    check(f"{case}: store fallback in the updates card with checkbox and Install", fallback.count() == 1
          and fallback.locator(".pill").first.inner_text() == "Error"
          and fallback.locator("input[type=checkbox]:not(.ghost)").count() == 1 and fallback.locator("button.install").is_enabled())
    fallback.locator("button.install").click()
    d = dialog(f)
    approve = [b for b in d["buttons"] if b[0] == "Approve installation"]
    check(f"{case}: store fallback needs the risk acknowledgment", "at your own risk" in d["body"]
          and approve == [["Approve installation", True]], str(d["buttons"]))
    f.locator("#dlg-body label.check input").check()
    check(f"{case}: acknowledgment enables the approval",
          f.locator("#dlg-btns button", has_text="Approve installation").is_enabled())
    c.page.screenshot(path=c.out / f"{case}-install-store-fallback.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    step = card.locator("tr", has_text="Heat pump 3EM")
    step.locator("button.install").click()
    d = dialog(f)
    check(f"{case}: intermediate version named in the installation dialog",
          "1.5.0 is an intermediate version on the way to 1.7.0" in d["body"], d["body"][-300:])
    c.page.screenshot(path=c.out / f"{case}-install-intermediate.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()


def _layout(c):
    """Failed installation in the updates card, status dialog, installation dialog
    sections, tooltips on touch, phone tile alignment and the user in the log tile."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    card = f.locator("#dev-cards .card").first
    failed = card.locator("tr", has_text="Fridge")
    check(f"{case}: failed installation stays in the updates card with checkbox and Install", failed.count() == 1
          and failed.locator(".pill").first.inner_text() == "Error" and failed.locator("input[type=checkbox]:not(.ghost)").count() == 1
          and failed.locator("button.install").is_enabled())
    failed.locator("button.pill").click()
    pos = f.evaluate("""() => { const r = e => e.getBoundingClientRect(), m = document.querySelector('#dlg .dmsg'),
      p = document.querySelector('#dlg .dstatus .pill'); return {mid: Math.abs((r(m).top + r(m).height / 2) - (r(p).top + r(p).height / 2)),
      right: r(p).left >= r(m).right, title: document.getElementById('dlg-title').textContent,
      sections: [...document.querySelectorAll('#dlg .dsec h3')].map(h => h.textContent)}; }""")
    check(f"{case}: status dialog – message left, status right, sections", pos["right"] and pos["title"] == "Fridge"
          and pos["sections"] == ["Firmware", "History"], str(pos))
    page.screenshot(path=out / f"{case}-status-failed.png")
    f.locator("#dlg-x").click()
    tip_after_tap = None
    if width < 700:   # touch: a tap shows no tooltip
        card.locator("button.pill").first.tap()
        tip_after_tap = f.evaluate("document.getElementById('tip').hidden")
        f.locator("#dlg-x").click()
        check(f"{case}: no tooltip after a tap", tip_after_tap is True)
    card.locator("button.install").first.click()
    heads = f.evaluate("[...document.querySelectorAll('#dlg .dsec h3')].map(h => h.textContent)")
    check(f"{case}: installation dialog in sections", heads == ["Device", "Firmware", "Changelog"], str(heads))
    page.screenshot(path=out / f"{case}-install-dialog.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    if width < 700:   # phone tile: lines, flush button rows, Details button
        row = card.locator("tr", has_text="Attic fan")
        if not row.locator("td.c-details details").evaluate("d => d.open"):
            row.locator("td.c-details summary").click()
        row.locator(".act-strip.labeled").wait_for()
        geo = row.evaluate("""tr => { const r = e => e.getBoundingClientRect(), mid = e => r(e).top + r(e).height / 2;
          const q = s => tr.querySelector(s), strip = q('.act-strip.labeled'), buttons = [...strip.children];
          const rows = {}; buttons.forEach(b => { const t = Math.round(r(b).top); rows[t] = Math.max(rows[t] || 0, r(b).right); });
          return {check: Math.abs(mid(q('td.c-select input')) - mid(q('.name'))), menu: Math.abs(mid(q('td.c-menu button svg')) - mid(q('.name'))),
            status: Math.abs(mid(q('.status-cell .pill')) - mid(q('td.c-firmware .nowrap'))),
            ip: Math.abs(mid(q('td.c-ip a')) - mid(q('.shelly-name'))) < 4 && r(q('td.c-ip a')).right > r(q('.shelly-name')).right,
            details: {h: Math.round(r(q('td.c-details summary')).height), w: Math.round(r(q('td.c-details summary')).width),
                      full: Math.round(r(tr).width - r(q('td.c-details summary')).width),
                      border: getComputedStyle(q('td.c-details summary')).borderTopStyle},
            flush: Object.values(rows).map(x => Math.round(r(strip).right - x)),
            chevron: getComputedStyle(q('td.c-details summary'), '::after').borderRightStyle,
            top: Math.round(r(q('.name')).top - r(tr).top), bottom: Math.round(r(tr).bottom - r(q('dl.tile-kv')).bottom)}; }""")
        check(f"{case}: tile – checkbox and menu on the name line, IP right of the Shelly name, status on the firmware line",
              geo["check"] < 2 and geo["menu"] < 2 and geo["status"] < 2 and geo["ip"], str(geo))
        # Details: outlined button, 36 px, full content width (tile padding 12 px + border each side)
        det = geo["details"]
        check(f"{case}: tile – Details as outlined 36 px button over the content width",
              det["h"] == 36 and det["border"] == "solid" and det["full"] <= 26, str(det))
        check(f"{case}: tile – every button row flush with the content width", all(x <= 1 for x in geo["flush"]), str(geo["flush"]))
        check(f"{case}: tile – details chevron, padding top = bottom", geo["chevron"] == "solid" and abs(geo["top"] - geo["bottom"]) <= 3,
              str(geo))
        row.screenshot(path=out / f"{case}-tile.png")
        row.locator("td.c-details summary").click()


def _downgrade(c):
    """Row menu entry and the three steps of a downgrade (nothing is installed)."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    entry = "Downgrade"
    # Menu entry: Gen2+ (also ignored), never Gen1
    items = dict(menu_items(f, "Hall"))
    check(f"{case}: menu entry for Gen2", entry in items and items[entry] is False, str(items))
    page.screenshot(path=out / f"{case}-menu.png")
    page.keyboard.press("Escape")
    items = dict(menu_items(f, "Garage door"))  # ignored device
    check(f"{case}: menu entry for an ignored device", items.get(entry) is False, str(items))
    page.keyboard.press("Escape")
    items = dict(menu_items(f, "Garden plug"))  # Gen1
    check(f"{case}: no menu entry for Gen1", entry not in items, str(items))
    page.keyboard.press("Escape")

    # Step 1: version list
    menu_items(f, "Hall")
    f.locator("#rowmenu button", has_text=entry).click()
    f.wait_for_selector("#dlg-body .modes")
    labels = f.locator("#dlg-body .modes label").all_inner_texts()
    versions = [t.split("·")[0].strip() for t in labels]
    check(f"{case}: all older versions, newest first", versions == ["1.3.1", "1.3.0", "1.2.0"], str(versions))
    check(f"{case}: upload marked", "Uploaded" in labels[2] and "Uploaded" not in labels[0], str(labels))
    check(f"{case}: dialog fits", f.evaluate("() => { const r = document.getElementById('dlg').getBoundingClientRect();"
                                             f" return r.left >= 0 && r.right <= {width}; }}"))
    page.screenshot(path=out / f"{case}-downgrade-list.png")
    f.locator("#dlg-body .modes input").nth(1).check()

    # Step 2: installation dialog with the older target
    f.locator("#dlg-btns button", has_text="Continue").click()
    d = dialog(f)
    check(f"{case}: install dialog with older target", d["title"] == "Downgrade?" and "Version (older)" in d["body"]
          and "1.3.0" in d["body"] and "A downgrade installs an older firmware" in d["body"], d["body"][:200])
    page.screenshot(path=out / f"{case}-downgrade-install.png")

    # Step 3: second confirmation
    f.locator("#dlg-btns button", has_text="Continue").click()
    d = dialog(f)
    go = dict(d["buttons"]).get("Downgrade")
    check(f"{case}: confirmation button disabled", go is True, str(d["buttons"]))
    f.locator("#dlg-body input[type=checkbox]").check()
    go = dict(dialog(f)["buttons"]).get("Downgrade")
    check(f"{case}: enabled after acknowledgment", go is False)
    page.screenshot(path=out / f"{case}-downgrade-confirm.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()   # nothing is installed in the demo


def _firmware_tab(c):
    """Firmware tab: summary, locks, icons, Install button per device, changelog dialog.
    Returns the version rows."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    # Firmware tab
    f.locator("#tab-firmware").click()
    f.wait_for_selector(".fw-row")
    summary = f.locator("#fw-summary").text_content()
    check(f"{case}: total size shown", summary.startswith("4 files") and "MB stored" in summary, summary)
    tip = f.locator("#fw-info").get_attribute("data-tip")
    check(f"{case}: no automatic deletion hint", "never deleted automatically" in tip, tip[:60])
    rows = f.locator(".fw-row")
    centers = rows.first.locator(".actions > *").evaluate_all(
        "els => els.map(e => { const r = e.getBoundingClientRect(); return Math.round(r.top + r.height / 2); })")
    check(f"{case}: changelog, download and delete icons on one line", len(centers) == 3 and max(centers) - min(centers) <= 1, str(centers))
    first_del = rows.first.locator("button[aria-label], button.red").last
    check(f"{case}: provided file can be deleted", first_del.is_enabled() and first_del.get_attribute("title") == "Delete",
          str(first_del.get_attribute("title")))
    first_del.click()
    d = dialog(f)
    check(f"{case}: delete dialog with red trash title icon and red Continue", f.locator("#dlg-title svg.ti-delete").count() == 1
          and "danger" in f.locator("#dlg-btns button", has_text="Continue").get_attribute("class"))
    check(f"{case}: delete dialog names the devices that lose the provided update",
          "lose the provided update" in d["body"] or "loses the provided update" in d["body"], d["body"][:200])
    c.page.screenshot(path=c.out / f"{case}-delete-provided.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    check(f"{case}: upload tag in the list", f.locator(".fw-row .tag.warn", has_text="Uploaded").count() == 1)
    step = f.locator(".fw-row .tag.step")
    style = step.first.evaluate("e => { const s = getComputedStyle(e); return [s.backgroundColor, s.borderTopStyle, s.borderTopWidth]; }")
    check(f"{case}: intermediate label outlined", step.count() == 1 and step.first.inner_text() == "intermediate"
          and style == ["rgba(0, 0, 0, 0)", "solid", "1px"], str(style))
    labels = f.evaluate("""() => [...document.querySelectorAll('.fw-row .fw-ver')].filter(v => v.querySelector('.tag')).map(v => {
      const n = v.querySelector('.name').getBoundingClientRect(), t = v.querySelector('.tag').getBoundingClientRect();
      return {gap: Math.round(t.left - n.right), dy: Math.abs((n.top + n.height / 2) - (t.top + t.height / 2))}; })""")
    check(f"{case}: labels 12 px after the version, centred", labels and all(x["gap"] == 12 and x["dy"] < 1.5 for x in labels), str(labels))
    up = f.evaluate("""() => { const b = document.getElementById('fw-upload'), s = b.querySelector('svg'), r = e => e.getBoundingClientRect();
      const t = document.createRange(); t.selectNodeContents(b.firstChild); const tr = t.getBoundingClientRect();
      return {size: Math.round(r(s).width), hidden: s.getAttribute('aria-hidden'), after: r(s).left > tr.right,
        dy: Math.abs((r(s).top + r(s).height / 2) - (tr.top + tr.height / 2)), name: b.textContent}; }""")
    if width < 700:   # phones: the icon only, round 44 px at the right end of toolbar row 2
        geo = f.evaluate("""() => { const b = document.getElementById('fw-upload'), r = b.getBoundingClientRect(),
          row = b.parentElement.getBoundingClientRect();
          return {w: Math.round(r.width), h: Math.round(r.height), right: Math.round(row.right - r.right),
            text: getComputedStyle(b.querySelector('span')).display, label: b.getAttribute('aria-label')}; }""")
        check(f"{case}: upload button as icon at the right end", geo["w"] == 44 and geo["h"] == 44 and geo["right"] <= 1
              and geo["text"] == "none" and geo["label"] == "Upload firmware" and up["hidden"] == "true", str(geo))
    else:
        check(f"{case}: upload button with the upload icon after the text", up["size"] == 16 and up["hidden"] == "true"
              and up["after"] and up["dy"] < 1.5 and up["name"] == "Upload firmware", str(up))
    head = f.evaluate("""() => { const h = document.querySelector('#fw-cards .fw-head'), row = document.querySelector('#fw-cards .fw-row');
      if (getComputedStyle(h).display === 'none') return {shown: false,
        labels: [...row.querySelectorAll('[data-label]')].map(e => getComputedStyle(e, '::before').content)};
      const mid = e => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; };
      const titles = [...h.children], cells = [...row.children];
      return {shown: true, titles: titles.map(t => t.textContent), first: Math.round(titles[0].getBoundingClientRect().left - cells[0].getBoundingClientRect().left),
        centred: titles.slice(1, 5).map((t, i) => getComputedStyle(t).textAlign === 'center' && Math.abs(mid(t) - mid(cells[i + 1])) < 1)}; }""")
    if width > 700:
        check(f"{case}: firmware table head – titles over the columns, centred except Version", head["shown"]
              and head["titles"] == ["Version", "Size", "Stored", "SHA-256", "Devices", ""] and head["first"] == 0
              and all(head["centred"]), str(head))
    else:
        check(f"{case}: phones – no table head, labels in the tile", not head["shown"]
              and head["labels"] == ['"Size"', '"Stored"', '"SHA-256"'], str(head))
    if width > 700:
        step.first.hover()
    else:
        page.wait_for_timeout(300)   # a re-render after the previous state request hides every tooltip
        step.first.tap()
    check(f"{case}: intermediate label tooltip", tip_text(f) == "Intermediate version: required step before newer firmware can be installed safely",
          str(tip_text(f)))
    if width > 700:
        page.mouse.move(5, 5)
    else:
        step.first.tap()   # second tap hides it
    if width < 700:
        dates = f.evaluate("""() => [...document.querySelectorAll('.fw-row')].map(r => { const g = s => r.querySelector(s).getBoundingClientRect();
          return {right: Math.round(g('.actions').right - g('.fw-date').right), line: Math.round(Math.abs(g('.fw-date').top - g('.fw-size').top)),
            x: Math.round(g('.fw-date').right)}; })""")
        check(f"{case}: date right-aligned under the icons, same line as the size", all(d["right"] == 0 and d["line"] <= 1 for d in dates)
              and len({d["x"] for d in dates}) == 1, str(dates))
    else:
        page.mouse.move(5, 5)
    check(f"{case}: no overflow (firmware)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-firmware.png", full_page=True)

    # Install button behind every device a file is provided for; it opens
    # the usual installation dialog, which names the source of the update
    marker = f.evaluate("""() => { const s = document.querySelector('.fw-dev summary'), cs = getComputedStyle(s);
      return {text: s.textContent, list: cs.listStyleType, after: getComputedStyle(s, '::after').borderRightStyle}; }""")
    check(f"{case}: 'For n devices' with one chevron only", "▾" not in marker["text"] and marker["list"] == "none"
          and marker["after"] == "solid", str(marker))
    rows.first.locator(".fw-dev summary").click()
    entries = rows.first.locator(".fw-dev li")
    buttons = rows.first.locator(".fw-dev li button")
    # 5 ready devices + the failed installation (its file stays provided for a retry)
    # + the stored version offered while the update server could not be asked
    check(f"{case}: one Install button per device", entries.count() == 7 and buttons.count() == 7,
          f"{entries.count()} / {buttons.count()}")
    names = [t.replace("Install", "").strip() for t in entries.all_inner_texts()]
    check(f"{case}: devices ordered by name", names == sorted(names, key=str.lower) and "Kitchen light" in names, str(names))
    kitchen = entries.filter(has_text="Kitchen light").locator("button")   # got its update from the store
    check(f"{case}: Install enabled for a ready device", kitchen.is_enabled(), str(kitchen.get_attribute("title")))
    check(f"{case}: no overflow (firmware devices)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-firmware-devices.png", full_page=True)
    kitchen.click()
    d = dialog(f)
    check(f"{case}: installation dialog with the chip title icon", f.locator("#dlg-title svg.ti-chip").count() == 1)
    check(f"{case}: Install opens the installation dialog", d["title"] == "Install update?"
          and "Kitchen light" in d["body"] and "Firmware store (downloaded from Shelly earlier)" in d["body"], d["body"][:300])
    page.screenshot(path=out / f"{case}-firmware-install.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()   # nothing is installed in the demo
    rows.first.locator(".fw-dev summary").click()

    # Changelog icon per version – dialog with the stored changelog, disabled without one
    logs = f.locator(".fw-row .actions button[aria-label^='Changelog of']")
    check(f"{case}: one changelog icon per version", logs.count() == rows.count(), f"{logs.count()} / {rows.count()}")
    check(f"{case}: icon disabled without a stored changelog", logs.nth(1).is_disabled()
          and "No changelog stored" in logs.nth(1).get_attribute("title"), str(logs.nth(1).get_attribute("title")))
    if width < 700:   # touch: a tap on a disabled button shows why it is disabled
        logs.nth(1).tap(force=True)
        check(f"{case}: tap on a disabled button shows its reason", (tip_text(f) or "").startswith("No changelog stored"), str(tip_text(f)))
        f.locator("#fw-summary").tap()
        check(f"{case}: tap elsewhere hides it (firmware)", tip_text(f) is None)
    logs.first.click()
    f.wait_for_selector("#dlg-body .cl-list")
    d = dialog(f)
    check(f"{case}: changelog dialog with title icon", f.locator("#dlg-title svg.ti-changelog").count() == 1)
    check(f"{case}: changelog dialog of the stored version", d["title"] == "Changelog – Plus1 1.5.0"
          and "1.5.0 · 2026-10-01" in d["body"] and "Fixed a vulnerability" in d["body"] and "This model" in d["body"], d["body"][:300])
    check(f"{case}: changelog dialog fits", f.evaluate("() => { const r = document.getElementById('dlg').getBoundingClientRect();"
                                                       f" return r.left >= 0 && r.right <= {width}; }}"))
    page.screenshot(path=out / f"{case}-firmware-changelog.png")
    f.locator("#dlg-btns button", has_text="Close").click()
    return rows


def _checked(f, view):
    """Checked fields of the sort popover of a tab: [field, direction]."""
    return f.evaluate(f"""() => [...document.querySelectorAll('#view-{view} .sort-item[aria-checked=true]')]
      .map(b => b.dataset.value)""")


def _unused_chip(c):
    """Firmware chip "Unused": files of a firmware ID no device has; while a device's
    firmware ID is unknown (never reached) nothing counts as unused (tooltip says why)."""
    case, f, page = c.case, c.f, c.page
    unknown = {"on": False}

    def other_types(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        state = route.fetch(headers=headers).json()
        for d in state["devices"]:
            d["type"] = "OtherType"
        if unknown["on"]:
            state["devices"][0]["type"] = None
        route.fulfill(json=state)

    def unused():
        chip = f.locator("#fw-chips .chip", has_text="Unused")
        return int(chip.locator(".count").inner_text()), chip.get_attribute("title") or ""

    page.route("**/api/state", other_types)
    refresh_state(page, f)
    count, title = unused()
    check(f"{case}: Unused counts files without a device", count == f.locator(".fw-row").count() and not title, f"{count} {title}")
    unknown["on"] = True
    refresh_state(page, f)
    count, title = unused()
    check(f"{case}: unknown firmware ID blocks Unused", count == 0 and "1 device not known" in title.replace("ID of ", ""), f"{count} {title}")
    page.unroute("**/api/state")
    refresh_state(page, f)


def _sorting_and_downgrade(c):
    """Toolbar tools (search and sort icons with popovers; Devices and Firmware tab, kept
    per browser, coupled with the column heads on desktop) and the downgrade icon of an
    older version in the Firmware tab (dialog -> device -> second confirmation)."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    f.locator("#tab-devices").click()
    chips = f.evaluate("() => [...document.querySelectorAll('#dev-chips .chip')].map(c => c.firstChild.textContent)")
    check(f"{case}: Devices filter chips", chips == ["All", "Updates", "Errors", "Up-To-Date", "Unprotected", "Ignored"], str(chips))
    f.locator("#dev-chips .chip", has_text="Unprotected").click()
    shown = f.evaluate("() => [...document.querySelectorAll('#dev-cards tbody tr')].length")
    expected = f.locator("#dev-chips .chip", has_text="Unprotected").locator(".count").inner_text()
    check(f"{case}: Unprotected filter shows its count", str(shown) == expected, f"{shown} / {expected}")
    f.locator("#dev-chips .chip", has_text="All").click()
    if width < 700:   # header: pills stacked left, "Check now" right on the same line, not wrapped
        head = f.evaluate("""() => { const r = e => e.getBoundingClientRect(), m = r(document.getElementById('meta')),
          s = r(document.getElementById('scan')), p = [...document.querySelectorAll('#meta .meta-pill')].map(r);
          return {right: s.left >= m.right, mid: Math.abs((s.top + s.height / 2) - (m.top + m.height / 2)),
            stacked: p.length === 2 && p[1].top >= p[0].bottom, lines: Math.round(s.height)}; }""")
        check(f"{case}: header – pills stacked, Check now right and centred", head["right"] and head["mid"] < 2
              and head["stacked"] and head["lines"] <= 48, str(head))
    tools = f.locator("#view-devices .tool-btn")
    size = tools.first.evaluate("b => Math.round(b.getBoundingClientRect().width)")
    check(f"{case}: search and sort icons in the Devices toolbar", tools.count() == 2 and size == (44 if width < 700 else 36),
          f"{tools.count()} / {size}")
    sort = tools.nth(1)
    sort.click()
    pop = f.locator("#view-devices .tool.open .tool-pop")
    check(f"{case}: sort popover opens below the icon", pop.is_visible() and _checked(f, "devices") == ["device", "asc"],
          str(_checked(f, "devices")))
    if width < 700:   # phones: popover over the full width
        w = pop.evaluate("p => Math.round(p.getBoundingClientRect().width)")
        check(f"{case}: sort popover full width", w >= width - 20, str(w))
    page.screenshot(path=out / f"{case}-sort-popover.png")
    pop.locator(".sort-item", has_text="Descending").click()
    stored = f.evaluate("() => localStorage.getItem('shelly-firmware-manager:devices')")
    check(f"{case}: sort direction kept per browser", '"desc"' in (stored or ""), str(stored))
    page.keyboard.press("Escape")
    check(f"{case}: Escape closes the popover", f.locator("#view-devices .tool.open").count() == 0)
    if width > 700:
        head = f.locator("#dev-cards th.c-device").first.get_attribute("data-dir")
        check(f"{case}: column head follows the sort tool", head == "desc", str(head))
        f.locator("#dev-cards th.c-ip").first.click()
        check(f"{case}: sort tool follows the column head", _checked(f, "devices") == ["ip", "asc"], str(_checked(f, "devices")))
        f.locator("#dev-cards th.c-device").first.click()
    else:
        sort.click()
        f.locator("#view-devices .sort-item", has_text="Ascending").click()
        f.locator("#meta .meta-pill").first.click()   # a click outside closes the popover
        check(f"{case}: a click outside closes the popover", f.locator("#view-devices .tool.open").count() == 0)
    check(f"{case}: back to device name ascending", "asc" in f.evaluate(
        "() => localStorage.getItem('shelly-firmware-manager:devices')"))
    # Search: popover with the field, a dot on the icon while a text is set
    search = tools.first
    search.click()
    f.locator("#dev-search").fill("kitchen")
    page.keyboard.press("Enter")
    marked = search.evaluate("b => b.classList.contains('has-value') && getComputedStyle(b, '::after').content !== 'none'")
    rows = f.locator("#dev-cards tbody tr").count()
    check(f"{case}: search filters and marks the icon", marked and rows == 1, f"{marked} / {rows}")
    search.click()
    f.locator("#dev-search").fill("")
    page.keyboard.press("Enter")
    check(f"{case}: no dot without search text", not search.evaluate("b => b.classList.contains('has-value')"))
    f.locator("#tab-firmware").click()
    f.wait_for_selector(".fw-row")
    check(f"{case}: sort tool in the Firmware toolbar", _checked(f, "firmware") == ["type", "asc"], str(_checked(f, "firmware")))
    chips = f.evaluate("() => [...document.querySelectorAll('#fw-chips .chip')].map(c => c.firstChild.textContent)")
    check(f"{case}: Firmware filter chips", chips == ["All", "Provided", "Uploaded", "Intermediate", "Gen1", "Unused"], str(chips))
    _unused_chip(c)
    f.locator("#fw-chips .chip", has_text="Uploaded").click()
    tags = f.evaluate("() => [...document.querySelectorAll('.fw-row')].map(r => !!r.querySelector('.fw-ver .tag.warn'))")
    check(f"{case}: Uploaded filter shows only uploads", tags and all(tags), str(tags))
    f.locator("#fw-chips .chip", has_text="All").click()
    summary = f.locator("#fw-summary").text_content()
    check(f"{case}: free disk space in the summary", " free" in summary, summary)
    if width > 700:
        f.locator(".fw-head .sortable", has_text="Size").first.click()
        check(f"{case}: Firmware column head sorts the cards", _checked(f, "firmware")[0] == "size"
              and f.locator(".fw-head [data-dir]").count() == f.locator(".fw-head").count())
        f.locator("#view-firmware .tool-btn").nth(1).click()
        f.locator("#view-firmware .sort-item", has_text="Firmware ID").click()
        page.keyboard.press("Escape")
    icons = f.locator(".fw-row .actions button[aria-label^='Downgrade to']")
    enabled = [i for i in range(icons.count()) if icons.nth(i).is_enabled()]
    check(f"{case}: downgrade icon on older versions", bool(enabled), str(icons.count()))
    if enabled:
        icons.nth(enabled[0]).click()
        d = dialog(f)
        check(f"{case}: Firmware-tab downgrade dialog lists devices", d["title"].startswith("Downgrade to")
              and f.locator("#dlg-body .modes input[type=radio]").count() >= 1, d["title"])
        page.screenshot(path=out / f"{case}-fw-downgrade.png")
        f.locator("#dlg-btns button", has_text="Continue").click()
        d = dialog(f)
        check(f"{case}: then the second confirmation", d["title"] == "Downgrade?"
              and dict(d["buttons"]).get("Downgrade") is True, str(d["buttons"]))
        f.locator("#dlg-btns button", has_text="Cancel").click()


def _firmware_delete_and_upload(c, rows):
    """Two-step delete and the upload dialog (the last case changes the demo state)."""
    case, f, page, out, index, last = c.case, c.f, c.page, c.out, c.index, c.last
    # Two-step delete of 1.3.0
    row = rows.filter(has_text="1.3.0")
    row.locator("button.red").click()
    check(f"{case}: delete step 1", "Delete firmware?" == dialog(f)["title"])
    f.locator("#dlg-btns button", has_text="Continue").click()
    buttons = dict(dialog(f)["buttons"])
    check(f"{case}: delete needs acknowledgment", buttons.get("Delete") is True, str(buttons))
    f.locator("#dlg-body input[type=checkbox]").check()
    page.screenshot(path=out / f"{case}-delete-confirm.png")
    if last:
        f.locator("#dlg-btns button", has_text="Delete").click()
        check(f"{case}: deleted", wait_until(lambda: f.locator(".fw-row", has_text="1.3.0").count() == 0))
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()

    # Upload: pre-filled form; wrong SHA shows an inline error (first case), store (last case)
    f.locator("#fw-upload").click()
    check(f"{case}: upload dialog with title icon", f.locator("#dlg-title svg.ti-upload").count() == 1)
    f.locator("#dlg-body input[type=file]").set_input_files(
        files=[{"name": "plüs1-1.1.0.zip", "mimeType": "application/zip", "buffer": firmware_zip()}])
    page.screenshot(path=out / f"{case}-upload-choose.png")
    f.locator("#dlg-btns button", has_text="Upload").click()
    f.wait_for_selector("#dlg-body select")
    check(f"{case}: type pre-filled", f.locator("#dlg-body select").input_value() == "Plus1")
    check(f"{case}: version pre-filled", f.locator("#dlg-body input[type=text]").first.input_value() == "1.1.0")
    check(f"{case}: upload confirmation with title icon", f.locator("#dlg-title svg.ti-upload").count() == 1)
    if index == 0:
        f.locator("#dlg-body input[type=text]").nth(1).fill("a" * 64)
        f.locator("#dlg-btns button", has_text="Store").click()
        err = f.locator("#dlg-body .bad")
        check(f"{case}: wrong SHA-256 shown inline", wait_until(err.is_visible) and "does not match" in err.text_content())
        page.screenshot(path=out / f"{case}-upload-error.png")
    page.screenshot(path=out / f"{case}-upload-confirm.png")
    if last:
        f.locator("#dlg-btns button", has_text="Store").click()
        check(f"{case}: upload stored", wait_until(lambda: f.locator(".fw-row", has_text="1.1.0").count() == 1))
        page.screenshot(path=out / f"{case}-firmware-after.png", full_page=True)
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()
        f.wait_for_selector("#dlg", state="hidden")


def _logs(c):
    """Logs tab: group bar, coloured findings, deleting an entry / the whole log (last case)."""
    case, f, page, out, last = c.case, c.f, c.page, c.out, c.last
    # Logs – group bar, findings coloured by severity, delete one entry / all (last case)
    f.locator("#tab-logs").click()
    entries = f.locator("#log-list .log, #log-list .log-m")
    entries.first.wait_for()
    total = entries.count()
    check(f"{case}: three consecutive entries of one device grouped", f.locator("#log-list .grp").count() == 3
          and f.locator("#log-list .grp-first").count() == 1 and f.locator("#log-list .grp-last").count() == 1)
    check(f"{case}: delete icon per entry", f.locator("#log-list .log-del").count() == total, str(total))
    if c.width > 700:   # new log texts: check after an intermediate version, unverified installation
        text = f.locator("#log-list").inner_text()
        check(f"{case}: check after an intermediate version and unverified installation in the log",
              "After-intermediate-version check" in text and "Not verified by the update server" in text)
    if c.width > 700:   # desktop rows: user name and delete icon on one line
        offsets = f.evaluate("""() => [...document.querySelectorAll('#log-list .log')].filter(r => r.querySelector('.user').textContent)
          .map(r => { const m = e => { const b = e.getBoundingClientRect(); return b.top + b.height / 2; };
            const icon = r.querySelector('.log-del svg'); return Math.abs(m(r.querySelector('.user')) - m(icon)); })""")
        check(f"{case}: user name and delete icon on one line", offsets and max(offsets) < 1.5, str(offsets[:5]))
    else:   # phone tiles: user top right, beside the first summary line
        pos = f.evaluate("""() => { const t = document.querySelector('#log-list .log-m summary .first'), r = e => e.getBoundingClientRect();
          const u = t.querySelector('.user'), x = t.firstChild, tile = t.closest('.log-m');
          return {line: Math.abs(r(u).bottom - r(x).bottom) < 4, right: r(tile).right - r(u).right < 20, user: u.textContent}; }""")
        check(f"{case}: log tile – user top right beside the first line", pos["line"] and pos["right"] and pos["user"], str(pos))
    scheduled = f.locator("#log-list .log, #log-list .log-m", has_text="Firmware downloaded").first
    check(f"{case}: download names the user of its check", "Schedule" in scheduled.inner_text() if c.width > 700 else True)
    f.locator("#log-list summary", has_text="30.7 s").first.click()
    colours = f.evaluate("""() => { const ul = [...document.querySelectorAll('#log-list ul')].find(u => u.textContent.includes('Sauna'));
      return [...ul.children].map(li => li.className); }""")
    check(f"{case}: findings coloured by their own severity", colours == ["okc", "warnc", "bad"], str(colours))
    check(f"{case}: no overflow (logs)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-logs.png", full_page=True)
    f.locator("#log-list .log-del").last.click()
    check(f"{case}: delete entry asks first", dialog(f)["title"] == "Delete log entry?")
    if last:
        f.locator("#dlg-btns button", has_text="Delete").click()
        check(f"{case}: entry deleted", wait_until(lambda: entries.count() == total - 1), str(entries.count()))
        f.locator("#log-clear").click()
        d = dialog(f)
        check(f"{case}: clear asks first", d["title"] == "Delete all logs?" and str(total - 1) in d["body"], d["body"][:120])
        f.locator("#dlg-btns button", has_text="Delete all").click()
        wait_until(lambda: entries.count() == 1)
        check(f"{case}: log cleared, one entry names the user", entries.count() == 1 and "Log cleared" in entries.first.inner_text(),
              entries.first.inner_text()[:80])
        page.screenshot(path=out / f"{case}-logs-cleared.png")
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()


def firmware_store(browser, out):
    """Device list, downgrade, Firmware tab and Logs tab – one helper per area (the last
    case changes the demo state). Quick: a phone (embedded) and a desktop viewport;
    --full adds 430 px dark and 1440 px light."""
    cases = (((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False)) if FULL
             else ((360, "light", True), (1440, "dark", False)))
    for index, (width, scheme, embedded) in enumerate(cases):
        last = index == len(cases) - 1
        case = f"features-{width}-{scheme}{'-embedded' if embedded else ''}"
        entry = "Downgrade"
        if index == 0:   # No stored older version (demo "idle") -> entry disabled, no dialog
            ctx, page, f, errors = open_page(browser, IDLE, width, scheme, embedded)
            items = dict(menu_items(f, "Hall"))
            check(f"{case}: downgrade disabled without a stored older version", items.get(entry) is True, str(items))
            ctx.close()
            # Option "Allow downgrades" off (demo "missing"): disabled with the Settings hint
            ctx, page, f, errors = open_page(browser, MISSING, width, scheme, embedded)
            items = dict(menu_items(f, "Hall"))
            hint = f.locator("#rowmenu .menu-hint").all_inner_texts()
            check(f"{case}: downgrade off – entry disabled with hint", items.get(entry) is True
                  and any("Allow downgrades" in h for h in hint), str(hint))
            page.screenshot(path=out / f"{case}-downgrade-off.png")
            ctx.close()
        ctx, page, f, errors = open_page(browser, FEATURES, width, scheme, embedded, ignore=("/upload/confirm",))

        c = SimpleNamespace(case=case, f=f, page=page, out=out, width=width, index=index, last=last)
        _device_list(c)
        _layout(c)
        _keep_listed(c)
        _action_popups(c)
        _name_tips(c)
        _next_steps(c)
        _downgrade(c)
        rows = _firmware_tab(c)
        _sorting_and_downgrade(c)
        _firmware_delete_and_upload(c, rows)
        _logs(c)

        check(f"{case}: no overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()


def _export_import(c, page, f, out):
    """Export with the generated password, then import the downloaded file again."""
    f.click("#store-export")
    f.wait_for_selector("#dlg-body .pw-row input")
    password = f.input_value("#dlg-body .pw-row input")
    d = dialog(f)
    check(f"{c}: export dialog", d["title"] == "Export firmware store" and "Files" in d["body"], d["body"][:120])
    check(f"{c}: generated password, 5 groups", len(password) == 24 and password.count("-") == 4, password)
    check(f"{c}: copy button visible", f.locator('#dlg-body .pw-row button[aria-label="Copy password"]').is_visible())
    # Without the Clipboard API (no secure context, e.g. HA over plain HTTP) the password is
    # copied through a selected helper field inside the modal dialog (outside it is inert)
    f.evaluate("""() => { Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});
      window.__copied = null; document.addEventListener('copy', () => { window.__copied = String(getSelection()); }, {once: true}); }""")
    f.locator('#dlg-body .pw-row button[aria-label="Copy password"]').click()
    copied = wait_until(lambda: f.evaluate("() => window.__copied") is not None) and f.evaluate("() => window.__copied")
    check(f"{c}: copy without Clipboard API", copied == password, str(copied))
    full = f.evaluate("() => { const i = document.querySelector('#dlg-body .pw-row input'); return i.scrollWidth <= i.clientWidth; }")
    check(f"{c}: password not cut off", full)
    check(f"{c}: Export disabled before the acknowledgment", d["buttons"][-1] == ["Export", True], str(d["buttons"]))
    f.locator("#dlg-body label.check input").check()
    check(f"{c}: Export enabled after it", dialog(f)["buttons"][-1] == ["Export", False])
    page.screenshot(path=out / f"store-export-{c}.png")
    with page.expect_download() as info:
        f.locator("#dlg-btns button", has_text="Export").click()
    path = out / f"export-{c}.zip"
    info.value.save_as(path)
    check(f"{c}: export downloaded", zipfile.is_zipfile(path) and "manifest.json" in zipfile.ZipFile(path).namelist())
    check(f"{c}: success message", wait_until(lambda: "downloads it" in dialog(f)["body"]))
    f.click("#dlg-x")
    # Import: wrong password first, then the right one
    for pw, ok in (("AAAA-BBBB-CCCC-DDDD-EEEE", False), (password.lower(), True)):
        f.click("#store-import")
        f.wait_for_selector('#dlg-body input[type="file"]')
        f.set_input_files('#dlg-body input[type="file"]', str(path))
        f.fill("#dlg-body input.mono", pw)
        f.locator("#dlg-btns button", has_text="Check file").click()
        if not ok:
            check(f"{c}: wrong password refused", wait_until(lambda: "Wrong export password" in dialog(f)["body"]))
            f.click("#dlg-x")
            continue
        check(f"{c}: import checked", wait_until(lambda: "nothing was changed" in dialog(f)["body"]))
        d = dialog(f)
        check(f"{c}: counts (already stored)", "1 already stored" in d["body"].replace("\n", " "), d["body"][:300])
        check(f"{c}: finish button", d["buttons"][-1][0] == "Finish import", str(d["buttons"]))
        page.screenshot(path=out / f"store-import-{c}.png")
        f.locator("#dlg-btns button", has_text="Finish import").click()
        check(f"{c}: import done", wait_until(lambda: "0 new files stored" in dialog(f)["body"]), dialog(f)["body"][:200])
        f.click("#dlg-x")


def _import_in_parts(c, page, f, out):
    """A file above the 16 MiB Ingress limit is sent in parts of 8 MiB (here: a ZIP
    of 17 MiB without manifest – refused after the transfer, not by its size)."""
    path = out / "big-import.zip"
    if not path.exists():
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("data.bin", bytes(range(256)) * (17 * 4096))
    parts = []
    page.on("request", lambda r: parts.append(r.url) if r.url.endswith("/api/firmware/import") else None)
    f.click("#store-import")
    f.wait_for_selector('#dlg-body input[type="file"]')
    f.set_input_files('#dlg-body input[type="file"]', str(path))
    f.fill("#dlg-body input.mono", "AAAA-BBBB-CCCC-DDDD-EEEE")
    f.locator("#dlg-btns button", has_text="Check file").click()
    check(f"{c}: large file transferred, then checked",
          wait_until(lambda: "manifest.json missing" in dialog(f)["body"], 30), dialog(f)["body"][:120])
    check(f"{c}: sent in 3 parts", len(parts) == 3, str(len(parts)))
    f.click("#dlg-x")


def _bulk_passwords(c, page, f, out):
    """Dialog for several devices: steps per device, selection, confirmation."""
    f.click("#set-passwords")
    f.wait_for_selector("#dlg-body .bulk-list")
    d = dialog(f)
    rows = f.locator("#dlg-body .bulk-row").count()
    check(f"{c}: one row per device", rows == 25, str(rows))
    check(f"{c}: set is the default action", f.locator('#dlg-body input[value="set"]').is_checked())
    fits = f.locator("#dlg-body .bulk-row:not(.off)").count()
    check(f"{c}: count of fitting devices", f"All devices that fit ({fits} of 25)" in d["body"], d["body"][:200])
    check(f"{c}: protected without stored password greyed out", "store the current password first" in d["body"])
    check(f"{c}: step per device", "→ enable protection" in d["body"])
    check(f"{c}: red button with the count", d["buttons"][-1][0] == f"Set on {fits} devices …", str(d["buttons"]))
    f.locator("#dlg-body label.check", has_text="Store in this app").locator("input").uncheck()
    check(f"{c}: not stored: red box", f.locator("#dlg-body .warnbox.danger").count() == 1)
    page.screenshot(path=out / f"bulk-passwords-{c}.png", full_page=True)
    f.locator('#dlg-body input[value="store"]').check()
    d = dialog(f)
    stores = f.locator("#dlg-body .bulk-row:not(.off)").count()
    check(f"{c}: store mode: protected devices", "→ test and store" in d["body"] and stores > 0 and stores != fits, str(stores))
    check(f"{c}: store mode: no repeat field", not f.locator("#dlg-body label.field", has_text="Repeat password").is_visible())
    f.locator('#dlg-body input[value="set"]').check()
    f.locator("#dlg-btns button").last.click()
    check(f"{c}: password required", wait_until(lambda: "No password entered" in dialog(f)["body"]))
    f.click("#dlg-x")
    f.click("#set-passwords")
    f.wait_for_selector("#dlg-body .bulk-list")
    f.locator("#dlg-body .bulk-all input").uncheck()
    check(f"{c}: nothing selected: button disabled", dialog(f)["buttons"][-1][1] is True)
    f.locator("#dlg-body .bulk-all input").check()
    for field in f.locator('#dlg-body input[type="password"]').all():
        field.fill("Demo-Pass-1")
    f.locator("#dlg-btns button").last.click()
    check(f"{c}: second confirmation", wait_until(lambda: "I have stored the password securely" in dialog(f)["body"]))
    check(f"{c}: confirm disabled before the acknowledgment", dialog(f)["buttons"][-1][1] is True)
    f.click("#dlg-x")


def store_transfer(browser, out):
    """Firmware store export/import, device passwords for several devices, first start."""
    cases = ((360, "light", True), (1440, "dark", False))
    for width, scheme, embedded in cases:
        c = f"{width}-{scheme}{'-embedded' if embedded else ''}"
        # The wrong export password is refused on purpose (HTTP 400)
        ctx, page, f, errors = open_page(browser, IDLE, width, scheme, embedded, ignore=("import/check",))
        check(f"{c}: no first-start notice", f.locator("#notice").is_hidden())
        f.click("#tab-settings")
        f.wait_for_selector("#store-export")
        heads = f.locator("#settings-form h2").all_text_contents()
        check(f"{c}: cards in the Settings tab", heads == ["Checks", "Download Server", "Firmware & Storage", "Logs", "Advanced",
                                                          "Device Passwords"], str(heads))
        order = f.evaluate("""() => [...document.querySelectorAll('#settings-form .form > *')]
          .map(e => e.classList.contains('set-store') ? 'store' : (e.querySelector('input,select,textarea,.pins') || {}).id || '')
          .filter(x => ['opt-allow_downgrade', 'store', 'opt-import_max_mb'].includes(x))""")
        check(f"{c}: store block between Allow downgrades and Max. import size",
              order == ["opt-allow_downgrade", "store", "opt-import_max_mb"], str(order))
        check(f"{c}: import size option", f.locator("#opt-import_max_mb").input_value() == "100")
        page.screenshot(path=out / f"store-cards-{c}.png", full_page=True)
        _export_import(c, page, f, out)
        _import_in_parts(c, page, f, out)
        _bulk_passwords(c, page, f, out)
        check(f"{c}: no overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{c}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()
    for index, (width, scheme, embedded) in enumerate(cases):
        c = f"first-{width}-{scheme}{'-embedded' if embedded else ''}"
        ctx, page, f, errors = open_page(browser, FIRST, width, scheme, embedded)
        check(f"{c}: welcome dialog", wait_until(lambda f=f: dialog(f)["title"] == "Welcome – import a firmware store?"))
        check(f"{c}: buttons", dialog(f)["buttons"] == [["Skip", False], ["Import store …", False]], str(dialog(f)["buttons"]))
        page.screenshot(path=out / f"first-start-dialog-{c}.png")
        f.click("#dlg-x")
        notice = f.locator("#notice")
        check(f"{c}: notice bar", notice.is_visible() and "First start" in notice.text_content())
        check(f"{c}: Check now disabled", f.locator("#scan").is_disabled() and "first-start" in (f.get_attribute("#scan", "title") or ""))
        page.screenshot(path=out / f"first-start-notice-{c}.png")
        f.locator("#notice button", has_text="Import store …").click()
        check(f"{c}: import dialog from the notice", wait_until(lambda f=f: dialog(f)["title"] == "Import firmware store"))
        f.click("#dlg-x")
        page.reload()
        f = ui_frame(page, embedded)
        f.wait_for_selector("#dev-cards table")
        page.wait_for_timeout(300)
        check(f"{c}: dialog only once", not f.evaluate("() => document.getElementById('dlg').open"))
        check(f"{c}: notice stays until answered", f.locator("#notice").is_visible())
        if index == len(cases) - 1:   # changes the demo state: last case only
            f.locator("#notice button", has_text="Skip").click()
            check(f"{c}: Skip removes the notice", wait_until(f.locator("#notice").is_hidden))
            check(f"{c}: Check now enabled", wait_until(f.locator("#scan").is_enabled))
        check(f"{c}: no overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{c}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()


SUITES = {"download_url_hint": download_url_hint, "setting_help": setting_help, "download_url_notice": download_url_notice,
          "firmware_store": firmware_store, "store_transfer": store_transfer}


def main():
    """Run the selected suites (default: all, in the documented order)."""
    args = [a for a in sys.argv[1:] if a != "--full"]
    out = Path(args[0] if args else "shots")
    out.mkdir(parents=True, exist_ok=True)
    selected = args[1:] or list(SUITES)
    with sync_playwright() as pw:
        browser = launch(pw)
        for name in selected:
            SUITE[0] = name
            SUITES[name](browser, out)
        browser.close()
    failed = [r for r in results if not r[1]]
    for name in selected:
        mine = [r for r in results if r[0].startswith(name + ":")]
        print(f"{name}: {sum(r[1] for r in mine)}/{len(mine)} passed")
    for name, _ok, detail in failed:
        print("FAIL", name, detail)
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
