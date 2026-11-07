"use strict";
// Frontend of the Ingress UI.
// Security: all values are inserted via textContent (no innerHTML), so device
// names, changelogs or server responses cannot inject HTML/JS.
// Write calls send the CSRF token from the <meta> tag.
// Paths are relative so they work below the Ingress prefix.
const CSRF = document.querySelector("meta[name=csrf]").content;
// Inside the HA panel (Ingress iframe) HA shows the app name already: compact header on phones
if (window.parent !== window) document.documentElement.classList.add("embedded");
const $ = id => document.getElementById(id);
const ui = {state: null, firmware: [], firmwareTotal: 0, logs: [], options: null, view: "devices",
  devFilter: "all", logFilter: "all", rendered: ""};
const selected = new Set();       // device IDs ticked for a (multi) installation
const openDetails = new Set();    // keys of expanded <details>, kept across re-renders

// ---------------------------------------------------------------- HA theme
// Via Ingress the page runs in an iframe on the same origin as HA, so the
// variables of the active theme are readable. ALL custom properties of the HA
// root element are copied; values that could load resources (url(), image(),
// @import, CSS escapes) are rejected, as are unusual names and huge values.
const UNSAFE_CSS = /url\s*\(|image(-set)?\s*\(|cross-fade\s*\(|element\s*\(|src\s*\(|@import|\\/i;
const VAR_NAME = /^--[A-Za-z0-9_-]{1,100}$/;
const MAX_VARS = 3000;
let themed = new Set();           // variables currently copied from HA

/** Copy the CSS variables of the active HA theme into this document. */
function applyTheme() {
  let src;
  try {
    if (window.parent === window) return;            // not embedded: keep the fallbacks
    src = window.parent.document.documentElement;
  } catch (e) { return; }                            // other origin: keep the fallbacks
  const cs = getComputedStyle(src), names = new Set();
  for (const list of [cs, src.style]) for (let i = 0; i < list.length; i++) if (list[i].startsWith("--")) names.add(list[i]);
  const root = document.documentElement.style, next = new Set();
  for (const name of names) {
    if (next.size >= MAX_VARS) break;
    const value = cs.getPropertyValue(name).trim();
    if (!VAR_NAME.test(name) || !value || value.length > 2000 || UNSAFE_CSS.test(value)) continue;
    root.setProperty(name, value); next.add(name);
  }
  for (const name of themed) if (!next.has(name)) root.removeProperty(name);
  themed = next;
}
applyTheme();
// Follow theme and light/dark changes in HA immediately
try {
  new MutationObserver(applyTheme).observe(window.parent.document.documentElement, {attributes: true, attributeFilter: ["style", "class"]});
} catch (e) { /* not embedded on the same origin */ }
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

// ---------------------------------------------------------------- Helpers
/** GET without body, otherwise POST with JSON and CSRF token; errors throw with the server text. */
async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": CSRF}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(await r.text() || r.status);
  return r.json();
}
/** rc7: send a firmware file as raw body (the only non-JSON write); errors throw with the server text. */
async function uploadFile(file) {
  const r = await fetch("api/firmware/upload", {method: "POST", body: file,
    headers: {"Content-Type": "application/octet-stream", "X-CSRF-Token": CSRF, "X-Filename": file.name.slice(0, 200)}});
  if (!r.ok) throw new Error(await r.text() || r.status);
  return r.json();
}
let stateTag = null;
/** Device state; conditional request: an unchanged state (304) reuses the last one. */
async function getState() {
  const r = await fetch("api/state", stateTag && ui.state ? {headers: {"If-None-Match": stateTag}} : {});
  if (r.status === 304) return ui.state;
  if (!r.ok) throw new Error(await r.text() || r.status);
  stateTag = r.headers.get("ETag");
  return r.json();
}
/** Create an element with optional text (always textContent) and class. */
function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = String(text); if (cls) e.className = cls; return e; }
/** Element with children. */
function box(tag, cls, ...children) { const e = el(tag, null, cls); e.append(...children.filter(c => c != null)); return e; }
const pad = n => String(n).padStart(2, "0");
/** YYYY-MM-DD HH:MM in local time. */
function fmt(ts) {
  if (!ts) return "never";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const mb = n => (n / 1048576).toFixed(1) + " MB";
const devUrl = d => `api/devices/${encodeURIComponent(d.id)}`;
const collator = new Intl.Collator("en", {numeric: true, sensitivity: "base"});

// Inline icons: Material Design Icons (Apache 2.0)
const ICONS = {
  info: "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z",
  dots: "M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z",
  restart: "M12,4C14.1,4 16.1,4.8 17.6,6.3C20.7,9.4 20.7,14.5 17.6,17.6C15.8,19.5 13.3,20.2 10.9,19.9L11.4,17.9C13.1,18.1 14.9,17.5 16.2,16.2C18.5,13.9 18.5,10.1 16.2,7.7C15.1,6.6 13.5,6 12,6V10.6L7,5.6L12,0.6V4M6.3,17.6C3.7,15 3.3,11 5.1,7.9L6.6,9.4C5.5,11.6 5.9,14.4 7.8,16.2C8.3,16.7 8.9,17.1 9.6,17.4L9,19.4C8,19 7.1,18.4 6.3,17.6Z",
  download: "M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z",
  delete: "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z",
  copy: "M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z",
  close: "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z",
  // 3.0 action column: flash (power-on action), leaf, lock, wifi, bluetooth, link (actions), calendar, pencil
  eco: "M17,8C8,10 5.9,16.17 3.82,21.34L5.71,22L6.66,19.7C7.14,19.87 7.64,20 8,20C19,20 22,3 22,3C21,5 14,5.25 9,6.25C4,7.25 2,11.5 2,13.5C2,15.5 3.75,17.25 3.75,17.25C7,8 17,8 17,8Z",
  lock: "M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z",
  ap: "M12,21L15.6,16.2C14.6,15.45 13.35,15 12,15C10.65,15 9.4,15.45 8.4,16.2L12,21M12,3C7.95,3 4.21,4.34 1.2,6.6L3,9C5.5,7.12 8.62,6 12,6C15.38,6 18.5,7.12 21,9L22.8,6.6C19.79,4.34 16.05,3 12,3M12,9C9.3,9 6.81,9.89 4.8,11.4L6.6,13.8C8.1,12.67 9.97,12 12,12C14.03,12 15.9,12.67 17.4,13.8L19.2,11.4C17.19,9.89 14.7,9 12,9Z",
  ble: "M14.88,16.29L13,18.17V14.41M13,5.83L14.88,7.71L13,9.58M17.71,7.71L12,2H11V9.58L6.41,5L5,6.41L10.59,12L5,17.58L6.41,19L11,14.41V22H12L17.71,16.29L13.41,12L17.71,7.71Z",
  aop: "M7,2V13H10V22L17,10H13L17,2H7Z",
  actions: "M10.59,13.41C11,13.8 11,14.44 10.59,14.83C10.2,15.22 9.56,15.22 9.17,14.83C7.22,12.88 7.22,9.71 9.17,7.76L12.71,4.22C14.66,2.27 17.83,2.27 19.78,4.22C21.73,6.17 21.73,9.34 19.78,11.29L18.29,12.78C18.3,11.96 18.17,11.14 17.89,10.36L18.36,9.88C19.54,8.71 19.54,6.81 18.36,5.64C17.19,4.46 15.29,4.46 14.12,5.64L10.59,9.17C9.41,10.34 9.41,12.24 10.59,13.41M13.41,9.17C13.8,8.78 14.44,8.78 14.83,9.17C16.78,11.12 16.78,14.29 14.83,16.24L11.29,19.78C9.34,21.73 6.17,21.73 4.22,19.78C2.27,17.83 2.27,14.66 4.22,12.71L5.71,11.22C5.7,12.04 5.83,12.86 6.11,13.65L5.64,14.12C4.46,15.29 4.46,17.19 5.64,18.36C6.81,19.54 8.71,19.54 9.88,18.36L13.41,14.83C14.59,13.66 14.59,11.76 13.41,10.59C13,10.2 13,9.56 13.41,9.17Z",
  schedules: "M19,19H5V8H19M16,1V3H8V1H6V3H5C3.89,3 3,3.89 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5C21,3.89 20.1,3 19,3H18V1M17,12H12V17H17V12Z",
  pencil: "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z",
};
/** Inline SVG icon from a path. */
function icon(name) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), path = document.createElementNS(ns, "path");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true"); svg.classList.add("svg");
  path.setAttribute("d", ICONS[name]); svg.append(path); return svg;
}
/** Round icon button with accessible label. */
function iconButton(name, label, onclick, cls) {
  const b = el("button", null, "icon" + (cls ? " " + cls : "")); b.title = label; b.setAttribute("aria-label", label);
  b.append(icon(name)); if (onclick) b.onclick = onclick; return b;
}
/** <details> whose open state survives re-rendering. */
function keptDetails(key, summary, content) {
  const d = el("details"); d.open = openDetails.has(key);
  d.ontoggle = () => { d.open ? openDetails.add(key) : openDetails.delete(key); };
  d.append(el("summary", summary), content); return d;
}
/** Copy text to the clipboard; fallback for non-secure contexts. */
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return; } catch (e) { /* fallback below */ }
  const t = el("textarea"); t.value = text; document.body.append(t); t.select();
  document.execCommand("copy"); t.remove();
}

