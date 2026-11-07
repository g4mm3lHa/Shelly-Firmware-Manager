"""rc7 UI check: downgrade from the device menu, manual upload and two-step delete in
the Firmware tab, no automatic deletion hint.

Needs demo.py on port 8805 (scenario rc7: stored older versions 1.3.1, 1.3.0 and an
uploaded 1.2.0; devices run 1.4.0, 1.5.0 is provided).
Usage: python qa/rc7_ui_test.py <shots-dir>
Cases: 360 px light embedded, 430 px dark, 1440 px dark and light. Checks: menu entry
only for Gen2+ (also ignored devices), version list (all older, newest first, upload
marked), installation dialog with the older target, second confirmation needs the
acknowledgment, Firmware tab with total size, lock reason for a provided file,
two-step delete, upload with pre-filled firmware ID and version, inline error for a
wrong SHA-256, no horizontal overflow, no console errors. The last case really
uploads and deletes (it changes the demo state). Saves screenshots."""

import io
import json
import sys
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8805"
CHROME = "/home/user/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
OVERFLOW_JS = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
results = []


def check(name, ok, detail=""):
    """Record one check."""
    results.append((name, bool(ok), detail))


def firmware_zip():
    """Upload test file: ZIP with a manifest naming Plus1 1.1.0."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"name": "Plus1", "build_id": "20240101-000000/1.1.0-gabc"}))
        archive.writestr("app.bin", b"\x01" * 20000)
    return buf.getvalue()


def open_page(browser, width, scheme, embedded):
    """New page on the demo; returns (context, page, frame, errors)."""
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, has_touch=width <= 700)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
    page.on("response", lambda r, errs=errors: errs.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and not r.url.endswith("/favicon.ico") and "/upload/confirm" not in r.url else None)
    page.on("console", lambda m, errs=errors: errs.append(m.text)
            if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.goto(BASE + ("/wrap" if embedded else "/ui/"))
    page.wait_for_timeout(500)
    frame = page.frames[1] if embedded else page
    frame.wait_for_selector("#dev-cards table")
    return ctx, page, frame, errors


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


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME)
    cases = ((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False))
    for index, (width, scheme, embedded) in enumerate(cases):
        last = index == len(cases) - 1
        case = f"rc7-{width}-{scheme}{'-embedded' if embedded else ''}"
        ctx, page, f, errors = open_page(browser, width, scheme, embedded)
        entry = "Install older version …"

        # Menu entry: Gen2+ (also ignored), never Gen1
        items = dict(menu_items(f, "Hall"))
        check(f"{case}: menu entry for Gen2", entry in items and items[entry] is False, str(items))
        page.screenshot(path=OUT / f"{case}-menu.png")
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
        page.screenshot(path=OUT / f"{case}-downgrade-list.png")
        f.locator("#dlg-body .modes input").nth(1).check()

        # Step 2: installation dialog with the older target
        f.locator("#dlg-btns button", has_text="Continue").click()
        d = dialog(f)
        check(f"{case}: install dialog with older target", d["title"] == "Install older version?" and "Target (older)" in d["body"]
              and "1.3.0" in d["body"] and "A downgrade installs an older firmware" in d["body"], d["body"][:200])
        page.screenshot(path=OUT / f"{case}-downgrade-install.png")

        # Step 3: second confirmation
        f.locator("#dlg-btns button", has_text="Continue").click()
        d = dialog(f)
        go = dict(d["buttons"]).get("Install older version")
        check(f"{case}: confirmation button disabled", go is True, str(d["buttons"]))
        f.locator("#dlg-body input[type=checkbox]").check()
        go = dict(dialog(f)["buttons"]).get("Install older version")
        check(f"{case}: enabled after acknowledgment", go is False)
        page.screenshot(path=OUT / f"{case}-downgrade-confirm.png")
        f.locator("#dlg-btns button", has_text="Cancel").click()   # nothing is installed in the demo

        # Firmware tab
        f.locator("#tab-firmware").click()
        f.wait_for_selector(".fw-row")
        summary = f.locator("#fw-summary").text_content()
        check(f"{case}: total size shown", summary.startswith("4 files") and "MB stored" in summary, summary)
        tip = f.locator("#fw-info").get_attribute("data-tip")
        check(f"{case}: no automatic deletion hint", "never deleted automatically" in tip, tip[:60])
        rows = f.locator(".fw-row")
        first_del = rows.first.locator("button[aria-label], button.red").last
        check(f"{case}: provided file locked with reason", first_del.is_disabled()
              and first_del.get_attribute("title") == "Locked: provided as pending update", str(first_del.get_attribute("title")))
        check(f"{case}: upload tag in the list", f.locator(".fw-row .tag.warn", has_text="Uploaded").count() == 1)
        check(f"{case}: no overflow (firmware)", f.evaluate(OVERFLOW_JS) <= 0)
        page.screenshot(path=OUT / f"{case}-firmware.png", full_page=True)

        # Two-step delete of 1.3.0
        row = rows.filter(has_text="1.3.0")
        row.locator("button.red").click()
        check(f"{case}: delete step 1", "Delete firmware?" == dialog(f)["title"])
        f.locator("#dlg-btns button", has_text="Continue").click()
        buttons = dict(dialog(f)["buttons"])
        check(f"{case}: delete needs acknowledgment", buttons.get("Delete") is True, str(buttons))
        f.locator("#dlg-body input[type=checkbox]").check()
        page.screenshot(path=OUT / f"{case}-delete-confirm.png")
        if last:
            f.locator("#dlg-btns button", has_text="Delete").click()
            f.wait_for_timeout(1200)
            check(f"{case}: deleted", f.locator(".fw-row", has_text="1.3.0").count() == 0)
        else:
            f.locator("#dlg-btns button", has_text="Cancel").click()

        # Upload: pre-filled form; wrong SHA shows an inline error (430), store (last case)
        f.locator("#fw-upload").click()
        f.locator("#dlg-body input[type=file]").set_input_files(
            files=[{"name": "plus1-1.1.0.zip", "mimeType": "application/zip", "buffer": firmware_zip()}])
        page.screenshot(path=OUT / f"{case}-upload-choose.png")
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
            page.screenshot(path=OUT / f"{case}-upload-error.png")
        page.screenshot(path=OUT / f"{case}-upload-confirm.png")
        if last:
            f.locator("#dlg-btns button", has_text="Store").click()
            f.wait_for_timeout(1200)
            check(f"{case}: upload stored", f.locator(".fw-row", has_text="1.1.0").count() == 1)
            page.screenshot(path=OUT / f"{case}-firmware-after.png", full_page=True)
        else:
            f.locator("#dlg-btns button", has_text="Cancel").click()
            f.wait_for_timeout(300)

        check(f"{case}: no overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()
    browser.close()

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    if not ok:
        print("FAIL", name, detail)
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
