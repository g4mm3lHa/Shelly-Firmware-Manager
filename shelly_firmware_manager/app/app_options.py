"""App options (Settings tab): validation, defaults and the settings file.

The Settings tab is the only place for the options: they are stored by the app in
/data/settings.json (data_paths.SETTINGS_FILE) and applied by a restart via
POST /addons/self/restart (open to every app with its own SUPERVISOR_TOKEN, no
"hassio_api" permission). The Configuration tab of the app page in Home Assistant
holds only a hint to the Settings tab (option "settings" of config.yaml, no effect).
Options kept by the Supervisor from older versions are taken over once (main.py).
"""

import ipaddress
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import data_paths
from shelly_net import is_lan_address
from tls import is_shelly_host, parse_fingerprint

log = logging.getLogger("sfm.options")

INT_RANGES = {
    "check_interval_days": (1, 30),
    "max_parallel_installs": (1, 20),
    "log_retention_days": (1, 365),
    "log_entry_limit": (100, 3000),
    "import_max_mb": (1, 2048),
    "offer_after_days": (0, 60),
    "backup_keep": (1, 50),
}
BOOLS = (
    "restrict_downloads_to_devices",
    "gen1_updates_enabled",
    "allow_downgrade",
    "beta_firmware",
    "resolve_host_names",
    "keep_installed",
    "backup_before_install",
)
# Options with a fixed list of values; the first value is the default
CHOICES = {
    # Base colour of the UI (Settings tab, card Display): "default" keeps the primary colour
    # of the Home Assistant theme with neutral surfaces, a colour replaces the primary colour
    "base_color": ("default", "blue", "teal", "green", "violet", "orange"),
}
# Options that apply without a restart of the app: the page reads them on every load. A save
# that changes only these stores them and keeps the app running.
NO_RESTART = frozenset({"base_color"})
DEFAULTS = {
    "check_interval_days": 7,
    "check_time": "02:45",
    "gen1_updates_enabled": True,
    "resolve_host_names": False,
    "log_retention_days": 90,
    "log_entry_limit": 1000,
    "import_max_mb": 100,
    "keep_installed": True,
    "offer_after_days": 0,
    "backup_before_install": True,
    "backup_keep": 10,
    "restrict_downloads_to_devices": True,
    "max_parallel_installs": 5,
    **{key: values[0] for key, values in CHOICES.items()},
    # Pins for the Shelly servers with the in-house Allterco CA (see DOCS.md, Certificate pinning)
    "pinned_certificates": [
        {
            "host": "updates.shelly.cloud",
            "sha256": "6B:B3:78:67:78:EB:2A:7E:E1:D5:D2:75:CE:CC:01:D5:3D:DC:17:45:E8:00:2A:94:00:46:8F:F5:77:EF:EA:C0",
        },
        {
            "host": "fwcdn.shelly.cloud",
            "sha256": "BA:A5:BA:08:4E:6B:B4:03:2E:AE:16:C0:79:75:9B:BD:D1:40:54:F7:A4:ED:82:A0:32:CE:1C:10:28:2D:1B:A4",
        },
    ],
}
# All option keys of the Settings tab. Anything else in a stored or taken-over option set
# is ignored everywhere, so an unknown (e.g. removed) key can never reach the UI, the log
# or the settings file – e.g. the removed list of allowed Gen1 types (gen1_allowed_types)
# of older versions or the hint field "settings" of the Configuration tab.
KEYS = frozenset({*INT_RANGES, *BOOLS, *CHOICES, *DEFAULTS, "download_base_url", "pinned_certificates"})
# Former default/example of download_base_url (release candidates of 3.0.0). The option
# is optional without default; this value only counts after an explicit confirmation.
LEGACY_DOWNLOAD_BASE = "http://192.168.178.50:8098"
OPTIONAL = ("download_base_url",)  # options that can be removed (empty input)
# allow_downgrade and beta_firmware have no default either: missing = off (also for
# existing installations)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_LIST = 100
# Host written as numbers only, in any form an address parser may accept (decimal, octal or
# hex parts, e.g. 2130706433 or 0x7f.1): it is an IP address in disguise, never a host name
NUMERIC_HOST_RE = re.compile(r"(0x[0-9a-f]*|[0-9]+)(\.(0x[0-9a-f]*|[0-9]+))*\.?", re.IGNORECASE)
# A host name label: ASCII letters, digits and hyphens (international names only as punycode)
HOST_LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
# Reasons a download base URL is refused (shown in the Settings tab, the log and the notice)
DOWNLOAD_BASE_FORMAT = "download_base_url: http(s)://host:port expected"
DOWNLOAD_BASE_PARTS = "download_base_url: http(s)://host:port expected, without user name, query or fragment"
DOWNLOAD_BASE_NOT_PRIVATE = "download_base_url: the IP address is not in a private network"
# Warnings about the settings file at the start (shown in the status strip of the UI for that run)
SETTINGS_DAMAGED = (
    "The settings file was unreadable (kept as settings.damaged) – the defaults apply. "
    "Check and save the settings in the Settings tab."
)
SETTINGS_INVALID = (
    "Invalid values in the settings file – the defaults apply for: {keys}. "
    "Check and save the settings in the Settings tab."
)


