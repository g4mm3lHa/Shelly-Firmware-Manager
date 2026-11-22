// Firmware tab: stored files, changelog, Install button per device, delete and upload.
import {
  $, api, attachTip, box, closeModal, collator, copyText, el, filterControl, fmt, icon, iconButton, keptDetails, loadLocal, mb,
  message, modal, saveLocal, searchTool, section, setTip, shaRow, sortControl, toast, ui, uploadFile, valueList,
} from "./core.js";
import {installable, installBlocker} from "./devices.js";
import {changelogNodes, DOWNGRADE_OFF, DOWNGRADE_WARNING, downgradeConfirm, fwSource, openInstall, UPLOAD_HINT} from "./install.js";
import {render} from "./main.js";

// ---------------------------------------------------------------- Sorting
// The cards are sorted (versions inside a card stay newest first); the order is kept
// per end device. Column heads of the cards sort the cards as well.
const FW_SORT_FIELDS = [["type", "Firmware ID"], ["version", "Newest version"], ["size", "Size"], ["stored", "Stored"]];
const FW_SORT_HEADS = {Version: "version", Size: "size", Stored: "stored"};   // column title -> sort field
let fwSort = ["type", "asc"];
{
  const stored = loadLocal("firmware");
  if (Array.isArray(stored) && FW_SORT_FIELDS.some(f => f[0] === stored[0])) fwSort = [stored[0], stored[1] === "desc" ? "desc" : "asc"];
}
/** Change the order of the cards (toolbar control and column heads). */
function setFirmwareSort(value) { fwSort = value; saveLocal("firmware", fwSort); render(true); }
const fwSortControl = sortControl(FW_SORT_FIELDS, setFirmwareSort);
$("fw-sort").replaceWith(fwSortControl.node);
/** Sort value of a card (its files, newest first): newest version, total size, latest storage date. */
const CARD_VALUES = {type: (type, files) => type, version: (type, files) => files[0].version || "",
  size: (type, files) => files.reduce((sum, f) => sum + (f.size || 0), 0),
  stored: (type, files) => Math.max(...files.map(f => f.downloaded || 0))};
/** Compare two cards [type, files] by the chosen field; ties by firmware ID. */
function compareCards([ta, fa], [tb, fb]) {
  const value = CARD_VALUES[fwSort[0]], x = value(ta, fa), y = value(tb, fb);
  const c = typeof x === "number" ? x - y : collator.compare(String(x), String(y));
  return (fwSort[1] === "desc" ? -c : c) || collator.compare(ta, tb);
}

// ---------------------------------------------------------------- Filters
// Filter (popover of the toolbar) over the stored files (counts respect the search); a
// card shows only its matching versions. "Unused": no device of the list has the firmware ID of the file –
// while a device was never reached (firmware ID unknown, e.g. a sleeping battery
// device) nothing counts as unused, since that device may need any of the files.
/** Devices of the list whose firmware ID is not known yet. */
const unidentified = () => (ui.state?.devices || []).filter(d => !d.type);
const unused = f => !(ui.state?.devices || []).some(d => !d.type || d.type === f.type);
const FW_FILTERS = {all: ["All", () => true], provided: ["Provided", f => f.devices.length > 0],
  uploaded: ["Uploaded", f => f.source === "upload"], intermediate: ["Intermediate", f => !!f.intermediate],
  gen1: ["Gen1", f => f.gen === 1], unused: ["Unused", unused]};
/** Tooltip of "Unused" while the firmware ID of a device is not known yet (else none). */
function unusedTip() {
  const open = unidentified().length;
  return open ? `Firmware ID of ${open} device${open === 1 ? "" : "s"} not known yet (never reached) – no file counts as unused until ${open === 1 ? "it is" : "they are"} checked once` : null;
}
const fwFilter = filterControl(Object.entries(FW_FILTERS).map(([key, [label]]) => [key, label, key === "unused" ? unusedTip : null]),
  key => { ui.fwFilter = key; render(true); });
