"""Feature suite backups: Backups tab, the "Backups" dialog of a device, restore
(differences, network tick, progress, result), download and import of one backup,
"Back up now", the entry points in the other tabs, the unread mark of the Logs tab, the
page behind an open dialog, the keyboard focus (redraw, row menu, dialog, sorting), the
contrast of coloured text and the six tabs in both languages (demo "features" on 8808,
used by this suite only: its cases store, delete and restore backups)."""

import json
import re
from types import SimpleNamespace

from features.base import BACKUPS, FULL, OVERFLOW_JS, check, dialog, menu_items, open_page, refresh_state, wait_until

GERMAN = "shelly-firmware-manager:language"  # browser storage key of the language choice (i18n.js)
TABS = ("devices", "firmware", "schedules", "backups", "logs", "settings")
TABS_JS = """() => [...document.querySelectorAll('.tab')].map(t => { const r = t.getBoundingClientRect();
  return [Math.round(r.left), Math.round(r.right), t.scrollWidth > t.clientWidth]; })"""
# True if the content of a cell of a device row reaches into the next cell of the same line
CELLS_OVERLAP_JS = """() => [...document.querySelectorAll('#bk-cards .bk-row:not(.bk-head):not(.bk-none)')].some(row => {
  const cells = [...row.querySelectorAll('.bk-c')];
  return cells.some((cell, i) => { const next = cells[i + 1], last = cell.lastElementChild;
    return next && last && cell.getBoundingClientRect().top === next.getBoundingClientRect().top
      && last.getBoundingClientRect().right > next.getBoundingClientRect().left + 1; }); })"""

# The focused node as "TAG|label" (accessible name, else its text)
ACTIVE_JS = """() => { const a = document.activeElement;
  return a ? a.tagName + '|' + (a.getAttribute('aria-label') || a.textContent.trim().slice(0, 60)) : ''; }"""
# True if the border of the first node matching the selector has the strong line colour
STRONG_BORDER_JS = """sel => { const probe = document.createElement('div'); probe.style.border = '1px solid var(--line-strong)';
  document.body.append(probe); const want = getComputedStyle(probe).borderTopColor; probe.remove();
  return getComputedStyle(document.querySelector(sel)).borderTopColor === want; }"""
# Contrast (WCAG) of the text of the first node matching each selector against the
# background behind it (the nearest opaque one), read from the rendered pixels' colours
CONTRAST_JS = """sels => { const cv = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgba = c => { cv.clearRect(0, 0, 1, 1); cv.fillStyle = c; cv.fillRect(0, 0, 1, 1); return [...cv.getImageData(0, 0, 1, 1).data]; };
  const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  return sels.map(sel => { const n = document.querySelector(sel); if (!n) return [sel, null];
    let bg = [255, 255, 255, 255];
    for (let p = n; p; p = p.parentElement) { const c = rgba(getComputedStyle(p).backgroundColor); if (c[3] === 255) { bg = c; break; } }
    const a = lum(rgba(getComputedStyle(n).color)), b = lum(bg);
    return [sel, Math.round((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) * 100) / 100]; }); }"""


def _open_tab(c):
    """The Backups tab with its cards rendered."""
    c.f.locator("#tab-backups").click()
    c.f.wait_for_selector("#bk-cards .card.backups")


