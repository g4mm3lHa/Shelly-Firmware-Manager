"""Firmware store suite, Logs tab area (demo "features" on 8805; the last case changes the demo state)."""

from features.base import OVERFLOW_JS, check, dialog, refresh_state, wait_until


def _logs_after_check(c):
    """Logs tab open while a check runs: the log is fetched again as soon as the check
    has ended (no tab change needed), not only after the 30 s refresh."""
    case, f, page = c.case, c.f, c.page
    phase = {"busy": True}

    def state(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        s = route.fetch(headers=headers).json()
        if phase["busy"]:
            s["progress"] = {"kind": "all", "phase": "devices", "done": 1, "total": 25}
        else:
            s["last_scan"] = (s.get("last_scan") or 0) + 60
        route.fulfill(json=s)

    requests = []
    page.on("request", lambda r: "/api/logs" in r.url and requests.append(r.url))
    page.route("**/api/state", state)
    refresh_state(page, f)   # check running
    busy = len(requests)
    phase["busy"] = False
    refresh_state(page, f)   # check ended
    ended = wait_until(lambda: len(requests) > busy)
    page.unroute("**/api/state")
    check(f"{case}: Logs tab fetches the log when a check ends", busy == 0 and ended, f"{busy} / {len(requests)}")
    refresh_state(page, f)


def _logs(c):
    """Logs tab: group bar, coloured findings, deleting an entry / the whole log (last case)."""
    case, f, page, out, last = c.case, c.f, c.page, c.out, c.last
    # Logs – group bar, findings coloured by severity, delete one entry / all (last case)
    f.locator("#tab-logs").click()
    entries = f.locator("#log-list .log, #log-list .log-m")
    entries.first.wait_for()
    _logs_after_check(c)
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
    check(f"{case}: findings coloured by their own severity, then the ignored devices",
          colours == ["okc", "warnc", "bad", "neutral", "neutral"], str(colours))
    plain = f.evaluate("""() => { const li = document.querySelector('#log-list li.neutral'), body = getComputedStyle(document.body).color;
      return li && li.textContent.startsWith('Garage door: ignored') && getComputedStyle(li).color === body; }""")
    check(f"{case}: ignored devices in the normal text colour", plain)
    summary = f.locator("#log-list summary", has_text="30.7 s").first.inner_text()
    check(f"{case}: ignored devices counted in the summary", "2 ignored" in summary, summary)
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
