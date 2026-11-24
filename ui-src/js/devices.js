// Devices tab: status and action icons, the device table / phone tiles and the row menu.
import {
  $, api, attachTip, box, closeModal, collator, confirmDialog, devUrl, el, filterControl, fmt, hideTip, icon, iconButton, input, loadLocal,
  matches, message, modal, modelGen, openDetails, pad, saveLocal, searchText, searchTool, section, selected, setTip, sortControl, t, toast, toolButton,
  ui, valueList, webUrl,
} from "./core.js";
import {OFFER_SOURCES, cancelPlan, daysAgo, openDowngrade, openInstall} from "./install.js";
import {
  aopLabel, aopSummary, aopTone, editAop, editCloud, editDebug, editEco, editPassword, editSwitch, editValue, featureStrip,
  restartDevice,
} from "./device-settings.js";
import {load, render} from "./main.js";
import {openDeviceBackups, reasonLabel} from "./backups.js";
import {afterUpdateNote, gettingStartedCard, openComparison, openReport, unreachableBlock} from "./diagnosis.js";
import {SECURITY_ITEMS, anyArea, areaFilterNode, areaMatch, saveCsv, securityStrip, syncAreaFilter} from "./overview.js";

// ---------------------------------------------------------------- Devices
// Never checked (status of older versions "unregistered") and Gen1 devices skipped while
// Gen1 updates are disabled are both shown as "Not checked"
const STATUS_LABELS = {"up-to-date": t("Up-To-Date"), available: t("Available"), ready: t("Ready"), queued: t("Queued"),
  installing: t("Installing"), unconfirmed: t("Unconfirmed"), error: t("Error"), unregistered: t("Not checked"), skipped: t("Not checked"),
  updated: t("Updated"), asleep: t("Asleep"), scheduled: t("Scheduled")};
// Battery device (detected from the device while it was awake) that did not answer the last check
const asleep = d => d.status === "asleep";
// A device with a confirmed or unconfirmed installation stays in the "Updates
// available" card (without checkbox) until the next full check or a single check of
// this device; a confirmed one is shown as "Updated", an unconfirmed one keeps its status
const installedRecently = d => !!d.updated && ["up-to-date", "unconfirmed"].includes(d.status);
// A failed installation also stays there until that check – with checkbox and
// "Install" for a retry (its file stays provided); so does a stored version offered
// for a manual installation while the Shelly update server could not be asked
const failedInstall = d => d.status === "error" && !!(d.install_failed || d.store_fallback);
const justUpdated = d => installedRecently(d) && d.status === "up-to-date";
// An approved installation waiting for its time ("Install at") shows "Scheduled" in place of "Ready"
const shownStatus = d => d.planned && d.status === "ready" && !d.installing ? "scheduled" :
  d.installing && d.status === "ready" ? "queued" : justUpdated(d) ? "updated" : d.status;
// A running downgrade is no offered update: the device stays in its section (the updates
// card too, see listedUpdate), the row shows "installed → target" with the tag "Downgrade"
const downgrading = d => !!d.downgrade && d.status === "installing";
const hasUpdate = d => !downgrading(d) && ["available", "ready", "installing"].includes(d.status);
// Ignored devices are not installable until they are resumed; nothing is installable
// while the download base URL is missing or the unconfirmed former example; a scheduled
// installation is started from the row menu ("Install now") only
export const installable = (d, s) => !d.planned && (d.status === "ready" || failedInstall(d)) && !d.installing && !d.ignored && !s.scanning && !d.activity &&
  !(s.download_base && s.download_base.issue);
/** Why "Install" is not possible for a device right now (tooltip of the disabled button). */
export function installBlocker(d, s) {
  if (!d) return t("Device is not known anymore");
  if (d.installing || d.status === "installing") return t("Installation is running");
  if (s.scanning) return t("Check in progress");
  if (d.activity) return t("Device is being checked");
  if (d.ignored) return t("Device is ignored");
  if (s.download_base && s.download_base.issue) return s.download_base.message || t("Download base URL is not set");
  return d.status === "ready" || failedInstall(d) ? null : t("Not provided for this device anymore");
}
// A downgrade needs a reached Gen2+ device, no check/installation, no scheduled
// installation and a valid download URL
const downgradable = (d, s) => d.gen >= 2 && !!d.ip && !d.planned && !d.installing && d.status !== "installing" && !s.scanning &&
  d.activity !== "reloading" && !(s.download_base && s.download_base.issue);
// Settings can be read/changed once the device was reached and while it is not updating or reloading
const editable = d => !!d.ip && !!d.gen && !d.installing && d.status !== "installing" && d.activity !== "reloading";

// ---- Sections and filters
// Sections of the device list in their order; the server names the section of every
// device (field "section"), the read-only card "Other Shellys" follows them
const SECTIONS = [["updates", t("Updates Available")], ["beta", t("Beta Updates")], ["attention", t("Attention")], ["not_checked", t("Not Checked")],
  ["up_to_date", t("Up-To-Date")], ["asleep", t("Asleep")], ["ignored", t("Ignored")], ["removed", t("Removed")]];
// Devices shown in "Updates available" at the last render (kept there while checked)
const shownUpdates = new Set();
// A device listed in the updates card stays there while it is checked again (its markers
// are reset by the check) or downgraded; the result decides at the next render without activity
const listedUpdate = d => hasUpdate(d) || shownStatus(d) === "queued" || installedRecently(d) || failedInstall(d) ||
  ((!!d.activity || downgrading(d)) && shownUpdates.has(d.id));
/** Section of a device: removed and ignored by their marker, then the updates card – or
 *  the beta card for a beta version – (also while checked, see listedUpdate), else the
 *  section named by the server. */
function sectionOf(d) {
  if (d.removed) return "removed";
  if (d.ignored) return "ignored";
  if (["updates", "beta"].includes(d.section) || listedUpdate(d)) return d.beta ? "beta" : "updates";
  return SECTIONS.some(([key]) => key === d.section) ? d.section : "not_checked";
}
// Filter entries (popover of the toolbar) in the order of the sections; "Unprotected":
// devices without password protection set on the device (filter only, the user acts per
// device in the filtered list; an unknown state does not count); "Other Shellys": only
// the read-only card of the Shellys of other integrations
const inSection = key => d => sectionOf(d) === key;
// Option "Offer updates only after (days)": a new version younger than that is held back,
// the device stays up to date with the marker hold {version, built, until} (ISO dates)
const held = d => !!(d.hold && d.hold.version);
const holdTip = d => t("{version} was built on {built} ({age}). New versions are offered after {days} days (Settings → Checks): from {until}.",
  {version: d.hold.version, built: d.hold.built, age: daysAgo(d.hold.built), days: ui.state.offer_after_days, until: d.hold.until});
