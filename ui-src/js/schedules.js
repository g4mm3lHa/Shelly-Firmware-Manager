// Tab "Schedules": the schedules (restart, cloud or Bluetooth on/off at fixed times on
// several Shellys) and the approved installations waiting for their time ("Install at").
// The server runs the schedules (manager_schedules); times are local times of Home
// Assistant, shown as the server formats them.
import {$, api, box, closeModal, el, icon, iconButton, message, modal, section, t, toast, ui} from "./core.js";
import {cancelPlan} from "./install.js";

const ACTIONS = {restart: t("Restart"), cloud_on: t("Cloud on"), cloud_off: t("Cloud off"), ble_on: t("Bluetooth on"), ble_off: t("Bluetooth off")};
const ACTION_ICONS = {restart: "restart", cloud_on: "cloud", cloud_off: "cloud", ble_on: "ble", ble_off: "ble"};
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
  return shown.join(", ") + (ids.length > 3 ? " " + t("and {n} more", {n: ids.length - 3}) : "");
}
/** Short result of the last run, from its counts (English text of the server otherwise). */
function lastText(last) {
  if (last.severity === "missed") return last.reason ? t("Missed – {reason}", {reason: last.reason}) : last.text;
  const c = last.counts;
  if (!c) return last.text || "";
  const restart = last.action === "restart";
  // "n of m": m = all chosen devices, left-out ones included ("run": devices acted on)
  const parts = [(c.run ?? c.total) ? t(restart ? "{done} of {total} restarted" : "{done} of {total} switched", {done: c.done, total: c.total}) : t("No device to run on")];
  if (c.unreachable) parts.push(t("{n} not reachable", {n: c.unreachable}));
  if (c.refused) parts.push(t("{n} refused", {n: c.refused}));
  if (c.left) parts.push(t("{n} left out", {n: c.left}));
  return parts.join(" · ");
}
/** Cell of a row; on phones a labelled line of the tile (data-label). */
const cell = (label, cls, ...nodes) => { const c = box("div", "sch-c " + cls, ...nodes); if (label) c.dataset.label = label; return c; };
const head = (cls, labels) => box("div", `sch-row sch-head ${cls}`, ...labels.map(([text, c]) => el("div", text, "sch-c " + c)));
/** All user-editable fields of a schedule (what the server expects when saving). */
const fields = s => ({name: s.name, on: s.on, action: s.action, pause: s.pause ?? PAUSE_DEFAULT, time: s.time, repeat: s.repeat, devices: s.devices});

function scheduleRow(s) {
  const on = el("input"); on.type = "checkbox"; on.checked = s.on;
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
  return box("div", "sch-row" + (s.on ? "" : " off"),
    cell("", "sch-on", on),
    cell("", "sch-name", el("div", s.name, "name"), pause),
    cell(t("Action"), "sch-action", act),
    cell(t("Repeat"), "sch-when", el("span", repeatText(s))),
    cell(t("Devices"), "sch-dev", el("span", devicesText(s)), left),
    cell(t("Next run"), "sch-next", el("span", s.on && s.next_text ? s.next_text : "–", "nowrap")),
    cell(t("Last run"), "sch-last", ...last),
    cell("", "sch-btns",
      iconButton("play", t("Run “{name}” now", {name: s.name}), () => runNow(s)),
      iconButton("pencil", t("Edit “{name}”", {name: s.name}), () => openSchedule(s)),
      iconButton("delete", t("Delete “{name}”", {name: s.name}), () => deleteSchedule(s), "red")));
}
function plannedRow(d) {
  const p = d.planned, cancel = el("button", t("Cancel"), "small"); cancel.onclick = () => cancelPlan(d);
  return box("div", "sch-row sch-inst",
    cell("", "sch-name", el("div", d.name, "name"), d.shelly_name && d.shelly_name !== d.name ? el("div", d.shelly_name, "sub") : null),
    cell(t("Version"), "sch-ver", box("span", "nowrap", el("span", p.version || "?"), el("span", "→", "arrow"), el("span", p.target, "newver")),
      p.beta ? el("span", t("Beta"), "tag beta") : null),
    cell(t("Install at"), "sch-next", el("span", p.at_text, "nowrap")),
    cell(t("Approved"), "sch-last", el("span", `${p.on_text} · ${p.by}`)),
    cell("", "sch-btns", cancel));
}

