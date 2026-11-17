// Core of the Ingress UI: shared state, HA theme, API calls, DOM helpers, tooltip and dialogs.
import {load} from "./main.js";

// Frontend of the Ingress UI.
// Security: all values are inserted via textContent (no innerHTML), so device
// names, changelogs or server responses cannot inject HTML/JS.
// Write calls send the CSRF token from the <meta> tag.
// Paths are relative so they work below the Ingress prefix.
const CSRF = document.querySelector("meta[name=csrf]").content;
// Inside the HA panel (Ingress iframe) HA shows the app name already: compact header on phones
if (window.parent !== window) document.documentElement.classList.add("embedded");
export const $ = id => document.getElementById(id);
export const ui = {state: null, firmware: [], firmwareTotal: 0, logs: [], options: null, view: "devices",
  devFilter: "all", logFilter: "all", rendered: "",
  logsLoaded: 0,        // time of the last log fetch (0 = fetch at the next refresh)
  prefsLoaded: false};  // stored table settings were adopted
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
/** GET without body, otherwise POST with JSON and CSRF token; errors throw with the server text. */
export async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": CSRF}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(await r.text() || r.status);
  return r.json();
}
/** Send a firmware file as raw body (the only non-JSON write); errors throw with the server text. */
export async function uploadFile(file) {
  const r = await fetch("api/firmware/upload", {method: "POST", body: file,
    // Header values must be ASCII: other characters of the file name are replaced (display only)
    headers: {"Content-Type": "application/octet-stream", "X-CSRF-Token": CSRF,
      "X-Filename": file.name.replace(/[^\x20-\x7e]/g, "_").slice(0, 200)}});
  if (!r.ok) throw new Error(await r.text() || r.status);
  return r.json();
}
let stateTag = null;
/** Device state; conditional request: an unchanged state (304) reuses the last one. */
export async function getState() {
  const r = await fetch("api/state", stateTag && ui.state ? {headers: {"If-None-Match": stateTag}} : {});
  if (r.status === 304) return ui.state;
  if (!r.ok) throw new Error(await r.text() || r.status);
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
  if (!ts) return "never";
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
  info: "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z",
  dots: "M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z",
  restart: "M12,4C14.1,4 16.1,4.8 17.6,6.3C20.7,9.4 20.7,14.5 17.6,17.6C15.8,19.5 13.3,20.2 10.9,19.9L11.4,17.9C13.1,18.1 14.9,17.5 16.2,16.2C18.5,13.9 18.5,10.1 16.2,7.7C15.1,6.6 13.5,6 12,6V10.6L7,5.6L12,0.6V4M6.3,17.6C3.7,15 3.3,11 5.1,7.9L6.6,9.4C5.5,11.6 5.9,14.4 7.8,16.2C8.3,16.7 8.9,17.1 9.6,17.4L9,19.4C8,19 7.1,18.4 6.3,17.6Z",
  download: "M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z",
  delete: "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z",
  copy: "M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z",
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
  pencil: "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z",
};
/** Inline SVG icon from a path. */
export function icon(name) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), path = document.createElementNS(ns, "path");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true"); svg.classList.add("svg");
  path.setAttribute("d", ICONS[name]); svg.append(path); return svg;
}
/** Round icon button with accessible label. */
export function iconButton(name, label, onclick, cls) {
  const b = el("button", null, "icon" + (cls ? " " + cls : "")); b.title = label; b.setAttribute("aria-label", label);
  b.append(icon(name)); if (onclick) b.onclick = onclick; return b;
}
/** <details> whose open state survives re-rendering. */
export function keptDetails(key, summary, content) {
  const d = el("details"); d.open = openDetails.has(key);
  d.ontoggle = () => { d.open ? openDetails.add(key) : openDetails.delete(key); };
  d.append(el("summary", summary), content); return d;
}
/** Copy text to the clipboard; fallback for non-secure contexts. */
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return; } catch (e) { /* fallback below */ }
  const t = el("textarea"); t.value = text; document.body.append(t); t.select();
  document.execCommand("copy"); t.remove();
}

// ---------------------------------------------------------------- Tooltip
export const tip = $("tip");
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
// Touch: a tap elsewhere and scrolling hide the tooltip. A tap on a disabled button
// shows its title (the reason why it is disabled): it has no action and receives no
// click, but pointer events.
document.addEventListener("pointerdown", e => {
  lastPointer = e.pointerType || "mouse";
  if (isTap() && tipOwner && !tipOwner.contains(e.target)) hideTip();
}, true);
document.addEventListener("pointerup", e => {
  const b = e.pointerType !== "mouse" && e.target.closest && e.target.closest("button:disabled[title]");
  if (b) showTip(b, b.title, true);
}, true);
addEventListener("scroll", () => { if (isTap()) hideTip(); }, true);
$("fw-info").append(icon("info"));
attachTip($("fw-info"), () => $("fw-info").dataset.tip);

// ---------------------------------------------------------------- Dialog
// One dialog for everything: title + close, content, buttons bottom right.
// buttons: [{text, kind: "primary"|"danger"|"", action}] – a button without
// action closes the dialog; actions decide themselves (close or new content).
const dlg = $("dlg");
/** Open (or replace the content of) the dialog. titleIcon: optional icon name before
 *  the title; its colour comes from the class ti-<name> (style.css). */
export function modal(title, content, buttons = [{text: "Close"}], titleIcon) {
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
  if (!dlg.open) dlg.showModal();
}
/** Close the dialog and refresh. */
export function closeModal() { if (dlg.open) dlg.close(); load(); }
$("dlg-x").append(icon("close"));
$("dlg-x").onclick = closeModal;
export const message = (title, text, cls = "hint", titleIcon) => modal(title, [el("p", text, cls)], undefined, titleIcon);
// Short, non-blocking feedback (e.g. Reload Device Info – no popup)
let toastTimer = 0;
export function toast(text, warn) {
  const t = $("toast"); t.textContent = text; t.classList.toggle("warn", !!warn); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, warn ? 8000 : 4000);
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