const FILTERS = [
  ["all", t("All"), () => true],
  ["updates", t("Updates"), inSection("updates")],
  ["beta", t("Beta updates"), inSection("beta"), t("Devices with a beta version on offer (Settings → “Offer beta firmware”)")],
  ["planned", t("Scheduled"), d => !!d.planned, t("Devices with an approved installation waiting for its time")],
  ["attention", t("Attention"), inSection("attention"), t("Devices with an error, an unconfirmed installation or no usable address")],
  ["not_checked", t("Not checked"), inSection("not_checked"), t("Devices never checked, and Gen1 devices while Gen1 updates are disabled")],
  ["up_to_date", t("Up-To-Date"), inSection("up_to_date")],
  ["unprotected", t("Unprotected"), d => d.auth_enabled === false],
  ["cloud", t("Cloud on"), d => d.cloud === true, t("Devices with the Shelly cloud switched on")],
  // Access point and websocket debug log on: also set by the strip "Security"
  ...SECURITY_ITEMS.slice(2).map(([key, label, , , fn, tip]) => [key, label, fn, tip]),
  ["hold", t("On hold"), held, t("Devices with a new version that is not offered yet (Settings → Checks → “Offer updates only after”)")],
  ["asleep", t("Asleep"), inSection("asleep"), t("Battery devices that did not answer the last check")],
  ["ignored", t("Ignored"), inSection("ignored")],
  ["removed", t("Removed"), inSection("removed"), t("Devices removed from Home Assistant / the Shelly integration")],
  ["others", t("Other Shellys"), () => false, t("Shellys of other integrations (Bluetooth, Zigbee, MQTT, Matter) – display only")],
];
const filterFn = key => (FILTERS.find(f => f[0] === key) || FILTERS[0])[2];
const devFilter = filterControl(FILTERS.map(([key, label, , tip]) => [key, label, tip ? () => tip : null]),
  key => { ui.devFilter = key; render(true); });
$("dev-filter").replaceWith(devFilter.node);
devFilter.node.after(areaFilterNode);   // area filter tool, shown while a device has an area

// Running check of a device (server-side state, in memory only)
const ACTIVITY_LABELS = {waiting: t("Waiting"), checking: t("Checking …"), downloading: t("Downloading …"), reloading: t("Reloading …")};
// The last successful check differs from the last attempt only after a failed attempt
const lastSuccess = d => d.last_success && d.last_success !== d.last_check ? fmt(d.last_success) : null;

// Tooltip of the "Asleep" pill: the wake-up hint and the last successful contact only
const ASLEEP_HINT = t("Battery device asleep – wake it up (e.g. press its button) and check it again.");
/** Text of the status tooltip (details, versions, last attempt and last success). */
function statusText(d) {
  if (asleep(d)) return `${ASLEEP_HINT}\n` + t("Last contact: {time}", {time: fmt(d.last_success)});
  return [d.message, t("Installed: {version}", {version: d.version || "–"}), d.offer_version ? t("Available: {version}", {version: d.offer_version}) : null,
    d.planned ? t("Install at {time}", {time: d.planned.at_text}) : null,
    t("Last check: {time}", {time: fmt(d.last_check)}), lastSuccess(d) && t("Last successful check: {time}", {time: lastSuccess(d)})].filter(Boolean).join("\n");
}
/** Section "Backups" of the device details: the newest backup, how many are stored
 *  ("n of N", the kept ones do not count) and the button to the device's backups. */
function backupsSection(d) {
  const b = d.backups, show = el("button", t("Show backups"), "small"); show.onclick = () => openDeviceBackups(d.id);
  return section(t("Backups"), valueList([[t("Last backup"), b ? `${fmt(b.last)} · ${reasonLabel(b.reason)}` : t("never")],
    [t("Stored"), b ? t("{n} of {keep}", {n: b.count - b.kept, keep: ui.state.backup_keep}) + (b.kept ? " " + t("+ {n} kept", {n: b.kept}) : "") : null]]),
  box("div", "bk-more", show));
}
/** "Device details" popup: message left, status right on one line (a device that did not
 *  answer: "Not reachable", the causes and "Try again" below), then the sections Firmware,
 *  Device (model, generation, firmware ID, NTP), History and Backups; "Details for a bug
 *  ticket" bottom left. */
function showStatus(d) {
  const key = shownStatus(d), bad = ["error", "unconfirmed"].includes(d.status), unreachable = !!d.unreachable && !asleep(d);
  const head = box("div", "dstatus", el("p", unreachable ? t("Not reachable") : d.message || "", "dmsg" + (bad ? " bad" : "")),
    el("span", STATUS_LABELS[key] || d.status, "pill p-" + (key || "").replace(/[^a-z]/g, "")));
  const provided = hasUpdate(d) || failedInstall(d);
  modal(d.name, [head, unreachable ? unreachableBlock(d) : null,
    section(t("Firmware"), valueList([[t("Installed"), d.version],
      [t("Available"), held(d) ? t("{version} · on hold until {until}", {version: d.hold.version, until: d.hold.until}) :
        d.offer_version && d.beta ? t("{version} (beta)", {version: d.offer_version}) : d.offer_version],
      [t("Scheduled"), d.planned ? t("Install at {time} · approved {on} by {user}", {time: d.planned.at_text, on: d.planned.on_text, user: d.planned.by}) : null],
      [t("Build date"), held(d) ? d.hold.built : d.offer_version ? d.offer_date : null],
      [t("Previous version"), d.way_back ? (d.way_back.stored ? t("{version} is stored", {version: d.way_back.version}) : t("{version} is not stored", {version: d.way_back.version})) : null],
      [t("Source"), provided ? OFFER_SOURCES[d.offer_source] : null]])),
    section(t("Device"), valueList([[t("Model"), modelGen(d)], [t("Firmware ID"), d.type], ["NTP", d.ntp]])),
    section(t("History"), valueList([[t("Last check"), fmt(d.last_check)], [t("Last success"), fmt(d.last_success)],
      [t("Last update"), fmt(d.last_update)]])),
    backupsSection(d)].filter(Boolean),
    // Ready (and installable): red button right of Close opens the existing install dialog
    [{text: t("Details for a bug ticket"), action: () => openReport(d)}, {text: t("Close")},
      ...(installable(d, ui.state) ? [{text: t("Install update"), kind: "danger", action: () => openInstall([d])}] : [])]);
  $("dlg-btns").firstChild.classList.add("dlg-left");
}
const AOP_NOTE = t("Power-on action: not available – this device has no switchable output.");
/** Popup of a greyed-out action icon: status strip with the reason as pill, explanation below. */
function notePopup(iconName, label, pill, text) {
  modal(label, [featureStrip(iconName, label, "off", pill), el("p", text, "hint")]);
}
const AOP_UNKNOWN = t("Power-on action: unknown – the outputs of this device have not been read yet.");
/** Power-on action (AOP) in the action column: flash icon with state – green
 *  if every output restores its last state, red if an output has another value, "?"
 *  if the settings could not be read or the device was never read (outputs unknown);
 *  opens the same dialog as the row menu. Mobile (labeled): the current value is shown
 *  on the button next to the icon. Devices without switchable outputs get a disabled
 *  icon with an explanation. */
function aopButton(d, labeled) {
  const outputs = d.outputs || [];
  if (d.outputs == null) {   // never read: unknown like the other icons, not "not available"
    const b = el("button", null, "act s-unknown"); b.append(icon("aop"));
    if (labeled) b.append(el("span", t("Power-on action")));
    b.setAttribute("aria-label", AOP_UNKNOWN); attachTip(b, () => AOP_UNKNOWN);
    b.setAttribute("aria-disabled", "true"); b.onclick = () => notePopup("aop", t("Power-on action"), t("Unknown"), AOP_UNKNOWN);
    return b;
  }
  const state = !outputs.length ? "restart" : d.settings_error ? "s-unknown" : aopTone(outputs) === "good" ? "c-good" : "c-alert";
  const b = el("button", null, `act ${state}`);
  b.append(icon("aop"));
  if (!outputs.length) {
    if (labeled) b.append(el("span", t("Power-on action")));
    b.setAttribute("aria-label", AOP_NOTE); attachTip(b, () => AOP_NOTE);
    b.setAttribute("aria-disabled", "true"); b.onclick = () => notePopup("aop", t("Power-on action"), t("Not available"), AOP_NOTE);
    return b;
  }
  if (labeled) b.append(el("span", t("Power-on: {summary}", {summary: aopSummary(outputs)})));
  const text = t("Power-on action – {detail}", {detail: d.settings_error ? t("unknown – {error}", {error: d.settings_error.message}) :
    outputs.map(o => `${o.name || o.key}: ${aopLabel(o.aop)}`).join(", ")});
  b.setAttribute("aria-label", `${text} (${d.name})`); attachTip(b, () => text);
  b.disabled = !editable(d); b.onclick = () => editAop(d);
  return b;
}

