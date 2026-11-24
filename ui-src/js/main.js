// Entry module: rendering, tabs and polling. Importing it loads all other modules.
import {
  $, api, attachTip, box, closeModal, confirmDialog, el, fmt, getState, guarded, hideTip, keepFocus, loadLocal, message, modal, saveLocal, setTip, t, ui,
} from "./core.js";
import {lang} from "./i18n.js";
import {buildChooser, closeRowMenu, loadPrefs, renderDevices, syncChooser} from "./devices.js";
import {renderFirmware} from "./firmware.js";
import {mobileQuery, renderLogs} from "./logs.js";
import {IMPORT_OWN, importDialog, renderSettings} from "./app-settings.js";
import "./install.js";
import {renderSchedules} from "./schedules.js";
import {renderBackups} from "./backups.js";
import "./device-settings.js";

// ---------------------------------------------------------------- Rendering, tabs & polling
/** Render the active view; unchanged data is not re-rendered unless forced. The keyboard
 *  focus stays on the control it was on (keepFocus). */
export function render(force) {
  const s = ui.state;
  if (!s) return;
  renderMeta(s);
  // The line under the page title shows errors only (storage, loading), never a status
  $("busy").textContent = s.storage_error ? t("Storage error: {error}", {error: s.storage_error}) : "";
  $("busy").classList.toggle("error", !!s.storage_error);
  renderProgress(s.progress);
  renderNotice(s.first_start_open ? {issue: "first_start"} : s.download_base || {});
  markUnreadLogs(s);
  // Blocked during a check, installations or a schedule run, and on a fresh installation
  // until the first-start question (import a firmware store?) is answered
  $("scan").disabled = !!s.busy || !!s.schedule_running || !!s.first_start_open;
  setTip($("scan"), s.first_start_open ? FIRST_START_BLOCK : s.schedule_running ? t("Schedule “{name}” is running", {name: s.schedule_running}) : "");
  if (s.first_start_open) askFirstStart();
  else if (loadLocal("firstStartAsked")) saveLocal("firstStartAsked", null);   // answered: a reinstallation asks again
  const key = JSON.stringify([ui.view, mobileQuery.matches, s,
    ui.view === "firmware" ? ui.firmware : ui.view === "logs" ? [ui.logs, ui.logsUnreadFrom] : ui.view === "backups" ? ui.backups : null]);
  if (!force && key === ui.rendered) return;
  ui.rendered = key; hideTip();
  keepFocus($("view-" + ui.view), () => {
    if (ui.view === "devices") renderDevices();
    else if (ui.view === "firmware") renderFirmware();
    else if (ui.view === "logs") renderLogs();
    else if (ui.view === "schedules") renderSchedules();
    else if (ui.view === "backups") renderBackups();
  });
}
/** Logs tab: a red "!" while the log holds an error entry newer than the newest entry
 *  this browser has shown in the Logs tab (per browser; on the first visit nothing counts
 *  as unread; warnings and successes never set it). Opening the Logs tab marks
 *  everything as read; the entries that were unread at that moment stay lightly
 *  highlighted until the tab is left (ui.logsUnreadFrom: the mark read before). The
 *  marks grow with every entry, also within one second (stored as "logsMark"). */
function markUnreadLogs(s) {
  const latest = s.log_latest || 0, stored = loadLocal("logsMark");
  let seen = typeof stored === "number" ? stored : latest;
  if (ui.view === "logs") {
    if (ui.logsUnreadFrom == null) ui.logsUnreadFrom = seen;
    seen = latest;
  } else ui.logsUnreadFrom = null;
  if (seen !== stored) saveLocal("logsMark", seen);
  const tab = $("tab-logs");
  let mark = tab.querySelector(".tab-unread");
  if (!mark) {
    mark = box("span", "tab-unread", el("span", "!"), el("span", t("Unread error entries in the log"), "sr-only"));
    mark.firstChild.setAttribute("aria-hidden", "true"); tab.append(mark);
  }
  mark.hidden = !((s.log_latest_error || 0) > seen);
}
/** Header: last check and check cycle as two neutral pills; the cycle uses the
 *  Home Assistant time zone (not the browser's). */
function renderMeta(s) {
  const sc = s.schedule;
  let next = "";
  try { next = new Date(sc.next * 1000).toLocaleString(lang === "de" ? "de-DE" : "en-GB", {timeZone: sc.timezone, dateStyle: "medium", timeStyle: "short"}); }
  catch (e) { next = fmt(sc.next); }
  const cycle = el("span", sc.days === 1 ? t("Every day · {time}", {time: sc.time}) : t("Every {days} days · {time}", {days: sc.days, time: sc.time}), "pill meta-pill");
  attachTip(cycle, () => t("Next automatic check: {next} ({timezone})", {next, timezone: sc.timezone}), true); cycle.tabIndex = 0;
  $("meta").replaceChildren(el("span", t("Last checked: {time}", {time: fmt(s.last_scan)}), "pill meta-pill"), cycle);
}
/** Notice while installations are disabled by the download base URL: not set, an IP
 *  address outside the private networks, or still the former example value that the
 *  admin has not confirmed yet (only that one has a confirm button). Rebuilt only
 *  when the problem changes, so polling does not steal the focus from its buttons. */
