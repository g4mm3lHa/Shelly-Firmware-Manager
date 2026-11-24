// Settings tab: app options with validation hints and the download base URL notice;
// the firmware store block (export / import) in the card "Firmware & Storage" and the
// card "Device Passwords" (several devices).
import {
  $, ackGate, api, attachTip, box, checkbox, closeModal, el, field, filePassword, fmt, fmtSize, formError, guarded, icon, iconButton, input,
  mb, message, messageBody, modal, passwordCopy, section, startDownload, t, tn, ui, uploadFile, valueList,
} from "./core.js";
import {autoLanguage, languageChoice, LANGUAGES, setLanguage} from "./i18n.js";
import {bulkPasswords} from "./device-settings.js";
import {downgradeWarning} from "./install.js";
import {connectionRow} from "./diagnosis.js";

// ---------------------------------------------------------------- App settings
// All app options, grouped into cards in this order (Title Case card heads). Saving
// stores them in the app (settings.json) and restarts it. [key, label, type, description, group]
// in English (same texts as translations/en.yaml), translated when shown.
const OPTION_GROUPS = ["Checks", "Download Server", "Firmware & Storage", "Backups", "Logs", "Advanced"];
const STORAGE = "Firmware & Storage";
export const OPTION_FIELDS = [
  ["check_interval_days", "Check every (days)", "number", "1–30 days", "Checks"],
  ["check_time", "Check time", "time", "Local time of Home Assistant", "Checks"],
  ["offer_after_days", "Offer updates only after (days)", "number", "Holds a new firmware version back until it is this many days old (age from its build date); 0 = offer at once. 0–60, default 0", "Checks"],
  ["download_base_url", "Download base URL for the Shellys", "text", "LAN IP address of Home Assistant + host port 8098, e.g. http://192.168.1.20:8098 (no host name; IP addresses only in private networks). Required for installations", "Download Server"],
  ["restrict_downloads_to_devices", "Serve downloads only to known Shellys", "bool", "Switch off if downloads are rejected from 172.30.x.x", "Download Server"],
  ["gen1_updates_enabled", "Enable Gen1 updates", "bool", "Gen1 firmware is only available via plain HTTP without a checksum", STORAGE],
  ["allow_downgrade", "Allow downgrades", "bool", "Installing an older stored firmware on Gen2+ devices (device menu and Firmware tab); off by default", STORAGE],
  ["beta_firmware", "Offer beta firmware", "bool", "Also offers beta versions of the Shelly update server – Gen2+ only, Gen1 devices get no beta versions. They are marked “Beta”, listed under “Beta Updates” and installed only after your approval and an extra confirmation; the offer delay does not apply to them. Off by default", STORAGE],
  ["keep_installed", "Keep installed versions", "bool", "Also stores the firmware a device already runs while Shelly still offers it (Gen2+ only), so that a downgrade stays possible after a bad update; on by default", STORAGE],
  ["import_max_mb", "Max. import size (MB)", "number", "Largest file accepted by “Import store” (Settings → Firmware & Storage); 1–2048, default 100", STORAGE],
  ["backup_before_install", "Back up before every installation", "bool", "Stores the settings of a device right before an update or downgrade is installed on it; on by default", "Backups"],
  ["backup_keep", "Keep backups per device", "number", "The newest backups of each device are kept, older ones are removed automatically; a backup marked “Kept” stays. 1–50, default 10", "Backups"],
  ["log_entry_limit", "Max. log size (entries)", "number", "100–3000", "Logs"],
  ["log_retention_days", "Log retention (days)", "number", "1–365", "Logs"],
  ["max_parallel_installs", "Max. parallel installations", "number", "1–20; further approvals wait", "Advanced"],
  ["resolve_host_names", "Resolve host names", "bool", "Looks up each device's host name (<device id>.local) at every check and shows the column “Host name”; off by default", "Advanced"],
  ["pinned_certificates", "Pinned certificates", "pins", "Host below shelly.cloud + SHA-256 fingerprint; several entries per host allowed", "Advanced"],
];
// The firmware store block (export / import) stands in the card "Firmware & Storage"
// directly before this option
const STORE_BEFORE = "import_max_mb";
let settingsInputs = {};
/** Line under "Keep installed versions": storage of the versions the Gen2+ devices run
 *  (stored, downloaded by the next check with an estimated size) and the free space. */
