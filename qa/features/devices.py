"""Firmware store suite, devices area: device list, action popups and icons, name tips, battery, layout
(demo "features" on 8805)."""

from features.base import (
    OVERFLOW_JS, check, dialog, filter_entries, menu_items, pick_filter, refresh_state, tip_text, wait_until,
)


def _device_list(c):
    """Devices tab: Install button next to "Ready", "Updated" stays on top, icon colours."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    # Device list – Install button next to "Ready", "Updated" stays on top, icon colours
    card = f.locator("#dev-cards .card").first
    install = card.locator(".status-cell button.install")
    # features demo: one ready device offers a beta (own section), one has a scheduled installation (no button)
    expected = 5 if "features" in case else 7
    check(f"{case}: Install button for every ready device, the failed installation and the store fallback", install.count() == expected
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
    if width > 700:   # every column except "Device": title and content centred; IP and firmware with tabular digits
        align = f.evaluate("""() => { const t = document.querySelector('#dev-cards table'), cs = e => getComputedStyle(e);
          const cells = [...t.querySelectorAll('th, tbody tr:first-child td')].filter(e => cs(e).display !== 'none');
          const off = cells.filter(e => (cs(e).textAlign === 'center') === e.classList.contains('c-device')).map(e => e.className);
          const mid = e => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; };
          const strip = t.querySelector('td.c-action .act-strip'), status = t.querySelector('td.c-status .status-cell > *');
          return {off, strip: Math.abs(mid(strip) - mid(strip.parentElement)),
            status: cs(status.parentElement).justifyContent,
            nums: ['td.c-ip', 'td.c-firmware'].map(q => cs(t.querySelector(q)).fontVariantNumeric)}; }""")
        check(f"{case}: devices table – columns except Device centred, tabular digits", not align["off"] and align["strip"] < 1
              and align["status"] == "center" and align["nums"] == ["tabular-nums"] * 2, str(align))
    check(f"{case}: no overflow (devices)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-devices.png")
    install.first.click()
    check(f"{case}: Install opens the installation dialog (devices)", dialog(f)["title"] == "Install update?")
    # Full Gen1 version texts have no spaces: the Version row wraps inside the dialog
    wraps = f.evaluate("""() => { const v = document.querySelector('#dlg dl.kv>dd.ver'), body = document.getElementById('dlg-body');
      v.textContent = '20230503-101129/v1.13.0-g9aed950  →  20230913-112003/v1.14.0-gcb84623';
      return v.scrollWidth <= v.clientWidth + 1 && body.scrollWidth <= body.clientWidth + 1; }""")
    check(f"{case}: long Gen1 version wraps in the dialog", wraps)
    f.locator("#dlg-btns button", has_text="Cancel").click()
    if width > 700:   # the icon strip is part of the table on wide screens
        counts = f.evaluate("""() => ({good: document.querySelectorAll('.act.c-good').length,
          alert: document.querySelectorAll('.act.c-alert').length, warn: document.querySelectorAll('.act.c-warn').length,
          ap: document.querySelectorAll('.act.alert.s-on[aria-label^="Access point"]').length})""")
        check(f"{case}: power-on action green and red, lock yellow, access point red",
              all(counts[k] > 0 for k in ("good", "alert", "warn", "ap")), str(counts))


LIVE = {"shelly_name": "shelly-2", "ntp": "time.local", "eco": True, "ap": False, "ble": True, "debug_ws": False,
        "outputs": [{"key": "switch:0", "name": "switch:0", "aop": "restore_last", "options": ["off", "on", "restore_last"]}]}


def _security_strip(c):
    """Strip "Security": only counts above 0 with their short names; phones: symbol and number only."""
    case, f = c.case, c.f
    tiles = f.evaluate("""() => [...document.querySelectorAll('.sec-strip .sec-tile')].map(t => ({
      n: (t.querySelector('.sec-n') || {}).textContent || null, label: t.querySelector('.sec-label').textContent,
      shown: getComputedStyle(t.querySelector('.sec-label')).display !== 'none', zero: t.classList.contains('zero')}))""")
    counts = [t for t in tiles if not t["zero"]]
    check(f"{case}: security strip – only counts above 0, short names",
          counts and all(t["n"] and int(t["n"]) > 0 for t in counts)
          and {t["label"] for t in counts} <= {"Unprotected", "Cloud", "Access point", "Debug log"}, str(tiles))
    check(f"{case}: security strip – names hidden on phones only", all(t["shown"] == (c.width > 700) for t in counts), str(tiles))


def _popup(f, opener):
    """Open an action popup; returns (title, strip, body, buttons)."""
    opener.click(force=True)   # greyed-out icons are aria-disabled but open their note
    f.wait_for_selector("#dlg-body .fstrip")   # after "Reading current values …"
    d = dialog(f)
    strip = f.evaluate("""() => { const s = document.querySelector('#dlg-body .fstrip'); if (!s) return null;
      const p = s.querySelector('.fs-pill'), i = s.querySelector('.fs-icon');
      return {name: s.querySelector('.fs-name').textContent, pill: p ? p.textContent : null, tone: s.className,
        size: Math.round(i.getBoundingClientRect().width), first: s === document.getElementById('dlg-body').firstElementChild}; }""")
    return d["title"], strip, d["body"], d["buttons"]


def _action_popups(c):
    """Action icons: websocket debug icon (colours, Gen1 unsupported), access-point icon,
    status strip in every popup opened by an action icon, two icon rows of five."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    page.route("**/settings/refresh", lambda r: r.fulfill(json=LIVE))
    opened = []   # phone tiles whose details were opened here (closed again at the end)

    def icon_of(name, label):
        row = f.locator("#dev-cards tr", has_text=name).first
        if width < 700:
            det = row.locator("td.c-details details")
            if not det.evaluate("d => d.open"):
                row.locator("td.c-details summary").click()
                opened.append(row)
            return row.locator(f".act-strip.labeled [aria-label^='{label}']")
        return row.locator(f"td.c-action [aria-label^='{label}']")

    living = icon_of("Living room", "Websocket debug log")
    check(f"{case}: debug icon on = yellow", "c-warn" in living.get_attribute("class") and "s-on" in living.get_attribute("class"),
          living.get_attribute("class"))
    hall = icon_of("Hall", "Websocket debug log")
    check(f"{case}: debug icon off = grey", "s-off" in hall.get_attribute("class"), hall.get_attribute("class"))
    gen1 = icon_of("Garden plug", "Websocket debug log")
    check(f"{case}: Gen1 debug icon greyed out with explanation", gen1.get_attribute("aria-disabled") == "true"
          and "Gen1 devices have no websocket debug log" in gen1.get_attribute("aria-label"))
    ap_path = icon_of("Hall", "Access point").locator("path").get_attribute("d")
    check(f"{case}: access-point icon shape", ap_path.startswith("M4.93,4.93"), ap_path[:20])
    if width > 700:
        rows = f.evaluate("""() => { const s = document.querySelector('#dev-cards td.c-action .act-strip'); const tops = {};
          for (const b of s.children) { const t = Math.round(b.getBoundingClientRect().top); tops[t] = (tops[t] || 0) + 1; }
          return {width: s.getBoundingClientRect().width, rows: Object.values(tops)}; }""")
        check(f"{case}: action strip 170 px, two rows of 5 icons", rows["width"] == 170 and rows["rows"] == [5, 5], str(rows))
    else:
        labeled = f.locator("#dev-cards tr", has_text="Hall").first.locator(".act-strip.labeled > *")
        check(f"{case}: phone tile has the debug and cloud buttons", labeled.count() == 10
              and labeled.nth(8).inner_text() == "Websocket debug log" and labeled.nth(5).inner_text() == "Cloud",
              str(labeled.all_inner_texts()))
        f.locator("#dev-cards tr", has_text="Hall").first.screenshot(path=out / f"{case}-tile-debug.png")
    # Popups opened by an action icon: strip under the title (icon circle 32 px, name, state)
    # Switch and restart popups: no question; the button names the action
    expect = (("Hall", "Eco mode", "Eco mode – Hall", "On", "t-good", "Turn off"),
              ("Hall", "Bluetooth", "Bluetooth – Hall", "On", "t-good", "Turn off"),
              ("Hall", "Access point", "Access point – Hall", "Off", "t-off", "Turn on"),
              ("Hall", "Power-on action", "Power-on action – Hall", "Restore last state", "t-good", None),
              ("Hall", "Websocket debug log", "Websocket debug log – Hall", "Off", "t-off", "Turn on"),
              ("Living room", "Password protection", "Device password – Living room ceiling with a really long device name",
               "Protected", "t-good", None),
              ("Hall", "Restart", "Restart – Hall", None, "t-neutral", "Restart"))
    for name, label, title, pill, tone, button in expect:
        t, strip, body, buttons = _popup(f, icon_of(name, label))
        check(f"{case}: {label} popup with status strip", t == title and strip and strip["first"] and strip["pill"] == pill
              and tone in strip["tone"] and strip["size"] == 32, f"{t} {strip} {body[:120]}")
        if button:
            check(f"{case}: {label} popup without question, button \"{button}\"", "?" not in body and buttons[-1][0] == button
                  and (label != "Restart" or "The device will be unavailable for a few seconds." in body), f"{body[:160]} {buttons}")
        if label == "Websocket debug log":
            link = f.locator("#dlg-body a", has_text="Open Diagnostics in the Shelly web UI")
            check(f"{case}: debug popup links the Diagnostics page", link.get_attribute("href") == "http://192.168.1.12/#/diagnostics"
                  and link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel")
                  and "Turn it off afterwards" in body and "below 1.0.0" not in body, str(link.get_attribute("href")))
            page.screenshot(path=out / f"{case}-popup-debug.png")
        if label == "Eco mode":
            page.screenshot(path=out / f"{case}-popup-eco.png")
        if label == "Power-on action":  # the select stays inside the dialog, its text shortened
            inside = f.evaluate("""() => { const d = document.getElementById('dlg-body').getBoundingClientRect();
              return [...document.querySelectorAll('#dlg-body select')].every(s => s.getBoundingClientRect().right <= d.right + 0.5); }""")
            check(f"{case}: Power-on action select inside the dialog", inside)
        f.locator("#dlg-btns button", has_text="Cancel").click()
    # Notes of greyed-out icons: strip with the reason
    for name, label, pill, text in (("Garden plug", "Websocket debug log", "Not supported", "no websocket debug log"),
                                    ("Garden plug", "Access point", "Not available", "Not available on Gen1"),
                                    ("Kitchen light", "Power-on action", "Not available", "no switchable output"),
                                    ("Terrace", "Access point", "Not supported", "not supported")):
        t, strip, body, _buttons = _popup(f, icon_of(name, label))
        check(f"{case}: note of {label} ({name}) with status strip", t == label and strip and strip["pill"] == pill and text in body,
              f"{t} {strip} {body[:120]}")
        if label == "Websocket debug log":
            page.screenshot(path=out / f"{case}-popup-debug-gen1.png")
        f.locator("#dlg-btns button", has_text="Close").click()
    page.unroute("**/settings/refresh")

    # Firmware below 1.0.0: warning that the stream is not protected by the device password
    def old_firmware(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        state = route.fetch(headers=headers).json()
        for d in state["devices"]:
            if d["name"] == "Hall":
                d["version"] = "0.14.1"
        route.fulfill(json=state)
    page.route("**/api/state", old_firmware)
    page.route("**/settings/refresh", lambda r: r.fulfill(json=LIVE))
    refresh_state(page, f)
    _t, _strip, body, _buttons = _popup(f, icon_of("Hall", "Websocket debug log"))
    check(f"{case}: debug popup warns below firmware 1.0.0", "not protected by the device password" in body, body[-200:])
    f.locator("#dlg-btns button", has_text="Cancel").click()
    page.wait_for_timeout(400)   # the state request of closing the dialog is answered first
    page.unroute("**/settings/refresh")
    page.unroute("**/api/state")
    refresh_state(page, f)
    for row in opened:
        row.locator("td.c-details summary").click()
    f.evaluate("() => scrollTo(0, 0)")
    page.wait_for_timeout(300)   # the scroll event (hides a touch tooltip) arrives before the next tap


def _name_tips(c):
    """Tooltips of the HA name and the Shelly name (mouse hover, touch tap) and the
    touch behaviour of tooltips: shown on a tap without action, not on a tap with one."""
    case, f, page, width = c.case, c.f, c.page, c.width
    row = f.locator("#dev-cards tr", has_text="Hall").first
    ha, shelly_name = row.locator("td.c-device .name"), row.locator(".shelly-name > span")
    check(f"{case}: no native title on the names", ha.get_attribute("title") is None)
    check(f"{case}: no browser tooltip (title) anywhere – the app's own tooltip only", f.locator("[title]").count() == 0,
          str(f.locator("[title]").evaluate_all("l => l.slice(0, 5).map(e => e.outerHTML.slice(0, 80))")))
    if width > 700:
        ha.hover()
        check(f"{case}: HA name tooltip on hover", tip_text(f) == "Home Assistant device name: Hall", str(tip_text(f)))
        shelly_name.hover()
        check(f"{case}: Shelly name tooltip on hover", tip_text(f) == "Shelly device name: shelly-2", str(tip_text(f)))
        page.screenshot(path=c.out / f"{case}-name-tip.png")
        page.mouse.move(5, 5)
        return
    page.wait_for_timeout(300)   # a re-render after the previous state request hides every tooltip
    ha.tap()
    check(f"{case}: HA name tooltip on tap", tip_text(f) == "Home Assistant device name: Hall", str(tip_text(f)))
    page.screenshot(path=c.out / f"{case}-name-tip.png")
    shelly_name.tap()
    check(f"{case}: Shelly name tooltip on tap", tip_text(f) == "Shelly device name: shelly-2", str(tip_text(f)))
    shelly_name.tap()
    check(f"{case}: second tap hides it", tip_text(f) is None)
    page.route("**/settings/refresh", lambda r: r.fulfill(json=LIVE))
    row.locator(".pen").tap()
    check(f"{case}: pencil unchanged (opens the dialog, no tooltip)", tip_text(f) is None and f.locator("#dlg").is_visible())
    f.locator("#dlg-x").tap()
    page.wait_for_timeout(300)
    page.unroute("**/settings/refresh")
    ha.tap()
    f.locator("#view-devices .tool-btn").first.tap()
    page.keyboard.press("Escape")
    check(f"{case}: tap elsewhere hides the tooltip", tip_text(f) is None)
    page.keyboard.press("Escape")


def _battery(c):
    """Battery devices: battery icon right after the name on the same line (table and
    tile) with a tooltip; a sleeping one shows the grey "Asleep" pill and a dimmed IP
    and is listed under "Asleep", not "Attention"; an awake one keeps its normal IP; the
    tooltip of the icon names where the battery device was recognised."""
    case, f, page, width = c.case, c.f, c.page, c.width
    def row(name):
        return f.locator("#dev-cards tr", has_text=name).first
    geo = row("Doorbell").evaluate("""r => { const n = r.querySelector('td.c-device .name').getBoundingClientRect(),
      b = r.querySelector('td.c-device .battery'), g = b && b.getBoundingClientRect(), ip = r.querySelector('td.c-ip .num');
      return {icon: !!b, after: !!g && g.left >= n.right - 1 && g.left - n.right <= 12,
        line: !!g && Math.abs((g.top + g.height / 2) - (n.top + n.height / 2)) < 3,
        dim: !!ip && ip.classList.contains('dim') && getComputedStyle(ip).opacity === '0.5',
        pill: r.querySelector('td.c-status .pill').textContent}; }""")
    check(f"{case}: battery icon right after the name", geo["icon"] and geo["after"] and geo["line"], str(geo))
    check(f"{case}: sleeping battery device – Asleep pill and dimmed IP", geo["pill"] == "Asleep" and geo["dim"], str(geo))
    pill = row("Doorbell").locator("td.c-status .pill")
    grey = pill.evaluate("""p => { const s = getComputedStyle(p);
      return [s.getPropertyValue('--tone').trim(), s.getPropertyValue('--secondary-text-color').trim()]; }""")
    check(f"{case}: Asleep pill grey", grey[0] == grey[1] and grey[0] != "", str(grey))
    awake = row("Carport").evaluate("r => [!!r.querySelector('.battery'), r.querySelector('td.c-ip .num').classList.contains('dim')]")
    check(f"{case}: awake battery device – icon, IP not dimmed", awake == [True, False], str(awake))
    check(f"{case}: no battery icon on other devices", row("Hall").locator(".battery").count() == 0)
    pick_filter(f, "devices", "Attention")
    check(f"{case}: Asleep is not listed under Attention", f.locator("#dev-cards tr", has_text="Doorbell").count() == 0)
    pick_filter(f, "devices", "Asleep")
    check(f"{case}: Asleep filter lists it", f.locator("#dev-cards tr", has_text="Doorbell").count() == 1)
    pick_filter(f, "devices", "All")
    icon = row("Doorbell").locator(".battery")
    icon.scroll_into_view_if_needed()
    if width > 700:
        icon.hover()
    else:
        page.wait_for_timeout(300)
        icon.tap()
    check(f"{case}: battery tooltip", tip_text(f) == "Battery device: reachable only while it is awake\nDetected from Home Assistant",
          str(tip_text(f)))
    page.screenshot(path=c.out / f"{case}-battery.png")
    if width > 700:
        page.mouse.move(5, 5)
        pill.hover()   # Asleep tooltip: wake-up hint and the last contact only
        wait_until(lambda: tip_text(f))
        lines = (tip_text(f) or "").split("\n")
        check(f"{case}: Asleep tooltip – wake-up hint and last contact only", len(lines) == 2
              and lines[0].startswith("Battery device asleep – wake it up") and lines[1].startswith("Last contact: 20"), str(lines))
        page.mouse.move(5, 5)
    else:
        f.locator("#view-devices .tool-btn").first.tap()
        page.keyboard.press("Escape")


SECTION_TITLES = ["Updates Available", "Beta Updates", "Attention", "Not Checked", "Up-To-Date", "Asleep", "Ignored", "Removed", "Other Shellys"]
FILTER_LABELS = ["All", "Updates", "Beta updates", "Scheduled", "Attention", "Not checked", "Up-To-Date", "Unprotected", "Cloud on",
                 "Access point on", "Debug log on", "On hold", "Asleep", "Ignored", "Removed", "Other Shellys"]


def _host_names(c):
    """Option "Resolve host names": off (demo) – no Host name column, no Columns or Sort
    entry; on – the column is shown once in this browser and can be hidden again."""
    case, f, page = c.case, c.f, c.page
    entry = f.locator('#cols-devices .sort-item[data-col="host_name"]')
    sort = f.locator('#view-devices .sort-menu .sort-item[data-value="host_name"]')

    def shown_in(tool, item):
        """Whether the entry is visible while its popover is open (closed popovers hide everything)."""
        f.locator(f'#view-devices .tool-btn[aria-label^="{tool}"]').click()
        visible = item.is_visible()
        page.keyboard.press("Escape")
        return visible
    check(f"{case}: option off – no Host name column, Columns or Sort entry", f.locator("#dev-cards th.c-host_name").count() == 0
          and not shown_in("Sort", sort) and (c.width <= 700 or not shown_in("Columns", entry)))

    def on(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}  # no 304
        state = route.fetch(headers=headers).json()
        state["host_names"] = True
        route.fulfill(json=state)

    page.route("**/api/state", on)
    refresh_state(page, f)
    th = f.locator("#dev-cards th.c-host_name").first
    check(f"{case}: option on – Host name column shown and ticked in Columns", th.is_visible()
          and entry.get_attribute("aria-checked") == "true" and "shelly-" in f.locator(
              "#dev-cards td.c-host_name").all_inner_texts().__str__())
    page.unroute("**/api/state")
    refresh_state(page, f)


def _columns(c):
    """"Columns" (desktop): round icon at the right end of the toolbar, popover like sort and
    filter (title, check marks, "Reset columns" last), a click keeps it open, dot while the
    columns differ from the default."""
    case, f, page = c.case, c.f, c.page
    btn = f.locator('#view-devices .tb-extra .tool-btn[aria-label="Columns"]')
    geo = btn.evaluate("""b => { const r = b.getBoundingClientRect(), bar = b.closest('.toolbar').getBoundingClientRect();
      return {w: Math.round(r.width), right: Math.round(bar.right - r.right)}; }""")
    check(f"{case}: Columns as round icon at the right end", geo["w"] == 36 and geo["right"] <= 1, str(geo))
    btn.click()
    menu = f.evaluate("""() => { const m = document.getElementById('cols-devices');
      return {title: m.querySelector('.pop-title').textContent, last: [...m.querySelectorAll('.sort-item')].pop().textContent,
        checked: [...m.querySelectorAll('[data-col][aria-checked=true]')].map(b => b.dataset.col)}; }""")
    check(f"{case}: Columns popover – title, check marks, Reset last", menu["title"] == "Columns" and menu["last"] == "Reset columns"
          and "device" in menu["checked"] and "ntp" not in menu["checked"], str(menu))
    dot = lambda: btn.evaluate("b => b.classList.contains('has-value')")
    check(f"{case}: no dot with the default columns", not dot())
    f.locator('#cols-devices .sort-item[data-col="gen"]').click()
    check(f"{case}: a click hides the column, keeps the popover open and sets the dot",
          f.locator("#dev-cards th.c-gen").first.is_hidden() and f.locator("#cols.open").count() == 1 and dot())
    f.locator("#cols-devices .sort-item", has_text="Reset columns").click()
    check(f"{case}: Reset columns – column back, no dot", f.locator("#dev-cards th.c-gen").first.is_visible() and not dot())
    page.keyboard.press("Escape")


def _sections(c):
    """Sections of the device list in their order with the read-only "Other Shellys",
    filter popover (one section, "Other Shellys" only), Removed marker with its tooltip
    and the row menu "Remove from list" (confirmation; removed only in the last case),
    "No usable address", the "Host name" column (see _host_names), the beta mark of a pre-release
    and the header checkbox above the row checkboxes."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    titles = [t.split(" · ")[0] for t in f.locator("#dev-cards .card-head h2").all_inner_texts()]
    check(f"{case}: sections in order, Other Shellys last", titles == SECTION_TITLES, str(titles))
    labels = [e[0] for e in filter_entries(f, "devices")]
    check(f"{case}: filter entries in the order of the sections", labels == FILTER_LABELS, str(labels))
    pick_filter(f, "devices", "Attention")
    heads = f.locator("#dev-cards .card-head h2").all_inner_texts()
    check(f"{case}: filter Attention shows only that section", len(heads) == 1 and heads[0].startswith("Attention"), str(heads))
    pick_filter(f, "devices", "Other Shellys")
    others = f.locator("#dev-cards .card")
    check(f"{case}: filter Other Shellys shows only that card", others.count() == 1
          and others.locator(".oth-row:not(.oth-head)").count() == 5, str(others.count()))
    page.screenshot(path=out / f"{case}-other-shellys.png")
    pick_filter(f, "devices", "All")
    where = f.locator(".oth-row", has_text="Bedroom thermostat").inner_text()
    check(f"{case}: other Shelly – kind and where to update", "BLU TRV" in where and "Living room hub" in where, where)
    # Removed: yellow marker, no Ignored marker, tooltip reachable / not reachable
    def removed_tip(name):
        pill = f.locator("#dev-cards tr", has_text=name).first.locator(".p-removed")
        pill.scroll_into_view_if_needed()
        if width > 700:
            page.mouse.move(1, 1)
            pill.hover()
        else:
            page.wait_for_timeout(300)
            pill.tap()
        text = wait_until(lambda: tip_text(f)) or ""
        if width < 700:
            pill.tap()   # a second tap hides it
        return text
    reach, gone = removed_tip("Sauna"), removed_tip("Dryer")
    check(f"{case}: Removed tooltip – still reachable", reach.startswith("Removed from Home Assistant / the Shelly integration, but still reachable"), reach)
    check(f"{case}: Removed tooltip – no longer reachable", gone.startswith("Removed from Home Assistant / the Shelly integration and no longer reachable"), gone)
    page.mouse.move(1, 1)
    # No usable address: message instead of the IP, the reason below the name on wide screens
    guest = f.locator("#dev-cards tr", has_text="Guest room").first
    reason = guest.locator(".addr-reason")
    check(f"{case}: No usable address", guest.locator("td.c-ip").inner_text().strip() == "No usable address"
          and reason.is_visible() == (width > 700), guest.locator("td.c-ip").inner_text())
    # Pre-release (demo "features"): red "(beta)" – next to the title, inside HA on phones above the pills
    beta = f.evaluate("""() => { const b = document.querySelector('h1 .beta'), m = document.getElementById('meta');
      return b && {text: b.textContent, shown: b.getBoundingClientRect().height > 0,
        above: b.getBoundingClientRect().bottom <= m.getBoundingClientRect().top + 1}; }""")
    check(f"{case}: beta mark of a pre-release", bool(beta) and beta["text"] == "(beta)" and beta["shown"] and beta["above"], str(beta))
    if width > 700:
        _host_names(c)
        _columns(c)
        x = f.evaluate("""() => [document.querySelector('#dev-cards .card-head input'),
          document.querySelector('#dev-cards td.c-select input:not(.ghost)')].map(e => e.getBoundingClientRect().left)""")
        check(f"{case}: header checkbox above the row checkboxes", abs(x[0] - x[1]) < 0.5, str(x))
    # Row menu of a removed device: "Remove from list" instead of "Ignore device", with confirmation
    items = dict(menu_items(f, "Sauna"))
    check(f"{case}: removed device – Remove from list instead of Ignore", items.get("Remove from list") is False
          and "Ignore device" not in items, str(items))
    f.locator("#rowmenu button", has_text="Remove from list").click()
    d = dialog(f)
    check(f"{case}: Remove from list asks first", d["title"] == "Remove from list?" and [b[0] for b in d["buttons"]] == ["Cancel", "Remove"],
          str(d))
    if c.last:
        f.locator("#dlg-btns button", has_text="Remove").click()
        gone_row = wait_until(lambda: f.locator("#dev-cards tr", has_text="Sauna").count() == 0)
        check(f"{case}: removed device removed from the list", gone_row)
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()


def _keep_listed(c):
    """Devices of the updates card stay there while they are checked again (the check
    resets their markers); phone tiles: the Ignored label as high as the status pill."""
    case, f, page = c.case, c.f, c.page
    checked = ("Fridge", "Office desk")

    def checking(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}  # no 304
        state = route.fetch(headers=headers).json()
        for d in state["devices"]:
            if d["name"] in checked:   # what a running single check reports
                d.update(activity="checking", install_failed=None, updated=None, last_update=None)
        route.fulfill(json=state)

    page.route("**/api/state", checking)
    refresh_state(page, f)
    card = f.locator("#dev-cards .card").first
    for name in checked:
        row = card.locator("tr", has_text=name)
        check(f"{case}: {name} stays in the updates card while checked", row.count() == 1
              and row.locator(".pill").first.inner_text().startswith("Checking"))
    card.locator("tr", has_text="Fridge").screenshot(path=c.out / f"{case}-checking-kept.png")
    page.unroute("**/api/state")
    refresh_state(page, f)
    if c.width < 700:
        sizes = f.evaluate("""() => { const t = [...document.querySelectorAll('#dev-cards .status-cell')]
          .find(e => e.querySelector('.p-ignored') && e.querySelector('button.pill'));
          if (!t) return null; const r = e => e.getBoundingClientRect().height;
          return {ignored: r(t.querySelector('.p-ignored')), pill: r(t.querySelector('button.pill'))}; }""")
        f.locator("#dev-cards tr.ignored").first.screenshot(path=c.out / f"{case}-ignored-tile.png")
        check(f"{case}: Ignored label as high as the status pill", sizes and abs(sizes["ignored"] - sizes["pill"]) < 1,
              str(sizes))


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
    f.locator("#dlg-body label.check input[type=checkbox]").check()
    check(f"{case}: acknowledgment enables the approval",
          f.locator("#dlg-btns button", has_text="Approve installation").is_enabled())
    c.page.screenshot(path=c.out / f"{case}-install-store-fallback.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    step = card.locator("tr", has_text="Heat pump")
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
          and pos["sections"] == ["Firmware", "Device", "History", "Backups"], str(pos))
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
    check(f"{case}: installation dialog in sections", heads == ["Device", "Firmware", "Before installing", "Start", "Changelog"], str(heads))
    page.screenshot(path=out / f"{case}-install-dialog.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    if width < 700:   # phone tile: lines, flush button rows, Details button
        row = card.locator("tr", has_text="Attic fan")
        if not row.locator("td.c-details details").evaluate("d => d.open"):
            row.locator("td.c-details summary").click()
        row.locator(".act-strip.labeled").wait_for()
        geo = row.evaluate("""tr => { const r = e => e.getBoundingClientRect(), mid = e => r(e).top + r(e).height / 2;
          const q = s => tr.querySelector(s), strip = q('.act-strip.labeled'), buttons = [...strip.children];
          const rows = {}; buttons.forEach(b => { const t = Math.round(r(b).top); rows[t] = Math.max(rows[t] || 0, r(b).right); });
          return {check: Math.abs(mid(q('td.c-select input')) - mid(q('.name'))), menu: Math.abs(mid(q('td.c-menu button svg')) - mid(q('.name'))),
            status: Math.abs(mid(q('.status-cell .pill')) - mid(q('td.c-firmware .nowrap'))),
            ip: Math.abs(mid(q('td.c-ip a')) - mid(q('.shelly-name'))) < 4 && r(q('td.c-ip a')).right > r(q('.shelly-name')).right,
            details: {h: Math.round(r(q('td.c-details summary')).height), w: Math.round(r(q('td.c-details summary')).width),
                      full: Math.round(r(tr).width - r(q('td.c-details summary')).width),
                      border: getComputedStyle(q('td.c-details summary')).borderTopStyle},
            flush: Object.values(rows).map(x => Math.round(r(strip).right - x)),
            chevron: getComputedStyle(q('td.c-details summary'), '::after').borderRightStyle,
            top: Math.round(r(q('.name')).top - r(tr).top), bottom: Math.round(r(tr).bottom - r(q('dl.tile-kv')).bottom)}; }""")
        check(f"{case}: tile – checkbox and menu on the name line, IP right of the Shelly name, status on the firmware line",
              geo["check"] < 2 and geo["menu"] < 2 and geo["status"] < 2 and geo["ip"], str(geo))
        # Details: outlined button, 36 px, full content width (tile padding 12 px + border each side)
        det = geo["details"]
        check(f"{case}: tile – Details as outlined 36 px button over the content width",
              det["h"] == 36 and det["border"] == "solid" and det["full"] <= 26, str(det))
        check(f"{case}: tile – every button row flush with the content width", all(x <= 1 for x in geo["flush"]), str(geo["flush"]))
        check(f"{case}: tile – details chevron, padding top = bottom", geo["chevron"] == "solid" and abs(geo["top"] - geo["bottom"]) <= 3,
              str(geo))
        row.screenshot(path=out / f"{case}-tile.png")
        row.locator("td.c-details summary").click()