$("fw-filter").replaceWith(fwFilter.node);

// ---------------------------------------------------------------- Firmware
/** Free disk space for the summary: MB, from 1024 MB on in GB (one decimal). */
const freeText = bytes => bytes >= 1024 * 1048576 ? `${(bytes / 1073741824).toFixed(1)} GB free` : `${mb(bytes)} free`;
/** Firmware tab: one card per firmware ID, versions as rows, newest first. */
export function renderFirmware() {
  const q = $("fw-search").value.trim().toLowerCase();
  // Files in the firmware folder the store does not list (marker unknown): own card, only under "All"
  const stored = ui.firmware.filter(f => !f.unknown), unknown = ui.firmware.filter(f => f.unknown && (!q || f.name.toLowerCase().includes(q)));
  const found = stored.filter(f => !q || [f.type, f.version, ...f.devices.map(d => d.name)].some(v => String(v || "").toLowerCase().includes(q)));
  fwFilter.set(ui.fwFilter, Object.fromEntries(Object.entries(FW_FILTERS).map(([key, [, fn]]) => [key, found.filter(fn).length])));
  const list = found.filter(FW_FILTERS[ui.fwFilter][1]);
  const total = ui.firmwareTotal, free = ui.firmwareFree;
  // Files of the store (unknown files are listed in their own card, not counted here)
  $("fw-summary").textContent = `${stored.length} file${stored.length === 1 ? "" : "s"} · ${mb(total)} stored` +
    (typeof free === "number" ? ` · ${freeText(free)}` : "");
  const groups = new Map();
  for (const f of list) { if (!groups.has(f.type)) groups.set(f.type, []); groups.get(f.type).push(f); }
  fwSortControl.set(fwSort);
  const sorted = [...groups].map(([type, files]) => [type, files.sort((a, b) => collator.compare(b.version || "", a.version || ""))]);
  const cards = sorted.sort(compareCards).map(([type, files]) => {
    const head = box("div", "card-head", el("h2", type), el("span", `Gen ${files[0].gen ?? "?"}`, "tag"),
      files.some(f => f.source !== "upload" && !f.https) ? el("span", "HTTP only", "tag warn") : null);
    const card = box("div", "card", head, firmwareHead());
    files.forEach((f, i) => card.append(firmwareRow(f, i === 0, i > 0 && f.gen >= 2)));
    return card;
  });
  if (unknown.length && ui.fwFilter === "all") cards.push(unknownCard(unknown));
  if (!cards.length) cards.push(box("div", "card", el("div", ui.firmware.length ? "No firmware matches." : "No firmware stored.", "empty")));
  $("fw-cards").replaceChildren(...cards);
}
/** Card of the unknown files: file name, size and a delete icon each. */
function unknownCard(files) {
  const head = box("div", "card-head", el("h2", `Unknown Files · ${files.length}`),
    el("span", "In the firmware folder, not part of the store – delete them when they are not needed", "sub"));
  return box("div", "card", head, ...files.map(f => {
    const del = iconButton("delete", f.deletable ? "Delete" : (f.locked || "Locked"), () => deleteUnknown(f), "red");
    del.disabled = !f.deletable; del.setAttribute("aria-label", f.deletable ? `Delete ${f.name}` : (f.locked || "Locked"));
    return box("div", "unk-row", el("span", f.name, "mono unk-name"), el("span", mb(f.size || 0), "nowrap unk-size"), box("div", "actions", del));
  }));
}
/** Delete an unknown file after a confirmation with acknowledgment. */
function deleteUnknown(f) {
  const title = "Delete unknown file?", ack = el("input"); ack.type = "checkbox";
  modal(title, [valueList([["File", f.name], ["Size", mb(f.size || 0)]]),
    el("div", "The file is not part of the firmware store; it is deleted for good.", "warnbox danger"),
    box("label", "check", ack, " I understand that this file cannot be restored by the app")],
  [{text: "Cancel"}, {text: "Delete", kind: "danger", action: async () => {
    try { await api(`api/firmware/${encodeURIComponent(f.name)}/delete`, {confirm: true, acknowledged: ack.checked}); closeModal(); }
    catch (e) { message(title, e.message, "error", "delete"); }
  }}], "delete");
  const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
}
/** Column titles over the version rows (wide screens; phones label the values in the tile). */
const FW_COLUMNS = ["Version", "Size", "Stored", "SHA-256", "Devices", ""];
/** Column titles; Version, Size and Stored sort the cards (click, second click reverses). */
function firmwareHead() {
  return box("div", "fw-head", ...FW_COLUMNS.map(t => {
    const key = FW_SORT_HEADS[t], head = el("span", t);
    if (!key) return head;
    head.className = "sortable"; head.tabIndex = 0; head.role = "button"; setTip(head, "Click to sort");
    if (fwSort[0] === key) head.dataset.dir = fwSort[1];
    head.onclick = () => setFirmwareSort([key, fwSort[0] === key && fwSort[1] === "asc" ? "desc" : "asc"]);
    head.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); head.onclick(); } };
    return head;
  }));
}
/** Text of the "Intermediate" label (tooltip without action, also on touch). */
const INTERMEDIATE_TIP = "Intermediate version: required step before newer firmware can be installed safely";
/** Device a firmware is provided for, with an "Install" button that opens the
 *  usual installation dialog (same conditions as "Install update" in the Devices tab). */
