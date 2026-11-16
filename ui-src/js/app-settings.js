// Settings tab: app options with validation hints and the download base URL notice.
import {$, api, box, el, iconButton, message, modal, ui} from "./core.js";

// ---------------------------------------------------------------- App settings
// All app options, grouped into cards. Saving goes through the Supervisor and
// restarts the app. [key, label, type, description, group]
const OPTION_GROUPS = ["Checks", "Firmware & storage", "Logs", "Advanced"];
export const OPTION_FIELDS = [
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
// localhost) gets the "not local" hint; every host name (also .local, .lan,
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
export function renderSettings() {
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
