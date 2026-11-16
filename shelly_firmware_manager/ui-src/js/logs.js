// Logs tab: activity log with filters, grouping per device and deleting entries.
import {$, api, box, closeModal, el, fmt, iconButton, keptDetails, mb, message, modal, openDetails, pad, ui, valueList} from "./core.js";
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
const KIND_LABELS = {check: "Check", download: "Download", upload: "Upload", install: "Installation", delete: "Deleted", setting: "Setting",
  password: "Password", options: "App settings", log: "Log"};
// Successful actions, failed actions are logged with the mode; older entries: set/removed/test_failed
const PASSWORD_ACTIONS = {stored: "password stored", enabled: "password protection enabled", changed: "password changed",
  removed: "password protection removed", forgotten: "stored password forgotten",
  store: "storing the password failed", enable: "enabling protection failed", change: "changing the password failed",
  remove: "removing protection failed", forget: "forgetting the password failed",
  set: "device password set", test_failed: "device password test failed"};
/** Label of an app option for the log (key if unknown). */
const optionLabel = key => (OPTION_FIELDS.find(f => f[0] === key) || [key, key])[1];
const INSTALL_EVENTS = {started: "started", confirmed: "confirmed", unconfirmed: "not confirmed", failed: "failed"};
// "install": check after installations, only in entries of 3.1.0-rc2 to 3.1.1-rc2
const TRIGGER_LABELS = {manual: "Manual", scheduled: "Scheduled", device: "Single-device", install: "After-installation",
  step: "After-intermediate-version"};
const arrow = (a, b) => `${a ?? "–"} → ${b ?? "–"}`;

