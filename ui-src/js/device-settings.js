// Dialogs for the settings of a single Shelly: name, NTP, power-on action, eco mode, switches,
// websocket debug log, password, restart; passwords of several devices in one step.
import {
  api, box, checkbox, closeModal, confirmDialog, devUrl, el, field, gateButton, icon, input, message, messageBody,
  modal, radio, section, t, tn, ui, valueList, webUrl,
} from "./core.js";

// ---------------------------------------------------------------- Device settings
// Flow: read live values from the Shelly -> input -> (Shelly name: confirmation
// old -> new) -> send. NTP and AOP are saved directly; the dialog only stays open
// for errors, "nothing changed" or a restart notice.
const FIELDS = {name: {label: t("Shelly name"), prop: "shelly_name"}, ntp: {label: t("NTP server"), prop: "ntp"}};
// Gen1 values (last, switch) carry the names of the Gen1 web interface
const AOP_LABELS = {off: t("Off"), on: t("On"), restore_last: t("Restore last state"), last: t("Restore last mode"),
  match_input: t("Match input"), switch: t("Switch"), toggle: t("Toggle")};
export const aopLabel = v => AOP_LABELS[v] || v || "–";
const AOP_RESTORE = ["restore_last", "last"];
/** Colour of the power-on action: green if every output restores its last state. */
export const aopTone = outputs => outputs.every(o => AOP_RESTORE.includes(o.aop)) ? "good" : "alert";
/** Current power-on action of all outputs as one short text ("Mixed" if they differ). */
export function aopSummary(outputs) {
  const values = [...new Set(outputs.map(o => aopLabel(o.aop)))];
  return values.length === 1 ? values[0] : t("Mixed");
}

/** Status strip under the title of the popups opened by an action icon: the icon in a
 *  tinted circle (colour of the action column), the feature name and the state pill.
 *  tone: good | alert | warn | off | unknown | neutral; pill: state text or null. */
export function featureStrip(iconName, label, tone, pill) {
  const circle = box("span", "fs-icon", icon(iconName));
  return box("div", `fstrip t-${tone}`, circle, el("span", label, "fs-name"), pill ? el("span", pill, "pill fs-pill") : null);
}
/** Strip of an on/off switch; onTone = colour while it is on (action column colours). */
const switchStrip = (iconName, label, on, onTone) => featureStrip(iconName, label, on ? onTone : "off", on ? t("On") : t("Off"));

