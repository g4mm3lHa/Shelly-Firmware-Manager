// Logs tab: activity log with filters, grouping per device and deleting entries.
import {
  $, api, box, closeModal, el, filterControl, fmt, iconButton, keptDetails, mb, message, modal, openDetails, pad, searchTool, ui,
  valueList,
} from "./core.js";
import {OFFER_SOURCES} from "./install.js";
import {OPTION_FIELDS} from "./app-settings.js";
import {load, render} from "./main.js";

// ---------------------------------------------------------------- Logs
// Severity of an entry; entries written before 3.0 only have "ok"
const severity = e => ["success", "warning", "error"].includes(e.severity) ? e.severity : e.ok === false ? "error" : "success";
const SEVERITY_LABELS = {success: "Success", warning: "Warning", error: "Error"};
const LOG_FILTERS = {all: ["All", () => true], checks: ["Checks", e => e.kind === "check"],
  installs: ["Installations", e => e.kind === "install"], settings: ["Settings", e => ["setting", "password", "options"].includes(e.kind)],
  warnings: ["Warnings", e => severity(e) === "warning"], errors: ["Errors", e => severity(e) === "error"]};
const logFilter = filterControl(Object.entries(LOG_FILTERS).map(([key, [label]]) => [key, label]),
  key => { ui.logFilter = key; render(true); });
$("log-filter").replaceWith(logFilter.node);
const KIND_LABELS = {check: "Check", download: "Download", upload: "Upload", install: "Installation", delete: "Deleted", setting: "Setting",
  password: "Password", options: "App settings", log: "Log", export: "Store export", import: "Store import", setup: "First start",
  store: "Firmware store", device: "Device"};
/** Device entry: removed from the list, or a held-back update offered now. */
const deviceAction = e => e.action === "offered_now" ? `${e.device}: held update ${e.version || ""} offered now`
  : `${e.device}: removed from the list`;
/** Firmware files read back from the disk after the store lost its list (kind "store"). */
const adoptedCount = e => (e.adopted || []).length;
const adoptedLine = e => `${adoptedCount(e)} file${adoptedCount(e) === 1 ? "" : "s"} read from the disk – they could have been changed` +
  (e.unknown ? ` · ${e.unknown} unknown file${e.unknown === 1 ? "" : "s"} (Firmware tab)` : "");
// Successful actions, failed actions are logged with the mode; older entries: set/removed/test_failed
const PASSWORD_ACTIONS = {stored: "password stored", enabled: "password protection enabled", changed: "password changed",
  removed: "password protection removed", forgotten: "stored password forgotten",
  store: "storing the password failed", enable: "enabling protection failed", change: "changing the password failed",
  remove: "removing protection failed", forget: "forgetting the password failed",
  set: "device password set", test_failed: "device password test failed"};
/** Password action of a log entry; a new password that was not stored is marked. */
const passwordAction = e => (PASSWORD_ACTIONS[e.action] || e.action) + (e.stored === false ? " – not stored in this app" : "");
/** Label of an app option for the log (key if unknown). */
const optionLabel = key => (OPTION_FIELDS.find(f => f[0] === key) || [key, key])[1];
const INSTALL_EVENTS = {started: "started", confirmed: "confirmed", unconfirmed: "not confirmed", failed: "failed"};
// "install": check after installations, only in entries of 3.1.0-rc2 to 3.1.1-rc2
const TRIGGER_LABELS = {manual: "Manual", scheduled: "Scheduled", device: "Single-device", install: "After-installation",
  step: "After-intermediate-version"};
const arrow = (a, b) => `${a ?? "–"} → ${b ?? "–"}`;
/** Result of a check from its counts ("1 update · 1 warning · 1 error · 2 ignored"), the
 *  same form on every width; entries before 3.0 have only the server's text. */
function checkCounts(e) {
  if (typeof e.updates !== "number") return e.result;
  const n = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  return [n(e.updates, "update"), n(e.warnings || 0, "warning"), n(e.errors || 0, "error"),
    e.ignored?.length ? `${e.ignored.length} ignored` : null].filter(Boolean).join(" · ");
}