function installedNote(v) {
  if (!v || !(v.stored + v.pending)) return "";
  const total = v.stored + v.pending, parts = [t("{n} stored ({size})", {n: v.stored, size: mb(v.stored_size)})];
  if (v.pending) parts.push(t("{n} downloaded at the next check", {n: v.pending}) +
    (v.pending_size ? " " + t("(about {mb} MB each)", {mb: Math.max(1, Math.round(v.pending_size / 1048576))}) : ""));
  return tn(total, "1 installed version: {parts}.", "{n} installed versions: {parts}.", {parts: parts.join(", ")}) +
    (v.free != null ? " " + t("Free: {size}.", {size: fmtSize(v.free)}) : "");
}

// The Shellys download the firmware from download_base_url, so it must point to Home
// Assistant in the local network. The server accepts only an IP address in the private
// networks (10/8, 172.16/12, 192.168/16, fc00::/7) or an ASCII host name, and refuses user
// name/password, query and fragment; the Settings tab names the reason while typing (no
// blocking). The checks read the typed text, not the normalised URL, so a number form such
// as 3232235778 is judged like the server does. A host name gets a hint, because Shellys
// often cannot resolve names. Other format errors get no hint (the server reports them).
const OCTET = "(25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)";
const IPV4 = new RegExp(`^${OCTET}(\\.${OCTET}){3}$`);
const LAN_IPV4 = new RegExp(`^(10\\.${OCTET}|172\\.(1[6-9]|2\\d|3[01])|192\\.168)\\.${OCTET}\\.${OCTET}$`);
const LAN_IPV6 = /^\[f[cd][0-9a-f]{2}:/i;
const NUMERIC_HOST = /^(0x[0-9a-f]*|[0-9]+)(\.(0x[0-9a-f]*|[0-9]+))*\.?$/i;
const LABEL = "[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?";
const HOST_NAME = new RegExp(`^${LABEL}(\\.${LABEL})*\\.?$`, "i");
const HINT_NOT_PRIVATE = t("Not accepted: this IP address is not in a private network (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7). Use the LAN IP of Home Assistant.");
const HINT_CREDENTIALS = t("Not accepted: the address must not contain a user name or password.");
const HINT_QUERY = t("Not accepted: the address must not contain a query (?…) or a fragment (#…).");
const HINT_LOCALHOST = t("This host is not in your local network. The Shellys download the firmware from this URL – use the LAN IP of Home Assistant.");
const SUGGESTION_NOTE = t("Suggestion from the address of this page – please check it before saving.");
const HINT_HOST_NAME = t("Use the LAN IP address of Home Assistant instead of a host name – Shellys often cannot resolve host names, the update then fails with a connection error.");
/** Hint text for the URL (rejection reason or advice), else "". */
function lanHint(value) {
  if (/[^\x21-\x7e]/.test(value)) return "";   // spaces, control or non-ASCII characters: the server refuses
  let url = null;
  try { url = new URL(value); } catch (e) { /* invalid: judged by the typed host below */ }
  if (url && (url.username || url.password)) return HINT_CREDENTIALS;
  if (url && (url.search || url.hash)) return HINT_QUERY;
  const authority = (/^[a-z][a-z0-9+.-]*:\/\/([^/?#]*)/i.exec(value) || [])[1];
  if (authority === undefined) return "";
  const typed = authority.replace(/^.*@/, "");
  if (typed.startsWith("[")) return !url ? "" : LAN_IPV6.test(url.hostname) ? "" : HINT_NOT_PRIVATE;
  const host = typed.replace(/:[^:]*$/, "");
  if (IPV4.test(host)) return LAN_IPV4.test(host) ? "" : HINT_NOT_PRIVATE;
  if (NUMERIC_HOST.test(host)) return HINT_NOT_PRIVATE;
  if (!url || host.length > 253 || !HOST_NAME.test(host)) return "";
  return host.replace(/\.$/, "").toLowerCase() === "localhost" ? HINT_LOCALHOST : HINT_HOST_NAME;
}

/** Suggestion for an empty download base URL: the address this page was opened with
 *  (the browser's Home Assistant address) on the download port, only if that host is an IP
 *  address in a private network; "" otherwise (host name, public address, localhost). */
function suggestedBaseUrl() {
  const host = location.hostname;
  const lan = IPV4.test(host) ? LAN_IPV4.test(host) : LAN_IPV6.test(host);
  return lan ? `http://${host}:8098` : "";
}

/** Line under "Keep backups per device": what is stored now and where it is managed. */
function backupsNote(totals) {
  const where = t("Manage them in the Backups tab; a schedule with the action “Backup” stores them regularly.");
  if (!totals || !totals.count) return t("No backups are stored yet.") + " " + where;
  const size = fmtSize(totals.size);
  const stored = totals.count === 1 ? t("1 backup of 1 device is stored ({size}), encrypted.", {size})
    : totals.devices === 1 ? t("{n} backups of 1 device are stored ({size}), encrypted.", {n: totals.count, size})
      : t("{n} backups of {devices} devices are stored ({size}), encrypted.", {n: totals.count, devices: totals.devices, size});
  return stored + " " + where;
}
/** Build the grouped settings cards with one common Save button. */
export function renderSettings() {
  const o = ui.options; settingsInputs = {};
  if (!o) { $("settings-form").replaceChildren(box("div", "card", box("div", "card-body", el("p", t("Loading …"), "hint")))); return; }
  const cards = OPTION_GROUPS.map(group => {
    const form = el("div", null, "form");
    for (const [key, english, type, descEn] of OPTION_FIELDS.filter(f => f[4] === group)) {
      if (key === STORE_BEFORE) form.append(storeBlock());
      const label = t(english), desc = t(descEn);
      const id = "opt-" + key, lab = el("label", label); lab.htmlFor = id;
      const control = type === "bool" ? input("checkbox", {checked: !!o[key]}) : type === "number" ? input("number", {value: o[key]})
        : type === "time" ? input("time", {step: 60, value: o[key] || ""}) : type === "pins" ? pinsEditor(o[key] || []) : input("text", {value: o[key] ?? ""});
      if (key === "download_base_url" && !control.value) control.value = suggestedBaseUrl();   // only a suggestion: counts as unsaved
      control.id = id; settingsInputs[key] = control;
      if (key === "allow_downgrade") control.addEventListener("change", () => { if (control.checked) confirmDowngrades(control); });
      // One block per option: name with help icon, control (and the live warning of
      // the download base URL). On wide screens the block is "display: contents", so the
      // name/control grid is unchanged. The help text is a tooltip of the name and the
      // icon; it stays in the page (visually hidden) as description of the control.
      const help = el("div", desc, "sr-only"); help.id = id + "-help";
      control.setAttribute("aria-describedby", help.id);
      const info = el("button", null, "icon help"); info.type = "button"; info.append(icon("info"));
      info.setAttribute("aria-label", t("Help: {name}", {name: label})); info.setAttribute("aria-describedby", help.id);
      attachTip(lab, () => desc, true); attachTip(info, () => desc, true);
      let warn = null, suggest = null;
      if (key === "download_base_url") {   // live hint while typing, no blocking
        warn = el("div", null, "desc warnc"); warn.id = id + "-warn"; warn.setAttribute("role", "status");
        // Note under the field while it still holds the suggested address
        const suggested = !o[key] && control.value;
        suggest = el("div", SUGGESTION_NOTE, "desc"); suggest.id = id + "-suggest";
        const update = () => {
          warn.textContent = lanHint(control.value.trim()); warn.hidden = !warn.textContent;
          suggest.hidden = !(suggested && control.value === suggested);
        };
        control.addEventListener("input", update); update();
        control.setAttribute("aria-describedby", `${help.id} ${warn.id} ${suggest.id}`);
      }
      const note = key === "keep_installed" ? el("div", installedNote(o.installed_versions), "desc set-note")
        : key === "backup_keep" ? el("div", backupsNote(ui.state && ui.state.backup_totals), "desc set-note") : null;
      form.append(box("div", `set-field t-${type}`, box("div", "set-name", lab, info), box("div", "set-control", control), help, warn, suggest, note));
      if (key === "download_base_url") form.append(connectionRow(control));   // "Test connection" and the last result
    }
    return box("div", "card", box("div", "card-head", el("h2", t(group))), box("div", "card-body", form));
  });
  cards.unshift(passwordsCard());   // Device Passwords first
  cards.push(languageCard());       // Display last
  const save = el("button", t("Save settings"), "primary"); save.onclick = saveOptions;
  const dirty = el("span", t("Unsaved changes"), "unsaved"); dirty.hidden = true;
  const markDirty = () => { dirty.hidden = !Object.keys(optionChanges()).length; };
  const wrap = box("div", "settings-grid");
  settingsCards = cards; layoutSettings(wrap);
  wrap.addEventListener("input", markDirty); wrap.addEventListener("change", markDirty); wrap.addEventListener("click", () => setTimeout(markDirty));
  $("settings-form").replaceChildren(wrap, box("div", "form-actions", dirty, save), changelogNote());   // common save area
  markDirty();   // a suggested download base URL is an unsaved change
}
/** Popup when "Allow downgrades" is ticked: the red downgrade warning; the red "Enable"
 *  needs the tick "I understand the risk". Cancel, the X and Escape take the tick back.
 *  Like every option it applies only after saving. */
function confirmDowngrades(control) {
  const [ack, ackLabel] = checkbox(t("I understand the risk"));
  const untick = () => { control.checked = false; control.dispatchEvent(new Event("change", {bubbles: true})); closeModal(); };
  modal(t("Allow downgrades?"), [downgradeWarning(), ackLabel, el("p", t("Applies only after saving."), "hint")],
    [{text: t("Cancel"), cancel: true, action: untick}, {text: t("Enable"), kind: "danger", action: closeModal}], "history");
  ackGate(ack);
}
// Columns of the Settings tab: the cards fill independent stacks from left to right (card
// n goes to stack n mod columns), so the cards of a column follow each other without the
// gaps of shared row heights. Three columns from 1600 px, two from 1168 px (two cards of
// at least 560 px), else one. A changed width moves the same card elements: typed values stay.
const SETTINGS_THREE = matchMedia("(min-width:1600px)"), SETTINGS_TWO = matchMedia("(min-width:1168px)");
let settingsCards = [];
/** Distribute the settings cards over the column stacks of the grid element. */
function layoutSettings(wrap) {
  const n = SETTINGS_THREE.matches ? 3 : SETTINGS_TWO.matches ? 2 : 1;
  const stacks = Array.from({length: n}, () => box("div", "set-stack"));
  settingsCards.forEach((card, i) => stacks[i % n].append(card));
  wrap.replaceChildren(...stacks);
}
for (const query of [SETTINGS_THREE, SETTINGS_TWO]) {
  query.addEventListener("change", () => { const wrap = document.querySelector(".settings-grid"); if (wrap) layoutSettings(wrap); });
}
// Path of this app's page in Home Assistant (server: slug from the Supervisor); the list
// of apps until it is known
const APP_LIST_PAGE = "/config/apps";
let appPage = APP_LIST_PAGE;
/** Last line of the tab: where the changelog is – the info card of the app page in
 *  Home Assistant has the link "Changelog" next to the version. The link opens the
 *  page in the whole window (the app runs in a frame of Home Assistant). */
function changelogNote() {
  const link = el("a", t("app page in Home Assistant")); link.href = appPage; link.target = "_top"; link.id = "set-changelog";
  if (appPage === APP_LIST_PAGE) api("api/settings/app-page").then(r => { appPage = r.url; link.href = appPage; }).catch(() => {});
  return box("p", "hint set-changelog", t("What changed in each version: open the"), " ", link, " ", t("and choose “Changelog” next to the version."));
}
/** Editable list of host + fingerprint rows. */
function pinsEditor(pins) {
  const wrap = el("div", null, "pins"), rows = el("div"); rows.style.flexDirection = "column";
  const addRow = (host = "", sha = "") => {
    const h = input("text", {value: host, placeholder: t("host, e.g. updates.shelly.cloud")});
    const s = input("text", {value: sha, placeholder: t("SHA-256 AB:CD:…"), className: "mono"});
    h.setAttribute("aria-label", t("Host")); s.setAttribute("aria-label", t("SHA-256 fingerprint"));
    const row = box("div", null, h, s, iconButton("delete", t("Remove entry"), () => row.remove(), "red"));
    rows.append(row);
  };
  pins.forEach(p => addRow(p.host, p.sha256));
  const add = el("button", t("Add entry")); add.type = "button"; add.onclick = () => addRow();
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
    else if (type === "pins") value = input.values();
    else value = input.value.trim();
    // A missing optional switch (allow_downgrade, beta_firmware) counts as off
    const old = type === "pins" ? o[key] || [] : type === "text" ? o[key] ?? "" :
      type === "bool" ? !!o[key] : o[key];
    if (JSON.stringify(value) !== JSON.stringify(old)) changes[key] = value;
  }
  return changes;
}
/** Collect changed options, confirm, save and wait for the app restart. */
function saveOptions() {
  const changes = optionChanges();
  if (!Object.keys(changes).length) { message(t("Settings"), t("Nothing changed.")); return; }
  const names = Object.keys(changes).map(k => t(OPTION_FIELDS.find(f => f[0] === k)[1]));
  modal(t("Save settings?"), [el("p", t("Changed: ") + names.join(", ")), el("p", t("The app restarts to apply the settings; this page reconnects automatically."), "hint")],
    [{text: t("Cancel")}, {text: t("Save & restart"), kind: "primary", action: () => guarded(t("Save settings?"), undefined, async () => {
      const r = await api("api/settings", changes);
      if (!r.restarting) { message(t("Settings"), t("Nothing changed.")); return; }
      modal(t("Settings saved"), [el("p", t("The app is restarting …"), "hint")], []);
      waitForRestart();
    })}]);
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
  const ex = el("button", null, "icon-text sm"); ex.append(icon("exportIcon"), " " + t("Export store")); ex.id = "store-export"; ex.onclick = exportDialog;
  const im = el("button", null, "icon-text sm"); im.append(icon("importIcon"), " " + t("Import store")); im.id = "store-import"; im.onclick = importDialog;
  return box("div", "set-store", el("div", t("Firmware store"), "set-store-name"),
    el("p", t("Move the complete firmware store to another Home Assistant instance: export it as one ZIP file (sealed with a password) and import it there. The backups of the device settings are part of the export. Device passwords are not exported."), "hint"),
    box("div", "store-btns", ex, im));
}
/** Card "Display": language of this page, stored in this browser only. */
function languageCard() {
  const select = el("select"); select.id = "set-language";
  for (const [code, name] of LANGUAGES) {
    const auto = code === "auto" ? LANGUAGES.find(l => l[0] === autoLanguage)[1] : "";
    const option = el("option", auto ? t("Same as Home Assistant ({language})", {language: auto}) : name); option.value = code;
    select.append(option);
  }
  select.value = languageChoice();
  select.onchange = () => setLanguage(select.value);
  const lab = el("label", t("Language")); lab.htmlFor = select.id;
  return box("div", "card", box("div", "card-head", el("h2", t("Display"))), box("div", "card-body",
    box("div", "form", box("div", "set-field t-select", box("div", "set-name", lab), box("div", "set-control", select))),
    el("p", t("Applies at once and only in this browser – no “Save settings” needed. Logs, error messages and the documentation stay English."), "hint lang-note")));
}
/** Settings card that opens the dialog for passwords of several devices. */
function passwordsCard() {
  const pw = el("button", null, "icon-text sm"); pw.append(icon("lock"), " " + t("Device passwords")); pw.id = "set-passwords"; pw.onclick = bulkPasswords;
  return box("div", "card", box("div", "card-head", el("h2", t("Device Passwords"))), box("div", "card-body",
    el("p", t("Store the existing password or set a new one on several devices in one step (each device is set, verified and stored on its own)."), "hint"),
    box("div", "store-btns", pw)));
}
const EXPORT_TITLE = t("Export firmware store"), IMPORT_TITLE = t("Import firmware store");
// The seal proves that the file was not changed since the export, not who created it: a
// warning in step 1 (and the first-start dialog), every new file is stored as "Imported"
export const IMPORT_OWN = t("Import only a store you exported yourself. The password proves that the file was not changed after the export – it does not prove who created it. A file from someone else can contain manipulated firmware.");
const IMPORT_MARK = t("Every file is stored as “Imported”, whatever the export says about its source. An imported file is used for an update only once the Shelly update server offers exactly this file (same SHA-256, Gen2+ only); it then counts as downloaded. While Shelly cannot be reached, imported files are offered only for a manual installation at your own risk.");
/** Export: the server generates the password (shown only here) and a single-use
 *  download link; "Export" is enabled after "I have saved the password". */
async function exportDialog() {
  let x;
  try { x = await api("api/firmware/export", {}); } catch (e) { message(EXPORT_TITLE, e.message, "error", "exportIcon"); return; }
  const prepared = Date.now();
  const [out, copy] = passwordCopy(x.password, t("Export password"), "mono");
  const [ack, ackLabel] = checkbox(t("I have saved the password"));
  const files = t("{files} ({downloads} downloaded, {uploads} uploaded, {imports} imported)", {files: x.files, downloads: x.downloads, uploads: x.uploads, imports: x.imports || 0});
  modal(EXPORT_TITLE, [
    valueList([[t("Files"), files], [t("Backups"), x.backups ? tn(x.backups, "{n} backup of device settings", "{n} backups of device settings") : null],
      [t("Size"), mb(x.size)], [t("File"), x.filename]]),
    field(t("Export password (generated, shown only now)"), box("div", "pw-row", out, copy)),
    el("div", t("The password seals the file: the import checks that no file and no entry was changed. It is not stored anywhere – the file cannot be imported without it."), "warnbox"),
    ackLabel],
  [{text: t("Cancel")}, {text: t("Export"), kind: "primary", action: () => {
    // The server refuses the download of an expired export; a download through a link
    // cannot show that answer, so the dialog checks the time itself
    if (Date.now() - prepared > x.expires_in * 1000) { message(EXPORT_TITLE, t("Export expired – please start it again."), "error", "exportIcon"); return; }
    startDownload(`api/firmware/export/${encodeURIComponent(x.token)}`, x.filename);
    modal(EXPORT_TITLE, [messageBody(t("The export is being created – your browser downloads it in a moment. Damaged stored files are left out (see the log)."), "success")],
      undefined, "exportIcon");
  }}], "exportIcon");
  ackGate(ack);
}
/** Import step 1: export file and its password; the file is uploaded and checked
 *  completely (seal, every file) before anything is stored. A refused file brings back
 *  this step with the file, the password and the error. */
export function importDialog() {
  const max = (ui.state && ui.state.import_max_mb) || 100;
  const file = input("file", {accept: ".zip,application/zip"}), pw = filePassword(t("XXXX-XXXX-XXXX-XXXX-XXXX")), error = formError();
  const content = [
    el("p", t("Choose a file created by “Export store” of this app and enter its export password. The file is checked completely before anything is stored."), "hint"),
    field(t("Export file (.zip, max. {max} MB – see “Max. import size”)", {max}), file),
    field(t("Export password"), pw),
    el("div", IMPORT_OWN, "warnbox"), error.line];
  const form = () => modal(IMPORT_TITLE, content, [{text: t("Cancel")}, {text: t("Check file"), kind: "primary", action: async () => {
    const f = file.files[0];
    if (!f) { error.show(t("No file chosen.")); return; }
    if (f.size > max * 1048576) { error.show(t("The file is larger than the maximum import size ({max} MB). Raise “Max. import size” in the settings first.", {max})); return; }
    if (!pw.value.trim()) { error.show(t("Enter the export password.")); return; }
    await checkImport(f, pw.value, text => { form(); error.show(text); });
  }}], "importIcon");
  form();
}
/** Upload and check; shows the summary, or refused(error) (the staged file is discarded). */
async function checkImport(f, password, refused) {
  const status = el("p", t("Uploading {name} ({size}) …", {name: f.name, size: mb(f.size)}), "hint");
  modal(IMPORT_TITLE, [status], [], "importIcon");
  let token = null;
  try {
    token = (await uploadFile(f, "api/firmware/import", (sent, total) => {
      status.textContent = sent < total ? t("Uploading {name} … {percent} %", {name: f.name, percent: Math.floor(sent * 100 / total)}) : t("Checking {name} …", {name: f.name});
    })).token;
    importSummary(token, f.name, await api("api/firmware/import/check", {token, password}));
  } catch (e) {
    if (token) api("api/firmware/import/cancel", {token}).catch(() => {});
    refused(e.message);
  }
}
const IMPORT_GROUPS = [["new", t("new – will be stored as “Imported”"), "p-uptodate"], ["same", t("already stored – data added if missing"), ""],
  ["conflict", t("conflict – stored file kept"), "p-available"]];
/** Import step 2: result of the check as counts; every group can be expanded,
 *  conflicts are expanded from the start. Nothing is stored before "Import". */
function importSummary(token, name, r) {
  // The source named in the export is only its creator's claim: it is not shown
  const line = e => `${e.type} ${e.version}${e.gen === 1 ? " (Gen1)" : ""}` + (e.note ? ` – ${e.note}` : "");
  const groups = IMPORT_GROUPS.map(([key, label, cls]) => {
    const rows = r[key] || [], d = el("details");
    d.open = key === "conflict" && rows.length > 0;
    d.append(box("summary", "imp-sum", el("span", String(rows.length), "pill " + cls), " " + label),
      rows.length ? box("ul", "imp-list", ...rows.map(e => el("li", line(e)))) : el("p", t("none"), "hint"));
    return d;
  });
  const n = r.new.length, label = n ? tn(n, "Import 1 file", "Import {n} files") : t("Finish import");
  const cancel = async () => { await api("api/firmware/import/cancel", {token}).catch(() => {}); closeModal(); };
  const [ack, ackLabel] = checkbox(t("I created this export myself or I trust its source"));
  modal(IMPORT_TITLE, [
    messageBody(tn(r.files, "Seal and the file checked – nothing was changed since the export.", "Seal and all {n} files checked – nothing was changed since the export."), "success"),
    valueList([[t("File"), name], [t("Exported"), r.exported ? fmt(r.exported) : "–"], [t("Size"), mb(r.size)],
      [t("Backups"), r.backups ? t("{n} in the file · {new} new", {n: r.backups, new: r.backups_new || 0}) : null]]),
    section(t("Result"), box("div", "imp-groups", ...groups)),
    el("p", IMPORT_MARK, "hint"),
    ackLabel],
  [{text: t("Cancel"), cancel: true, action: cancel}, {text: label, kind: "primary", action: () => guarded(IMPORT_TITLE, "importIcon", async () => {
    const c = await api("api/firmware/import/confirm", {token, acknowledged: ack.checked});
    const parts = [tn(c.stored, "1 new file stored as “Imported”", "{n} new files stored as “Imported”")];
    if (c.updated) parts.push(tn(c.updated, "data added to 1 stored file", "data added to {n} stored files"));
    if (c.conflicts) parts.push(tn(c.conflicts, "1 conflict – the stored files were kept", "{n} conflicts – the stored files were kept"));
    if (c.backups) parts.push(tn(c.backups, "{n} backup of device settings imported", "{n} backups of device settings imported"));
    message(IMPORT_TITLE, parts.join(", ") + ".", c.conflicts ? "warning" : "success", "importIcon");
  })}], "importIcon");
  // Enabled once the user states to have created the export or to trust its source
  ackGate(ack);
}