/** Read the live settings, then continue with next(live); errors are shown. */
async function withLive(d, title, next) {
  modal(title, [el("p", t("Reading current values from the Shelly …"), "hint")], [{text: t("Cancel")}]);
  try { next(await api(`${devUrl(d)}/settings/refresh`, {})); }
  catch (e) { message(title, e.message, "error"); }
}
/** Send changes; quiet = close the dialog on success unless a restart is required. */
async function saveSettings(d, title, changes, quiet) {
  try {
    const r = await api(`${devUrl(d)}/settings`, changes);
    if (r.verified === false) {   // written, but the read-back failed: state unknown
      message(title, t("Sent to the Shelly, but the new value could not be read back – please check the device."), "warning"); return;
    }
    if (r.not_applied?.length) {  // accepted, but the read-back shows another value
      message(title, t("The Shelly accepted the request but did not apply: {settings}. The value read back is shown.", {settings: r.not_applied.join(", ")}), "warning"); return;
    }
    if (quiet && !r.restart_required) { closeModal(); return; }
    message(title, r.restart_required ? t("Saved. The Shelly reports that a restart is required to apply the change.") : t("Saved and read back from the Shelly."), "success");
  } catch (e) { message(title, e.message, "error"); }
}
/** Edit the Shelly name (with old -> new confirmation) or the NTP server. */
export function editValue(d, key) {
  const {label, prop} = FIELDS[key], title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    const value = input("text", {value: live[prop] || "", maxLength: key === "name" ? 64 : 260});
    const old = live[prop] || "";
    modal(title, [field(label, value)], [{text: t("Cancel")}, {text: key === "name" ? t("Continue") : t("Save"), kind: "primary", action: () => {
      const typed = value.value.trim();
      if (typed === old) { message(title, t("Nothing changed.")); return; }
      if (key !== "name") { saveSettings(d, title, {[key]: typed}, true); return; }
      modal(title, [el("p", t("Write this change to {name}?", {name: d.name})), valueList([[t("Current"), old || "–"], [t("New"), typed || "–"]])],
        [{text: t("Cancel")}, {text: t("Apply"), kind: "danger", action: () => saveSettings(d, title, {name: typed}, false)}]);
    }}]);
    value.focus();
  });
}
/** Power-on action of all outputs. */
export function editAop(d) {
  const title = `${t("Power-on action")} – ${d.name}`;
  withLive(d, title, live => {
    if (!live.outputs.length) { message(title, t("This device has no outputs with a power-on action.")); return; }
    const table = el("table"), h = el("tr"), selects = [];
    for (const c of [t("Output"), t("Action on power on")]) h.append(el("th", c));
    table.append(box("thead", null, h));
    for (const o of live.outputs) {
      const sel = el("select"); sel.setAttribute("aria-label", o.name);
      for (const v of o.options) { const opt = el("option", aopLabel(v)); opt.value = v; opt.selected = v === o.aop; sel.append(opt); }
      selects.push([o, sel]);
      table.append(box("tr", null, el("td", o.name === o.key ? o.key : `${o.name} (${o.key})`), box("td", null, sel)));
    }
    const strip = featureStrip("aop", t("Power-on action"), aopTone(live.outputs), aopSummary(live.outputs));
    modal(title, [strip, table], [{text: t("Cancel")}, {text: t("Save"), kind: "primary", action: () => {
      const changed = selects.filter(([o, sel]) => sel.value !== o.aop);
      if (!changed.length) { message(title, t("Nothing changed.")); return; }
      saveSettings(d, title, {aop: Object.fromEntries(changed.map(([o, sel]) => [o.key, sel.value]))}, true);
    }}]);
  });
}
/** Eco mode: one confirmation; the button names the action ("Turn on" / "Turn off"). */
export function editEco(d) {
  const title = `${t("Eco mode")} – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live.eco !== "boolean") { message(title, t("This device does not report an eco mode.")); return; }
    const target = !live.eco, word = target ? t("Turn on") : t("Turn off");
    modal(title, [switchStrip("eco", t("Eco mode"), live.eco, "good"),
      el("p", t("The Shelly may restart to apply the change."), "hint")],
    [{text: t("Cancel")}, {text: word, kind: "primary", action: () => saveSettings(d, title, {eco: target}, true)}]);
  });
}
/** Access point / Bluetooth: only the on/off switch, no further configuration.
 *  [label, colour while on] – an active access point is shown red like in the action column. */
const SWITCHES = {ap: [t("Access point"), "alert"], ble: [t("Bluetooth"), "good"]};
// Bluetooth while Home Assistant runs its proxy script on the Shelly: Home Assistant turns
// Bluetooth on again unless its scanner mode is disabled in the Shelly integration
const BLE_PROXY = t("Home Assistant uses this Shelly as a Bluetooth proxy (its script “aioshelly_ble_integration” is running).") + " " +
  t("If you turn Bluetooth off here, Home Assistant turns it on again. To switch it off for good, first set “Bluetooth scanner mode” to “Disabled” in the options of this device in the Shelly integration.");
const CLOUD_TEXT = t("Only the cloud switch is changed. With the cloud off, the Shelly app and other Shelly cloud services no longer reach this device from outside your network. Home Assistant and this app keep working locally.");
/** Shelly cloud: state strip (yellow while on), the connection, one confirmation. */
export function editCloud(d) {
  const title = `${t("Cloud")} – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live.cloud !== "boolean") { message(title, t("This device does not offer a cloud switch.")); return; }
    const target = !live.cloud, word = target ? t("Turn on") : t("Turn off");
    modal(title, [switchStrip("cloud", t("Cloud"), live.cloud, "warn"),
      valueList([[t("Connection"), live.cloud ? (live.cloud_connected ? t("Connected to the Shelly cloud") : t("Not connected at the moment")) : null]]),
      el("p", CLOUD_TEXT, "hint")],
    [{text: t("Cancel")}, {text: word, kind: "primary", action: () => saveSettings(d, title, {cloud: target}, true)}]);
  });
}
export function editSwitch(d, key) {
  const [label, onTone] = SWITCHES[key], title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live[key] !== "boolean") { message(title, key === "ap" ? t("This device does not offer an access point switch.") : t("This device does not offer a Bluetooth switch.")); return; }
    const target = !live[key], word = target ? t("Turn on") : t("Turn off");
    modal(title, [switchStrip(key, label, live[key], onTone),
      el("p", key === "ap" ? t("Only the switch is changed; all other Wi-Fi settings stay as they are.") : t("Only the global Bluetooth switch is changed."), "hint"),
      key === "ble" && live.ble && live.ble_proxy ? el("div", BLE_PROXY, "warnbox") : null].filter(Boolean),
    [{text: t("Cancel")}, {text: word, kind: "primary", action: () => saveSettings(d, title, {[key]: target}, true)}]);
  });
}
// Websocket debug log (Gen2+): the device streams its debug log over a websocket,
// shown live on the Diagnostics page of its web UI. Below firmware 1.0.0 the stream
// is not protected by the device password (Shelly API documentation).
const DEBUG_TEXT = t("The Shelly streams its debug log over a websocket; the Diagnostics page of the Shelly web UI shows it live. Turn it off afterwards: the log may contain network and configuration details, and streaming uses device resources.");
const DEBUG_UNPROTECTED = t("Firmware below 1.0.0: the debug stream is not protected by the device password.");
/** Major version below 1 ("0.14.1") – the unprotected debug stream. */
const belowOne = v => /^0\./.test(String(v || ""));
export function editDebug(d) {
  const label = t("Websocket debug log"), title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live.debug_ws !== "boolean") { message(title, t("This device does not offer a websocket debug log.")); return; }
    const target = !live.debug_ws, word = target ? t("Turn on") : t("Turn off"), content = [switchStrip("bug", label, live.debug_ws, "warn"),
      el("p", DEBUG_TEXT, "hint")];
    const url = webUrl(d);
    if (url) {   // Diagnostics page of the device web UI (Gen2+: /#/diagnostics), new tab
      const a = el("a", t("Open Diagnostics in the Shelly web UI ↗")); a.href = url + "#/diagnostics"; a.target = "_blank";
      a.rel = "noopener noreferrer"; content.push(box("p", null, a));
    }
    if (belowOne(d.version)) content.push(el("div", DEBUG_UNPROTECTED, "warnbox"));
    modal(title, content, [{text: t("Cancel")}, {text: word, kind: "primary", action: () => saveSettings(d, title, {debug_ws: target}, true)}]);
  });
}
/** Restart: one confirmation (not logged). */
export function restartDevice(d) {
  const title = `${t("Restart")} – ${d.name}`;
  confirmDialog({title, button: t("Restart"),
    content: [featureStrip("restart", t("Restart"), "neutral", null), el("p", t("The device will be unavailable for a few seconds."), "hint")],
    action: async () => { await api(`${devUrl(d)}/restart`, {}); message(title, t("Restart command sent."), "success"); }});
}
// Device password. Storing existing access credentials (this app only) is clearly
// separated from changing the protection on the device. The password is
// write-only: the server never returns it; every mode is confirmed here and
// checked again by the server.
const PASSWORD_WARNING = t("Keep the password safe. If it is lost, access to the device may be locked. Recovery may require a factory reset; settings may be lost.");
const PASSWORD_MODES = {
  store: {label: t("Store existing password"), hint: t("The device is already protected: test the password and store it in this app. Nothing is changed on the device."), input: true},
  enable: {label: t("Enable protection"), hint: t("Sets a password on the device (user “admin”)."), input: true, repeat: true, risky: true},
  change: {label: t("Change password"), hint: t("Replaces the password on the device. The stored password is kept until the new one is verified."), input: true, repeat: true, risky: true},
  remove: {label: t("Remove protection"), hint: t("Removes the password from the device. The stored password is deleted only after the device confirms.")},
  forget: {label: t("Forget stored password"), hint: t("Deletes the password stored in this app only. The device stays as it is.")},
};
const PASSWORD_DONE = {store: t("Password tested and stored."), enable: t("Password protection enabled and verified."),
  change: t("Password changed and verified."), remove: t("Password protection removed."), forget: t("Stored password deleted from this app.")};