// Action column: one icon per feature. Values from the server: true/false,
// "unsupported" (device/firmware lacks it) or null/missing (not known yet or
// the last read failed) – unknown is shown distinctly ("?"), never as "off".
const UNSUPPORTED = "unsupported";
const GEN1_AP_NOTE = t("Not available on Gen1: enabling the access point turns off the Wi-Fi client mode, the device would leave your network.");
const GEN1_DEBUG_NOTE = t("Gen1 devices have no websocket debug log.");
const ACTION_ITEMS = [
  {key: "eco", icon: "eco", label: t("Eco mode"), on: t("on"), off: t("off"), edit: d => editEco(d)},
  {key: "auth_enabled", icon: "lock", label: t("Password protection"), on: t("protected"), off: t("not protected"), edit: d => editPassword(d), always: true},
  {key: "ap", icon: "ap", label: t("Access point"), on: t("on"), off: t("off"), alert: true, edit: d => editSwitch(d, "ap")},
  {key: "ble", icon: "ble", label: t("Bluetooth"), on: t("on"), off: t("off"), edit: d => editSwitch(d, "ble")},
  // Shelly cloud: yellow while on (the device is reachable from outside the network)
  {key: "cloud", icon: "cloud", label: t("Cloud"), on: t("on"), off: t("off"), warn: true, edit: d => editCloud(d)},
  {key: "actions", icon: "actions", label: t("Actions"), on: t("at least one active"), off: t("none active"), alert: true, link: true},
  {key: "schedules", icon: "schedules", label: t("Device schedules"), on: t("at least one active"), off: t("none active"), alert: true, link: true},
  // Debug output costs resources and may expose details: yellow while on
  {key: "debug_ws", icon: "bug", label: t("Websocket debug log"), on: t("on"), off: t("off"), warn: true, edit: d => editDebug(d)},
];
/** State of one feature: "on" | "off" | "unknown" | "unsupported". */
function featureState(d, key) {
  const v = d[key];
  if (v === UNSUPPORTED || (key === "debug_ws" && d.gen === 1)) return "unsupported";
  if (typeof v !== "boolean" || (d.settings_error && key !== "auth_enabled")) return "unknown";
  return v ? "on" : "off";
}
/** One action icon (button or link) with accessible name and tooltip;
 *  labeled = mobile, the text is shown next to the icon. */
function actionButton(d, item, labeled) {
  const state = featureState(d, item.key), gen1Ap = item.key === "ap" && d.gen === 1;
  let text = `${item.label}: ${state === "on" ? item.on : state === "off" ? item.off : state === "unsupported" ? t("not supported by this device") : t("unknown")}`;
  if (state === "unknown" && d.settings_error) text += ` – ${d.settings_error.message}`;
  if (gen1Ap) text += ` – ${GEN1_AP_NOTE}`;
  if (item.key === "debug_ws" && d.gen === 1) text += ` – ${GEN1_DEBUG_NOTE}`;
  // Protected without a password stored in this app = warning (the app cannot
  // read or update the device); green only if protection is on and the password is stored
  const unstored = item.key === "auth_enabled" && state === "on" && !d.password_set;
  if (unstored) text += " – " + t("password not stored in this app");
  if (item.key === "cloud" && state === "on") text += " – " + (d.cloud_connected ? t("connected to the Shelly cloud") : t("not connected at the moment"));
  if (item.key === "ble" && state === "on" && d.ble_proxy) text += " – " + t("used by Home Assistant as Bluetooth proxy");
  const url = item.link ? webUrl(d) : null;
  const node = item.link && url && state !== "unsupported" ? el("a") : el("button");
  node.className = `act s-${state}${item.alert ? " alert" : ""}${unstored || (item.warn && state === "on") ? " c-warn" : ""}`;
  node.setAttribute("aria-label", text); attachTip(node, () => text);
  node.append(icon(item.icon)); if (labeled) node.append(el("span", item.label));
  if (node.tagName === "A") {   // opens the device web UI (root, no route probing)
    node.href = url; node.target = "_blank"; node.rel = "noopener noreferrer";
  } else if (state === "unsupported" || gen1Ap || (item.link && !url)) {
    // aria-disabled keeps hover/focus/tap for the explanation
    node.setAttribute("aria-disabled", "true");
    const pill = state === "unsupported" ? t("Not supported") : t("Not available");
    node.onclick = () => notePopup(item.icon, item.label, pill, text);
  } else {
    node.disabled = !(item.always ? !!d.ip && !!d.gen : editable(d)); node.onclick = () => item.edit(d);
  }
  return node;
}
/** Icon strip of the action column (and labeled buttons in the mobile details). */
function actionStrip(d, labeled) {
  const strip = box("div", labeled ? "act-strip labeled" : "act-strip", aopButton(d, labeled), ...ACTION_ITEMS.map(item => actionButton(d, item, labeled)));
  const r = el("button", null, "act restart"); r.append(icon("restart")); if (labeled) r.append(el("span", t("Restart")));
  r.setAttribute("aria-label", t("Restart {name}", {name: d.name})); attachTip(r, () => t("Restart")); r.disabled = !editable(d); r.onclick = () => restartDevice(d);
  strip.append(r);
  return strip;
}
/** Ignored devices: additional yellow marker next to the status, no function. */
const ignoredPill = d => d.ignored ? el("span", t("Ignored"), "pill p-ignored") : null;
// Tooltip of the "Removed" marker: still reachable at the last address or not (unknown
// until the device was tried there)
const REMOVED_TIPS = {
  true: t("Removed from Home Assistant / the Shelly integration, but still reachable at its last address. All actions stay available; “Check now” only tests whether it still answers."),
  false: t("Removed from Home Assistant / the Shelly integration and no longer reachable. “Check now” only tests whether it answers again."),
  null: t("Removed from Home Assistant / the Shelly integration. “Check now” only tests whether it still answers.")};
/** Removed devices: yellow marker next to the status (like "Ignored"), tooltip also on touch. */
function removedPill(d) {
  if (!d.removed) return null;
  const p = el("span", t("Removed"), "pill p-removed"); p.tabIndex = 0;
  attachTip(p, () => REMOVED_TIPS[d.removed_reachable === true ? "true" : d.removed_reachable === false ? "false" : "null"], true);
  return p;
}
// Battery marker: where the battery device was recognised
const BATTERY_SOURCES = {device: t("Detected from the device"), home_assistant: t("Detected from Home Assistant")};
// Device without usable address in the device registry: message in the IP column, the
// reason below the Shelly name (wide screens), the server's text as tooltip
const NO_ADDRESS = t("No usable address");
const ADDRESS_REASONS = {missing: t("No address in Home Assistant"), unresolved: t("Host name does not resolve"),
  not_private: t("Address not allowed (public IP)")};
