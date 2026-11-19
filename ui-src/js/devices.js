// Devices tab: status and action icons, the device table / phone tiles and the row menu.
import {
  $, api, attachTip, box, collator, devUrl, el, fmt, hideTip, icon, iconButton, loadLocal, message, modal, openDetails,
  saveLocal, section, selected, sortControl, toast, ui, valueList, webUrl,
} from "./core.js";
import {DOWNGRADE_OFF, OFFER_SOURCES, openDowngrade, openInstall} from "./install.js";
import {
  aopLabel, aopSummary, aopTone, editAop, editDebug, editEco, editPassword, editSwitch, editValue, featureStrip,
  restartDevice,
} from "./device-settings.js";
import {load, queued, render} from "./main.js";

// ---------------------------------------------------------------- Devices
const STATUS_LABELS = {"up-to-date": "Up-To-Date", available: "Available", ready: "Ready", queued: "Queued",
  installing: "Installing", unconfirmed: "Unconfirmed", error: "Error", unregistered: "Unregistered", skipped: "Skipped",
  updated: "Updated"};
// A device with a confirmed or unconfirmed installation stays in the "Updates
// available" card (without checkbox) until the next full check or a single check of
// this device; a confirmed one is shown as "Updated", an unconfirmed one keeps its status
const installedRecently = d => !!d.updated && ["up-to-date", "unconfirmed"].includes(d.status);
// A failed installation also stays there until that check – with checkbox and
// "Install" for a retry (its file stays provided); so does a stored version offered
// for a manual installation while the Shelly update server could not be asked
const failedInstall = d => d.status === "error" && !!(d.install_failed || d.store_fallback);
const justUpdated = d => installedRecently(d) && d.status === "up-to-date";
const shownStatus = d => d.installing && d.status === "ready" ? "queued" : justUpdated(d) ? "updated" : d.status;
const hasUpdate = d => ["available", "ready", "installing"].includes(d.status);
// Ignored devices are not installable until they are resumed; nothing is installable
// while the download base URL is missing or the unconfirmed former example
export const installable = (d, s) => (d.status === "ready" || failedInstall(d)) && !d.installing && !d.ignored && !s.scanning && !d.activity &&
  !(s.download_base && s.download_base.issue);
/** Why "Install" is not possible for a device right now (tooltip of the disabled button). */
export function installBlocker(d, s) {
  if (!d) return "Device is not known anymore";
  if (d.installing || d.status === "installing") return "Installation is running";
  if (s.scanning) return "Check in progress";
  if (d.activity) return "Device is being checked";
  if (d.ignored) return "Device is ignored";
  if (s.download_base && s.download_base.issue) return s.download_base.message || "Download base URL is not set";
  return d.status === "ready" || failedInstall(d) ? null : "Not provided for this device anymore";
}
// A downgrade needs a reached Gen2+ device, no check/installation and a valid download URL
const downgradable = (d, s) => d.gen >= 2 && !!d.ip && !d.installing && d.status !== "installing" && !s.scanning &&
  d.activity !== "reloading" && !(s.download_base && s.download_base.issue);
// Settings can be read/changed once the device was reached and while it is not updating or reloading
const editable = d => !!d.ip && !!d.gen && !d.installing && d.status !== "installing" && d.activity !== "reloading";
const FILTERS = {all: ["All", () => true], updates: ["Updates", d => hasUpdate(d) && !d.ignored],
  errors: ["Errors", d => ["error", "unconfirmed"].includes(d.status)], uptodate: ["Up to date", d => d.status === "up-to-date"],
  ignored: ["Ignored", d => d.ignored]};

// Running check of a device (server-side state, in memory only)
const ACTIVITY_LABELS = {waiting: "Waiting", checking: "Checking…", downloading: "Downloading…", reloading: "Reloading…"};
// The last successful check differs from the last attempt only after a failed attempt
const lastSuccess = d => d.last_success && d.last_success !== d.last_check ? fmt(d.last_success) : null;