// Reminder after a change on the device: the Home Assistant Shelly integration needs it too
const PASSWORD_HA = {enable: t("Also enter the password for this device in the Home Assistant Shelly integration."),
  change: t("Also change the password for this device in the Home Assistant Shelly integration."),
  remove: t("Also remove the password for this device in the Home Assistant Shelly integration.")};
const HA_AFTER_SET = t("After setting it, also enter the password for this device in the Home Assistant Shelly integration.");
const NOT_STORED = t("Not stored in this app: the manager can no longer access this device (checks, installations, settings) until the password is stored.");
/** Modes that fit the reported protection state (unknown: all device modes). */
function passwordModes(d) {
  const p = d.auth_enabled, modes = [];
  if (p !== false) modes.push("store");
  if (p !== true) modes.push("enable");
  if (p !== false) modes.push("change", "remove");
  if (d.password_set) modes.push("forget");
  return modes;
}
export function editPassword(d) {
  const title = `${t("Device password")} – ${d.name}`, modes = passwordModes(d);
  const [pw, pw2] = [0, 0].map(() => input("password", {autocomplete: "new-password", maxLength: d.gen === 1 ? 50 : 128}));
  const pwField = field(t("Password (user “admin”)"), pw), pw2Field = field(t("Repeat password"), pw2);
  const hint = el("p", null, "hint"), radios = el("div", null, "modes"), storage = el("div");
  // "Store in this app": only for enable/change, directly under the selected mode
  const [keep, keepLabel] = checkbox(t("Store in this app"), true, "sub-check");
  const storedText = d.gen === 1 ? t("Gen1: the password is stored in plain text in the app's private storage (needed for Basic Auth); 1–50 characters.") :
    t("Gen2+: only a SHA-256 hash bound to this device is stored.");
  const update = () => {
    const input = radios.querySelector("input:checked"), mode = input.value, m = PASSWORD_MODES[mode];
    hint.textContent = m.hint; pwField.hidden = !m.input; pw2Field.hidden = !m.repeat;
    const choice = mode === "enable" || mode === "change";
    keepLabel.hidden = !choice;
    if (choice) input.parentElement.after(keepLabel);
    // Storage note: yellow box (Gen2+ hash) or red box (Gen1 plain text, or not stored)
    if (!choice) storage.replaceChildren(el("p", storedText, "hint"));
    else if (keep.checked) storage.replaceChildren(el("div", `${storedText} ${HA_AFTER_SET}`, d.gen === 1 ? "warnbox danger" : "warnbox"));
    else storage.replaceChildren(el("div", `${NOT_STORED} ${HA_AFTER_SET}`, "warnbox danger"));
  };
  keep.onchange = update;
  modes.forEach((mode, i) => {
    const [r, label] = radio("pw-mode", PASSWORD_MODES[mode].label, i === 0, mode); r.onchange = update;
    radios.append(label);
  });
  // Colours of the lock icon: green = protected and stored, yellow = protected without
  // stored password, grey = not protected
  const tone = d.auth_enabled === true ? (d.password_set ? "good" : "warn") : d.auth_enabled === false ? "off" : "unknown";
  modal(title, [
    featureStrip("lock", t("Password protection"), tone, d.auth_enabled === true ? t("Protected") : d.auth_enabled === false ? t("Not protected") : t("Unknown")),
    valueList([[t("Stored in this app"), d.password_set ? t("yes") : t("no")]]),   // the protection state is in the strip
    radios, hint, pwField, pw2Field, storage,
    el("div", PASSWORD_WARNING, "warnbox")],
  [{text: t("Cancel")}, {text: t("Continue"), kind: "primary", action: () => {
    const mode = radios.querySelector("input:checked").value, m = PASSWORD_MODES[mode], password = pw.value;
    if (m.input && !password) { message(title, t("No password entered."), "error"); return; }
    if (m.repeat && password !== pw2.value) { message(title, t("The passwords do not match."), "error"); return; }
    passwordConfirm(d, title, mode, m.input ? password : null, (mode !== "enable" && mode !== "change") || keep.checked);
  }}]);
  update();
}
/** Second step: explicit confirmation; enable/change additionally need the
 *  "stored securely" acknowledgment before the red button becomes active.
 *  keep = store the new password in this app (enable/change). */
