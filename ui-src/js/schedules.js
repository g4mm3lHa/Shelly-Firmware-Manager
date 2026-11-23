// Tab "Schedules": the schedules (restart, cloud or Bluetooth on/off, backup of the
// settings at fixed times on several Shellys) and the approved installations waiting for
// their time ("Install at").
// The server runs the schedules (manager_schedules); times are local times of Home
// Assistant, shown as the server formats them.
import {
  $, api, box, cell, checkbox, closeModal, collator, confirmDialog, deviceChoice, el, formError, icon, iconButton, input, message, modal, radio, section, t,
  toast, ui,
} from "./core.js";
import {cancelPlan} from "./install.js";

const ACTIONS = {restart: t("Restart"), cloud_on: t("Cloud on"), cloud_off: t("Cloud off"), ble_on: t("Bluetooth on"), ble_off: t("Bluetooth off"),
  backup: t("Backup")};
const ACTION_ICONS = {restart: "restart", cloud_on: "cloud", cloud_off: "cloud", ble_on: "ble", ble_off: "ble", backup: "backup"};
const DAYS = [t("Mon"), t("Tue"), t("Wed"), t("Thu"), t("Fri"), t("Sat"), t("Sun")];
const PAUSE_DEFAULT = 3, PAUSE_MAX = 600, EVERY_MAX = 365, NAME_MAX = 64;
const SEVERITY = {success: [t("Success"), "sev-success"], warning: [t("Warning"), "sev-warning"], missed: [t("Missed"), "sev-warning"]};
const isBle = action => action === "ble_on" || action === "ble_off";
const isCloud = action => action === "cloud_on" || action === "cloud_off";

/** Repeat rule and time ("Daily · 03:00", "Mon, Thu · 05:00", "Every 14 days · 04:00"). */
const repeatText = s => (s.repeat.kind === "daily" ? t("Daily") : s.repeat.kind === "every" ? t("Every {n} days", {n: s.repeat.n})
  : s.repeat.days.map(d => DAYS[d]).join(", ")) + ` · ${s.time}`;
/** Devices of a schedule: "All Shellys (n)" or the names (at most three, then the count). */
function devicesText(s) {
  if (s.devices === "all") return t("All Shellys ({n})", {n: s.count});
  const names = new Map(ui.state.devices.map(d => [d.id, d.name]));
  const ids = Array.isArray(s.devices) ? s.devices : [], shown = ids.slice(0, 3).map(id => names.get(id) || "?");
  if (!ids.length) return t("No device");   // all its devices were removed from the list: switched off, choose again
  return shown.join(", ") + (ids.length > 3 ? " " + t("and {n} more", {n: ids.length - 3}) : "");
}
/** Short result of the last run, from its counts (English text of the server otherwise). */
function lastText(last) {
  if (last.severity === "missed") return last.reason ? t("Missed – {reason}", {reason: last.reason}) : last.text;
  const c = last.counts;
  if (!c) return last.text || "";
  const restart = last.action === "restart", ran = (c.run ?? c.total) || c.interrupted;
  // Backup: a device whose settings did not change since its newest backup gets no new one
  if (last.action === "backup") {
    const parts = [ran ? t("{done} stored · {unchanged} unchanged · {failed} not backed up", {done: c.done, unchanged: c.unchanged || 0,
      failed: (c.unreachable || 0) + (c.refused || 0)}) : t("No device to run on")];
    if (c.left) parts.push(t("{n} left out", {n: c.left}));
    if (c.interrupted) parts.push(t("interrupted – the app stopped"));
    return parts.join(" · ");
  }
  // "n of m": m = all chosen devices, left-out ones included ("run": devices acted on)
  const parts = [ran ? t(restart ? "{done} of {total} restarted" : "{done} of {total} switched", {done: c.done, total: c.total}) : t("No device to run on")];
  if (c.unreachable) parts.push(t("{n} not reachable", {n: c.unreachable}));
  if (c.refused) parts.push(t("{n} refused", {n: c.refused}));
  if (c.left) parts.push(t("{n} left out", {n: c.left}));
  if (c.interrupted) parts.push(t("interrupted – the app stopped"));
  return parts.join(" · ");
}
/** Cell of a row; on phones a labelled line of the tile (data-label). */
const schCell = (label, cls, ...nodes) => cell("sch-c " + cls, label, ...nodes);
const head = (cls, labels) => box("div", `sch-row sch-head ${cls}`, ...labels.map(([text, c]) => el("div", text, "sch-c " + c)));
/** All user-editable fields of a schedule (what the server expects when saving). */
const fields = s => ({name: s.name, on: s.on, action: s.action, pause: s.pause ?? PAUSE_DEFAULT, time: s.time, repeat: s.repeat, devices: s.devices});

