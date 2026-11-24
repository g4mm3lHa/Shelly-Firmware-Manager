// Tab "Backups": the stored backups of the device settings per Shelly, "Back up now",
// download and import of single backups, and restoring a backup to its device (preview
// of the differences, confirmation, progress, result). The server reads, stores and
// writes (manager_backups); the page shows its lists and the state of its two jobs
// (backup run of several devices, restore).
import {
  $, ackGate, api, box, cell, checkbox, checkLine, closeModal, collator, confirmDialog, devUrl, deviceChoice, el, filePassword, filterControl,
  fmt, fmtSize, formError, gateButton, guarded, icon, iconButton, input, matches, message, messageBody, modal, modalOpen, modelGen,
  passwordCopy, searchText, searchTool, section, setTip, startDownload, t, tn, toast, ui, uploadFile, valueList,
} from "./core.js";
import {render} from "./main.js";
import {areaChoice} from "./overview.js";

// ---------------------------------------------------------------- Texts and small parts
// Reason of a backup: [label, tag class]; the automatic ones before a change are coloured
const REASONS = {manual: [t("Manual"), ""], update: [t("Before update"), "primary"], downgrade: [t("Before downgrade"), "primary"],
  schedule: [t("Schedule"), ""], restore: [t("Before restore"), "primary"]};
/** Label of the reason of a backup ("Manual", "Before update" …). */
export const reasonLabel = reason => (REASONS[reason] || [reason || "–"])[0];
// A device whose newest backup is older than this many days is listed by the filter "Older than 30 days"
const OLD_DAYS = 30;
// The dialog of a running job asks the server for its state this often
const POLL_MS = 1000;
/** What a backup contains ("Settings · 2 scripts · 3 schedules · 1 webhook"; Gen1:
 *  "Settings · 1 action"). */
function partsText(p) {
  if (!p) return "–";
  if (p.actions != null) return [t("Settings"), tn(p.actions, "{n} action", "{n} actions")].join(" · ");
  return [t("Settings"), tn(p.scripts ?? 0, "{n} script", "{n} scripts"), tn(p.schedules ?? 0, "{n} schedule", "{n} schedules"),
    tn(p.webhooks ?? 0, "{n} webhook", "{n} webhooks")].join(" · ");
}
/** "2026-11-22 03:30 · Schedule" of a backup. */
const backupText = b => `${fmt(b.at)} · ${reasonLabel(b.reason)}`;
/** Reason tag of a backup with its note (update: "1.4.0 → 1.5.0", schedule: its name)
 *  and the mark of an imported backup. */
function reasonTag(b) {
  const [label, cls] = REASONS[b.reason] || [b.reason || "–", ""];
  return box("span", "bk-why", el("span", label, "tag " + cls), b.note ? el("span", b.reason === "schedule" ? `“${b.note}”` : b.note, "sub") : null,
    b.imported ? el("span", t("Imported"), "tag") : null);
}
/** Cell of a row; on phones a labelled line of the tile (data-label). */
const bkCell = (label, cls, ...nodes) => cell("bk-c " + cls, label, ...nodes);
/** Head of a dialog about one device: name and model left, an optional pill right. */
const deviceHead = (d, pill) => box("div", "bk-dhead", box("div", null, el("div", d.name, "name"), el("div", modelGen(d), "sub")),
  pill ? el("span", pill, "pill") : null);
/** Backups of a device that count for the limit ("n of N"): the kept ones do not. */
const counted = list => list.filter(b => !b.kept).length;
/** "7 of 10" and, if some are kept, "+ 2 kept". */
function storedText(list, keep) {
  const kept = list.length - counted(list);
  return t("{n} of {keep}", {n: counted(list), keep}) + (kept ? " " + t("+ {n} kept", {n: kept}) : "");
}

// ---------------------------------------------------------------- Data
/** Fetch the lists of the tab again and render. */
async function reloadBackups() {
  ui.backups = await api("api/backups");
  render(true);
}
/** Where a dialog returns to when it is done: the tab (dialog closed, lists fetched
 *  again) or the "Backups" dialog of a device. */
const toTab = async () => { closeModal(); try { await reloadBackups(); } catch (e) { toast(e.message, true); } };
/** A job runs that reads or writes devices: no second one is started meanwhile. */
const jobRunning = s => !!(s && ((s.backup_job && !s.backup_job.finished) || (s.restore && !s.restore.finished)));
/** Follow a job of the server while its dialog shows node: draw(state) is called with
 *  every fresh state until it returns true (the job ended) or the dialog shows something
 *  else. A failed request (app restarting, session renewed) is repeated. */
async function follow(node, draw) {
  for (;;) {
    let s = null;
    try { s = await api("api/state"); } catch (e) { s = null; }
    if (!node.isConnected || !modalOpen()) return;
    if (s && draw(s)) return;
    await new Promise(resolve => { setTimeout(resolve, POLL_MS); });
  }
}

// ---------------------------------------------------------------- One backup (row)
/** One backup as a row: the tab and the "Backups" dialog of a device use the same rows.
 *  d: its device (id null: no longer listed – download and delete only); back: where
 *  the dialogs opened from the row return to. */
