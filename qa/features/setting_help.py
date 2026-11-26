"""Feature suite setting_help: help texts of the Settings tab as tooltips and the base colour
of the card Display (demo "idle" on 8801)."""

from features.base import IDLE, OVERFLOW_JS, check, dialog, open_page, tip_text, wait_until

ROOT_COLOR = "() => [document.documentElement.dataset.baseColor, getComputedStyle(document.documentElement).getPropertyValue('--primary-color').trim()]"
CHOSEN = "() => [document.querySelector('#opt-base_color input:checked').value, document.querySelector('#opt-base_color .swatch-name').textContent]"
THEME = "#ff0000"  # primary colour of the HA theme, played by an inline style as core.js copies it
SAVE_BASE = """value => fetch('api/settings', {method: 'POST', body: JSON.stringify({base_color: value}),
  headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name=csrf]').content}}).then(r => r.status)"""


def _base_color(case, page, f, save):
    """Card Display: the base colour dots preview at once, mark the form as changed, are
    dropped by leaving the tab and (save) are stored, kept after a reload."""
    names = f.evaluate("() => [...document.querySelectorAll('#opt-base_color input')].map(i => [i.value, i.getAttribute('aria-label'), i.dataset.tip])")
    check(f"{case}: base colour: six named radios", [n[0] for n in names] == ["default", "blue", "teal", "green", "violet", "orange"]
          and all(n[1] == n[2] for n in names) and names[2][1] == "Teal", str(names))
    group = f.evaluate("() => { const g = document.getElementById('opt-base_color'); return [g.getAttribute('role'), document.getElementById(g.getAttribute('aria-labelledby')).textContent]; }")
    check(f"{case}: base colour: one radio group named by its label", group == ["radiogroup", "Base colour"], str(group))
    f.evaluate(f"() => document.documentElement.style.setProperty('--primary-color', '{THEME}')")
    check(f"{case}: base colour: Default keeps the theme colour", f.evaluate(ROOT_COLOR) == ["default", THEME]
          and f.evaluate(CHOSEN) == ["default", "Default"], str(f.evaluate(ROOT_COLOR)))
    teal = f.locator("#opt-base_color label.swatch", has=f.locator("input[value=teal]"))
    size = teal.bounding_box()
    check(f"{case}: base colour: touch target", size["width"] >= (44 if page.viewport_size["width"] <= 700 else 32)
          and size["height"] >= size["width"] - 0.5, str(size))
    if page.viewport_size["width"] > 700:
        f.locator("#opt-base_color input[value=teal]").hover()
        check(f"{case}: base colour: tooltip with the name", tip_text(f) == "Teal", str(tip_text(f)))
    teal.click()
    check(f"{case}: base colour: a dot previews the colour in the whole page",
          wait_until(lambda: f.evaluate(ROOT_COLOR) == ["teal", "#00897b"]) and f.evaluate(CHOSEN) == ["teal", "Teal"], str(f.evaluate(ROOT_COLOR)))
    check(f"{case}: base colour: Unsaved changes", f.locator(".form-actions .unsaved").is_visible())
    f.click("#tab-devices")
    check(f"{case}: base colour: leaving the tab shows the saved colour", f.evaluate(ROOT_COLOR) == ["default", THEME], str(f.evaluate(ROOT_COLOR)))
    f.click("#tab-settings")
    f.wait_for_selector("#opt-base_color")
    check(f"{case}: base colour: form from the saved settings", f.evaluate(CHOSEN) == ["default", "Default"]
          and f.locator(".form-actions .unsaved").is_hidden(), str(f.evaluate(CHOSEN)))
    if not save:
        return
    f.locator("#opt-base_color input[value=teal]").focus()   # keyboard: arrows move the choice within the group
    page.keyboard.press("ArrowRight")
    check(f"{case}: base colour: arrow key chooses the next dot", wait_until(lambda: f.evaluate(ROOT_COLOR)[0] == "green"), str(f.evaluate(ROOT_COLOR)))
    try:
        f.click("#settings-form .form-actions button.primary")
        check(f"{case}: base colour: named in Save settings?", "Changed: Base colour" in dialog(f)["body"], dialog(f)["body"])
        f.locator("#dlg-btns button", has_text="Save & restart").click()
        check(f"{case}: base colour: saved", wait_until(lambda: dialog(f)["title"] == "Settings saved"))
        page.reload()   # as after the restart: the page of the server carries the saved colour, the Settings tab opens again
        check(f"{case}: base colour: a reload keeps it", f.evaluate(ROOT_COLOR) == ["green", "#558b2f"], str(f.evaluate(ROOT_COLOR)))
        f.wait_for_selector("#opt-base_color")
        check(f"{case}: base colour: chosen dot after the reload", f.evaluate(CHOSEN) == ["green", "Green"], str(f.evaluate(CHOSEN)))
    finally:
        check(f"{case}: base colour: demo reset to Default", f.evaluate(SAVE_BASE, "default") == 200)


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
        check(f"{case}: one i-icon per setting", icons.count() == 19, str(icons.count()))
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
        _base_color(case, page, f, save=width > 700)
        check(f"{case}: no horizontal overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: no console errors", not errors, str(errors[:2]))
        ctx.close()
