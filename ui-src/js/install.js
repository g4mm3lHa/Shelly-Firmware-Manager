// Installation and downgrade dialogs (approval bound to the SHA-256 of the provided file).
import {
  $, api, box, checkbox, checkLine, closeModal, confirmDialog, devUrl, el, fmt, input, mb, message, modal, modelGen, pad, radio, section, selected,
  shaRow, t, tn, toast, ui, valueList,
} from "./core.js";
import {load} from "./main.js";

// ---------------------------------------------------------------- Installation
/** Changelog versions as nodes: category tags, security highlighted, model entries marked. */
export function changelogNodes(data) {
  if (!data || !data.available) return [el("p", t("Changelog not available."), "hint")];
  if (!data.versions.length) return [el("p", t("No changelog entries between the installed and the target version."), "hint")];
  const out = [];
  for (const v of data.versions) {
    out.push(el("div", v.date ? `${v.version} · ${v.date}` : v.version, "cl-version"));
    if (!v.entries.length) { out.push(el("p", t("No entries listed for this version."), "hint")); continue; }
    const ul = el("ul", null, "cl-list");
    for (const e of v.entries) {
      const li = el("li", null, e.this_model ? "this" : "");
      if (e.security) li.append(el("span", t("Security"), "tag sec"));
      if (e.category && e.category.toLowerCase() !== "security") li.append(el("span", e.category, "tag"));
      if (e.this_model) li.append(el("span", t("This model"), "tag model"));
      else if (e.model_specific) li.append(el("span", t("Model-specific"), "tag model"));
      li.append(e.text); ul.append(li);
    }
    out.push(ul);
  }
  return out;
}
/** Container that loads the changelog of one device. */
function changelogBox(d) {
  const c = box("div", null, el("p", t("Loading changelog …"), "hint"));
  api(`${devUrl(d)}/changelog`).then(r => c.replaceChildren(...changelogNodes(r))).catch(() => c.replaceChildren(...changelogNodes(null)));
  return c;
}
// Where the provided update comes from (device field offer_source). Updates
// from the firmware store are provided without asking the Shelly update server.
export const OFFER_SOURCES = {manufacturer: t("Shelly update server"), store: t("Firmware store (downloaded from Shelly earlier)"),
  upload: t("Firmware store (uploaded file)"), import: t("Firmware store (imported file)")};
const OFFER_SHORT = {manufacturer: t("Shelly server"), store: t("Store"), upload: t("Upload"), import: t("Import")};
/** Source line of the install dialog: the store label, or the manufacturer's URL. */
const offerSource = d => d.offer_source && d.offer_source !== "manufacturer" ? OFFER_SOURCES[d.offer_source] : d.source_url;
const FALLBACK_WARNING = t("The Shelly update server could not be asked. This stored version is offered for a manual installation at your own risk: it cannot be verified whether the device needs an intermediate version first. If it does, the device rejects the firmware.");
/** Hint of an intermediate version: the device is checked again after its installation. */
const stepHint = d => `${d.name}: ` + (d.step_target ?
  t("{version} is an intermediate version on the way to {target}. After the installation the device is checked again and the next version is provided (it is installed only after a new approval).", {version: d.offer_version, target: d.step_target}) :
  t("{version} is an intermediate version. After the installation the device is checked again and the next version is provided (it is installed only after a new approval).", {version: d.offer_version}));
const GEN1_WARNING = t("Gen1 firmware is offered by Shelly only via plain HTTP and without a checksum – its integrity and origin cannot be verified. Install only if you accept this risk.");
/** "Device" block of the installation and downgrade dialogs. */
const deviceSection = d => section(t("Device"), valueList([[t("Name"), d.name], [t("Shelly"), d.shelly_name], ["IP", d.ip, "num"],
  [t("Model"), modelGen(d)]]));
