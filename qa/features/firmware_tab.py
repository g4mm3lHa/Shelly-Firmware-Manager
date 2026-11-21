"""Firmware store suite, Firmware tab area: downgrade, rows, sorting, delete and upload (demo "features" on 8805)."""

from features.base import OVERFLOW_JS, check, dialog, firmware_zip, menu_items, refresh_state, tip_text, wait_until


def _downgrade(c):
    """Row menu entry and the three steps of a downgrade (nothing is installed)."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    entry = "Downgrade"
    # Menu entry: Gen2+ (also ignored), never Gen1
    items = dict(menu_items(f, "Hall"))
    check(f"{case}: menu entry for Gen2", entry in items and items[entry] is False, str(items))
    page.screenshot(path=out / f"{case}-menu.png")
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
    page.screenshot(path=out / f"{case}-downgrade-list.png")
    f.locator("#dlg-body .modes input").nth(1).check()

    # Step 2: installation dialog with the older target
    f.locator("#dlg-btns button", has_text="Continue").click()
    d = dialog(f)
    check(f"{case}: install dialog with older target", d["title"] == "Downgrade?" and "Version (older)" in d["body"]
          and "1.3.0" in d["body"] and "A downgrade installs an older firmware" in d["body"], d["body"][:200])
    page.screenshot(path=out / f"{case}-downgrade-install.png")

    # Step 3: second confirmation
    f.locator("#dlg-btns button", has_text="Continue").click()
    d = dialog(f)
    go = dict(d["buttons"]).get("Downgrade")
    check(f"{case}: confirmation button disabled", go is True, str(d["buttons"]))
    f.locator("#dlg-body input[type=checkbox]").check()
    go = dict(dialog(f)["buttons"]).get("Downgrade")
    check(f"{case}: enabled after acknowledgment", go is False)
    page.screenshot(path=out / f"{case}-downgrade-confirm.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()   # nothing is installed in the demo


def _firmware_tab(c):
    """Firmware tab: summary, locks, icons, Install button per device, changelog dialog.
    Returns the version rows."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    # Firmware tab
    f.locator("#tab-firmware").click()
    f.wait_for_selector(".fw-row")
    summary = f.locator("#fw-summary").text_content()
    check(f"{case}: total size shown", summary.startswith("4 files") and "MB stored" in summary, summary)
    tip = f.locator("#fw-info").get_attribute("data-tip")
    check(f"{case}: no automatic deletion hint", "never deleted automatically" in tip, tip[:60])
    rows = f.locator(".fw-row")
    centers = rows.first.locator(".actions > *").evaluate_all(
        "els => els.map(e => { const r = e.getBoundingClientRect(); return Math.round(r.top + r.height / 2); })")
    check(f"{case}: changelog, download and delete icons on one line", len(centers) == 3 and max(centers) - min(centers) <= 1, str(centers))
    first_del = rows.first.locator("button[aria-label], button.red").last
    check(f"{case}: provided file can be deleted", first_del.is_enabled() and first_del.get_attribute("title") == "Delete",
          str(first_del.get_attribute("title")))
    first_del.click()
    d = dialog(f)
    check(f"{case}: delete dialog with red trash title icon and red Continue", f.locator("#dlg-title svg.ti-delete").count() == 1
          and "danger" in f.locator("#dlg-btns button", has_text="Continue").get_attribute("class"))
    check(f"{case}: delete dialog names the devices that lose the provided update",
          "lose the provided update" in d["body"] or "loses the provided update" in d["body"], d["body"][:200])
    c.page.screenshot(path=c.out / f"{case}-delete-provided.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()
    check(f"{case}: upload tag in the list", f.locator(".fw-row .tag.warn", has_text="Uploaded").count() == 1)
    step = f.locator(".fw-row .tag.step")
    style = step.first.evaluate("e => { const s = getComputedStyle(e); return [s.backgroundColor, s.borderTopStyle, s.borderTopWidth]; }")
    check(f"{case}: intermediate label outlined", step.count() == 1 and step.first.inner_text() == "intermediate"
          and style == ["rgba(0, 0, 0, 0)", "solid", "1px"], str(style))
    labels = f.evaluate("""() => [...document.querySelectorAll('.fw-row .fw-ver')].filter(v => v.querySelector('.tag')).map(v => {
      const n = v.querySelector('.name').getBoundingClientRect(), t = v.querySelector('.tag').getBoundingClientRect();
      return {gap: Math.round(t.left - n.right), dy: Math.abs((n.top + n.height / 2) - (t.top + t.height / 2))}; })""")
    check(f"{case}: labels 12 px after the version, centred", labels and all(x["gap"] == 12 and x["dy"] < 1.5 for x in labels), str(labels))
    up = f.evaluate("""() => { const b = document.getElementById('fw-upload'), s = b.querySelector('svg'), r = e => e.getBoundingClientRect();
      const t = document.createRange(); t.selectNodeContents(b.firstChild); const tr = t.getBoundingClientRect();
      return {size: Math.round(r(s).width), hidden: s.getAttribute('aria-hidden'), after: r(s).left > tr.right,
        dy: Math.abs((r(s).top + r(s).height / 2) - (tr.top + tr.height / 2)), name: b.textContent}; }""")
    if width < 700:   # phones: the icon only, round 44 px at the right end of toolbar row 2
        geo = f.evaluate("""() => { const b = document.getElementById('fw-upload'), r = b.getBoundingClientRect(),
          row = b.parentElement.getBoundingClientRect();
          return {w: Math.round(r.width), h: Math.round(r.height), right: Math.round(row.right - r.right),
            text: getComputedStyle(b.querySelector('span')).display, label: b.getAttribute('aria-label')}; }""")
        check(f"{case}: upload button as icon at the right end", geo["w"] == 44 and geo["h"] == 44 and geo["right"] <= 1
              and geo["text"] == "none" and geo["label"] == "Upload firmware" and up["hidden"] == "true", str(geo))
    else:
        check(f"{case}: upload button with the upload icon after the text", up["size"] == 16 and up["hidden"] == "true"
              and up["after"] and up["dy"] < 1.5 and up["name"] == "Upload firmware", str(up))
    head = f.evaluate("""() => { const h = document.querySelector('#fw-cards .fw-head'), row = document.querySelector('#fw-cards .fw-row');
      if (getComputedStyle(h).display === 'none') return {shown: false,
        labels: [...row.querySelectorAll('[data-label]')].map(e => getComputedStyle(e, '::before').content)};
      const mid = e => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; };
      const titles = [...h.children], cells = [...row.children];
      return {shown: true, titles: titles.map(t => t.textContent), first: Math.round(titles[0].getBoundingClientRect().left - cells[0].getBoundingClientRect().left),
        centred: titles.slice(1, 5).map((t, i) => getComputedStyle(t).textAlign === 'center' && Math.abs(mid(t) - mid(cells[i + 1])) < 1)}; }""")
    if width > 700:
        check(f"{case}: firmware table head – titles over the columns, centred except Version", head["shown"]
              and head["titles"] == ["Version", "Size", "Stored", "SHA-256", "Devices", ""] and head["first"] == 0
              and all(head["centred"]), str(head))
    else:
        check(f"{case}: phones – no table head, labels in the tile", not head["shown"]
              and head["labels"] == ['"Size"', '"Stored"', '"SHA-256"'], str(head))
    if width > 700:
        step.first.hover()
    else:
        page.wait_for_timeout(300)   # a re-render after the previous state request hides every tooltip
        step.first.tap()
    check(f"{case}: intermediate label tooltip", tip_text(f) == "Intermediate version: required step before newer firmware can be installed safely",
          str(tip_text(f)))
    if width > 700:
        page.mouse.move(5, 5)
    else:
        step.first.tap()   # second tap hides it
    if width < 700:
        dates = f.evaluate("""() => [...document.querySelectorAll('.fw-row')].map(r => { const g = s => r.querySelector(s).getBoundingClientRect();
          return {right: Math.round(g('.actions').right - g('.fw-date').right), line: Math.round(Math.abs(g('.fw-date').top - g('.fw-size').top)),
            x: Math.round(g('.fw-date').right)}; })""")
        check(f"{case}: date right-aligned under the icons, same line as the size", all(d["right"] == 0 and d["line"] <= 1 for d in dates)
              and len({d["x"] for d in dates}) == 1, str(dates))
    else:
        page.mouse.move(5, 5)
    check(f"{case}: no overflow (firmware)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-firmware.png", full_page=True)

    # Install button behind every device a file is provided for; it opens
    # the usual installation dialog, which names the source of the update
    marker = f.evaluate("""() => { const s = document.querySelector('.fw-dev summary'), cs = getComputedStyle(s);
      return {text: s.textContent, list: cs.listStyleType, after: getComputedStyle(s, '::after').borderRightStyle}; }""")
    check(f"{case}: 'For n devices' with one chevron only", "▾" not in marker["text"] and marker["list"] == "none"
          and marker["after"] == "solid", str(marker))
    rows.first.locator(".fw-dev summary").click()
    entries = rows.first.locator(".fw-dev li")
    buttons = rows.first.locator(".fw-dev li button")
    # 5 ready devices + the failed installation (its file stays provided for a retry)
    # + the stored version offered while the update server could not be asked
    check(f"{case}: one Install button per device", entries.count() == 7 and buttons.count() == 7,
          f"{entries.count()} / {buttons.count()}")
    names = [t.replace("Install", "").strip() for t in entries.all_inner_texts()]
    check(f"{case}: devices ordered by name", names == sorted(names, key=str.lower) and "Kitchen light" in names, str(names))
    kitchen = entries.filter(has_text="Kitchen light").locator("button")   # got its update from the store
    check(f"{case}: Install enabled for a ready device", kitchen.is_enabled(), str(kitchen.get_attribute("title")))
    check(f"{case}: no overflow (firmware devices)", f.evaluate(OVERFLOW_JS) <= 0)
    page.screenshot(path=out / f"{case}-firmware-devices.png", full_page=True)
    kitchen.click()
    d = dialog(f)
    check(f"{case}: installation dialog with the chip title icon", f.locator("#dlg-title svg.ti-chip").count() == 1)
    check(f"{case}: Install opens the installation dialog", d["title"] == "Install update?"
          and "Kitchen light" in d["body"] and "Firmware store (downloaded from Shelly earlier)" in d["body"], d["body"][:300])
    page.screenshot(path=out / f"{case}-firmware-install.png")
    f.locator("#dlg-btns button", has_text="Cancel").click()   # nothing is installed in the demo
    rows.first.locator(".fw-dev summary").click()

    # Changelog icon per version – dialog with the stored changelog, disabled without one
    logs = f.locator(".fw-row .actions button[aria-label^='Changelog of']")
    check(f"{case}: one changelog icon per version", logs.count() == rows.count(), f"{logs.count()} / {rows.count()}")
    check(f"{case}: icon disabled without a stored changelog", logs.nth(1).is_disabled()
          and "No changelog stored" in logs.nth(1).get_attribute("title"), str(logs.nth(1).get_attribute("title")))
    if width < 700:   # touch: a tap on a disabled button shows why it is disabled
        logs.nth(1).tap(force=True)
        check(f"{case}: tap on a disabled button shows its reason", (tip_text(f) or "").startswith("No changelog stored"), str(tip_text(f)))
        f.locator("#fw-summary").tap()
        check(f"{case}: tap elsewhere hides it (firmware)", tip_text(f) is None)
    logs.first.click()
    f.wait_for_selector("#dlg-body .cl-list")
    d = dialog(f)
    check(f"{case}: changelog dialog with title icon", f.locator("#dlg-title svg.ti-changelog").count() == 1)
    check(f"{case}: changelog dialog of the stored version", d["title"] == "Changelog – Plus1 1.5.0"
          and "1.5.0 · 2026-10-01" in d["body"] and "Fixed a vulnerability" in d["body"] and "This model" in d["body"], d["body"][:300])
    check(f"{case}: changelog dialog fits", f.evaluate("() => { const r = document.getElementById('dlg').getBoundingClientRect();"
                                                       f" return r.left >= 0 && r.right <= {width}; }}"))
    page.screenshot(path=out / f"{case}-firmware-changelog.png")
    f.locator("#dlg-btns button", has_text="Close").click()
    return rows


def _checked(f, view):
    """Checked fields of the sort popover of a tab: [field, direction]."""
    return f.evaluate(f"""() => [...document.querySelectorAll('#view-{view} .sort-item[aria-checked=true]')]
      .map(b => b.dataset.value)""")


def _unused_chip(c):
    """Firmware chip "Unused": files of a firmware ID no device has; while a device's
    firmware ID is unknown (never reached) nothing counts as unused (tooltip says why)."""
    case, f, page = c.case, c.f, c.page
    unknown = {"on": False}

    def other_types(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
        state = route.fetch(headers=headers).json()
        for d in state["devices"]:
            d["type"] = "OtherType"
        if unknown["on"]:
            state["devices"][0]["type"] = None
        route.fulfill(json=state)

    def unused():
        chip = f.locator("#fw-chips .chip", has_text="Unused")
        return int(chip.locator(".count").inner_text()), chip.get_attribute("title") or ""

    page.route("**/api/state", other_types)
    refresh_state(page, f)
    count, title = unused()
    check(f"{case}: Unused counts files without a device", count == f.locator(".fw-row").count() and not title, f"{count} {title}")
    unknown["on"] = True
    refresh_state(page, f)
    count, title = unused()
    check(f"{case}: unknown firmware ID blocks Unused", count == 0 and "1 device not known" in title.replace("ID of ", ""), f"{count} {title}")
    page.unroute("**/api/state")
    refresh_state(page, f)


def _sorting_and_downgrade(c):
    """Toolbar tools (search and sort icons with popovers; Devices and Firmware tab, kept
    per browser, coupled with the column heads on desktop) and the downgrade icon of an
    older version in the Firmware tab (dialog -> device -> second confirmation)."""
    case, f, page, out, width = c.case, c.f, c.page, c.out, c.width
    f.locator("#tab-devices").click()
    chips = f.evaluate("() => [...document.querySelectorAll('#dev-chips .chip')].map(c => c.firstChild.textContent)")
    check(f"{case}: Devices filter chips", chips == ["All", "Updates", "Errors", "Up-To-Date", "Unprotected", "Ignored"], str(chips))
    f.locator("#dev-chips .chip", has_text="Unprotected").click()
    shown = f.evaluate("() => [...document.querySelectorAll('#dev-cards tbody tr')].length")
    expected = f.locator("#dev-chips .chip", has_text="Unprotected").locator(".count").inner_text()
    check(f"{case}: Unprotected filter shows its count", str(shown) == expected, f"{shown} / {expected}")
    f.locator("#dev-chips .chip", has_text="All").click()
    if width < 700:   # header: pills stacked left, "Check now" right on the same line, not wrapped;
        # centred with the title block (embedded: the pills only, the title is hidden)
        head = f.evaluate("""() => { const r = e => e.getBoundingClientRect(), m = r(document.querySelector('.titlebar>div')),
          s = r(document.getElementById('scan')), p = [...document.querySelectorAll('#meta .meta-pill')].map(r);
          return {right: s.left >= m.right, mid: Math.abs((s.top + s.height / 2) - (m.top + m.height / 2)),
            stacked: p.length === 2 && p[1].top >= p[0].bottom, lines: Math.round(s.height)}; }""")
        check(f"{case}: header – pills stacked, Check now right and centred", head["right"] and head["mid"] < 2
              and head["stacked"] and head["lines"] <= 48, str(head))
    tools = f.locator("#view-devices .tool-btn")
    size = tools.first.evaluate("b => Math.round(b.getBoundingClientRect().width)")
    check(f"{case}: search and sort icons in the Devices toolbar", tools.count() == 2 and size == (44 if width < 700 else 36),
          f"{tools.count()} / {size}")
    sort = tools.nth(1)
    sort.click()
    pop = f.locator("#view-devices .tool.open .tool-pop")
    check(f"{case}: sort popover opens below the icon", pop.is_visible() and _checked(f, "devices") == ["device", "asc"],
          str(_checked(f, "devices")))
    if width < 700:   # phones: popover over the full width
        w = pop.evaluate("p => Math.round(p.getBoundingClientRect().width)")
        check(f"{case}: sort popover full width", w >= width - 20, str(w))
    page.screenshot(path=out / f"{case}-sort-popover.png")
    pop.locator(".sort-item", has_text="Descending").click()
    stored = f.evaluate("() => localStorage.getItem('shelly-firmware-manager:devices')")
    check(f"{case}: sort direction kept per browser", '"desc"' in (stored or ""), str(stored))
    page.keyboard.press("Escape")
    check(f"{case}: Escape closes the popover", f.locator("#view-devices .tool.open").count() == 0)
    if width > 700:
        head = f.locator("#dev-cards th.c-device").first.get_attribute("data-dir")
        check(f"{case}: column head follows the sort tool", head == "desc", str(head))
        f.locator("#dev-cards th.c-ip").first.click()
        check(f"{case}: sort tool follows the column head", _checked(f, "devices") == ["ip", "asc"], str(_checked(f, "devices")))
        f.locator("#dev-cards th.c-device").first.click()
    else:
        sort.click()
        f.locator("#view-devices .sort-item", has_text="Ascending").click()
        f.locator("#meta .meta-pill").first.click()   # a click outside closes the popover
        check(f"{case}: a click outside closes the popover", f.locator("#view-devices .tool.open").count() == 0)
    check(f"{case}: back to device name ascending", "asc" in f.evaluate(
        "() => localStorage.getItem('shelly-firmware-manager:devices')"))
    # Search: popover with the field, a dot on the icon while a text is set
    search = tools.first
    search.click()
    f.locator("#dev-search").fill("kitchen")
    page.keyboard.press("Enter")
    marked = search.evaluate("b => b.classList.contains('has-value') && getComputedStyle(b, '::after').content !== 'none'")
    rows = f.locator("#dev-cards tbody tr").count()
    check(f"{case}: search filters and marks the icon", marked and rows == 1, f"{marked} / {rows}")
    search.click()
    f.locator("#dev-search").fill("")
    page.keyboard.press("Enter")
    check(f"{case}: no dot without search text", not search.evaluate("b => b.classList.contains('has-value')"))
    f.locator("#tab-firmware").click()
    f.wait_for_selector(".fw-row")
    check(f"{case}: sort tool in the Firmware toolbar", _checked(f, "firmware") == ["type", "asc"], str(_checked(f, "firmware")))
    chips = f.evaluate("() => [...document.querySelectorAll('#fw-chips .chip')].map(c => c.firstChild.textContent)")
    check(f"{case}: Firmware filter chips", chips == ["All", "Provided", "Uploaded", "Intermediate", "Gen1", "Unused"], str(chips))
    _unused_chip(c)
    f.locator("#fw-chips .chip", has_text="Uploaded").click()
    tags = f.evaluate("() => [...document.querySelectorAll('.fw-row')].map(r => !!r.querySelector('.fw-ver .tag.warn'))")
    check(f"{case}: Uploaded filter shows only uploads", tags and all(tags), str(tags))
    f.locator("#fw-chips .chip", has_text="All").click()
    summary = f.locator("#fw-summary").text_content()
    check(f"{case}: free disk space in the summary", " free" in summary, summary)
    if width > 700:
        f.locator(".fw-head .sortable", has_text="Size").first.click()
        check(f"{case}: Firmware column head sorts the cards", _checked(f, "firmware")[0] == "size"
              and f.locator(".fw-head [data-dir]").count() == f.locator(".fw-head").count())
        f.locator("#view-firmware .tool-btn").nth(1).click()
        f.locator("#view-firmware .sort-item", has_text="Firmware ID").click()
        page.keyboard.press("Escape")
    icons = f.locator(".fw-row .actions button[aria-label^='Downgrade to']")
    enabled = [i for i in range(icons.count()) if icons.nth(i).is_enabled()]
    check(f"{case}: downgrade icon on older versions", bool(enabled), str(icons.count()))
    if enabled:
        icons.nth(enabled[0]).click()
        d = dialog(f)
        check(f"{case}: Firmware-tab downgrade dialog lists devices", d["title"].startswith("Downgrade to")
              and f.locator("#dlg-body .modes input[type=radio]").count() >= 1, d["title"])
        page.screenshot(path=out / f"{case}-fw-downgrade.png")
        f.locator("#dlg-btns button", has_text="Continue").click()
        d = dialog(f)
        check(f"{case}: then the second confirmation", d["title"] == "Downgrade?"
              and dict(d["buttons"]).get("Downgrade") is True, str(d["buttons"]))
        f.locator("#dlg-btns button", has_text="Cancel").click()


def _firmware_delete_and_upload(c, rows):
    """Two-step delete and the upload dialog (the last case changes the demo state)."""
    case, f, page, out, index, last = c.case, c.f, c.page, c.out, c.index, c.last
    # Two-step delete of 1.3.0
    row = rows.filter(has_text="1.3.0")
    row.locator("button.red").click()
    check(f"{case}: delete step 1", "Delete firmware?" == dialog(f)["title"])
    f.locator("#dlg-btns button", has_text="Continue").click()
    buttons = dict(dialog(f)["buttons"])
    check(f"{case}: delete needs acknowledgment", buttons.get("Delete") is True, str(buttons))
    f.locator("#dlg-body input[type=checkbox]").check()
    page.screenshot(path=out / f"{case}-delete-confirm.png")
    if last:
        f.locator("#dlg-btns button", has_text="Delete").click()
        check(f"{case}: deleted", wait_until(lambda: f.locator(".fw-row", has_text="1.3.0").count() == 0))
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()

    # Upload: pre-filled form; wrong SHA shows an inline error (first case), store (last case)
    f.locator("#fw-upload").click()
    check(f"{case}: upload dialog with title icon", f.locator("#dlg-title svg.ti-upload").count() == 1)
    f.locator("#dlg-body input[type=file]").set_input_files(
        files=[{"name": "plüs1-1.1.0.zip", "mimeType": "application/zip", "buffer": firmware_zip()}])
    page.screenshot(path=out / f"{case}-upload-choose.png")
    f.locator("#dlg-btns button", has_text="Upload").click()
    f.wait_for_selector("#dlg-body select")
    check(f"{case}: type pre-filled", f.locator("#dlg-body select").input_value() == "Plus1")
    check(f"{case}: version pre-filled", f.locator("#dlg-body input[type=text]").first.input_value() == "1.1.0")
    check(f"{case}: upload confirmation with title icon", f.locator("#dlg-title svg.ti-upload").count() == 1)
    if index == 0:
        f.locator("#dlg-body input[type=text]").nth(1).fill("a" * 64)
        f.locator("#dlg-btns button", has_text="Store").click()
        err = f.locator("#dlg-body .bad")
        check(f"{case}: wrong SHA-256 shown inline", wait_until(err.is_visible) and "does not match" in err.text_content())
        page.screenshot(path=out / f"{case}-upload-error.png")
    page.screenshot(path=out / f"{case}-upload-confirm.png")
    if last:
        f.locator("#dlg-btns button", has_text="Store").click()
        check(f"{case}: upload stored", wait_until(lambda: f.locator(".fw-row", has_text="1.1.0").count() == 1))
        page.screenshot(path=out / f"{case}-firmware-after.png", full_page=True)
    else:
        f.locator("#dlg-btns button", has_text="Cancel").click()
        f.wait_for_selector("#dlg", state="hidden")
