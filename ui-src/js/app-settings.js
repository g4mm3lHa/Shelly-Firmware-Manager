// Settings tab: app options with validation hints and the download base URL notice;
// the firmware store block (export / import) in the card "Firmware & Storage" and the
// card "Device Passwords" (several devices).
import {
  $, api, attachTip, box, closeModal, copyText, el, fmt, icon, iconButton, mb, message, messageBody, modal, section, ui,
  uploadFile, valueList,
} from "./core.js";
import {bulkPasswords} from "./device-settings.js";

// ---------------------------------------------------------------- App settings
// All app options, grouped into cards in this order (Title Case card heads). Saving
// goes through the Supervisor and restarts the app. [key, label, type, description, group]
const OPTION_GROUPS = ["Checks", "Download Server", "Firmware & Storage", "Logs", "Advanced"];
const STORAGE = "Firmware & Storage";
export const OPTION_FIELDS = [
  ["check_interval_days", "Check every (days)", "number", "1–30 days", "Checks"],
  ["check_time", "Check time", "time", "Local time of Home Assistant", "Checks"],
  ["download_base_url", "Download base URL for the Shellys", "text", "LAN IP address of Home Assistant + host port 8098, e.g. http://192.168.1.20:8098 (no host name). Required for installations", "Download Server"],
  ["restrict_downloads_to_devices", "Serve downloads only to known Shellys", "bool", "Switch off if downloads are rejected from 172.30.x.x", "Download Server"],
  ["gen1_updates_enabled", "Enable Gen1 updates", "bool", "Gen1 firmware is only available via plain HTTP without a checksum", STORAGE],
  ["gen1_allowed_types", "Allowed Gen1 types", "list", "Comma-separated, e.g. SHSW-1, SHSW-PM", STORAGE],
  ["allow_downgrade", "Allow downgrades", "bool", "Installing an older stored firmware on Gen2+ devices (device menu and Firmware tab); off by default", STORAGE],
  ["import_max_mb", "Max. import size (MB)", "number", "Largest file accepted by “Import store” (Settings → Firmware & Storage); 1–2048, default 100", STORAGE],
  ["log_entry_limit", "Max. log size (entries)", "number", "100–3000", "Logs"],
  ["log_retention_days", "Log retention (days)", "number", "1–365", "Logs"],
  ["max_parallel_installs", "Max. parallel installations", "number", "1–20; further approvals wait", "Advanced"],
  ["pinned_certificates", "Pinned certificates", "pins", "Host below shelly.cloud + SHA-256 fingerprint; several entries per host allowed", "Advanced"],
];
// The firmware store block (export / import) stands in the card "Firmware & Storage"
// directly before this option
const STORE_BEFORE = "import_max_mb";
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
      if (key === STORE_BEFORE) form.append(storeBlock());
      const id = "opt-" + key, lab = el("label", label); lab.htmlFor = id;
      let input;
      if (type === "bool") { input = el("input"); input.type = "checkbox"; input.checked = !!o[key]; }
      else if (type === "number") { input = el("input"); input.type = "number"; input.value = o[key]; }
      else if (type === "time") { input = el("input"); input.type = "time"; input.step = 60; input.value = o[key] || ""; }
      else if (type === "list") { input = el("input"); input.type = "text"; input.value = (o[key] || []).join(", "); }
      else if (type === "pins") input = pinsEditor(o[key] || []);
      else { input = el("input"); input.type = "text"; input.value = o[key] ?? ""; }
      input.id = id; settingsInputs[key] = input;
      // One block per option: name with help icon, control (and the live warning of
      // the download base URL). On wide screens the block is "display: contents", so the
      // name/control grid is unchanged. The help text is a tooltip of the name and the
      // icon; it stays in the page (visually hidden) as description of the control.
      const help = el("div", desc, "sr-only"); help.id = id + "-help";
      input.setAttribute("aria-describedby", help.id);
      const info = el("button", null, "icon help"); info.type = "button"; info.append(icon("info"));
      info.setAttribute("aria-label", `Help: ${label}`); info.setAttribute("aria-describedby", help.id);
      attachTip(lab, () => desc, true); attachTip(info, () => desc, true);
      let warn = null;
      if (key === "download_base_url") {   // live hint while typing, no blocking
        warn = el("div", null, "desc warnc"); warn.id = id + "-warn"; warn.setAttribute("role", "status");
        const update = () => { warn.textContent = lanHint(input.value.trim()); warn.hidden = !warn.textContent; };
        input.addEventListener("input", update); update();
        input.setAttribute("aria-describedby", `${help.id} ${warn.id}`);
      }
      form.append(box("div", `set-field t-${type}`, box("div", "set-name", lab, info), box("div", "set-control", input), help, warn));
    }
    return box("div", "card", box("div", "card-head", el("h2", group)), box("div", "card-body", form));
  });
  cards.push(passwordsCard());
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
    // A missing optional switch (allow_downgrade) counts as off
    const old = type === "list" ? [...(o[key] || [])].sort() : type === "pins" ? o[key] || [] : type === "text" ? o[key] ?? "" :
      type === "bool" ? !!o[key] : o[key];
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

