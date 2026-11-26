"""Feature suite firmware_store: runs the area checks of devices, firmware_tab and logs per viewport
(demos "features" 8805, "idle" 8801, "missing" 8804)."""

from types import SimpleNamespace

from features.base import FEATURES, FULL, IDLE, MISSING, OVERFLOW_JS, check, menu_items, open_page, pick_filter
from features.devices import (_action_popups, _battery, _device_list, _install_steps, _keep_listed, _layout, _name_tips,
                              _security_strip, _next_steps, _sections)
from features.firmware_tab import _downgrade, _firmware_delete_and_upload, _firmware_tab, _sorting_and_downgrade
from features.logs import _logs


def firmware_store(browser, out):
    """Device list, downgrade, Firmware tab and Logs tab – one helper per area (the last
    case changes the demo state). Quick: a phone (embedded) and a desktop viewport;
    --full adds 430 px dark and 1440 px light."""
    cases = (((360, "light", True), (430, "dark", False), (1440, "dark", False), (1440, "light", False)) if FULL
             else ((360, "light", True), (1440, "dark", False)))
    for index, (width, scheme, embedded) in enumerate(cases):
        last = index == len(cases) - 1
        case = f"features-{width}-{scheme}{'-embedded' if embedded else ''}"
        entry = "Downgrade"
        if index == 0:   # No stored older version (demo "idle") -> entry disabled, no dialog
            ctx, page, f, errors = open_page(browser, IDLE, width, scheme, embedded)
            items = dict(menu_items(f, "Hall"))
            check(f"{case}: downgrade disabled without a stored older version", items.get(entry) is True, str(items))
            ctx.close()
            # Option "Allow downgrades" off (demo "missing"): no menu entry, no Firmware-tab icon
            ctx, page, f, errors = open_page(browser, MISSING, width, scheme, embedded)
            items = dict(menu_items(f, "Hall"))
            check(f"{case}: downgrade off – no menu entry", entry not in items, str(items))
            page.screenshot(path=out / f"{case}-downgrade-off.png")
            page.keyboard.press("Escape")
            f.click("#tab-firmware")
            pick_filter(f, "firmware", "All")
            icons = f.locator(".fw-row .actions button[aria-label^='Downgrade to']").count()
            check(f"{case}: downgrade off – no downgrade icon", icons == 0, str(icons))
            ctx.close()
        ctx, page, f, errors = open_page(browser, FEATURES, width, scheme, embedded, ignore=("/upload/confirm",))

        c = SimpleNamespace(case=case, f=f, page=page, out=out, width=width, index=index, last=last)
        _device_list(c)
        _security_strip(c)
        _layout(c)
        _keep_listed(c)
        _action_popups(c)
        _name_tips(c)
        _battery(c)
        _sections(c)
        _install_steps(c)
        _next_steps(c)
        _downgrade(c)
        rows = _firmware_tab(c)
        _sorting_and_downgrade(c)
        _firmware_delete_and_upload(c, rows)
        _logs(c)

        check(f"{case}: no overflow", f.evaluate(OVERFLOW_JS) <= 0)
        check(f"{case}: no console errors", not errors, "; ".join(errors[:3]))
        ctx.close()
