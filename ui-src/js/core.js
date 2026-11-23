// Core of the Ingress UI: shared state, HA theme, API calls, DOM helpers, tooltip and dialogs.
import {load} from "./main.js";
import {t} from "./i18n.js";
export {t};

// Frontend of the Ingress UI.
// Security: all values are inserted via textContent (no innerHTML), so device
// names, changelogs or server responses cannot inject HTML/JS.
// Write calls send the CSRF token from the <meta> tag.
// Paths are relative so they work below the Ingress prefix.
const CSRF = document.querySelector("meta[name=csrf]").content;
// Inside the HA panel (Ingress iframe) HA shows the app name already: compact header on phones
if (window.parent !== window) document.documentElement.classList.add("embedded");
export const $ = id => document.getElementById(id);
export const ui = {state: null, firmware: [], firmwareTotal: 0, firmwareFree: null, logs: [], options: null, view: "devices",
  devFilter: "all", fwFilter: "all", logFilter: "all", rendered: "",
  backups: null,        // lists of the Backups tab (api/backups); null until fetched
  logsUnreadFrom: null, // Logs tab open: mark of the newest entry read before it was opened
  logsLoaded: 0};       // time of the last log fetch (0 = fetch at the next refresh)
export const selected = new Set();       // device IDs ticked for a (multi) installation
export const openDetails = new Set();    // keys of expanded <details>, kept across re-renders

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
/** Error of a refused request: the server text, the HTTP status in `status`. */
async function httpError(r) {
  const e = new Error(await r.text() || r.status); e.status = r.status;
  return e;
}
/** GET without body, otherwise POST with JSON and CSRF token; errors throw with the server text. */
export async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": CSRF}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (!r.ok) throw await httpError(r);
  return r.json();
}
// Home Assistant Ingress refuses request bodies above 16 MiB: files are sent in parts
// of at most this size (the server continues each part at the received size)
const UPLOAD_PART = 8 * 1048576;
/** Send a file as raw body in parts (firmware upload, store import – the only
 *  non-JSON writes); onProgress(sentBytes, totalBytes) after every part. Returns the
 *  answer to the last part; errors throw with the server text. */