function backupRow(d, b, back) {
  const restore = el("button", t("Restore"), "small"); restore.onclick = () => openRestore(d, b, back);
  if (!d.id) { restore.disabled = true; setTip(restore, t("The device of this backup is no longer listed – it can only be downloaded or deleted")); }
  const when = fmt(b.at);
  const keep = iconButton("pin", b.kept ? t("Kept – click to let it be removed automatically again") : t("Keep – never remove this backup automatically"),
    async () => {
      try { await api(`api/backups/${b.id}/keep`, {kept: !b.kept}); await back(); }
      catch (e) { toast(e.message, true); }
    }, "bk-keep" + (b.kept ? " on" : ""));
  keep.setAttribute("aria-pressed", String(!!b.kept));
  const row = box("div", "bk-item",
    bkCell("", "bk-date", el("span", when, "nowrap"), b.kept ? box("span", "bk-pin", icon("pin"), el("span", t("Kept"))) : null),
    bkCell(t("Reason"), "bk-reason", reasonTag(b)),
    bkCell(t("Firmware"), "bk-fw", el("span", b.fw || "–", "nowrap")),
    bkCell(t("Contents"), "bk-parts", el("span", partsText(b.parts)), b.parts && b.parts.partial ? el("span", t("Actions not readable"), "tag warn") : null),
    bkCell(t("Size"), "bk-size", el("span", fmtSize(b.size), "nowrap")),
    bkCell("", "bk-btns", restore,
      iconButton("download", t("Download the backup of {time}", {time: when}), () => openDownload(d, b, back)),
      keep,
      iconButton("delete", t("Delete the backup of {time}", {time: when}), () => deleteBackup(d, b, back), "red")));
  row.dataset.key = b.id;   // the keyboard focus stays in this row when the list is redrawn
  return row;
}
const listHead = () => box("div", "bk-item bk-head", ...[[t("Date"), "bk-date"], [t("Reason"), "bk-reason"], [t("Firmware"), "bk-fw"],
  [t("Contents"), "bk-parts"], [t("Size"), "bk-size"], ["", "bk-btns"]].map(([text, c]) => el("div", text, "bk-c " + c)));

// ---------------------------------------------------------------- Tab "Backups"
// Devices whose backups are expanded (device ID, or "mac:…" of one no longer listed) and the filter
const view = {open: new Set(), filter: "all"};
const keyOf = d => d.id || `mac:${d.mac}`;
/** Why a device has no backup (card "No Backup"). */
function noneText(none) {
  const time = none.at ? fmt(none.at) : "–";
  switch (none.reason) {
    case "removed": return t("Removed from Home Assistant – not backed up any more.");
    case "password": return t("Protected, and its password is not stored in this app.");
    case "asleep": return t("Battery device – asleep at every attempt. It is backed up as soon as a run reaches it awake.");
    case "unreachable": return t("Not reachable at the last attempt ({time}).", {time});
    case "failed": return t("The last attempt failed ({time}): {error}", {time, error: none.message || "–"});
    default: return t("No backup yet.");
  }
}
/** Why "Back up now" is not possible for a device right now (tooltip), or null. */
function backupBlocker(d, s) {
  if (!d.id) return t("No longer listed");
  if (d.removed) return t("Removed from Home Assistant – not backed up any more.");
  if (jobRunning(s)) return s.restore && !s.restore.finished ? t("A backup is being restored") : t("A backup run is in progress");
  return null;
}
/** Row of a device with backups; expanded: its backups below it. */
function deviceRow(d, keep) {
  const list = d.backups, last = list[0], key = keyOf(d), open = view.open.has(key);
  const total = list.reduce((sum, b) => sum + (b.size || 0), 0);
  const toggle = el("button", null, "icon bk-tog" + (open ? " open" : "")); toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? t("Hide the backups of {name}", {name: d.name}) : t("Show the backups of {name}", {name: d.name}));
  toggle.onclick = () => { open ? view.open.delete(key) : view.open.add(key); render(true); };
  const blocker = backupBlocker(d, ui.state);
  const now = iconButton("backup", blocker || t("Back up {name} now", {name: d.name}), () => backupOne(d, toTab));
  now.disabled = !!blocker; now.setAttribute("aria-label", t("Back up {name} now", {name: d.name}));
  const sub = [modelGen(d), d.id ? null : t("no longer listed")].filter(Boolean).join(" · ");
  const row = box("div", "bk-row" + (open ? " open" : ""),
    bkCell("", "bk-chev", toggle),
    bkCell("", "bk-name", box("div", "name", d.name, d.removed ? el("span", t("Removed"), "tag warn") : null), el("div", sub, "sub")),
    bkCell(t("Latest backup"), "bk-last", el("span", fmt(last.at), "nowrap"), reasonTag(last)),
    bkCell(t("Firmware"), "bk-fw", el("span", last.fw || "–", "nowrap")),
    bkCell(t("Stored"), "bk-count", el("span", storedText(list, keep))),
    bkCell(t("Size"), "bk-size", el("span", fmtSize(total), "nowrap")),
    bkCell("", "bk-btns", now));
  row.dataset.key = key;
  return open ? [row, box("div", "bk-list", listHead(), ...list.map(b => backupRow(d, b, toTab)))] : [row];
}
/** Row of a device without a backup: the reason and "Back up now". */
function noneRow(d) {
  const now = el("button", t("Back up now"), "small");
  const blocker = backupBlocker(d, ui.state) || (d.none.reason === "password" ? t("Store the device password first (device menu)") : null);
  now.disabled = !!blocker; if (blocker) setTip(now, blocker);
  now.setAttribute("aria-label", t("Back up {name} now", {name: d.name}));
  now.onclick = () => backupOne(d, toTab);
  const row = box("div", "bk-row bk-none",
    bkCell("", "bk-name", el("div", d.name, "name"), el("div", modelGen(d), "sub")),
    bkCell("", "bk-why-none", box("span", "bk-warn", icon("warn")), el("span", noneText(d.none))),
    bkCell("", "bk-btns", now));
  row.dataset.key = keyOf(d);
  return row;
}
const rowHead = labels => box("div", "bk-row bk-head", ...labels.map(([text, c]) => el("div", text, "bk-c " + c)));
const bkFilter = filterControl([["all", t("All")], ["with", t("With backup")], ["none", t("No backup")], ["old", t("Older than 30 days")]],
  key => { view.filter = key; render(true); });