function scheduleRow(s) {
  const on = input("checkbox", {checked: s.on});
  on.setAttribute("aria-label", t("Schedule “{name}” is on", {name: s.name}));
  on.onchange = async () => {
    try { await saveSchedule(s.id, {...fields(s), on: on.checked}); closeModal(); }
    catch (e) { on.checked = s.on; message(t("Edit schedule"), e.message, "error", "schedules"); }
  };
  const act = box("span", "sch-act", icon(ACTION_ICONS[s.action] || "schedules"), el("span", ACTIONS[s.action] || s.action));
  const last = s.running ? [el("span", t("Running …"), "sub")]
    : s.last ? [el("span", s.last.at_text, "nowrap"), el("span", (SEVERITY[s.last.severity] || SEVERITY.warning)[0], "tag " + (SEVERITY[s.last.severity] || SEVERITY.warning)[1]),
      el("div", lastText({...s.last, action: s.action}), "sub")]
    : [el("span", t("never"), "sub")];
  const pause = s.action === "restart" ? el("div", s.pause ? t("{n} s between devices", {n: s.pause}) : t("All at once"), "sub") : null;
  const left = s.left_out ? el("div", t("{n} left out at the moment", {n: s.left_out}), "sub") : null;
  const row = box("div", "sch-row" + (s.on ? "" : " off"),
    schCell("", "sch-on", on),
    schCell("", "sch-name", el("div", s.name, "name"), pause),
    schCell(t("Action"), "sch-action", act),
    schCell(t("Repeat"), "sch-when", el("span", repeatText(s))),
    schCell(t("Devices"), "sch-dev", el("span", devicesText(s)), left),
    schCell(t("Next run"), "sch-next", el("span", s.on && s.next_text ? s.next_text : "–", "nowrap")),
    schCell(t("Last run"), "sch-last", ...last),
    schCell("", "sch-btns",
      iconButton("play", t("Run “{name}” now", {name: s.name}), () => runNow(s)),
      iconButton("pencil", t("Edit “{name}”", {name: s.name}), () => openSchedule(s)),
      iconButton("delete", t("Delete “{name}”", {name: s.name}), () => deleteSchedule(s), "red")));
  row.dataset.key = s.id;   // the keyboard focus stays in this row when the list is redrawn
  return row;
}
function plannedRow(d) {
  const p = d.planned, cancel = el("button", t("Cancel"), "small"); cancel.onclick = () => cancelPlan(d);
  const row = box("div", "sch-row sch-inst",
    schCell("", "sch-name", el("div", d.name, "name"), d.shelly_name && d.shelly_name !== d.name ? el("div", d.shelly_name, "sub") : null),
    schCell(t("Version"), "sch-ver", box("span", "nowrap", el("span", p.version || "?"), el("span", "→", "arrow"), el("span", p.target, "newver")),
      p.beta ? el("span", t("Beta"), "tag beta") : null),
    schCell(t("Install at"), "sch-next", el("span", p.at_text, "nowrap")),
    schCell(t("Approved"), "sch-last", el("span", `${p.on_text} · ${p.by}`)),
    schCell("", "sch-btns", cancel));
  row.dataset.key = d.id;
  return row;
}