// ---------------------------------------------------------------- Firmware store: export / import
// The whole store moves to another Home Assistant instance as one ZIP file (readable
// folders per generation and firmware ID, manifest.json, SHA256SUMS.txt). The app
// generates the export password and shows it once; it seals the file (HMAC over the
// manifest with every SHA-256): the import checks that nothing was changed.
/** Block "Firmware store" of the card "Firmware & Storage" with the Export and Import
 *  buttons (full width of the card, between the options). */
function storeBlock() {
  const ex = el("button"); ex.append(icon("exportIcon"), " Export store …"); ex.id = "store-export"; ex.onclick = exportDialog;
  const im = el("button"); im.append(icon("importIcon"), " Import store …"); im.id = "store-import"; im.onclick = importDialog;
  return box("div", "set-store", el("div", "Firmware store", "set-store-name"),
    el("p", "Move the complete firmware store to another Home Assistant instance: export it as one ZIP file (sealed with a password) and import it there. Device passwords are not exported.", "hint"),
    box("div", "store-btns", ex, im));
}
/** Settings card that opens the dialog for passwords of several devices. */
function passwordsCard() {
  const pw = el("button"); pw.append(icon("lock"), " Device passwords …"); pw.id = "set-passwords"; pw.onclick = bulkPasswords;
  return box("div", "card", box("div", "card-head", el("h2", "Device Passwords")), box("div", "card-body",
    el("p", "Store the existing password or set a new one on several devices in one step (each device is set, verified and stored on its own).", "hint"),
    box("div", "store-btns", pw)));
}
const EXPORT_TITLE = "Export firmware store", IMPORT_TITLE = "Import firmware store";
/** Export: the server generates the password (shown only here) and a single-use
 *  download link; "Export" is enabled after "I have saved the password". */
async function exportDialog() {
  let x;
  try { x = await api("api/firmware/export", {}); } catch (e) { message(EXPORT_TITLE, e.message, "error", "exportIcon"); return; }
  const out = el("input"); out.type = "text"; out.readOnly = true; out.value = x.password; out.className = "mono";
  out.setAttribute("aria-label", "Export password");
  // Not copyable (browser refuses both ways): the password is selected for a manual copy
  const copy = iconButton("copy", "Copy password", async () => { if (!await copyText(x.password)) { out.focus(); out.select(); } });
  const ack = el("input"); ack.type = "checkbox";
  const files = `${x.files} (${x.downloads} downloaded, ${x.uploads} uploaded)`;
  modal(EXPORT_TITLE, [
    valueList([["Files", files], ["Size", mb(x.size)], ["File", x.filename]]),
    box("label", "field", "Export password (generated, shown only now)", box("div", "pw-row", out, copy)),
    el("p", "The password seals the file: the import checks that no file and no entry was changed. It is not stored anywhere – the file cannot be imported without it.", "hint"),
    box("label", "check", ack, " I have saved the password")],
  [{text: "Cancel"}, {text: "Export", kind: "primary", action: () => {
    // Download through a link: the file is streamed to disk by the browser, never held in memory
    const a = el("a"); a.href = `api/firmware/export/${encodeURIComponent(x.token)}`; a.download = x.filename;
    document.body.append(a); a.click(); a.remove();
    modal(EXPORT_TITLE, [messageBody("The export is being created – your browser downloads it in a moment. Damaged stored files are left out (see the log).", "success")],
      undefined, "exportIcon");
  }}], "exportIcon");
  const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
}
/** Import step 1: export file and its password; the file is uploaded and checked
 *  completely (seal, every file) before anything is stored. */