function passwordConfirm(d, title, mode, password, keep) {
  const m = PASSWORD_MODES[mode], content = [el("p", t("{action} for {name}?", {action: m.label, name: d.name})), el("div", PASSWORD_WARNING, "warnbox")];
  const [ack, ackLabel] = checkbox(t("I have stored the password securely"));
  if (m.risky) content.push(ackLabel);
  confirmDialog({title, content, button: mode === "store" ? t("Test & store") : m.label, kind: mode === "forget" || mode === "store" ? "primary" : "danger",
    ack: m.risky ? ack : null, action: async () => {
      await api(`${devUrl(d)}/password`, {mode, password, confirm: true, acknowledged: m.risky ? ack.checked : false, keep});
      const done = keep ? PASSWORD_DONE[mode] : `${PASSWORD_DONE[mode]} ${t("It is not stored in this app.")}`;
      modal(title, [messageBody(done, "success"), PASSWORD_HA[mode] ? el("div", PASSWORD_HA[mode], "warnbox") : null].filter(Boolean));
    }});
}

// ---------------------------------------------------------------- Passwords of several devices
// One password for all selected devices; each device is still set, verified, stored and
// logged on its own (same server path as the single-device dialog). Only "store" and
// "set" – removing and forgetting stay in the single-device dialog. "set" enables the
// protection of unprotected devices and changes it on protected ones whose current
// password is stored; other devices are greyed out with the reason.
const BULK_TITLE = t("Device passwords");
const BULK_MODES = [["store", t("Store the existing device password in this app")], ["set", t("Set password (enable protection or change it)")]];
/** [step text, fits] of a device for the action (mirrors the server check). */
function bulkStep(d, mode) {
  if (!d.ip || !d.gen || d.installing || d.status === "installing" || d.activity) return [t("busy or not reachable – try again later"), false];
  if (d.auth_enabled == null) return [t("state unknown – reload device info"), false];
  if (mode === "store") return d.auth_enabled ? [t("test and store"), true] : [t("not protected"), false];
  if (!d.auth_enabled) return [t("enable protection"), true];
  return d.password_set ? [t("change password"), true] : [t("store the current password first"), false];
}
/** First step: action, devices, password (+ repeat), storage choice. */
export function bulkPasswords() {
  const devices = [...((ui.state && ui.state.devices) || [])].sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  const modes = el("div", null, "modes"), list = el("div", null, "bulk-list"), storage = el("div");
  const [pw, pw2] = [0, 0].map(() => input("password", {autocomplete: "new-password", maxLength: 128}));
  const pw2Field = field(t("Repeat password"), pw2);
  const [keep, keepLabel] = checkbox(t("Store in this app"), true);
  let boxes = [];
  const mode = () => modes.querySelector("input:checked").value;
  const chosen = () => boxes.filter(([cb]) => cb.checked).map(([, d]) => d);
  const update = () => {
    const set = mode() === "set", n = chosen().length;
    pw2Field.hidden = !set; keepLabel.hidden = !set;
    const stored = t("Gen2+: only a SHA-256 hash bound to each device is stored. Gen1: plain text in the app's private storage (1–50 characters).");
    const ha = t("After setting it, also enter the password for these devices in the Home Assistant Shelly integration.");
    if (!set) storage.replaceChildren(el("p", stored, "hint"));
    else if (keep.checked) storage.replaceChildren(el("div", `${stored} ${ha}`, "warnbox"));
    else storage.replaceChildren(el("div", `${NOT_STORED} ${ha}`, "warnbox danger"));
    // Step 1 is "Continue" (blue); the red button with the count follows in the confirmation
    gateButton(() => n > 0);
  };
  const fill = () => {
    const m = mode(), fits = devices.filter(d => bulkStep(d, m)[1]);
    const all = input("checkbox", {checked: fits.length > 0, disabled: !fits.length});
    boxes = devices.map(d => {
      const [text, ok] = bulkStep(d, m), cb = input("checkbox", {checked: ok, disabled: !ok});
      cb.onchange = () => { all.checked = boxes.every(([c]) => c.disabled || c.checked); update(); };
      return [cb, d, text, ok];
    });
    all.onchange = () => { for (const [cb] of boxes) if (!cb.disabled) cb.checked = all.checked; update(); };
    const state = d => d.auth_enabled === true ? (d.password_set ? t("protected · stored") : t("protected · not stored")) :
      d.auth_enabled === false ? t("not protected") : t("unknown");
    list.replaceChildren(box("label", "check bulk-all", all, " " + t("All devices that fit ({n} of {total})", {n: fits.length, total: devices.length})),
      ...boxes.map(([cb, d, text, ok]) => box("div", "bulk-row" + (ok ? "" : " off"),
        box("label", "check", cb, box("span", "bulk-dev", el("span", d.name || "?", "name"), el("span", `Gen ${d.gen || "?"} · ${state(d)}`, "sub"))),
        el("span", (ok ? "→ " : "") + text, ok ? "bulk-step" : "sub"))));
    update();
  };
  BULK_MODES.forEach(([k, label]) => {
    const [r, row] = radio("bulk-mode", label, k === "set", k); r.onchange = fill;
    modes.append(row);
  });
  keep.onchange = update;
  modal(BULK_TITLE, [section(t("Action"), modes), section(t("Devices"), devices.length ? list : el("p", t("No devices yet – they are found by the check."), "hint")),
    section(t("Password (every selected device gets it)"), box("div", null, field(t("Password (user “admin”)"), pw), pw2Field)),
    keepLabel, storage, el("div", PASSWORD_WARNING, "warnbox")],
  [{text: t("Cancel")}, {text: t("Continue"), kind: "primary", action: () => {
    const m = mode(), set = m === "set", picked = chosen(), password = pw.value;
    if (!picked.length) { message(BULK_TITLE, t("No device selected."), "error", "lock"); return; }
    if (!password) { message(BULK_TITLE, t("No password entered."), "error", "lock"); return; }
    if (set && password !== pw2.value) { message(BULK_TITLE, t("The passwords do not match."), "error", "lock"); return; }
    if (password.length > 50 && picked.some(d => d.gen === 1)) { message(BULK_TITLE, t("Gen1 devices accept 1–50 characters – choose a shorter password or leave the Gen1 devices out."), "error", "lock"); return; }
    bulkConfirm(m, picked, password, !set || keep.checked);
  }}], "lock");
  fill();
}
/** Second step: explicit confirmation ("set" also needs "stored securely"). */
function bulkConfirm(mode, picked, password, keep) {
  const set = mode === "set", [ack, ackLabel] = checkbox(t("I have stored the password securely"));
  const names = picked.map(d => d.name || "?").join(", ");
  const count = tn(picked.length, "1 device", "{n} devices");
  const content = [el("p", set ? t("Set the password on {count}?", {count}) : t("Test and store the password for {count}?", {count})),
    el("p", names, "hint"), el("div", PASSWORD_WARNING, "warnbox")];
  if (set) content.push(ackLabel);
  confirmDialog({title: BULK_TITLE, icon: "lock", content, button: set ? t("Set on {count}", {count}) : t("Test & store"), kind: set ? "danger" : "primary",
    ack: set ? ack : null, action: async () => {
      modal(BULK_TITLE, [el("p", t("Working on {count} …", {count}), "hint")], [], "lock");
      const r = await api("api/devices/passwords", {mode, devices: picked.map(d => d.id), password, confirm: true, acknowledged: set ? ack.checked : false, keep});
      bulkResult(mode, r.results, keep);
    }});
}
const BULK_ICONS = {ok: ["check", "msg-success"], error: ["close", "msg-error"], skipped: ["alert", "msg-warning"]};
/** Result per device: green check, red X, yellow "!" (skipped). */
function bulkResult(mode, results, keep) {
  const count = k => results.filter(r => r.result === k).length;
  const text = r => r.result === "ok" ? (PASSWORD_DONE[r.action] || t("Done.")) + (keep || r.action === "store" ? "" : " " + t("Not stored in this app.")) :
    r.result === "skipped" ? t("Skipped: {reason}", {reason: r.message}) : t("{error} – nothing changed", {error: r.message});
  const done = count("ok") > 0 && mode === "set";
  modal(`${BULK_TITLE} – ${t("result")}`, [
    el("p", t("{done} done · {failed} failed · {skipped} skipped", {done: count("ok"), failed: count("error"), skipped: count("skipped")}), "hint"),
    box("div", "bulk-res", ...results.map(r => box("div", "bulk-res-row " + BULK_ICONS[r.result][1],
      box("span", "msg-icon sm", icon(BULK_ICONS[r.result][0])), box("span", "bulk-dev", el("span", r.name, "name"), el("span", text(r), "sub"))))),
    done ? el("div", t("Also enter the new password for these devices in the Home Assistant Shelly integration."), "warnbox") : null].filter(Boolean),
  [{text: t("Close"), kind: "primary"}], "lock");
}