/** Installed and target version on one line. */
const versionStep = (from, to) => `${from || "–"}  →  ${to || "–"}`;
// ---- Before installing: way back (installed version stored) and Wi-Fi signal
// Signal classes in dBm: better than -70 good, -70 to -79 fair, -80 and below weak
const WEAK_RSSI = -80, FAIR_RSSI = -70;
const dbm = rssi => `${String(rssi).replace("-", "−")} dBm`;
const weak = rssi => typeof rssi === "number" && rssi <= WEAK_RSSI;
const signalText = rssi => `${dbm(rssi)} · ${weak(rssi) ? t("weak") : rssi <= FAIR_RSSI ? t("fair") : t("good")}.`;
const WEAK_TEXT = rssi => t("Weak Wi-Fi signal ({dbm}). The Shelly downloads the firmware over Wi-Fi – the download may fail.", {dbm: dbm(rssi)});
const BACK_OK = d => t("{version} is stored – a downgrade stays possible.", {version: d.way_back.version});
const BACK_NO = d => t("{version} is not stored and Shelly no longer offers it – there is no way back after this update.", {version: d.way_back.version});
const noWayBack = d => !!d.way_back && !d.way_back.stored;
/** Build date of the offered version with its age in days (local dates). */
export function daysAgo(day) {
  const [y, m, dd] = day.split("-").map(Number), now = new Date();
  const days = Math.round((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(y, m - 1, dd)) / 86400000);
  return days <= 0 ? t("today") : tn(days, "1 day ago", "{n} days ago");
}
const buildAge = day => `${day} · ${daysAgo(day)}`;
/** Wi-Fi signal of the devices, read now (null: not on Wi-Fi or not readable). */
// The device did not answer or reported no usable value (shown as "unknown")
const UNKNOWN_SIGNAL = "unknown";
async function readSignals(devs) {
  return Promise.all(devs.map(d => api(`${devUrl(d)}/signal`).then(r => r.rssi ?? null).catch(() => UNKNOWN_SIGNAL)));
}
/** Line "Backup" of the check list (option "Back up before every installation"): the
 *  settings are backed up right before the installation; nothing is installed if that
 *  fails. [] while the option is off. */
const backupCheck = one => ui.state && ui.state.backup_before_install ? [checkLine("ok", t("Backup"),
  one ? t("The settings of the device are backed up right before the installation.") : t("The settings of every device are backed up right before its installation."))] : [];
/** Section "Before installing": way back, backup and Wi-Fi signal; for several devices only
 *  the findings per device (or one line "every device" without findings). The signal is
 *  read when the dialog opens ("Reading …"); devices on the cable have no signal line, a
 *  value that could not be read is shown as "unknown" (yellow, a finding). */
function beforeInstalling(devs) {
  const list = el("ul", null, "prechk"), one = devs.length === 1;
  const back = () => {
    const known = devs.filter(d => d.way_back), missing = known.filter(noWayBack);
    if (!known.length) return [];
    if (one) return [checkLine(known[0].way_back.stored ? "ok" : "warn", t("Way back"), known[0].way_back.stored ? BACK_OK(known[0]) : BACK_NO(known[0]))];
    return missing.length ? missing.map(d => checkLine("warn", `${t("Way back")} – ${d.name}`, BACK_NO(d)))
      : [checkLine("ok", t("Way back"), t("The installed version of every device is stored."))];
  };
  const signal = values => {
    if (!values) return [checkLine("wait", t("Wi-Fi signal"), t("Reading …"))];
    const read = devs.map((d, i) => [d, values[i]]).filter(([, v]) => v !== null);
    if (!read.length) return [];
    const finding = v => v === UNKNOWN_SIGNAL ? t("unknown") : WEAK_TEXT(v);
    const bad = read.filter(([, v]) => v === UNKNOWN_SIGNAL || weak(v));
    if (one) return [checkLine(bad.length ? "warn" : "ok", t("Wi-Fi signal"), bad.length ? finding(read[0][1]) : signalText(read[0][1]))];
    return bad.length ? bad.map(([d, v]) => checkLine("warn", `${t("Wi-Fi signal")} – ${d.name}`, finding(v)))
      : [checkLine("ok", t("Wi-Fi signal"), t("Good or fair on every device."))];
  };
  const block = section(t("Before installing"), list);
  const fill = values => { list.replaceChildren(...back(), ...backupCheck(one), ...signal(values)); block.hidden = !list.children.length; };
  fill(null);
  readSignals(devs).then(fill);
  return block;
}
// Imported files (V1): offered only while Shelly cannot be asked (store fallback)
export const IMPORT_WARNING = t("This file comes from an imported firmware store. The Shelly update server has not confirmed it – it is only as trustworthy as the export you imported.");
const IMPORT_FALLBACK = d => (d.gen === 1 ? t("Shelly's firmware list could not be loaded.") : t("The Shelly update server could not be asked.")) +
  " " + t("This stored version comes from an imported firmware store and Shelly has not confirmed it – it is only as trustworthy as the export you imported. It is offered for a manual installation at your own risk.") +
  (d.gen === 1 ? "" : " " + t("It also cannot be verified whether the device needs an intermediate version first; if it does, the device rejects the firmware."));