const addressReason = d => ADDRESS_REASONS[d.address_issue.reason] || d.address_issue.message || NO_ADDRESS;
/** Status cell: while a check runs the activity replaces the (then outdated) status. */
function statusCell(d) {
  if (d.activity) {
    const chip = el("span", null, "pill p-activity"); chip.setAttribute("role", "status");
    if (d.activity !== "waiting") chip.append(el("span", null, "spinner"));
    chip.append(ACTIVITY_LABELS[d.activity] || d.activity);
    return chip;
  }
  const key = shownStatus(d);
  // Gen2+ installations report the device's own download progress, then its restart
  const restarting = key === "installing" && d.ota_phase === "restarting";
  const name = key === "installing" && downgrading(d) ? t("Downgrading") : STATUS_LABELS[key] || key || "–";
  const label = restarting ? t("Restarting") : name + (key === "installing" && typeof d.ota_progress === "number" ? ` ${d.ota_progress} %` : "");
  const p = el("button", label, "pill p-" + (key || "").replace(/[^a-z]/g, ""));
  p.onclick = () => showStatus(d); attachTip(p, () => statusText(d));
  return p;
}
/** "Install" next to the status of a device with a provided update ("Ready").
 *  Opens the usual installation dialog; disabled with the reason while it is not
 *  possible (same conditions as "Install update"). Not shown while a check runs for
 *  the device or its installation is queued. */
function installButton(d) {
  if (!(d.status === "ready" || failedInstall(d)) || d.activity || d.installing || d.planned) return null;
  const blocker = installBlocker(d, ui.state), go = el("button", t("Install"), "danger small install");
  go.disabled = !!blocker;
  setTip(go, blocker || (d.store_fallback ? t("Install the stored version (not verified by the Shelly update server)") : t("Install the provided update")));
  go.setAttribute("aria-label", t("Install update on {name}", {name: d.name}));
  go.onclick = () => { if (installable(d, ui.state)) openInstall([d]); };
  return go;
}
/** Mobile only: expandable details with the controls hidden from the compact card.
 *  The content is built only while open (keeps the DOM small on wide screens and
 *  for closed cards); the open state survives re-rendering. */
function detailsCell(d) {
  const key = "dev:" + d.id, det = el("details");
  const fill = () => {
    const controls = box("div", "detail-actions", actionStrip(d, true));
    const list = valueList([...(anyArea(ui.state.devices) ? [[t("Area"), d.area || "–"]] : []), [t("Model"), modelGen(d)], [t("Firmware ID"), d.type],
      ...(ui.state.host_names ? [[t("Host name"), d.host_name]] : []), ["NTP", d.ntp],
      [t("Last check"), fmt(d.last_check)], [t("Last success"), fmt(d.last_success)],
      [t("Last update"), fmt(d.last_update)]], "tile-kv");
    det.replaceChildren(det.firstChild, box("div", "detail-body", controls, list));
  };
  det.append(el("summary", t("Details")));
  det.open = openDetails.has(key);
  if (det.open) fill();
  det.ontoggle = () => {
    if (det.open) { openDetails.add(key); if (det.childNodes.length < 2) fill(); } else openDetails.delete(key);
  };
  return det;
}

// Gen1 version strings ("20230913-112003/v1.14.0-gcb84623") are shortened to the
// version number ("1.14.0") in the Firmware column only; Gen2+ stays unchanged
// (there the part after "-" marks a pre-release, e.g. "1.5.0-beta2").
const GEN1_VERSION_RE = /\/v?(\d+\.\d+\.\d+)/;
const shortVersion = (d, v) => { const m = d.gen === 1 && GEN1_VERSION_RE.exec(v || ""); return m ? m[1] : v; };

// Each column: label, cell renderer (returns nodes), sort value, fixed = always shown
const COLUMNS = {
  select: {label: "", fixed: true, cell: d => {
    if (installedRecently(d)) {   // no selection, but the row keeps the indentation of a checkbox
      const gap = input("checkbox", {disabled: true, className: "ghost", tabIndex: -1}); gap.setAttribute("aria-hidden", "true");
      return [gap];
    }
    if (!installable(d, ui.state)) return [];
    const cb = input("checkbox", {checked: selected.has(d.id)}); cb.setAttribute("aria-label", t("Select {name}", {name: d.name}));
    cb.onchange = () => { cb.checked ? selected.add(d.id) : selected.delete(d.id); render(true); };
    return [cb];
  }},
  device: {label: t("Device"), sort: d => d.name, cell: d => {
    // Shelly name + pencil: opens the existing Shelly-name dialog (no HA rename)
    const pen = iconButton("pencil", t("Edit Shelly name of {name}", {name: d.name}), () => editValue(d, "name"), "pen"); pen.disabled = !editable(d);
    // Tooltips name both names (mouse hover, touch tap)
    const ha = el("div", d.name, "name"), shellyName = el("span", d.shelly_name || "–");
    attachTip(ha, () => t("Home Assistant device name: {name}", {name: d.name}), true);
    attachTip(shellyName, () => t("Shelly device name: {name}", {name: d.shelly_name || "–"}), true);
    // Battery icon right after the name (table and phone tiles)
    let battery = null;
    if (d.battery === true) {
      battery = box("span", "battery", icon("battery")); battery.setAttribute("role", "img");
      battery.setAttribute("aria-label", t("Battery device"));
      attachTip(battery, () => t("Battery device: reachable only while it is awake") + "\n" + (BATTERY_SOURCES[d.battery_source] || BATTERY_SOURCES.device), true);
    }
    // "Gen n" label beside the name: phone tiles only (the table has its own Gen column)
    const head = box("div", "name-line", ha, battery, d.gen ? el("span", `Gen ${d.gen}`, "tag") : null);
    // No usable address: the reason below the Shelly name (wide screens only)
    const reason = d.address_issue ? box("div", "sub addr-reason", icon("warn"), el("span", addressReason(d))) : null;
    return [head, box("div", "sub shelly-name", shellyName, pen), reason].filter(Boolean);
  }},
  // Area of Home Assistant: offered while a device has one (phones: first row of the details)
  area: {label: t("Area"), sort: d => d.area, cell: d => [el("span", d.area || "–", d.area ? "" : "sub")]},
  ip: {label: "IP", sort: d => (d.ip || "").split(".").map(n => n.padStart(3, "0")).join("."), cell: d => {
    if (d.address_issue) {   // instead of the (outdated) IP: the message, details as tooltip
      const issue = box("span", "addr-issue", icon("warn"), el("span", NO_ADDRESS)); issue.tabIndex = 0;
      attachTip(issue, () => `${NO_ADDRESS}: ${d.address_issue.message || addressReason(d)}`, true);
      return [issue];
    }
    if (!d.ip) return ["–"];
    // Dimmed while the battery device sleeps (its web UI does not answer then)
    const cls = "num" + (asleep(d) ? " dim" : "");
    const url = webUrl(d); if (!url) return [el("span", d.ip, cls)];
    const a = el("a", d.ip, cls); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; setTip(a, t("Open the Shelly web UI"));
    return [a];
  }},
  gen: {label: "Gen", sort: d => d.gen, cell: d => [d.gen ?? "–"]},
  firmware: {label: t("Firmware"), sort: d => d.type, cell: d => {
    const offer = d.offer_version && hasUpdate(d), down = downgrading(d) && d.target;
    const line = box("div", "nowrap", el("span", shortVersion(d, d.version) || "–"));
    if (offer) line.append(el("span", "→", "arrow"), el("span", shortVersion(d, d.offer_version), "newver"));
    else if (down) line.append(el("span", "→", "arrow"), el("span", d.target, "newver"));
    // Gen1: the full version strings stay available as tooltip (and in the status details)
    if (d.gen === 1 && (d.version || offer)) {
      const full = [t("Installed: {version}", {version: d.version || "–"}), offer ? t("Available: {version}", {version: d.offer_version}) : null].filter(Boolean).join(" · ");
      line.tabIndex = 0; attachTip(line, () => full, true);
    }
    // Held back (offer delay): clock with the held version and the date it is offered from
    if (held(d)) {
      const note = box("div", "hold-note", icon("clock"), el("span", t("{version} from {date}", {version: shortVersion(d, d.hold.version), date: d.hold.until}))); note.tabIndex = 0;
      attachTip(note, () => holdTip(d), true);
      return [line, el("div", d.type || "–", "sub"), note];
    }
    // Beta version on offer: violet "Beta" tag beside the firmware ID; running downgrade: tag "Downgrade"
    const sub = box("div", "sub fw-id", el("span", d.type || "–"), d.beta && offer ? el("span", t("Beta"), "tag beta") : null,
      down ? el("span", t("Downgrade"), "tag") : null);
    // Scheduled installation: clock line with its time under the version
    if (d.planned) return [line, sub, box("div", "hold-note plan-note", icon("clock"), el("span", t("Install at {time}", {time: d.planned.at_text})))];
    // Updated: what the comparison with the backup before the update found
    return [line, sub, justUpdated(d) ? afterUpdateNote(d) : null].filter(Boolean);
  }},
  status: {label: t("Status"), sort: d => STATUS_LABELS[shownStatus(d)] || d.status, cell: d => [box("div", "status-cell", statusCell(d), installButton(d), ignoredPill(d), removedPill(d))]},
  action: {label: t("Action"), cell: d => [actionStrip(d)]},
  last_update: {label: t("Last update"), optional: true, sort: d => d.last_update, cell: d => [el("span", fmt(d.last_update), "nowrap")]},
  host_name: {label: t("Host name"), optional: true, sort: d => d.host_name, cell: d => [el("span", d.host_name || "–", "num")]},
  ntp: {label: "NTP", optional: true, sort: d => d.ntp, cell: d => [d.ntp || "–"]},
  menu: {label: "", fixed: true, cell: d => [iconButton("dots", t("Actions for {name}", {name: d.name}), e => openRowMenu(d, e.currentTarget))]},
  // Mobile card only (hidden on wide screens by CSS, not offered in the column chooser)
  details: {label: "", fixed: true, cell: d => [detailsCell(d)]},
};
// Data columns can be reordered; selection (left) and menu/details (right) stay at the edges
const DATA_COLUMNS = Object.keys(COLUMNS).filter(k => !COLUMNS[k].fixed);
const defaultPrefs = () => ({sort: ["device", "asc"], hidden: DATA_COLUMNS.filter(k => COLUMNS[k].optional), order: [...DATA_COLUMNS]});
/** Hidden columns of "Reset columns": the optional ones; Host name is shown while the option
 *  "Resolve host names" is on (as when the option is switched on). */
