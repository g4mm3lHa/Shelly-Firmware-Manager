// Overview parts of the Devices tab and its dialogs: the Home Assistant areas (filter
// tool, choice of areas in schedules and "Back up now"), the strip "Security" and the
// device list as CSV file.
import {attachTip, box, checkbox, collator, el, icon, radio, setTip, t, tn, toolButton, ui} from "./core.js";
import {render} from "./main.js";

// ---------------------------------------------------------------- Areas
// The area of a device comes from the area registry of Home Assistant (device field area,
// null: none). "" stands for "Without area" in a choice of areas.
/** Area of a device as a choice value ("" = without area). */
export const areaKey = d => d.area || "";
/** Sorted area names of the devices (without the empty one). */
const areaNames = devices => [...new Set(devices.map(d => d.area).filter(Boolean))].sort(collator.compare);
/** True if at least one device has an area (area column, filter tool, area choices). */
export const anyArea = devices => devices.some(d => d.area);
// Chosen areas of the filter tool ("" = without area); empty: all areas. Kept for the page only.
const areaPick = new Set();
/** True if a device passes the area filter. */
export const areaMatch = d => !areaPick.size || areaPick.has(areaKey(d));
// Filter tool "Area" (MDI texture-box, the area icon of Home Assistant) beside the filter:
// several areas at once, a dot on the icon while a choice is set. Shown only while a
// device has an area.
const areaMenu = box("div", "sort-menu");
areaMenu.role = "menu";
const areaTool = toolButton("area", t("Area"), areaMenu);
/** The node of the area filter tool (placed beside the filter by the Devices tab). */
export const areaFilterNode = areaTool.node;
/** Entries of the area filter for the devices (search applied): "All areas", every area
 *  and "Without area", each with its count; the tooltip names the choice. */
export function syncAreaFilter(devices) {
  const shown = anyArea(ui.state.devices);
  areaTool.node.hidden = !shown;
  if (!shown) areaPick.clear();
  const count = key => devices.filter(d => areaKey(d) === key).length;
  const item = (label, key, n) => {
    const b = el("button", null, "sort-item filter-item"); b.type = "button"; b.role = "menuitemcheckbox";
    b.append(el("span", label), el("span", String(n), "count"));
    b.setAttribute("aria-checked", String(key === null ? !areaPick.size : areaPick.has(key)));
    b.onclick = () => {
      if (key === null) areaPick.clear(); else if (areaPick.has(key)) areaPick.delete(key); else areaPick.add(key);
      render(true);
    };
    return b;
  };
  const names = areaNames(ui.state.devices);
  areaMenu.replaceChildren(el("div", t("Area"), "pop-title"), item(t("All areas"), null, devices.length), ...names.map(a => item(a, a, count(a))),
    item(t("Without area"), "", count("")));
  areaTool.button.classList.toggle("has-value", areaPick.size > 0);
  const text = t("Area: {areas}", {areas: areaPick.size ? [...areaPick].map(a => a || t("Without area")).join(", ") : t("All")});
  setTip(areaTool.button, text); areaTool.button.setAttribute("aria-label", text);
}
/** Third choice "Areas" of a device choice (schedules, "Back up now"): a radio of the group
 *  name and the areas to tick, each with its number of devices (shown while the radio is
 *  chosen). chosen: the areas of a stored schedule (null: none). Returns {nodes, radio,
 *  picked() (the ticked areas), devices() (the devices in them), refresh()}; null while
 *  no device has an area and nothing is chosen. */