/** Tab "Schedules": the schedules and the approved installations waiting for their time. */
export function renderSchedules() {
  const s = ui.state, list = s.schedules || [], planned = s.devices.filter(d => d.planned);
  const cards = [];
  if (!list.length && !planned.length) {
    cards.push(box("div", "card", box("div", "card-head", el("h2", t("Schedules · {n}", {n: 0}))),
      el("div", t("No schedules yet – “Add schedule” restarts Shellys or switches their cloud or Bluetooth at fixed times. Updates are scheduled in the installation dialog (“Install at”)."), "empty")));
  } else {
    cards.push(box("div", "card schedules", box("div", "card-head", el("h2", t("Schedules · {n}", {n: list.length}))),
      ...(list.length ? [head("", [[t("On"), "sch-on"], [t("Name"), "sch-name"], [t("Action"), "sch-action"], [t("Repeat"), "sch-when"], [t("Devices"), "sch-dev"],
        [t("Next run"), "sch-next"], [t("Last run"), "sch-last"], ["", "sch-btns"]]), ...list.map(scheduleRow)]
        : [el("div", t("No schedules yet – “Add schedule” restarts Shellys or switches their cloud or Bluetooth at fixed times."), "empty")])));
    cards.push(box("div", "card schedules", box("div", "card-head", el("h2", t("Scheduled Installations · {n}", {n: planned.length})),
      el("span", t("Approved in the installation dialog with “Install at”"), "sub")),
      ...(planned.length ? [head("sch-inst", [[t("Device"), "sch-name"], [t("Version"), "sch-ver"], [t("Install at"), "sch-next"], [t("Approved"), "sch-last"], ["", "sch-btns"]]),
        ...planned.map(plannedRow)] : [el("div", t("No scheduled installation – choose “Install at” in the installation dialog."), "empty")])));
  }
  $("sch-cards").replaceChildren(...cards);
}

