// Installation and downgrade dialogs (approval bound to the SHA-256 of the provided file).
import {$, api, box, closeModal, devUrl, el, fmt, icon, mb, message, modal, section, selected, shaRow, valueList} from "./core.js";
import {load} from "./main.js";

// ---------------------------------------------------------------- Installation
/** Changelog versions as nodes: category tags, security highlighted, model entries marked. */
export function changelogNodes(data) {
  if (!data || !data.available) return [el("p", "Changelog not available.", "hint")];
  if (!data.versions.length) return [el("p", "No changelog entries between the installed and the target version.", "hint")];
  const out = [];
  for (const v of data.versions) {
    out.push(el("div", v.date ? `${v.version} · ${v.date}` : v.version, "cl-version"));
    if (!v.entries.length) { out.push(el("p", "No entries listed for this version.", "hint")); continue; }
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
// Where the provided update comes from (device field offer_source). Updates
// from the firmware store are provided without asking the Shelly update server.
export const OFFER_SOURCES = {manufacturer: "Shelly update server", store: "Firmware store (downloaded from Shelly earlier)",
  upload: "Firmware store (uploaded file)", import: "Firmware store (imported file)"};
const OFFER_SHORT = {manufacturer: "Shelly server", store: "Store", upload: "Upload", import: "Import"};
/** Source line of the install dialog: the store label, or the manufacturer's URL. */
const offerSource = d => d.offer_source && d.offer_source !== "manufacturer" ? OFFER_SOURCES[d.offer_source] : d.source_url;
const FALLBACK_WARNING = "The Shelly update server could not be asked. This stored version is offered for a manual installation at your own risk: it cannot be verified whether the device needs an intermediate version first. If it does, the device rejects the firmware.";
/** Hint of an intermediate version: the device is checked again after its installation. */
const stepHint = d => `${d.name}: ${d.offer_version} is an intermediate version${d.step_target ? ` on the way to ${d.step_target}` : ""}. After the installation the device is checked again and the next version is provided (it is installed only after a new approval).`;
const GEN1_WARNING = "Gen1 firmware is offered by Shelly only via plain HTTP and without a checksum – its integrity and origin cannot be verified. Install only if you accept this risk.";
/** "Device" block of the installation and downgrade dialogs. */
const deviceSection = d => section("Device", valueList([["Name", d.name], ["Shelly", d.shelly_name], ["IP", d.ip, "num"],
  ["Model", [d.model, d.gen ? `Gen ${d.gen}` : null].filter(Boolean).join(" · ")]]));
/** Installed and target version on one line. */
const versionStep = (from, to) => `${from || "–"}  →  ${to || "–"}`;
// ---- Before installing: way back (installed version stored) and Wi-Fi signal
// Signal classes in dBm: better than -70 good, -70 to -79 fair, -80 and below weak
const WEAK_RSSI = -80, FAIR_RSSI = -70;
const dbm = rssi => `${String(rssi).replace("-", "−")} dBm`;
const weak = rssi => typeof rssi === "number" && rssi <= WEAK_RSSI;
const signalText = rssi => `${dbm(rssi)} · ${weak(rssi) ? "weak" : rssi <= FAIR_RSSI ? "fair" : "good"}.`;
const WEAK_TEXT = rssi => `Weak Wi-Fi signal (${dbm(rssi)}). The Shelly downloads the firmware over Wi-Fi – the download may fail.`;
const BACK_OK = d => `${d.way_back.version} is stored – a downgrade stays possible.`;
const BACK_NO = d => `${d.way_back.version} is not stored and Shelly no longer offers it – there is no way back after this update.`;
const noWayBack = d => !!d.way_back && !d.way_back.stored;
/** Build date of the offered version with its age in days (local dates). */
export function daysAgo(day) {
  const [y, m, dd] = day.split("-").map(Number), now = new Date();
  const days = Math.round((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(y, m - 1, dd)) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
}
export const buildAge = day => `${day} · ${daysAgo(day)}`;
/** One line of the check list: state icon (green check or yellow exclamation mark), title, text. */
function checkLine(ok, title, text) {
  return box("li", ok === null ? "wait" : ok ? "ok" : "warn",
    box("span", "pc-icon", ok === null ? null : icon(ok ? "check" : "alert")), box("div", null, el("strong", title), el("span", text)));
}
/** Wi-Fi signal of the devices, read now (null: not on Wi-Fi or not readable). */
// The device did not answer or reported no usable value (shown as "unknown")
const UNKNOWN_SIGNAL = "unknown";
async function readSignals(devs) {
  return Promise.all(devs.map(d => api(`${devUrl(d)}/signal`).then(r => r.rssi ?? null).catch(() => UNKNOWN_SIGNAL)));
}
/** Section "Before installing": way back and Wi-Fi signal; for several devices only
 *  the findings per device (or one line "every device" without findings). The signal is
 *  read when the dialog opens ("Reading …"); devices on the cable have no signal line, a
 *  value that could not be read is shown as "unknown" (yellow, a finding). */
function beforeInstalling(devs) {
  const list = el("ul", null, "prechk"), one = devs.length === 1;
  const back = () => {
    const known = devs.filter(d => d.way_back), missing = known.filter(noWayBack);
    if (!known.length) return [];
    if (one) return [checkLine(known[0].way_back.stored, "Way back", known[0].way_back.stored ? BACK_OK(known[0]) : BACK_NO(known[0]))];
    return missing.length ? missing.map(d => checkLine(false, `Way back – ${d.name}`, BACK_NO(d)))
      : [checkLine(true, "Way back", "The installed version of every device is stored.")];
  };
  const signal = values => {
    if (!values) return [checkLine(null, "Wi-Fi signal", "Reading …")];
    const read = devs.map((d, i) => [d, values[i]]).filter(([, v]) => v !== null);
    if (!read.length) return [];
    const finding = v => v === UNKNOWN_SIGNAL ? "unknown" : WEAK_TEXT(v);
    const bad = read.filter(([, v]) => v === UNKNOWN_SIGNAL || weak(v));
    if (one) return [checkLine(!bad.length, "Wi-Fi signal", bad.length ? finding(read[0][1]) : signalText(read[0][1]))];
    return bad.length ? bad.map(([d, v]) => checkLine(false, `Wi-Fi signal – ${d.name}`, finding(v)))
      : [checkLine(true, "Wi-Fi signal", "Good or fair on every device.")];
  };
  const block = section("Before installing", list);
  const fill = values => { list.replaceChildren(...back(), ...signal(values)); block.hidden = !list.children.length; };
  fill(null);
  readSignals(devs).then(fill);
  return block;
}
// Imported files (V1): offered only while Shelly cannot be asked (store fallback)
export const IMPORT_WARNING = "This file comes from an imported firmware store. The Shelly update server has not confirmed it – it is only as trustworthy as the export you imported.";
const IMPORT_FALLBACK = d => (d.gen === 1 ? "Shelly's firmware list could not be loaded." : "The Shelly update server could not be asked.") +
  " This stored version comes from an imported firmware store and Shelly has not confirmed it – it is only as trustworthy as the export you imported. It is offered for a manual installation at your own risk." +
  (d.gen === 1 ? "" : " It also cannot be verified whether the device needs an intermediate version first; if it does, the device rejects the firmware.");
/** Confirmation dialog: one device with all details, several as an overview; approval bound to the SHA-256. */
export function openInstall(devs) {
  const content = [], restart = el("input"); restart.type = "checkbox";
  const retry = devs.length === 1 && !!devs[0].install_failed;
  if (devs.length === 1) {
    const d = devs[0];
    content.push(deviceSection(d), section("Firmware", valueList([["Firmware ID", d.type], ["Version", versionStep(d.version, d.offer_version), "ver"],
      ["Build date", d.offer_date ? buildAge(d.offer_date) : null], ["Size", mb(d.size || 0)], ["Source", offerSource(d), "src"], shaRow(d.sha256)])),
    beforeInstalling(devs));
    if (retry) {   // after a failed installation: restart the device first (pre-selected)
      restart.checked = true;
      content.push(section("Last attempt", el("p", `Failed: ${d.message || "–"}`, "pc-fail")),
        box("label", "check", restart, " Restart the device first"),
        el("p", "The app restarts the Shelly, waits until it answers again and then starts the installation.", "hint pc-sub"));
    }
    if (d.gen === 1) content.push(el("div", GEN1_WARNING, "warnbox"));
    if (d.offer_source === "upload") content.push(el("div", UPLOAD_HINT, "warnbox"));
    if (d.step_target != null) content.push(el("p", stepHint(d), "hint"));
    content.push(section("Changelog", changelogBox(d)));
  } else {
    // Phones show the rows as two-line entries (style.css, class inst-list)
    const t = el("table", null, "inst-list"), h = el("tr");
    for (const c of ["Device", "IP", "Installed", "Target", "Source"]) h.append(el("th", c));
    t.append(box("thead", null, h));
    for (const d of devs) {
      const r = el("tr");
      for (const [v, cls] of [[d.name], [d.ip, "num"], [d.version], [d.offer_version], [OFFER_SHORT[d.offer_source]]]) r.append(el("td", v ?? "–", cls));
      t.append(r);
    }
    content.push(t, beforeInstalling(devs));
    if (devs.some(d => d.gen === 1)) content.push(el("div", GEN1_WARNING, "warnbox"));
    if (devs.some(d => d.offer_source === "upload")) content.push(el("div", UPLOAD_HINT, "warnbox"));
    for (const d of devs.filter(x => x.step_target != null)) content.push(el("p", stepHint(d), "hint"));
    for (const d of devs) {
      const det = el("details"); det.append(el("summary", `Changelog – ${d.name}`));
      det.ontoggle = () => { if (det.open && det.children.length === 1) det.append(changelogBox(d)); };
      content.push(det);
    }
  }
  // A stored version offered without the server's answer needs the risk acknowledgment;
  // an imported file (only offered this way) gets its own box that names its origin
  const unverified = devs.filter(d => d.store_fallback), ack = el("input");
  if (unverified.length) {
    ack.type = "checkbox";
    const plain = unverified.filter(d => d.offer_source !== "import"), imported = unverified.filter(d => d.offer_source === "import");
    if (plain.length) content.push(el("div", plain.length === devs.length ? FALLBACK_WARNING : `${plain.map(d => d.name).join(", ")}: ${FALLBACK_WARNING}`, "warnbox"));
    for (const d of imported) content.push(el("div", (devs.length === 1 ? "" : `${d.name}: `) + IMPORT_FALLBACK(d), "warnbox"));
    content.push(box("label", "check", ack, imported.length ? " I accept the risk of installing a file that Shelly has not confirmed"
      : " I accept the risk of installing without the update server's verification"));
  }
  content.push(el("p", devs.length === 1 ? "The device restarts during the installation." : "The devices restart during the installation.", "hint"));
  // An error keeps the title of this dialog
  const title = devs.length === 1 ? "Install update?" : `Install ${devs.length} updates?`;
  modal(title, content, [{text: "Cancel"},
    {text: "Approve installation", kind: "danger", action: async () => {
      const errors = [];
      for (const d of devs) {   // each installation starts in the background – calls return at once
        const body = {sha256: d.sha256, confirm: true, ...(d.store_fallback ? {acknowledged: ack.checked} : {}),
          ...(retry && restart.checked ? {restart: true} : {})};
        try { await api(`${devUrl(d)}/install`, body); selected.delete(d.id); }
        catch (e) { errors.push(`${d.name}: ${e.message}`); }
      }
      errors.length ? message(title, errors.join("\n"), "error", "chip") : closeModal();
    }}], "chip");
  const go = $("dlg-btns").lastChild;
  if (unverified.length) { go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; }; }
  if (retry) { const label = () => { go.textContent = restart.checked ? "Restart & install" : "Approve installation"; }; restart.onchange = label; label(); }
}
// ---------------------------------------------------------------- Downgrade
// Flow: list of stored older versions -> installation dialog for the selected
// version -> second confirmation with acknowledgment (as for the device password).
export const DOWNGRADE_WARNING = "A downgrade installs an older firmware. The device may refuse it, settings added by newer versions may be lost, and some devices may need a factory reset afterwards. Install only if you need this version.";
export const UPLOAD_HINT = "An uploaded file does not come from the Shelly update server; its origin is only as trustworthy as its source.";
/** Label of a stored version for lists and dialogs. */
export const fwSource = v => v.source === "import" ? "Imported (not confirmed by the Shelly update server)"
  : v.source === "upload" ? (v.sha_checked ? "Uploaded (SHA-256 checked)" : "Uploaded (SHA-256 not checked)") : "Downloaded from Shelly";
/** Tag of a stored version that does not come from Shelly's download (lists and dialogs). */
export const sourceTag = v => v.source === "upload" ? el("span", "Uploaded", "tag warn") : v.source === "import" ? el("span", "Imported", "tag warn") : null;
/** Step 1: choose one of the stored older versions. */
export async function openDowngrade(d) {
  const title = `Downgrade – ${d.name}`;
  modal(title, [el("p", "Loading stored versions …", "hint")], [{text: "Cancel"}], "history");
  let r;
  try { r = await api(`${devUrl(d)}/downgrades`); } catch (e) { message(title, e.message, "error", "history"); return; }
  if (r.blocked) { message(title, r.blocked, "error", "history"); return; }
  if (!r.versions.length) {
    message(title, `No stored version of ${r.type || d.type} is older than the installed version ${r.installed || "–"}. Older versions stay stored once newer ones were downloaded, or can be uploaded in the Firmware tab.`, "info", "history");
    return;
  }
  const list = el("div", null, "modes");
  r.versions.forEach((v, i) => {
    const radio = el("input"); radio.type = "radio"; radio.name = "dg-version"; radio.value = String(i); radio.checked = i === 0;
    const label = box("label", "check", radio, " ", el("span", v.version, "name"),
      el("span", ` · ${mb(v.size || 0)} · ${fmt(v.downloaded)} `, "sub"), sourceTag(v));
    list.append(label);
  });
  modal(title, [valueList([["Firmware ID", r.type], ["Installed", r.installed]]), el("p", "Stored older versions (newest first):"), list,
    el("p", "Newer versions are installed with “Install update”.", "hint")],
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    downgradeInstall(d, r.versions[Number(list.querySelector("input:checked").value)]);
  }}], "history");
}
/** Step 2: the known installation dialog, with the selected older version as target. */
function downgradeInstall(d, v) {
  const content = [deviceSection(d), section("Firmware", valueList([["Firmware ID", d.type],
    ["Version (older)", versionStep(d.version, v.version), "ver"], ["Size", mb(v.size || 0)], ["Source", fwSource(v), "src"], shaRow(v.sha256)])),
  el("div", DOWNGRADE_WARNING, "warnbox")];
  if (v.source === "upload") content.push(el("div", UPLOAD_HINT, "warnbox"));
  if (v.source === "import") content.push(el("div", IMPORT_WARNING, "warnbox"));
  content.push(el("p", "The device restarts during the installation.", "hint"));
  modal("Downgrade?", content, [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => downgradeConfirm(d, v)}], "history");
}
/** Hint while the option "Allow downgrades" is off (row menu, Firmware tab). */
export const DOWNGRADE_OFF = "Downgrades are off – enable “Allow downgrades” in Settings";
/** Step 3: explicit confirmation; the red button needs the acknowledgment
 *  (also the last step of a downgrade started in the Firmware tab). */
export function downgradeConfirm(d, v) {
  const title = "Downgrade?", ack = el("input"); ack.type = "checkbox";
  const imported = v.source === "import";   // the acknowledgment also names the unconfirmed file
  modal(title, [el("p", `Install ${v.version} on ${d.name} (installed: ${d.version})?`), el("div", DOWNGRADE_WARNING, "warnbox"),
    imported ? el("div", IMPORT_WARNING, "warnbox") : null,
    box("label", "check", ack, imported ? " I understand that the downgrade may fail or reset settings, and that the Shelly update server has not confirmed this file"
      : " I understand that the downgrade may fail or reset settings")].filter(Boolean),
  [{text: "Cancel"}, {text: "Downgrade", kind: "danger", action: async () => {
    try { await api(`${devUrl(d)}/install`, {sha256: v.sha256, confirm: true, downgrade: true, acknowledged: ack.checked}); closeModal(); }
    catch (e) { message(title, e.message, "error", "history"); }
  }}], "history");
  const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
}
$("scan").onclick = () => api("api/scan", {}).then(() => load(true)).catch(e => message("Check", e.message, "error"));