def _tab(c):
    """Summary with the lock, both cards, the backups of a device below its row and the
    filter "No backup"."""
    case, f = c.case, c.f
    _open_tab(c)
    summary = f.locator("#bk-summary").inner_text()
    check(f"{case}: summary names backups, devices and the encryption", " backups of " in summary and "Stored encrypted" in summary,
          summary)
    titles = f.locator("#bk-cards .card-head h2").all_inner_texts()
    check(f"{case}: cards Backups and No Backup", len(titles) == 2 and titles[0].startswith("Backups · ")
          and titles[1].startswith("No Backup · "), str(titles))
    first = f.locator("#bk-cards .bk-row:not(.bk-head)").first
    text = first.inner_text()
    check(f"{case}: device row – latest backup, reason and count", "Kitchen light" in text and "Manual" in text
          and " of 10 + 2 kept" in text, text[:200])
    first.locator(".bk-tog").click()
    rows = f.locator("#bk-cards .bk-list .bk-item")
    wait_until(lambda: rows.count() > 1)
    row = rows.nth(rows.count() - 1)
    labels = [b.get_attribute("aria-label") or b.inner_text() for b in row.locator("button").all()]
    check(f"{case}: backup row – Restore, download, keep, delete", len(labels) == 4 and labels[0] == "Restore"
          and labels[1].startswith("Download") and "Kept" in labels[2] and labels[3].startswith("Delete"), str(labels))
    check(f"{case}: kept backup marked", row.locator(".bk-pin").count() == 1, row.inner_text()[:120])
    check(f"{case}: no overflow (backups)", f.evaluate(OVERFLOW_JS) <= 0)
    if c.width <= 700:
        check(f"{case}: phone tiles of the devices with the strong line", f.evaluate(STRONG_BORDER_JS, "#bk-cards .bk-row:not(.bk-head)"))
    c.page.screenshot(path=c.out / f"{case}-backups.png", full_page=not c.embedded)
    removed = f.locator("#bk-cards .bk-row", has_text="Old plug").first
    check(f"{case}: backups of a device that is no longer listed stay, no new backup",
          "no longer listed" in removed.inner_text() and removed.locator(".bk-btns button").is_disabled())
    none = f.locator("#bk-cards .bk-none")
    reasons = " | ".join(none.locator(".bk-why-none").all_inner_texts())
    check(f"{case}: No Backup names the reason", all(part in reasons for part in (
        "No backup yet.", "its password is not stored", "Battery device", "The last attempt failed")), reasons[:300])
    locked = none.filter(has_text="its password is not stored").first.locator("button")
    check(f"{case}: no backup without the device password", locked.is_disabled())
    first.locator(".bk-tog").click()


def _entry_points(c):
    """Row menu and device details of the Devices tab, the "Backups" dialog of a device,
    the check line of the installation dialog."""
    case, f = c.case, c.f
    f.locator("#tab-devices").click()
    f.wait_for_selector("#dev-cards table")
    items = [text for text, _disabled in menu_items(f, "Kitchen light")]
    check(f"{case}: row menu – Backups after Device password", "Backups" in items
          and items.index("Backups") == items.index("Device password") + 1, str(items))
    f.locator("#rowmenu button", has_text="Device details").click()
    f.wait_for_selector("#dlg-body")
    body = dialog(f)["body"]
    check(f"{case}: device details – section Backups", "BACKUPS" in body.upper() and "Last backup" in body and "Stored" in body,
          body[-200:])
    f.locator("#dlg-body button", has_text="Show backups").click()
    f.wait_for_selector("#dlg .bk-list")
    d = dialog(f)
    check(f"{case}: dialog Backups – pill and buttons", d["title"] == "Backups" and " of 10 stored" in d["body"]
          and [b[0] for b in d["buttons"]] == ["Import backup", "Close", "Back up now"], f"{d['buttons']} {d['body'][:80]}")
    inside = f.evaluate("""() => { const d = document.getElementById('dlg-body'), w = d.getBoundingClientRect();
      return [...d.querySelectorAll('.bk-item button')].every(b => { const r = b.getBoundingClientRect(),
        t = b.closest('.bk-item').querySelector('.bk-date').getBoundingClientRect();
        return r.right <= w.right + 1 && (r.left >= t.right || r.top >= t.bottom); }); }""")
    check(f"{case}: dialog Backups – buttons beside or below the date, inside the dialog", inside)
    c.page.screenshot(path=c.out / f"{case}-backups-device.png")
    f.locator("#dlg-btns button", has_text="Close").click()
    row = f.locator("#dev-cards tr", has_text="Kitchen light").first
    row.scroll_into_view_if_needed()
    row.locator("button.install").click()
    f.wait_for_selector("#dlg-body .prechk")
    lines = f.locator("#dlg-body .prechk > li").all_inner_texts()
    check(f"{case}: installation dialog – check line Backup", any(line.startswith("Backup") for line in lines), str(lines))
    f.locator("#dlg-btns button", has_text="Cancel").click()


