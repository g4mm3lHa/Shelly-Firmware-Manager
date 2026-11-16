// Dialogs for the settings of a single Shelly: name, NTP, power-on action, eco mode, switches, password.
import {$, api, box, closeModal, devUrl, el, message, modal, valueList} from "./core.js";

// ---------------------------------------------------------------- Device settings
// Flow: read live values from the Shelly -> input -> (Shelly name: confirmation
// old -> new) -> send. NTP and AOP are saved directly; the dialog only stays open
// for errors, "nothing changed" or a restart notice.
const FIELDS = {name: {label: "Shelly name", prop: "shelly_name"}, ntp: {label: "NTP server", prop: "ntp"}};
const AOP_LABELS = {off: "Off", on: "On", restore_last: "Restore last state", last: "Restore last state",
  match_input: "Match input", switch: "Match input", toggle: "Toggle"};
export const aopLabel = v => AOP_LABELS[v] || v || "–";

/** Read the live settings, then continue with next(live); errors are shown. */
async function withLive(d, title, next) {
  modal(title, [el("p", "Reading current values from the Shelly …", "hint")], [{text: "Cancel"}]);
  try { next(await api(`${devUrl(d)}/settings/refresh`, {})); }
  catch (e) { message(title, e.message, "error"); }
}
/** Send changes; quiet = close the dialog on success unless a restart is required. */
async function saveSettings(d, title, changes, quiet) {
  try {
    const r = await api(`${devUrl(d)}/settings`, changes);
    if (r.verified === false) {   // written, but the read-back failed: state unknown
      message(title, "Sent to the Shelly, but the new value could not be read back – please check the device.", "warnbox"); return;
    }
    if (quiet && !r.restart_required) { closeModal(); return; }
    message(title, r.restart_required ? "Saved. The Shelly reports that a restart is required to apply the change." : "Saved and read back from the Shelly.");
  } catch (e) { message(title, e.message, "error"); }
}
/** Edit the Shelly name (with old -> new confirmation) or the NTP server. */
export function editValue(d, field) {
  const {label, prop} = FIELDS[field], title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    const input = el("input"); input.type = "text"; input.value = live[prop] || ""; input.maxLength = field === "name" ? 64 : 260;
    const old = live[prop] || "";
    modal(title, [box("label", "field", label, input)], [{text: "Cancel"}, {text: field === "name" ? "Next" : "Save", kind: "primary", action: () => {
      const value = input.value.trim();
      if (value === old) { message(title, "Nothing changed.", "error"); return; }
      if (field !== "name") { saveSettings(d, title, {[field]: value}, true); return; }
      modal(title, [el("p", `Write this change to ${d.name}?`), valueList([["Current", old || "–"], ["New", value || "–"]])],
        [{text: "Cancel"}, {text: "Apply", kind: "danger", action: () => saveSettings(d, title, {name: value}, false)}]);
    }}]);
    input.focus();
  });
}
/** Power-on action of all outputs. */
export function editAop(d) {
  const title = `Power-on action – ${d.name}`;
  withLive(d, title, live => {
    if (!live.outputs.length) { message(title, "This device has no outputs with a power-on action."); return; }
    const t = el("table"), h = el("tr"), selects = [];
    for (const c of ["Output", "Action on power on"]) h.append(el("th", c));
    t.append(box("thead", null, h));
    for (const o of live.outputs) {
      const sel = el("select"); sel.setAttribute("aria-label", o.name);
      for (const v of o.options) { const opt = el("option", aopLabel(v)); opt.value = v; opt.selected = v === o.aop; sel.append(opt); }
      selects.push([o, sel]);
      t.append(box("tr", null, el("td", o.name === o.key ? o.key : `${o.name} (${o.key})`), box("td", null, sel)));
    }
    modal(title, [t], [{text: "Cancel"}, {text: "Save", kind: "primary", action: () => {
      const changed = selects.filter(([o, sel]) => sel.value !== o.aop);
      if (!changed.length) { message(title, "Nothing changed.", "error"); return; }
      saveSettings(d, title, {aop: Object.fromEntries(changed.map(([o, sel]) => [o.key, sel.value]))}, true);
    }}]);
  });
}
/** Eco mode: one confirmation. */
export function editEco(d) {
  const title = `Eco mode – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live.eco !== "boolean") { message(title, "This device does not report an eco mode."); return; }
    const target = !live.eco, word = target ? "on" : "off";
    modal(title, [el("p", `Eco mode is currently ${live.eco ? "on" : "off"}. Turn it ${word} for ${d.name}?`),
      el("p", "The Shelly may restart to apply the change.", "hint")],
    [{text: "Cancel"}, {text: `Turn ${word}`, kind: "primary", action: () => saveSettings(d, title, {eco: target}, true)}]);
  });
}
/** Access point / Bluetooth: only the on/off switch, no further configuration. */
const SWITCHES = {ap: "Access point", ble: "Bluetooth"};
export function editSwitch(d, key) {
  const label = SWITCHES[key], title = `${label} – ${d.name}`;
  withLive(d, title, live => {
    if (typeof live[key] !== "boolean") { message(title, `This device does not offer a ${label.toLowerCase()} switch.`); return; }
    const target = !live[key], word = target ? "on" : "off";
    modal(title, [el("p", `${label} is currently ${live[key] ? "on" : "off"}. Turn it ${word} for ${d.name}?`),
      el("p", key === "ap" ? "Only the switch is changed; all other Wi-Fi settings stay as they are." : "Only the global Bluetooth switch is changed.", "hint")],
    [{text: "Cancel"}, {text: `Turn ${word}`, kind: "primary", action: () => saveSettings(d, title, {[key]: target}, true)}]);
  });
}
/** Restart: one confirmation (not logged). */
export function restartDevice(d) {
  const title = `Restart – ${d.name}`;
  modal(title, [el("p", `Restart ${d.name} now?`), el("p", "The device is unavailable for a few seconds.", "hint")], [{text: "Cancel"},
    {text: "Restart", kind: "danger", action: async () => {
      try { await api(`${devUrl(d)}/restart`, {}); message(title, "Restart command sent."); }
      catch (e) { message(title, e.message, "error"); }
    }}]);
}
// Device password. Storing existing access credentials (this app only) is clearly
// separated from changing the protection on the device. The password is
// write-only: the server never returns it; every mode is confirmed here and
// checked again by the server.
const PASSWORD_WARNING = "Keep the password safe. If it is lost, access to the device may be locked. Recovery may require a factory reset; settings may be lost.";
const PASSWORD_MODES = {
  store: {label: "Store existing password", hint: "The device is already protected: test the password and store it in this app. Nothing is changed on the device.", input: true},
  enable: {label: "Enable protection", hint: "Sets a password on the device (user “admin”).", input: true, repeat: true, risky: true},
  change: {label: "Change password", hint: "Replaces the password on the device. The stored password is kept until the new one is verified.", input: true, repeat: true, risky: true},
  remove: {label: "Remove protection", hint: "Removes the password from the device. The stored password is deleted only after the device confirms."},
  forget: {label: "Forget stored password", hint: "Deletes the password stored in this app only. The device stays as it is."},
};
const PASSWORD_DONE = {store: "Password tested and stored.", enable: "Password protection enabled and verified.",
  change: "Password changed and verified.", remove: "Password protection removed.", forget: "Stored password deleted from this app."};
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
  const title = `Device password – ${d.name}`, modes = passwordModes(d);
  const prot = d.auth_enabled === true ? "protected" : d.auth_enabled === false ? "not protected" : "unknown";
  const pw = el("input"), pw2 = el("input");
  for (const i of [pw, pw2]) { i.type = "password"; i.autocomplete = "new-password"; i.maxLength = d.gen === 1 ? 50 : 128; }
  const pwField = box("label", "field", "Password (user “admin”)", pw), pw2Field = box("label", "field", "Repeat password", pw2);
  const hint = el("p", null, "hint"), radios = el("div", null, "modes");
  const update = () => {
    const m = PASSWORD_MODES[radios.querySelector("input:checked").value];
    hint.textContent = m.hint; pwField.hidden = !m.input; pw2Field.hidden = !m.repeat;
  };
  modes.forEach((mode, i) => {
    const r = el("input"); r.type = "radio"; r.name = "pw-mode"; r.value = mode; r.checked = i === 0; r.onchange = update;
    radios.append(box("label", "check", r, " " + PASSWORD_MODES[mode].label));
  });
  modal(title, [
    valueList([["Device protection", prot], ["Stored in this app", d.password_set ? "yes" : "no"]]),
    radios, hint, pwField, pw2Field,
    el("p", d.gen === 1 ? "Gen1: the password is stored in plain text in the app's private storage (needed for Basic Auth); 1–50 characters." :
      "Gen2+: only a SHA-256 hash bound to this device is stored.", "hint"),
    el("div", PASSWORD_WARNING, "warnbox")],
  [{text: "Cancel"}, {text: "Continue", kind: "primary", action: () => {
    const mode = radios.querySelector("input:checked").value, m = PASSWORD_MODES[mode], password = pw.value;
    if (m.input && !password) { message(title, "No password entered.", "error"); return; }
    if (m.repeat && password !== pw2.value) { message(title, "The passwords do not match.", "error"); return; }
    passwordConfirm(d, title, mode, m.input ? password : null);
  }}]);
  update();
}
/** Second step: explicit confirmation; enable/change additionally need the
 *  "stored securely" acknowledgment before the red button becomes active. */
function passwordConfirm(d, title, mode, password) {
  const m = PASSWORD_MODES[mode], content = [el("p", `${m.label} for ${d.name}?`), el("div", PASSWORD_WARNING, "warnbox")];
  const ack = el("input"); ack.type = "checkbox";
  if (m.risky) content.push(box("label", "check", ack, " I have stored the password securely"));
  const send = async () => {
    try {
      await api(`${devUrl(d)}/password`, {mode, password, confirm: true, acknowledged: m.risky ? ack.checked : false});
      message(title, PASSWORD_DONE[mode]);
    } catch (e) { message(title, e.message, "error"); }
  };
  modal(title, content, [{text: "Cancel"}, {text: mode === "store" ? "Test & save" : m.label, kind: mode === "forget" || mode === "store" ? "primary" : "danger", action: send}]);
  if (m.risky) {
    const go = $("dlg-btns").lastChild; go.disabled = true; ack.onchange = () => { go.disabled = !ack.checked; };
  }
}
