// Entry module: rendering, tabs and polling. Importing it loads all other modules.
import {$, api, attachTip, closeModal, el, fmt, getState, hideTip, loadLocal, message, modal, saveLocal, ui} from "./core.js";
import {buildChooser, closeRowMenu, loadPrefs, renderDevices, syncChooser} from "./devices.js";
import {renderFirmware} from "./firmware.js";
import {mobileQuery, renderLogs} from "./logs.js";
import {importDialog, renderSettings} from "./app-settings.js";
import "./install.js";
import "./device-settings.js";

// ---------------------------------------------------------------- Rendering, tabs & polling
/** Render the active view; unchanged data is not re-rendered unless forced. */
export function render(force) {
  const s = ui.state;
  if (!s) return;
  renderMeta(s);
  $("busy").textContent = s.storage_error ? `Storage error: ${s.storage_error}` : s.progress ? "" : s.busy || "";
  $("busy").classList.toggle("error", !!s.storage_error);
  renderProgress(s.progress);
  renderNotice(s.first_start_open ? {issue: "first_start"} : s.download_base || {});
  // Blocked during a check or installations, and on a fresh installation until the
  // first-start question (import a firmware store?) is answered
  $("scan").disabled = !!s.busy || !!s.first_start_open;
  $("scan").title = s.first_start_open ? FIRST_START_BLOCK : "";
  if (s.first_start_open) askFirstStart();
  else if (loadLocal("firstStartAsked")) saveLocal("firstStartAsked", null);   // answered: a reinstallation asks again
  const key = JSON.stringify([ui.view, mobileQuery.matches, s, ui.view === "firmware" ? ui.firmware : ui.view === "logs" ? ui.logs : null]);
  if (!force && key === ui.rendered) return;
  ui.rendered = key; hideTip();
  if (ui.view === "devices") renderDevices();
  else if (ui.view === "firmware") renderFirmware();
  else if (ui.view === "logs") renderLogs();
}
/** Header: last check and check cycle as two neutral pills; the cycle uses the
 *  Home Assistant time zone (not the browser's). */
function renderMeta(s) {
  const sc = s.schedule, days = sc.days === 1 ? "day" : `${sc.days} days`;
  let next = "";
  try { next = new Date(sc.next * 1000).toLocaleString("en-GB", {timeZone: sc.timezone, dateStyle: "medium", timeStyle: "short"}); }
  catch (e) { next = fmt(sc.next); }
  const cycle = el("span", `Every ${days} · ${sc.time}`, "pill meta-pill");
  attachTip(cycle, () => `Next automatic check: ${next} (${sc.timezone})`, true); cycle.tabIndex = 0;
  $("meta").replaceChildren(el("span", `Last checked: ${fmt(s.last_scan)}`, "pill meta-pill"), cycle);
}
/** Notice while installations are disabled by the download base URL: not set,
 *  or still the former example value that the admin has not confirmed yet. Rebuilt only
 *  when the problem changes, so polling does not steal the focus from its buttons. */
function renderNotice(info) {
  const n = $("notice"), key = info.issue ? `${info.issue}|${info.url}` : "";
  if (n.dataset.key === key) return;
  n.dataset.key = key; n.hidden = !info.issue;
  if (!info.issue) { n.replaceChildren(); return; }
  const buttons = el("div", null, "notice-btns");
  if (info.issue === "first_start") {
    const imp = el("button", "Import store …", "primary"), skip = el("button", "Skip");
    imp.onclick = importDialog; skip.onclick = skipFirstStart;
    buttons.append(imp, skip);
    n.replaceChildren(el("span", `First start: ${FIRST_START}`), buttons);
    return;
  }
  if (info.issue === "unconfirmed") {
    const yes = el("button", "This address is correct", "primary"); yes.onclick = () => confirmDownloadBase(info.url);
    buttons.append(yes);
  }
  const open = el("button", "Open settings"); open.onclick = () => showView("settings");
  buttons.append(open);
  n.replaceChildren(el("span", info.message), buttons);
}
// Fresh installation (no stored state, also after a reinstallation): before the first
// check the app asks once whether a firmware store from another instance should be
// imported. The dialog opens once per browser; the notice bar stays until answered.
const FIRST_START = "No check runs until you choose: import a firmware store from another Home Assistant instance, or skip it.";
const FIRST_START_BLOCK = "Not before the first-start question is answered (import a firmware store or skip it)";
let firstStartAsked = false;
/** The welcome dialog, once. */
function askFirstStart() {
  if (firstStartAsked || loadLocal("firstStartAsked")) return;
  firstStartAsked = true; saveLocal("firstStartAsked", true);
  modal("Welcome – import a firmware store?", [
    el("p", "Moving from another Home Assistant instance? Import its exported firmware store first: the stored versions (also older ones Shelly no longer offers) and uploads are then available at once."),
    el("p", FIRST_START, "hint"), el("div", "You can also import later: Settings → Firmware & Storage.", "hint")],
  [{text: "Skip", action: skipFirstStart}, {text: "Import store …", kind: "primary", action: importDialog}], "importIcon");
}
/** Answer the first-start question with "Skip": checks may run from now on. */
async function skipFirstStart() {
  try { await api("api/first-start/skip", {}); closeModal(); }
  catch (e) { message("First start", e.message, "error"); }
}
/** Confirm that the former example URL really is the address of Home Assistant. */
function confirmDownloadBase(url) {
  modal("Confirm download base URL", [el("p", `Is ${url} really the LAN address of Home Assistant with the host port 8098?`),
    el("p", "The Shellys download the firmware from this address. If it is wrong, every update fails. The confirmation ends when the address is changed.", "hint")],
  [{text: "Cancel"}, {text: "Yes, this is correct", kind: "primary", action: async () => {
    try { await api("api/settings/download-base/confirm", {url}); closeModal(); }
    catch (e) { message("Download base URL", e.message, "error"); }
  }}]);
}
const PHASES = {queued: "starting", registry: "reading the device list", devices: "checking devices",
  download: "downloading firmware", changelog: "checking firmware information"};