/** Summary of the toolbar: "143 backups of 22 devices · 2.1 MB" and the lock "Stored encrypted". */
function totalsText(totals) {
  if (!totals.count) return t("No backups stored");
  const counts = totals.count === 1 ? t("1 backup of 1 device")
    : totals.devices === 1 ? t("{n} backups of 1 device", {n: totals.count}) : t("{n} backups of {devices} devices", {n: totals.count, devices: totals.devices});
  return `${counts} · ${fmtSize(totals.size)}`;
}
/** Bar above the cards while a job runs or its result was not looked at yet (the
 *  dialog was closed or the page loaded anew): what it is and "Show". */
function jobNotes(s) {
  const notes = [], job = s.backup_job, restore = s.restore;
  const note = (running, text, open) => {
    const show = el("button", running ? t("Show") : t("Show result")); show.onclick = open;
    notes.push(box("div", "notice bk-job", box("span", null, running ? el("span", null, "spinner") : null, " " + text), box("div", "notice-btns", show)));
  };
  if (job) note(!job.finished, job.finished ? t("Backup run finished: {done} of {total} devices.", {done: job.done, total: job.total})
    : t("Backing up … {done} of {total} devices", {done: job.done, total: job.total}), openRun);
  if (restore) note(!restore.finished, restore.finished ? t("{name}: restoring the backup has finished.", {name: restore.name})
    : t("{name}: a backup is being restored …", {name: restore.name}), () => openRestoreProgress(toTab));
  return notes;
}
/** Tab "Backups": every Shelly with its stored backups, then the Shellys without one. */
export function renderBackups() {
  const v = ui.backups, s = ui.state;
  if (!v) { $("bk-cards").replaceChildren(box("div", "card", box("div", "card-body", el("p", t("Loading …"), "hint")))); return; }
  const q = searchText($("bk-search"));
  const devs = v.devices.filter(d => matches(q, [d.name, d.shelly_name, d.model]));
  const withBackup = devs.filter(d => d.backups.length), none = devs.filter(d => !d.backups.length);
  const limit = Date.now() / 1000 - OLD_DAYS * 86400, old = withBackup.filter(d => d.backups[0].at < limit);
  bkFilter.set(view.filter, {all: devs.length, with: withBackup.length, none: none.length, old: old.length});
  const summary = [el("span", totalsText(v.totals))];
  if (v.totals.count) summary.push(box("span", "bk-enc", icon("lock"), el("span", t("Stored encrypted"))));
  $("bk-summary").replaceChildren(...summary);
  const blocked = jobRunning(s) && !(s.backup_job && !s.backup_job.finished);   // a restore runs: no backup run
  $("bk-now").disabled = blocked; setTip($("bk-now"), blocked ? t("A backup is being restored") : "");
  const cards = jobNotes(s);
  if (v.warning) cards.push(el("div", v.warning, "warnbox"));
  if (!v.totals.count) {
    cards.push(box("div", "card", box("div", "card-head", el("h2", t("Backups · {n}", {n: 0}))),
      el("div", t("No backups yet – “Back up now” stores the settings of your Shellys in this app. A backup is also stored automatically before every installation, and a schedule with the action “Backup” does it regularly (Schedules tab)."), "empty")));
  } else {
    const shown = view.filter === "old" ? old : view.filter === "none" ? [] : withBackup;
    const listed = ["all", "none"].includes(view.filter) ? none : [];
    if (view.filter !== "none") cards.push(box("div", "card backups", box("div", "card-head", el("h2", t("Backups · {n}", {n: shown.length})),
      el("span", t("The newest {n} per device are kept", {n: v.keep}), "sub")),
      ...(shown.length ? [rowHead([["", "bk-chev"], [t("Device"), "bk-name"], [t("Latest backup"), "bk-last"], [t("Firmware"), "bk-fw"], [t("Stored"), "bk-count"],
        [t("Size"), "bk-size"], ["", "bk-btns"]]), ...shown.flatMap(d => deviceRow(d, v.keep))] : [el("div", t("No device matches."), "empty")])));
    if (listed.length) cards.push(box("div", "card backups", box("div", "card-head", el("h2", t("No Backup · {n}", {n: listed.length}))), ...listed.map(noneRow)));
    else if (view.filter === "none") cards.push(box("div", "card", el("div", t("No device matches."), "empty")));
  }
  $("bk-cards").replaceChildren(...cards);
}

// ---------------------------------------------------------------- Dialog "Backups" of one device
/** The backups of one device (row menu and device details of the Devices tab): the
 *  same rows as in the tab, "Import backup" and "Back up now". */