export async function uploadFile(file, path = "api/firmware/upload", onProgress) {
  // Header values must be ASCII: other characters of the file name are replaced (display only)
  const name = file.name.replace(/[^\x20-\x7e]/g, "_").slice(0, 200);
  let transfer = null, answer = null;
  for (let offset = 0; offset < file.size || offset === 0; offset += UPLOAD_PART) {
    const headers = {"Content-Type": "application/octet-stream", "X-CSRF-Token": CSRF, "X-Filename": name,
      "X-Upload-Total": String(file.size), "X-Upload-Offset": String(offset)};
    if (transfer) headers["X-Upload-Transfer"] = transfer;
    const r = await fetch(path, {method: "POST", body: file.slice(offset, offset + UPLOAD_PART), headers});
    if (!r.ok) throw new Error(await r.text() || r.status);
    answer = await r.json(); transfer = answer.transfer || null;
    if (onProgress) onProgress(Math.min(offset + UPLOAD_PART, file.size), file.size);
    if (!transfer) break;
  }
  return answer;
}
let stateTag = null;
/** Device state; conditional request: an unchanged state (304) reuses the last one. */
export async function getState() {
  const r = await fetch("api/state", stateTag && ui.state ? {headers: {"If-None-Match": stateTag}} : {});
  if (r.status === 304) return ui.state;
  if (!r.ok) throw await httpError(r);
  stateTag = r.headers.get("ETag");
  return r.json();
}
/** Create an element with optional text (always textContent) and class. */
export function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = String(text); if (cls) e.className = cls; return e; }
/** Element with children. */
export function box(tag, cls, ...children) { const e = el(tag, null, cls); e.append(...children.filter(c => c != null)); return e; }
export const pad = n => String(n).padStart(2, "0");
/** YYYY-MM-DD HH:MM in local time. */
export function fmt(ts) {
  if (!ts) return t("never");
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export const mb = n => (n / 1048576).toFixed(1) + " MB";
export const devUrl = d => `api/devices/${encodeURIComponent(d.id)}`;
// Link to the Shelly web UI. The server only sends checked private IP addresses;
// the format is checked here once more.
const WEB_URL_RE = /^http:\/\/(\d{1,3}(\.\d{1,3}){3}|\[[0-9a-fA-F:]+\])(:\d{1,5})?\/$/;
export const webUrl = d => WEB_URL_RE.test(d.web_url || "") ? d.web_url : null;
export const collator = new Intl.Collator("en", {numeric: true, sensitivity: "base"});

// Inline icons: Material Design Icons (Apache 2.0)
const ICONS = {
  cloud: "M19.35,10.03C18.67,6.59 15.64,4 12,4C9.11,4 6.6,5.64 5.35,8.03C2.34,8.36 0,10.9 0,14A6,6 0 0,0 6,20H19A5,5 0 0,0 24,15C24,12.36 21.95,10.22 19.35,10.03Z",
  clock: "M12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22C6.47,22 2,17.5 2,12A10,10 0 0,1 12,2M12.5,7V12.25L17,14.92L16.25,16.15L11,13V7H12.5Z",
  info: "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z",
  dots: "M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z",
  restart: "M12,4C14.1,4 16.1,4.8 17.6,6.3C20.7,9.4 20.7,14.5 17.6,17.6C15.8,19.5 13.3,20.2 10.9,19.9L11.4,17.9C13.1,18.1 14.9,17.5 16.2,16.2C18.5,13.9 18.5,10.1 16.2,7.7C15.1,6.6 13.5,6 12,6V10.6L7,5.6L12,0.6V4M6.3,17.6C3.7,15 3.3,11 5.1,7.9L6.6,9.4C5.5,11.6 5.9,14.4 7.8,16.2C8.3,16.7 8.9,17.1 9.6,17.4L9,19.4C8,19 7.1,18.4 6.3,17.6Z",
  download: "M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z",
  delete: "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z",
  copy: "M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z",
  // Downgrade (Firmware tab, older versions)
  history: "M13.5,8H12V13L16.28,15.54L17,14.33L13.5,12.25V8M13,3A9,9 0 0,0 4,12H1L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3Z",
  // Toolbar tools: search (magnify) and sort (lines of falling length); sort directions
  search: "M9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.44,13.73L14.71,14H15.5L20.5,19L19,20.5L14,15.5V14.71L13.73,14.44C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 3,9.5A6.5,6.5 0 0,1 9.5,3M9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5Z",
  sort: "M3,13H15V11H3M3,6V8H21V6M3,18H9V16H3V18Z",
  // Filter tool of the toolbars (MDI "filter-variant")
  filter: "M6,13H18V11H6M3,6V8H21V6M10,18H14V16H10V18Z",
  // Device without usable address (MDI "alert", warning triangle)
  warn: "M13,14H11V10H13M13,18H11V16H13M1,21H23L12,2L1,21Z",
  up: "M13,20H11V8L5.5,13.5L4.08,12.08L12,4.16L19.92,12.08L18.5,13.5L13,8V20Z",
  down: "M11,4H13V16L18.5,10.5L19.92,11.92L12,19.84L4.08,11.92L5.5,10.5L11,16V4Z",
  // Message dialogs: success check and warning exclamation mark (errors use "close")
  check: "M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z",
  alert: "M10,3H14V14H10V3M10,21V17H14V21H10Z",
  close: "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z",
  // Action column: flash (power-on action), leaf, lock, access point, bluetooth, link (actions),
  // calendar, bug (websocket debug log), pencil
  eco: "M17,8C8,10 5.9,16.17 3.82,21.34L5.71,22L6.66,19.7C7.14,19.87 7.64,20 8,20C19,20 22,3 22,3C21,5 14,5.25 9,6.25C4,7.25 2,11.5 2,13.5C2,15.5 3.75,17.25 3.75,17.25C7,8 17,8 17,8Z",
  lock: "M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z",
  ap: "M4.93,4.93C3.12,6.74 2,9.24 2,12C2,14.76 3.12,17.26 4.93,19.07L6.34,17.66C4.89,16.22 4,14.22 4,12C4,9.79 4.89,7.78 6.34,6.34L4.93,4.93M19.07,4.93L17.66,6.34C19.11,7.78 20,9.79 20,12C20,14.22 19.11,16.22 17.66,17.66L19.07,19.07C20.88,17.26 22,14.76 22,12C22,9.24 20.88,6.74 19.07,4.93M7.76,7.76C6.67,8.85 6,10.35 6,12C6,13.65 6.67,15.15 7.76,16.24L9.17,14.83C8.45,14.11 8,13.11 8,12C8,10.89 8.45,9.89 9.17,9.17L7.76,7.76M16.24,7.76L14.83,9.17C15.55,9.89 16,10.89 16,12C16,13.11 15.55,14.11 14.83,14.83L16.24,16.24C17.33,15.15 18,13.65 18,12C18,10.35 17.33,8.85 16.24,7.76M12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10Z",
  bug: "M14,12H10V10H14M14,16H10V14H14M20,8H17.19C16.74,7.22 16.12,6.55 15.37,6.04L17,4.41L15.59,3L13.42,5.17C12.96,5.06 12.5,5 12,5C11.5,5 11.04,5.06 10.59,5.17L8.41,3L7,4.41L8.62,6.04C7.88,6.55 7.26,7.22 6.81,8H4V10H6.09C6.04,10.33 6,10.66 6,11V12H4V14H6V15C6,15.34 6.04,15.67 6.09,16H4V18H6.81C7.85,19.79 9.78,21 12,21C14.22,21 16.15,19.79 17.19,18H20V16H17.91C17.96,15.67 18,15.34 18,15V14H20V12H18V11C18,10.66 17.96,10.33 17.91,10H20V8Z",
  // Installation dialog title: firmware chip
  chip: "M6,4H18V5H21V7H18V9H21V11H18V13H21V15H18V17H21V19H18V20H6V19H3V17H6V15H3V13H6V11H3V9H6V7H3V5H6V4M11,15V18H12V15H11M13,15V18H14V15H13M15,15V18H16V15H15Z",
  upload: "M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z",
  ble: "M14.88,16.29L13,18.17V14.41M13,5.83L14.88,7.71L13,9.58M17.71,7.71L12,2H11V9.58L6.41,5L5,6.41L10.59,12L5,17.58L6.41,19L11,14.41V22H12L17.71,16.29L13.41,12L17.71,7.71Z",
  aop: "M7,2V13H10V22L17,10H13L17,2H7Z",
  actions: "M10.59,13.41C11,13.8 11,14.44 10.59,14.83C10.2,15.22 9.56,15.22 9.17,14.83C7.22,12.88 7.22,9.71 9.17,7.76L12.71,4.22C14.66,2.27 17.83,2.27 19.78,4.22C21.73,6.17 21.73,9.34 19.78,11.29L18.29,12.78C18.3,11.96 18.17,11.14 17.89,10.36L18.36,9.88C19.54,8.71 19.54,6.81 18.36,5.64C17.19,4.46 15.29,4.46 14.12,5.64L10.59,9.17C9.41,10.34 9.41,12.24 10.59,13.41M13.41,9.17C13.8,8.78 14.44,8.78 14.83,9.17C16.78,11.12 16.78,14.29 14.83,16.24L11.29,19.78C9.34,21.73 6.17,21.73 4.22,19.78C2.27,17.83 2.27,14.66 4.22,12.71L5.71,11.22C5.7,12.04 5.83,12.86 6.11,13.65L5.64,14.12C4.46,15.29 4.46,17.19 5.64,18.36C6.81,19.54 8.71,19.54 9.88,18.36L13.41,14.83C14.59,13.66 14.59,11.76 13.41,10.59C13,10.2 13,9.56 13.41,9.17Z",
  schedules: "M19,19H5V8H19M16,1V3H8V1H6V3H5C3.89,3 3,3.89 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5C21,3.89 20.1,3 19,3H18V1M17,12H12V17H17V12Z",
  // Changelog of a stored firmware (same document shape as the Logs tab icon)
  changelog: "M5,3C3.89,3 3,3.89 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5C21,3.89 20.1,3 19,3H5M5,5H19V19H5V5M7,7V9H17V7H7M7,11V13H17V11H7M7,15V17H14V15H7Z",
  // Settings tab, Firmware store card: export / import
  exportIcon: "M23,12L19,8V11H10V13H19V16M1,18V6C1,4.89 1.9,4 3,4H15A2,2 0 0,1 17,6V9H15V6H3V18H15V15H17V18A2,2 0 0,1 15,20H3A2,2 0 0,1 1,18Z",
  importIcon: "M14,12L10,8V11H2V13H10V16M20,18V6C20,4.89 19.1,4 18,4H6A2,2 0 0,0 4,6V9H6V6H18V18H6V15H4V18A2,2 0 0,0 6,20H18A2,2 0 0,0 20,18Z",
  // Battery device marker after the device name (outline with a half-full bar)
  battery: "M16,20H8V6H16M16.67,4H15V2H9V4H7.33A1.33,1.33 0 0,0 6,5.33V20.67C6,21.4 6.6,22 7.33,22H16.67A1.33,1.33 0 0,0 18,20.67V5.33C18,4.6 17.4,4 16.67,4M15,13H9V18H15V13Z",
  // Column chooser of the Devices tab (MDI "view-column-outline")
  columns: "M4,5V18H21V5H4M14,7V16H11V7H14M6,7H9V16H6V7M19,16H16V7H19V16Z",
  plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
  // Backups: tab, "Back up now", restore dialogs (MDI "backup-restore")
  backup: "M12,3A9,9 0 0,0 3,12H0L4,16L8,12H5A7,7 0 0,1 12,5A7,7 0 0,1 19,12A7,7 0 0,1 12,19C10.5,19 9.09,18.5 7.94,17.7L6.5,19.14C8.04,20.3 9.94,21 12,21A9,9 0 0,0 21,12A9,9 0 0,0 12,3M14,12A2,2 0 0,0 12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12Z",
  play: "M8,5.14V19.14L19,12.14L8,5.14Z",
  pencil: "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z",
};
/** Inline SVG icon from a path. */
export function icon(name) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), path = document.createElementNS(ns, "path");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true"); svg.classList.add("svg");
  path.setAttribute("d", ICONS[name]); svg.append(path); return svg;
}
/** Round icon button with accessible label; the label is its tooltip. */
export function iconButton(name, label, onclick, cls) {
  const b = el("button", null, "icon" + (cls ? " " + cls : "")); setTip(b, label); b.setAttribute("aria-label", label);
  b.append(icon(name)); if (onclick) b.onclick = onclick; return b;
}
/** Settings kept per end device (browser storage); null if missing or unreadable. */
export function loadLocal(key) {
  try { return JSON.parse(localStorage.getItem(`shelly-firmware-manager:${key}`) || "null"); }
  catch (e) { return null; }   // storage blocked or damaged: defaults
}
/** Store a setting per end device; without browser storage it lasts for this page only. */
export function saveLocal(key, value) {
  try { localStorage.setItem(`shelly-firmware-manager:${key}`, JSON.stringify(value)); } catch (e) { /* page only */ }
}
// ---- Toolbar tools
// Search, sort and filter are small round icons at the start of a toolbar; a click opens a
// popover below the icon (phones: full width below the first toolbar row). Only one
// popover is open at a time; a click outside it or Escape closes it.
/** Close every open toolbar popover. */
function closeTools() {
  for (const t of document.querySelectorAll(".tool.open")) {
    t.classList.remove("open"); t.querySelector(".tool-pop").hidden = true;
    t.querySelector(".tool-btn").setAttribute("aria-expanded", "false");
  }
}
/** Round toolbar icon with a popover holding content. Returns {node, button}. */
export function toolButton(name, label, ...content) {
  const b = iconButton(name, label, null, "tool-btn"); b.type = "button";
  b.setAttribute("aria-haspopup", "true"); b.setAttribute("aria-expanded", "false");
  const pop = box("div", "tool-pop", ...content); pop.hidden = true;
  const wrap = box("div", "tool", b, pop);
  b.onclick = () => {
    const opening = pop.hidden;
    closeTools(); hideTip();
    if (!opening) return;
    wrap.classList.add("open"); pop.hidden = false; b.setAttribute("aria-expanded", "true");
    const first = pop.querySelector("input,[aria-checked=true],button"); if (first) first.focus();
  };
  return {node: wrap, button: b};
}
document.addEventListener("click", e => { if (!e.target.closest(".tool")) closeTools(); });
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  const open = document.querySelector(".tool.open .tool-btn");
  if (open) { closeTools(); open.focus(); }
});
/** Search tool: moves the given search input into the popover of a search icon (in
 *  place of the input); a dot on the icon marks a set search text. Enter closes it. */