def _page_behind_dialog(c):
    """An open dialog: the page behind it does not scroll, a click outside does not
    close it, the dialog itself scrolls; closing gives the page back."""
    case, f, page = c.case, c.f, c.page
    f.locator("#tab-devices").click()
    f.wait_for_selector("#dev-cards table")
    f.evaluate("() => scrollTo(0, 300)")
    menu_items(f, "Kitchen light")
    f.locator("#rowmenu button", has_text="Device details").click()
    f.wait_for_selector("#dlg[open]")
    before = f.evaluate("() => scrollY")
    locked = f.evaluate("() => getComputedStyle(document.documentElement).overflowY")
    box = f.locator("#dlg").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, max(2, box["y"] - 20) if box["y"] > 30 else box["y"] + box["height"] + 10)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(200)
    check(f"{case}: page behind the dialog does not scroll", locked == "hidden" and f.evaluate("() => scrollY") == before,
          f"{locked} {before} -> {f.evaluate('() => scrollY')}")
    f.evaluate("() => document.getElementById('dlg').dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: 2, clientY: 2}))")
    page.mouse.click(3, 3)
    page.wait_for_timeout(150)
    check(f"{case}: click outside does not close the dialog", f.locator("#dlg[open]").count() == 1)
    scrolls = f.evaluate("""() => { const b = document.getElementById('dlg-body'); b.scrollTop = 9999;
      return b.scrollHeight <= b.clientHeight || b.scrollTop > 0; }""")
    check(f"{case}: the dialog scrolls", scrolls)
    f.locator("#dlg-x").click()
    state_js = "() => [getComputedStyle(document.documentElement).overflowY, document.body.style.paddingRight, scrollY]"
    wait_until(lambda: f.evaluate(state_js)[0] != "hidden")  # the dialog reports its closing in the next task
    freed = f.evaluate(state_js)
    check(f"{case}: page scrolls again after closing", freed[0] != "hidden" and freed[1] == "" and freed[2] == before, str(freed))
    f.evaluate("() => scrollTo(0, 0)")


