// Firmware tab: stored files, changelog, Install button per device, delete and upload.
import {
  $, api, attachTip, box, checkbox, closeModal, collator, confirmDialog, copyText, el, field, filterControl, fmt, fmtSize, guarded,
  formError, icon, iconButton, input, keptDetails, loadLocal, matches, mb, message, modal, radio, saveLocal, searchText, searchTool, section,
  setTip, shaRow, sortControl, t, tn, toast, ui, uploadFile, valueList,
} from "./core.js";
import {installable, installBlocker} from "./devices.js";
import {changelogNodes, downgradeConfirm, downgradeWarning, fwSource, IMPORT_WARNING, openInstall, UPLOAD_HINT} from "./install.js";
import {render} from "./main.js";

// ---------------------------------------------------------------- Sorting
// The cards are sorted (versions inside a card stay newest first); the order is kept
// per end device. Column heads of the cards sort the cards as well.
const FW_SORT_FIELDS = [["type", t("Firmware ID")], ["version", t("Newest version")], ["size", t("Size")], ["stored", t("Stored")]];

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
// Beta and RC versions of Shelly (option "Offer beta firmware"): violet "Beta" tag and filter
const isBeta = v => /^\d{1,4}\.\d{1,4}\.\d{1,4}-(beta|rc)\d{1,3}$/.test(v || "");
// "Installed": versions Gen2+ devices run now (kept as their way back, option keep_installed);
// "Imported": files of an imported firmware store not confirmed by the Shelly update server
const FW_FILTERS = {all: [t("All"), () => true], provided: [t("Provided"), f => f.devices.length > 0],
  installed: [t("Installed"), f => (f.installed || []).length > 0],
  uploaded: [t("Uploaded"), f => f.source === "upload"], imported: [t("Imported"), f => f.source === "import"], intermediate: [t("Intermediate"), f => !!f.intermediate],
  beta: [t("Beta"), f => isBeta(f.version)], gen1: [t("Gen1"), f => f.gen === 1], unused: [t("Unused"), unused]};
/** Tooltip of "Unused" while the firmware ID of a device is not known yet (else none). */
function unusedTip() {
  const open = unidentified().length;
  if (!open) return null;
  return tn(open, "Firmware ID of 1 device not known yet (never reached) – no file counts as unused until it is checked once",
    "Firmware ID of {n} devices not known yet (never reached) – no file counts as unused until they are checked once");
}
const FW_TIPS = {unused: unusedTip, beta: () => t("Beta versions of the Shelly update server (Settings → “Offer beta firmware”)"), installed: () => t("Versions that devices run at the moment – their way back after the next update"),
  imported: () => t("Files from an imported firmware store that the Shelly update server has not confirmed yet")};
const fwFilter = filterControl(Object.entries(FW_FILTERS).map(([key, [label]]) => [key, label, FW_TIPS[key] || null]),
  key => { ui.fwFilter = key; render(true); });
$("fw-filter").replaceWith(fwFilter.node);

