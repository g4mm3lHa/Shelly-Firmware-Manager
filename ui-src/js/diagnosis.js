// Diagnosis and orientation: the connection test of the download server (Settings tab,
// installation dialog, card "Getting started"), the card "Getting started" of a fresh
// installation, "Try again" for a device that did not answer, the text for a bug ticket
// and the comparison after an update.
import {
  $, api, attachTip, box, checkLine, copyText, devUrl, el, field, fmt, guarded, icon, message, messageBody, modal, modelGen, section, t, tn,
  toast, ui, valueList,
} from "./core.js";
import {lang} from "./i18n.js";
import {load, render, showView} from "./main.js";

// ---------------------------------------------------------------- Connection test
// A Gen2+ Shelly fetches a test address from the download server (server: run_connection_test).
// The answer arrives once the device has tried (one device, or up to three in the automatic
// choice); the last result is part of the state (connection_test) while the download base
// URL is unchanged.
const TEST_TITLE = t("Test the connection to the download server");
const TEST_CAUSES = [t("The IP address is not the LAN address of Home Assistant."),
  t("The host port 8098 was changed or switched off in the app configuration (Network)."),
  t("A firewall or a separate network (VLAN, guest Wi-Fi) lies between the Shelly and Home Assistant.")];
/** Port of a URL (the default port of its scheme if it names none). */
const urlPort = url => { try { const u = new URL(url); return u.port || (u.protocol === "https:" ? "443" : "80"); } catch (e) { return "8098"; } };
/** "Reachable · last tested <time> with <device>" (or not reachable, or not tested yet). */
function testNote(test) {
  if (!test) return el("span", t("Not tested yet"), "conn-res");
  const text = t(test.ok ? "Reachable · last tested {time} with {device}" : "Not reachable · last tested {time} with {device}",
    {time: fmt(test.at), device: test.device || "–"});
  return box("span", "conn-res " + (test.ok ? "ok" : "err"), icon(test.ok ? "check" : "close"), el("span", text));
}
let connNote = null;   // result line of the Settings tab (null while the tab was never shown)
/** Row below the download base URL in the Settings tab: "Test connection" and the last
 *  result. The test uses the saved address: while the field holds an unsaved change (or
 *  installations are disabled by the address) the button is disabled with the reason. */
export function connectionRow(urlInput) {
  const b = el("button", null, "icon-text sm"); b.type = "button"; b.append(icon("play"), el("span", t("Test connection")));
  b.onclick = () => openConnectionTest();
  connNote = box("span", null, testNote(ui.state && ui.state.connection_test));
  const sync = () => {
    const base = (ui.state && ui.state.download_base) || {}, unsaved = urlInput.value.trim() !== ((ui.options && ui.options.download_base_url) || "");
    b.disabled = unsaved || !!base.issue;
    b.dataset.tip = unsaved ? t("Save first") : base.issue ? base.message || t("Download base URL is not set") : "";
  };
  attachTip(b, () => b.dataset.tip);
  urlInput.addEventListener("input", sync); sync();
  return box("div", "set-field conn-row", box("div", "set-name"), box("div", "set-control conn-ctl", b, connNote));
}
/** Refresh the result line of the Settings tab (after a test). */
function syncConnNote() { if (connNote) connNote.replaceChildren(testNote(ui.state && ui.state.connection_test)); }
/** Dialog of the connection test: the address, the choice of the test device and "Start
 *  test"; without a device of Gen 2 or newer only the hint to run a check first. */