def download_host_problem(host: str) -> str | None:
    """Why a host of the download base URL is refused, else None (allow-list).

    Allowed: an IP address inside shelly_net.LAN_NETWORKS, or an ASCII host name (labels of
    letters, digits and hyphens, at most one trailing dot). Everything else is refused:
    a public or local IP address, an IP address in disguise (numbers-only forms such as
    2130706433 or 0x7f.1), IPv6 zone ids and any other character – the Shellys fetch the
    firmware from this host, and a parser on the device may read odd text as an address."""
    if ":" in host:  # IPv6 literal (urlsplit has removed the brackets)
        if "%" in host:  # zone id: meaningless for a Shelly
            return DOWNLOAD_BASE_FORMAT
        try:
            return None if is_lan_address(ipaddress.IPv6Address(host)) else DOWNLOAD_BASE_NOT_PRIVATE
        except ValueError:  # includes a zone id (fe80::1%eth0)
            return DOWNLOAD_BASE_FORMAT
    try:
        return None if is_lan_address(ipaddress.IPv4Address(host)) else DOWNLOAD_BASE_NOT_PRIVATE
    except ValueError:
        pass
    if NUMERIC_HOST_RE.fullmatch(host):
        return DOWNLOAD_BASE_NOT_PRIVATE
    labels = host.removesuffix(".").split(".")
    if len(host) > 253 or not all(HOST_LABEL_RE.fullmatch(label) for label in labels):
        return DOWNLOAD_BASE_FORMAT
    return None


def download_base_problem(url: str) -> str | None:
    """Why a stored download base URL is not allowed (message), else None.

    The address rules only (printable ASCII without spaces, host allow-list); options
    saved by an older version can hold anything the Supervisor accepted."""
    if not url.isascii() or not url.isprintable() or " " in url:
        return DOWNLOAD_BASE_FORMAT
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return DOWNLOAD_BASE_FORMAT
    return download_host_problem(host) if host else DOWNLOAD_BASE_FORMAT


def with_defaults(options: dict[str, Any]) -> dict[str, Any]:
    """Known options of a stored option set completed with defaults for missing keys.

    Unknown or removed keys are dropped, so a legacy secret can never reach the
    UI, the log or a save request."""
    return {**DEFAULTS, **{k: v for k, v in options.items() if k in KEYS}}


def public_view(options: dict[str, Any]) -> dict[str, Any]:
    """Options for the UI (there are no secret options)."""
    return with_defaults(options)


def needs_restart(changed: list[str]) -> bool:
    """True if a save of these changed keys must restart the app (any key outside NO_RESTART,
    including "host_port"); an empty list needs none."""
    return any(key not in NO_RESTART for key in changed)


def _validate(key: str, value: Any) -> Any:
    """Validated value of one option; raises ValueError with a readable message."""
    if key in INT_RANGES:
        low, high = INT_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key}: whole number between {low} and {high} expected")
        return value
    if key in BOOLS:
        if not isinstance(value, bool):
            raise ValueError(f"{key}: true or false expected")
        return value
    if key in CHOICES:
        if not isinstance(value, str) or value not in CHOICES[key]:
            raise ValueError(f"{key}: one of {', '.join(CHOICES[key])} expected")
        return value
    if key == "download_base_url":
        # Printable ASCII only, no spaces: nothing the Shelly or a log line could misread
        text = value if isinstance(value, str) else ""
        problem = download_base_problem(text)
        if len(text) > 200 or problem == DOWNLOAD_BASE_FORMAT:
            raise ValueError(DOWNLOAD_BASE_FORMAT)
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https"):
            raise ValueError(DOWNLOAD_BASE_FORMAT)
        try:
            _ = parts.port  # raises ValueError for an invalid port
        except ValueError:
            raise ValueError(DOWNLOAD_BASE_FORMAT) from None
        # The file name is appended to this address and the address is shown in the UI
        # and the log: nothing may follow the path, and it must not carry credentials
        if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError(DOWNLOAD_BASE_PARTS)
        if problem:
            raise ValueError(problem)
        return value
    if key == "pinned_certificates":
        if not isinstance(value, list) or len(value) > MAX_LIST:
            raise ValueError("pinned_certificates: list expected")
        clean = []
        for entry in value:
            if not isinstance(entry, dict):
                raise ValueError("pinned_certificates: host and sha256 expected")
            host, sha = str(entry.get("host", "")).strip().lower(), str(entry.get("sha256", "")).strip()
            if not is_shelly_host(host):
                raise ValueError(f"pinned_certificates: only *.shelly.cloud allowed ({host[:60]!r})")
            parse_fingerprint(sha)  # raises ValueError
            clean.append({"host": host, "sha256": sha})
        return clean
    if key == "check_time":
        if not isinstance(value, str) or not TIME_RE.fullmatch(value):
            raise ValueError("check_time: time HH:MM (00:00–23:59) expected")
        return value
    raise ValueError(f"Unknown option: {str(key)[:40]}")


