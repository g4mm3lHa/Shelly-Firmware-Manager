// Installation and downgrade dialogs (approval bound to the SHA-256 of the provided file).
import {$, api, box, closeModal, devUrl, el, fmt, mb, message, modal, section, selected, shaRow, valueList} from "./core.js";
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
  upload: "Firmware store (uploaded file)"};
const OFFER_SHORT = {manufacturer: "Shelly server", store: "Store", upload: "Upload"};
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
/** Confirmation dialog: one device with all details, several as an overview; approval bound to the SHA-256. */
export function openInstall(devs) {
  const content = [];
  if (devs.length === 1) {
    const d = devs[0];
    content.push(deviceSection(d), section("Firmware", valueList([["Firmware ID", d.type], ["Version", versionStep(d.version, d.offer_version), "ver"],
      ["Size", mb(d.size || 0)], ["Source", offerSource(d), "src"], shaRow(d.sha256)])));
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
    content.push(t);
    if (devs.some(d => d.gen === 1)) content.push(el("div", GEN1_WARNING, "warnbox"));
    if (devs.some(d => d.offer_source === "upload")) content.push(el("div", UPLOAD_HINT, "warnbox"));
    for (const d of devs.filter(x => x.step_target != null)) content.push(el("p", stepHint(d), "hint"));
    for (const d of devs) {
      const det = el("details"); det.append(el("summary", `Changelog – ${d.name}`));
      det.ontoggle = () => { if (det.open && det.children.length === 1) det.append(changelogBox(d)); };
      content.push(det);
    }
  }
  // A stored version offered without the server's answer needs the risk acknowledgment
  const unverified = devs.filter(d => d.store_fallback), ack = el("input");
  if (unverified.length) {
    ack.type = "checkbox";
    content.push(el("div", unverified.length === devs.length ? FALLBACK_WARNING
      : `${unverified.map(d => d.name).join(", ")}: ${FALLBACK_WARNING}`, "warnbox"),
    box("label", "check", ack, " I accept the risk of installing without the update server's verification"));
  }
  content.push(el("p", devs.length === 1 ? "The device restarts during the installation." : "The devices restart during the installation.", "hint"));
  // An error keeps the title of this dialog
  const title = devs.length === 1 ? "Install update?" : `Install ${devs.length} updates?`;
  modal(title, content, [{text: "Cancel"},
    {text: "Approve installation", kind: "danger", action: async () => {
      const errors = [];
      for (const d of devs) {   // each installation starts in the background – calls return at once
        const body = {sha256: d.sha256, confirm: true, ...(d.store_fallback ? {acknowledged: ack.checked} : {})};
        try { await api(`${devUrl(d)}/install`, body); selected.delete(d.id); }
        catch (e) { errors.push(`${d.name}: ${e.message}`); }
      }
      errors.length ? message(title, errors.join("\n"), "error", "chip") : closeModal();
    }}], "chip");
  if (unverified.length) { const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; }; }
}
// ---------------------------------------------------------------- Downgrade
// Flow: list of stored older versions -> installation dialog for the selected
// version -> second confirmation with acknowledgment (as for the device password).
export const DOWNGRADE_WARNING = "A downgrade installs an older firmware. The device may refuse it, settings added by newer versions may be lost, and some devices may need a factory reset afterwards. Install only if you need this version.";
export const UPLOAD_HINT = "An uploaded file does not come from the Shelly update server; its origin is only as trustworthy as its source.";
/** Label of a stored version for lists and dialogs. */
export const fwSource = v => v.source === "upload" ? (v.sha_checked ? "Uploaded (SHA-256 checked)" : "Uploaded (SHA-256 not checked)") : "Downloaded from Shelly";
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
      el("span", ` · ${mb(v.size || 0)} · ${fmt(v.downloaded)} `, "sub"), v.source === "upload" ? el("span", "Uploaded", "tag warn") : null);
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
  content.push(el("p", "The device restarts during the installation.", "hint"));
  modal("Downgrade?", content, [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => downgradeConfirm(d, v)}], "history");
}
/** Hint while the option "Allow downgrades" is off (row menu, Firmware tab). */
export const DOWNGRADE_OFF = "Downgrades are off – enable “Allow downgrades” in Settings";
/** Step 3: explicit confirmation; the red button needs the acknowledgment
 *  (also the last step of a downgrade started in the Firmware tab). */
export function downgradeConfirm(d, v) {
  const title = "Downgrade?", ack = el("input"); ack.type = "checkbox";
  modal(title, [el("p", `Install ${v.version} on ${d.name} (installed: ${d.version})?`), el("div", DOWNGRADE_WARNING, "warnbox"),
    box("label", "check", ack, " I understand that the downgrade may fail or reset settings")],
  [{text: "Cancel"}, {text: "Downgrade", kind: "danger", action: async () => {
    try { await api(`${devUrl(d)}/install`, {sha256: v.sha256, confirm: true, downgrade: true, acknowledged: ack.checked}); closeModal(); }
    catch (e) { message(title, e.message, "error", "history"); }
  }}], "history");
  const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
}
$("scan").onclick = () => api("api/scan", {}).then(() => load(true)).catch(e => message("Check", e.message, "error"));