export function searchTool(input) {
  const parent = input.parentNode, next = input.nextSibling;
  const tool = toolButton("search", input.getAttribute("aria-label") || t("Search"), input);
  parent.insertBefore(tool.node, next);
  const mark = () => tool.button.classList.toggle("has-value", !!input.value.trim());
  input.addEventListener("input", mark);
  // preventDefault: the key press would otherwise reach the focused icon and reopen it
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); closeTools(); tool.button.focus(); } });
  mark();
}
/** Sort tool of a toolbar: popover with the fields and the direction (Ascending /
 *  Descending). fields: [[key, label], …]; onChange([key, dir]) is called on every
 *  change. Returns the node and set([key, dir]) to show the current order. */
export function sortControl(fields, onChange) {
  let current = [fields[0][0], "asc"];
  const item = (text, value, pick) => {
    const b = el("button", text, "sort-item"); b.type = "button"; b.role = "menuitemradio"; b.dataset.value = value;
    b.onclick = () => onChange(pick()); return b;
  };
  const byField = fields.map(([key, label]) => item(label, key, () => [key, current[1]]));
  const byDir = [["asc", t("Ascending"), "up"], ["desc", t("Descending"), "down"]].map(([dir, label, arrow]) => {
    const b = item(label, dir, () => [current[0], dir]); b.prepend(icon(arrow)); return b;
  });
  const menu = box("div", "sort-menu", el("div", t("Sort by"), "pop-title"), ...byField, el("hr"), ...byDir);
  menu.role = "menu";
  const tool = toolButton("sort", t("Sort"), menu);
  const set = value => {
    current = value;
    for (const b of byField) b.setAttribute("aria-checked", String(b.dataset.value === value[0]));
    for (const b of byDir) b.setAttribute("aria-checked", String(b.dataset.value === value[1]));
    const field = (fields.find(f => f[0] === value[0]) || fields[0])[1];
    const text = t("Sort: {field} · {direction}", {field, direction: value[1] === "desc" ? t("Descending") : t("Ascending")});
    setTip(tool.button, text); tool.button.setAttribute("aria-label", text);
  };
  set(current);
  return {node: tool.node, set};
}
/** Filter tool of a toolbar: popover "Filter" with one entry per filter (label, count
 *  right, check mark at the chosen one, optional tooltip on hover/focus) – look and
 *  behaviour of the sort tool; a dot on the icon while another filter than the first
 *  ("All") is chosen. items: [[key, label, tip?], …] (tip: function returning the
 *  text or null); onChange(key) is called on a choice. Returns the node and
 *  set(key, counts) to show the choice and the counts ({key: number}). */