/** Save a schedule (new: no id). */
const saveSchedule = (id, body) => api(id ? `api/schedules/${encodeURIComponent(id)}` : "api/schedules", body);
const radio = (name, text, checked, ...extra) => {
  const r = el("input"); r.type = "radio"; r.name = name; r.checked = checked;
  return box("label", "check", r, " ", el("span", text), ...extra);
};
/** Dialog "Add schedule" / "Edit schedule". */
export function openSchedule(s) {
  const edit = !!s;
  s = s || {on: true, name: "", action: "restart", repeat: {kind: "daily"}, time: "03:00", devices: "all", pause: PAUSE_DEFAULT};
  const name = el("input"); name.type = "text"; name.value = s.name; name.maxLength = NAME_MAX; name.placeholder = t("e.g. Weekly restart");
  const action = el("select");
  for (const [key, label] of Object.entries(ACTIONS)) { const o = el("option", label); o.value = key; o.selected = key === s.action; action.append(o); }
  const time = el("input"); time.type = "time"; time.step = 60; time.value = s.time;
  const pause = el("input"); pause.type = "number"; pause.min = 0; pause.max = PAUSE_MAX; pause.value = s.pause ?? PAUSE_DEFAULT;
  const field = (label, control, hint) => {
    const lab = el("label", label); control.id = "sch-" + Math.random().toString(36).slice(2); lab.htmlFor = control.id;
    return box("div", "sch-field", lab, box("div", null, control, hint ? el("div", hint, "desc") : null));
  };
  const pauseField = field(t("Pause between devices (seconds)"), pause, t("Restarts the devices one after the other; 0 = all at once. Default 3"));
  // Repeat
  const daily = radio("sch-rep", t("Daily"), s.repeat.kind === "daily");
  const weekdays = radio("sch-rep", t("On weekdays"), s.repeat.kind === "weekdays");
  const dayBoxes = DAYS.map((d, i) => { const c = el("input"); c.type = "checkbox"; c.checked = s.repeat.kind === "weekdays" && s.repeat.days.includes(i); return c; });
  const days = box("div", "sch-days", ...dayBoxes.map((c, i) => box("label", "check", c, " " + DAYS[i])));
  const n = el("input"); n.type = "number"; n.min = 1; n.max = EVERY_MAX; n.value = (s.repeat.kind === "every" && s.repeat.n) || 14; n.className = "sch-n";
  n.setAttribute("aria-label", t("Number of days"));
  const every = radio("sch-rep", t("Every"), s.repeat.kind === "every", " ", n, " ", el("span", t("days")));
  // Devices: all or a list (removed devices cannot be chosen)
  const chosen = new Set(Array.isArray(s.devices) ? s.devices : []);
  const devices = ui.state.devices.filter(d => !d.removed || chosen.has(d.id)).sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const picks = devices.map(d => { const c = el("input"); c.type = "checkbox"; c.checked = chosen.has(d.id); c.value = d.id; return [d, c]; });
  const tagsOf = d => [d.ignored ? el("span", t("Ignored"), "tag warn") : null, d.battery ? el("span", t("Battery"), "tag") : null,
    d.removed ? el("span", t("Removed"), "tag warn") : null,
    isBle(action.value) && (d.gen === 1 || d.ble === "unsupported") ? el("span", t("No Bluetooth"), "tag") : null,
    isCloud(action.value) && d.cloud === "unsupported" ? el("span", t("No cloud"), "tag") : null];
  const list = box("div", "sch-pick");
  const all = radio("sch-dev", t("All Shellys (new ones are included automatically)"), s.devices === "all");
  const some = radio("sch-dev", t("Selected devices"), s.devices !== "all");
  const someCount = el("span", "", "sub");
  some.append(" ", someCount);
  const proxyBox = el("div", "", "warnbox");
  const leftHint = el("p", "", "hint");
  const on = el("input"); on.type = "checkbox"; on.checked = s.on;
  const sync = () => {
    const kind = action.value;
    pauseField.hidden = kind !== "restart";
    days.hidden = !weekdays.querySelector("input").checked;
    const listed = !all.querySelector("input").checked;
    list.hidden = !listed;
    list.replaceChildren(...picks.map(([d, c]) => box("label", "check", c, " ", el("span", d.name), ...tagsOf(d))));
    const ticked = picks.filter(([, c]) => c.checked);
    someCount.textContent = listed ? `(${ticked.length})` : "";
    // Bluetooth off on a Bluetooth proxy of Home Assistant: HA switches it on again
    const targets = listed ? ticked.map(([d]) => d) : devices.filter(d => !d.removed);
    const proxies = kind === "ble_off" ? targets.filter(d => d.ble_proxy).map(d => d.name) : [];
    proxyBox.textContent = proxies.length ? t("{names}: Home Assistant uses this Shelly as Bluetooth proxy and turns Bluetooth on again.", {names: proxies.join(", ")}) : "";
    proxyBox.hidden = !proxies.length;
    leftHint.textContent = (isBle(kind) ? t("Not included in a run: ignored and removed devices, battery devices (usually asleep), devices without a Bluetooth switch (Gen1).")
      : isCloud(kind) ? t("Not included in a run: ignored and removed devices, battery devices (usually asleep), devices without a cloud switch.")
        : t("Not included in a run: ignored and removed devices, battery devices (usually asleep).")) + " " +
      t("Every run is written to the log; Home Assistant notifies only about errors. A run the app misses is skipped.");
  };
  for (const node of [action, daily, weekdays, every, all, some, list]) node.addEventListener("change", sync);
  sync();
  const content = [
    box("div", "sch-form", field(t("Name"), name), field(t("Action"), action), pauseField, field(t("Time"), time, t("Local time of Home Assistant"))),
    section(t("Repeat"), box("div", "modes", daily, weekdays, days, every)),
    section(t("Devices"), box("div", "modes", all, some, list)),
    proxyBox, leftHint,
    box("label", "check", on, " " + t("Schedule is on")),
  ];
  // An error (e.g. no name) is shown in the dialog, which stays open with all entries
  const error = el("p", null, "bad"); error.hidden = true; error.setAttribute("role", "alert"); content.push(error);
  const title = edit ? t("Edit schedule") : t("Add schedule");
  modal(title, content, [{text: t("Cancel")}, {text: t("Save"), kind: "primary", action: async () => {
    const kind = [daily, weekdays, every].findIndex(r => r.querySelector("input").checked);
    const repeat = kind === 1 ? {kind: "weekdays", days: dayBoxes.flatMap((c, i) => c.checked ? [i] : [])}
      : kind === 2 ? {kind: "every", n: Number(n.value)} : {kind: "daily"};
    // The pause counts only for a restart; another action keeps a valid default
    const seconds = pause.value === "" ? NaN : Number(pause.value);
    const body = {name: name.value.trim(), on: on.checked, action: action.value, time: time.value, repeat,
      pause: action.value === "restart" || Number.isInteger(seconds) ? seconds : PAUSE_DEFAULT,
      devices: all.querySelector("input").checked ? "all" : picks.filter(([, c]) => c.checked).map(([d]) => d.id)};
    try { await saveSchedule(edit ? s.id : null, body); closeModal(); }
    catch (e) {
      for (const b of $("dlg-btns").children) b.disabled = false;
      error.textContent = e.message; error.hidden = false; error.scrollIntoView({block: "nearest"});
    }
  }}], "schedules");
  name.focus();
}
function deleteSchedule(s) {
  const title = t("Delete schedule?");
  modal(title, [el("p", t("“{name}” ({action}, {repeat}) is deleted. Its log entries stay.", {name: s.name, action: ACTIONS[s.action] || s.action, repeat: repeatText(s)}))],
    [{text: t("Cancel")}, {text: t("Delete"), kind: "danger", action: async () => {
      try { await api(`api/schedules/${encodeURIComponent(s.id)}/delete`, {confirm: true}); closeModal(); }
      catch (e) { message(title, e.message, "error", "delete"); }
    }}], "delete");
}
/** "Run now": the schedule runs at once on its devices, after a confirmation. */
function runNow(s) {
  const title = t("Run schedule now?");
  modal(title, [el("p", t("“{name}” runs now: {action} on {devices}.", {name: s.name, action: ACTIONS[s.action] || s.action, devices: devicesText(s)})),
    el("p", t("The next scheduled run stays as it is. The result is written to the log and shown under “Last run”."), "hint")],
  [{text: t("Cancel")}, {text: t("Run now"), kind: "primary", action: async () => {
    try { await api(`api/schedules/${encodeURIComponent(s.id)}/run`, {confirm: true}); closeModal(); toast(t("“{name}” is running", {name: s.name})); }
    catch (e) { message(title, e.message, "error", "schedules"); }
  }}], "schedules");
}
const add = $("sch-add"); add.prepend(icon("plus")); add.onclick = () => openSchedule(null);