export function openConnectionTest(device = "") {
  const s = ui.state, url = s.download_base.url;
  const intro = el("p", t("A Shelly fetches a test address from the download server of this app. This shows before the first installation whether the address, the port and the firewall are right. Nothing is changed or installed on the device."));
  const ids = s.test_devices || [];
  if (!ids.length) {
    modal(TEST_TITLE, [intro, messageBody(t("No Shelly of Gen 2 or newer is known yet. Run “Check now” first, so that the app reads the devices from Home Assistant."), "warning")],
      undefined, "play");
    return;
  }
  const select = el("select"), auto = el("option", t("Automatic (first reachable device of Gen 2 or newer)")); auto.value = ""; select.append(auto);
  for (const id of ids) {
    const d = s.devices.find(x => x.id === id); if (!d) continue;
    const o = el("option", [d.name, `Gen ${d.gen}`, d.ip].filter(Boolean).join(" · ")); o.value = id; o.selected = id === device; select.append(o);
  }
  modal(TEST_TITLE, [intro, section(t("Test"), valueList([[t("Download base URL"), url]]), field(t("Test device"), select)),
    el("p", t("Gen 1 devices cannot run the test. The test address is valid for this one test and only for the chosen device."), "hint")],
  [{text: t("Cancel")}, {text: t("Start test"), kind: "primary", action: () => runConnectionTest(select.value)}], "play");
}
/** Lines "Download base URL", "Download server" and the fetch by the device. */
function testLines(url, state, title, text) {
  return box("ul", "prechk", checkLine("ok", t("Download base URL"), t("{url} · saved, private IP address", {url})),
    checkLine("ok", t("Download server"), t("Running in this app")), checkLine(state, title, text));
}
/** Run the test with a device ("" = automatic) and show its result. */
async function runConnectionTest(device) {
  const url = ui.state.download_base.url, d = device && ui.state.devices.find(x => x.id === device);
  modal(TEST_TITLE, [section(t("Test"), testLines(url, "run", d ? t("Fetch by {name}", {name: d.name}) : t("Fetch by a Shelly"),
    t("The device fetches the test address …")))], [{text: t("Close")}], "play");
  await guarded(TEST_TITLE, "play", async () => {
    const r = await api("api/settings/connection-test", {device: device || null});
    ui.state.connection_test = r; syncConnNote(); render(true);
    const who = t("Fetch by {name}", {name: r.ip ? `${r.device} (${r.ip})` : r.device || "–"});
    if (r.ok) {
      modal(TEST_TITLE, [messageBody(t("The download server is reachable for the Shellys. Installations can start."), "success"),
        section(t("Test"), testLines(url, "ok", who, t("Test address fetched · answer after {s} s", {s: secondsText(r.seconds)})))],
      [{text: t("Close")}], "play");
    } else {
      modal(TEST_TITLE, [messageBody(t("The Shelly cannot reach the download server. An installation would fail like this."), "error"),
        section(t("Test"), testLines(url, "err", who, r.error || "–")),
        section(t("Common causes"), box("ul", "causes", ...TEST_CAUSES.map(c => el("li", c)))),
        el("p", t("Home Assistant itself reaches the device. Only the way from the Shelly to Home Assistant (port {port}) matters.", {port: urlPort(url)}), "hint")],
      [{text: t("Close")}, {text: t("Test again"), kind: "primary", action: () => runConnectionTest(device)}], "play");
    }
    load();
  });
}
/** Seconds with one decimal in the format of the page language. */
const secondsText = s => typeof s === "number" ? s.toLocaleString(lang, {maximumFractionDigits: 1}) : "–";
/** Line of "Before installing" while the connection was never tested successfully (with
 *  the current address); null once it was, or when no device can run the test. */
export function connectionCheckLine() {
  const s = ui.state, test = s.connection_test;
  if ((test && test.ok) || !(s.test_devices || []).length || (s.download_base && s.download_base.issue)) return null;
  const li = checkLine("warn", t("Download server"), test ? t("The last connection test failed. If the connection fails, the installation stops.")
    : t("The connection was not tested yet. If it fails, the installation stops."));
  const b = el("button", t("Test now"), "small inline-btn"); b.type = "button"; b.onclick = () => openConnectionTest();
  li.lastChild.append(" ", b);
  return li;
}

