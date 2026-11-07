"""Capture devices, firmware and settings at 360/430/390-embedded/1440 px (light/dark)
for the 3.0.0 rc2 mobile fix. Needs demo.py on port 8711; patches firmware and
settings responses with long names, extra versions and a pinned certificate."""
import json, sys, glob
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
B = "http://127.0.0.1:8711"
LONG = "Living room ceiling light with a really long device name over the sofa"
def fw_patch(route):
    r = route.fetch(); d = r.json(); base = d["firmware"][0]
    d["firmware"][0]["used_by"] = d["firmware"][0]["used_by"] + [LONG, "Garage-door-controller-north-entrance-2nd-floor"]
    for i, v in enumerate(["1.4.0", "1.3.2"]):
        d["firmware"].append({**base, "name": f"{i}{base['name'][1:]}", "version": v, "used_by": [] if i else ["Hall"], "deletable": i == 0,
                              "sha256": ("f" * 64)})
    d["firmware"].append({**base, "name": "g1" + base["name"][2:], "type": "SHSW-1", "gen": 1, "https": False,
                          "version": "20230913-112003/v1.14.0-gcb84623", "used_by": ["Garden plug", "Kids room"]})
    route.fulfill(response=r, body=json.dumps(d), headers={**r.headers, "etag": "x"})
def set_patch(route):
    r = route.fetch(); d = r.json()
    d["pinned_certificates"] = [{"host": "updates.shelly.cloud", "sha256": "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89"}]
    route.fulfill(response=r, body=json.dumps(d))
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=glob.glob("/home/user/.cache/ms-playwright/chromium_headless_shell-1217/*/chrome-headless-shell")[0])
    cases = [(360, "dark", False), (360, "light", False), (430, "dark", False), (430, "light", False), (390, "dark", True), (1440, "dark", False), (1440, "light", False)]
    for w, scheme, emb in cases:
        ctx = br.new_context(viewport={"width": w, "height": 900}, color_scheme=scheme, device_scale_factor=1)
        p = ctx.new_page(); errs = []
        p.on("pageerror", lambda e, errs=errs: errs.append(str(e)))  # bind this case's list
        p.route("**/api/firmware", fw_patch); p.route("**/api/settings", set_patch)
        p.goto(B + ("/wrap" if emb else "/ui/")); p.wait_for_timeout(900)
        f = p.frames[1] if emb else p
        tag = f"{w}-{scheme}" + ("-embedded" if emb else "")
        full = not emb
        if w < 700:
            f.locator("td.c-details summary").nth(1).click(); p.wait_for_timeout(200)
        p.screenshot(path=OUT / f"devices-{tag}.png", full_page=full)
        # computed tile border / background colors
        info = f.evaluate("""() => { const tr = document.querySelector('#dev-cards tbody tr'); const cs = getComputedStyle(tr);
            const tb = getComputedStyle(tr.parentElement); return {border: cs.borderTopColor, bw: cs.borderTopWidth, bg: cs.backgroundColor,
            tbodyBg: tb.backgroundColor, card: getComputedStyle(document.querySelector('#dev-cards .card')).backgroundColor,
            sw: document.documentElement.scrollWidth, vw: innerWidth}; }""")
        f.click("#tab-firmware"); p.wait_for_timeout(500)
        for s in f.locator("#fw-cards details summary").all()[:1]: s.click()
        p.wait_for_timeout(200)
        p.screenshot(path=OUT / f"firmware-{tag}.png", full_page=full)
        fwinfo = f.evaluate("""() => [...document.querySelectorAll('.fw-row')].map(r => { const a = r.querySelector('.actions');
            const bs = [...a.children].map(c => c.getBoundingClientRect()); const rr = r.getBoundingClientRect();
            const kids = [...r.children].filter(c => c !== a).map(c => c.getBoundingClientRect().bottom);
            return {sameTop: Math.abs(bs[0].top - bs[1].top) < 1, belowMeta: bs[0].top >= Math.max(...kids) - 1,
                    right: Math.round(rr.right - bs[1].right), inside: [...r.querySelectorAll('*')].every(e => e.getBoundingClientRect().right <= rr.right + 0.5)}; })""")
        fwsw = f.evaluate("document.documentElement.scrollWidth")
        f.click("#tab-settings"); p.wait_for_timeout(500)
        if full:   # tall viewport: the sticky save bar then sits at its natural place
            h = f.evaluate("document.documentElement.scrollHeight"); p.set_viewport_size({"width": w, "height": h}); p.wait_for_timeout(150)
        p.screenshot(path=OUT / f"settings-{tag}.png", full_page=full)
        p.set_viewport_size({"width": w, "height": 900})
        f.locator("#opt-check_interval_days").fill("5"); p.wait_for_timeout(100)
        p.screenshot(path=OUT / f"settings-{tag}-unsaved.png", full_page=False)
        ssw = f.evaluate("document.documentElement.scrollWidth")
        ov = f.evaluate("() => [...document.querySelectorAll('#settings-form *')].filter(e => e.getBoundingClientRect().right > innerWidth + 0.5).map(e => e.tagName + '.' + e.className).slice(0,5)")
        print("  settings overflow", ov)
        print(tag, "errs", errs, "dev", info, "fw-sw", fwsw, "set-sw", ssw)
        if w < 700: print("  fw rows", fwinfo)
        ctx.close()
    br.close()
