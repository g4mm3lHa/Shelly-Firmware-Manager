"""Feature suite schedules: Schedules tab, "Install at" in the installation dialog, a
scheduled installation in the device list, a running downgrade and the German layout
(demo "features" on 8805; nothing is saved, the demo state stays as it is)."""

import json
from types import SimpleNamespace

from features.base import FEATURES, FULL, OVERFLOW_JS, check, dialog, menu_items, open_page, refresh_state, wait_until

GERMAN = "shelly-firmware-manager:language"  # browser storage key of the language choice (i18n.js)


def _schedules_tab(c):
    """Both cards with their rows; a schedule saved without a name keeps the dialog open
    with the error in it (nothing is saved)."""
    case, f, page = c.case, c.f, c.page
    f.locator("#tab-schedules").click()
    heads = f.locator("#view-schedules .card-head h2")
    heads.first.wait_for()
    titles = heads.all_inner_texts()
    check(f"{case}: Schedules tab – both cards", len(titles) == 2 and titles[0].endswith("· 2") and titles[1].endswith("· 1"),
          str(titles))
    check(f"{case}: no overflow (schedules)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=c.out / f"{case}-schedules.png", full_page=not c.embedded)
    f.locator("#view-schedules button", has_text="Add schedule").first.click()
    f.wait_for_selector("#dlg-body input")
    f.locator("#dlg-btns button", has_text="Save").click()
    error = f.locator("#dlg-body p.bad[role=alert]")
    shown = wait_until(lambda: error.count() > 0 and error.first.inner_text().strip() != "")
    d = dialog(f)
    check(f"{case}: schedule without a name – error in the dialog, dialog stays open",
          shown and d["title"] == "Add schedule", f"{d['title']} / {d['body'][-120:]}")
    f.locator("#dlg-btns button", has_text="Cancel").click()


def _install_at(c):
    """Installation dialog: "Install at" changes the button, the date picker spans 7
    days from today (time zone of Home Assistant); the planned device shows "Scheduled"
    and its downgrade is blocked with the hint."""
    case, f, page = c.case, c.f, c.page
    f.locator("#tab-devices").click()
    f.wait_for_selector("#dev-cards table")
    row = f.locator("#dev-cards tr", has_text="Kitchen light").first
    row.scroll_into_view_if_needed()
    row.locator("button.install").click()
    f.wait_for_selector("#dlg-body .inst-start")
    f.locator("#dlg-body .inst-start input[type=radio]").nth(1).check()
    d = dialog(f)
    bounds = f.evaluate("""() => { const d = document.querySelector('#dlg-body .inst-when input[type=date]');
      return {min: d.min, max: d.max, value: d.value}; }""")
    days = f.evaluate("b => (Date.parse(b.max) - Date.parse(b.min)) / 86400000", bounds)
    check(f"{case}: Install at – button and 7-day range", d["buttons"][-1][0] == "Approve & schedule" and days == 7
          and bounds["min"] < bounds["value"] <= bounds["max"], f"{d['buttons']} {bounds}")
    check(f"{case}: no overflow (Install at)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=c.out / f"{case}-install-at.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    planned = f.locator("#dev-cards tr", has_text="Bathroom").first
    planned.scroll_into_view_if_needed()
    check(f"{case}: scheduled installation – pill and clock line", planned.locator(".p-scheduled").count() == 1
          and planned.locator(".plan-note").count() == 1)
    items = dict(menu_items(f, "Bathroom"))
    hints = f.locator("#rowmenu .menu-hint").all_inner_texts()
    check(f"{case}: downgrade blocked while scheduled", items.get("Downgrade") is True
          and "Cancel the scheduled installation first" in hints, f"{items} {hints}")
    f.evaluate("() => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")


def _downgrade_view(c):
    """A running downgrade (state faked): the device stays in "Up-To-Date", the row shows
    installed → target with the tag "Downgrade", the pill "Downgrading 60 %"."""
    case, f, page = c.case, c.f, c.page

    def state(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        s = route.fetch(headers=headers).json()
        for d in s["devices"]:
            if d["name"] == "Hall":
                d.update(status="installing", downgrade=True, target="1.3.1", ota_progress=60, installing=True,
                         section="up_to_date", message="Downgrade started")
        route.fulfill(json=s)

    page.route("**/api/state", state)
    refresh_state(page, f)
    card = f.locator("#dev-cards .card", has=f.locator(".card-head h2", has_text="Up-To-Date"))
    row = card.locator("tr", has_text="Hall").first
    row.scroll_into_view_if_needed()
    text = row.inner_text()
    check(f"{case}: running downgrade – stays up to date, target and tag", row.count() == 1 and "1.3.1" in text
          and row.locator(".fw-id .tag", has_text="Downgrade").count() == 1, text[:160])
    pill = row.locator(".pill.p-installing").inner_text()
    check(f"{case}: running downgrade – pill", pill == "Downgrading 60 %", pill)
    page.screenshot(path=c.out / f"{case}-downgrade-running.png")
    page.unroute("**/api/state")
    refresh_state(page, f)


def _german(browser, out, width, scheme, embedded):
    """German UI: no overflow in Devices, Schedules and Settings, all tabs inside the
    viewport, "Installieren" beside its status pill on wide screens."""
    case = f"schedules-de-{width}-{scheme}{'-embedded' if embedded else ''}"
    script = f"localStorage.setItem({json.dumps(GERMAN)}, {json.dumps(json.dumps('de'))})"
    ctx, page, f, errors = open_page(browser, FEATURES, width, scheme, embedded, init=script)
    check(f"{case}: German UI", f.locator("#tab-schedules").inner_text().strip().startswith("Zeitpl") or width <= 700,
          f.locator("#tab-schedules").inner_text())
    for tab in ("devices", "schedules", "settings"):
        f.locator(f"#tab-{tab}").click()
        page.wait_for_timeout(200)
        check(f"{case}: no overflow ({tab})", f.evaluate(OVERFLOW_JS) <= 0)
        tabs = f.evaluate("""() => [...document.querySelectorAll('.tab')].map(t => { const r = t.getBoundingClientRect();
          return [r.left, r.right]; })""")
        check(f"{case}: tabs inside the viewport ({tab})", all(left >= 0 and right <= width for left, right in tabs), str(tabs))
        page.screenshot(path=out / f"{case}-{tab}.png", full_page=not embedded)
    f.locator("#tab-devices").click()
    if width > 700:
        lines = f.evaluate("""() => [...document.querySelectorAll('#dev-cards .status-cell')].filter(c => c.querySelector('button.install'))
          .map(c => { const p = c.querySelector('.pill').getBoundingClientRect(), b = c.querySelector('button.install').getBoundingClientRect();
            return Math.abs((p.top + p.bottom) / 2 - (b.top + b.bottom) / 2); })""")
        check(f"{case}: Installieren beside the status pill", lines and max(lines) < 4, str(lines[:5]))
    check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
    ctx.close()


def schedules(browser, out):
    """Quick: a phone (embedded) and a desktop viewport, both also in German; --full
    adds 412 px embedded dark, 768 px and 1024 px light and 1440 px dark."""
    cases = (((360, "light", True), (412, "dark", True), (768, "light", False), (1024, "light", False),
              (1440, "light", False), (1440, "dark", False)) if FULL
             else ((360, "light", True), (1440, "light", False)))
    for width, scheme, embedded in cases:
        case = f"schedules-{width}-{scheme}{'-embedded' if embedded else ''}"
        # the schedule saved without a name is refused on purpose (400)
        ctx, page, f, errors = open_page(browser, FEATURES, width, scheme, embedded, ignore=("/api/schedules",))
        c = SimpleNamespace(case=case, f=f, page=page, out=out, width=width, embedded=embedded)
        _schedules_tab(c)
        _install_at(c)
        _downgrade_view(c)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()
        _german(browser, out, width, scheme, embedded)