const defaultHidden = () => defaultPrefs().hidden.filter(k => k !== "host_name" || !(ui.state && ui.state.host_names));
// Sort order, hidden columns and column order – kept per end device (browser storage);
// a new device starts with the defaults (device name ascending)
const prefs = {devices: defaultPrefs()};
const savePrefs = () => saveLocal("devices", prefs.devices);
/** Adopt the stored prefs of this end device; unknown column IDs are discarded. */
export function loadPrefs() {
  const raw = loadLocal("devices"), stored = raw && typeof raw === "object" ? raw : {};
  const known = k => DATA_COLUMNS.includes(k), base = defaultPrefs();
  const order = [...new Set((Array.isArray(stored.order) ? stored.order : []).filter(known))];
  const sort = Array.isArray(stored.sort) && COLUMNS[stored.sort[0]] && COLUMNS[stored.sort[0]].sort ? stored.sort : base.sort;
  // Optional columns added since the prefs were stored start hidden like on a new device
  const added = order.length ? DATA_COLUMNS.filter(k => !order.includes(k) && COLUMNS[k].optional) : [];
  // Columns added since then take their default place: after the column they follow by default
  for (const [i, k] of DATA_COLUMNS.entries()) if (!order.includes(k)) order.splice(i ? order.indexOf(DATA_COLUMNS[i - 1]) + 1 : 0, 0, k);
  prefs.devices = {sort: sort[1] === "desc" ? sort : [sort[0], "asc"],
    hidden: Array.isArray(stored.hidden) ? [...stored.hidden.filter(known), ...added] : base.hidden, order};
}
/** Change the sort order (toolbar control and column heads share it). */
function setSort(value) { prefs.devices.sort = value; savePrefs(); render(true); }
// Toolbar control: all sortable columns, also those hidden in the table
const devSort = sortControl(DATA_COLUMNS.filter(k => COLUMNS[k].sort).map(k => [k, COLUMNS[k].label]), setSort);
$("dev-sort").replaceWith(devSort.node);
/** All column keys in display order; without the option "Resolve host names" there is no
 *  Host name column, without a device in an area of Home Assistant no Area column. */
const columnKeys = () => ["select", ...prefs.devices.order.filter(offered),
  ...Object.keys(COLUMNS).filter(k => COLUMNS[k].fixed && k !== "select")];
/** False for a column the app does not offer right now (Host name while the option is off,
 *  Area while no device has an area). */
const offered = key => key === "host_name" ? !!(ui.state && ui.state.host_names) : key === "area" ? !!(ui.state && anyArea(ui.state.devices)) : true;
// The option "Resolve host names" as last seen by this end device: switching it on shows
// the Host name column once (the user may hide it again)
function followHostNames() {
  const on = !!ui.state.host_names;
  if (on && loadLocal("hostNamesSeen") !== true && prefs.devices.hidden.includes("host_name")) {
    prefs.devices.hidden = prefs.devices.hidden.filter(k => k !== "host_name"); savePrefs(); syncChooser();
  }
  if (loadLocal("hostNamesSeen") !== on) saveLocal("hostNamesSeen", on);
  for (const n of document.querySelectorAll('#cols-devices .sort-item[data-col="host_name"], .sort-menu .sort-item[data-value="host_name"]')) n.hidden = !on;
  if (!on && prefs.devices.sort[0] === "host_name") prefs.devices.sort = ["device", prefs.devices.sort[1]];
  // The same for the Area column while no device has an area
  const areas = offered("area");
  for (const n of document.querySelectorAll('#cols-devices .sort-item[data-col="area"], .sort-menu .sort-item[data-value="area"]')) n.hidden = !areas;
  if (!areas && prefs.devices.sort[0] === "area") prefs.devices.sort = ["device", prefs.devices.sort[1]];
}
/** Move a data column before another one (drag & drop) or by one step (keyboard). */
function moveColumn(key, target) {
  const order = prefs.devices.order.filter(k => k !== key);
  order.splice(target == null ? order.length : Math.max(0, Math.min(order.length, target)), 0, key);
  prefs.devices.order = order; savePrefs(); render(true);
}
// All columns are rendered; columns hidden by the user only get a CSS class, so the
// mobile card (which ignores the column choice) always shows its fixed content.
const colClass = key => (prefs.devices.hidden.includes(key) ? " col-hidden" : "");