export function filterControl(items, onChange) {
  const buttons = items.map(([key, label, tip]) => {
    const b = el("button", null, "sort-item filter-item"); b.type = "button"; b.role = "menuitemradio"; b.dataset.value = key;
    b.append(el("span", label), el("span", null, "count"));
    if (tip) attachTip(b, tip);
    b.onclick = () => onChange(key); return b;
  });
  const menu = box("div", "sort-menu", el("div", t("Filter"), "pop-title"), ...buttons);
  menu.role = "menu";
  const tool = toolButton("filter", t("Filter"), menu);
  const set = (current, counts) => {
    for (const b of buttons) {
      b.setAttribute("aria-checked", String(b.dataset.value === current));
      b.lastChild.textContent = String(counts[b.dataset.value] ?? "");
    }
    const label = (items.find(i => i[0] === current) || items[0])[1];
    tool.button.classList.toggle("has-value", current !== items[0][0]);
    const text = t("Filter: {label}", {label});
    setTip(tool.button, text); tool.button.setAttribute("aria-label", text);
  };
  return {node: tool.node, set};
}
/** <details> whose open state survives re-rendering. */
export function keptDetails(key, summary, content) {
  const d = el("details"); d.open = openDetails.has(key);
  d.ontoggle = () => { d.open ? openDetails.add(key) : openDetails.delete(key); };
  d.append(el("summary", summary), content); return d;
}
/** Copy text to the clipboard. True if copied.
 *  The Clipboard API is missing or refused outside a secure context (e.g. Home
 *  Assistant over plain HTTP) and in frames without clipboard permission; then a
 *  selected helper field is copied. An open modal dialog makes everything outside it
 *  inert (no selection possible), so the helper field is placed inside the dialog. */
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fallback below */ }
  const helper = el("textarea", null, "copy-helper"); helper.value = text; helper.readOnly = true;
  const focused = document.activeElement;
  (dlg.open ? dlg : document.body).append(helper);
  helper.focus(); helper.select(); helper.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  helper.remove(); if (focused instanceof HTMLElement) focused.focus();
  return ok;
}

