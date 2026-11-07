"""Browser check: versioned assets come from the cache on reload; /api/state answers 304."""

import json

from playwright.sync_api import sync_playwright

CHROME = "/home/user/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"
res = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=CHROME)
    page = b.new_page()
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": False})
    seen = []
    cdp.on("Network.responseReceived", lambda e: seen.append((e["response"]["url"].split("/ui/")[-1], e["response"]["status"],
                                                             e["response"].get("fromDiskCache") or e["response"].get("fromMemoryCache") or False)))
    cdp.on("Network.requestServedFromCache", lambda e: seen.append(("served-from-cache", e["requestId"], True)))
    page.goto("http://127.0.0.1:8802/ui/")
    page.wait_for_selector("#dev-cards table")
    res["first_load"] = [s for s in seen if "static" in s[0]]
    seen.clear()
    page.goto("about:blank")
    page.goto("http://127.0.0.1:8802/ui/")  # new navigation, not a forced reload
    page.wait_for_selector("#dev-cards table")
    page.wait_for_timeout(7000)  # busy scenario: polling every 3 s
    res["second_load_static"] = [s for s in seen if "static" in s[0]]
    res["state"] = [s for s in seen if "api/state" in s[0]]
    res["rendered"] = page.evaluate("() => document.querySelectorAll('#dev-cards tbody tr').length")
    b.close()
print(json.dumps(res, indent=1))