/** Sort rows by the chosen column; empty values last, ties by name. */
function sortRows(rows) {
  const [key, dir] = prefs.devices.sort, col = COLUMNS[key] && COLUMNS[key].sort ? COLUMNS[key] : COLUMNS.device;
  const sign = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = col.sort(a), y = col.sort(b);
    if (x == null || x === "") return y == null || y === "" ? 0 : 1;
    if (y == null || y === "") return -1;
    const c = typeof x === "number" && typeof y === "number" ? x - y : collator.compare(String(x), String(y));
    return sign * c || collator.compare(a.name || "", b.name || "");
  });
}
/** Table header: a click, Enter or the space bar on a sortable column sorts, a second
 *  time reverses (aria-sort names the order). Data columns are draggable (mouse) and
 *  movable with Alt+ArrowLeft/Right (keyboard). */
function tableHead() {
  const tr = el("tr");
  for (const key of columnKeys()) {
    const col = COLUMNS[key], th = el("th", col.label, `c-${key}${colClass(key)}`);
    const [sKey, dir] = prefs.devices.sort;
    if (col.sort) {
      th.classList.add("sortable");
      if (sKey === key) { th.dataset.dir = dir; th.setAttribute("aria-sort", dir === "desc" ? "descending" : "ascending"); }
    }
    if (!col.fixed) {
      th.tabIndex = 0; th.draggable = true; setTip(th, col.sort ? t("Click to sort · drag or Alt+Arrow to move") : t("Drag or Alt+Arrow to move"));
      th.ondragstart = e => { e.dataTransfer.setData("text/plain", key); e.dataTransfer.effectAllowed = "move"; };
      th.ondragover = e => { e.preventDefault(); th.classList.add("drop"); };
      th.ondragleave = () => th.classList.remove("drop");
      th.ondrop = e => {
        e.preventDefault(); th.classList.remove("drop");
        const from = e.dataTransfer.getData("text/plain");
        if (DATA_COLUMNS.includes(from) && from !== key) moveColumn(from, prefs.devices.order.filter(k => k !== from).indexOf(key));
      };
      th.onkeydown = e => {
        if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
          e.preventDefault();
          moveColumn(key, prefs.devices.order.indexOf(key) + (e.key === "ArrowLeft" ? -1 : 1));
          const moved = document.querySelector(`#dev-cards th.c-${key}`); if (moved) moved.focus();
        } else if ((e.key === "Enter" || e.key === " ") && col.sort) { e.preventDefault(); th.onclick(); }
      };
    }
    if (col.sort) th.onclick = () => setSort([key, sKey === key && dir === "asc" ? "desc" : "asc"]);
    tr.append(th);
  }
  return box("thead", null, tr);
}
/** One device card (table with header) – or nothing if it has no rows. */
function deviceCard(title, rows, head, headExtra) {
  const card = el("div", null, "card"), h = el("div", null, "card-head");
  h.append(...(head || []), el("h2", `${title} · ${rows.length}`), el("span", null, "spacer"), ...(headExtra || []));
  const tbody = el("tbody");
  for (const d of sortRows(rows)) {
    const tr = el("tr"); tr.dataset.key = d.id;
    if (d.ignored) tr.className = "ignored";
    for (const key of columnKeys()) {
      const c = el("td", null, `c-${key}${colClass(key)}`);
      c.append(...COLUMNS[key].cell(d)); tr.append(c);
    }
    tbody.append(tr);
  }
  const table = el("table", null, "dev-table"); table.append(tableHead(), tbody);
  card.append(h, table);
  return card;
}
// Kinds of the Shellys of other integrations and where they are updated
const OTHER_KINDS = {blu_gateway: "BLU TRV", bluetooth: "BLU (Bluetooth)", zha: "Zigbee (ZHA)", zigbee2mqtt: "Zigbee2MQTT",
  mqtt: "MQTT", matter: "Matter"};
/** "Where to update" of a Shelly of another integration. */
function whereToUpdate(o) {
  switch (o.kind) {
    case "blu_gateway": return o.gateway ? t("Update in the Shelly app, or later via its gateway “{gateway}”", {gateway: o.gateway}) : t("Update in the Shelly app");
    case "bluetooth": return t("Update in the Shelly app (Bluetooth)");
    case "zha": return t("Update with ZHA (Zigbee OTA)");
    case "zigbee2mqtt": return t("Update with Zigbee2MQTT");
    case "matter": return t("Update with the Home Assistant Matter server");
    default: return t("Update in the device's web interface or the Shelly app");
  }
}
/** Read-only card "Other Shellys": name, kind, model, firmware and where to update. */
function othersCard(list) {
  const head = box("div", "card-head", el("h2", `${t("Other Shellys")} · ${list.length}`), el("span", t("Display only – no update function here"), "sub"));
  const cols = box("div", "oth-row oth-head", ...[t("Name"), t("Kind"), t("Model"), t("Firmware"), t("Where to update")].map(x => el("span", x)));
  const rows = list.map(o => box("div", "oth-row", el("span", o.name, "name oth-name"), el("span", OTHER_KINDS[o.kind] || o.kind, "oth-kind"),
    el("span", o.model || "–", "oth-model"), el("span", o.firmware || "–", "oth-fw"), el("span", whereToUpdate(o), "oth-where")));
  return box("div", "card others", head, cols, ...rows);
}
/** Device tab: one card per section in the order of SECTIONS (only sections with
 *  devices), then the read-only "Other Shellys"; the filter of the toolbar limits the
 *  rows ("Other Shellys": only that card, "All": every card). */
