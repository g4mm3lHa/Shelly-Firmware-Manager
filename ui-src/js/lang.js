// Choice of the UI language, shared by the loader (ui-src/start.js) and i18n.js: "Same as
// Home Assistant" (default) follows the language of the Home Assistant page around this
// panel, else the browser's; anything without a dictionary falls back to English.

export const STORE_KEY = "shelly-firmware-manager:language";
/** Choices of the language field: [value, label]; labels of the languages in their own language. */
export const LANGUAGES = [["auto", "Same as Home Assistant"], ["en", "English"], ["de", "Deutsch"],
  ["nl", "Nederlands"], ["fr", "Français"], ["it", "Italiano"], ["es", "Español"], ["pl", "Polski"], ["pt", "Português"]];
// Languages with a dictionary (static/i18n-<code>.js)
const DICTIONARY_CODES = LANGUAGES.map(([code]) => code).filter(code => code !== "auto" && code !== "en");

/** Language code of the Home Assistant page (Ingress iframe on the same origin), else
 *  of the browser; "" if neither is readable. */
function hostLanguage() {
  let code = "";
  try { if (window.parent !== window) code = window.parent.document.documentElement.lang || ""; } catch (e) { code = ""; }
  return (code || navigator.language || "").toLowerCase().split("-")[0];
}
/** Stored choice of this browser: "auto", "en" or a language code of LANGUAGES. */
export function languageChoice() {
  let value = null;
  try { value = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { value = null; }
  return LANGUAGES.some(([code]) => code === value) ? value : "auto";
}
/** Language of a choice: the chosen one, for "auto" the host's – if a dictionary exists, else "en". */
export function resolve(choice) {
  const code = choice === "auto" ? hostLanguage() : choice;
  return DICTIONARY_CODES.includes(code) ? code : "en";
}