function firmwareDevice(entry) {
  const s = ui.state || {devices: []}, d = s.devices.find(x => x.id === entry.id);
  const blocker = installBlocker(d, s), go = el("button", "Install", "danger small");
  go.disabled = !!blocker; setTip(go, blocker || "Install this firmware on the device");
  go.setAttribute("aria-label", `Install on ${entry.name}`);
  go.onclick = () => { if (d && installable(d, ui.state)) openInstall([d]); };
  return box("li", null, el("span", entry.name), go);
}
/** Dialog with the changelog kept with a stored firmware (nothing is requested
 *  from Shelly; a check stores it when it provides the file). */
async function openFirmwareChangelog(f) {
  const title = `Changelog – ${f.type} ${f.version}`;
  try { modal(title, changelogNodes(await api(`api/firmware/${encodeURIComponent(f.name)}/changelog`)), undefined, "changelog"); }
  catch (e) { message(title, e.message, "error", "changelog"); }
}
/** One version row with size, date, SHA (copy), devices and icon actions.
 *  older: an older Gen2+ version (gets the downgrade icon). */
function firmwareRow(f, latest, older) {
  const sha = box("span", "nowrap fw-sha", el("span", (f.sha256 || "").slice(0, 12) + "…", "mono"),
    iconButton("copy", "Copy SHA-256", async () => { await copyText(f.sha256 || ""); }));
  attachTip(sha, () => f.sha256 || "", true);
  const devices = f.devices.length
    ? keptDetails(`fw-${f.name}`, `For ${f.devices.length} device${f.devices.length === 1 ? "" : "s"}`, box("ul", null, ...f.devices.map(firmwareDevice)))
    : el("span", "–", "muted");
  devices.classList.add("fw-dev");
  const dl = el("a", null, "icon"); dl.href = `api/firmware/${encodeURIComponent(f.name)}/download`; dl.setAttribute("download", "");
  setTip(dl, "Download"); dl.setAttribute("aria-label", `Download ${f.type} ${f.version}`); dl.append(icon("download"));
  const del = iconButton("delete", f.deletable ? "Delete" : (f.locked || "Locked"), () => deleteFirmware(f), "red");
  del.disabled = !f.deletable;
  const log = iconButton("changelog", f.changelog ? "Changelog" : "No changelog stored – it is stored by a check that provides this firmware",
    () => openFirmwareChangelog(f));
  log.disabled = !f.changelog; log.setAttribute("aria-label", `Changelog of ${f.type} ${f.version}`);
  // Class names only place the cells on phones (grid areas); the desktop grid is unchanged
  const uploaded = f.source === "upload" ? el("span", "Uploaded", "tag warn") : null;
  if (uploaded) attachTip(uploaded, () => fwSource(f), true);
  // Once provided as an intermediate version by a check (permanent store marker)
  const step = f.intermediate ? el("span", "Intermediate", "tag step") : null;
  if (step) attachTip(step, () => INTERMEDIATE_TIP, true);
  // Phones: small labels in the tile (data-label, shown by CSS only there)
  const size = el("span", mb(f.size || 0), "nowrap fw-size"), date = el("span", fmt(f.downloaded), "nowrap sub fw-date");
  size.dataset.label = "Size"; date.dataset.label = "Stored"; sha.dataset.label = "SHA-256";
  return box("div", "fw-row", box("div", "fw-ver", el("span", f.version, "name"), latest ? el("span", "Latest", "tag primary") : null, uploaded, step),
    size, date, sha, devices, box("div", "actions", older ? downgradeButton(f) : null, log, dl, del));
}
// ---------------------------------------------------------------- Downgrade from the Firmware tab
const STABLE = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;   // downgrades only to stable versions
/** Gen2+ devices of the file's firmware ID with a newer installed version. */
const downgradeTargets = f => STABLE.test(f.version || "") ? (ui.state?.devices || []).filter(d =>
  d.gen >= 2 && d.type === f.type && d.version && collator.compare(d.version, f.version) > 0) : [];