// ---------------------------------------------------------------- Tooltip
const tip = $("tip");
let tipOwner = null;              // node whose tooltip is shown
let tipTapped = false;            // shown by a tap (a second tap hides it; hover does not count)
// Type of the last pointerdown ("mouse", "touch", "pen"): a following click is a tap
// if it was not the mouse (click.pointerType is not reported by every browser)
let lastPointer = "mouse";
const isTap = () => lastPointer !== "mouse";
/** Show text as tooltip below (or above) node; tapped = shown by a touch or pen tap. */
function showTip(node, t, tapped = false) {
  if (!t) return;
  tip.textContent = t; tip.hidden = false; tipOwner = node; tipTapped = tapped;
  const r = node.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(4, Math.min(r.left, innerWidth - w - 4)) + "px";
  tip.style.top = (r.bottom + h + 8 > innerHeight ? r.top - h - 6 : r.bottom + 6) + "px";
}
/** Hide the tooltip. */
export function hideTip() { tip.hidden = true; tipOwner = null; }
/** Tooltip on mouse hover and keyboard focus; text() is evaluated when shown. Focus
 *  only shows it when focus came from the keyboard (:focus-visible).
 *  tap = the node has no action of its own: a touch or pen tap toggles the tooltip
 *  (and does nothing else – e.g. a label does not focus its control). Without tap a
 *  tap only runs the node's action (opens a popup, a link …) and shows no tooltip. */