// ---------------------------------------------------------------- Tooltip
const tip = $("tip");
/** Show a tooltip on hover and keyboard focus; text() is evaluated when shown. */
function attachTip(node, text) {
  const show = () => {
    const t = text(); if (!t) return;
    tip.textContent = t; tip.hidden = false;
    const r = node.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(4, Math.min(r.left, innerWidth - w - 4)) + "px";
    tip.style.top = (r.bottom + h + 8 > innerHeight ? r.top - h - 6 : r.bottom + 6) + "px";
  };
  const hide = () => { tip.hidden = true; };
  node.addEventListener("mouseenter", show); node.addEventListener("focus", show);
  node.addEventListener("mouseleave", hide); node.addEventListener("blur", hide);
}
$("fw-info").append(icon("info"));
attachTip($("fw-info"), () => $("fw-info").dataset.tip);

// ---------------------------------------------------------------- Dialog
// One dialog for everything: title + close, content, buttons bottom right.
// buttons: [{text, kind: "primary"|"danger"|"", action}] – a button without
// action closes the dialog; actions decide themselves (close or new content).
const dlg = $("dlg");
/** Open (or replace the content of) the dialog. */
function modal(title, content, buttons = [{text: "Close"}]) {
  $("dlg-title").textContent = title;
  $("dlg-body").replaceChildren(...content);
  $("dlg-btns").replaceChildren(...buttons.map(({text, kind, action}) => {
    const b = el("button", text, kind || "");
    b.onclick = async () => {
      if (!action) { closeModal(); return; }
      for (const x of $("dlg-btns").children) x.disabled = true;
      await action();
    };
    return b;
  }));
  if (!dlg.open) dlg.showModal();
}
/** Close the dialog and refresh. */
function closeModal() { if (dlg.open) dlg.close(); load(); }
$("dlg-x").append(icon("close"));
$("dlg-x").onclick = closeModal;
const message = (title, text, cls = "hint") => modal(title, [el("p", text, cls)]);
// Short, non-blocking feedback (e.g. Reload Device Info – no popup)
let toastTimer = 0;
function toast(text, warn) {
  const t = $("toast"); t.textContent = text; t.classList.toggle("warn", !!warn); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, warn ? 8000 : 4000);
}
/** Two-column value list. */
function valueList(pairs) {
  const dl = el("dl");
  for (const [k, v] of pairs) dl.append(el("dt", k), el("dd", v ?? "–"));
  return dl;
}

// ---------------------------------------------------------------- Devices
const STATUS_LABELS = {"up-to-date": "Up-To-Date", available: "Available", ready: "Ready", queued: "Queued",
  installing: "Installing", unconfirmed: "Unconfirmed", error: "Error", unregistered: "Unregistered", skipped: "Skipped"};
const shownStatus = d => d.installing && d.status === "ready" ? "queued" : d.status;
const hasUpdate = d => ["available", "ready", "installing"].includes(d.status);
// Ignored devices are not installable until they are resumed; nothing is installable
// while the download base URL is missing or the unconfirmed former example (rc6)
const installable = (d, s) => d.status === "ready" && !d.installing && !d.ignored && !s.scanning &&
  !(s.download_base && s.download_base.issue);
// rc7: a downgrade needs a reached Gen2+ device, no check/installation and a valid download URL
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
/** Popup with all status details of a device. */
function showStatus(d) {
  modal(`Status – ${d.name}`, [valueList([["Status", STATUS_LABELS[shownStatus(d)] || d.status], ["Details", d.message],
    ["Installed", d.version], ["Available", d.offer_version], ["Last check", fmt(d.last_check)],
    ["Last successful check", d.last_success ? fmt(d.last_success) : "never"],
    ["Last update", d.last_update ? fmt(d.last_update) : null]])],
    // Ready (and installable): red button right of Close opens the existing install dialog
    installable(d, ui.state) ? [{text: "Close"}, {text: "Install update", kind: "danger", action: () => openInstall([d])}] : undefined);
}
/** Current power-on action of all outputs as one short text ("Mixed" if they differ). */
function aopSummary(d) {
  const values = [...new Set(d.outputs.map(o => aopLabel(o.aop)))];
  return values.length === 1 ? values[0] : "Mixed";
}
const AOP_NOTE = "Power-on action: not available – this device has no switchable output.";
/** Power-on action (AOP) in the action column: flash icon without state, coloured
 *  like restart; opens the same dialog as the row menu. Mobile (labeled): the
 *  current value is shown on the button next to the icon. Devices without
 *  switchable outputs get a disabled icon with an explanation. */
function aopButton(d, labeled) {
  const b = el("button", null, "act restart");
  b.append(icon("aop"));
  if (!d.outputs || !d.outputs.length) {
    if (labeled) b.append(el("span", "Power-on action"));
    b.setAttribute("aria-label", AOP_NOTE); attachTip(b, () => AOP_NOTE);
    b.setAttribute("aria-disabled", "true"); b.onclick = () => message("Power-on action", AOP_NOTE);
    return b;
  }
  if (labeled) b.append(el("span", `Power-on: ${aopSummary(d)}`));
  const text = labeled ? "Action on power on – " + d.outputs.map(o => `${o.name || o.key}: ${aopLabel(o.aop)}`).join(", ") : "Power-on action";
  b.setAttribute("aria-label", `${text} (${d.name})`); attachTip(b, () => text);
  b.disabled = !editable(d); b.onclick = () => editAop(d);
  return b;
}