export function areaChoice(name, devices, label, chosen) {
  const picked = new Set(chosen || []);
  if (!anyArea(devices) && !picked.size) return null;
  const keys = [...new Set([...areaNames(devices), ...[...picked].filter(Boolean)])].sort(collator.compare);
  if (devices.some(d => !d.area) || picked.has("")) keys.push("");
  const [r, row] = radio(name, [el("span", label)], !!chosen);
  const count = el("span", "", "sub"); row.append(" ", count);
  const boxes = keys.map(key => {
    const n = devices.filter(d => areaKey(d) === key).length;
    const [c, lab] = checkbox([el("span", key || t("Without area")), el("span", tn(n, "1 device", "{n} devices"), "tag")], picked.has(key));
    c.value = key; return [c, lab];
  });
  const list = box("div", "sch-pick", ...boxes.map(([, lab]) => lab));
  const ticked = () => boxes.filter(([c]) => c.checked).map(([c]) => c.value);
  const inAreas = () => { const set = new Set(ticked()); return devices.filter(d => set.has(areaKey(d))); };
  const refresh = () => {
    list.hidden = !r.checked;
    count.textContent = r.checked ? `(${tn(inAreas().length, "1 device", "{n} devices")})` : "";
  };
  refresh();
  return {nodes: [row, list], radio: r, picked: ticked, devices: inAreas, refresh};
}

// ---------------------------------------------------------------- Security
// Narrow strip above the device cards: counters of no password protection, cloud, access
// point and websocket debug log switched on, each only while it is above 0 (unknown states
// do not count); all 0: one green check "No findings". Tiles carry a short name (phones:
// symbol and number only, the name in the tooltip); the filter entries keep the long one.
// A click sets the matching filter of the list, a second click clears it.
/** [key, filter name, tile name, icon, test, tooltip] of each counter. */
export const SECURITY_ITEMS = [
  ["unprotected", t("Unprotected"), t("Unprotected"), "lock", d => d.auth_enabled === false, t("Devices without password protection: anyone in the network can switch and change them.")],
  ["cloud", t("Cloud on"), t("Cloud"), "cloud", d => d.cloud === true, t("Devices with the Shelly cloud switched on.")],
  ["ap", t("Access point on"), t("Access point"), "ap", d => d.ap === true, t("Devices whose own Wi-Fi access point is switched on.")],
  ["debug", t("Debug log on"), t("Debug log"), "bug", d => d.debug_ws === true, t("Devices with the websocket debug log switched on.")],
];
/** The strip "Security" for the devices (search applied). */
export function securityStrip(devices) {
  const tiles = [];
  for (const [key, , label, iconName, fn, tip] of SECURITY_ITEMS) {
    const n = devices.filter(fn).length, active = ui.devFilter === key;
    if (!n && !active) continue;   // an active filter keeps its tile until it is cleared
    const b = el("button", null, "sec-tile has" + (active ? " active" : "")); b.type = "button";
    b.append(box("span", "sec-icon", icon(iconName)), el("span", String(n), "sec-n"), el("span", label, "sec-label"));
    b.setAttribute("aria-pressed", String(active)); b.setAttribute("aria-label", `${label}: ${n}`);
    attachTip(b, () => `${label}: ${tip} ` + (active ? t("Click: clear the filter.") : t("Click: filter the list.")));
    b.onclick = () => { ui.devFilter = active ? "all" : key; render(true); };
    tiles.push(b);
  }
  if (!tiles.length) tiles.push(box("span", "sec-tile zero", box("span", "sec-icon", icon("check")), el("span", t("No findings"), "sec-label")));
  return box("div", "sec-strip", el("span", t("Security"), "sec-title"), ...tiles);
}

// ---------------------------------------------------------------- CSV export
// The list as it is shown (search, filter and area filter applied, in its sort order), one
// line per device; semicolon, UTF-8 with byte order mark (spreadsheet programs then read
// the umlauts right). Cells that a spreadsheet would run as a formula start with "'".
/** One CSV cell: quoted when needed, formulas defused. */
function csvCell(value) {
  let text = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}
/** Save rows ([[cell, …], …], the first one the heads) as a CSV file. */
export function saveCsv(rows, filename) {
  const text = "﻿" + rows.map(r => r.map(csvCell).join(";")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([text], {type: "text/csv;charset=utf-8"}));
  const a = el("a"); a.href = url; a.download = filename;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