function renderNotice(info) {
  const n = $("notice"), key = info.issue ? `${info.issue}|${info.url}` : "";
  if (n.dataset.key === key) return;
  n.dataset.key = key; n.hidden = !info.issue;
  if (!info.issue) { n.replaceChildren(); return; }
  const buttons = el("div", null, "notice-btns");
  if (info.issue === "first_start") {
    const imp = el("button", t("Import store"), "primary"), skip = el("button", t("Skip"));
    imp.onclick = importDialog; skip.onclick = () => skipFirstStart();
    buttons.append(imp, skip);
    n.replaceChildren(el("span", t("First start: {text}", {text: FIRST_START})), buttons);
    return;
  }
  if (info.issue === "unconfirmed") {
    const yes = el("button", t("This address is correct"), "primary"); yes.onclick = () => confirmDownloadBase(info.url);
    buttons.append(yes);
  }
  const open = el("button", t("Open settings")); open.onclick = () => showView("settings");
  buttons.append(open);
  n.replaceChildren(el("span", info.message), buttons);
}
// Fresh installation (no stored state, also after a reinstallation): before the first
// check the app asks once whether a firmware store from another instance should be
// imported. The dialog opens once per browser; the notice bar stays until answered.
const FIRST_START = t("No check runs until you choose: import a firmware store from another Home Assistant instance, or skip it.");
const FIRST_START_BLOCK = t("Not before the first-start question is answered (import a firmware store or skip it)");
let firstStartAsked = false;
/** The welcome dialog, once. */
function askFirstStart() {
  if (firstStartAsked || loadLocal("firstStartAsked")) return;
  firstStartAsked = true; saveLocal("firstStartAsked", true);
  modal(WELCOME_TITLE, [
    el("p", t("Moving from another Home Assistant instance? Import its exported firmware store first: the stored versions (also older ones Shelly no longer offers) and uploads are then available at once.")),
    el("p", FIRST_START, "hint"), el("div", t("You can also import later: Settings → Firmware & Storage."), "hint"),
    el("div", IMPORT_OWN, "warnbox")],
  [{text: t("Skip"), action: () => skipFirstStart(WELCOME_TITLE)}, {text: t("Import store"), kind: "primary", action: importDialog}], "importIcon");
}
const WELCOME_TITLE = t("Welcome – import a firmware store?");
/** Answer the first-start question with "Skip": checks may run from now on. An error
 *  keeps the title of the welcome dialog when it was skipped there (title). */
async function skipFirstStart(title) {
  await guarded(title || t("First start"), title ? "importIcon" : undefined, async () => { await api("api/first-start/skip", {}); closeModal(); });
}
/** Confirm that the former example URL really is the address of Home Assistant. */
function confirmDownloadBase(url) {
  confirmDialog({title: t("Confirm download base URL"), button: t("Yes, this is correct"), kind: "primary",
    content: [el("p", t("Is {url} really the LAN address of Home Assistant with the host port 8098?", {url})),
      el("p", t("The Shellys download the firmware from this address. If it is wrong, every update fails. The confirmation ends when the address is changed."), "hint")],
    action: async () => { await api("api/settings/download-base/confirm", {url}); closeModal(); }});
}
const PHASES = {queued: t("starting"), registry: t("reading the device list"), devices: t("checking devices"),
  download: t("downloading firmware"), changelog: t("checking firmware information")};
/** Global progress line (and bar) of a running check. */
function renderProgress(p) {
  const box_ = $("progress");
  box_.hidden = !p;
  if (!p) return;
  const parts = [t("Checking devices")];
  if (p.total) parts.push(t("{done}/{total} completed", {done: p.done, total: p.total}));
  if (p.errors) parts.push(t("{n} unavailable", {n: p.errors}));
  parts.push(PHASES[p.phase] || p.phase || "");
  $("progress-text").textContent = parts.filter(Boolean).join(" · ");
  const bar = $("progress-bar");
  if (p.total) { bar.max = p.total; bar.value = p.done; } else bar.removeAttribute("value");   // indeterminate
}
const VIEWS = ["devices", "firmware", "schedules", "backups", "logs", "settings"];
/** Switch tab and load its data. */
export function showView(v) {
  ui.view = v; closeRowMenu();
  for (const k of VIEWS) { $("view-" + k).hidden = k !== v; $("tab-" + k).classList.toggle("active", k === v); $("tab-" + k).setAttribute("aria-selected", k === v); }
  // "Check now" belongs to the device data, not to the settings: hidden there, its place
  // is kept so the header does not move when the tab changes
  $("scan").classList.toggle("off-view", v === "settings");
  if (v === "settings") { ui.options = null; renderSettings(); api("api/settings").then(o => { ui.options = o; renderSettings(); }).catch(e => message(t("Settings"), e.message, "error")); }
  load(true);
}
for (const v of VIEWS) $("tab-" + v).onclick = () => showView(v);