/** Global progress line (and bar) of a running check. */
function renderProgress(p) {
  const box_ = $("progress");
  box_.hidden = !p;
  if (!p) return;
  const parts = ["Checking devices"];
  if (p.total) parts.push(`${p.done}/${p.total} completed`);
  if (p.errors) parts.push(`${p.errors} unavailable`);
  parts.push(PHASES[p.phase] || p.phase || "");
  $("progress-text").textContent = parts.filter(Boolean).join(" · ");
  const bar = $("progress-bar");
  if (p.total) { bar.max = p.total; bar.value = p.done; } else bar.removeAttribute("value");   // indeterminate
}
const VIEWS = ["devices", "firmware", "logs", "settings"];
/** Switch tab and load its data. */
function showView(v) {
  ui.view = v; closeRowMenu();
  for (const k of VIEWS) { $("view-" + k).hidden = k !== v; $("tab-" + k).classList.toggle("active", k === v); $("tab-" + k).setAttribute("aria-selected", k === v); }
  if (v === "settings") { ui.options = null; renderSettings(); api("api/settings").then(o => { ui.options = o; renderSettings(); }).catch(e => message("Settings", e.message, "error")); }
  load(true);
}
for (const v of VIEWS) $("tab-" + v).onclick = () => showView(v);

let timer = 0, inflight = false;
export let queued = false;   // read by devices.js
const POLL_IDLE_MS = 30000, POLL_BUSY_MS = 3000;
/** Something runs: a check, an installation or another device activity. */
const isActive = s => !!(s && (s.busy || s.progress || s.devices.some(d => d.installing || d.activity)));
/** Work ended since the last state (it writes its log entry at the end) or a check
 *  finished in between (last check time changed): the Logs tab fetches the log at once. */
const workEnded = (before, now) => !!before && now !== before &&
  ((isActive(before) && !isActive(now)) || now.last_scan !== before.last_scan);
/** Next refresh: fast while something runs, slow when idle, none while the page is hidden. */
function schedule() {
  clearTimeout(timer);
  if (document.hidden) return;
  timer = setTimeout(load, isActive(ui.state) ? POLL_BUSY_MS : POLL_IDLE_MS);
}
// Hidden tab / HA panel in background: no requests; visible again: refresh at once
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); else clearTimeout(timer); });
/** Fetch the state (and the data of the active tab) and render. */
export async function load(force) {
  if (inflight) { queued = queued || force || "again"; return; }   // never two refreshes at once
  inflight = true;
  try {
    const s = await getState();
    if (ui.view === "firmware") { const f = await api("api/firmware"); ui.firmware = f.firmware; ui.firmwareTotal = f.total_size || 0; ui.firmwareFree = f.free_space ?? null; }
    if (ui.view === "logs" && (force || workEnded(ui.state, s) || Date.now() - ui.logsLoaded > 30000)) { ui.logs = (await api("api/logs")).entries; ui.logsLoaded = Date.now(); }
    ui.state = s;
    render(force);
  } catch (e) { $("busy").textContent = "Error: " + e.message; }
  finally {
    inflight = false;
    if (queued) { const f = queued === true; queued = false; load(f); } else schedule();
  }
}
loadPrefs(); buildChooser(); syncChooser();
load();