// ---------------------------------------------------------------- Getting started
// Card above the device list of a fresh installation (state getting_started): three steps
// in this order – download address, first check, connection test. Each step is ticked
// from the state; only the next open step has an active button. After the third tick
// the card stays until "Hide".
/** The card "Getting started", or null while it is not shown. */
export function gettingStartedCard() {
  const s = ui.state;
  if (!s.getting_started) return null;
  const devices = s.devices.filter(d => !d.removed), updates = devices.filter(d => ["available", "ready"].includes(d.status)).length;
  const steps = [
    {title: t("Set the download address"), text: t("The Shellys load the firmware from Home Assistant: LAN IP and port 8098."),
      done: !!(s.download_base.url && !s.download_base.issue), result: s.download_base.url, action: t("Open settings"), run: () => showView("settings")},
    {title: t("Start the first check"), text: t("Reads the Shellys from Home Assistant and asks for new firmware. Nothing is installed."),
      done: !!s.last_scan, result: tn(devices.length, "1 Shelly found", "{n} Shellys found") + " · " + tn(updates, "1 update", "{n} updates"),
      action: t("Check now"), run: () => api("api/scan", {}).then(() => load(true)).catch(e => message(t("Check"), e.message, "error")), blocked: () => $("scan").disabled},
    {title: t("Test the connection"), text: t("A Shelly fetches a test address. This shows before the first installation whether the address, the port and the firewall are right."),
      done: !!(s.connection_test && s.connection_test.ok), result: s.connection_test && t("Reachable · tested with {device}", {device: s.connection_test.device || "–"}),
      action: t("Test now"), run: () => openConnectionTest()},
  ];
  const next = steps.findIndex(x => !x.done), count = steps.filter(x => x.done).length;
  const items = steps.map((step, i) => {
    const mark = box("span", "step-mark", step.done ? icon("check") : el("span", String(i + 1)));
    const body = box("div", "step-body", el("strong", step.title), el("span", step.done ? step.result : step.text, "sub"));
    let button = null;
    if (!step.done) {
      button = el("button", step.action, i === next ? "primary" : ""); button.type = "button";
      button.disabled = i !== next || !!(step.blocked && step.blocked()); button.onclick = step.run;
    }
    return box("li", "step" + (step.done ? " ok" : i === next ? " now" : ""), mark, body, button);
  });
  const head = box("div", "card-head", el("h2", t("Getting started")), el("span", t("{n} of 3 done", {n: count}), "tag" + (count === 3 ? " sev-success" : " primary")),
    el("span", null, "spacer"));
  if (count === 3) {
    const hide = el("button", t("Hide"), "small"); hide.type = "button";
    hide.onclick = () => api("api/getting-started/hide", {}).then(() => load(true)).catch(e => message(t("Getting started"), e.message, "error"));
    head.append(hide);
  }
  const foot = count === 3 ? el("p", t("Everything is set up. Updates are installed only after your approval."), "hint steps-foot") : null;
  return box("div", "card start-card", head, box("ol", "steps", ...items), foot);
}