def _restore(c):
    """Restore of an older backup: differences per part, network settings unticked and
    behind their own confirmation, progress, result and the backup "Before restore"."""
    case, f, page = c.case, c.f, c.page
    _open_tab(c)
    f.locator("#bk-cards .bk-row:not(.bk-head)").first.locator(".bk-tog").click()
    row = f.locator("#bk-cards .bk-list .bk-item", has_text="before rewiring").first
    row.locator("button", has_text="Restore").click()
    f.wait_for_selector("#dlg .bk-parts-list")
    d = dialog(f)
    ticks = f.evaluate("() => [...document.querySelectorAll('#dlg .bk-part input')].map(i => [i.checked, i.disabled])")
    check(f"{case}: restore – checks and parts", d["title"] == "Restore backup?" and "Same device" in d["body"]
          and "Device reachable" in d["body"] and "4 differences" in d["body"], d["body"][:300])
    check(f"{case}: restore – parts ticked, network not, password protection fixed",
          ticks == [[True, False]] * 4 + [[False, False], [False, True]], str(ticks))
    table = f.locator("#dlg table.bk-diff").first.inner_text()
    check(f"{case}: restore – differences with the keys of the Shelly", "sys · device.name" in table and "Kitchen light (new)" in table
          and "On the device" in table and "In the backup" in table, table[:200])
    danger = f.locator("#dlg .warnbox.danger")
    hidden = danger.is_hidden()
    f.locator("#dlg .bk-part input").nth(4).check()
    check(f"{case}: restore – red box with the network settings", hidden and danger.is_visible())
    text = danger.inner_text()
    check(f"{case}: restore – red box names the other Wi-Fi and the password that is not restored",
          "another Wi-Fi (SSID): “Home” instead of “Home-IoT”" in text and "password is not part of the backup" in text, text[-300:])
    f.evaluate("() => document.querySelectorAll('#dlg details').forEach(d => { d.open = true; })")
    fits = f.evaluate("""() => { const b = document.getElementById('dlg-body');
      return b.scrollWidth <= b.clientWidth && [...b.querySelectorAll('table.bk-diff th')].every(h => h.scrollWidth <= h.clientWidth); }""")
    check(f"{case}: restore – differences fit the dialog", fits)
    page.screenshot(path=c.out / f"{case}-restore.png")
    f.locator("#dlg-btns button", has_text="Continue").click()
    f.wait_for_selector("#dlg .bk-sure")
    d = dialog(f)
    check(f"{case}: restore step 2 – changes and Restore behind the network tick", "1 written, 1 removed" in d["body"]
          and "Before restore" in d["body"] and d["buttons"] == [["Back", False], ["Restore", True]], f"{d['buttons']} {d['body'][:200]}")
    f.locator("#dlg .bk-sure input").check()
    f.locator("#dlg-btns button", has_text="Restore").click()
    f.wait_for_selector("#dlg .prechk li.run")
    steps = f.locator("#dlg .prechk > li").all_inner_texts()
    check(f"{case}: restore – progress per step", len(steps) == 7 and steps[0].startswith("Backup “Before restore”")
          and steps[-1].startswith("Reading the settings again"), str(steps))
    page.screenshot(path=c.out / f"{case}-restore-progress.png")
    done = wait_until(lambda: "restored" in dialog(f)["body"], timeout=30)
    d = dialog(f)
    check(f"{case}: restore – result per part", done and d["body"].count("applied and read back") == 5
          and "Before restore" in d["body"], d["body"][:400])
    page.screenshot(path=c.out / f"{case}-restore-result.png")
    f.locator("#dlg-btns button", has_text="Close").click()
    newest = f.locator("#bk-cards .bk-row:not(.bk-head)").first
    wait_until(lambda: "Before restore" in newest.inner_text())
    check(f"{case}: restore – backup “Before restore” is the newest", "Before restore" in newest.inner_text(), newest.inner_text()[:120])
    newest.locator(".bk-tog").click()