/** Text of the status tooltip (details, versions, last attempt and last success). */
function statusText(d) {
  return [d.message, `Installed: ${d.version || "–"}`, d.offer_version ? `Available: ${d.offer_version}` : null,
    `Last check: ${fmt(d.last_check)}`, lastSuccess(d) && `Last successful check: ${lastSuccess(d)}`].filter(Boolean).join("\n");
}
/** Popup with all status details of a device: message left, status right on one line. */
function showStatus(d) {
  const key = shownStatus(d), bad = ["error", "unconfirmed"].includes(d.status);
  const head = box("div", "dstatus", el("p", d.message || "", "dmsg" + (bad ? " bad" : "")),
    el("span", STATUS_LABELS[key] || d.status, "pill p-" + (key || "").replace(/[^a-z]/g, "")));
  const provided = hasUpdate(d) || failedInstall(d);
  modal(d.name, [head,
    section("Firmware", valueList([["Installed", d.version], ["Available", d.offer_version],
      ["Source", provided ? OFFER_SOURCES[d.offer_source] : null]])),
    section("History", valueList([["Last check", fmt(d.last_check)], ["Last success", d.last_success ? fmt(d.last_success) : "never"],
      ["Last update", d.last_update ? fmt(d.last_update) : "–"]]))],
    // Ready (and installable): red button right of Close opens the existing install dialog
    installable(d, ui.state) ? [{text: "Close"}, {text: "Install update", kind: "danger", action: () => openInstall([d])}] : undefined);
}
const AOP_NOTE = "Power-on action: not available – this device has no switchable output.";
/** Popup of a greyed-out action icon: status strip with the reason as pill, explanation below. */
function notePopup(iconName, label, pill, text) {
  modal(label, [featureStrip(iconName, label, "off", pill), el("p", text, "hint")]);
}
/** Power-on action (AOP) in the action column: flash icon with state – green
 *  if every output restores its last state, red if an output has another value, "?"
 *  if the settings could not be read; opens the same dialog as the row menu. Mobile
 *  (labeled): the current value is shown on the button next to the icon. Devices
 *  without switchable outputs get a disabled icon with an explanation. */
function aopButton(d, labeled) {
  const outputs = d.outputs || [];
  const state = !outputs.length ? "restart" : d.settings_error ? "s-unknown" : aopTone(outputs) === "good" ? "c-good" : "c-alert";
  const b = el("button", null, `act ${state}`);
  b.append(icon("aop"));
  if (!outputs.length) {
    if (labeled) b.append(el("span", "Power-on action"));
    b.setAttribute("aria-label", AOP_NOTE); attachTip(b, () => AOP_NOTE);
    b.setAttribute("aria-disabled", "true"); b.onclick = () => notePopup("aop", "Power-on action", "Not available", AOP_NOTE);
    return b;
  }
  if (labeled) b.append(el("span", `Power-on: ${aopSummary(outputs)}`));
  const text = "Power-on action – " + (d.settings_error ? `unknown – ${d.settings_error.message}` : outputs.map(o => `${o.name || o.key}: ${aopLabel(o.aop)}`).join(", "));
  b.setAttribute("aria-label", `${text} (${d.name})`); attachTip(b, () => text);
  b.disabled = !editable(d); b.onclick = () => editAop(d);
  return b;
}