export async function openDeviceBackups(deviceId) {
  const title = t("Backups");
  let v;
  try { v = await api("api/backups"); } catch (e) { message(title, e.message, "error", "backup"); return; }
  ui.backups = v;
  const d = v.devices.find(x => x.id === deviceId);
  if (!d) { message(title, t("The device is no longer listed."), "error", "backup"); return; }
  const back = () => openDeviceBackups(deviceId), list = d.backups;
  const body = list.length ? box("div", "bk-list in-dlg", ...list.map(b => backupRow(d, b, back)))
    : box("div", "warnbox", el("span", noneText(d.none || {})));
  const blocker = backupBlocker(d, ui.state);
  modal(title, [deviceHead(d, list.length ? t("{n} of {keep} stored", {n: counted(list), keep: v.keep}) : t("No backup")), body,
    el("p", t("The newest {n} backups are kept; a backup marked “Kept” is never removed automatically.", {n: v.keep}) +
      (ui.state && ui.state.backup_before_install ? " " + t("A backup is stored automatically before every installation.") : ""), "hint")],
  [{text: t("Import backup"), action: () => openImport(d, back)}, {text: t("Close")},
    {text: t("Back up now"), kind: "primary", action: () => backupOne(d, back)}], "backup");
  const now = gateButton(() => !blocker); if (blocker) setTip(now, blocker);
}

// ---------------------------------------------------------------- Download and import of one backup
/** Download of one backup: the server generates the password of the file (shown only
 *  here) and a single-use link; "Download" is enabled after "I have saved the password". */
async function openDownload(d, b, back) {
  const title = t("Download backup");
  let x;
  try { x = await api(`api/backups/${b.id}/download`, {}); } catch (e) { message(title, e.message, "error", "download"); return; }
  const prepared = Date.now();
  const [out, copy] = passwordCopy(x.password, t("Password of the file"), "bk-pw");
  const [saved, savedLabel] = checkbox([el("span", t("I have saved the password"))], false, "bk-sure");
  modal(title, [
    section(t("Backup"), valueList([[t("Device"), `${d.name} · ${modelGen(d)}`], [t("Backup"), backupText(b)], [t("Contents"), partsText(b.parts)],
      [t("File"), `${x.filename} · ${fmtSize(x.size)}`]])),
    section(t("Password"), box("div", "bk-pwrow", out, copy),
      el("div", t("The file is sealed with this password. It is shown only here and stored nowhere – without it the file cannot be imported again."), "warnbox")),
    box("div", "warnbox", el("span", t("Scripts and webhook addresses in a backup can contain secrets (tokens, keys). Keep the file and the password apart."))),
    savedLabel],
  [{text: t("Cancel"), cancel: true, action: back}, {text: t("Download"), kind: "primary", action: () => {
    // The server refuses an expired download; a download through a link cannot show that
    // answer, so the dialog checks the time itself
    if (Date.now() - prepared > x.expires_in * 1000) { message(title, t("Download expired – please start it again."), "error", "download"); return; }
    startDownload(`api/backups/download/${encodeURIComponent(x.token)}`, x.filename);
    modal(title, [messageBody(t("Your browser downloads {file} in a moment.", {file: x.filename}), "success")], [{text: t("Close"), cancel: true, action: back}], "download");
  }}], "download");
  ackGate(saved);
}
/** Import step 1: a backup file downloaded from this app and its password, for the
 *  device d. The file is uploaded and checked before anything is stored. */
function openImport(d, back) {
  const title = t("Import backup");
  const file = input("file", {accept: ".sfmb,application/octet-stream"}), pw = filePassword(t("Password of the file")), error = formError();
  file.setAttribute("aria-label", t("Backup file")); pw.setAttribute("aria-label", t("Password of the file"));
  const content = [deviceHead(d), section(t("File"), box("div", "bk-form", file, pw)),
    el("p", t("Imports a backup file that was downloaded from this app. It must come from this device (MAC address and model are checked) and needs the password shown at the download."), "hint"),
    error.line];
  const form = () => {
    modal(title, content, [{text: t("Cancel"), cancel: true, action: back}, {text: t("Check file"), kind: "primary", action: async () => {
      const f = file.files[0];
      modal(title, [el("p", t("Checking {name} …", {name: f.name}), "hint")], [], "upload");
      let token = null;
      try {
        token = (await uploadFile(f, `${devUrl(d)}/backups/import`)).token;
        importSummary(d, f.name, token, await api("api/backups/import/check", {token, password: pw.value}), back);
      } catch (e) {
        // Refused (wrong password, no backup file): back to this step with file, password and the error
        if (token) api("api/backups/import/cancel", {token}).catch(() => {});
        form(); error.show(e.message);
      }
    }}], "upload");
    // "Check file" needs both a file and its password
    gateButton(() => file.files.length && pw.value.trim(), file, pw);
  };
  form();
}
/** Import step 2: result of the check; nothing is stored before "Import backup". A
 *  file of another device or a backup that is stored already cannot be imported. */