def _download_import(c):
    """Download with the password shown once, import of the same file (stored already),
    delete, import again (marked Imported and Kept), a wrong password leads back."""
    case, f, page = c.case, c.f, c.page
    _open_tab(c)
    device = f.locator("#bk-cards .bk-row:not(.bk-head)", has_text="Hall").first
    device.locator(".bk-tog").click()
    row = f.locator("#bk-cards .bk-list .bk-item").nth(1)
    row.locator("button").nth(1).click()
    f.wait_for_selector("#dlg .bk-pw")
    d = dialog(f)
    password = f.locator("#dlg .bk-pw").input_value()
    check(f"{case}: download – password shown, Download after the tick", d["title"] == "Download backup" and len(password) >= 20
          and d["buttons"] == [["Cancel", False], ["Download", True]] and ".sfmb" in d["body"], f"{d['buttons']} {len(password)}")
    page.screenshot(path=c.out / f"{case}-backup-download.png")
    f.locator("#dlg .bk-sure input").check()
    with page.expect_download() as download:
        f.locator("#dlg-btns button", has_text="Download").click()
    path = c.out / f"{case}-backup.sfmb"
    download.value.save_as(path)
    blob = path.read_bytes()
    check(f"{case}: download – sealed file", len(blob) > 200 and b"ssid" not in blob and b"Hall" not in blob, str(len(blob)))
    f.locator("#dlg-btns button", has_text="Close").click()

    def import_file(secret):
        if not f.locator("#dlg[open]").count():  # from the "Backups" dialog of the device
            f.locator("#tab-devices").click()
            menu_items(f, "Hall")
            f.locator("#rowmenu button", has_text="Backups").click()
            f.wait_for_selector("#dlg-btns button")
        f.locator("#dlg-btns button", has_text="Import backup").click()
        f.wait_for_selector("#dlg input[type=file]")
        check_button = f.locator("#dlg-btns button", has_text="Check file")
        blocked = check_button.is_disabled()
        f.locator("#dlg input[type=file]").set_input_files(str(path))
        f.locator("#dlg .bk-form input[type=text]").fill(secret)
        check_button.click()
        wait_until(lambda: f.locator("#dlg .prechk, #dlg-body .msg, #dlg-body p.bad:not([hidden])").count() > 0, timeout=15)
        return blocked

    blocked = import_file(password)
    d = dialog(f)
    check(f"{case}: import – Check file needs file and password", blocked)
    check(f"{case}: import – a stored backup is not imported twice", "cannot be imported" in d["body"] and "Already stored" in d["body"]
          and d["buttons"] == [["Close", False]], d["body"][:200])
    f.locator("#dlg-btns button", has_text="Close").click()
    f.wait_for_selector("#dlg .bk-list, #dlg .warnbox")
    f.locator("#dlg .bk-item button").last.click()
    wait_until(lambda: dialog(f)["title"] == "Delete backup?")
    f.locator("#dlg-btns button", has_text="Delete").click()
    wait_until(lambda: dialog(f)["title"] == "Backups" and "No backup" in dialog(f)["body"])
    check(f"{case}: delete – the device has no backup left", "No backup" in dialog(f)["body"], dialog(f)["body"][:120])
    import_file("wrong-password")
    d = dialog(f)
    kept = f.evaluate("""() => { const file = document.querySelector('#dlg input[type=file]'), pw = document.querySelector('#dlg .bk-form input[type=text]');
      return !!file && file.files.length === 1 && !!pw && pw.value === 'wrong-password'; }""")
    check(f"{case}: import – wrong password refused, the error stays in the step with file and password",
          "Wrong password" in d["body"] and kept and d["buttons"] == [["Cancel", False], ["Check file", False]], d["body"][:120])
    f.locator("#dlg-btns button", has_text="Cancel").click()
    f.wait_for_selector("#dlg-btns button:text-is('Import backup')")
    import_file(password)
    d = dialog(f)
    check(f"{case}: import – check list and Import backup", "can be imported" in d["body"] and "Seal intact" in d["body"]
          and "Same device" in d["body"] and "Not stored yet" in d["body"] and d["buttons"][-1] == ["Import backup", False], d["body"][:300])
    check(f"{case}: import – “Not stored yet” is a green line",
          f.locator("#dlg .prechk > li.ok", has_text="Not stored yet").count() == 1)
    page.screenshot(path=c.out / f"{case}-backup-import.png")
    f.locator("#dlg-btns button", has_text="Import backup").click()
    f.wait_for_selector("#dlg .bk-list")
    text = f.locator("#dlg .bk-item").first.inner_text()
    check(f"{case}: import – marked Imported and Kept", "Imported" in text and "Kept" in text, text[:120])
    f.locator("#dlg-btns button", has_text="Close").click()


