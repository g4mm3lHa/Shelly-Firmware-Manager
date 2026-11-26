// Language of the UI: English texts are the keys, other languages are dictionaries
// keyed by the English text (i18n-<code>.js). The language is chosen per browser in the
// Settings tab (card "Display", see lang.js); a change reloads the page. The loader
// (ui-src/start.js) loads only the dictionary of the page language, before this script:
// it sets SFM_LANG (the code) and the dictionary file SFM_TEXTS; without a loaded
// dictionary the page is English.
// Status messages and errors of the server are translated by server-texts.js; log
// entries and texts of a Shelly stay English. Dates and times keep the form
// "2026-11-23 03:00" in every language.
import {resolve, STORE_KEY} from "./lang.js";

/** Store the language choice for the next page load (this browser only). */
export function storeLanguage(value) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(value)); } catch (e) { /* page only */ }
}
/** Store the choice and reload the page in the new language. The language is chosen in the
 *  Settings tab, so the page opens it again (marker "#settings" in the address, removed at
 *  the start, see main.js). */
export function setLanguage(value) {
  storeLanguage(value);
  try { history.replaceState(null, "", "#settings"); } catch (e) { /* page opens on Devices */ }
  location.reload();
}
// The dictionary the loader brought (i18n-<code>.js as a classic script: SFM_TEXTS.default)
const loaded = (window.SFM_TEXTS && window.SFM_TEXTS.default) || null;
/** Language the page uses now: the one the loader chose, English without its dictionary. */
export const lang = loaded && window.SFM_LANG ? window.SFM_LANG : "en";
/** Language of "Same as Home Assistant" for its label ("Deutsch", "English"). */
export const autoLanguage = resolve("auto");
const dict = lang === "en" ? null : loaded;
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