function importSummary(d, name, token, r, back) {
  const title = t("Import backup"), b = r.backup, o = r.other;
  const cancel = async () => { await api("api/backups/import/cancel", {token}).catch(() => {}); await back(); };
  const checks = el("ul", null, "prechk");
  checks.append(checkLine("ok", t("Seal intact"), t("The password fits and the file was not changed since the download.")));
  if (o) checks.append(checkLine("err", t("Another device"), t("This file was made on another device (MAC {mac}, model {model}); this device has MAC {own}. A backup can only be imported for the device it was made on.",
    {mac: o.backup_mac || "–", model: o.backup_model || "–", own: o.device_mac || "–"})));
  else {
    checks.append(checkLine("ok", t("Same device"), t("MAC address and model match this device.")));
    checks.append(r.known ? checkLine("err", t("Already stored"), t("This backup is in the list already – there is nothing to import."))
      : checkLine("ok", t("Not stored yet"), t("This backup is no longer in the app – it is added to the list with its original date.")));
  }
  const possible = !o && !r.known;
  modal(title, [messageBody(possible ? t("{file} can be imported.", {file: name}) : t("{file} cannot be imported.", {file: name}), possible ? "success" : "error"),
    section(t("Backup"), valueList([[t("Device"), `${d.name} · ${modelGen(d)}`], [t("Backup"), backupText(b)], [t("Firmware"), b.fw],
      [t("Contents"), partsText(b.parts)], [t("Size"), fmtSize(b.size)]])),
    section(t("Check"), checks),
    possible ? el("p", t("An imported backup is marked “Imported” and “Kept”, so the limit of {n} does not remove it at once. Nothing is changed on the device – restore it from the list.",
      {n: (ui.backups && ui.backups.keep) || (ui.state && ui.state.backup_keep) || 10}), "hint") : null].filter(Boolean),
  possible ? [{text: t("Cancel"), cancel: true, action: cancel}, {text: t("Import backup"), kind: "primary", action: () => guarded(title, "upload", async () => {
    await api("api/backups/import/confirm", {token}); toast(t("Backup imported")); await back();
  })}] : [{text: t("Close"), cancel: true, action: cancel}], "upload");
}
/** Delete one backup after a confirmation. */
function deleteBackup(d, b, back) {
  confirmDialog({title: t("Delete backup?"), icon: "delete", button: t("Delete"), back,
    content: [el("p", t("The backup of {name} from {time} ({reason}, {size}) is deleted. It cannot be brought back.",
      {name: d.name, time: fmt(b.at), reason: reasonLabel(b.reason), size: fmtSize(b.size)}))],
    action: async () => { await api(`api/backups/${b.id}/delete`, {confirm: true}); await back(); }});
}

// ---------------------------------------------------------------- "Back up now"
/** Text of a stored backup in a result ("14 KB · settings, 2 scripts, …"). */
const storedLine = r => `${fmtSize(r.size)} · ${partsText(r.parts)}`;
/** "Back up now" for one device: progress line, then a success message (or the error). */
async function backupOne(d, back) {
  const title = t("Back up now");
  modal(title, [box("ul", "prechk", checkLine("run", d.name, t("Reading the settings …")))], [], "backup");
  try {
    const b = (await api(`${devUrl(d)}/backup`, {})).backup;
    const gap = b.parts && b.parts.partial;
    modal(title, [messageBody(t("Backup of {name} stored ({details}).", {name: d.name, details: storedLine(b)}) +
      (gap ? " " + t("The actions could not be read on this device.") : ""), gap ? "warning" : "success")], [{text: t("Close"), cancel: true, action: back}], "backup");
  } catch (e) { modal(title, [messageBody(`${d.name}: ${e.message}`, "error")], [{text: t("Close"), cancel: true, action: back}], "backup"); }
}
/** Dialog "Back up now" of the tab: all Shellys or chosen ones. A running or finished
 *  run (its result was not looked at yet) is shown instead. */
function openBackupNow() {
  const s = ui.state;
  if (s && s.backup_job) { openRun(); return; }
  const title = t("Back up now");
  const devs = ((s && s.devices) || []).filter(d => !d.removed).sort((a, b) => collator.compare(String(a.name), String(b.name)));
  const pick = deviceChoice("bk-dev", devs, [el("span", t("All Shellys ({n})", {n: devs.length}))], "all",
    d => [d.ignored ? el("span", t("Ignored"), "tag warn") : null, d.battery ? el("span", t("Battery"), "tag") : null].filter(Boolean));
  const areas = areaChoice("bk-dev", devs, t("Areas"), null);
  const modes = box("div", "modes", ...(areas ? [pick.nodes[0], ...areas.nodes, pick.nodes[1], pick.nodes[2]] : pick.nodes));
  modal(title, [
    section(t("Devices"), modes),
    el("p", t("Stores the settings, scripts, schedules and webhooks of each device (Gen 1: settings and actions) encrypted in this app. Passwords (Wi-Fi, device, MQTT) are not part of a backup – a Shelly never gives them out. Nothing is changed on the devices."), "hint")],
  [{text: t("Cancel")}, {text: "", kind: "primary", action: async () => {
    const ids = pick.all.checked ? "all" : (areas && areas.radio.checked ? areas.devices() : pick.ticked()).map(d => d.id);
    if (Array.isArray(ids) && ids.length === 1) { backupOne(devs.find(d => d.id === ids[0]), toTab); return; }
    await guarded(title, "backup", async () => { await api("api/backups/run", {devices: ids}); openRun(); });
  }}], "backup");
  const count = () => pick.all.checked ? devs.length : areas && areas.radio.checked ? areas.devices().length : pick.ticked().length;
  const go = gateButton(() => count() > 0, modes);
  const sync = () => { pick.refresh(); if (areas) areas.refresh(); go.textContent = tn(count(), "Back up 1 device", "Back up {n} devices"); };
  modes.addEventListener("change", sync);
  sync();
}
/** One device of a backup run as a line of the result. */
function runLine(r) {
  if (r.result === "stored") {
    return r.parts && r.parts.partial ? checkLine("warn", r.device, t("Stored · {details} – actions not readable on this device (Gen 1)", {details: storedLine(r)}))
      : checkLine("ok", r.device, t("Stored · {details}", {details: storedLine(r)}));
  }
  const last = r.last ? " " + t("The last backup ({time}) stays.", {time: fmt(r.last)}) : "";
  if (r.result === "asleep") return checkLine("warn", r.device, t("Not stored – battery device asleep.") + last);
  return checkLine("err", r.device, t("Not stored – {error}.", {error: (r.detail || "–").replace(/\.$/, "")}) + last);
}
/** Backup run of several devices: progress (bar and the devices done so far), then the
 *  result per device. Closing the result forgets the run on the server. */
