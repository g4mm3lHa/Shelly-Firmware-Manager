"""App options (Settings tab): validation and saving via the Supervisor.

The options are saved with POST /addons/self/options and applied by a restart via
POST /addons/self/restart. Both endpoints are open to every app with its own
SUPERVISOR_TOKEN – no "hassio_api" permission is required. The Supervisor
validates the options against config.yaml again; the checks here mirror that
schema to give clear error messages.
"""

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from shelly import is_lan_address
from tls import is_shelly_host, parse_fingerprint

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
}
# All option keys of config.yaml. Anything else in options.json is ignored everywhere,
# so an unknown (e.g. removed) key can never reach the UI, the log or a save request –
# e.g. the removed list of allowed Gen1 types (gen1_allowed_types) of older versions.
KEYS = frozenset({*INT_RANGES, *BOOLS, *DEFAULTS, "download_base_url", "pinned_certificates"})
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


def download_host_problem(host: str) -> str | None:
    """Why a host of the download base URL is refused, else None (allow-list).

    Allowed: an IP address inside shelly.LAN_NETWORKS, or an ASCII host name (labels of
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
    """Known options from /data/options.json completed with defaults for new keys.

    Unknown or removed keys are dropped, so a legacy secret can never reach the
    UI, the log or a save request."""
    return {**DEFAULTS, **{k: v for k, v in options.items() if k in KEYS}}


def public_view(options: dict[str, Any]) -> dict[str, Any]:
    """Options for the UI (there are no secret options)."""
    return with_defaults(options)


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