export function importDialog() {
  const max = (ui.state && ui.state.import_max_mb) || 100;
  const file = el("input"); file.type = "file"; file.accept = ".zip,application/zip";
  const pw = el("input"); pw.type = "text"; pw.className = "mono"; pw.placeholder = "XXXX-XXXX-XXXX-XXXX-XXXX";
  pw.autocomplete = "off"; pw.spellcheck = false; pw.maxLength = 64;
  modal(IMPORT_TITLE, [
    el("p", "Choose a file created by “Export store” of this app and enter its export password. The file is checked completely before anything is stored.", "hint"),
    box("label", "field", `Export file (.zip, max. ${max} MB – see “Max. import size”)`, file),
    box("label", "field", "Export password", pw)],
  [{text: "Cancel"}, {text: "Check file", kind: "primary", action: async () => {
    const f = file.files[0];
    if (!f) { message(IMPORT_TITLE, "No file chosen.", "error", "importIcon"); return; }
    if (f.size > max * 1048576) { message(IMPORT_TITLE, `The file is larger than the maximum import size (${max} MB). Raise “Max. import size” in the settings first.`, "error", "importIcon"); return; }
    if (!pw.value.trim()) { message(IMPORT_TITLE, "Enter the export password.", "error", "importIcon"); return; }
    await checkImport(f, pw.value);
  }}], "importIcon");
}
/** Upload and check; shows the summary or the error (the staged file is discarded). */
async function checkImport(f, password) {
  const status = el("p", `Uploading ${f.name} (${mb(f.size)}) …`, "hint");
  modal(IMPORT_TITLE, [status], [], "importIcon");
  let token = null;
  try {
    token = (await uploadFile(f, "api/firmware/import", (sent, total) => {
      status.textContent = sent < total ? `Uploading ${f.name} … ${Math.floor(sent * 100 / total)} %` : `Checking ${f.name} …`;
    })).token;
    importSummary(token, f.name, await api("api/firmware/import/check", {token, password}));
  } catch (e) {
    if (token) api("api/firmware/import/cancel", {token}).catch(() => {});
    message(IMPORT_TITLE, e.message, "error", "importIcon");
  }
}
const IMPORT_GROUPS = [["new", "new – will be stored", "p-uptodate"], ["same", "already stored – data added if missing", ""],
  ["conflict", "conflict – stored file kept", "p-available"]];
/** Import step 2: result of the check as counts; every group can be expanded,
 *  conflicts are expanded from the start. Nothing is stored before "Import". */
function importSummary(token, name, r) {
  const line = e => `${e.type} ${e.version}${e.gen === 1 ? " (Gen1)" : ""} – ${e.source === "upload" ? "uploaded" : "downloaded"}` +
    (e.note ? ` – ${e.note}` : "");
  const groups = IMPORT_GROUPS.map(([key, label, cls]) => {
    const rows = r[key] || [], d = el("details");
    d.open = key === "conflict" && rows.length > 0;
    d.append(box("summary", "imp-sum", el("span", String(rows.length), "pill " + cls), " " + label),
      rows.length ? box("ul", "imp-list", ...rows.map(e => el("li", line(e)))) : el("p", "none", "hint"));
    return d;
  });
  const n = r.new.length, label = n ? `Import ${n} file${n === 1 ? "" : "s"}` : "Finish import";
  const cancel = async () => { await api("api/firmware/import/cancel", {token}).catch(() => {}); closeModal(); };
  modal(IMPORT_TITLE, [
    messageBody(`Seal and ${r.files === 1 ? "the file" : `all ${r.files} files`} checked – nothing was changed since the export.`, "success"),
    valueList([["File", name], ["Exported", r.exported ? fmt(r.exported) : "unknown"], ["Size", mb(r.size)]]),
    section("Result", box("div", "imp-groups", ...groups))],
  [{text: "Cancel", action: cancel}, {text: label, kind: "primary", action: async () => {
    try {
      const c = await api("api/firmware/import/confirm", {token});
      const parts = [`${c.stored} new file${c.stored === 1 ? "" : "s"} stored`];
      if (c.updated) parts.push(`data added to ${c.updated} stored file${c.updated === 1 ? "" : "s"}`);
      if (c.conflicts) parts.push(`${c.conflicts} conflict${c.conflicts === 1 ? "" : "s"} – the stored files were kept`);
      message(IMPORT_TITLE, parts.join(", ") + ".", c.conflicts ? "warning" : "success", "importIcon");
    } catch (e) { message(IMPORT_TITLE, e.message, "error", "importIcon"); }
  }}], "importIcon");
}