function openRun() {
  const title = t("Back up now");
  const status = el("span"), bar = el("progress"), list = el("ul", null, "prechk");
  const head = box("div", "progress bk-progress", el("span", null, "spinner"), status, bar);
  head.setAttribute("role", "status");
  modal(title, [head, section(t("Result"), list)], [{text: t("Close")}], "backup");
  follow(head, s => {
    const job = s.backup_job;
    if (!job) { closeModal(); return true; }
    const results = [...job.results].sort((a, b) => collator.compare(String(a.device), String(b.device)));
    list.replaceChildren(...results.map(runLine));
    list.parentNode.hidden = !results.length;
    if (!job.finished) {
      status.textContent = t("Backing up … {done} of {total} devices", {done: job.done, total: job.total});
      bar.max = job.total; bar.value = job.done;
      return false;
    }
    const stored = results.filter(r => r.result === "stored").length;
    const text = job.total === 1 ? t("{n} of 1 device backed up.", {n: stored}) : t("{n} of {total} devices backed up.", {n: stored, total: job.total});
    modal(title, [messageBody(text, stored === job.total ? "success" : stored ? "warning" : "error"), section(t("Result"), list)],
      [{text: t("Close"), cancel: true, action: async () => { await api("api/backups/run/clear", {}).catch(() => {}); await toTab(); }}], "backup");
    return true;
  });
}

// ---------------------------------------------------------------- Restore
// Parts of a backup as the dialogs name them (Gen1 calls its settings just "Settings")
const partName = (part, gen) => ({settings: gen === 1 ? t("Settings") : t("Device settings"), scripts: t("Scripts"), schedules: t("Schedules"),
  webhooks: t("Webhooks"), actions: t("Actions"), network: t("Wi-Fi and network settings")})[part] || part;
const linesText = n => tn(n ?? 0, "{n} line", "{n} lines");
/** What a row of the differences shows: [setting, on the device, in the backup]. The
 *  names of settings are Shelly's own keys in every language. */
function diffCells(part, row) {
  if (part === "scripts") {
    const wanted = linesText(row.backup_lines) + (row.autostart ? " · " + t("runs at start") : "");
    if (row.state === "extra") return [row.key, t("only on the device") + ` (${linesText(row.device_lines)})`, t("is removed")];
    if (row.state === "missing") return [row.key, t("missing"), wanted];
    return [row.key, t("changed ({lines})", {lines: linesText(row.device_lines)}), wanted];
  }
  if (row.state === "extra") return [row.key, row.device ? `${row.device} · ${t("only on the device")}` : t("only on the device"), t("is removed")];
  if (row.state === "missing") return [row.key, t("missing"), row.backup];
  return [row.key, row.device, row.backup];
}
// Why a setting of the backup is not restored (named below the differences)
const SKIPPED = {unknown: t("not known by the firmware of the device"), fixed: t("cannot be changed by a restore"), unreadable: t("cannot be read on the device now")};
function diffTable(part, rows) {
  const table = el("table", null, "bk-diff"), head = el("tr");
  for (const h of [t("Setting"), t("On the device"), t("In the backup")]) head.append(el("th", h));
  table.append(head);
  for (const row of rows) {
    const [key, device, backup] = diffCells(part, row), r = el("tr");
    r.append(el("td", key, "bk-key"), el("td", device ?? "–"), el("td", backup ?? "–", "bk-new"));
    table.append(r);
  }
  return table;
}
/** One part of the restore dialog: tick, name, number of differences; "Show
 *  differences" opens the table. Without differences it is greyed out. Returns
 *  {node, input, rows}. */
function partBlock(p, gen, checked) {
  const rows = p.rows || [], skipped = p.skipped || [];
  const tick = input("checkbox", {checked: checked && rows.length > 0, disabled: !rows.length});
  const count = rows.length ? el("span", tn(rows.length, "{n} difference", "{n} differences"), "tag primary") : el("span", t("identical"), "sub");
  const head = box("label", "check bk-part-head", tick, el("span", partName(p.part, gen), "bk-part-title"), count);
  const skippedList = skipped.length ? box("ul", "bk-skipped", ...skipped.map(x => el("li", `${x.key} – ${SKIPPED[x.reason] || x.reason}`))) : null;
  const node = box("div", "bk-part" + (rows.length ? "" : " same"), head);
  if (rows.length || skippedList) {
    const details = el("details"), summary = el("summary");
    const label = () => { summary.textContent = rows.length ? (details.open ? t("Hide differences") : t("Show differences")) : tn(skipped.length, "{n} setting is skipped", "{n} settings are skipped"); };
    details.ontoggle = label; label();
    details.append(...[summary, rows.length ? diffTable(p.part, rows) : null,
      rows.length && skippedList ? el("p", t("Skipped – not restored:"), "hint") : null, skippedList].filter(Boolean));
    node.append(details);
  }
  return {node, input: tick, part: p.part, rows};
}
/** Dialog "Restore backup?" – step 1: the device is read and compared with the backup;
 *  the parts to restore are chosen. Nothing is changed. */