// ---------------------------------------------------------------- Not reachable
// Device details of a device that did not answer its last check: plain text, common causes
// and "Try again" (address, port and answer of the device, nothing stored; no automatic
// new attempt).
const PROBE_TITLES = {address: t("Address"), device: t("Device")};
/** Block below the head of the device details. */
export function unreachableBlock(d) {
  const again = el("button", null, "icon-text sm"); again.type = "button"; again.append(icon("restart"), el("span", t("Try again")));
  const note = el("span", t("Checks the address, port 80 and the answer time. No automatic new attempt."), "sub");
  const result = box("ul", "prechk probe-res"); result.hidden = true;
  again.onclick = async () => {
    again.disabled = true; note.hidden = true; result.hidden = false;
    result.replaceChildren(checkLine("run", PROBE_TITLES.address, t("Asking Home Assistant …")), checkLine("wait", t("Port"), null), checkLine("wait", PROBE_TITLES.device, null));
    try {
      const r = await api(`${devUrl(d)}/probe`, {});
      result.replaceChildren(probeLine("address", r.address), probeLine("port", r.port), probeLine("device", r.device));
    } catch (e) { result.replaceChildren(checkLine("err", t("Try again"), e.message)); }
    again.disabled = false;
  };
  return box("div", "unreach", el("p", t("The device did not answer. Home Assistant may still show it as available.")),
    el("div", t("Common causes"), "unreach-title"),
    box("ul", "causes", el("li", t("A battery device that is asleep right now.")), el("li", t("The device is in another network (VLAN, guest Wi-Fi) than Home Assistant.")),
      el("li", t("The address in Home Assistant is outdated (new IP from the router)."))),
    box("div", "unreach-btn", again, note), result);
}
/** One line of the result of "Try again". */
function probeLine(key, step) {
  const title = key === "port" ? (step.port ? t("Port {port}", {port: step.port}) : t("Port")) : PROBE_TITLES[key];
  if (!step || step.state === "skipped") return checkLine("wait", title, t("not tested"));
  if (step.state !== "ok") return checkLine("err", title, step.text || "–");
  if (key === "address") return checkLine("ok", title, t(step.moved ? "{ip} · from Home Assistant, new address" : "{ip} · from Home Assistant", {ip: step.ip || "–"}));
  const port = key === "port";
  return checkLine("ok", title, t(port ? "open · {s} s" : "answers · {s} s", {s: secondsText(step.seconds)}));
}

// ---------------------------------------------------------------- Details for a bug ticket
// The server writes the text (English, names and addresses masked, no passwords); the page
// adds its language and browser. Nothing is sent anywhere: "Copy text" only fills the clipboard.
const REPORT_TITLE = t("Details for a bug ticket");
/** Name and main version of the browser ("Firefox 145"), else "unknown". */
function browserName() {
  const ua = navigator.userAgent;
  for (const [name, re] of [["Edge", /Edg\/(\d+)/], ["Opera", /OPR\/(\d+)/], ["Firefox", /Firefox\/(\d+)/], ["Chrome", /Chrome\/(\d+)/], ["Safari", /Version\/(\d+).*Safari/]]) {
    const m = re.exec(ua); if (m) return `${name} ${m[1]}`;
  }
  return "unknown";
}
/** Dialog with the text for a bug ticket of a device and "Copy text". */
export async function openReport(d) {
  modal(REPORT_TITLE, [el("p", t("Loading …"), "hint")], [{text: t("Close")}], "copy");
  await guarded(REPORT_TITLE, "copy", async () => {
    const lines = (await api(`${devUrl(d)}/report`)).text.split("\n");
    lines.splice(2, 0, `Language: ${lang} · Browser: ${browserName()}`);
    const text = lines.join("\n");
    modal(REPORT_TITLE, [el("p", t("This text holds what a bug ticket on GitHub or a question in the forum needs. Names, IP addresses and MAC addresses are masked, passwords are never included.")),
      el("pre", text, "report-text"), el("p", t("Please read it before pasting. Nothing is sent – the text only goes to the clipboard."), "hint")],
    [{text: t("Close")}, {text: t("Copy text"), kind: "primary", action: async () => {
      if (await copyText(text)) toast(t("Text copied")); else toast(t("Copying is not possible here – select the text and copy it."), true);
      for (const b of $("dlg-btns").children) b.disabled = false;
    }}], "copy");
  });
}

// ---------------------------------------------------------------- Comparison after an update
// After a confirmed update the server compares the device with its backup "Before update"
// (device field after_update): changed values, settings new with the firmware (collapsed,
// they have Shelly's default value) and the scripts that ran before. Counters that change
// with every update (sys.cfg_rev) are never compared.
const COMPARE_TITLE = t("Comparison after update");
/** True if the comparison found something to look at: changed values or stopped scripts. */
const hasFindings = a => !!a && !a.error && (a.changed_count > 0 || (a.scripts && a.scripts.stopped.length > 0));
/** Line below the firmware of an updated device: "n settings changed" (or the stopped
 *  scripts), opens the comparison; null without findings. */
