"""Feature suites download_url_hint and download_url_notice: hints and notice for the download base URL
(demos "idle" 8801, "missing" 8804, "public" 8807)."""

import json
from pathlib import Path

from features.base import IDLE, MISSING, OVERFLOW_JS, PUBLIC, check, open_page, ui_frame, wait_until

# Cases shared with tests/test_guards.py: the hint the Settings tab shows for each address
URL_CASES = json.loads((Path(__file__).resolve().parents[2] / "tests" / "download_url_cases.json").read_text())
HINT_WORDS = {"none": None, "private": "private network", "credentials": "user name", "query": "query",
              "host": "host name", "localhost": "not in your local network", "nohint": None}


def hint_matches(kind, text):
    """True if the hint text is the one expected for the case kind (empty for none / nohint)."""
    word = HINT_WORDS[kind]
    return not text if word is None else word in text


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
        wrong = []
        for entry in URL_CASES:
            if entry["ui"] == "skip":  # edge whitespace: the field is trimmed before the check
                continue
            f.fill("#opt-download_base_url", entry["url"])
            text = warn.text_content() if warn.is_visible() else ""
            if not hint_matches(entry["ui"], text):
                wrong.append((entry["url"], entry["ui"], text[:30]))
        check(f"{case}: hints agree with the shared case list", not wrong, str(wrong[:3]))
        ctx.close()


def download_url_notice(browser, out):
    """Notice for a missing / public-IP download base URL; device progress in the pill."""
    cases = ((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False))
    for width, scheme, embedded in cases:
        case = f"{width}-{scheme}{'-embedded' if embedded else ''}"
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
        check(f"{case}: only the settings button (public)", buttons == ["Open settings"], str(buttons))
        check(f"{case}: no install selection (public)", f.locator("td.c-select input:not(.ghost)").count() == 0)
        pills = f.locator("button.pill").all_text_contents()
        check(f"{case}: device progress in the status pill", "Installing 55 %" in pills, str(sorted(set(pills))))
        check(f"{case}: no horizontal overflow (public)", f.evaluate(OVERFLOW_JS) <= 0)
        page.screenshot(path=out / f"notice-public-{case}.png")
        f.locator("#notice button", has_text="Open settings").click()  # leaves the Devices tab: last check
        check(f"{case}: settings opened", wait_until(f.locator("#view-settings").is_visible))
        check(f"{case}: no console errors (public)", not errors, str(errors[:2]))
        ctx.close()


def serve_as(base):
    """Route handler that serves the "missing" demo under another host name (its answers do not
    depend on the host)."""
    return lambda route: route.fulfill(response=route.fetch(url=route.request.url.replace(base, MISSING)))


def download_url_suggestion(browser, out):
    """An empty download base URL is pre-filled from the address the page was opened with, only for a
    private IP address (the demo "missing" 8804 is reached under other host names via a route)."""
    for host, expected in (("192.168.1.20", "http://192.168.1.20:8098"), ("[fd12:3456::1]", "http://[fd12:3456::1]:8098"),
                           ("203.0.113.10", ""), ("homeassistant.local", ""), ("127.0.0.1", "")):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        base = f"http://{host}:8804"
        page.route(base + "/**", serve_as(base))
        page.goto(base + "/ui/", wait_until="commit")
        page.click("#tab-settings")
        page.wait_for_selector("#opt-download_base_url")
        check(f"{host}: suggested URL", wait_until(lambda p=page, e=expected: p.input_value("#opt-download_base_url") == e),
              page.input_value("#opt-download_base_url"))
        check(f"{host}: unsaved marker follows the suggestion (both save areas)",
              page.locator(".form-actions .unsaved").is_visible() == bool(expected)
              and page.locator(".form-actions-top .unsaved").is_visible() == bool(expected))
        note = page.locator("#opt-download_base_url-suggest")
        check(f"{host}: suggestion note shown only with a suggestion", note.is_visible() == bool(expected))
        if expected:
            check(f"{host}: note text", "please check it before saving" in note.text_content())
            page.fill("#opt-download_base_url", expected + "/x")
            check(f"{host}: note hidden after an edit", note.is_hidden())
        ctx.close()