async function openRestore(d, b, back) {
  const title = t("Restore backup?");
  modal(title, [deviceHead(d), box("ul", "prechk", checkLine("run", t("Device reachable"), t("Reading the current settings for the comparison …")))], [{text: t("Cancel"), cancel: true, action: back}], "backup");
  let r;
  try { r = await api(`api/backups/${b.id}/preview`, {}); } catch (e) { modal(title, [messageBody(e.message, "error")], [{text: t("Close"), cancel: true, action: back}], "backup"); return; }
  const now = r.device.version, gen = r.device.gen;
  const info = section(t("Backup"), valueList([[t("Device"), `${d.name} · ${modelGen(d)}`], [t("Backup"), backupText(b)],
    [t("Firmware"), !b.fw ? null : !now || b.fw === now ? t("{version} (as now)", {version: b.fw}) : t("{version} at the backup · {now} now", {version: b.fw, now})]]));
  const checks = el("ul", null, "prechk");
  if (r.other) {
    const o = r.other;
    checks.append(checkLine("err", t("Another device"), t("This backup was made on another device (MAC {mac}, model {model}); the device now has MAC {own}. A backup can only be restored to the device it was made on.",
      {mac: o.backup_mac || "–", model: o.backup_model || "–", own: o.device_mac || "–"})));
    modal(title, [info, section(t("Check"), checks)], [{text: t("Close"), cancel: true, action: back}], "backup");
    return;
  }
  if (!r.reachable) {
    checks.append(checkLine("err", t("Device not reachable"), r.error || ""));
    modal(title, [info, section(t("Check"), checks), el("p", t("The current settings are needed for the comparison – nothing can be restored while the device does not answer."), "hint")],
      [{text: t("Close"), cancel: true, action: back}], "backup");
    return;
  }
  checks.append(checkLine("ok", t("Same device"), t("MAC address and model match the backup.")));
  if (r.fw_differs) checks.append(checkLine("warn", t("Firmware differs"), t("The backup was made with {version}, the device runs {now}. Settings this firmware does not know are skipped and named in the result.", {version: b.fw, now})));
  checks.append(checkLine("ok", t("Device reachable"), t("The current settings were read for the comparison.")));
  const blocks = r.parts.filter(p => p.part !== "network").map(p => partBlock(p, gen, true));
  const network = partBlock(r.parts.find(p => p.part === "network") || {part: "network", rows: [], skipped: []}, gen, false);
  // Opened from the start: the differences of the first part that has some
  const first = blocks.find(x => x.rows.length); if (first) first.node.querySelector("details").open = true;
  const protection = r.protection ? box("div", "bk-part same", box("label", "check bk-part-head", input("checkbox", {disabled: true}),
    el("span", t("Password protection"), "bk-part-title"), el("span", r.protection === "same" ? t("identical") : t("differs – not restored"), "sub"))) : null;
  // The backup names another Wi-Fi than the device uses now: its password cannot be restored
  const wifiNames = network.rows.filter(row => row.wifi_name).map(row => el("p", t("The backup names another Wi-Fi (SSID): “{backup}” instead of “{device}”. The Wi-Fi password is not part of the backup and stays as it is on the device – it can only connect to “{backup}” with the password it has stored now. If that does not fit, the device becomes unreachable.",
    {backup: row.backup, device: row.device}), "bk-wifi-name"));
  const danger = box("div", "warnbox danger", el("span", t("Wrong network settings can make the device unreachable – then only a reset at the device helps. Restore them only if the backup's network still exists.")), ...wifiNames);
  const content = [info, section(t("Check"), checks),
    section(t("What to restore"), box("div", "bk-parts-list", ...blocks.map(x => x.node))),
    section(t("Network and access"), box("div", "bk-parts-list", network.node, protection), danger,
      el("p", t("Left as they are unless ticked. Passwords (Wi-Fi, device, MQTT) are never part of a backup – a Shelly does not give them out; they stay unchanged on the device."), "hint"))];
  const all = [...blocks, network];
  const next = () => restoreConfirm(d, b, all.filter(x => x.input.checked), all, gen, back, () => { modal(title, content, buttons, "backup"); sync(); });
  const buttons = [{text: t("Cancel"), cancel: true, action: back}, {text: t("Continue"), kind: "primary", action: next}];
  const sync = () => {
    danger.hidden = !network.input.checked;
    gateButton(() => all.some(x => x.input.checked));
  };
  modal(title, content, buttons, "backup");
  for (const x of all) x.input.addEventListener("change", sync);
  sync();
}
/** What restoring a chosen part does, in a few words ("4 changes", "2 written, 1 removed"). */
function changeText(block) {
  const rows = block.rows, n = state => rows.filter(row => row.state === state).length;
  if (["settings", "network"].includes(block.part)) return tn(rows.length, "{n} change", "{n} changes");
  const written = block.part === "scripts" ? n("changed") + n("missing") : 0;
  return [written ? t("{n} written", {n: written}) : null,
    block.part !== "scripts" && n("changed") ? t("{n} changed", {n: n("changed")}) : null,
    block.part !== "scripts" && n("missing") ? t("{n} added", {n: n("missing")}) : null,
    n("extra") ? t("{n} removed", {n: n("extra")}) : null].filter(Boolean).join(", ");
}
/** Step 2: what is restored, the hint about the backup stored first, and the
 *  confirmation; the network settings need their own tick. */
