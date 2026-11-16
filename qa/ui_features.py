"""Feature UI checks (Playwright) against the demo servers – one file for all
feature suites (3.0.1; formerly lan_hint_test.py, rc6_ui_test.py, rc7_ui_test.py).

Suites, in this order (the last two change the demo state in their last case):
- download_url_hint   Settings: hints for download_base_url (LAN IP, public IP,
                      localhost, host names). Demo "idle" on 8801.
- download_url_notice notice for a missing / unconfirmed download base URL, device
                      progress of a Gen2+ installation. Demos "legacy" 8803, "missing" 8804.
- firmware_store      downgrade from the device menu, upload, two-step delete, the
                      Install button behind a device and the changelog icon in the
                      Firmware tab; 3.1.1: Install button and "Updated" in the device
                      list, state colours of the action icons, log grouping and
                      deleting log entries; 3.2.0: failed installation in the updates
                      card, dialog sections, phone tile layout, no tooltip after a tap,
                      user in the log tile. Demo "rc7" on 8805 (and "idle" on 8801).
Usage: python qa/ui_features.py <shots-dir> [suite ...]   (qa/run_ui.py starts the demos)
Every suite checks: no horizontal overflow, no console errors; screenshots go to
<shots-dir>."""

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

from common import launch
from playwright.sync_api import sync_playwright

IDLE, LEGACY, MISSING, RC7 = (f"http://127.0.0.1:{p}" for p in (8801, 8803, 8804, 8805))
OVERFLOW_JS = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
results = []  # (suite: name, ok, detail)
SUITE = [""]  # name of the running suite (prefix of the result names)


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
    page.wait_for_timeout(500)
    frame = page.frames[1] if embedded else page
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
    f.wait_for_timeout(150)
    return f.evaluate("() => [...document.querySelectorAll('#rowmenu button')].map(b => [b.textContent, b.disabled])")