/** Tab "Schedules": the schedules and the approved installations waiting for their time. */
export function renderSchedules() {
  const s = ui.state, list = s.schedules || [], planned = s.devices.filter(d => d.planned);
  const cards = [];
  if (!list.length && !planned.length) {
    cards.push(box("div", "card", box("div", "card-head", el("h2", t("Schedules · {n}", {n: 0}))),
      el("div", t("No schedules yet – “Add schedule” restarts Shellys, switches their cloud or Bluetooth or backs up their settings at fixed times. Updates are scheduled in the installation dialog (“Install at”)."), "empty")));
  } else {
    cards.push(box("div", "card schedules", box("div", "card-head", el("h2", t("Schedules · {n}", {n: list.length}))),
      ...(list.length ? [head("", [[t("On"), "sch-on"], [t("Name"), "sch-name"], [t("Action"), "sch-action"], [t("Repeat"), "sch-when"], [t("Devices"), "sch-dev"],
        [t("Next run"), "sch-next"], [t("Last run"), "sch-last"], ["", "sch-btns"]]), ...list.map(scheduleRow)]
        : [el("div", t("No schedules yet – “Add schedule” restarts Shellys, switches their cloud or Bluetooth or backs up their settings at fixed times."), "empty")])));
    cards.push(box("div", "card schedules", box("div", "card-head", el("h2", t("Scheduled Installations · {n}", {n: planned.length})),
      el("span", t("Approved in the installation dialog with “Install at”"), "sub")),
      ...(planned.length ? [head("sch-inst", [[t("Device"), "sch-name"], [t("Version"), "sch-ver"], [t("Install at"), "sch-next"], [t("Approved"), "sch-last"], ["", "sch-btns"]]),
        ...planned.map(plannedRow)] : [el("div", t("No scheduled installation – choose “Install at” in the installation dialog."), "empty")])));
  }
  $("sch-cards").replaceChildren(...cards);
}