def _back_up_now(c):
    """One device from the card "No Backup", then all Shellys: progress and the result per device."""
    case, f, page = c.case, c.f, c.page
    _open_tab(c)
    f.locator("#bk-cards .bk-none", has_text="Terrace").locator("button").click()
    done = wait_until(lambda: "Backup of Terrace stored" in dialog(f)["body"], timeout=15)
    check(f"{case}: Back up now – one device", done, dialog(f)["body"][:160])
    f.locator("#dlg-btns button", has_text="Close").click()
    f.locator("#bk-now").click()
    f.wait_for_selector("#dlg .modes")
    d = dialog(f)
    check(f"{case}: Back up now – all Shellys chosen", d["buttons"][-1][0].startswith("Back up ") and "All Shellys (" in d["body"]
          and "Selected devices" in d["body"], f"{d['buttons']} {d['body'][:80]}")
    f.locator("#dlg .modes input[type=radio]").nth(1).check()
    check(f"{case}: Back up now – nothing selected, nothing to start", f.locator("#dlg-btns button").last.is_disabled())
    f.locator("#dlg .modes input[type=radio]").first.check()
    f.locator("#dlg-btns button").last.click()
    f.wait_for_selector("#dlg .bk-progress")
    done = wait_until(lambda: "devices backed up." in dialog(f)["body"], timeout=60)
    d = dialog(f)
    check(f"{case}: Back up now – result per device", done and "Stored · " in d["body"] and "Not stored – " in d["body"]
          and "battery device asleep" in d["body"], d["body"][:300])
    check(f"{case}: no overflow (backup result)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=c.out / f"{case}-backup-run.png")
    f.locator("#dlg-btns button", has_text="Close").click()
    wait_until(lambda: f.locator(".notice.bk-job").count() == 0)
    check(f"{case}: Back up now – no bar left after the result was closed", f.locator(".notice.bk-job").count() == 0)


def _logs(c):
    """Logs tab: the "!" only for unread error entries, unread entries highlighted while
    the tab is open for the first time, filter "Backups"."""
    case, f, page = c.case, c.f, c.page
    f.locator("#tab-devices").click()
    refresh_state(page, f)
    mark = f.locator("#tab-logs .tab-unread")
    check(f"{case}: Logs – no mark for warnings and successes", mark.is_hidden())

    def state(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        s = route.fetch(headers=headers).json()
        s["log_latest_error"] = s["log_latest"]
        route.fulfill(json=s)

    page.route("**/api/state", state)
    refresh_state(page, f)
    check(f"{case}: Logs – mark for an unread error entry", mark.is_visible() and mark.inner_text().startswith("!"), mark.inner_text())
    red = f.evaluate("""() => { const probe = document.createElement('div'); probe.style.background = 'var(--error-color)';
      document.body.append(probe); const want = getComputedStyle(probe).backgroundColor; probe.remove();
      return getComputedStyle(document.querySelector('#tab-logs .tab-unread')).backgroundColor === want; }""")
    check(f"{case}: Logs – the mark is red", red)
    page.screenshot(path=c.out / f"{case}-logs-mark.png")
    page.unroute("**/api/state")
    f.locator("#tab-logs").click()
    f.wait_for_selector("#log-list .log, #log-list .log-m")
    unread = f.locator("#log-list .unread")
    wait_until(lambda: unread.count() > 0)
    total = f.locator("#log-list .log, #log-list .log-m").count()
    check(f"{case}: Logs – unread entries highlighted, older ones not", 0 < unread.count() < total, f"{unread.count()} of {total}")
    check(f"{case}: Logs – mark gone in the tab", mark.is_hidden())
    page.screenshot(path=c.out / f"{case}-logs-unread.png")
    kinds = f.evaluate("() => [...document.querySelectorAll('#view-logs .filter-item')].map(b => b.firstChild.textContent)")
    check(f"{case}: Logs – filter Backups", "Backups" in kinds, str(kinds))
    # "Back up now" for all Shellys (run before): one entry for the run, its devices below it
    entries = f.locator("#log-list .log, #log-list .log-m")
    run = entries.filter(has_text="Backup run").first
    line = run.locator("summary").first.inner_text()
    found = re.search(r"Backup run · manual · (\d+) devices · \d+ s · \d+ stored", line)
    devices = run.locator("li").count()
    check(f"{case}: Logs – one entry for a run of “Back up now”, one line per device", bool(found) and devices == int(found[1]),
          f"{line} / {devices}")
    single = entries.filter(has_text="backup stored · manual").count()
    check(f"{case}: Logs – no entry per device of the run", single <= 2, str(single))
    f.locator("#tab-devices").click()
    f.locator("#tab-logs").click()
    f.wait_for_selector("#log-list .log, #log-list .log-m")
    check(f"{case}: Logs – nothing highlighted at the next visit", unread.count() == 0)


def _other_tabs(c):
    """Schedules: the action "Backup" with its result; Settings: card "Backups"."""
    case, f = c.case, c.f
    f.locator("#tab-schedules").click()
    card = f.locator("#view-schedules .sch-row, #view-schedules tr", has_text="Weekly backup").first
    card.wait_for()
    text = card.inner_text()
    check(f"{case}: schedule Backup – stored, unchanged, not backed up", "3 stored · 17 unchanged · 2 not backed up" in text, text[:200])
    if c.width <= 700:
        check(f"{case}: phone tiles of the schedules with the strong line", f.evaluate(STRONG_BORDER_JS, "#view-schedules .sch-row:not(.sch-head)"))
    f.locator("#tab-settings").click()
    f.wait_for_selector("#view-settings .card")
    card = f.locator("#view-settings .card", has=f.locator(".card-head h2", has_text="Backups")).first
    text = card.inner_text()
    check(f"{case}: settings – card Backups with both options and the storage note", "Back up before every installation" in text
          and "Keep backups per device" in text and "encrypted" in text, text[:300])
    check(f"{case}: no overflow (settings)", f.evaluate(OVERFLOW_JS) <= 0)


def _keyboard(c):
    """Keyboard: the focus survives a redraw, the row menu and a dialog give it back, a
    dialog is named by its title and keeps the focus inside at a new step, the space bar
    sorts, the tabs name the chosen one."""
    case, f, page = c.case, c.f, c.page
    f.locator("#tab-devices").click()
    f.wait_for_selector("#dev-cards table")
    tabs = f.evaluate("() => [...document.querySelectorAll('[role=tab]')].map(t => [t.getAttribute('aria-selected'), t.getAttribute('aria-controls')])")
    check(f"{case}: keyboard – tabs name the chosen one and their views",
          tabs == [[str(tab == "devices").lower(), f"view-{tab}"] for tab in TABS], str(tabs))
    button = f.locator("#dev-cards td.c-menu button[aria-label='Actions for Kitchen light']")
    button.focus()
    own = f.evaluate(ACTIVE_JS)
    f.evaluate("() => document.getElementById('dev-search').dispatchEvent(new Event('input'))")  # redraws the list
    check(f"{case}: keyboard – focus stays on its control when the list is redrawn", f.evaluate(ACTIVE_JS) == own
          and f.evaluate("() => document.activeElement.isConnected"), f.evaluate(ACTIVE_JS))
    page.keyboard.press("Enter")
    f.wait_for_selector("#rowmenu:not([hidden])")
    inside = f.evaluate("() => document.getElementById('rowmenu').contains(document.activeElement)")
    page.keyboard.press("Escape")
    check(f"{case}: keyboard – Escape closes the row menu and returns the focus", inside and f.locator("#rowmenu").is_hidden()
          and f.evaluate(ACTIVE_JS) == own, f.evaluate(ACTIVE_JS))
    page.keyboard.press("Enter")
    f.locator("#rowmenu button", has_text="Device password").click()
    f.wait_for_selector("#dlg[open]")
    named = f.evaluate("() => document.getElementById(document.getElementById('dlg').getAttribute('aria-labelledby')).textContent")
    check(f"{case}: keyboard – dialog named by its title, focus on its first control", "Device password" in named
          and f.evaluate("() => document.activeElement.id") == "dlg-x", named)
    f.locator("#dlg-btns button").last.click()  # next step without a password: the error stays in the step
    f.wait_for_selector("#dlg [role=alert]:visible")
    check(f"{case}: keyboard – an input error keeps the focus in the dialog and is announced",
          f.evaluate("() => document.getElementById('dlg').contains(document.activeElement)")
          and "No password entered." in f.locator("#dlg [role=alert]").inner_text(), f.evaluate(ACTIVE_JS))
    page.keyboard.press("Escape")
    wait_until(lambda: f.evaluate(ACTIVE_JS) == own)
    check(f"{case}: keyboard – closing the dialog returns the focus", f.evaluate(ACTIVE_JS) == own, f.evaluate(ACTIVE_JS))
    head = f.locator("#dev-cards th.c-device").first
    head.focus()
    page.keyboard.press("Space")
    order = f.evaluate("() => { const a = document.activeElement; return [a.tagName, a.getAttribute('aria-sort')]; }")
    page.keyboard.press("Space")
    again = f.evaluate("() => { const a = document.activeElement; return [a.tagName, a.getAttribute('aria-sort')]; }")
    check(f"{case}: keyboard – the space bar sorts, the head keeps the focus and names the order",
          order == ["TH", "descending"] and again == ["TH", "ascending"], f"{order} {again}")


def _contrast(c):
    """Coloured text is mixed towards the text colour: at least 4.5:1 for the active tab,
    links, a new version, error text and grey tags (the fallback colours of the app)."""
    case, f = c.case, c.f
    f.locator("#tab-devices").click()
    f.wait_for_selector("#dev-cards table")
    values = f.evaluate(CONTRAST_JS, [".tab.active", "#dev-cards td a", "#dev-cards .newver", "#dev-cards .tag:not([class*=' '])"])
    f.locator("#tab-logs").click()
    f.wait_for_selector("#log-list .log, #log-list .log-m")
    values += f.evaluate(CONTRAST_JS, ["#log-list .bad"])
    check(f"{case}: contrast of coloured text at least 4.5:1", all(value is not None and value >= 4.5 for _sel, value in values), str(values))


def _languages(browser, out, width, scheme, embedded):
    """Six tabs: every tab inside the viewport with its whole label, in English and
    German; German terms of the Backups tab; no overflow."""
    for lang in ("en", "de"):
        case = f"backups-{lang}-{width}-{scheme}{'-embedded' if embedded else ''}"
        script = f"localStorage.setItem({json.dumps(GERMAN)}, {json.dumps(json.dumps(lang))})"
        ctx, page, f, errors = open_page(browser, BACKUPS, width, scheme, embedded, init=script)
        for tab in TABS:
            f.locator(f"#tab-{tab}").click()
            page.wait_for_timeout(200)
            tabs = f.evaluate(TABS_JS)
            check(f"{case}: six tabs inside the viewport, labels not cut ({tab})", len(tabs) == 6
                  and all(left >= 0 and right <= width and not cut for left, right, cut in tabs), str(tabs))
            check(f"{case}: no overflow ({tab})", f.evaluate(OVERFLOW_JS) <= 0)
        f.locator("#tab-backups").click()
        f.wait_for_selector("#bk-cards .card.backups")
        if lang == "de":
            text = f.locator("#view-backups").inner_text()
            check(f"{case}: German terms", all(term in text for term in ("Jetzt sichern", "Kein Backup", "Verschlüsselt gespeichert")
                                                 ) and f.locator("#tab-backups").inner_text().strip() == "Backups", text[:200])
            f.locator("#bk-cards .bk-row:not(.bk-head)").first.locator(".bk-tog").click()
            f.locator("#bk-cards .bk-list .bk-item button").first.wait_for()
            text = f.locator("#bk-cards .bk-list").inner_text()
            check(f"{case}: German terms of a backup", "Wiederherstellen" in text and ("Anlass" in text or width <= 700), text[:200])
        check(f"{case}: no overflow (backups)", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: columns of a device row do not run into each other", not f.evaluate(CELLS_OVERLAP_JS))
        page.screenshot(path=out / f"{case}-backups.png", full_page=not embedded)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()


def backups(browser, out):
    """Quick: a phone (embedded) and a desktop viewport, the tabs also in German; --full
    adds 412 px embedded dark, 768 px light and 1440 px dark. The cases that change the
    demo state (restore, download and import, back up now) run once, on the desktop."""
    cases = (((360, "light", True), (412, "dark", True), (768, "light", False), (1440, "light", False), (1440, "dark", False))
             if FULL else ((360, "light", True), (1440, "light", False)))
    for number, (width, scheme, embedded) in enumerate(cases):
        case = f"backups-{width}-{scheme}{'-embedded' if embedded else ''}"
        # a file with a wrong password is refused on purpose (400)
        ctx, page, f, errors = open_page(browser, BACKUPS, width, scheme, embedded, ignore=("/api/backups/import/check",))
        c = SimpleNamespace(case=case, f=f, page=page, out=out, width=width, embedded=embedded)
        _tab(c)
        _entry_points(c)
        _page_behind_dialog(c)
        _other_tabs(c)
        if not embedded:
            _contrast(c)  # the colours of the app itself, not those of a theme
        if width > 700:
            _keyboard(c)
        if number == len(cases) - 1:
            _restore(c)
            _download_import(c)
            _back_up_now(c)
            _logs(c)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()
        _languages(browser, out, width, scheme, embedded)