export function attachTip(node, text, tap = false) {
  node.addEventListener("pointerenter", e => { if (e.pointerType === "mouse") showTip(node, text()); });
  node.addEventListener("focus", () => { if (node.matches(":focus-visible")) showTip(node, text()); });
  node.addEventListener("pointerleave", e => { if (e.pointerType === "mouse" && tipOwner === node) hideTip(); });
  node.addEventListener("blur", () => { if (tipOwner === node) hideTip(); });
  if (!tap) { node.addEventListener("pointerdown", e => { if (e.pointerType !== "mouse") hideTip(); }); return; }
  node.addEventListener("click", e => {
    // Controls inside the node (copy, pencil …) keep their own action
    const control = e.target.closest("button,a,input,select,textarea");
    if (!isTap() || (control && control !== node && node.contains(control))) return;
    e.preventDefault();
    if (tipOwner === node && tipTapped && !tip.hidden) hideTip(); else showTip(node, text(), true);
  });
}
// The app's own tooltip replaces the browser's (title attribute) everywhere: it looks the
// same on every element, shows at once and also works on touch devices.
const tipped = new WeakSet();     // nodes with an attached tooltip of setTip
/** Tooltip text of a node in place of a title (icon buttons, toolbar tools, "Install",
 *  links, column heads): shown on mouse hover and keyboard focus; a tap runs the node's
 *  action as before. "" removes the text. A disabled button shows it on a tap too. */
