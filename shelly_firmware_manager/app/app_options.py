"""App options (Settings tab): validation and saving via the Supervisor.

The options are saved with POST /addons/self/options and applied by a restart via
POST /addons/self/restart. Both endpoints are open to every app with its own
SUPERVISOR_TOKEN – no "hassio_api" permission is required. The Supervisor
validates the options against config.yaml again; the checks here mirror that
schema to give clear error messages.
"""

import re
from urllib.parse import urlsplit

from tls import is_shelly_host, parse_fingerprint

INT_RANGES = {
    "check_interval_days": (1, 30),
    "max_parallel_installs": (1, 20),
    "log_retention_days": (1, 365),
    "log_entry_limit": (100, 3000),
}
BOOLS = ("restrict_downloads_to_devices", "gen1_updates_enabled")
DEFAULTS = {
    "check_interval_days": 7,
    "check_time": "02:45",
    "gen1_updates_enabled": True,
    "log_retention_days": 90,
    "log_entry_limit": 1000,
}
# All option keys of config.yaml. Anything else in options.json is ignored everywhere,
# so an unknown (e.g. removed) key can never reach the UI, the log or a save request.
KEYS = frozenset({*INT_RANGES, *BOOLS, *DEFAULTS, "download_base_url", "gen1_allowed_types", "pinned_certificates"})
# Former default/example of download_base_url (up to rc5). Since rc6 the option is
# optional without default; this value only counts after an explicit confirmation.
LEGACY_DOWNLOAD_BASE = "http://192.168.178.50:8098"
OPTIONAL = ("download_base_url",)  # options that can be removed (empty input)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
GEN1_TYPE_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")
MAX_LIST = 100


def with_defaults(options):
    """Known options from /data/options.json completed with defaults for new keys.

    Unknown or removed keys are dropped, so a legacy secret can never reach the
    UI, the log or a save request."""
    return {**DEFAULTS, **{k: v for k, v in options.items() if k in KEYS}}


def public_view(options):
    """Options for the UI (v3 has no secret options any more)."""
    return with_defaults(options)


def _validate(key, value):
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
        parts = urlsplit(value) if isinstance(value, str) else None
        if not parts or parts.scheme not in ("http", "https") or not parts.hostname or len(value) > 200:
            raise ValueError("download_base_url: http(s)://host:port expected")
        _ = parts.port  # raises ValueError for an invalid port
        return value
    if key == "gen1_allowed_types":
        if not isinstance(value, list) or len(value) > MAX_LIST:
            raise ValueError("gen1_allowed_types: list expected")
        if not all(isinstance(t, str) and GEN1_TYPE_RE.match(t) for t in value):
            raise ValueError("gen1_allowed_types: letters, digits and '-' only (max. 32)")
        return sorted(set(value))
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
        if not isinstance(value, str) or not TIME_RE.match(value):
            raise ValueError("check_time: time HH:MM (00:00–23:59) expected")
        return value
    raise ValueError(f"Unknown option: {str(key)[:40]}")


def merge(options, changes):
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


def change_list(old, new, changed):
    """Activity-log view of changed options: [{"option", "old", "new"}].

    v3 has no secret options; historic entries of the removed shared password
    ({"option", "redacted": True}) stay readable in the UI."""
    return [{"option": key, "old": _display(old.get(key)), "new": _display(new.get(key))} for key in changed]


def _display(value):
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