// Beta version (option "Offer beta firmware"): yellow box and an own tick in the dialog
const BETA_WARNING = t("Beta firmware: a pre-release version of Shelly for testing. It can contain errors. The way back to a stable version is a downgrade, which needs that version in the firmware store.");
// "Install at": once, at most 7 days ahead, in the time zone of Home Assistant
const PLAN_DAYS = 7;
const isoDay = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
/** "YYYY-MM-DD" of a time (ms) in the time zone of Home Assistant – the server checks the
 *  chosen date there; the browser's own date if the zone is unknown to the browser. */
function haDay(ms) {
  const zone = ui.state && ui.state.schedule ? ui.state.schedule.timezone : undefined;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit"}).formatToParts(new Date(ms));
    const part = type => (parts.find(x => x.type === type) || {}).value;
    return `${part("year")}-${part("month")}-${part("day")}`;
  } catch (e) { return isoDay(new Date(ms)); }
}
/** Confirmation dialog: one device with all details, several as an overview; approval
 *  bound to the SHA-256. Section "Start" (above the changelog): now, or "Install at" a
 *  date and time (the approval is given now and stored until then). A beta version
 *  needs its own tick. */
export function openInstall(devs) {
  // changelogs: shown below the section "Start"
  const content = [], changelogs = [], [restart, restartLabel] = checkbox(t("Restart the device first"));
  const retry = devs.length === 1 && !!devs[0].install_failed, betas = devs.filter(d => d.beta);
  if (devs.length === 1) {
    const d = devs[0];
    const target = versionStep(d.version, d.offer_version) + (d.beta ? `  (${t("beta")})` : "");
    content.push(deviceSection(d), section(t("Firmware"), valueList([[t("Firmware ID"), d.type], [t("Version"), target, "ver"],
      [t("Build date"), d.offer_date ? buildAge(d.offer_date) : null], [t("Size"), mb(d.size || 0)], [t("Source"), offerSource(d), "src"], shaRow(d.sha256)])),
    beforeInstalling(devs));
    if (retry) {   // after a failed installation: restart the device first (pre-selected)
      restart.checked = true;
      content.push(section(t("Last attempt"), el("p", t("Failed: {error}", {error: d.message || "–"}), "pc-fail")),
        restartLabel,
        el("p", t("The app restarts the Shelly, waits until it answers again and then starts the installation."), "hint pc-sub"));
    }
    if (d.gen === 1) content.push(el("div", GEN1_WARNING, "warnbox"));
    if (d.offer_source === "upload") content.push(el("div", UPLOAD_HINT, "warnbox"));
    if (d.beta) content.push(el("div", BETA_WARNING, "warnbox"));
    if (d.step_target != null) content.push(el("p", stepHint(d), "hint"));
    changelogs.push(section(t("Changelog"), changelogBox(d)));
  } else {
    // Phones show the rows as two-line entries (style.css, class inst-list)
    const table = el("table", null, "inst-list"), h = el("tr");
    for (const c of [t("Device"), "IP", t("Installed"), t("Target"), t("Source")]) h.append(el("th", c));
    table.append(box("thead", null, h));
    for (const d of devs) {
      const r = el("tr");
      for (const [v, cls] of [[d.name], [d.ip, "num"], [d.version], [d.offer_version], [OFFER_SHORT[d.offer_source]]]) r.append(el("td", v ?? "–", cls));
      if (d.beta) r.children[3].append(" ", el("span", t("Beta"), "tag beta"));
      table.append(r);
    }
    content.push(table, beforeInstalling(devs));
    if (betas.length) content.push(el("div", (betas.length === devs.length ? "" : `${betas.map(d => d.name).join(", ")}: `) + BETA_WARNING, "warnbox"));
    if (devs.some(d => d.gen === 1)) content.push(el("div", GEN1_WARNING, "warnbox"));
    if (devs.some(d => d.offer_source === "upload")) content.push(el("div", UPLOAD_HINT, "warnbox"));
    for (const d of devs.filter(x => x.step_target != null)) content.push(el("p", stepHint(d), "hint"));
    for (const d of devs) {
      const det = el("details"); det.append(el("summary", `${t("Changelog")} – ${d.name}`));
      det.ontoggle = () => { if (det.open && det.children.length === 1) det.append(changelogBox(d)); };
      changelogs.push(det);
    }
  }
  // A stored version offered without the server's answer needs the risk acknowledgment;
  // an imported file (only offered this way) gets its own box that names its origin
  const unverified = devs.filter(d => d.store_fallback);
  const [ack, ackLabel] = checkbox(unverified.some(d => d.offer_source === "import") ? t("I accept the risk of installing a file that Shelly has not confirmed")
    : t("I accept the risk of installing without the update server's verification"));
  if (unverified.length) {
    const plain = unverified.filter(d => d.offer_source !== "import"), imported = unverified.filter(d => d.offer_source === "import");
    if (plain.length) content.push(el("div", plain.length === devs.length ? FALLBACK_WARNING : `${plain.map(d => d.name).join(", ")}: ${FALLBACK_WARNING}`, "warnbox"));
    for (const d of imported) content.push(el("div", (devs.length === 1 ? "" : `${d.name}: `) + IMPORT_FALLBACK(d), "warnbox"));
    content.push(ackLabel);
  }
  // Start: now, or once at a chosen date and time (default: tomorrow 03:00)
  const [now, nowLabel] = radio("inst-start", t("Now"), true), [at, atLabel] = radio("inst-start", t("Install at")), today = Date.now();
  const day = input("date", {min: haDay(today), max: haDay(today + PLAN_DAYS * 86400000), value: haDay(today + 86400000)});
  const clock = input("time", {value: "03:00"});
  day.setAttribute("aria-label", t("Date")); clock.setAttribute("aria-label", t("Time"));
  const atHint = el("p", tn(devs.length, "Your approval is stored for this file (SHA-256).", "Your approval is stored for these files (SHA-256).") + " " +
    t("At that time (time zone of Home Assistant) the app installs without asking again – only if the device still runs the version shown here and the file is still provided. Otherwise nothing is installed and the log names the reason. You can cancel it in the Schedules tab or the device menu."), "hint pc-sub");
  content.push(section(t("Start"), box("div", "modes inst-start", nowLabel, atLabel, box("div", "sub-check inst-when", day, clock)), atHint), ...changelogs);
  const [betaAck, betaLabel] = checkbox(t("I understand that beta firmware is a pre-release and may contain bugs."), false, "beta-ack");
  if (betas.length) content.push(betaLabel);
  const restartHint = el("p", "", "hint"); content.push(restartHint);
  // An error keeps the title of this dialog
  const allBeta = betas.length === devs.length;
  const title = devs.length === 1 ? (allBeta ? t("Install beta update?") : t("Install update?")) :
    allBeta ? t("Install {n} beta updates?", {n: devs.length}) : t("Install {n} updates?", {n: devs.length});
  modal(title, content, [{text: t("Cancel")},
    {text: t("Approve installation"), kind: "danger", action: async () => {
      const errors = [], when = at.checked ? `${day.value}T${clock.value}` : null;
      for (const d of devs) {   // each installation starts in the background – calls return at once
        const body = {sha256: d.sha256, confirm: true, ...(d.store_fallback ? {acknowledged: ack.checked} : {}),
          ...(retry && restart.checked ? {restart: true} : {}), ...(d.beta ? {beta_acknowledged: betaAck.checked} : {}),
          ...(when ? {at: when} : {})};
        try { await api(`${devUrl(d)}/install`, body); selected.delete(d.id); }
        catch (e) { errors.push(`${d.name}: ${e.message}`); }
      }
      if (errors.length) { message(title, errors.join("\n"), "error", "chip"); return; }
      closeModal();
      if (when) toast(t("Installation scheduled for {time}", {time: when.replace("T", " ")}));
    }}], "chip");
  const go = $("dlg-btns").lastChild;
  const sync = () => {
    go.disabled = (unverified.length > 0 && !ack.checked) || (betas.length > 0 && !betaAck.checked) || (at.checked && !(day.value && clock.value));
    atHint.hidden = !at.checked; day.disabled = clock.disabled = !at.checked;
    go.textContent = at.checked ? t("Approve & schedule") : retry && restart.checked ? t("Restart & install") : t("Approve installation");
    restartHint.textContent = at.checked ? tn(devs.length, "The device restarts during the installation – at the chosen time.", "The devices restart during the installation – at the chosen time.")
      : tn(devs.length, "The device restarts during the installation.", "The devices restart during the installation.");
  };
  for (const input of [ack, betaAck, now, at, restart, day, clock]) { input.addEventListener("change", sync); input.addEventListener("input", sync); }
  sync();
}
/** Cancel a scheduled installation ("Install at") after a confirmation; the update stays provided. */
export function cancelPlan(d) {
  confirmDialog({title: t("Cancel scheduled installation?"), icon: "clock", cancel: t("Keep"), button: t("Cancel installation"),
    content: [el("p", t("{name}: the installation of {version} at {time} is cancelled. The update stays provided and can be installed or scheduled again.",
      {name: d.name, version: d.planned ? d.planned.target : d.offer_version, time: d.planned ? d.planned.at_text : "–"}))],
    action: async () => { await api(`${devUrl(d)}/planned/cancel`, {confirm: true}); closeModal(); }});
}
// ---------------------------------------------------------------- Downgrade
// Flow: list of stored older versions -> installation dialog for the selected
// version -> second confirmation with acknowledgment (as for the device password).
export const DOWNGRADE_WARNING = t("A downgrade installs an older firmware. The device may refuse it, settings added by newer versions may be lost, and some devices may need a factory reset afterwards. Install only if you need this version.");
export const UPLOAD_HINT = t("An uploaded file does not come from the Shelly update server; its origin is only as trustworthy as its source.");
/** Label of a stored version for lists and dialogs. */
export const fwSource = v => v.source === "import" ? t("Imported (not confirmed by the Shelly update server)")
  : v.source === "upload" ? (v.sha_checked ? t("Uploaded (SHA-256 checked)") : t("Uploaded (SHA-256 not checked)")) : t("Downloaded from Shelly");