let timer = 0, inflight = false;
// Home Assistant renews an expired Ingress session when the page becomes visible again –
// asynchronously, so the first refreshes after a long time in the background can still be
// refused with 401. They are repeated silently after these waits; only a refusal that
// lasts longer is shown.
const AUTH_RETRY_MS = [1000, 3000, 8000, 15000];
let authRetry = 0;
let queued = false;   // a refresh asked for while one runs: true (forced) or "again"
const POLL_IDLE_MS = 30000, POLL_BUSY_MS = 3000;
// A schedule run or scheduled installation starts at its time: the page refreshes this
// long after it, so the Devices, Schedules and Logs tabs show it at once
const DUE_DELAY_MS = 2000;
let duePolled = 0;   // time of the last job the page refreshed for (epoch s)
/** Something runs: a check, an installation, a schedule run, a backup run, a restore or
 *  another device activity. */
const isActive = s => !!(s && (s.busy || s.progress || s.schedule_running || (s.backup_job && !s.backup_job.finished) ||
  (s.restore && !s.restore.finished) || s.devices.some(d => d.installing || d.activity)));
/** Time of the next schedule run or scheduled installation the page has not refreshed
 *  for yet (epoch s; Infinity: none). */
function nextDue(s) {
  const times = [...(s?.schedules || []).filter(x => x.on && x.next).map(x => x.next),
    ...(s?.devices || []).filter(d => d.planned).map(d => d.planned.at)];
  return Math.min(Infinity, ...times.filter(x => x > duePolled));
}
/** Work ended since the last state (it writes its log entry at the end) or a check
 *  finished in between (last check time changed): the Logs tab fetches the log at once. */
const workEnded = (before, now) => !!before && now !== before &&
  ((isActive(before) && !isActive(now)) || now.last_scan !== before.last_scan);
/** Next refresh: fast while something runs, slow when idle, shortly after the time of the
 *  next job, none while the page is hidden. */
function schedule() {
  clearTimeout(timer);
  if (document.hidden) return;
  const wait = isActive(ui.state) ? POLL_BUSY_MS : POLL_IDLE_MS, due = nextDue(ui.state);
  const untilDue = due * 1000 + DUE_DELAY_MS - Date.now();
  if (untilDue < wait) timer = setTimeout(() => { duePolled = due; load(); }, Math.max(0, untilDue));
  else timer = setTimeout(load, wait);
}
// Hidden tab / HA panel in background: no requests; visible again: refresh at once
document.addEventListener("visibilitychange", () => { if (!document.hidden) { authRetry = 0; load(); } else clearTimeout(timer); });
/** Fetch the state (and the data of the active tab) and render. */
export async function load(force) {
  if (inflight) { queued = queued || force || "again"; return; }   // never two refreshes at once
  inflight = true;
  let retry = 0;   // wait before a silent repetition after a refused session (401)
  try {
    const s = await getState();
    if (ui.view === "firmware") { const f = await api("api/firmware"); ui.firmware = f.firmware; ui.firmwareTotal = f.total_size || 0; ui.firmwareFree = f.free_space ?? null; }
    // Logs tab: reloaded when a new entry was written (newest entry time) or work ended
    if (ui.view === "logs" && (force || workEnded(ui.state, s) || s.log_latest !== ui.state?.log_latest || Date.now() - ui.logsLoaded > 30000)) { ui.logs = (await api("api/logs")).entries; ui.logsLoaded = Date.now(); }
    // Backups tab: its lists are fetched again whenever the state changed (a backup was
    // stored or removed, an attempt failed, a device was renamed)
    if (ui.view === "backups" && (force || !ui.backups || s !== ui.state)) ui.backups = await api("api/backups");
    ui.state = s;
    authRetry = 0;
    render(force);
  } catch (e) {
    if (e.status === 401 && authRetry < AUTH_RETRY_MS.length) retry = AUTH_RETRY_MS[authRetry++];
    else $("busy").textContent = e.status === 401 ? t("Session expired – reload the page") : t("Error: {error}", {error: e.message});
  }
  finally {
    inflight = false;
    if (queued) { const f = queued === true; queued = false; load(f); }
    else if (retry && !document.hidden) { clearTimeout(timer); timer = setTimeout(() => load(force), retry); }
    else schedule();
  }
}
loadPrefs(); buildChooser(); syncChooser();
load();
