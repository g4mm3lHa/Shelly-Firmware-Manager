// Firmware tab: stored files, changelog, Install button per device, delete and upload.
import {
  $, api, box, closeModal, collator, copyText, el, fmt, icon, iconButton, keptDetails, mb, message, modal,
  toast, ui, uploadFile, valueList,
} from "./core.js";
import {installable, installBlocker} from "./devices.js";
import {changelogNodes, fwSource, openInstall, UPLOAD_HINT} from "./install.js";
import {render} from "./main.js";

// ---------------------------------------------------------------- Firmware
/** Firmware tab: one card per firmware ID, versions as rows, newest first. */
export function renderFirmware() {
  const q = $("fw-search").value.trim().toLowerCase();
  const list = ui.firmware.filter(f => !q || [f.type, f.version, ...f.devices.map(d => d.name)].some(v => String(v || "").toLowerCase().includes(q)));
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
/** Device a firmware is provided for, with an "Install" button that opens the
 *  usual installation dialog (same conditions as "Install update" in the Devices tab). */
function firmwareDevice(entry) {
  const s = ui.state || {devices: []}, d = s.devices.find(x => x.id === entry.id);
  const blocker = installBlocker(d, s), go = el("button", "Install", "danger small");
  go.disabled = !!blocker; go.title = blocker || "Install this firmware on the device";
  go.setAttribute("aria-label", `Install on ${entry.name}`);
  go.onclick = () => { if (d && installable(d, ui.state)) openInstall([d]); };
  return box("li", null, el("span", entry.name), go);
}
/** Dialog with the changelog kept with a stored firmware (nothing is requested
 *  from Shelly; a check stores it when it provides the file). */
async function openFirmwareChangelog(f) {
  const title = `Changelog – ${f.type} ${f.version}`;
  try { modal(title, changelogNodes(await api(`api/firmware/${encodeURIComponent(f.name)}/changelog`))); }
  catch (e) { message(title, e.message, "error"); }
}
/** One version row with size, date, SHA (copy), devices and icon actions. */
function firmwareRow(f, latest) {
  const sha = box("span", "nowrap fw-sha", el("span", (f.sha256 || "").slice(0, 12) + "…", "mono"),
    iconButton("copy", "Copy SHA-256", async () => { await copyText(f.sha256 || ""); }));
  sha.title = f.sha256 || "";
  const devices = f.devices.length
    ? keptDetails(`fw-${f.name}`, `For ${f.devices.length} device${f.devices.length === 1 ? "" : "s"} ▾`, box("ul", null, ...f.devices.map(firmwareDevice)))
    : el("span", "–", "muted");
  devices.classList.add("fw-dev");
  const dl = el("a", null, "icon"); dl.href = `api/firmware/${encodeURIComponent(f.name)}/download`; dl.setAttribute("download", "");
  dl.title = "Download"; dl.setAttribute("aria-label", `Download ${f.type} ${f.version}`); dl.append(icon("download"));
  const del = iconButton("delete", f.deletable ? "Delete" : (f.locked || "Locked"), () => deleteFirmware(f), "red");
  del.disabled = !f.deletable;
  const log = iconButton("changelog", f.changelog ? "Changelog" : "No changelog stored – it is stored by a check that provides this firmware",
    () => openFirmwareChangelog(f));
  log.disabled = !f.changelog; log.setAttribute("aria-label", `Changelog of ${f.type} ${f.version}`);
  // Class names only place the cells on phones (grid areas); the desktop grid is unchanged
  const uploaded = f.source === "upload" ? el("span", "Uploaded", "tag warn") : null;
  if (uploaded) uploaded.title = fwSource(f);
  return box("div", "fw-row", box("div", "fw-ver", el("span", f.version, "name"), latest ? el("span", "latest", "tag primary") : null, uploaded),
    el("span", mb(f.size || 0), "nowrap fw-size"), el("span", fmt(f.downloaded), "nowrap sub fw-date"), sha, devices, box("div", "actions", log, dl, del));
}
/** Delete a stored firmware: dialog, then a second confirmation with
 *  acknowledgment (as for the device password). Nothing is deleted automatically. */
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
/** Upload a firmware file (a newer one is provided as update by the next check) –
 *  step 1 choose and transfer, step 2 confirm
 *  firmware ID and version (pre-filled from the file where possible), optional SHA-256. */
function openUpload() {
  const title = "Upload firmware", input = el("input"); input.type = "file"; input.accept = ".zip,.bin,.gbl";
  modal(title, [box("label", "field", "Firmware file (.zip, .bin or .gbl, max. 64 MiB)", input),
    el("p", "Only for Gen2+ devices known to this app and stable versions. A version newer than the installed one is provided as update by the next check, an older one allows a downgrade. Use files from a source you trust.", "hint"), el("div", UPLOAD_HINT, "warnbox")],
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