// ---------------------------------------------------------------- Firmware
/** Free disk space for the summary. */
const freeText = bytes => t("{size} free", {size: fmtSize(bytes)});
/** Firmware tab: one card per firmware ID, versions as rows, newest first. */
export function renderFirmware() {
  const q = searchText($("fw-search"));
  // Files in the firmware folder the store does not list (marker unknown): own card, only under "All"
  const stored = ui.firmware.filter(f => !f.unknown), unknown = ui.firmware.filter(f => f.unknown && matches(q, [f.name]));
  const found = stored.filter(f => matches(q, [f.type, f.version, ...f.devices.map(d => d.name)]));
  fwFilter.set(ui.fwFilter, Object.fromEntries(Object.entries(FW_FILTERS).map(([key, [, fn]]) => [key, found.filter(fn).length])));
  const list = found.filter(FW_FILTERS[ui.fwFilter][1]);
  const total = ui.firmwareTotal, free = ui.firmwareFree;
  // Files of the store (unknown files are listed in their own card, not counted here)
  $("fw-summary").textContent = tn(stored.length, "1 file · {size} stored", "{n} files · {size} stored", {size: mb(total)}) +
    (typeof free === "number" ? ` · ${freeText(free)}` : "");
  const groups = new Map();
  for (const f of list) { if (!groups.has(f.type)) groups.set(f.type, []); groups.get(f.type).push(f); }
  fwSortControl.set(fwSort);
  const sorted = [...groups].map(([type, files]) => [type, files.sort((a, b) => collator.compare(b.version || "", a.version || ""))]);
  const cards = sorted.sort(compareCards).map(([type, files]) => {
    const head = box("div", "card-head", el("h2", type), el("span", `Gen ${files[0].gen ?? "?"}`, "tag"),
      files.some(f => f.source !== "upload" && !f.https) ? el("span", t("HTTP only"), "tag warn") : null);
    const card = box("div", "card", head, firmwareHead());
    files.forEach((f, i) => card.append(firmwareRow(f, i === 0, i > 0 && f.gen >= 2)));
    return card;
  });
  if (unknown.length && ui.fwFilter === "all") cards.push(unknownCard(unknown));
  if (!cards.length) cards.push(box("div", "card", el("div", ui.firmware.length ? t("No firmware matches.") : t("No firmware stored."), "empty")));
  $("fw-cards").replaceChildren(...cards);
}
/** Card of the unknown files: file name, size and a delete icon each. */
function unknownCard(files) {
  const head = box("div", "card-head", el("h2", `${t("Unknown Files")} · ${files.length}`),
    el("span", t("In the firmware folder, not part of the store – delete them when they are not needed"), "sub"));
  return box("div", "card", head, ...files.map(f => {
    const del = iconButton("delete", f.deletable ? t("Delete") : (f.locked || t("Locked")), () => deleteUnknown(f), "red");
    del.disabled = !f.deletable; del.setAttribute("aria-label", f.deletable ? t("Delete {name}", {name: f.name}) : (f.locked || t("Locked")));
    return box("div", "unk-row", el("span", f.name, "mono unk-name"), el("span", mb(f.size || 0), "nowrap unk-size"), box("div", "actions", del));
  }));
}
/** Delete an unknown file after a confirmation with acknowledgment. */
function deleteUnknown(f) {
  const [ack, ackLabel] = checkbox(t("I understand that this file cannot be restored by the app"));
  confirmDialog({title: t("Delete unknown file?"), icon: "delete", button: t("Delete"), ack,
    content: [valueList([[t("File"), f.name], [t("Size"), mb(f.size || 0)]]),
      el("div", t("The file is not part of the firmware store; it is deleted for good."), "warnbox danger"), ackLabel],
    action: async () => { await api(`api/firmware/${encodeURIComponent(f.name)}/delete`, {confirm: true, acknowledged: ack.checked}); closeModal(); }});
}
/** Column titles over the version rows (wide screens; phones label the values in the tile). */
// [title, sort field]: Version, Size and Stored sort the cards
const FW_COLUMNS = [[t("Version"), "version"], [t("Size"), "size"], [t("Stored"), "stored"], ["SHA-256"], [t("Devices")], [""]];
/** Column titles; Version, Size and Stored sort the cards (click, second click reverses). */
function firmwareHead() {
  return box("div", "fw-head", ...FW_COLUMNS.map(([title, key]) => {
    const head = el("span", title);
    if (!key) return head;
    head.className = "sortable"; head.tabIndex = 0; head.role = "button"; setTip(head, t("Click to sort"));
    if (fwSort[0] === key) head.dataset.dir = fwSort[1];
    head.onclick = () => setFirmwareSort([key, fwSort[0] === key && fwSort[1] === "asc" ? "desc" : "asc"]);
    head.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); head.onclick(); } };
    return head;
  }));
}
/** Text of the "Intermediate" label (tooltip without action, also on touch). */
const INTERMEDIATE_TIP = t("Intermediate version: required step before newer firmware can be installed safely");
/** Device a firmware is provided for, with an "Install" button that opens the
 *  usual installation dialog (same conditions as "Install update" in the Devices tab). */
function firmwareDevice(entry) {
  const s = ui.state || {devices: []}, d = s.devices.find(x => x.id === entry.id);
  const blocker = installBlocker(d, s), go = el("button", t("Install"), "danger small");
  go.disabled = !!blocker; setTip(go, blocker || t("Install this firmware on the device"));
  go.setAttribute("aria-label", t("Install on {name}", {name: entry.name}));
  go.onclick = () => { if (d && installable(d, ui.state)) openInstall([d]); };
  return box("li", null, el("span", entry.name), go);
}
/** Dialog with the changelog kept with a stored firmware (nothing is requested
 *  from Shelly; a check stores it when it provides the file). */