/** Downgrade icon of an older version; disabled (reason as title, also on touch)
 *  while the option is off or no device has a newer version installed. */
function downgradeButton(f) {
  const reason = !ui.state?.allow_downgrade ? DOWNGRADE_OFF : !downgradeTargets(f).length
    ? "No device of this firmware ID has a newer version installed" : null;
  const b = iconButton("history", reason || "Downgrade", () => openFirmwareDowngrade(f));
  b.disabled = !!reason; b.setAttribute("aria-label", reason ? `Downgrade to ${f.version}: ${reason}` : `Downgrade to ${f.type} ${f.version}`);
  return b;
}
/** Dialog: the stored version, the devices that can go back to it, the warning;
 *  Continue leads to the known second confirmation of a downgrade. */
function openFirmwareDowngrade(f) {
  const targets = downgradeTargets(f), list = el("div", null, "modes");
  targets.forEach((d, i) => {
    const radio = el("input"); radio.type = "radio"; radio.name = "dg-device"; radio.value = String(i); radio.checked = i === 0;
    list.append(box("label", "check", radio, " ", el("span", d.name, "name"), el("span", ` · installed ${d.version}`, "sub")));
  });
  modal(`Downgrade to ${f.type} ${f.version}`, [
    valueList([["Firmware ID", f.type], ["Version", f.version], ["Size", mb(f.size || 0)], ["Stored", fmt(f.downloaded)],
      ["Source", fwSource(f), "src"], shaRow(f.sha256)]),
    section("Device (newer version installed)", list), el("div", DOWNGRADE_WARNING, "warnbox"),
    f.source === "upload" ? el("div", UPLOAD_HINT, "warnbox") : null].filter(Boolean),
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    downgradeConfirm(targets[Number(list.querySelector("input:checked").value)], f);
  }}], "history");
}

/** Delete a stored firmware: dialog, then a second confirmation with
 *  acknowledgment (as for the device password). Nothing is deleted automatically. */