export function afterUpdateNote(d) {
  const a = d.after_update;
  if (!hasFindings(a)) return null;
  const b = el("button", null, "hold-note after-note"); b.type = "button";
  b.append(icon("alert"), el("span", a.changed_count ? tn(a.changed_count, "1 setting changed", "{n} settings changed")
    : tn(a.scripts.stopped.length, "1 script stopped", "{n} scripts stopped")));
  attachTip(b, () => t("Comparison with the backup made right before the update – click to show it"));
  b.onclick = () => openComparison(d);
  return b;
}
/** Table of a comparison: heads and rows (first cell the setting, last cell highlighted). */
function compareTable(heads, rows) {
  const table = el("table", null, "bk-diff"), head = el("tr");
  for (const h of heads) head.append(el("th", h));
  table.append(head);
  for (const row of rows) {
    const tr = el("tr");
    row.forEach((v, i) => tr.append(el("td", v, i === 0 ? "bk-key" : i === row.length - 1 ? "bk-new" : "")));
    table.append(tr);
  }
  return table;
}
/** Dialog "Comparison after update" of a device. */
export function openComparison(d) {
  const a = d.after_update || {};
  const head = valueList([[t("Device"), [d.name, modelGen(d)].filter(Boolean).join(" · ")], [t("Update"), a.from_version || a.to_version ? `${a.from_version || "–"} → ${a.to_version || "–"}` : null],
    [t("Compared with"), a.backup_at ? t("Backup “Before update” · {time}", {time: fmt(a.backup_at)}) : null]]);
  if (a.error) {
    modal(COMPARE_TITLE, [head, messageBody(t("The comparison could not be made: {error}", {error: a.error}), "warning")], undefined, "backup");
    return;
  }
  const scripts = a.scripts || {before: 0, again: 0, stopped: []}, lines = [
    a.changed_count ? checkLine("warn", t("Settings"), tn(a.changed_count, "1 value differs from before the update.", "{n} values differ from before the update."))
      : checkLine("ok", t("Settings"), t("All values are as before the update."))];
  if (scripts.stopped.length) {
    lines.push(checkLine("err", t("Scripts"), t("{names} ran before the update and do not run now.", {names: scripts.stopped.map(n => `“${n}”`).join(", ")})));
  } else if (scripts.before) lines.push(checkLine("ok", t("Scripts"), t("{again} of {before} run again.", {again: scripts.again, before: scripts.before})));
  if (a.new_count) lines.push(checkLine("ok", t("New fields"), tn(a.new_count, "1 setting exists only since this firmware. It has Shelly's default value.",
    "{n} settings exist only since this firmware. They have Shelly's default value.")));
  const more = (shown, total) => total > shown ? el("p", t("The first {n} are shown.", {n: shown}), "hint") : null;
  const content = [head, section(t("Result"), box("ul", "prechk", ...lines))];
  if (a.changed_count) {
    content.push(section(`${t("Changed")} · ${a.changed_count}`, compareTable([t("Setting"), t("Before the update"), t("Now")], a.changed.map(r => [r.key, r.before, r.now])),
      more(a.changed.length, a.changed_count)));
  }
  if (a.new_count) {
    const fold = el("details"); fold.append(el("summary", t("Show fields")), compareTable([t("Setting"), t("Default value")], a.new.map(r => [r.key, r.value])));
    content.push(section(`${t("New with the firmware")} · ${a.new_count}`, fold, more(a.new.length, a.new_count)));
  }
  if (d.gen >= 2) content.push(el("p", t("Not compared: counters that change with every update (sys.cfg_rev)."), "hint"));
  modal(COMPARE_TITLE, content, undefined, "backup");
}