/** Save a schedule (new: no id). */
const saveSchedule = (id, body) => api(id ? `api/schedules/${encodeURIComponent(id)}` : "api/schedules", body);
/** Dialog "Add schedule" / "Edit schedule". */
function openSchedule(s) {
  const edit = !!s;
  s = s || {on: true, name: "", action: "restart", repeat: {kind: "daily"}, time: "03:00", devices: "all", pause: PAUSE_DEFAULT};
  const name = input("text", {value: s.name, maxLength: NAME_MAX, placeholder: t("e.g. Weekly restart")});
  const action = el("select");
  for (const [key, label] of Object.entries(ACTIONS)) { const o = el("option", label); o.value = key; o.selected = key === s.action; action.append(o); }
  const time = input("time", {step: 60, value: s.time});
  const pause = input("number", {min: 0, max: PAUSE_MAX, value: s.pause ?? PAUSE_DEFAULT});
  const field = (label, control, hint) => {
    const lab = el("label", label); control.id = "sch-" + Math.random().toString(36).slice(2); lab.htmlFor = control.id;
    return box("div", "sch-field", lab, box("div", null, control, hint ? el("div", hint, "desc") : null));
  };
  const pauseField = field(t("Pause between devices (seconds)"), pause, t("Restarts the devices one after the other; 0 = all at once. Default 3"));
  // Repeat
  const [dailyR, daily] = radio("sch-rep", [el("span", t("Daily"))], s.repeat.kind === "daily");
  const [weekdaysR, weekdays] = radio("sch-rep", [el("span", t("On weekdays"))], s.repeat.kind === "weekdays");
  const dayChecks = DAYS.map((d, i) => checkbox(d, s.repeat.kind === "weekdays" && s.repeat.days.includes(i))), dayBoxes = dayChecks.map(([c]) => c);
  const days = box("div", "sch-days", ...dayChecks.map(([, label]) => label));
  const n = input("number", {min: 1, max: EVERY_MAX, value: (s.repeat.kind === "every" && s.repeat.n) || 14, className: "sch-n"});
  n.setAttribute("aria-label", t("Number of days"));
  const [everyR, every] = radio("sch-rep", [el("span", t("Every")), " ", n, " ", el("span", t("days"))], s.repeat.kind === "every");
  // Devices: all or a list (removed devices cannot be chosen)
  const chosen = new Set(Array.isArray(s.devices) ? s.devices : []);
  const devices = ui.state.devices.filter(d => !d.removed || chosen.has(d.id)).sort((a, b) => collator.compare(String(a.name), String(b.name)));
  const tagsOf = d => [d.ignored ? el("span", t("Ignored"), "tag warn") : null, d.battery ? el("span", t("Battery"), "tag") : null,
    d.removed ? el("span", t("Removed"), "tag warn") : null,
    isBle(action.value) && (d.gen === 1 || d.ble === "unsupported") ? el("span", t("No Bluetooth"), "tag") : null,
    isCloud(action.value) && d.cloud === "unsupported" ? el("span", t("No cloud"), "tag") : null];
  const choice = deviceChoice("sch-dev", devices, [el("span", t("All Shellys (new ones are included automatically)"))], s.devices === "all" ? "all" : chosen, tagsOf);
  const proxyBox = el("div", "", "warnbox");
  const leftHint = el("p", "", "hint");
  const [on, onLabel] = checkbox(t("Schedule is on"), s.on);
  const sync = () => {
    const kind = action.value;
    pauseField.hidden = kind !== "restart";
    days.hidden = !weekdaysR.checked;
    choice.refresh();
    // Bluetooth off on a Bluetooth proxy of Home Assistant: HA switches it on again
    const targets = choice.all.checked ? devices.filter(d => !d.removed) : choice.ticked();
    const proxies = kind === "ble_off" ? targets.filter(d => d.ble_proxy).map(d => d.name) : [];
    proxyBox.textContent = proxies.length ? t("{names}: Home Assistant uses this Shelly as Bluetooth proxy and turns Bluetooth on again.", {names: proxies.join(", ")}) : "";
    proxyBox.hidden = !proxies.length;
    leftHint.textContent = (isBle(kind) ? t("Not included in a run: ignored and removed devices, battery devices (usually asleep), devices without a Bluetooth switch (Gen1).")
      : isCloud(kind) ? t("Not included in a run: ignored and removed devices, battery devices (usually asleep), devices without a cloud switch.")
        : kind === "backup" ? t("Not included in a run: ignored and removed devices, battery devices that are asleep. A device whose settings did not change since its last backup gets no new one.")
          : t("Not included in a run: ignored and removed devices, battery devices (usually asleep).")) + " " +
      t("Every run is written to the log; Home Assistant notifies only about errors. A run the app misses is skipped.");
  };
  for (const node of [action, daily, weekdays, every, ...choice.nodes]) node.addEventListener("change", sync);
  sync();
  const content = [
    box("div", "sch-form", field(t("Name"), name), field(t("Action"), action), pauseField, field(t("Time"), time, t("Local time of Home Assistant"))),
    section(t("Repeat"), box("div", "modes", daily, weekdays, days, every)),
    section(t("Devices"), box("div", "modes", ...choice.nodes)),
    proxyBox, leftHint,
    onLabel,
  ];
  // An error (e.g. no name) is shown in the dialog, which stays open with all entries
  const error = formError(); content.push(error.line);
  const title = edit ? t("Edit schedule") : t("Add schedule");
  modal(title, content, [{text: t("Cancel")}, {text: t("Save"), kind: "primary", action: async () => {
    const kind = [dailyR, weekdaysR, everyR].findIndex(r => r.checked);
    const repeat = kind === 1 ? {kind: "weekdays", days: dayBoxes.flatMap((c, i) => c.checked ? [i] : [])}
      : kind === 2 ? {kind: "every", n: Number(n.value)} : {kind: "daily"};
    // The pause counts only for a restart; another action keeps a valid default
    const seconds = pause.value === "" ? NaN : Number(pause.value);
    const body = {name: name.value.trim(), on: on.checked, action: action.value, time: time.value, repeat,
      pause: action.value === "restart" || Number.isInteger(seconds) ? seconds : PAUSE_DEFAULT,
      devices: choice.all.checked ? "all" : choice.ticked().map(d => d.id)};
    try { await saveSchedule(edit ? s.id : null, body); closeModal(); }
    catch (e) { error.show(e.message); }
  }}], "schedules");
  name.focus();
}
function deleteSchedule(s) {
  confirmDialog({title: t("Delete schedule?"), icon: "delete", button: t("Delete"),
    content: [el("p", t("“{name}” ({action}, {repeat}) is deleted. Its log entries stay.", {name: s.name, action: ACTIONS[s.action] || s.action, repeat: repeatText(s)}))],
    action: async () => { await api(`api/schedules/${encodeURIComponent(s.id)}/delete`, {confirm: true}); closeModal(); }});
}
/** "Run now": the schedule runs at once on its devices, after a confirmation. */
function runNow(s) {
  confirmDialog({title: t("Run schedule now?"), icon: "schedules", button: t("Run now"), kind: "primary",
    content: [el("p", t("“{name}” runs now: {action} on {devices}.", {name: s.name, action: ACTIONS[s.action] || s.action, devices: devicesText(s)})),
      el("p", t("The next scheduled run stays as it is. The result is written to the log and shown under “Last run”."), "hint")],
    action: async () => { await api(`api/schedules/${encodeURIComponent(s.id)}/run`, {confirm: true}); closeModal(); toast(t("“{name}” is running", {name: s.name})); }});
}
const add = $("sch-add"); add.prepend(icon("plus")); add.onclick = () => openSchedule(null);