async function openFirmwareChangelog(f) {
  const title = `${t("Changelog")} – ${f.type} ${f.version}`;
  await guarded(title, "changelog", async () => modal(title, changelogNodes(await api(`api/firmware/${encodeURIComponent(f.name)}/changelog`)), undefined, "changelog"));
}
const IMPORTED_TIP = t("Imported with a firmware store – not confirmed by the Shelly update server yet");
const devicesText = names => tn(names.length, "1 device", "{n} devices");
/** One version row with size, date, SHA (copy), devices and icon actions.
 *  older: an older Gen2+ version (gets the downgrade icon). */
function firmwareRow(f, latest, older) {
  const sha = box("span", "nowrap fw-sha", el("span", (f.sha256 || "").slice(0, 12) + "…", "mono"),
    iconButton("copy", t("Copy SHA-256"), async () => { await copyText(f.sha256 || ""); }));
  attachTip(sha, () => f.sha256 || "", true);
  const devices = f.devices.length
    ? keptDetails(`fw-${f.name}`, t("For {devices}", {devices: devicesText(f.devices)}), box("ul", null, ...f.devices.map(firmwareDevice)))
    : el("span", "–", "muted fw-none");   // no device: the placeholder of the column, not shown in the phone tile
  devices.classList.add("fw-dev");
  const dl = el("a", null, "icon"); dl.href = `api/firmware/${encodeURIComponent(f.name)}/download`; dl.setAttribute("download", "");
  setTip(dl, t("Download")); dl.setAttribute("aria-label", t("Download {firmware}", {firmware: `${f.type} ${f.version}`})); dl.append(icon("download"));
  const del = iconButton("delete", f.deletable ? t("Delete") : (f.locked || t("Locked")), () => deleteFirmware(f), "red");
  del.disabled = !f.deletable;
  const log = iconButton("changelog", f.changelog ? t("Changelog") : t("No changelog stored – it is stored by a check that provides this firmware"),
    () => openFirmwareChangelog(f));
  log.disabled = !f.changelog; log.setAttribute("aria-label", t("Changelog of {firmware}", {firmware: `${f.type} ${f.version}`}));
  // Class names only place the cells on phones (grid areas); the desktop grid is unchanged
  const uploaded = f.source === "upload" ? el("span", t("Uploaded"), "tag warn") : f.source === "import" ? el("span", t("Imported"), "tag warn") : null;
  if (uploaded) attachTip(uploaded, () => f.source === "import" ? IMPORTED_TIP : fwSource(f), true);
  // Version a device runs now: kept as its way back after the next update
  const users = f.installed || [], inst = users.length ? el("span", t("Installed"), "tag inst") : null;
  if (inst) attachTip(inst, () => t("Installed on {devices}: {names}", {devices: devicesText(users), names: users.join(", ")}) + "\n" + t("Kept as their way back after the next update"), true);
  // Once provided as an intermediate version by a check (permanent store marker)
  const step = f.intermediate ? el("span", t("Intermediate"), "tag step") : null;
  if (step) attachTip(step, () => INTERMEDIATE_TIP, true);
  // Phones: small labels in the tile (data-label, shown by CSS only there)
  const size = el("span", mb(f.size || 0), "nowrap fw-size"), date = el("span", fmt(f.downloaded), "nowrap sub fw-date");
  size.dataset.label = t("Size"); date.dataset.label = t("Stored"); sha.dataset.label = "SHA-256";
  // The labels form one group: beside the version, or together on the next line where
  // the width does not allow that (phones) – never one beside and one below
  const labels = [latest ? el("span", t("Latest"), "tag primary") : null, isBeta(f.version) ? el("span", t("Beta"), "tag beta") : null,
    inst, uploaded, step].filter(Boolean);
  const row = box("div", "fw-row", box("div", "fw-ver", el("span", f.version, "name"), labels.length ? box("span", "fw-tags", ...labels) : null),
    size, date, sha, devices, box("div", "actions", older ? downgradeButton(f) : null, log, dl, del));
  row.dataset.key = f.name;   // the keyboard focus stays in this row when the list is redrawn
  return row;
}
// ---------------------------------------------------------------- Downgrade from the Firmware tab
const STABLE = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;   // downgrades only to stable versions
/** Gen2+ devices of the file's firmware ID with a newer installed version. */
const downgradeTargets = f => STABLE.test(f.version || "") ? (ui.state?.devices || []).filter(d =>
  d.gen >= 2 && d.type === f.type && d.version && collator.compare(d.version, f.version) > 0) : [];
