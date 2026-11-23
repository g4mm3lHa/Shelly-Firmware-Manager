// Language of the UI: English texts are the keys, other languages are dictionaries
// keyed by the English text (i18n-de.js). The language is chosen per browser in the
// Settings tab (card "Display"): "Same as Home Assistant" (default) follows the language
// of the Home Assistant page around this panel, else the browser's; anything without a
// dictionary falls back to English. A change reloads the page.
// Texts from the server or a Shelly (messages, log entries) stay English; dates and
// times keep the form "2026-11-23 03:00" in every language.
import DE from "./i18n-de.js";

const DICTIONARIES = {de: DE};
const STORE_KEY = "shelly-firmware-manager:language";
/** Choices of the language field: [value, label]; labels of the languages in their own language. */
export const LANGUAGES = [["auto", "Same as Home Assistant"], ["en", "English"], ["de", "Deutsch"]];

/** Language code of the Home Assistant page (Ingress iframe on the same origin), else
 *  of the browser; "" if neither is readable. */
function hostLanguage() {
  let code = "";
  try { if (window.parent !== window) code = window.parent.document.documentElement.lang || ""; } catch (e) { code = ""; }
  return (code || navigator.language || "").toLowerCase().split("-")[0];
}
/** Stored choice of this browser: "auto", "en" or "de". */
export function languageChoice() {
  let value = null;
  try { value = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { value = null; }
  return LANGUAGES.some(([code]) => code === value) ? value : "auto";
}
/** Store the choice and reload the page in the new language. */
export function setLanguage(value) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(value)); } catch (e) { /* page only */ }
  location.reload();
}
/** Language the page uses now: the chosen one, for "auto" the host's if a dictionary exists. */
function resolve(choice) {
  const code = choice === "auto" ? hostLanguage() : choice;
  return DICTIONARIES[code] ? code : "en";
}
export const lang = resolve(languageChoice());
/** Language of "Same as Home Assistant" for its label ("Deutsch", "English"). */
export const autoLanguage = resolve("auto");
const dict = DICTIONARIES[lang] || null;
document.documentElement.lang = lang;

/** Translation of an English UI text; {name} placeholders are filled from vars (values
 *  stay as they are). Unknown texts are shown in English. */
export function t(text, vars) {
  let out = (dict && dict[text]) || text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, key) => (key in vars ? String(vars[key]) : m));
  return out;
}
/** Translate the static page (index.html): text nodes and the attributes placeholder,
 *  aria-label and data-tip whose whole text is a known English text. */
function translatePage(root = document.body) {
  if (!dict) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.nodeValue.trim();
    if (text && dict[text]) n.nodeValue = n.nodeValue.replace(text, dict[text]);
  }
  for (const node of root.querySelectorAll("[placeholder],[aria-label],[data-tip]")) {
    for (const attr of ["placeholder", "aria-label", "data-tip"]) {
      const value = node.getAttribute(attr);
      if (value && dict[value]) node.setAttribute(attr, dict[value]);
    }
  }
}
translatePage();