// Action column: one icon per feature. Values from the server: true/false,
// "unsupported" (device/firmware lacks it) or null/missing (not known yet or
// the last read failed) – unknown is shown distinctly ("?"), never as "off".
const UNSUPPORTED = "unsupported";
const GEN1_AP_NOTE = "Not available on Gen1: enabling the access point turns off the Wi-Fi client mode, the device would leave your network.";
const GEN1_DEBUG_NOTE = "Gen1 devices have no websocket debug log.";
const ACTION_ITEMS = [
  {key: "eco", icon: "eco", label: "Eco mode", on: "on", off: "off", edit: d => editEco(d)},
  {key: "auth_enabled", icon: "lock", label: "Password protection", on: "protected", off: "not protected", edit: d => editPassword(d), always: true},
  {key: "ap", icon: "ap", label: "Access point", on: "on", off: "off", alert: true, edit: d => editSwitch(d, "ap")},
  {key: "ble", icon: "ble", label: "Bluetooth", on: "on", off: "off", edit: d => editSwitch(d, "ble")},
  {key: "actions", icon: "actions", label: "Actions", on: "at least one active", off: "none active", alert: true, link: true},
  {key: "schedules", icon: "schedules", label: "Schedules", on: "at least one active", off: "none active", alert: true, link: true},
  // Debug output costs resources and may expose details: yellow while on
  {key: "debug_ws", icon: "bug", label: "Websocket debug log", on: "on", off: "off", warn: true, edit: d => editDebug(d)},
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
  let text = `${item.label}: ${state === "on" ? item.on : state === "off" ? item.off : state === "unsupported" ? "not supported by this device" : "unknown"}`;
  if (state === "unknown" && d.settings_error) text += ` – ${d.settings_error.message}`;
  if (gen1Ap) text += ` – ${GEN1_AP_NOTE}`;
  if (item.key === "debug_ws" && d.gen === 1) text += ` – ${GEN1_DEBUG_NOTE}`;
  // Protected without a password stored in this app = warning (the app cannot
  // read or update the device); green only if protection is on and the password is stored
  const unstored = item.key === "auth_enabled" && state === "on" && !d.password_set;
  if (unstored) text += " – password not stored in this app";
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
    const pill = state === "unsupported" ? "Not supported" : "Not available";
    node.onclick = () => notePopup(item.icon, item.label, pill, text);
  } else {
    node.disabled = !(item.always ? !!d.ip && !!d.gen : editable(d)); node.onclick = () => item.edit(d);
  }
  return node;
}
/** Icon strip of the action column (and labeled buttons in the mobile details). */
function actionStrip(d, labeled) {
  const strip = box("div", labeled ? "act-strip labeled" : "act-strip", aopButton(d, labeled), ...ACTION_ITEMS.map(item => actionButton(d, item, labeled)));
  const r = el("button", null, "act restart"); r.append(icon("restart")); if (labeled) r.append(el("span", "Restart"));
  r.setAttribute("aria-label", `Restart ${d.name}`); attachTip(r, () => "Restart"); r.disabled = !editable(d); r.onclick = () => restartDevice(d);
  strip.append(r);
  return strip;
}
/** Ignored devices: additional yellow marker next to the status, no function. */
const ignoredPill = d => d.ignored ? el("span", "Ignored", "pill p-ignored") : null;
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
  const label = restarting ? "Restarting" :
    (STATUS_LABELS[key] || key || "–") + (key === "installing" && typeof d.ota_progress === "number" ? ` ${d.ota_progress} %` : "");
  const p = el("button", label, "pill p-" + (key || "").replace(/[^a-z]/g, ""));
  p.onclick = () => showStatus(d); attachTip(p, () => statusText(d));
  return p;
}
/** "Install" next to the status of a device with a provided update ("Ready").
 *  Opens the usual installation dialog; disabled with the reason while it is not
 *  possible (same conditions as "Install update"). Not shown while a check runs for
 *  the device or its installation is queued. */