export function setTip(node, text) {
  node.dataset.tip = text || "";
  if (!tipped.has(node)) { tipped.add(node); attachTip(node, () => node.dataset.tip); }
}
// Touch: a tap elsewhere and scrolling hide the tooltip. A tap on a disabled button
// shows its tooltip (the reason why it is disabled): it has no action and receives no
// click, but pointer events.
document.addEventListener("pointerdown", e => {
  lastPointer = e.pointerType || "mouse";
  if (isTap() && tipOwner && !tipOwner.contains(e.target)) hideTip();
}, true);
document.addEventListener("pointerup", e => {
  const b = e.pointerType !== "mouse" && e.target.closest && e.target.closest("button:disabled[data-tip]");
  if (b && b.dataset.tip) showTip(b, b.dataset.tip, true);
}, true);
addEventListener("scroll", () => { if (isTap()) hideTip(); }, true);
$("fw-info").append(icon("info"));
attachTip($("fw-info"), () => $("fw-info").dataset.tip);

// ---------------------------------------------------------------- Keyboard focus
// The lists are redrawn as a whole. The node that had the keyboard focus is replaced by
// a new one then, so the focus is put on the node that took its place: found by its row
// (data-key of the row), its tag and its label.
/** What a control is recognised by after a redraw: its accessible name, else its text. */
const focusLabel = node => node.getAttribute("aria-label") ?? node.textContent.trim().slice(0, 80);
/** Where a node stands below root: its row (nearest data-key), tag, label and place
 *  among the nodes of that tag and label in the row (without a row: in root). */
function focusMark(root, node) {
  const row = node.closest("[data-key]"), scope = row && root.contains(row) ? row : root, label = focusLabel(node);
  const same = [...scope.querySelectorAll(node.tagName)].filter(n => focusLabel(n) === label);
  return {key: scope === root ? null : row.dataset.key, tag: node.tagName, label, nth: same.indexOf(node)};
}
/** The node standing where the marked one stood (null: its row or the node is gone). */
function focusTarget(root, mark) {
  const scope = mark.key == null ? root : [...root.querySelectorAll("[data-key]")].find(n => n.dataset.key === mark.key);
  if (!scope) return null;
  const same = [...scope.querySelectorAll(mark.tag)].filter(n => focusLabel(n) === mark.label);
  return same[mark.nth] || same[0] || null;
}
/** Redraw something below root with draw() and keep the keyboard focus where it was.
 *  A tooltip that was not shown before does not appear by the returning focus. */
export function keepFocus(root, draw) {
  const active = document.activeElement;
  const mark = active && active !== root && root.contains(active) ? focusMark(root, active) : null, tipShown = tipOwner === active;
  draw();
  if (!mark || root.contains(document.activeElement)) return;   // nothing to keep, or the redraw set the focus itself
  const node = focusTarget(root, mark);
  if (!node) return;
  node.focus({preventScroll: true});
  if (!tipShown) hideTip();
}

// ---------------------------------------------------------------- Dialog
// One dialog for everything: title + close, content, buttons bottom right.
// buttons: [{text, kind: "primary"|"danger"|"", action}] – a button without
// action closes the dialog; actions decide themselves (close or new content).
// Keyboard and screen readers: the dialog is named by its title; a new dialog and every
// new step start with the focus on the title (a dialog may then focus a field of its
// own), so the focus never falls back to the page behind it. Closing returns the focus
// to where it was.
const dlg = $("dlg");
let opener = null;   // where the focus was when the dialog opened: {root, mark} (see keepFocus)
/** Open (or replace the content of) the dialog. titleIcon: optional icon name before
 *  the title; its colour comes from the class ti-<name> (style.css). */
