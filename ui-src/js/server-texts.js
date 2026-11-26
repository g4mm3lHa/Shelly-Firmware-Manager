// Translation of texts the server sends in English: status messages of the devices (the
// pill tooltip and the device details), install errors, the reasons of the download base
// URL, the results of the connection test and the error texts of API answers. Each text of SERVER_TEXTS is the English
// original as the server builds it, a value of its own as a {placeholder}; a dictionary
// entry keyed by it translates every text of this form. A placeholder of NESTED holds a
// text of the server itself and is translated the same way. Any other text (e.g. an
// error of the network library) is shown as it came. The activity log stays English.
import {t} from "./i18n.js";

// Keep in sync with the server (tests/test_i18n.py checks the fixed texts).
const SERVER_TEXTS = [
  // Checks and status of a device
  "Battery device asleep – wake it up (e.g. press its button) and check it again. Last attempt: {error}",
  "Gen1 updates are disabled in the settings",
  "No stable offer",
  "Provided, not installed",
  "Provided from the firmware store, not installed",
  "Provided from the firmware store (uploaded file), not installed",
  "Provided from the firmware store (imported file), not installed",
  "{offer} – intermediate version on the way to {version}; the device is checked again after its installation",
  "{offer} – intermediate version; the device is checked again after its installation",
  "{offer} – beta version",
  "Known offer installed",
  "Stored Gen1 firmware is now treated as imported – check the device again",
  "Provided firmware deleted – check the device again",
  "Beta version withdrawn – beta firmware is switched off in the settings",
  "{error}. Stored version {version} is ready and can be installed manually – at your own risk: without the answer of the Shelly update server it cannot be verified whether an intermediate version is required first.",
  "{error}. Stored version {version} (imported file, not confirmed by Shelly) is ready and can be installed manually – at your own risk: without the answer of the Shelly update server it cannot be verified whether an intermediate version is required first.",
  "{error}. Stored version {version} (imported file, not confirmed by Shelly) is ready and can be installed manually – at your own risk.",
  "Unexpected error ({error}) – see the app log",
  "The device is busy – please try again afterwards",
  "The device is no longer in the device registry of Home Assistant",
  "Approval does not match the provided update (anymore)",
  "Not before the first-start question is answered",
  "Not before the first-start question is answered: import a firmware store or skip it",
  "Not possible now – {error}",
  "Firmware cannot be deleted during a check",
  // Installation
  "Backing up the settings before the installation",
  "Approved, waiting for a free installation slot",
  "Installation started",
  "Downgrade started",
  "Restarting the device before the installation",
  "Update accepted, waiting for restart",
  "The Shelly started the update",
  "The Shelly is downloading the firmware: {progress} %",
  "Update applied, the Shelly restarts",
  "The Shelly restarts",
  "Firmware transferred, waiting for restart",
  "Waiting for the Shelly to download the firmware",
  "Updated",
  "Downgraded to {version}",
  "Downgraded to {version} – a newer version is provided again at the next check",
  "App was restarted during the installation",
  "Target version not confirmed. {diagnosis}",
  "The Shelly reports {version} instead of the target version {target}",
  "The Shelly downloaded the firmware but still reports {version} – check the device log or the Shelly's web interface and try again with enabled websocket debugging",
  "Download rejected: request came from {ips}, not from the device IP {ip}. Disable the option restrict_downloads_to_devices",
  "The Shelly never requested the firmware – is {url} reachable from the Shelly (IP of Home Assistant, port from the Network section)?",
  "Device or firmware changed since the check – please check again",
  "The device did not answer within 2 minutes after its restart – nothing was installed",
  "Firmware file was modified",
  "The Shelly reports an update error: {error}. It downloaded the firmware but rejected it – the installed version may be too old for a direct update to {version}: install an intermediate version first (web interface of the Shelly or upload in the Firmware tab)",
  "The Shelly reports an update error: {error}. Check that {url} is reachable from the Shelly (LAN IP address of Home Assistant, port 8098)",
  "The update stopped at {progress} %: no progress from the Shelly for {seconds} s",
  "The update stopped: no progress from the Shelly for {seconds} s",
  "The Shelly applied the update but did not restart within {seconds} s",
  "After the restart the Shelly still reports {version} – the update was not applied",
  "After the restart the Shelly reports {version} instead of {target}",
  "No answer from the Shelly within {seconds} s after the restart",
  "Connection to the Shelly lost before the update command – no answer in {attempts} attempts within {seconds} s. Nothing was installed.",
  "Connection to the Shelly lost before the update command – no answer in {attempts} attempts within {seconds} s. Nothing was installed. Wi-Fi signal: {rssi} dBm (weak).",
  "The Shelly restarted unexpectedly before the update command (running for {seconds} s). Nothing was installed.",
  "A connection test is already running",
  "This device cannot run the test now (Gen 2 or newer, checked, not busy)",
  "No Shelly of Gen 2 or newer is known yet – run a check first",
  // Results of the connection test of the download server
  "No connection to {url}: timeout after {seconds} s (Shelly code {codes})",
  "No connection to {url}: connection error (Shelly code {codes})",
  "The device could not fetch {url} (Shelly code {codes})",
  "The request reached the download server from {ips}, not from the device address {ip} – switch off the option “Serve downloads only to known Shellys” or check the network (NAT)",
  "Another web server answered at {url} (HTTP {status}) – check the IP address and the port",
  "The device gave no answer code",
  "Device not reachable: {error}",
  // Download base URL
  "Installations are disabled: set the download base URL (LAN IP address of Home Assistant and port 8098) in the Settings tab.",
  "Installations are disabled: the download base URL is still the former example value {url}. Change it in the Settings tab or confirm that it really is the address of Home Assistant.",
  "Installations are disabled: the IP address of the download base URL is not in a private network (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7). Correct it in the Settings tab.",
  "Installations are disabled: the download base URL is not a valid address (http(s)://host:port, letters, digits, dots and hyphens in the host). Correct it in the Settings tab.",
  // Answers of a Shelly and its address
  "Device not reachable ({error})",
  "The Shelly rejected the password",
  "The Shelly requires a password – store it in the device's password dialog",
  "Stored device password belongs to another device ID – enter it again",
  "Authentication with the Shelly failed",
  "Unexpected authentication request from the Shelly",
  "Incomplete authentication request from the Shelly",
  "Shelly rejected the request (HTTP {status})",
  "Invalid response from the device",
  "Response from the device too large",
  "Invalid authentication request from the Shelly",
  "The Shelly answered the WebSocket request with a redirect",
  "The device reported no usable Wi-Fi signal",
  "Unknown version format: {value}",
  "Invalid value for {key} in the answer",
  "Device reports: {error}",
  "Not supported by this device ({method})",
  "Incomplete device information from the Shelly",
  "No device information (HTTP {status})",
  "Invalid device address",
  "Device ID unknown – run a check first",
  "No usable local device address in the device registry",
  "Host name {host} could not be resolved ({error})",
  "Host name {host} could not be resolved",
  "Device address {address} is not in a private network",
];

// Placeholders that hold a text of the server (translated as well)
const NESTED = new Set(["offer", "error", "diagnosis"]);
// Deepest nesting followed (e.g. a beta offer that is an intermediate version)
const MAX_DEPTH = 3;

/** [regular expression, English text, placeholder names] of every text with a placeholder. */
const PATTERNS = SERVER_TEXTS.filter(text => text.includes("{")).map(text => {
  const names = [...text.matchAll(/\{(\w+)\}/g)].map(m => m[1]);
  const source = text.split(/\{\w+\}/).map(part => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("(.+?)");
  return [new RegExp(`^${source}$`, "s"), text, names];
});

/** The text in the page language if it is a known text of the server, else unchanged. */
export function serverText(text, depth = 0) {
  if (!text || typeof text !== "string") return text;
  const direct = t(text);
  if (direct !== text || depth >= MAX_DEPTH) return direct;
  for (const [pattern, english, names] of PATTERNS) {
    const m = pattern.exec(text);
    if (!m) continue;
    const vars = Object.fromEntries(names.map((name, i) => [name, NESTED.has(name) ? serverText(m[i + 1], depth + 1) : m[i + 1]]));
    return t(english, vars);
  }
  return text;
}