function restoreConfirm(d, b, chosen, all, gen, back, previous) {
  const title = t("Restore backup?");
  const [sure, sureLabel] = checkbox([el("span", t("I understand that the device can become unreachable if the network settings no longer fit."))], false, "bk-sure");
  const picked = new Set(chosen.map(x => x.part)), net = picked.has("network");
  const rows = [[t("Device"), d.name], [t("Backup"), backupText(b)],
    ...all.filter(x => x.rows.length).map(x => [partName(x.part, gen), picked.has(x.part) ? changeText(x) : t("not restored")])];
  modal(title, [section(t("Restore"), valueList(rows)),
    box("div", "warnbox", el("span", t("The current settings are stored as a backup first (“Before restore”), so this step can be taken back. The device may restart; running scripts are stopped and started again as in the backup."))),
    net ? sureLabel : null].filter(Boolean),
  [{text: t("Back"), action: previous}, {text: t("Restore"), kind: "danger", action: async () => {
    try {
      await api(`api/backups/${b.id}/restore`, {parts: [...picked], confirm: true, network_acknowledged: net && sure.checked});
      openRestoreProgress(back);
    } catch (e) { modal(title, [messageBody(e.message, "error")], [{text: t("Close"), cancel: true, action: back}], "backup"); }
  }}], "backup");
  gateButton(() => !net || sure.checked, sure);
}
// Steps of a running restore as the progress view names them
const stepName = (step, gen) => ({backup: t("Backup “Before restore”"), restart: t("Restart"), verify: t("Reading the settings again")})[step] || partName(step, gen);
/** Progress of the running restore (one line per step: done, running, waiting), then
 *  its result. Also shows the last result again while it was not closed. */
function openRestoreProgress(back) {
  const title = t("Restore backup?");
  const list = el("ul", null, "prechk"), name = el("div", "", "name");
  const head = box("div", "bk-dhead", box("div", null, name, el("div", t("Restoring – do not switch the device off."), "sub")));
  modal(title, [head, section(t("Progress"), list)], [{text: t("Close")}], "backup");
  follow(head, s => {
    const job = s.restore;
    if (!job) { closeModal(); return true; }
    if (job.finished) { restoreResult(job, s, back); return true; }
    const gen = (s.devices.find(x => x.id === job.device) || {}).gen;
    name.textContent = `${job.name} · ${t("Backup of {time}", {time: fmt(job.backup_at)})}`;
    const at = job.steps.indexOf(job.step);
    list.replaceChildren(...job.steps.map((step, i) => checkLine(i < at ? "ok" : i === at ? "run" : "wait", stepName(step, gen),
      i < at ? t("Done") : i === at ? t("Running …") : t("Waiting"))));
    return false;
  });
}
/** Result of a restore: one line per part (applied and read back / not applied with the
 *  reason), the restart, and the backup stored before. Closing it forgets the job. */
function restoreResult(job, s, back) {
  const title = t("Restore backup?"), when = fmt(job.backup_at);
  const done = async () => { await api("api/backups/restore/clear", {}).catch(() => {}); await back(); };
  const safety = job.safety ? el("p", t("The state before the restore is stored as backup “Before restore” ({time}).", {time: fmt(job.safety)}), "hint") : null;
  if (job.error || !job.result) {
    modal(title, [messageBody(`${job.name}: ${job.error || t("The restore ended without a result.")}`, "error"), safety].filter(Boolean), [{text: t("Close"), cancel: true, action: done}], "backup");
    return;
  }
  const r = job.result, gen = (s.devices.find(x => x.id === job.device) || {}).gen;
  const list = el("ul", null, "prechk");
  let attention = 0;
  for (const p of r.parts) {
    const open = p.open || [], applied = Math.max(0, p.total - open.length);
    const skipped = (p.skipped || []).length ? " " + tn(p.skipped.length, "{n} setting skipped: {keys}.", "{n} settings skipped: {keys}.", {keys: p.skipped.map(x => x.key).join(", ")}) : "";
    const running = p.part === "scripts" && typeof r.running === "number" ? " " + tn(r.running, "{n} script running.", "{n} scripts running.") : "";
    if (!r.confirmed) { attention++; list.append(checkLine("warn", partName(p.part, gen), t("Written, but the device could not be read again – not confirmed.") + skipped)); continue; }
    if (!open.length) { list.append(checkLine("ok", partName(p.part, gen), t("{n} of {total} applied and read back.", {n: p.total, total: p.total}) + running + skipped)); continue; }
    attention++;
    const failed = open.map(x => x.error ? `${x.key} (${x.error})` : x.key).join(", ");
    list.append(checkLine("warn", partName(p.part, gen), t("{n} of {total} applied – not applied: {keys}.", {n: applied, total: p.total, keys: failed}) + running + skipped));
  }
  if (r.restart === true) list.append(checkLine("ok", t("Restart"), t("The device restarted and answers again.")));
  if (r.restart === false) { attention++; list.append(checkLine("err", t("Restart"), t("The device did not answer again after the restart."))); }
  if (r.lost) { attention++; list.append(checkLine("err", t("Connection lost"), r.lost)); }
  const text = !r.confirmed ? t("{name}: backup of {time} written, but the result could not be read back.", {name: job.name, time: when})
    : attention ? tn(attention, "{name}: backup of {time} restored – {n} point needs attention.", "{name}: backup of {time} restored – {n} points need attention.", {name: job.name, time: when})
      : t("{name}: backup of {time} restored.", {name: job.name, time: when});
  modal(title, [messageBody(text, !r.confirmed ? "error" : attention ? "warning" : "success"), section(t("Result"), list), safety].filter(Boolean),
    [{text: t("Close"), cancel: true, action: done}], "backup");
}

searchTool($("bk-search"));
$("bk-search").addEventListener("input", () => render(true));
$("bk-filter").replaceWith(bkFilter.node);
$("bk-now").prepend(icon("backup"));
$("bk-now").onclick = openBackupNow;