export function renderDevices() {
  const s = ui.state, q = searchText($("dev-search"));
  const ok = new Set(s.devices.filter(d => installable(d, s)).map(d => d.id));
  for (const id of [...selected]) if (!ok.has(id)) selected.delete(id);
  followHostNames(); syncChooser();
  const searched = s.devices.filter(d => matches(q, [d.name, d.shelly_name, d.ip, d.host_name, d.model, d.type, d.area]));
  syncAreaFilter(searched);
  const found = searched.filter(areaMatch);
  const others = (s.others || []).filter(o => matches(q, [o.name, o.model, o.firmware, OTHER_KINDS[o.kind]]));
  if (!FILTERS.some(f => f[0] === ui.devFilter)) ui.devFilter = "all";
  // Counts of the filter entries respect the search
  // "On hold" only while the option "Offer updates only after (days)" is above 0
  const filterItem = devFilter.node.querySelector('.filter-item[data-value="hold"]');
  if (filterItem) filterItem.hidden = !(s.offer_after_days > 0);
  if (ui.devFilter === "hold" && !(s.offer_after_days > 0)) ui.devFilter = "all";
  devFilter.set(ui.devFilter, Object.fromEntries(FILTERS.map(([key, , fn]) => [key, key === "others" ? others.length : found.filter(fn).length])));
  devSort.set(prefs.devices.sort);
  const rows = found.filter(filterFn(ui.devFilter)), bySection = new Map(SECTIONS.map(([key]) => [key, []]));
  shownRows = rows;
  for (const d of rows) bySection.get(sectionOf(d)).push(d);
  for (const d of s.devices) if (["updates", "beta"].includes(sectionOf(d))) shownUpdates.add(d.id); else shownUpdates.delete(d.id);
  const cards = [];
  for (const [key, title] of SECTIONS) {
    const list = bySection.get(key);
    if (!list.length) continue;
    if (key !== "updates" && key !== "beta") { cards.push(deviceCard(title, list)); continue; }
    // "Updates Available" and "Beta Updates" each have their own selection and button:
    // one dialog never mixes stable and beta versions
    const sel = list.filter(d => ok.has(d.id)), mine = sel.filter(d => selected.has(d.id));
    const all = input("checkbox");
    all.setAttribute("aria-label", key === "beta" ? t("Select all installable beta updates") : t("Select all installable updates"));
    // Without an installable device the box is hidden (its place is kept, like a row without checkbox)
    all.disabled = !sel.length; all.checked = sel.length > 0 && sel.every(d => selected.has(d.id));
    if (!sel.length) all.classList.add("ghost");
    all.onchange = () => { for (const d of sel) all.checked ? selected.add(d.id) : selected.delete(d.id); render(true); };
    const go = el("button", t("Review & install ({n})", {n: mine.length}), "primary"); go.disabled = !mine.length;
    go.onclick = () => openInstall(mine);
    cards.push(deviceCard(title, list, [all], [go]));
  }
  if (others.length && ["all", "others"].includes(ui.devFilter)) cards.push(othersCard(others));
  if (!cards.length) cards.push(box("div", "card", el("div", s.devices.length || (s.others || []).length ? t("No devices match.") :
    s.first_start_open ? t("No devices yet – they are found by the first check.") : t("No devices yet – run “Check now”."), "empty")));
  // Above the cards: the strip "Security" (while devices are known), above it the card
  // "Getting started" of a fresh installation, on phones the line of the missing Shellys first
  if (s.devices.length) cards.unshift(securityStrip(found));
  const start = gettingStartedCard(); if (start) cards.unshift(start);
  listingLine(s, cards);
  $("dev-cards").replaceChildren(...cards);
}
// ---- CSV export
// Toolbar icon (wide screens, before "Columns"): saves the list as shown – search, filter
// and area filter applied, in the chosen sort order – at once, without a dialog.
let shownRows = [];
/** Save the shown devices as CSV file. */
function exportCsv() {
  const heads = [t("Name"), t("Shelly name"), t("Area"), t("Model"), t("Generation"), "IP", t("Firmware"), t("Available"), t("Status"), t("Last check")];
  const lines = sortRows(shownRows).map(d => [d.name, d.shelly_name, d.area, d.model, d.gen, d.ip, d.version, hasUpdate(d) ? d.offer_version : "",
    STATUS_LABELS[shownStatus(d)] || d.status, d.last_check ? fmt(d.last_check) : ""]);
  const now = new Date();
  saveCsv([heads, ...lines], `shelly-devices-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.csv`);
}
$("cols").before(iconButton("download", t("Save the list as CSV file"), exportCsv, "tool-btn csv-btn"));
// ---- Shellys not listed
// Shellys of the Shelly integration that Home Assistant knows but the app does not list
// (state field listing {listed, missing: [{name, model, reason}]}, updated by full checks)
const MISSING_REASONS = {
  disabled: [t("Disabled in Home Assistant"), t("Enable the device (or its entry) in Settings → Devices & services → Shelly.")],
  discovered: [t("Found, but not set up"), t("Home Assistant discovered it in the network – add it in Settings → Devices & services.")],
  ignored: [t("Ignored in Home Assistant"), t("The discovery was ignored – stop ignoring it in Settings → Devices & services → Shelly, then add it.")],
};
/** Dialog "Shellys not listed": one entry per missing Shelly with its reason and what to do. */
function openMissing() {
  const l = ui.state.listing, total = l.listed + l.missing.length;
  const rows = l.missing.map(m => {
    const [reason, fix] = MISSING_REASONS[m.reason] || [m.reason, ""];
    return box("div", "miss-row", box("div", "miss-head", el("span", m.name, "name"), el("span", reason, "tag warn")),
      m.model ? el("div", m.model, "sub") : null, fix ? el("div", fix, "miss-fix") : null);
  });
  modal(t("Shellys not listed"), [
    el("p", t("Home Assistant knows {total} Shellys of the Shelly integration; {listed} of them are listed here.", {total, listed: l.listed})),
    section(`${t("Not listed")} · ${l.missing.length}`, box("div", "miss-list", ...rows)),
    section(t("Still missing?"), el("p", t("A Shelly that Home Assistant does not know cannot be listed – add it to the Shelly integration first. ") +
      t("Shellys of other integrations (Bluetooth, Zigbee, Matter) are shown under “Other Shellys”."), "hint"))]);
}
/** Line "N of M Shellys listed · Why are K missing?" (only while at least one is missing):
 *  right in the toolbar on wide screens, above the first card on phones. */
function listingLine(s, cards) {
  for (const n of document.querySelectorAll("#view-devices .listing")) n.remove();
  const l = s.listing;
  if (!l || !l.missing || !l.missing.length) return;
  const make = () => {
    const b = el("button", null, "listing"); b.type = "button";
    b.append(el("span", t("{listed} of {total} Shellys listed", {listed: l.listed, total: l.listed + l.missing.length})),
      el("span", t("Why are {n} missing?", {n: l.missing.length}), "listing-link"));
    b.onclick = openMissing; return b;
  };
  document.querySelector("#view-devices .tb-extra").prepend(make());
  const phone = make(); phone.classList.add("listing-phone"); cards.unshift(phone);
}

// ---- Column chooser
// "Columns": round icon at the right end of the toolbar (wide screens only; the phone tiles
// have a fixed layout). Its popover looks like the sort and filter tools: title, one entry
// per data column with a check mark while shown (a click keeps the popover open), "Reset
// columns" last. A dot on the icon while the columns differ from the default.
let columnsTool = null;
/** Build the column chooser once; its entries follow prefs (syncChooser). */
export function buildChooser() {
  const items = DATA_COLUMNS.map(key => {
    const b = el("button", COLUMNS[key].label, "sort-item"); b.type = "button"; b.role = "menuitemcheckbox"; b.dataset.col = key;
    b.onclick = () => {
      const hidden = new Set(prefs.devices.hidden); hidden.has(key) ? hidden.delete(key) : hidden.add(key);
      prefs.devices.hidden = [...hidden]; savePrefs(); syncChooser(); render(true);
    };
    return b;
  });
  const reset = el("button", t("Reset columns"), "sort-item reset-cols"); reset.type = "button"; reset.role = "menuitem";
  reset.onclick = () => { prefs.devices = {...defaultPrefs(), hidden: defaultHidden()}; savePrefs(); syncChooser(); render(true); };
  const menu = box("div", "sort-menu", el("div", t("Columns"), "pop-title"), ...items, el("hr"), reset);
  menu.role = "menu"; menu.id = "cols-devices";
  columnsTool = toolButton("columns", t("Columns"), menu); columnsTool.node.id = "cols";
  $("cols").replaceWith(columnsTool.node);
}
/** Check marks of the chooser from prefs; the dot while hidden columns or their order differ
 *  from the default (columns the app does not offer right now do not count). */
export function syncChooser() {
  for (const b of $("cols-devices").querySelectorAll("[data-col]")) b.setAttribute("aria-checked", String(!prefs.devices.hidden.includes(b.dataset.col)));
  const shown = keys => DATA_COLUMNS.filter(k => offered(k) && !keys.includes(k)).join();
  const changed = shown(prefs.devices.hidden) !== shown(defaultHidden()) ||
    prefs.devices.order.filter(offered).join() !== DATA_COLUMNS.filter(offered).join();
  columnsTool.button.classList.toggle("has-value", changed);
}
$("dev-search").oninput = () => render(true);
searchTool($("dev-search"));