function installButton(d) {
  if (!(d.status === "ready" || failedInstall(d)) || d.activity || d.installing) return null;
  const blocker = installBlocker(d, ui.state), go = el("button", "Install", "danger small install");
  go.disabled = !!blocker;
  go.title = blocker || (d.store_fallback ? "Install the stored version (not verified by the Shelly update server)" : "Install the provided update");
  go.setAttribute("aria-label", `Install update on ${d.name}`);
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
    const list = valueList([["Model", [d.model, d.gen ? `Gen ${d.gen}` : null].filter(Boolean).join(" · ")], ["Firmware ID", d.type], ["NTP", d.ntp],
      ["Last check", fmt(d.last_check)], ["Last success", d.last_success ? fmt(d.last_success) : "never"],
      ["Last update", d.last_update ? fmt(d.last_update) : "–"]], "tile-kv");
    det.replaceChildren(det.firstChild, box("div", "detail-body", controls, list));
  };
  det.append(el("summary", "Details"));
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
      const gap = el("input"); gap.type = "checkbox"; gap.disabled = true; gap.className = "ghost"; gap.setAttribute("aria-hidden", "true"); gap.tabIndex = -1;
      return [gap];
    }
    if (!installable(d, ui.state)) return [];
    const cb = el("input"); cb.type = "checkbox"; cb.checked = selected.has(d.id); cb.setAttribute("aria-label", `Select ${d.name}`);
    cb.onchange = () => { cb.checked ? selected.add(d.id) : selected.delete(d.id); render(true); };
    return [cb];
  }},
  device: {label: "Device", sort: d => d.name, cell: d => {
    // Shelly name + pencil: opens the existing Shelly-name dialog (no HA rename)
    const pen = iconButton("pencil", `Edit Shelly name of ${d.name}`, () => editValue(d, "name"), "pen"); pen.disabled = !editable(d);
    // Tooltips name both names (mouse hover, touch tap)
    const ha = el("div", d.name, "name"), shellyName = el("span", d.shelly_name || "–");
    attachTip(ha, () => `Home Assistant device name: ${d.name}`, true);
    attachTip(shellyName, () => `Shelly device name: ${d.shelly_name || "–"}`, true);
    return [ha, box("div", "sub shelly-name", shellyName, pen)];
  }},
  ip: {label: "IP", sort: d => (d.ip || "").split(".").map(n => n.padStart(3, "0")).join("."), cell: d => {
    if (!d.ip) return ["–"];
    const url = webUrl(d); if (!url) return [el("span", d.ip, "mono")];
    const a = el("a", d.ip, "mono"); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.title = "Open the Shelly web UI";
    return [a];
  }},
  gen: {label: "Gen", sort: d => d.gen, cell: d => [d.gen ?? "–"]},
  firmware: {label: "Firmware", sort: d => d.type, cell: d => {
    const offer = d.offer_version && hasUpdate(d);
    const line = box("div", "nowrap", el("span", shortVersion(d, d.version) || "–"));
    if (offer) line.append(el("span", "→", "arrow"), el("span", shortVersion(d, d.offer_version), "newver"));
    // Gen1: the full version strings stay available as tooltip (and in the status details)
    if (d.gen === 1 && (d.version || offer)) {
      const full = [`Installed: ${d.version || "–"}`, offer ? `Available: ${d.offer_version}` : null].filter(Boolean).join(" · ");
      line.tabIndex = 0; attachTip(line, () => full, true);
    }
    return [line, el("div", d.type || "–", "sub")];
  }},
  status: {label: "Status", sort: d => STATUS_LABELS[shownStatus(d)] || d.status, cell: d => [box("div", "status-cell", statusCell(d), installButton(d), ignoredPill(d))]},
  action: {label: "Action", cell: d => [actionStrip(d)]},
  last_update: {label: "Last update", optional: true, sort: d => d.last_update, cell: d => [el("span", d.last_update ? fmt(d.last_update) : "–", "nowrap")]},
  ntp: {label: "NTP", optional: true, sort: d => d.ntp, cell: d => [d.ntp || "–"]},
  menu: {label: "", fixed: true, cell: d => [iconButton("dots", `Actions for ${d.name}`, e => openRowMenu(d, e.currentTarget))]},
  // Mobile card only (hidden on wide screens by CSS, not offered in the column chooser)
  details: {label: "", fixed: true, cell: d => [detailsCell(d)]},
};
// Data columns can be reordered; selection (left) and menu/details (right) stay at the edges
const DATA_COLUMNS = Object.keys(COLUMNS).filter(k => !COLUMNS[k].fixed);
const defaultPrefs = () => ({sort: ["device", "asc"], hidden: DATA_COLUMNS.filter(k => COLUMNS[k].optional), order: [...DATA_COLUMNS]});
// Sort order, hidden columns and column order – kept per end device (browser storage);
// a new device starts with the defaults (device name ascending)
const prefs = {devices: defaultPrefs()};
const savePrefs = () => saveLocal("devices", prefs.devices);
/** Adopt the stored prefs of this end device; unknown column IDs are discarded. */
export function loadPrefs() {
  const raw = loadLocal("devices"), stored = raw && typeof raw === "object" ? raw : {};
  const known = k => DATA_COLUMNS.includes(k), base = defaultPrefs();
  const order = (Array.isArray(stored.order) ? stored.order : []).filter(known);
  const sort = Array.isArray(stored.sort) && COLUMNS[stored.sort[0]] && COLUMNS[stored.sort[0]].sort ? stored.sort : base.sort;
  prefs.devices = {sort: sort[1] === "desc" ? sort : [sort[0], "asc"],
    hidden: Array.isArray(stored.hidden) ? stored.hidden.filter(known) : base.hidden,
    order: [...new Set(order), ...DATA_COLUMNS.filter(k => !order.includes(k))]};
}
/** Change the sort order (toolbar control and column heads share it). */
function setSort(value) { prefs.devices.sort = value; savePrefs(); render(true); }
// Toolbar control: all sortable columns, also those hidden in the table
const devSort = sortControl(DATA_COLUMNS.filter(k => COLUMNS[k].sort).map(k => [k, COLUMNS[k].label]), setSort);
$("dev-sort").replaceWith(devSort.node);
/** All column keys in display order. */
const columnKeys = () => ["select", ...prefs.devices.order, ...Object.keys(COLUMNS).filter(k => COLUMNS[k].fixed && k !== "select")];
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
/** Table header: click on a sortable column sorts, a second click reverses. */
/** Data columns are draggable (mouse) and movable with Alt+ArrowLeft/Right (keyboard). */
function tableHead() {
  const tr = el("tr");
  for (const key of columnKeys()) {
    const col = COLUMNS[key], th = el("th", col.label, `c-${key}${colClass(key)}`);
    const [sKey, dir] = prefs.devices.sort;
    if (col.sort) { th.classList.add("sortable"); if (sKey === key) th.dataset.dir = dir; }
    if (!col.fixed) {
      th.tabIndex = 0; th.draggable = true; th.title = col.sort ? "Click to sort · drag or Alt+Arrow to move" : "Drag or Alt+Arrow to move";
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
        } else if (e.key === "Enter" && col.sort) th.onclick();
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
    const tr = el("tr");
    if (d.ignored) tr.className = "ignored";
    for (const key of columnKeys()) {
      const c = el("td", null, `c-${key}${colClass(key)}`);
      if (COLUMNS[key].label) c.dataset.label = COLUMNS[key].label;
      c.append(...COLUMNS[key].cell(d)); tr.append(c);
    }
    tbody.append(tr);
  }
  const table = el("table"); table.append(tableHead(), tbody);
  card.append(h, table);
  return card;
}
// Devices shown in "Updates available" at the last render (kept there while checked)
const shownUpdates = new Set();
/** Device tab: "Updates available" card on top, "All other devices" below. */
export function renderDevices() {
  const s = ui.state, q = $("dev-search").value.trim().toLowerCase();
  const ok = new Set(s.devices.filter(d => installable(d, s)).map(d => d.id));
  for (const id of [...selected]) if (!ok.has(id)) selected.delete(id);
  const match = d => !q || [d.name, d.shelly_name, d.ip, d.model, d.type].some(v => String(v || "").toLowerCase().includes(q));
  const found = s.devices.filter(match);
  // Filter chips with counts (counts respect the search)
  $("dev-chips").replaceChildren(...Object.entries(FILTERS).map(([key, [label, fn]]) => {
    const b = el("button", label, "chip" + (ui.devFilter === key ? " active" : ""));
    b.append(el("span", found.filter(fn).length, "count")); b.onclick = () => { ui.devFilter = key; render(true); };
    return b;
  }));
  devSort.set(prefs.devices.sort);
  const rows = found.filter(FILTERS[ui.devFilter][1]);
  // Ignored devices form their own group, always last (known update stays visible there)
  const ignored = rows.filter(d => d.ignored), active = rows.filter(d => !d.ignored);
  // A device listed here stays while it is checked again (its markers are reset by
  // the check); the result of the check decides at the next render without activity
  const listed = d => hasUpdate(d) || shownStatus(d) === "queued" || installedRecently(d) || failedInstall(d) ||
    (!!d.activity && shownUpdates.has(d.id));
  const updates = active.filter(listed), others = active.filter(d => !updates.includes(d));
  for (const d of s.devices) if (listed(d)) shownUpdates.add(d.id); else shownUpdates.delete(d.id);
  const cards = [];
  if (updates.length) {
    const sel = updates.filter(d => ok.has(d.id));
    const all = el("input"); all.type = "checkbox"; all.setAttribute("aria-label", "Select all installable updates");
    all.disabled = !sel.length; all.checked = sel.length > 0 && sel.every(d => selected.has(d.id));
    all.onchange = () => { for (const d of sel) all.checked ? selected.add(d.id) : selected.delete(d.id); render(true); };
    const go = el("button", `Review & install (${selected.size})`, "primary"); go.disabled = !selected.size;
    go.onclick = () => openInstall(s.devices.filter(d => selected.has(d.id)));
    cards.push(deviceCard("Updates available", updates, [all], [go]));
  }
  if (others.length) cards.push(deviceCard(updates.length ? "All other devices" : "Devices", others));
  if (ignored.length) cards.push(deviceCard("Ignored", ignored));
  if (!cards.length) cards.push(box("div", "card", el("div", s.devices.length ? "No devices match." : "No devices yet – run “Check now”.", "empty")));
  $("dev-cards").replaceChildren(...cards);
}
// Column chooser: built once, checkbox state follows prefs
export function buildChooser() {
  for (const [key, col] of Object.entries(COLUMNS)) {
    if (col.fixed) continue;
    const label = el("label"), cb = el("input"); cb.type = "checkbox"; cb.dataset.col = key;
    cb.onchange = () => {
      const hidden = new Set(prefs.devices.hidden); cb.checked ? hidden.delete(key) : hidden.add(key);
      prefs.devices.hidden = [...hidden]; savePrefs(); render(true);
    };
    label.append(cb, " " + col.label); $("cols-devices").append(label);
  }
  const reset = el("button", "Reset columns", "reset-cols"); reset.type = "button";
  reset.onclick = () => { prefs.devices = defaultPrefs(); syncChooser(); savePrefs(); render(true); };
  $("cols-devices").append(el("hr"), reset);
}
/** Set the chooser checkboxes from prefs. */
export function syncChooser() { for (const cb of $("cols-devices").querySelectorAll("input")) cb.checked = !prefs.devices.hidden.includes(cb.dataset.col); }
document.addEventListener("click", e => { if (!$("cols").contains(e.target)) $("cols").open = false; });
$("dev-search").oninput = () => render(true);

