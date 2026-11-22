"""Feature suite setting_help: help texts of the Settings tab as tooltips (demo "idle" on 8801)."""

from features.base import IDLE, OVERFLOW_JS, check, open_page, tip_text


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
        check(f"{case}: one i-icon per setting", icons.count() == 14, str(icons.count()))
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
