"""Feature suites download_url_hint and download_url_notice: hints and notice for the download base URL
(demos "idle" 8801, "legacy" 8803, "missing" 8804)."""

from features.base import IDLE, LEGACY, MISSING, OVERFLOW_JS, PUBLIC, check, open_page, ui_frame, wait_until


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
        check(f"{case}: hint for a public IP", warn.is_visible() and "Not accepted" in warn.text_content() and "private network" in warn.text_content())
        described = f.get_attribute("#opt-download_base_url", "aria-describedby") or ""
        check(f"{case}: hint linked for screen readers", "opt-download_base_url-warn" in described, described)
        overflow = f.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(f"{case}: no horizontal overflow", overflow <= 0, f"{overflow}px")
        f.locator("#opt-download_base_url").scroll_into_view_if_needed()
        page.screenshot(path=out / f"lan-hint-{case}.png")
        f.fill("#opt-download_base_url", "http://[fe80::1]:8098")
        check(f"{case}: hint for a link-local IPv6 address", warn.is_visible() and "private network" in warn.text_content())
        f.fill("#opt-download_base_url", "http://user:pw@192.168.1.20:8098")
        check(f"{case}: hint for credentials", warn.is_visible() and "user name" in warn.text_content())
        f.fill("#opt-download_base_url", "http://192.168.1.20:8098/?x=1")
        check(f"{case}: hint for a query", warn.is_visible() and "query" in warn.text_content())
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
        # Public IP address saved before it was refused
        ctx, page, f, errors = open_page(browser, PUBLIC, width, scheme, embedded)
        notice = f.locator("#notice")
        check(f"{case}: notice for a public IP", notice.is_visible() and "private network" in notice.text_content())
        buttons = f.locator("#notice button").all_text_contents()
        check(f"{case}: no confirm button", buttons == ["Open settings"], str(buttons))
        check(f"{case}: no install selection (public)", f.locator("td.c-select input:not(.ghost)").count() == 0)
        check(f"{case}: no horizontal overflow (public)", f.evaluate(OVERFLOW_JS) <= 0)
        page.screenshot(path=out / f"notice-public-{case}.png")
        check(f"{case}: no console errors (public)", not errors, str(errors[:2]))
        ctx.close()