// ---------------------------------------------------------------- Row menu ⋮
const rowmenu = $("rowmenu");
/** Open the action menu of a device next to its ⋮ button. */
function openRowMenu(d, anchor) {
  const can = editable(d), items = [];
  const add = (text, fn, enabled = can, cls = "") => {
    const b = el("button", text, cls); b.role = "menuitem"; b.disabled = !enabled;
    b.onclick = () => { closeRowMenu(); fn(); }; items.push(b);
  };
  // The full check blocks everything; otherwise only work on this device itself
  const s = ui.state || {}, running = d.installing || d.status === "installing";
  const blocked = s.scanning ? s.busy || "Check in progress" : running ? "Installation is running" :
    d.activity ? (d.activity === "reloading" ? "Device info is being reloaded" : "Device is being checked") : null;
  add("Check for updates", () => checkDevice(d), !blocked);
  if (installable(d, ui.state)) add("Install update", () => openInstall([d]), true);   // only for Ready
  // Downgrade to a stored older version – Gen2+ only, also up to date / ignored devices
  // Disabled (no dialog) while no older version is stored or something blocks it; while
  // the option "Allow downgrades" is off it is shown disabled with the hint below it
  if (d.gen >= 2 && !s.allow_downgrade) { add("Downgrade", () => {}, false); items.push(el("div", DOWNGRADE_OFF, "menu-hint")); }
  else if (d.gen >= 2) add("Downgrade", () => openDowngrade(d), downgradable(d, ui.state) && d.downgrades > 0);
  add("Reload device info", () => reloadDevice(d), !blocked && !!d.ip);
  if (blocked) items.push(el("div", `Not available – ${blocked}`, "menu-hint"));   // reason, also for touch
  add(d.ignored ? "Resume device" : "Ignore device", () => setIgnored(d, !d.ignored), !d.installing);
  items.push(el("hr"));
  const url = webUrl(d);
  if (url) { const a = el("a", "Open web UI"); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.role = "menuitem"; items.push(a); }
  add("NTP server", () => editValue(d, "ntp"));
  add("Power-on action", () => editAop(d), can && !!(d.outputs && d.outputs.length));
  add("Eco mode", () => editEco(d), can && typeof d.eco === "boolean");
  add(d.password_set ? "Device password (stored)" : "Device password", () => editPassword(d), !!d.ip && !!d.gen);
  add("Status details", () => showStatus(d), true);
  items.push(el("hr"));
  add("Restart", () => restartDevice(d), can, "red");
  rowmenu.replaceChildren(...items); rowmenu.hidden = false;
  const r = anchor.getBoundingClientRect();
  rowmenu.style.top = Math.min(r.bottom + 4, innerHeight - rowmenu.offsetHeight - 8) + "px";
  rowmenu.style.left = Math.max(8, r.right - rowmenu.offsetWidth) + "px";
  const first = rowmenu.querySelector("a,button:not(:disabled)"); if (first) first.focus();
}
/** Check only this device (no installation); the server refuses a second check (409). */
function checkDevice(d) {
  api(`${devUrl(d)}/check`, {}).then(() => load(true)).catch(e => message("Check", e.message, "error"));
}
/** Reload Device Info: device-only read (no firmware lookup), row shows the
 *  loading state, then a short success or warning – no popup. */
async function reloadDevice(d) {
  const row = ui.state.devices.find(x => x.id === d.id); if (row) { row.activity = "reloading"; render(true); }
  try {
    const r = await api(`${devUrl(d)}/reload`, {});
    r.warning ? toast(`${d.name}: ${r.warning}`, true) : toast(`${d.name}: device info reloaded`);
  } catch (e) { toast(`${d.name}: ${e.message}`, true); }
  // The state object may be reused for an unchanged (304) answer: undo the local mark
  if (row && row.activity === "reloading") row.activity = null;
  load(true);
}
/** Ignore or resume a device (local only; resuming sends nothing to the device). */
function setIgnored(d, ignored) {
  api(`${devUrl(d)}/ignore`, {ignored}).then(() => { selected.delete(d.id); load(true); }).catch(e => message("Ignore", e.message, "error"));
}
/** Close the row menu. */
export function closeRowMenu() { rowmenu.hidden = true; }
document.addEventListener("click", e => { if (!rowmenu.hidden && !rowmenu.contains(e.target) && !e.target.closest("td.c-menu")) closeRowMenu(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeRowMenu(); hideTip(); } });
addEventListener("scroll", closeRowMenu, true);