def merge(options: dict[str, Any], changes: object) -> tuple[dict[str, Any], list[str]]:
    """New complete option set from the current options and the requested changes.

    Returns (new options, list of changed keys); absent keys keep their value. An
    optional option given as "" (or null) is removed from the set (unset)."""
    if not isinstance(changes, dict):
        raise ValueError("JSON object expected")
    new = with_defaults(options)
    changed = []
    for key, value in changes.items():
        if key in OPTIONAL and value in ("", None):
            if key in new:
                del new[key]
                changed.append(key)
            continue
        clean = _validate(key, value)
        if new.get(key) != clean:
            new[key] = clean
            changed.append(key)
    return new, sorted(changed)


def change_list(old: dict[str, Any], new: dict[str, Any], changed: list[str]) -> list[dict[str, str]]:
    """Activity-log view of changed options: [{"option", "old", "new"}].

    There are no secret options; historic entries of the removed shared password
    ({"option", "redacted": True}) stay readable in the UI."""
    return [{"option": key, "old": _display(old.get(key)), "new": _display(new.get(key))} for key in changed]


def _display(value: Any) -> str:
    """Short readable form of an option value for the log (max. 200 characters)."""
    if isinstance(value, bool):
        text = "on" if value else "off"
    elif isinstance(value, list):
        text = ", ".join(
            f"{v.get('host')} ({str(v.get('sha256'))[:11]}…)" if isinstance(v, dict) else str(v)
            for v in value
        )
    else:
        text = "" if value is None else str(value)
    return text[:200] or "–"


def take_over(options: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Option set to store from options kept by the Supervisor (older versions).

    Known keys only; every value is validated as on saving, an invalid one is left out
    (its default applies) and named in the returned list. A download base URL is kept as
    long as it is text: one that the current rules refuse is shown with its notice in the
    app and corrected there, as before."""
    clean: dict[str, Any] = {}
    dropped = []
    for key, value in options.items():
        if key not in KEYS or value is None:
            continue
        if key == "download_base_url":
            if isinstance(value, str) and 0 < len(value) <= 200:
                clean[key] = value
            else:
                dropped.append(key)
            continue
        try:
            clean[key] = _validate(key, value)
        except ValueError:
            dropped.append(key)
    return with_defaults(clean), sorted(dropped)


def read_settings() -> tuple[dict[str, Any] | None, str | None]:
    """The stored options (settings.json) and a warning for the UI (None if the file is fine);
    options None while the file does not exist.

    Every value is checked as at the take-over (take_over): an invalid one (e.g. changed by
    hand) is left out, its default applies and the warning names it, until the Settings tab
    is saved. A file that is no JSON object is kept as settings.damaged and None is returned,
    so the app starts with the defaults instead of failing at every start (logged as error,
    warning for this run)."""
    path = data_paths.SETTINGS_FILE
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as err:
        data = err
    if isinstance(data, dict):
        options, dropped = take_over(data)
        if not dropped:
            return options, None
        log.warning("%s: invalid values left out, defaults apply: %s", path.name, ", ".join(dropped))
        return options, SETTINGS_INVALID.format(keys=", ".join(dropped))
    reason = data if isinstance(data, Exception) else "no object"
    log.error("%s is damaged (%s) – kept as settings.damaged, defaults apply", path.name, reason)
    try:
        path.replace(path.with_suffix(".damaged"))
    except OSError as err:
        log.error("%s could not be moved aside: %s", path.name, err)
    return None, SETTINGS_DAMAGED


def write_settings(options: dict[str, Any]) -> None:
    """Store the complete option set (settings.json, fsync before the rename);
    raises OSError."""
    content = json.dumps(with_defaults(options), indent=1).encode()
    data_paths.write_atomic(data_paths.SETTINGS_FILE, content, durable=True)