export function modal(title, content, buttons = [{text: t("Close")}], titleIcon) {
  const head = $("dlg-title");
  head.replaceChildren(el("span", title));
  if (titleIcon) { const i = icon(titleIcon); i.classList.add("ti", "ti-" + titleIcon); head.prepend(i); }
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
  const opening = !dlg.open;
  if (opening) {
    const active = document.activeElement, root = active && active.closest("section");
    opener = root ? {root, mark: focusMark(root, active)} : null;
    lockPage(true); dlg.showModal();
  }
  // A step replaced the button that had the focus: the focus stays inside the dialog
  if (opening || !dlg.contains(document.activeElement)) head.focus({preventScroll: true});
}
/** Close the dialog and refresh. */
export function closeModal() { if (dlg.open) dlg.close(); load(); }
/** True while the dialog is open. */
export const modalOpen = () => dlg.open;
/** The page behind an open dialog does not scroll (the dialog's own content does). The
 *  width the hidden scrollbar took is kept as padding, so the page does not jump. */
function lockPage(on) {
  const root = document.documentElement;
  document.body.style.paddingRight = on ? `${Math.max(0, innerWidth - root.clientWidth)}px` : "";
  root.classList.toggle("dlg-open", on);
}
// Every way of closing ends the lock: a button, the X and the Escape key. The browser
// returns the focus to the node that had it; if the list was redrawn meanwhile, the node
// in its place gets it.
dlg.addEventListener("close", () => {
  lockPage(false);
  const from = opener; opener = null;
  if (!from || (document.activeElement && document.activeElement !== document.body)) return;
  const node = focusTarget(from.root, from.mark); if (node) node.focus({preventScroll: true});
});
$("dlg-x").append(icon("close"));
$("dlg-x").onclick = closeModal;
// Icon of a message kind; "info" (neutral notes such as "Nothing changed.") has none
const MESSAGE_ICONS = {success: "check", warning: "alert", error: "close"};
/** Message dialog. kind: "info" (default, no icon), "success", "warning" or "error" –
 *  the last three show a large round status icon left of the text. */
export function message(title, text, kind = "info", titleIcon) {
  modal(title, [messageBody(text, kind)], undefined, titleIcon);
}
/** Content of a message dialog (for dialogs that add more below it). Screen readers
 *  announce it: an error or warning at once (alert), a success when idle (status). */
export function messageBody(text, kind = "info") {
  const name = MESSAGE_ICONS[kind];
  if (!name) return el("p", text, "hint");
  const node = box("div", `msg msg-${kind}`, box("span", "msg-icon", icon(name)), el("p", text));
  node.setAttribute("role", kind === "success" ? "status" : "alert");
  return node;
}
// Short, non-blocking feedback (e.g. Reload Device Info – no popup)
let toastTimer = 0;
export function toast(text, warn) {
  const node = $("toast"); node.textContent = text; node.classList.toggle("warn", !!warn); node.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; }, warn ? 8000 : 4000);
}
/** Two-column value list: [label, value, optional value class]; rows without a
 *  value (null or "") are left out. cls: extra class of the list (e.g. the phone tile). */
export function valueList(pairs, cls) {
  const dl = el("dl", null, "kv" + (cls ? " " + cls : ""));
  for (const [k, v, vc] of pairs) if (v != null && v !== "") dl.append(el("dt", k), el("dd", v, vc || ""));
  return dl;
}
/** Titled block of a dialog (small uppercase heading). */
export const section = (title, ...nodes) => box("section", "dsec", el("h3", title), ...nodes);
/** SHA-256 row of a value list (small, monospace). */
export const shaRow = sha => ["SHA-256", sha, "mono sha"];