/** Downgrade icon of an older version, only while the option "Allow downgrades" is on;
 *  disabled (reason as title, also on touch) while no device has a newer version installed. */
function downgradeButton(f) {
  if (!ui.state?.allow_downgrade) return null;
  const reason = !downgradeTargets(f).length ? t("No device of this firmware ID has a newer version installed") : null;
  const b = iconButton("history", reason || t("Downgrade"), () => openFirmwareDowngrade(f));
  b.disabled = !!reason; b.setAttribute("aria-label", reason ? `${t("Downgrade to {version}", {version: f.version})}: ${reason}` : t("Downgrade to {version}", {version: `${f.type} ${f.version}`}));
  return b;
}
/** Dialog: the stored version, the devices that can go back to it, the warning;
 *  Continue leads to the known second confirmation of a downgrade. */
function openFirmwareDowngrade(f) {
  const targets = downgradeTargets(f), list = el("div", null, "modes");
  targets.forEach((d, i) => {
    list.append(radio("dg-device", [el("span", d.name, "name"), el("span", ` · ${t("installed {version}", {version: d.version})}`, "sub")], i === 0, String(i))[1]);
  });
  modal(t("Downgrade to {version}", {version: `${f.type} ${f.version}`}), [
    valueList([[t("Firmware ID"), f.type], [t("Version"), f.version], [t("Size"), mb(f.size || 0)], [t("Stored"), fmt(f.downloaded)],
      [t("Source"), fwSource(f), "src"], shaRow(f.sha256)]),
    section(t("Device (newer version installed)"), list), downgradeWarning(),
    f.source === "upload" ? el("div", UPLOAD_HINT, "warnbox") : f.source === "import" ? el("div", IMPORT_WARNING, "warnbox") : null].filter(Boolean),
  [{text: t("Cancel")}, {text: t("Continue"), kind: "primary", action: () => {
    downgradeConfirm(targets[Number(list.querySelector("input:checked").value)], f);
  }}], "history");
}

/** Delete a stored firmware: dialog, then a second confirmation with
 *  acknowledgment (as for the device password). Nothing is deleted automatically. */
function deleteFirmware(f) {
  const title = t("Delete firmware?"), details = valueList([[t("Firmware ID"), f.type], [t("Version"), f.version], [t("Source"), fwSource(f)], shaRow(f.sha256)]);
  // A file provided to devices may be deleted: they lose the provided update
  const held = (f.devices || []).map(d => d.name);
  // A version devices run now: without the file they have no way back after the next update
  const users = f.installed || [];
  const names = `${users.slice(0, 3).join(", ")}${users.length > 3 ? ", …" : ""}`;
  const wayBack = users.length ? el("div", tn(users.length, "Installed on 1 device ({names}): without this file it has no way back after the next update.",
    "Installed on {n} devices ({names}): without this file they have no way back after the next update.", {names}), "warnbox") : null;
  const lose = tn(held.length, "Provided for {names}: this device loses the provided update and is shown as “Available” until it is checked again.",
    "Provided for {names}: these devices lose the provided update and are shown as “Available” until they are checked again.", {names: held.join(", ")});
  modal(title, [details, wayBack, held.length ? el("div", lose, "warnbox") : null,
    // What cannot be restored: red warning box
    el("div", f.source === "upload" ? t("An uploaded file can only be restored by uploading it again.") :
    f.source === "import" ? t("An imported file can only be restored by importing its export again.") :
    t("Only versions that Shelly still offers can be downloaded again; older versions are gone for good."), "warnbox danger")].filter(Boolean),
  [{text: t("Cancel")}, {text: t("Continue"), kind: "primary", action: () => {
    const [ack, ackLabel] = checkbox(t("I understand that this file cannot be restored by the app"));
    confirmDialog({title, icon: "delete", button: t("Delete"), ack,
      content: [el("p", t("Delete {firmware} permanently?", {firmware: `${f.type} ${f.version}`})), ackLabel],
      action: async () => { await api(`api/firmware/${encodeURIComponent(f.name)}/delete`, {confirm: true, acknowledged: ack.checked}); closeModal(); }});
  }}], "delete");
}
/** Upload a firmware file (a newer one is provided as update by the next check) –
 *  step 1 choose and transfer, step 2 confirm
 *  firmware ID and version (pre-filled from the file where possible), optional SHA-256. */