// Action column: one icon per feature. Values from the server: true/false,
// "unsupported" (device/firmware lacks it) or null/missing (not known yet or
// the last read failed) – unknown is shown distinctly ("?"), never as "off".
const UNSUPPORTED = "unsupported";
const GEN1_AP_NOTE = "Not available on Gen1: enabling the access point turns off the Wi-Fi client mode, the device would leave your network.";
const ACTION_ITEMS = [
  {key: "eco", icon: "eco", label: "Eco mode", on: "on", off: "off", edit: d => editEco(d)},
  {key: "auth_enabled", icon: "lock", label: "Password protection", on: "protected", off: "not protected", edit: d => editPassword(d), always: true},
  {key: "ap", icon: "ap", label: "Access point", on: "on", off: "off", edit: d => editSwitch(d, "ap")},
  {key: "ble", icon: "ble", label: "Bluetooth", on: "on", off: "off", edit: d => editSwitch(d, "ble")},
  {key: "actions", icon: "actions", label: "Actions", on: "at least one active", off: "none active", alert: true, link: true},
  {key: "schedules", icon: "schedules", label: "Schedules", on: "at least one active", off: "none active", alert: true, link: true},
];
/** State of one feature: "on" | "off" | "unknown" | "unsupported". */
function featureState(d, key) {
  const v = d[key];
  if (v === UNSUPPORTED) return "unsupported";
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
  const url = item.link ? webUrl(d) : null;
  const node = item.link && url && state !== "unsupported" ? el("a") : el("button");
  node.className = `act s-${state}${item.alert ? " alert" : ""}`;
  node.setAttribute("aria-label", text); attachTip(node, () => text);
  node.append(icon(item.icon)); if (labeled) node.append(el("span", item.label));
  if (node.tagName === "A") {   // opens the device web UI (root, no route probing)
    node.href = url; node.target = "_blank"; node.rel = "noopener noreferrer";
  } else if (state === "unsupported" || gen1Ap || (item.link && !url)) {
    // aria-disabled keeps hover/focus/tap for the explanation
    node.setAttribute("aria-disabled", "true"); node.onclick = () => message(item.label, text);
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
  // Gen2+ installations report the device's own download progress (rc6)
  const label = (STATUS_LABELS[key] || key || "–") + (key === "installing" && typeof d.ota_progress === "number" ? ` ${d.ota_progress} %` : "");
  const p = el("button", label, "pill p-" + (key || "").replace(/[^a-z]/g, ""));
  p.onclick = () => showStatus(d); attachTip(p, () => statusText(d));
  return p;
}
/** Mobile only: expandable details with the controls hidden from the compact card.
 *  The content is built only while open (keeps the DOM small on wide screens and
 *  for closed cards); the open state survives re-rendering. */
function detailsCell(d) {
  const key = "dev:" + d.id, det = el("details");
  const fill = () => {
    const controls = box("div", "detail-actions", actionStrip(d, true));
    const list = valueList([["Generation", d.gen], ["Model", d.model], ["Firmware ID", d.type], ["NTP", d.ntp], ["Last check", fmt(d.last_check)],
      ["Last successful check", d.last_success ? fmt(d.last_success) : "never"], ["Last update", d.last_update ? fmt(d.last_update) : null]]);
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
// Link to the Shelly web UI. The server only sends checked private IP addresses;
// the format is checked here once more.
const WEB_URL_RE = /^http:\/\/(\d{1,3}(\.\d{1,3}){3}|\[[0-9a-fA-F:]+\])(:\d{1,5})?\/$/;
const webUrl = d => WEB_URL_RE.test(d.web_url || "") ? d.web_url : null;

// Gen1 version strings ("20230913-112003/v1.14.0-gcb84623") are shortened to the
// version number ("1.14.0") in the Firmware column only; Gen2+ stays unchanged
// (there the part after "-" marks a pre-release, e.g. "1.5.0-beta2").
const GEN1_VERSION_RE = /\/v?(\d+\.\d+\.\d+)/;
const shortVersion = (d, v) => { const m = d.gen === 1 && GEN1_VERSION_RE.exec(v || ""); return m ? m[1] : v; };

// Each column: label, cell renderer (returns nodes), sort value, fixed = always shown
const COLUMNS = {
  select: {label: "", fixed: true, cell: d => {
    if (!installable(d, ui.state)) return [];
    const cb = el("input"); cb.type = "checkbox"; cb.checked = selected.has(d.id); cb.setAttribute("aria-label", `Select ${d.name}`);
    cb.onchange = () => { cb.checked ? selected.add(d.id) : selected.delete(d.id); render(true); };
    return [cb];
  }},
  device: {label: "Device", sort: d => d.name, cell: d => {
    // Shelly name + pencil: opens the existing Shelly-name dialog (no HA rename)
    const pen = iconButton("pencil", `Edit Shelly name of ${d.name}`, () => editValue(d, "name"), "pen"); pen.disabled = !editable(d);
    return [Object.assign(el("div", d.name, "name"), {title: d.name}), box("div", "sub shelly-name", el("span", d.shelly_name || "–"), pen)];
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
      line.tabIndex = 0; attachTip(line, () => full);
    }
    return [line, el("div", d.type || "–", "sub")];
  }},
  status: {label: "Status", sort: d => STATUS_LABELS[shownStatus(d)] || d.status, cell: d => [box("div", "status-cell", statusCell(d), ignoredPill(d))]},
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
// Sort order, hidden columns and column order – shared by all users, stored in the app
const prefs = {devices: defaultPrefs()};
let prefsLoaded = false;
const savePrefs = () => api("api/ui", prefs).catch(e => message("Columns", e.message, "error"));
/** Adopt stored prefs; unknown column IDs (older/newer versions) are discarded. */
function adoptPrefs(stored) {
  const known = k => DATA_COLUMNS.includes(k), base = defaultPrefs();
  const order = (Array.isArray(stored.order) ? stored.order : []).filter(known);
  const sort = Array.isArray(stored.sort) && COLUMNS[stored.sort[0]] && COLUMNS[stored.sort[0]].sort ? stored.sort : base.sort;
  prefs.devices = {sort, hidden: Array.isArray(stored.hidden) ? stored.hidden.filter(known) : base.hidden,
    order: [...new Set(order), ...DATA_COLUMNS.filter(k => !order.includes(k))]};
}
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
    if (col.sort) th.onclick = () => { prefs.devices.sort = [key, sKey === key && dir === "asc" ? "desc" : "asc"]; savePrefs(); render(true); };
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
/** Device tab: "Updates available" card on top, "All other devices" below. */
function renderDevices() {
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
  const rows = found.filter(FILTERS[ui.devFilter][1]);
  // Ignored devices form their own group, always last (known update stays visible there)
  const ignored = rows.filter(d => d.ignored), active = rows.filter(d => !d.ignored);
  const updates = active.filter(d => hasUpdate(d) || shownStatus(d) === "queued"), others = active.filter(d => !updates.includes(d));
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
function buildChooser() {
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
function syncChooser() { for (const cb of $("cols-devices").querySelectorAll("input")) cb.checked = !prefs.devices.hidden.includes(cb.dataset.col); }
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
  const busy = ui.state && ui.state.busy;
  add("Check for updates", () => checkDevice(d), !busy && !d.installing);
  if (installable(d, ui.state)) add("Install update", () => openInstall([d]), true);   // only for Ready
  // rc7: downgrade to a stored older version – Gen2+ only, also up to date / ignored devices
  if (d.gen >= 2) add("Install older version …", () => openDowngrade(d), downgradable(d, ui.state));
  add("Reload device info", () => reloadDevice(d), !busy && !d.installing && !d.activity && !!d.ip);
  if (busy) items.push(el("div", `Not available – ${busy}`, "menu-hint"));   // reason, also for touch
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
function closeRowMenu() { rowmenu.hidden = true; }
document.addEventListener("click", e => { if (!rowmenu.hidden && !rowmenu.contains(e.target) && !e.target.closest("td.c-menu")) closeRowMenu(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeRowMenu(); tip.hidden = true; } });
addEventListener("scroll", closeRowMenu, true);

// ---------------------------------------------------------------- Installation
/** Changelog versions as nodes: category tags, security highlighted, model entries marked. */
function changelogNodes(data) {
  if (!data || !data.available) return [el("p", "Changelog not available.", "hint")];
  if (!data.versions.length) return [el("p", "No changelog entries between the installed and the target version.", "hint")];
  const out = [];
  for (const v of data.versions) {
    out.push(el("div", v.date ? `${v.version} · ${v.date}` : v.version, "cl-version"));
    const ul = el("ul", null, "cl-list");
    for (const e of v.entries) {
      const li = el("li", null, e.this_model ? "this" : "");
      if (e.security) li.append(el("span", "Security", "tag sec"));
      if (e.category && e.category.toLowerCase() !== "security") li.append(el("span", e.category, "tag"));
      if (e.this_model) li.append(el("span", "This model", "tag model"));
      else if (e.model_specific) li.append(el("span", "Model-specific", "tag model"));
      li.append(e.text); ul.append(li);
    }
    out.push(ul);
  }
  return out;
}
/** Container that loads the changelog of one device. */
function changelogBox(d) {
  const c = box("div", null, el("p", "Loading changelog …", "hint"));
  api(`${devUrl(d)}/changelog`).then(r => c.replaceChildren(...changelogNodes(r))).catch(() => c.replaceChildren(...changelogNodes(null)));
  return c;
}
const GEN1_WARNING = "Gen1 firmware is offered by Shelly only via plain HTTP and without a checksum – its integrity and origin cannot be verified. Install only if you accept this risk.";
/** Confirmation dialog: one device with all details, several as an overview; approval bound to the SHA-256. */
function openInstall(devs) {
  const content = [];
  if (devs.length === 1) {
    const d = devs[0];
    content.push(valueList([["Device", d.name], ["Shelly", d.shelly_name], ["IP", d.ip], ["Generation", d.gen], ["Firmware ID", d.type],
      ["Model", d.model], ["Installed", d.version], ["Target", d.offer_version], ["Size", mb(d.size || 0)], ["SHA-256", d.sha256], ["Source", d.source_url]]));
    if (d.gen === 1) content.push(el("div", GEN1_WARNING, "warnbox"));
    content.push(el("h2", "Changelog"), changelogBox(d));
  } else {
    const t = el("table"), h = el("tr");
    for (const c of ["Device", "IP", "Installed", "Target"]) h.append(el("th", c));
    t.append(box("thead", null, h));
    for (const d of devs) { const r = el("tr"); for (const v of [d.name, d.ip, d.version, d.offer_version]) r.append(el("td", v ?? "–")); t.append(r); }
    content.push(t);
    if (devs.some(d => d.gen === 1)) content.push(el("div", GEN1_WARNING, "warnbox"));
    for (const d of devs) {
      const det = el("details"); det.append(el("summary", `Changelog – ${d.name}`));
      det.ontoggle = () => { if (det.open && det.children.length === 1) det.append(changelogBox(d)); };
      content.push(det);
    }
  }
  content.push(el("p", devs.length === 1 ? "The device restarts during the installation." : "The devices restart during the installation.", "hint"));
  modal(devs.length === 1 ? "Install update?" : `Install ${devs.length} updates?`, content, [{text: "Cancel"},
    {text: "Approve installation", kind: "danger", action: async () => {
      const errors = [];
      for (const d of devs) {   // each installation starts in the background – calls return at once
        try { await api(`${devUrl(d)}/install`, {sha256: d.sha256, confirm: true}); selected.delete(d.id); }
        catch (e) { errors.push(`${d.name}: ${e.message}`); }
      }
      errors.length ? message("Installation", errors.join("\n"), "error") : closeModal();
    }}]);
}
// ---------------------------------------------------------------- Downgrade (rc7)
// Flow: list of stored older versions -> installation dialog for the selected
// version -> second confirmation with acknowledgment (as for the device password).
const DOWNGRADE_WARNING = "A downgrade installs an older firmware. The device may refuse it, settings added by newer versions may be lost, and some devices may need a factory reset afterwards. Install only if you need this version.";
const UPLOAD_HINT = "An uploaded file does not come from the Shelly update server; its origin is only as trustworthy as its source.";
/** Label of a stored version for lists and dialogs. */
const fwSource = v => v.source === "upload" ? (v.sha_checked ? "Uploaded (SHA-256 checked)" : "Uploaded (SHA-256 not checked)") : "Downloaded from Shelly";
/** Step 1: choose one of the stored older versions. */
async function openDowngrade(d) {
  const title = `Install older version – ${d.name}`;
  modal(title, [el("p", "Loading stored versions …", "hint")], [{text: "Cancel"}]);
  let r;
  try { r = await api(`${devUrl(d)}/downgrades`); } catch (e) { message(title, e.message, "error"); return; }
  if (r.blocked) { message(title, r.blocked, "error"); return; }
  if (!r.versions.length) {
    message(title, `No stored version of ${r.type || d.type} is older than the installed version ${r.installed || "–"}. Older versions stay stored once newer ones were downloaded, or can be uploaded in the Firmware tab.`);
    return;
  }
  const list = el("div", null, "modes");
  r.versions.forEach((v, i) => {
    const radio = el("input"); radio.type = "radio"; radio.name = "dg-version"; radio.value = String(i); radio.checked = i === 0;
    const label = box("label", "check", radio, " ", el("span", v.version, "name"),
      el("span", ` · ${mb(v.size || 0)} · ${fmt(v.downloaded)} `, "sub"), v.source === "upload" ? el("span", "Uploaded", "tag warn") : null);
    list.append(label);
  });
  modal(title, [valueList([["Firmware ID", r.type], ["Installed", r.installed]]), el("p", "Stored older versions (newest first):"), list,
    el("p", "Newer versions are installed with “Install update”.", "hint")],
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    downgradeInstall(d, r.versions[Number(list.querySelector("input:checked").value)]);
  }}]);
}
/** Step 2: the known installation dialog, with the selected older version as target. */
function downgradeInstall(d, v) {
  const content = [valueList([["Device", d.name], ["Shelly", d.shelly_name], ["IP", d.ip], ["Generation", d.gen], ["Firmware ID", d.type],
    ["Model", d.model], ["Installed", d.version], ["Target (older)", v.version], ["Size", mb(v.size || 0)], ["SHA-256", v.sha256], ["Source", fwSource(v)]]),
  el("div", DOWNGRADE_WARNING, "warnbox")];
  if (v.source === "upload") content.push(el("div", UPLOAD_HINT, "warnbox"));
  content.push(el("p", "The device restarts during the installation.", "hint"));
  modal("Install older version?", content, [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => downgradeConfirm(d, v)}]);
}
/** Step 3: explicit confirmation; the red button needs the acknowledgment. */
function downgradeConfirm(d, v) {
  const title = "Install older version?", ack = el("input"); ack.type = "checkbox";
  modal(title, [el("p", `Install ${v.version} on ${d.name} (installed: ${d.version})?`), el("div", DOWNGRADE_WARNING, "warnbox"),
    box("label", "check", ack, " I understand that the downgrade may fail or reset settings")],
  [{text: "Cancel"}, {text: "Install older version", kind: "danger", action: async () => {
    try { await api(`${devUrl(d)}/install`, {sha256: v.sha256, confirm: true, downgrade: true, acknowledged: ack.checked}); closeModal(); }
    catch (e) { message(title, e.message, "error"); }
  }}]);
  const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
}
$("scan").onclick = () => api("api/scan", {}).then(() => load(true)).catch(e => message("Check", e.message, "error"));

// ---------------------------------------------------------------- Device settings
// Flow: read live values from the Shelly -> input -> (Shelly name: confirmation
// old -> new) -> send. NTP and AOP are saved directly; the dialog only stays open
// for errors, "nothing changed" or a restart notice.
const FIELDS = {name: {label: "Shelly name", prop: "shelly_name"}, ntp: {label: "NTP server", prop: "ntp"}};
const AOP_LABELS = {off: "Off", on: "On", restore_last: "Restore last state", last: "Restore last state",
  match_input: "Match input", switch: "Match input", toggle: "Toggle"};
const aopLabel = v => AOP_LABELS[v] || v || "–";

/** Read the live settings, then continue with next(live); errors are shown. */
async function withLive(d, title, next) {
  modal(title, [el("p", "Reading current values from the Shelly …", "hint")], [{text: "Cancel"}]);
  try { next(await api(`${devUrl(d)}/settings/refresh`, {})); }
  catch (e) { message(title, e.message, "error"); }
}
/** Send changes; quiet = close the dialog on success unless a restart is required. */
async function saveSettings(d, title, changes, quiet) {
  try {
    const r = await api(`${devUrl(d)}/settings`, changes);
    if (r.verified === false) {   // written, but the read-back failed: state unknown
      message(title, "Sent to the Shelly, but the new value could not be read back – please check the device.", "warnbox"); return;
    }
    if (quiet && !r.restart_required) { closeModal(); return; }
    message(title, r.restart_required ? "Saved. The Shelly reports that a restart is required to apply the change." : "Saved and read back from the Shelly.");
  } catch (e) { message(title, e.message, "error"); }
}
/** Edit the Shelly name (with old -> new confirmation) or the NTP server. */
function editValue(d, field) {
  const {label, prop} = FIELDS[field], title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    const input = el("input"); input.type = "text"; input.value = live[prop] || ""; input.maxLength = field === "name" ? 64 : 260;
    const old = live[prop] || "";
    modal(title, [box("label", "field", label, input)], [{text: "Cancel"}, {text: field === "name" ? "Next" : "Save", kind: "primary", action: () => {
      const value = input.value.trim();
      if (value === old) { message(title, "Nothing changed.", "error"); return; }
      if (field !== "name") { saveSettings(d, title, {[field]: value}, true); return; }
      modal(title, [el("p", `Write this change to ${d.name}?`), valueList([["Current", old || "–"], ["New", value || "–"]])],
        [{text: "Cancel"}, {text: "Apply", kind: "danger", action: () => saveSettings(d, title, {name: value}, false)}]);
    }}]);
    input.focus();
  });
}
/** Power-on action of all outputs. */
function editAop(d) {
  const title = `Power-on action – ${d.name}`;
  withLive(d, title, live => {
    if (!live.outputs.length) { message(title, "This device has no outputs with a power-on action."); return; }
    const t = el("table"), h = el("tr"), selects = [];
    for (const c of ["Output", "Action on power on"]) h.append(el("th", c));
    t.append(box("thead", null, h));
    for (const o of live.outputs) {
      const sel = el("select"); sel.setAttribute("aria-label", o.name);
      for (const v of o.options) { const opt = el("option", aopLabel(v)); opt.value = v; opt.selected = v === o.aop; sel.append(opt); }
      selects.push([o, sel]);
      t.append(box("tr", null, el("td", o.name === o.key ? o.key : `${o.name} (${o.key})`), box("td", null, sel)));
    }
    modal(title, [t], [{text: "Cancel"}, {text: "Save", kind: "primary", action: () => {
      const changed = selects.filter(([o, sel]) => sel.value !== o.aop);
      if (!changed.length) { message(title, "Nothing changed.", "error"); return; }
      saveSettings(d, title, {aop: Object.fromEntries(changed.map(([o, sel]) => [o.key, sel.value]))}, true);
    }}]);
  });
}
/** Eco mode: one confirmation. */
function editEco(d) {
  const title = `Eco mode – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live.eco !== "boolean") { message(title, "This device does not report an eco mode."); return; }
    const target = !live.eco, word = target ? "on" : "off";
    modal(title, [el("p", `Eco mode is currently ${live.eco ? "on" : "off"}. Turn it ${word} for ${d.name}?`),
      el("p", "The Shelly may restart to apply the change.", "hint")],
    [{text: "Cancel"}, {text: `Turn ${word}`, kind: "primary", action: () => saveSettings(d, title, {eco: target}, true)}]);
  });
}
/** Access point / Bluetooth: only the on/off switch, no further configuration. */
const SWITCHES = {ap: "Access point", ble: "Bluetooth"};
function editSwitch(d, key) {
  const label = SWITCHES[key], title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live[key] !== "boolean") { message(title, `This device does not offer a ${label.toLowerCase()} switch.`); return; }
    const target = !live[key], word = target ? "on" : "off";
    modal(title, [el("p", `${label} is currently ${live[key] ? "on" : "off"}. Turn it ${word} for ${d.name}?`),
      el("p", key === "ap" ? "Only the switch is changed; all other Wi-Fi settings stay as they are." : "Only the global Bluetooth switch is changed.", "hint")],
    [{text: "Cancel"}, {text: `Turn ${word}`, kind: "primary", action: () => saveSettings(d, title, {[key]: target}, true)}]);
  });
}
/** Restart: one confirmation (not logged). */
function restartDevice(d) {
  const title = `Restart – ${d.name}`;
  modal(title, [el("p", `Restart ${d.name} now?`), el("p", "The device is unavailable for a few seconds.", "hint")], [{text: "Cancel"},
    {text: "Restart", kind: "danger", action: async () => {
      try { await api(`${devUrl(d)}/restart`, {}); message(title, "Restart command sent."); }
      catch (e) { message(title, e.message, "error"); }
    }}]);
}
// Device password. Storing existing access credentials (this app only) is clearly
// separated from changing the protection on the device. The password is
// write-only: the server never returns it; every mode is confirmed here and
// checked again by the server.
const PASSWORD_WARNING = "Keep the password safe. If it is lost, access to the device may be locked. Recovery may require a factory reset; settings may be lost.";
const PASSWORD_MODES = {
  store: {label: "Store existing password", hint: "The device is already protected: test the password and store it in this app. Nothing is changed on the device.", input: true},
  enable: {label: "Enable protection", hint: "Sets a password on the device (user “admin”).", input: true, repeat: true, risky: true},
  change: {label: "Change password", hint: "Replaces the password on the device. The stored password is kept until the new one is verified.", input: true, repeat: true, risky: true},
  remove: {label: "Remove protection", hint: "Removes the password from the device. The stored password is deleted only after the device confirms."},
  forget: {label: "Forget stored password", hint: "Deletes the password stored in this app only. The device stays as it is."},
};
const PASSWORD_DONE = {store: "Password tested and stored.", enable: "Password protection enabled and verified.",
  change: "Password changed and verified.", remove: "Password protection removed.", forget: "Stored password deleted from this app."};
/** Modes that fit the reported protection state (unknown: all device modes). */
function passwordModes(d) {
  const p = d.auth_enabled, modes = [];
  if (p !== false) modes.push("store");
  if (p !== true) modes.push("enable");
  if (p !== false) modes.push("change", "remove");
  if (d.password_set) modes.push("forget");
  return modes;
}
function editPassword(d) {
  const title = `Device password – ${d.name}`, modes = passwordModes(d);
  const prot = d.auth_enabled === true ? "protected" : d.auth_enabled === false ? "not protected" : "unknown";
  const pw = el("input"), pw2 = el("input");
  for (const i of [pw, pw2]) { i.type = "password"; i.autocomplete = "new-password"; i.maxLength = d.gen === 1 ? 50 : 128; }
  const pwField = box("label", "field", "Password (user “admin”)", pw), pw2Field = box("label", "field", "Repeat password", pw2);
  const hint = el("p", null, "hint"), radios = el("div", null, "modes");
  const update = () => {
    const m = PASSWORD_MODES[radios.querySelector("input:checked").value];
    hint.textContent = m.hint; pwField.hidden = !m.input; pw2Field.hidden = !m.repeat;
  };
  modes.forEach((mode, i) => {
    const r = el("input"); r.type = "radio"; r.name = "pw-mode"; r.value = mode; r.checked = i === 0; r.onchange = update;
    radios.append(box("label", "check", r, " " + PASSWORD_MODES[mode].label));
  });
  modal(title, [
    valueList([["Device protection", prot], ["Stored in this app", d.password_set ? "yes" : "no"]]),
    radios, hint, pwField, pw2Field,
    el("p", d.gen === 1 ? "Gen1: the password is stored in plain text in the app's private storage (needed for Basic Auth); 1–50 characters." :
      "Gen2+: only a SHA-256 hash bound to this device is stored.", "hint"),
    el("div", PASSWORD_WARNING, "warnbox")],
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    const mode = radios.querySelector("input:checked").value, m = PASSWORD_MODES[mode], password = pw.value;
    if (m.input && !password) { message(title, "No password entered.", "error"); return; }
    if (m.repeat && password !== pw2.value) { message(title, "The passwords do not match.", "error"); return; }
    passwordConfirm(d, title, mode, m.input ? password : null);
  }}]);
  update();
}
/** Second step: explicit confirmation; enable/change additionally need the
 *  "stored securely" acknowledgment before the red button becomes active. */
function passwordConfirm(d, title, mode, password) {
  const m = PASSWORD_MODES[mode], content = [el("p", `${m.label} for ${d.name}?`), el("div", PASSWORD_WARNING, "warnbox")];
  const ack = el("input"); ack.type = "checkbox";
  if (m.risky) content.push(box("label", "check", ack, " I have stored the password securely"));
  const send = async () => {
    try {
      await api(`${devUrl(d)}/password`, {mode, password, confirm: true, acknowledged: m.risky ? ack.checked : false});
      message(title, PASSWORD_DONE[mode]);
    } catch (e) { message(title, e.message, "error"); }
  };
  modal(title, content, [{text: "Cancel"}, {text: mode === "store" ? "Test & save" : m.label, kind: mode === "forget" || mode === "store" ? "primary" : "danger", action: send}]);
  if (m.risky) {
    const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
  }
}

// ---------------------------------------------------------------- Firmware
/** Firmware tab: one card per firmware ID, versions as rows, newest first. */
function renderFirmware() {
  const q = $("fw-search").value.trim().toLowerCase();
  const list = ui.firmware.filter(f => !q || [f.type, f.version, ...f.used_by].some(v => String(v || "").toLowerCase().includes(q)));
  const total = ui.firmwareTotal;
  $("fw-summary").textContent = `${ui.firmware.length} file${ui.firmware.length === 1 ? "" : "s"} · ${mb(total)} stored`;
  const groups = new Map();
  for (const f of list) { if (!groups.has(f.type)) groups.set(f.type, []); groups.get(f.type).push(f); }
  const cards = [...groups.keys()].sort(collator.compare).map(type => {
    const files = groups.get(type).sort((a, b) => collator.compare(b.version || "", a.version || ""));
    const head = box("div", "card-head", el("h2", type), el("span", `Gen ${files[0].gen ?? "?"}`, "tag"),
      files.some(f => f.source !== "upload" && !f.https) ? el("span", "HTTP only", "tag warn") : null);
    const card = box("div", "card", head);
    files.forEach((f, i) => card.append(firmwareRow(f, i === 0)));
    return card;
  });
  if (!cards.length) cards.push(box("div", "card", el("div", ui.firmware.length ? "No firmware matches." : "No firmware stored.", "empty")));
  $("fw-cards").replaceChildren(...cards);
}
/** One version row with size, date, SHA (copy), devices and icon actions. */
function firmwareRow(f, latest) {
  const sha = box("span", "nowrap fw-sha", el("span", (f.sha256 || "").slice(0, 12) + "…", "mono"),
    iconButton("copy", "Copy SHA-256", async () => { await copyText(f.sha256 || ""); }));
  sha.title = f.sha256 || "";
  const devices = f.used_by.length
    ? keptDetails(`fw-${f.name}`, `For ${f.used_by.length} device${f.used_by.length === 1 ? "" : "s"} ▾`, box("ul", null, ...f.used_by.map(n => el("li", n))))
    : el("span", "–", "muted");
  devices.classList.add("fw-dev");
  const dl = el("a", null, "icon"); dl.href = `api/firmware/${encodeURIComponent(f.name)}/download`; dl.setAttribute("download", "");
  dl.title = "Download"; dl.setAttribute("aria-label", `Download ${f.type} ${f.version}`); dl.append(icon("download"));
  const del = iconButton("delete", f.deletable ? "Delete" : (f.locked || "Locked"), () => deleteFirmware(f), "red");
  del.disabled = !f.deletable;
  // Class names only place the cells on phones (grid areas); the desktop grid is unchanged
  const uploaded = f.source === "upload" ? el("span", "Uploaded", "tag warn") : null;
  if (uploaded) uploaded.title = fwSource(f);
  return box("div", "fw-row", box("div", "fw-ver", el("span", f.version, "name"), latest ? el("span", "latest", "tag primary") : null, uploaded),
    el("span", mb(f.size || 0), "nowrap fw-size"), el("span", fmt(f.downloaded), "nowrap sub fw-date"), sha, devices, box("div", "actions", dl, del));
}
/** Delete a stored firmware: dialog, then a second confirmation with
 *  acknowledgment (as for the device password, rc7). Nothing is deleted automatically. */
function deleteFirmware(f) {
  const title = "Delete firmware?", details = valueList([["Firmware ID", f.type], ["Version", f.version], ["Source", fwSource(f)], ["SHA-256", f.sha256]]);
  modal(title, [details, el("p", f.source === "upload" ? "An uploaded file can only be restored by uploading it again." :
    "Only versions that Shelly still offers can be downloaded again; older versions are gone for good.", "hint")],
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    const ack = el("input"); ack.type = "checkbox";
    modal(title, [el("p", `Delete ${f.type} ${f.version} permanently?`), box("label", "check", ack, " I understand that this file cannot be restored by the app")],
      [{text: "Cancel"}, {text: "Delete", kind: "danger", action: async () => {
        try { await api(`api/firmware/${encodeURIComponent(f.name)}/delete`, {confirm: true, acknowledged: ack.checked}); closeModal(); }
        catch (e) { message("Delete firmware", e.message, "error"); }
      }}]);
    const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
  }}]);
}
/** rc7: upload an older firmware file – step 1 choose and transfer, step 2 confirm
 *  firmware ID and version (pre-filled from the file where possible), optional SHA-256. */
function openUpload() {
  const title = "Upload firmware", input = el("input"); input.type = "file"; input.accept = ".zip,.bin,.gbl";
  modal(title, [box("label", "field", "Firmware file (.zip, .bin or .gbl, max. 64 MiB)", input),
    el("p", "Only for Gen2+ devices known to this app and stable versions. Use files from a source you trust.", "hint"), el("div", UPLOAD_HINT, "warnbox")],
  [{text: "Cancel"}, {text: "Upload", kind: "primary", action: async () => {
    const file = input.files && input.files[0];
    if (!file) { message(title, "No file selected.", "error"); return; }
    if (file.size > 64 * 1048576) { message(title, "Firmware larger than 64 MiB.", "error"); return; }
    modal(title, [el("p", `Uploading ${file.name} …`, "hint")], []);
    try { uploadConfirm(await uploadFile(file)); } catch (e) { message(title, e.message, "error"); }
  }}]);
}
function uploadConfirm(r) {
  const title = "Upload firmware";
  if (!r.types.length) { api("api/firmware/upload/cancel", {token: r.token}).catch(() => {}); message(title, "No Gen2+ device is known yet – run a check first.", "error"); return; }
  const type = el("select"); type.setAttribute("aria-label", "Firmware ID");
  for (const t of r.types) { const o = el("option", t); o.value = t; o.selected = t.toLowerCase() === String(r.detected.type || "").toLowerCase(); type.append(o); }
  const version = el("input"); version.type = "text"; version.maxLength = 14; version.placeholder = "e.g. 1.4.4"; version.value = r.detected.version || "";
  const sha = el("input"); sha.type = "text"; sha.maxLength = 64; sha.placeholder = "optional – compared with the file"; sha.spellcheck = false;
  const detected = r.detected.type || r.detected.version
    ? `Read from the file: ${r.detected.type || "no firmware ID"} · ${r.detected.version || "no version"} – please check.`
    : "The file contains no readable firmware ID or version – please enter them.";
  const content = [valueList([["File", r.filename], ["Size", mb(r.size)], ["SHA-256", r.sha256]]), el("p", detected, "hint"),
    box("label", "field", "Firmware ID", type), box("label", "field", "Version (stable, X.Y.Z)", version), box("label", "field", "Expected SHA-256", sha)];
  if (r.duplicate) content.push(el("div", `This file is already stored as ${r.duplicate}.`, "warnbox"));
  const error = el("p", null, "bad"); error.hidden = true; error.setAttribute("role", "alert"); content.push(error);
  const cancel = () => api("api/firmware/upload/cancel", {token: r.token}).catch(() => {}).then(closeModal);
  modal(title, content, [{text: "Cancel", action: cancel}, {text: "Store", kind: "primary", action: async () => {
    try {
      const res = await api("api/firmware/upload/confirm", {token: r.token, type: type.value, version: version.value.trim(), sha256: sha.value.trim()});
      toast(`${res.type} ${res.version} stored`); closeModal();
    } catch (e) {
      // The upload stays staged: show the error and let the user correct the values
      for (const b of $("dlg-btns").children) b.disabled = false;
      error.textContent = e.message; error.hidden = false;
    }
  }}]);
}
$("fw-upload").onclick = openUpload;
$("fw-search").oninput = () => render(true);

// ---------------------------------------------------------------- Logs
// Severity of an entry; entries written before 3.0 only have "ok"
const severity = e => ["success", "warning", "error"].includes(e.severity) ? e.severity : e.ok === false ? "error" : "success";
const SEVERITY_LABELS = {success: "Success", warning: "Warning", error: "Error"};
const LOG_FILTERS = {all: ["All", () => true], checks: ["Checks", e => e.kind === "check"],
  installs: ["Installations", e => e.kind === "install"], settings: ["Settings", e => ["setting", "password", "options"].includes(e.kind)],
  warnings: ["Warnings", e => severity(e) === "warning"], errors: ["Errors", e => severity(e) === "error"]};
const KIND_LABELS = {check: "Check", download: "Download", upload: "Upload", install: "Installation", delete: "Deleted", setting: "Setting",
  password: "Password", options: "App settings"};
// Successful actions (3.0), failed actions are logged with the mode; older entries: set/removed/test_failed
const PASSWORD_ACTIONS = {stored: "password stored", enabled: "password protection enabled", changed: "password changed",
  removed: "password protection removed", forgotten: "stored password forgotten",
  store: "storing the password failed", enable: "enabling protection failed", change: "changing the password failed",
  remove: "removing protection failed", forget: "forgetting the password failed",
  set: "device password set", test_failed: "device password test failed"};
/** Label of an app option for the log (key if unknown). */
const optionLabel = key => (OPTION_FIELDS.find(f => f[0] === key) || [key, key])[1];
const INSTALL_EVENTS = {started: "started", confirmed: "confirmed", unconfirmed: "not confirmed", failed: "failed"};
const TRIGGER_LABELS = {manual: "Manual", scheduled: "Scheduled", device: "Single-device"};
const arrow = (a, b) => `${a ?? "–"} → ${b ?? "–"}`;

/** Main line and optional details of one log entry. */
function logContent(e) {
  switch (e.kind) {
    case "check": {
      const line = el("div", `${TRIGGER_LABELS[e.trigger] || "Scheduled"} check · ${e.devices} device${e.devices === 1 ? "" : "s"} · ${e.duration} s · ${e.result}`);
      if (!e.findings || !e.findings.length) return [line];
      const ul = checkFindings(e);
      return [keptDetails(`log-${e.ts}-check`, line.textContent, ul)];
    }
    case "download":
      return [box("div", null, `Firmware downloaded: ${e.type} ${e.version} · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}… `,
        e.https === false ? el("span", "HTTP only", "tag warn") : null)];
    case "install":
      return [el("div", `${e.device}: ${e.downgrade ? "downgrade" : "installation"} ${INSTALL_EVENTS[e.event] || e.event} · ${arrow(e.old, e.new)}`), e.detail ? el("div", e.detail, "sub") : null].filter(Boolean);
    case "delete":
      return [el("div", `Firmware deleted: ${e.type} ${e.version}${e.source === "upload" ? " (uploaded)" : ""}`)];
    case "upload":
      return [el("div", `Firmware uploaded: ${e.type} ${e.version} · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}… · ${e.sha_checked ? "SHA-256 checked" : "SHA-256 not checked"}`)];
    case "setting":
      return [el("div", e.device), ...(e.changes || []).map(c => el("div", `${c.setting}: ${arrow(c.old, c.new)}`, "sub")), e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "password":
      return [el("div", `${e.device}: ${PASSWORD_ACTIONS[e.action] || e.action}`), e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "options":
      if (e.confirmed) return [el("div", "Download base URL confirmed as the address of Home Assistant")];
      return [el("div", e.ok ? "App settings saved – app restarted" : "App settings could not be saved"),
        ...(e.changes || []).map(c => el("div", c.redacted ? `${optionLabel(c.option)}: changed` : `${optionLabel(c.option)}: ${arrow(c.old, c.new)}`, "sub")),
        e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    default:
      return [el("div", e.kind)];
  }
}
/** Device results of a check, colored by severity. */
function checkFindings(e) {
  return box("ul", null, ...e.findings.map(f => {
    const sev = f.severity || (f.error ? "error" : "success");
    return el("li", f.error ? `${f.device}: ${f.error}` : `${f.device}: ${arrow(f.version, f.offer)}`, sev === "error" ? "bad" : sev === "warning" ? "warnc" : "");
  }));
}
/** Mobile: short summary lines of an entry (details stay collapsed). */
function logSummary(e) {
  switch (e.kind) {
    case "check": {
      const first = `${TRIGGER_LABELS[e.trigger] || "Scheduled"} · ${e.devices} device${e.devices === 1 ? "" : "s"} · ${e.duration} s`;
      const counts = typeof e.updates === "number"
        ? `${e.updates} update${e.updates === 1 ? "" : "s"} · ${e.warnings || 0} warning${e.warnings === 1 ? "" : "s"} · ${e.errors || 0} error${e.errors === 1 ? "" : "s"}`
        : e.result;   // entries before 3.0
      return [first, counts];
    }
    case "download": return [`${e.type} ${e.version}`];
    case "delete": return [`${e.type} ${e.version}`];
    case "upload": return [`${e.type} ${e.version}`];
    case "install": return [`${e.device}: ${e.downgrade ? "downgrade " : ""}${INSTALL_EVENTS[e.event] || e.event}`];
    case "setting": return [e.device];
    case "password": return [`${e.device}: ${PASSWORD_ACTIONS[e.action] || e.action}`];
    case "options": return [e.confirmed ? "Download base URL confirmed" : e.ok ? "App settings saved" : "App settings not saved"];
    default: return [e.kind];
  }
}
const mobileQuery = matchMedia("(max-width:700px)");
mobileQuery.addEventListener("change", () => render(true));
/** Desktop entry (arrangement unchanged): time, type, content, user. */
function logRow(e, d) {
  const sev = severity(e);
  return box("div", "log", el("span", `${pad(d.getHours())}:${pad(d.getMinutes())}`, "mono sub"),
    el("span", KIND_LABELS[e.kind] || e.kind, "tag" + (sev === "error" ? " sec" : "")),
    box("div", "log-body" + (sev === "error" ? " bad" : ""), el("span", SEVERITY_LABELS[sev], `tag sev-${sev}`), box("div", null, ...logContent(e))),
    el("span", e.user || "", "user"));
}
/** Mobile entry: time/type/outcome, short summary, details collapsed. */
function logTile(e, d) {
  const sev = severity(e), summary = logSummary(e);
  const head = box("div", "log-head", el("span", `${pad(d.getHours())}:${pad(d.getMinutes())}`, "mono sub"),
    el("span", KIND_LABELS[e.kind] || e.kind, "tag"), el("span", SEVERITY_LABELS[sev], `tag sev-${sev}`));
  const body = box("div", "log-details", ...(e.kind === "check" ? (e.findings && e.findings.length ? [checkFindings(e)] : [el("div", e.result, "sub")]) : logContent(e)),
    e.user ? el("div", `By ${e.user}`, "sub") : null);
  const det = el("details"), key = `logm-${e.ts}-${e.kind}`;
  det.open = openDetails.has(key);
  det.ontoggle = () => { det.open ? openDetails.add(key) : openDetails.delete(key); };
  const sum = el("summary"); sum.append(...summary.map(t => el("div", t)));
  det.append(sum, body);
  return box("div", "log-m", head, det);
}
/** Logs tab: grouped by day, filters, device search, quiet checks. */
function renderLogs() {
  const q = $("log-search").value.trim().toLowerCase(), quiet = $("log-quiet").checked;
  const devMatch = e => !q || [e.device, ...(e.findings || []).map(f => f.device)].some(v => String(v || "").toLowerCase().includes(q));
  const base = ui.logs.filter(e => devMatch(e) && !(quiet && e.kind === "check" && !(e.findings || []).length));
  $("log-chips").replaceChildren(...Object.entries(LOG_FILTERS).map(([key, [label, fn]]) => {
    const b = el("button", label, "chip" + (ui.logFilter === key ? " active" : ""));
    b.append(el("span", base.filter(fn).length, "count")); b.onclick = () => { ui.logFilter = key; render(true); };
    return b;
  }));
  const out = [];
  let day = null, card = null;
  for (const e of base.filter(LOG_FILTERS[ui.logFilter][1])) {
    const d = new Date(e.ts * 1000), key = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    if (key !== day) {
      day = key; out.push(el("div", `${key} · ${d.toLocaleDateString("en", {weekday: "long"})}`, "day"));
      card = el("div", null, "card"); out.push(card);
    }
    card.append(mobileQuery.matches ? logTile(e, d) : logRow(e, d));
  }
  if (!out.length) out.push(box("div", "card", el("div", ui.logs.length ? "No entries match." : "No log entries yet.", "empty")));
  $("log-list").replaceChildren(...out);
}
$("log-search").oninput = () => render(true);
$("log-quiet").onchange = () => render(true);

// ---------------------------------------------------------------- App settings
// All app options, grouped into cards. Saving goes through the Supervisor and
// restarts the app. [key, label, type, description, group]
const OPTION_GROUPS = ["Checks", "Firmware & storage", "Logs", "Advanced"];
const OPTION_FIELDS = [
  ["check_interval_days", "Check every (days)", "number", "1–30 days", "Checks"],
  ["check_time", "Check time", "time", "Local time of Home Assistant", "Checks"],
  ["download_base_url", "Download base URL for the Shellys", "text", "LAN IP address of Home Assistant + host port 8098, e.g. http://192.168.1.20:8098 (no host name). Required for installations", "Firmware & storage"],
  ["restrict_downloads_to_devices", "Serve downloads only to known Shellys", "bool", "Switch off if downloads are rejected from 172.30.x.x", "Firmware & storage"],
  ["gen1_updates_enabled", "Enable Gen1 updates", "bool", "Gen1 firmware is only available via plain HTTP without a checksum", "Firmware & storage"],
  ["gen1_allowed_types", "Allowed Gen1 types", "list", "Comma-separated, e.g. SHSW-1, SHSW-PM", "Firmware & storage"],
  ["log_entry_limit", "Log size (max. entries)", "number", "100–3000", "Logs"],
  ["log_retention_days", "Log retention (days)", "number", "1–365", "Logs"],
  ["max_parallel_installs", "Parallel installations (max.)", "number", "1–20; further approvals wait", "Advanced"],
  ["pinned_certificates", "Pinned certificates", "pins", "Host below shelly.cloud + SHA-256 fingerprint; several entries per host allowed", "Advanced"],
];
let settingsInputs = {};

// The Shellys download the firmware from download_base_url, so it must point to Home
// Assistant in the local network. Not enforced (the Supervisor schema only requires a
// URL); the Settings tab shows a hint while typing. No hint for private IPv4 ranges,
// IPv6 unique-local (fc00::/7) and link-local (fe80::/10). Any other IP address (and
// localhost) gets the "not local" hint; every host name (rc6, R2 – also .local, .lan,
// single-label names) gets the advice to use the IP: Shellys often cannot resolve names.
const LAN_IPV4 = /^(10\.\d{1,3}|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}$/;
const LAN_IPV6 = /^\[(f[cd][0-9a-f]{2}|fe[89ab][0-9a-f]):/i;
const ANY_IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;
const HINT_NOT_LOCAL = "This host is not in your local network. The Shellys download the firmware from this URL – use the LAN IP of Home Assistant.";
const HINT_HOST_NAME = "Use the LAN IP address of Home Assistant instead of a host name – Shellys often cannot resolve host names, the update then fails with a connection error.";
/** Hint text for the URL's host, else "" (also for invalid URLs: the server validates
 *  the format). */
function lanHint(value) {
  let host;
  try { host = new URL(value).hostname; } catch (e) { return ""; }
  if (!host || LAN_IPV4.test(host) || LAN_IPV6.test(host)) return "";
  if (ANY_IPV4.test(host) || host.startsWith("[") || host === "localhost") return HINT_NOT_LOCAL;
  return HINT_HOST_NAME;
}

/** Build the grouped settings cards with one common Save button. */
function renderSettings() {
  const o = ui.options; settingsInputs = {};
  if (!o) { $("settings-form").replaceChildren(box("div", "card", box("div", "card-body", el("p", "Loading …", "hint")))); return; }
  const cards = OPTION_GROUPS.map(group => {
    const form = el("div", null, "form");
    for (const [key, label, type, desc] of OPTION_FIELDS.filter(f => f[4] === group)) {
      const id = "opt-" + key, lab = el("label", label); lab.htmlFor = id;
      let input;
      if (type === "bool") { input = el("input"); input.type = "checkbox"; input.checked = !!o[key]; }
      else if (type === "number") { input = el("input"); input.type = "number"; input.value = o[key]; }
      else if (type === "time") { input = el("input"); input.type = "time"; input.step = 60; input.value = o[key] || ""; }
      else if (type === "list") { input = el("input"); input.type = "text"; input.value = (o[key] || []).join(", "); }
      else if (type === "pins") input = pinsEditor(o[key] || []);
      else { input = el("input"); input.type = "text"; input.value = o[key] ?? ""; }
      input.id = id; settingsInputs[key] = input;
      // One block per option: label, control and help text stay together. On wide
      // screens the block is "display: contents", so the label/control grid is unchanged.
      const help = el("div", desc, "desc"); help.id = id + "-help";
      input.setAttribute("aria-describedby", help.id);
      let warn = null;
      if (key === "download_base_url") {   // live hint while typing, no blocking
        warn = el("div", null, "desc warnc"); warn.id = id + "-warn"; warn.setAttribute("role", "status");
        const update = () => { warn.textContent = lanHint(input.value.trim()); warn.hidden = !warn.textContent; };
        input.addEventListener("input", update); update();
        input.setAttribute("aria-describedby", `${help.id} ${warn.id}`);
      }
      form.append(box("div", `set-field t-${type}`, lab, box("div", "set-control", input), help, warn));
    }
    return box("div", "card", box("div", "card-head", el("h2", group)), box("div", "card-body", form));
  });
  const save = el("button", "Save settings", "primary"); save.onclick = saveOptions;
  const dirty = el("span", "Unsaved changes", "unsaved"); dirty.hidden = true;
  const markDirty = () => { dirty.hidden = !Object.keys(optionChanges()).length; };
  const wrap = box("div", "settings-grid", ...cards);
  wrap.addEventListener("input", markDirty); wrap.addEventListener("change", markDirty); wrap.addEventListener("click", () => setTimeout(markDirty));
  $("settings-form").replaceChildren(wrap, box("div", "form-actions", dirty, save));   // common save area
}
/** Editable list of host + fingerprint rows. */
function pinsEditor(pins) {
  const wrap = el("div", null, "pins"), rows = el("div"); rows.style.flexDirection = "column";
  const addRow = (host = "", sha = "") => {
    const h = el("input"), s = el("input"); h.type = s.type = "text"; h.value = host; s.value = sha;
    h.placeholder = "host, e.g. updates.shelly.cloud"; s.placeholder = "SHA-256 AB:CD:…"; s.className = "mono";
    h.setAttribute("aria-label", "Host"); s.setAttribute("aria-label", "SHA-256 fingerprint");
    const row = box("div", null, h, s, iconButton("delete", "Remove entry", () => row.remove(), "red"));
    rows.append(row);
  };
  pins.forEach(p => addRow(p.host, p.sha256));
  const add = el("button", "Add entry"); add.type = "button"; add.onclick = () => addRow();
  wrap.append(rows, box("div", null, add));
  wrap.values = () => [...rows.children].map(r => { const [h, s] = r.querySelectorAll("input"); return {host: h.value.trim().toLowerCase(), sha256: s.value.trim()}; })
    .filter(p => p.host || p.sha256);
  return wrap;
}
/** Changed options compared with the loaded values. */
function optionChanges() {
  const o = ui.options, changes = {};
  for (const [key, , type] of OPTION_FIELDS) {
    const input = settingsInputs[key];
    let value;
    if (type === "bool") value = input.checked;
    else if (type === "number") value = Number(input.value);
    else if (type === "list") value = input.value.split(",").map(s => s.trim()).filter(Boolean).sort();
    else if (type === "pins") value = input.values();
    else value = input.value.trim();
    const old = type === "list" ? [...(o[key] || [])].sort() : type === "pins" ? o[key] || [] : type === "text" ? o[key] ?? "" : o[key];
    if (JSON.stringify(value) !== JSON.stringify(old)) changes[key] = value;
  }
  return changes;
}
/** Collect changed options, confirm, save and wait for the app restart. */
function saveOptions() {
  const changes = optionChanges();
  if (!Object.keys(changes).length) { message("Settings", "Nothing changed."); return; }
  const names = Object.keys(changes).map(k => OPTION_FIELDS.find(f => f[0] === k)[1]);
  modal("Save settings?", [el("p", "Changed: " + names.join(", ")), el("p", "The app restarts to apply the settings; this page reconnects automatically.", "hint")],
    [{text: "Cancel"}, {text: "Save & restart", kind: "primary", action: async () => {
      try {
        const r = await api("api/settings", changes);
        if (!r.restarting) { message("Settings", "Nothing changed."); return; }
        modal("Settings saved", [el("p", "The app is restarting …", "hint")], []);
        waitForRestart();
      } catch (e) { message("Settings", e.message, "error"); }
    }}]);
}
/** Poll until the restarted app answers again, then reload the page. */
function waitForRestart() {
  let wentDown = false;
  const started = Date.now();
  const probe = async () => {
    try { await api("api/state"); if (wentDown || Date.now() - started > 60000) { location.reload(); return; } }
    catch (e) { wentDown = true; }
    setTimeout(probe, 2000);
  };
  setTimeout(probe, 2000);
}

// ---------------------------------------------------------------- Rendering, tabs & polling
/** Render the active view; unchanged data is not re-rendered unless forced. */
function render(force) {
  const s = ui.state;
  if (!s) return;
  renderMeta(s);
  $("busy").textContent = s.storage_error ? `Storage error: ${s.storage_error}` : s.progress ? "" : s.busy || "";
  $("busy").classList.toggle("error", !!s.storage_error);
  renderProgress(s.progress);
  renderNotice(s.download_base || {});
  $("scan").disabled = !!s.busy;   // blocked during a check or installations
  const key = JSON.stringify([ui.view, mobileQuery.matches, s, ui.view === "firmware" ? ui.firmware : ui.view === "logs" ? ui.logs : null]);
  if (!force && key === ui.rendered) return;
  ui.rendered = key; tip.hidden = true;
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
  attachTip(cycle, () => `Next automatic check: ${next} (${sc.timezone})`); cycle.tabIndex = 0;
  $("meta").replaceChildren(el("span", `Last checked: ${fmt(s.last_scan)}`, "pill meta-pill"), cycle);
}
/** Notice while installations are disabled by the download base URL (rc6, R1): not set,
 *  or still the former example value that the admin has not confirmed yet. Rebuilt only
 *  when the problem changes, so polling does not steal the focus from its buttons. */
function renderNotice(info) {
  const n = $("notice"), key = info.issue ? `${info.issue}|${info.url}` : "";
  if (n.dataset.key === key) return;
  n.dataset.key = key; n.hidden = !info.issue;
  if (!info.issue) { n.replaceChildren(); return; }
  const buttons = el("div", null, "notice-btns");
  if (info.issue === "unconfirmed") {
    const yes = el("button", "This address is correct", "primary"); yes.onclick = () => confirmDownloadBase(info.url);
    buttons.append(yes);
  }
  const open = el("button", "Open settings"); open.onclick = () => showView("settings");
  buttons.append(open);
  n.replaceChildren(el("span", info.message), buttons);
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
  const parts = [p.kind === "device" ? `Checking ${p.device || "device"}` : "Checking devices"];
  if (p.kind !== "device" && p.total) parts.push(`${p.done}/${p.total} completed`);
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

let lastLogs = 0, timer = 0, inflight = false, queued = false;
const POLL_IDLE_MS = 30000, POLL_BUSY_MS = 3000;
/** Next refresh: fast while something runs, slow when idle, none while the page is hidden. */
function schedule() {
  clearTimeout(timer);
  if (document.hidden) return;
  const s = ui.state, active = s && (s.busy || s.progress || s.devices.some(d => d.installing || d.activity));
  timer = setTimeout(load, active ? POLL_BUSY_MS : POLL_IDLE_MS);
}
// Hidden tab / HA panel in background: no requests; visible again: refresh at once
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); else clearTimeout(timer); });
/** Fetch the state (and the data of the active tab) and render. */
async function load(force) {
  if (inflight) { queued = queued || force || "again"; return; }   // never two refreshes at once
  inflight = true;
  try {
    const s = await getState();
    if (!prefsLoaded) {   // stored table settings are adopted once; afterwards the page owns them
      if (s.ui && s.ui.devices) adoptPrefs(s.ui.devices);
      prefsLoaded = true; syncChooser();
    }
    if (ui.view === "firmware") { const f = await api("api/firmware"); ui.firmware = f.firmware; ui.firmwareTotal = f.total_size || 0; }
    if (ui.view === "logs" && (force || Date.now() - lastLogs > 30000)) { ui.logs = (await api("api/logs")).entries; lastLogs = Date.now(); }
    ui.state = s;
    render(force);
  } catch (e) { $("busy").textContent = "Error: " + e.message; }
  finally {
    inflight = false;
    if (queued) { const f = queued === true; queued = false; load(f); } else schedule();
  }
}
buildChooser();
load();