/** Main line and optional details of one log entry. */
function logContent(e) {
  switch (e.kind) {
    case "check": {
      // A scheduled check that could not start (other work running): cancelled, not repeated
      if (e.skipped) return [el("div", `Scheduled check cancelled · ${e.skipped}`)];
      const line = el("div", `${TRIGGER_LABELS[e.trigger] || "Scheduled"} check · ${e.devices} device${e.devices === 1 ? "" : "s"} · ${e.duration} s · ${e.result}`);
      if (!e.findings || !e.findings.length) return [line];
      const ul = checkFindings(e);
      return [keptDetails(`log-${e.ts}-check`, line.textContent, ul)];
    }
    case "download":
      return [box("div", null, `Firmware downloaded: ${e.type} ${e.version} · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}… `,
        e.https === false ? el("span", "HTTP only", "tag warn") : null)];
    case "install":
      return [box("div", null, `${e.device}: ${e.downgrade ? "downgrade" : "installation"} ${INSTALL_EVENTS[e.event] || e.event} · ${arrow(e.old, e.new)} `,
        e.unverified ? el("span", "Not verified by the update server", "tag warn") : null), e.detail ? el("div", e.detail, "sub") : null].filter(Boolean);
    case "delete":
      return [el("div", `Firmware deleted: ${e.type} ${e.version}${e.source === "upload" ? " (uploaded)" : ""}`),
        e.released?.length ? el("div", `No longer provided for: ${e.released.join(", ")}`, "sub") : null].filter(Boolean);
    case "upload":
      return [el("div", `Firmware uploaded: ${e.type} ${e.version} · ${mb(e.size || 0)} · SHA ${(e.sha256 || "").slice(0, 12)}… · ${e.sha_checked ? "SHA-256 checked" : "SHA-256 not checked"}`)];
    case "setting":
      return [el("div", e.device), ...(e.changes || []).map(c => el("div", `${c.setting}: ${arrow(c.old, c.new)}`, "sub")), e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "password":
      return [el("div", `${e.device}: ${PASSWORD_ACTIONS[e.action] || e.action}`), e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "options":
      if (e.confirmed) return [el("div", "Download base URL confirmed as the address of Home Assistant")];
      return [el("div", e.ok ? "App settings saved – app restarted" : "App settings could not be saved"),
        ...(e.changes || []).map(c => el("div", c.redacted ? `${optionLabel(c.option)}: changed` : `${optionLabel(c.option)}: ${arrow(c.old, c.new)}`, "sub")),
        e.error ? el("div", e.error, "bad") : null].filter(Boolean);
    case "log":
      return [el("div", `Log cleared · ${e.entries ?? 0} entr${e.entries === 1 ? "y" : "ies"} deleted`)];
    default:
      return [el("div", e.kind)];
  }
}
/** Device results of a check, each colored by its own severity (an update is
 *  green and a warning yellow also inside an entry that is an error as a whole). */
function checkFindings(e) {
  return box("ul", null, ...e.findings.map(f => {
    const sev = f.severity || (f.error ? "error" : "success");
    // Updates from the firmware store are marked (the server was not asked)
    const from = f.source && f.source !== "manufacturer" && OFFER_SOURCES[f.source] ? ` · ${OFFER_SOURCES[f.source]}` : "";
    return el("li", f.error ? `${f.device}: ${f.error}` : `${f.device}: ${arrow(f.version, f.offer)}${from}`, sev === "error" ? "bad" : sev === "warning" ? "warnc" : "okc");
  }));
}
/** Mobile: short summary lines of an entry (details stay collapsed). */
function logSummary(e) {
  switch (e.kind) {
    case "check": {
      if (e.skipped) return ["Scheduled check cancelled", e.skipped];
      const first = `${TRIGGER_LABELS[e.trigger] || "Scheduled"} · ${e.devices} device${e.devices === 1 ? "" : "s"} · ${e.duration} s`;
      const counts = typeof e.updates === "number"
        ? `${e.updates} update${e.updates === 1 ? "" : "s"} · ${e.warnings || 0} warning${e.warnings === 1 ? "" : "s"} · ${e.errors || 0} error${e.errors === 1 ? "" : "s"}`
        : e.result;   // entries before 3.0
      return [first, counts];
    }
    case "download": return [`${e.type} ${e.version}`];
    case "delete": return [`${e.type} ${e.version}`];
    case "upload": return [`${e.type} ${e.version}`];
    case "install": return [`${e.device}: ${e.downgrade ? "downgrade " : ""}${INSTALL_EVENTS[e.event] || e.event}`];
    case "setting": return [e.device];
    case "password": return [`${e.device}: ${PASSWORD_ACTIONS[e.action] || e.action}`];
    case "options": return [e.confirmed ? "Download base URL confirmed" : e.ok ? "App settings saved" : "App settings not saved"];
    case "log": return ["Log cleared"];
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
    catch (err) { message(title, err.message, "error"); }
  }}]);
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
    el("span", KIND_LABELS[e.kind] || e.kind, "tag" + (sev === "error" ? " sec" : "")),
    box("div", "log-body" + (sev === "error" ? " bad" : ""), el("span", SEVERITY_LABELS[sev], `tag sev-${sev}`), box("div", null, ...logContent(e))),
    el("span", e.user || "", "user"), logDelete(e));
}
/** Mobile entry: time/type/outcome, short summary, details collapsed. */
function logTile(e, d, group) {
  const sev = severity(e), summary = logSummary(e);
  const head = box("div", "log-head", el("span", `${pad(d.getHours())}:${pad(d.getMinutes())}`, "mono sub"),
    el("span", KIND_LABELS[e.kind] || e.kind, "tag"), el("span", SEVERITY_LABELS[sev], `tag sev-${sev}`), el("span", null, "spacer"), logDelete(e));
  const body = box("div", "log-details", ...(e.kind === "check" ? (e.findings && e.findings.length ? [checkFindings(e)] : [el("div", e.result, "sub")]) : logContent(e)));
  const det = el("details"), key = `logm-${e.ts}-${e.kind}`;
  det.open = openDetails.has(key);
  det.ontoggle = () => { det.open ? openDetails.add(key) : openDetails.delete(key); };
  // The user stands top right, beside the first summary line
  const sum = el("summary");
  sum.append(...summary.map((t, i) => i === 0 && e.user ? box("div", "first", el("span", t), el("span", e.user, "user")) : el("div", t)));
  det.append(sum, body);
  return box("div", "log-m" + (group ? ` grp grp-${group}` : ""), head, det);
}
/** Logs tab: grouped by day, filters, device search, quiet checks. */
export function renderLogs() {
  const q = $("log-search").value.trim().toLowerCase(), quiet = $("log-quiet").checked;
  const devMatch = e => !q || [e.device, ...(e.findings || []).map(f => f.device)].some(v => String(v || "").toLowerCase().includes(q));
  const base = ui.logs.filter(e => devMatch(e) && !(quiet && e.kind === "check" && !(e.findings || []).length));
  $("log-chips").replaceChildren(...Object.entries(LOG_FILTERS).map(([key, [label, fn]]) => {
    const b = el("button", label, "chip" + (ui.logFilter === key ? " active" : ""));
    b.append(el("span", base.filter(fn).length, "count")); b.onclick = () => { ui.logFilter = key; render(true); };
    return b;
  }));
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
$("log-quiet").onchange = () => render(true);
/** Delete the whole activity log after a confirmation (one entry naming the user stays). */
$("log-clear").onclick = () => {
  const title = "Delete all logs?", count = ui.logs.length;
  modal(title, [el("p", `All ${count} log entr${count === 1 ? "y" : "ies"} are deleted for good – regardless of the filter. One entry stays that names who cleared the log.`)],
    [{text: "Cancel"}, {text: "Delete all", kind: "danger", action: async () => {
      try { await api("api/logs/clear", {confirm: true}); closeModal(); ui.logsLoaded = 0; load(true); }
      catch (err) { message(title, err.message, "error"); }
    }}]);
};