/** Main line and optional details of one log entry. */
function logContent(e) {
  switch (e.kind) {
    case "check": {
      // A scheduled check that could not start (other work running): cancelled, not repeated
      if (e.skipped) return [el("div", `Scheduled check cancelled · ${e.skipped}`)];
      const line = el("div", `${TRIGGER_LABELS[e.trigger] || "Scheduled"} check · ${e.devices} device${e.devices === 1 ? "" : "s"} · ${e.duration} s · ${checkCounts(e)}`);
      if (!e.findings?.length && !e.ignored?.length) return [line];
      return [keptDetails(`log-${e.ts}-check`, line.textContent, checkFindings(e))];
    }
    case "download":
      return [box("div", null, `Firmware downloaded: ${e.type} ${e.version} · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}… `,
        e.https === false ? el("span", "HTTP only", "tag warn") : null)];
    case "install":
      return [box("div", null, `${e.device}: ${e.downgrade ? "downgrade" : "installation"} ${INSTALL_EVENTS[e.event] || e.event} · ${arrow(e.old, e.new)} `,
        e.unverified ? el("span", "Not verified by the update server", "tag warn") : null),
        e.restarted ? el("div", "Device restarted before the installation", "sub") : null, e.detail ? el("div", e.detail, "sub") : null].filter(Boolean);
    case "delete":
      if (e.unknown) return [el("div", `Unknown file deleted · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}…`)];
      return [el("div", `Firmware deleted: ${e.type} ${e.version}${e.source === "upload" ? " (uploaded)" : e.source === "import" ? " (imported)" : ""}`),
        e.released?.length ? el("div", `No longer provided for: ${e.released.join(", ")}`, "sub") : null].filter(Boolean);
    case "upload":
      return [el("div", `Firmware uploaded: ${e.type} ${e.version} · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}… · ${e.sha_checked ? "SHA-256 checked" : "SHA-256 not checked"}`)];
    case "setting":
      return [el("div", e.device), ...(e.changes || []).map(c => el("div", `${c.setting}: ${arrow(c.old, c.new)}`, "sub")), e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "password":
      return [el("div", `${e.device}: ${passwordAction(e)}`), e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "options":
      if (e.confirmed) return [el("div", "Download base URL confirmed as the address of Home Assistant")];
      return [el("div", e.ok ? "App settings saved – app restarted" : "App settings could not be saved"),
        ...(e.changes || []).map(c => el("div", c.redacted ? `${optionLabel(c.option)}: changed` : `${optionLabel(c.option)}: ${arrow(c.old, c.new)}`, "sub")),
        e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "export":
      return [el("div", `Firmware store exported · ${e.files ?? 0} file${e.files === 1 ? "" : "s"} (${e.downloads ?? 0} downloaded, ${e.uploads ?? 0} uploaded, ${e.imports ?? 0} imported) · ${mb(e.size || 0)}`),
        e.damaged ? el("div", `${e.damaged} damaged stored file${e.damaged === 1 ? "" : "s"} left out (SHA-256 no longer matches)`, "warnc") : null].filter(Boolean);
    case "import": {
      const line = el("div", `Firmware store imported · ${e.stored ?? 0} new · ${e.same ?? 0} already stored (${e.updated ?? 0} with data added) · ${e.conflicts ?? 0} conflict${e.conflicts === 1 ? "" : "s"}`);
      if (!e.conflicts_list || !e.conflicts_list.length) return [line];
      return [line, el("div", `Conflicts – stored file kept: ${e.conflicts_list.join(", ")}`, "sub")];
    }
    case "setup":
      return [el("div", "First start: firmware store import skipped")];
    case "store":   // the list of the store was lost: files taken over from the disk
      return [el("div", `Firmware store list was lost: ${adoptedLine(e)}`),
        ...(e.adopted || []).map(f => el("div", `${f.type} ${f.version} · SHA ${(f.sha256 || "").slice(0, 12)}…`, "sub"))];
    case "device":
      return [el("div", deviceAction(e))];
    case "log":
      return [el("div", `Log cleared · ${e.entries ?? 0} entr${e.entries === 1 ? "y" : "ies"} deleted`)];
    default:
      return [el("div", e.kind)];
  }
}
/** Device results of a check, each colored by its own severity (an update is
 *  green and a warning yellow also inside an entry that is an error as a whole);
 *  then the ignored devices (not contacted) in the normal text colour. */
function checkFindings(e) {
  return box("ul", null, ...(e.findings || []).map(f => {
    const sev = f.severity || (f.error ? "error" : "success");
    // Updates from the firmware store are marked (the server was not asked)
    const from = f.source && f.source !== "manufacturer" && OFFER_SOURCES[f.source] ? ` · ${OFFER_SOURCES[f.source]}` : "";
    return el("li", f.error ? `${f.device}: ${f.error}` : `${f.device}: ${arrow(f.version, f.offer)}${from}`, sev === "error" ? "bad" : sev === "warning" ? "warnc" : "okc");
  }), ...(e.ignored || []).map(name => el("li", `${name}: ignored – not checked`, "neutral")));
}
/** Mobile: short summary lines of an entry (details stay collapsed). */
function logSummary(e) {
  switch (e.kind) {
    case "check": {
      if (e.skipped) return ["Scheduled check cancelled", e.skipped];
      const first = `${TRIGGER_LABELS[e.trigger] || "Scheduled"} · ${e.devices} device${e.devices === 1 ? "" : "s"} · ${e.duration} s`;
      return [first, checkCounts(e)];
    }
    case "download": return [`${e.type} ${e.version}`];
    case "delete": return [e.unknown ? "Unknown file" : `${e.type} ${e.version}`];
    case "upload": return [`${e.type} ${e.version}`];
    case "install": return [`${e.device}: ${e.downgrade ? "downgrade " : ""}${INSTALL_EVENTS[e.event] || e.event}`];
    case "setting": return [e.device];
    case "password": return [`${e.device}: ${passwordAction(e)}`];
    case "options": return [e.confirmed ? "Download base URL confirmed" : e.ok ? "App settings saved" : "App settings not saved"];
    case "log": return ["Log cleared"];
    case "export": return [`${e.files ?? 0} file${e.files === 1 ? "" : "s"} exported`];
    case "import": return [`${e.stored ?? 0} new · ${e.conflicts ?? 0} conflict${e.conflicts === 1 ? "" : "s"}`];
    case "setup": return ["Import skipped"];
    case "store": return [`${adoptedCount(e)} read from the disk${e.unknown ? ` · ${e.unknown} unknown` : ""}`];
    case "device": return [deviceAction(e)];
    default: return [e.kind];
  }
}
export const mobileQuery = matchMedia("(max-width:700px)");
mobileQuery.addEventListener("change", () => render(true));
/** The device an entry is about (for grouping): the device of an installation, setting
 *  or password entry, or the only device of a single-device check with a finding. */
const logDevice = e => e.device || (e.kind === "check" && e.devices === 1 && e.findings && e.findings.length === 1 ? e.findings[0].device : null) || null;
/** Delete one log entry after a confirmation. */
function deleteLogEntry(e) {
  const title = "Delete log entry?";
  modal(title, [valueList([["Time", fmt(e.ts)], ["Type", KIND_LABELS[e.kind] || e.kind], ["Entry", logSummary(e).join(" · ")]]),
    el("p", "The entry is removed from the activity log for good.", "hint")],
  [{text: "Cancel"}, {text: "Delete", kind: "danger", action: async () => {
    try { await api(`api/logs/${encodeURIComponent(e.id)}/delete`, {confirm: true}); closeModal(); ui.logsLoaded = 0; load(true); }
    catch (err) { message(title, err.message, "error", "delete"); }
  }}], "delete");
}
/** Delete icon of an entry (entries of versions before 3.1.1 get their ID at the start). */
function logDelete(e) {
  const b = iconButton("delete", "Delete entry", () => deleteLogEntry(e), "red log-del"); b.disabled = !e.id;
  return b;
}
/** Desktop entry: time, type, content, user, delete. group = position in a run of
 *  entries of the same device ("first" | "mid" | "last" | null): marked by a side bar. */
function logRow(e, d, group) {
  const sev = severity(e);
  return box("div", "log" + (group ? ` grp grp-${group}` : ""), el("span", `${pad(d.getHours())}:${pad(d.getMinutes())}`, "mono sub"),
    el("span", KIND_LABELS[e.kind] || e.kind, "tag"),   // neutral like on phones: the outcome has its own label
    box("div", "log-body" + (sev === "error" ? " bad" : ""), el("span", SEVERITY_LABELS[sev], `tag sev-${sev}`), box("div", null, ...logContent(e))),
    el("span", e.user || "", "user"), logDelete(e));
}
/** Mobile entry: time/type/outcome, short summary, details collapsed. Details that
 *  only repeat the summary are left out: the summary is shown without expansion. */
function logTile(e, d, group) {
  const sev = severity(e), summary = logSummary(e);
  const head = box("div", "log-head", el("span", `${pad(d.getHours())}:${pad(d.getMinutes())}`, "mono sub"),
    el("span", KIND_LABELS[e.kind] || e.kind, "tag"), el("span", SEVERITY_LABELS[sev], `tag sev-${sev}`), el("span", null, "spacer"), logDelete(e));
  const body = box("div", "log-details", ...(e.kind === "check" ? (e.findings?.length || e.ignored?.length ? [checkFindings(e)] : [el("div", checkCounts(e), "sub")]) : logContent(e)));
  // The user stands top right, beside the first summary line
  const lines = () => summary.map((t, i) => i === 0 && e.user ? box("div", "first", el("span", t), el("span", e.user, "user")) : el("div", t));
  const same = [...body.children].map(c => c.textContent.trim()).join("\n") === summary.join("\n");
  if (same) return box("div", "log-m" + (group ? ` grp grp-${group}` : ""), head, box("div", "log-sum", ...lines()));
  const det = el("details"), key = `logm-${e.ts}-${e.kind}`;
  det.open = openDetails.has(key);
  det.ontoggle = () => { det.open ? openDetails.add(key) : openDetails.delete(key); };
  // One chevron after the text of the last summary line (beside the user name it follows the
  // text, so the name keeps its place)
  const sum = el("summary"), shown = lines(), last = shown[shown.length - 1], chev = el("span", null, "chev");
  (last.classList.contains("first") ? last.firstChild : last).append(chev);
  sum.append(...shown);
  det.append(sum, body);
  return box("div", "log-m" + (group ? ` grp grp-${group}` : ""), head, det);
}
/** Logs tab: grouped by day, filter, device search, quiet checks. */
export function renderLogs() {
  const q = $("log-search").value.trim().toLowerCase(), quiet = $("log-quiet").checked;
  const devMatch = e => !q || [e.device, ...(e.findings || []).map(f => f.device), ...(e.ignored || [])].some(v => String(v || "").toLowerCase().includes(q));
  const base = ui.logs.filter(e => devMatch(e) && !(quiet && e.kind === "check" && !(e.findings || []).length));
  logFilter.set(ui.logFilter, Object.fromEntries(Object.entries(LOG_FILTERS).map(([key, [, fn]]) => [key, base.filter(fn).length])));
  const out = [], shown = base.filter(LOG_FILTERS[ui.logFilter][1]);
  const dayOf = e => { const d = new Date(e.ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  // Directly consecutive entries of the same device on the same day form a group
  const same = (a, b) => !!a && !!b && !!logDevice(a) && logDevice(a) === logDevice(b) && dayOf(a) === dayOf(b);
  let day = null, card = null;
  shown.forEach((e, i) => {
    const d = new Date(e.ts * 1000), key = dayOf(e);
    if (key !== day) {
      day = key; out.push(el("div", `${key} · ${d.toLocaleDateString("en", {weekday: "long"})}`, "day"));
      card = el("div", null, "card"); out.push(card);
    }
    const before = same(shown[i - 1], e), after = same(e, shown[i + 1]);
    const group = before && after ? "mid" : before ? "last" : after ? "first" : null;
    card.append(mobileQuery.matches ? logTile(e, d, group) : logRow(e, d, group));
  });
  $("log-clear").disabled = !ui.logs.length;
  if (!out.length) out.push(box("div", "card", el("div", ui.logs.length ? "No entries match." : "No log entries yet.", "empty")));
  $("log-list").replaceChildren(...out);
}
$("log-search").oninput = () => render(true);
searchTool($("log-search"));
$("log-quiet").onchange = () => render(true);
/** Delete the whole activity log after a confirmation (one entry naming the user stays). */
$("log-clear").onclick = () => {
  const title = "Delete all logs?", count = ui.logs.length;
  modal(title, [el("p", `All ${count} log entr${count === 1 ? "y" : "ies"} are deleted for good – regardless of the filter. One entry stays that names who cleared the log.`)],
    [{text: "Cancel"}, {text: "Delete all", kind: "danger", action: async () => {
      try { await api("api/logs/clear", {confirm: true}); closeModal(); ui.logsLoaded = 0; load(true); }
      catch (err) { message(title, err.message, "error", "delete"); }
    }}], "delete");
};