function deleteFirmware(f) {
  const title = "Delete firmware?", details = valueList([["Firmware ID", f.type], ["Version", f.version], ["Source", fwSource(f)], shaRow(f.sha256)]);
  // A file provided to devices may be deleted: they lose the provided update
  const held = (f.devices || []).map(d => d.name);
  modal(title, [details, held.length ? el("div", `Provided for ${held.join(", ")}: ${held.length === 1 ? "this device loses" : "these devices lose"} the provided update and ${held.length === 1 ? "is" : "are"} shown as "Available" until ${held.length === 1 ? "it is" : "they are"} checked again.`, "warnbox") : null,
    // What cannot be restored: red warning box
    el("div", f.source === "upload" ? "An uploaded file can only be restored by uploading it again." :
    "Only versions that Shelly still offers can be downloaded again; older versions are gone for good.", "warnbox danger")].filter(Boolean),
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    const ack = el("input"); ack.type = "checkbox";
    modal(title, [el("p", `Delete ${f.type} ${f.version} permanently?`), box("label", "check", ack, " I understand that this file cannot be restored by the app")],
      [{text: "Cancel"}, {text: "Delete", kind: "danger", action: async () => {
        try { await api(`api/firmware/${encodeURIComponent(f.name)}/delete`, {confirm: true, acknowledged: ack.checked}); closeModal(); }
        catch (e) { message(title, e.message, "error", "delete"); }
      }}], "delete");
    const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
  }}], "delete");
}
/** Upload a firmware file (a newer one is provided as update by the next check) –
 *  step 1 choose and transfer, step 2 confirm
 *  firmware ID and version (pre-filled from the file where possible), optional SHA-256. */
function openUpload() {
  const title = "Upload firmware", input = el("input"); input.type = "file"; input.accept = ".zip,.bin,.gbl";
  modal(title, [box("label", "field", "Firmware file (.zip, .bin or .gbl, max. 64 MB)", input),
    el("p", "Only for Gen2+ devices known to this app and stable versions. An uploaded version is used when the Shelly update server offers exactly this version, or for a manual installation while the server cannot be reached; an older one allows a downgrade. Use files from a source you trust.", "hint"), el("div", UPLOAD_HINT, "warnbox")],
  [{text: "Cancel"}, {text: "Upload", kind: "primary", action: async () => {
    const file = input.files && input.files[0];
    if (!file) { message(title, "No file selected.", "error", "upload"); return; }
    if (file.size > 64 * 1048576) { message(title, "Firmware larger than 64 MB.", "error", "upload"); return; }
    const status = el("p", `Uploading ${file.name} …`, "hint");
    modal(title, [status], [], "upload");
    const progress = (sent, total) => { status.textContent = `Uploading ${file.name} … ${Math.floor(sent * 100 / total)} %`; };
    try { uploadConfirm(await uploadFile(file, undefined, progress)); } catch (e) { message(title, e.message, "error", "upload"); }
  }}], "upload");
}
function uploadConfirm(r) {
  const title = "Upload firmware";
  if (!r.types.length) { api("api/firmware/upload/cancel", {token: r.token}).catch(() => {}); message(title, "No Gen2+ device is known yet – run a check first.", "error", "upload"); return; }
  const type = el("select"); type.setAttribute("aria-label", "Firmware ID");
  for (const t of r.types) { const o = el("option", t); o.value = t; o.selected = t.toLowerCase() === String(r.detected.type || "").toLowerCase(); type.append(o); }
  const version = el("input"); version.type = "text"; version.maxLength = 14; version.placeholder = "e.g. 1.4.4"; version.value = r.detected.version || "";
  const sha = el("input"); sha.type = "text"; sha.maxLength = 64; sha.placeholder = "optional – compared with the file"; sha.spellcheck = false;
  const detected = r.detected.type || r.detected.version
    ? `Read from the file: ${r.detected.type || "no firmware ID"} · ${r.detected.version || "no version"} – please check.`
    : "The file contains no readable firmware ID or version – please enter them.";
  const content = [valueList([["File", r.filename], ["Size", mb(r.size)], shaRow(r.sha256)]), el("p", detected, "hint"),
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
  }}], "upload");
}
$("fw-upload").prepend(icon("upload"));   // same icon as the upload dialog title, before the text (aria-hidden)
$("fw-upload").onclick = openUpload;
$("fw-search").oninput = () => render(true);
searchTool($("fw-search"));