def dialog(f):
    """Title, body text and buttons [(text, disabled)] of the open dialog."""
    return f.evaluate("""() => ({title: document.getElementById('dlg-title').textContent,
      body: document.getElementById('dlg-body').innerText,
      buttons: [...document.querySelectorAll('#dlg-btns button')].map(b => [b.textContent, b.disabled])})""")


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
    if width < 700:   # phone tile: lines, flush button rows, chevron
        row = card.locator("tr", has_text="Attic fan")
        if not row.locator("td.c-details details").evaluate("d => d.open"):
            row.locator("td.c-details summary").click()
        row.locator(".act-strip.labeled").wait_for()
        geo = row.evaluate("""tr => { const r = e => e.getBoundingClientRect(), mid = e => r(e).top + r(e).height / 2;
          const q = s => tr.querySelector(s), strip = q('.act-strip.labeled'), buttons = [...strip.children];
          const rows = {}; buttons.forEach(b => { const t = Math.round(r(b).top); rows[t] = Math.max(rows[t] || 0, r(b).right); });
          return {check: Math.abs(mid(q('td.c-select input')) - mid(q('.name'))), menu: Math.abs(mid(q('td.c-menu button svg')) - mid(q('.name'))),
            status: Math.abs(mid(q('.status-cell .pill')) - mid(q('.shelly-name'))),
            flush: Object.values(rows).map(x => Math.round(r(strip).right - x)),
            chevron: getComputedStyle(q('td.c-details summary'), '::after').borderRightStyle,
            top: Math.round(r(q('.name')).top - r(tr).top), bottom: Math.round(r(tr).bottom - r(q('dl.tile-kv')).bottom)}; }""")
        check(f"{case}: tile – checkbox and menu on the name line, status on the Shelly-name line",
              geo["check"] < 2 and geo["menu"] < 2 and geo["status"] < 2, str(geo))
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
    check(f"{case}: delete dialog names the devices that lose the provided update",
          "lose the provided update" in d["body"] or "loses the provided update" in d["body"], d["body"][:200])
    c.page.screenshot(path=c.out / f"{case}-delete-provided.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    check(f"{case}: upload tag in the list", f.locator(".fw-row .tag.warn", has_text="Uploaded").count() == 1)
    check(f"{case}: no overflow (firmware)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-firmware.png", full_page=True)

    # Install button behind every device a file is provided for; it opens
    # the usual installation dialog, which names the source of the update
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
    logs.first.click()
    f.wait_for_selector("#dlg-body .cl-list")
    d = dialog(f)
    check(f"{case}: changelog dialog of the stored version", d["title"] == "Changelog – Plus1 1.5.0"
          and "1.5.0 · 2026-10-01" in d["body"] and "Fixed a vulnerability" in d["body"] and "This model" in d["body"], d["body"][:300])
    check(f"{case}: changelog dialog fits", f.evaluate("() => { const r = document.getElementById('dlg').getBoundingClientRect();"
                                                       f" return r.left >= 0 && r.right <= {width}; }}"))
    page.screenshot(path=out / f"{case}-firmware-changelog.png")
    f.locator("#dlg-btns button", has_text="Close").click()
    return rows


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
        f.wait_for_timeout(1200)
        check(f"{case}: deleted", f.locator(".fw-row", has_text="1.3.0").count() == 0)
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()

    # Upload: pre-filled form; wrong SHA shows an inline error (430), store (last case)
    f.locator("#fw-upload").click()
    f.locator("#dlg-body input[type=file]").set_input_files(
        files=[{"name": "plüs1-1.1.0.zip", "mimeType": "application/zip", "buffer": firmware_zip()}])
    page.screenshot(path=out / f"{case}-upload-choose.png")
    f.locator("#dlg-btns button", has_text="Upload").click()
    f.wait_for_selector("#dlg-body select")
    check(f"{case}: type pre-filled", f.locator("#dlg-body select").input_value() == "Plus1")
    check(f"{case}: version pre-filled", f.locator("#dlg-body input[type=text]").first.input_value() == "1.1.0")
    if index == 1:
        f.locator("#dlg-body input[type=text]").nth(1).fill("a" * 64)
        f.locator("#dlg-btns button", has_text="Store").click()
        f.wait_for_timeout(500)
        err = f.locator("#dlg-body .bad")
        check(f"{case}: wrong SHA-256 shown inline", err.is_visible() and "does not match" in err.text_content())
        page.screenshot(path=out / f"{case}-upload-error.png")
    page.screenshot(path=out / f"{case}-upload-confirm.png")
    if last:
        f.locator("#dlg-btns button", has_text="Store").click()
        f.wait_for_timeout(1200)
        check(f"{case}: upload stored", f.locator(".fw-row", has_text="1.1.0").count() == 1)
        page.screenshot(path=out / f"{case}-firmware-after.png", full_page=True)
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()
        f.wait_for_timeout(300)


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
        f.wait_for_timeout(800)
        check(f"{case}: entry deleted", entries.count() == total - 1, str(entries.count()))
        f.locator("#log-clear").click()
        d = dialog(f)
        check(f"{case}: clear asks first", d["title"] == "Delete all logs?" and str(total - 1) in d["body"], d["body"][:120])
        f.locator("#dlg-btns button", has_text="Delete all").click()
        f.wait_for_timeout(800)
        check(f"{case}: log cleared, one entry names the user", entries.count() == 1 and "Log cleared" in entries.first.inner_text(),
              entries.first.inner_text()[:80])
        page.screenshot(path=out / f"{case}-logs-cleared.png")
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()


def firmware_store(browser, out):
    """Device list, downgrade, Firmware tab and Logs tab in four viewports – one helper
    per area (the last case changes the demo state)."""
    cases = ((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False))
    for index, (width, scheme, embedded) in enumerate(cases):
        last = index == len(cases) - 1
        case = f"rc7-{width}-{scheme}{'-embedded' if embedded else ''}"
        entry = "Downgrade"
        if index == 0:   # No stored older version (demo "idle") -> entry disabled, no dialog
            ctx, page, f, errors = open_page(browser, IDLE, width, scheme, embedded)
            items = dict(menu_items(f, "Hall"))
            check(f"{case}: downgrade disabled without a stored older version", items.get(entry) is True, str(items))
            ctx.close()
        ctx, page, f, errors = open_page(browser, RC7, width, scheme, embedded, ignore=("/upload/confirm",))

        c = SimpleNamespace(case=case, f=f, page=page, out=out, width=width, index=index, last=last)
        _device_list(c)
        _layout(c)
        _next_steps(c)
        _downgrade(c)
        rows = _firmware_tab(c)
        _firmware_delete_and_upload(c, rows)
        _logs(c)

        check(f"{case}: no overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()


SUITES = {"download_url_hint": download_url_hint, "download_url_notice": download_url_notice,
          "firmware_store": firmware_store}


def main():
    """Run the selected suites (default: all, in the documented order)."""
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
    out.mkdir(parents=True, exist_ok=True)
    selected = sys.argv[2:] or list(SUITES)
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