function openUpload() {
  const title = t("Upload firmware"), chooser = input("file", {accept: ".zip,.bin,.gbl"}), error = formError();
  const content = [field(t("Firmware file (.zip, .bin or .gbl, max. 64 MB)"), chooser),
    el("p", (ui.state?.beta_firmware ? t("Only for Gen2+ devices known to this app, stable versions and beta versions (“Offer beta firmware” is on).")
      : t("Only for Gen2+ devices known to this app and stable versions.")) + " " +
      t("An uploaded version is used when the Shelly update server offers exactly this version, or for a manual installation while the server cannot be reached; an older one allows a downgrade. Use files from a source you trust."), "hint"),
    el("div", UPLOAD_HINT, "warnbox"), error.line];
  // A refused file brings back this step with the chosen file and the error
  const form = () => modal(title, content, [{text: t("Cancel")}, {text: t("Upload"), kind: "primary", action: async () => {
    const file = chooser.files && chooser.files[0];
    if (!file) { error.show(t("No file selected.")); return; }
    if (file.size > 64 * 1048576) { error.show(t("Firmware larger than 64 MB.")); return; }
    const status = el("p", t("Uploading {name} …", {name: file.name}), "hint");
    modal(title, [status], [], "upload");
    const progress = (sent, total) => { status.textContent = `${t("Uploading {name} …", {name: file.name})} ${Math.floor(sent * 100 / total)} %`; };
    let r;
    try { r = await uploadFile(file, undefined, progress); } catch (e) { form(); error.show(e.message); return; }
    uploadConfirm(r);
  }}], "upload");
  form();
}
function uploadConfirm(r) {
  const title = t("Upload firmware");
  if (!r.types.length) { api("api/firmware/upload/cancel", {token: r.token}).catch(() => {}); message(title, t("No Gen2+ device is known yet – run a check first."), "error", "upload"); return; }
  const type = el("select"); type.setAttribute("aria-label", t("Firmware ID"));
  for (const name of r.types) { const o = el("option", name); o.value = name; o.selected = name.toLowerCase() === String(r.detected.type || "").toLowerCase(); type.append(o); }
  // Beta versions (X.Y.Z-betaN) only while the option "Offer beta firmware" is on
  const beta = !!ui.state?.beta_firmware;
  const version = input("text", {maxLength: beta ? 24 : 14, value: r.detected.version || "", placeholder: beta ? t("e.g. 1.4.4 or 1.5.0-beta2") : t("e.g. 1.4.4")});
  const sha = input("text", {maxLength: 64, placeholder: t("optional – compared with the file"), spellcheck: false});
  const detected = r.detected.type || r.detected.version
    ? t("Read from the file: {type} · {version} – please check.", {type: r.detected.type || t("no firmware ID"), version: r.detected.version || t("no version")})
    : t("The file contains no readable firmware ID or version – please enter them.");
  const content = [valueList([[t("File"), r.filename], [t("Size"), mb(r.size)], shaRow(r.sha256)]), el("p", detected, "hint"),
    field(t("Firmware ID"), type), field(beta ? t("Version (X.Y.Z or X.Y.Z-betaN)") : t("Version (stable, X.Y.Z)"), version), field(t("Expected SHA-256"), sha)];
  if (r.duplicate) content.push(el("div", t("This file is already stored as {name}.", {name: r.duplicate}), "warnbox"));
  const error = formError(); content.push(error.line);
  const cancel = () => api("api/firmware/upload/cancel", {token: r.token}).catch(() => {}).then(closeModal);
  modal(title, content, [{text: t("Cancel"), cancel: true, action: cancel}, {text: t("Store"), kind: "primary", action: async () => {
    try {
      const res = await api("api/firmware/upload/confirm", {token: r.token, type: type.value, version: version.value.trim(), sha256: sha.value.trim()});
      toast(t("{firmware} stored", {firmware: `${res.type} ${res.version}`})); closeModal();
    } catch (e) { error.show(e.message); }   // the upload stays staged: the user corrects the values
  }}], "upload");
}
$("fw-upload").prepend(icon("upload"));   // same icon as the upload dialog title, before the text (aria-hidden)
$("fw-upload").onclick = openUpload;
$("fw-search").oninput = () => render(true);
searchTool($("fw-search"));