/** Tag of a stored version that does not come from Shelly's download (lists and dialogs). */
const sourceTag = v => v.source === "upload" ? el("span", t("Uploaded"), "tag warn") : v.source === "import" ? el("span", t("Imported"), "tag warn") : null;
/** Step 1: choose one of the stored older versions. */
export async function openDowngrade(d) {
  const title = `${t("Downgrade")} – ${d.name}`;
  modal(title, [el("p", t("Loading stored versions …"), "hint")], [{text: t("Cancel")}], "history");
  let r;
  try { r = await api(`${devUrl(d)}/downgrades`); } catch (e) { message(title, e.message, "error", "history"); return; }
  if (r.blocked) { message(title, r.blocked, "error", "history"); return; }
  if (!r.versions.length) {
    message(title, t("No stored version of {type} is older than the installed version {installed}. Older versions stay stored once newer ones were downloaded, or can be uploaded in the Firmware tab.", {type: r.type || d.type, installed: r.installed || "–"}), "info", "history");
    return;
  }
  const list = el("div", null, "modes");
  r.versions.forEach((v, i) => {
    list.append(radio("dg-version", [el("span", v.version, "name"), el("span", ` · ${mb(v.size || 0)} · ${fmt(v.downloaded)} `, "sub"), sourceTag(v)],
      i === 0, String(i))[1]);
  });
  modal(title, [valueList([[t("Firmware ID"), r.type], [t("Installed"), r.installed]]), el("p", t("Stored older versions (newest first):")), list,
    el("p", t("Newer versions are installed with “Install update”."), "hint")],
  [{text: t("Cancel")}, {text: t("Continue"), kind: "primary", action: () => {
    downgradeInstall(d, r.versions[Number(list.querySelector("input:checked").value)]);
  }}], "history");
}
/** Step 2: the known installation dialog, with the selected older version as target. */
function downgradeInstall(d, v) {
  const content = [deviceSection(d), section(t("Firmware"), valueList([[t("Firmware ID"), d.type],
    [t("Version (older)"), versionStep(d.version, v.version), "ver"], [t("Size"), mb(v.size || 0)], [t("Source"), fwSource(v), "src"], shaRow(v.sha256)])),
  el("div", DOWNGRADE_WARNING, "warnbox")];
  const checks = backupCheck(true);
  if (checks.length) content.splice(2, 0, section(t("Before installing"), box("ul", "prechk", ...checks)));
  if (v.source === "upload") content.push(el("div", UPLOAD_HINT, "warnbox"));
  if (v.source === "import") content.push(el("div", IMPORT_WARNING, "warnbox"));
  content.push(el("p", t("The device restarts during the installation."), "hint"));
  modal(t("Downgrade?"), content, [{text: t("Cancel")}, {text: t("Continue"), kind: "primary", action: () => downgradeConfirm(d, v)}], "history");
}
/** Hint while the option "Allow downgrades" is off (row menu, Firmware tab). */
export const DOWNGRADE_OFF = t("Downgrades are off – enable “Allow downgrades” in Settings");
/** Step 3: explicit confirmation; the red button needs the acknowledgment
 *  (also the last step of a downgrade started in the Firmware tab). */
export function downgradeConfirm(d, v) {
  const imported = v.source === "import";   // the acknowledgment also names the unconfirmed file
  const [ack, ackLabel] = checkbox(imported ? t("I understand that the downgrade may fail or reset settings, and that the Shelly update server has not confirmed this file")
    : t("I understand that the downgrade may fail or reset settings"));
  confirmDialog({title: t("Downgrade?"), icon: "history", button: t("Downgrade"), ack,
    content: [el("p", t("Install {version} on {name} (installed: {installed})?", {version: v.version, name: d.name, installed: d.version})), el("div", DOWNGRADE_WARNING, "warnbox"),
      imported ? el("div", IMPORT_WARNING, "warnbox") : null, ackLabel].filter(Boolean),
    action: async () => { await api(`${devUrl(d)}/install`, {sha256: v.sha256, confirm: true, downgrade: true, acknowledged: ack.checked}); closeModal(); }});
}
$("scan").onclick = () => api("api/scan", {}).then(() => load(true)).catch(e => message(t("Check"), e.message, "error"));
