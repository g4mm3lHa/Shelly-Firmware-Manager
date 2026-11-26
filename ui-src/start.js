// Loader of the UI (the only script of index.html): picks the language of the page (see
// js/lang.js), then loads its dictionary (static/i18n-<code>.js, none for English) and the
// UI (static/app.js) – in this order, with the version query of this script (cache busting).
// Only the one dictionary in use is loaded, not all of them.
import {languageChoice, resolve} from "./js/lang.js";

const version = new URL(document.currentScript.src).search;   // "?v=<asset version>"
const code = resolve(languageChoice());
window.SFM_LANG = code;
for (const src of [...(code === "en" ? [] : [`static/i18n-${code}.js`]), "static/app.js"]) {
  const script = document.createElement("script");
  script.src = src + version;
  script.async = false;   // dynamic scripts run in the order they were added
  document.body.append(script);
}
