"""Feature suite store_transfer: firmware store export/import, device passwords, first start
(demos "idle" 8801, "first" 8806)."""

import zipfile

from features.base import FIRST, IDLE, OVERFLOW_JS, check, dialog, open_page, ui_frame, wait_until


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
    full = f.evaluate("() => { const i = document.querySelector('#dlg-body .pw-row input'); return i.scrollWidth <= i.clientWidth + 1; }")
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
        check(f"{c}: import disabled before the acknowledgment", d["buttons"][-1][1] is True, str(d["buttons"]))
        check(f"{c}: imported files marked", "stored as “Imported”" in d["body"], d["body"][:300])
        f.locator("#dlg-body label.check input").check()
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
    unfit = f.evaluate("""() => { const det = document.querySelector('#dlg-body .bulk-off');
      return det && {last: det === det.parentElement.lastElementChild, open: det.open, rows: det.querySelectorAll('.bulk-row.off').length,
        title: det.querySelector('summary').textContent}; }""")
    check(f"{c}: devices that do not fit collapsed at the end", bool(unfit) and unfit["last"] and not unfit["open"]
          and unfit["rows"] == 25 - fits and unfit["title"] == f"{25 - fits} not possible", str(unfit))
    f.click("#dlg-body .bulk-off summary")
    check(f"{c}: protected without stored password greyed out", "store the current password first" in dialog(f)["body"])
    check(f"{c}: step per device", "→ enable protection" in d["body"])
    # Step 1 is "Continue" in blue; the red button with the count follows in the confirmation
    check(f"{c}: step 1 Continue in blue", d["buttons"][-1][0] == "Continue"
          and f.locator("#dlg-btns button").last.get_attribute("class") == "primary", str(d["buttons"]))
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
    check(f"{c}: red button with the count", dialog(f)["buttons"][-1][0] == f"Set on {fits} devices"
          and f.locator("#dlg-btns button").last.get_attribute("class") == "danger", str(dialog(f)["buttons"]))
    f.click("#dlg-x")


def _allow_downgrades_popup(c, page, f, out):
    """Ticking "Allow downgrades": popup with the red warning, "Enable" only after the
    acknowledgment; Cancel and Escape take the tick back (the demo has the option on)."""
    box = f.locator("#opt-allow_downgrade")
    box.uncheck()
    check(f"{c}: no popup when downgrades are switched off", not f.evaluate("() => document.getElementById('dlg').open"))
    box.check()
    d = dialog(f)
    check(f"{c}: popup when downgrades are switched on", d["title"] == "Allow downgrades?"
          and f.locator("#dlg-body .warnbox.danger").count() == 1 and "Applies only after saving." in d["body"], d["body"][:120])
    check(f"{c}: Enable disabled until the risk is acknowledged", d["buttons"] == [["Cancel", False], ["Enable", True]], str(d["buttons"]))
    page.screenshot(path=out / f"settings-downgrade-popup-{c}.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    check(f"{c}: Cancel takes the tick back", wait_until(lambda: not box.is_checked()))
    box.check()
    page.keyboard.press("Escape")
    check(f"{c}: Escape takes the tick back", wait_until(lambda: not box.is_checked()))
    box.check()
    f.locator("#dlg-body input[type=checkbox]").check()
    check(f"{c}: Enable is red and enabled after the acknowledgment", dialog(f)["buttons"] == [["Cancel", False], ["Enable", False]]
          and f.locator("#dlg-btns button").last.get_attribute("class") == "danger")
    f.locator("#dlg-btns button", has_text="Enable").click()
    check(f"{c}: Enable keeps the tick", wait_until(lambda: box.is_checked()
                                                    and not f.evaluate("() => document.getElementById('dlg').open")))


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
        # Cards in independent column stacks (1440 px: two, phones: one); card n stands in
        # stack n mod columns, so reading the stacks row by row gives the card order
        stacks = f.evaluate("""() => [...document.querySelectorAll('#settings-form .set-stack')]
          .map(s => [...s.querySelectorAll('h2')].map(h => h.textContent))""")
        heads = [s[i] for i in range(max(map(len, stacks), default=0)) for s in stacks if i < len(s)]
        check(f"{c}: cards in the Settings tab", len(stacks) == (1 if width < 1168 else 2) and heads == [
            "Device Passwords", "Checks", "Download Server", "Firmware & Storage", "Backups", "Logs", "Advanced", "Display"], str(stacks))
        order = f.evaluate("""() => [...document.querySelectorAll('#settings-form .form > *')]
          .map(e => e.classList.contains('set-store') ? 'store' : (e.querySelector('input,select,textarea,.pins') || {}).id || '')
          .filter(x => ['opt-allow_downgrade', 'store', 'opt-import_max_mb'].includes(x))""")
        check(f"{c}: store block between Allow downgrades and Max. import size",
              order == ["opt-allow_downgrade", "store", "opt-import_max_mb"], str(order))
        check(f"{c}: import size option", f.locator("#opt-import_max_mb").input_value() == "100")
        _allow_downgrades_popup(c, page, f, out)
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
        check(f"{c}: buttons", dialog(f)["buttons"] == [["Skip", False], ["Import store", False]], str(dialog(f)["buttons"]))
        page.screenshot(path=out / f"first-start-dialog-{c}.png")
        f.click("#dlg-x")
        notice = f.locator("#notice")
        check(f"{c}: notice bar", notice.is_visible() and "First start" in notice.text_content())
        check(f"{c}: Check now disabled", f.locator("#scan").is_disabled() and "first-start" in (f.get_attribute("#scan", "data-tip") or ""))
        page.screenshot(path=out / f"first-start-notice-{c}.png")
        f.locator("#notice button", has_text="Import store").click()
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