// ---------------------------------------------------------------- Row menu ⋮
const rowmenu = $("rowmenu");
let menuOwner = null;   // label of the ⋮ button the open menu belongs to
/** Open the action menu of a device next to its ⋮ button. Choosing an entry and the
 *  Escape key return the keyboard focus to that button. */
function openRowMenu(d, anchor) {
  const can = editable(d), items = [];
  menuOwner = anchor.getAttribute("aria-label");
  const add = (text, fn, enabled = can, cls = "") => {
    const b = el("button", text, cls); b.role = "menuitem"; b.disabled = !enabled;
    b.onclick = () => { closeRowMenu(true); fn(); }; items.push(b);
  };
  // The full check blocks everything; otherwise only work on this device itself
  const s = ui.state || {}, running = d.installing || d.status === "installing";
  const blocked = s.scanning ? s.busy || t("Check in progress") : running ? t("Installation is running") :
    d.activity ? (d.activity === "reloading" ? t("Device info is being reloaded") : t("Device is being checked")) : null;
  // Group 1: updates
  add(t("Check for updates"), () => checkDevice(d), !blocked);
  if (installable(d, ui.state)) add(t("Install update"), () => openInstall([d]), true);   // only for Ready
  if (held(d)) add(t("Offer update now"), () => offerNow(d), !blocked);
  // Scheduled installation: install it now (the usual dialog) or cancel it
  if (d.planned) {
    add(t("Install now"), () => openInstall([d]), !blocked && !d.activity);
    add(t("Cancel scheduled installation"), () => cancelPlan(d), true);
  }
  // Downgrade to a stored older version – Gen2+ only, also up to date / ignored devices,
  // and only while the option "Allow downgrades" is on (else the entry is not shown).
  // Disabled (no dialog) while no older version is stored or something blocks it; a
  // scheduled installation blocks it like "Install": disabled with the hint below it
  if (d.gen >= 2 && s.allow_downgrade && d.planned) {
    add(t("Downgrade"), () => {}, false); items.push(el("div", t("Cancel the scheduled installation first"), "menu-hint"));
  } else if (d.gen >= 2 && s.allow_downgrade) add(t("Downgrade"), () => openDowngrade(d), downgradable(d, ui.state) && d.downgrades > 0);
  // Group 2: the device itself
  items.push(el("hr"));
  add(t("Device details"), () => showStatus(d), true);
  // Until the next check of the device (the check after an intermediate version keeps it)
  if (d.after_update) add(t("Comparison after update"), () => openComparison(d), true);
  add(t("Reload device info"), () => reloadDevice(d), !blocked && !!d.ip);
  if (blocked) items.push(el("div", t("Not available – {reason}", {reason: blocked}), "menu-hint"));   // reason, also for touch
  const url = webUrl(d);
  if (url) { const a = el("a", t("Open web UI")); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.role = "menuitem"; items.push(a); }
  // Group 3: device settings
  items.push(el("hr"));
  add(t("NTP server"), () => editValue(d, "ntp"));
  add(t("Power-on action"), () => editAop(d), can && !!(d.outputs && d.outputs.length));
  add(t("Eco mode"), () => editEco(d), can && typeof d.eco === "boolean");
  add(d.password_set ? t("Device password (stored)") : t("Device password"), () => editPassword(d), !!d.ip && !!d.gen);
  add(t("Backups"), () => openDeviceBackups(d.id), true);
  // Group 4: ignore – a device removed from Home Assistant cannot be ignored, it can be
  // removed from the list instead
  items.push(el("hr"));
  if (d.removed) add(t("Remove from list"), () => forgetDevice(d), !blocked);
  else add(d.ignored ? t("Resume device") : t("Ignore device"), () => setIgnored(d, !d.ignored), !d.installing);
  // Group 5: restart
  items.push(el("hr"));
  add(t("Restart"), () => restartDevice(d), can, "red");
  rowmenu.replaceChildren(...items); rowmenu.hidden = false;
  const r = anchor.getBoundingClientRect();
  rowmenu.style.top = Math.min(r.bottom + 4, innerHeight - rowmenu.offsetHeight - 8) + "px";
  rowmenu.style.left = Math.max(8, r.right - rowmenu.offsetWidth) + "px";
  const first = rowmenu.querySelector("a,button:not(:disabled)"); if (first) first.focus();
}
/** Check only this device (no installation); the server refuses a second check (409). */
function checkDevice(d) {
  api(`${devUrl(d)}/check`, {}).then(() => load(true)).catch(e => message(t("Check"), e.message, "error"));
}
/** "Offer update now": lift the offer delay of the held version for this device; the
 *  server then checks the device, which provides the update (installed after approval). */
function offerNow(d) {
  api(`${devUrl(d)}/offer-now`, {}).then(() => load(true)).catch(e => message(t("Offer update now"), e.message, "error"));
}
/** Reload Device Info: device-only read (no firmware lookup), row shows the
 *  loading state, then a short success or warning – no popup. */
async function reloadDevice(d) {
  const row = ui.state.devices.find(x => x.id === d.id); if (row) { row.activity = "reloading"; render(true); }
  try {
    const r = await api(`${devUrl(d)}/reload`, {});
    r.warning ? toast(`${d.name}: ${r.warning}`, true) : toast(t("{name}: device info reloaded", {name: d.name}));
  } catch (e) { toast(`${d.name}: ${e.message}`, true); }
  // The state object may be reused for an unchanged (304) answer: undo the local mark
  if (row && row.activity === "reloading") row.activity = null;
  load(true);
}
/** Ignore or resume a device (local only; resuming sends nothing to the device). */
function setIgnored(d, ignored) {
  api(`${devUrl(d)}/ignore`, {ignored}).then(() => { selected.delete(d.id); load(true); }).catch(e => message(t("Ignore"), e.message, "error"));
}
/** Remove a device that was removed from Home Assistant from the list, after a
 *  confirmation (its stored data and stored password are forgotten). */
function forgetDevice(d) {
  confirmDialog({title: t("Remove from list?"), icon: "delete", button: t("Remove"),
    content: [el("p", t("{name} was removed from Home Assistant / the Shelly integration. Its stored data and a password stored in this app are deleted.", {name: d.name})),
      el("p", t("If the device is added to Home Assistant again, the next full check lists it anew."), "hint")],
    action: async () => {
      await api(`${devUrl(d)}/forget`, {confirm: true}); selected.delete(d.id); closeModal(); toast(t("{name}: removed from the list", {name: d.name}));
    }});
}
/** Close the row menu. refocus: the keyboard focus returns to the ⋮ button it was
 *  opened from (the list may have been redrawn since: found by its label). */
export function closeRowMenu(refocus) {
  if (rowmenu.hidden) return;
  rowmenu.hidden = true;
  const button = refocus && [...document.querySelectorAll("#dev-cards td.c-menu button")].find(b => b.getAttribute("aria-label") === menuOwner);
  if (button) button.focus({preventScroll: true});
}
document.addEventListener("click", e => { if (!rowmenu.hidden && !rowmenu.contains(e.target) && !e.target.closest("td.c-menu")) closeRowMenu(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeRowMenu(true); hideTip(); } });
addEventListener("scroll", closeRowMenu, true);
