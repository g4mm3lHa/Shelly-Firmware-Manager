"""Backups of device settings on the device side: read what a backup holds, compare a
backup with the device and write it back.

Gen2+ (RPC): the configuration of all components (Shelly.GetConfig), the scripts with
their code, the schedules and the webhooks of the device. Gen1 (HTTP): /settings and
the actions (/settings/actions, where the device offers them).

Never part of a backup: passwords and keys the device keeps in their own fields (a
Shelly does not give out its Wi-Fi, device and MQTT password; fields named like one,
SECRET_KEYS, are removed from what was read), the key-value store, and the script Home
Assistant maintains for its Bluetooth proxy. Script code, webhook URLs (Gen2+) and
action URLs (Gen1) are kept as they are: a token or a user:password@ written into them
is part of the backup (encrypted) and of the list of differences of a restore.

A backup is restored in parts (PARTS): each chosen part is made equal to the backup –
what differs is written, and scripts, schedules and webhooks that exist only on the
device are removed. The network settings are a part of their own that is never written
unless it is named. Afterwards the device is read again: a part counts as applied only
as far as the device then reports the values of the backup.

compare, digest and summary are pure; everything else talks to one device (Target)
with the limits and the error handling of the shelly module.
"""

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp
import shelly
from shelly import BLE_PROXY_SCRIPT, AuthError, DeviceAuth, ShellyError, UnsupportedError, clip, error_text

PART_SETTINGS = "settings"
PART_SCRIPTS = "scripts"
PART_SCHEDULES = "schedules"
PART_WEBHOOKS = "webhooks"
PART_ACTIONS = "actions"
PART_NETWORK = "network"

# Parts of a backup in the order they are written (the network settings last: after
# them the device may no longer answer at its address)
GEN2_PARTS = (PART_SETTINGS, PART_SCRIPTS, PART_SCHEDULES, PART_WEBHOOKS, PART_NETWORK)
GEN1_PARTS = (PART_SETTINGS, PART_ACTIONS, PART_NETWORK)

# Steps of a restore besides the parts (see restore)
STEP_RESTART = "restart"
STEP_VERIFY = "verify"

# Keys whose values are removed from everything read from a device (never stored, never shown)
SECRET_KEYS = frozenset({"pass", "password", "key", "ha1", "pin_code"})

# Gen2+ components of the part "network"
GEN2_NETWORK = frozenset({"wifi", "eth"})

# Names (SSID) of the Wi-Fi networks a device connects to. A restore that writes another
# name cannot write its password (no part of a backup): the dialog warns about it.
GEN2_WIFI_NAMES = frozenset({("wifi", "sta", "ssid"), ("wifi", "sta1", "ssid")})
GEN1_WIFI_NAMES = frozenset({("wifi_sta", "ssid"), ("wifi_sta1", "ssid")})

# Gen2+ values that are no settings: never compared and never written
GEN2_IGNORED = frozenset({("sys", "cfg_rev"), ("sys", "device", "mac"), ("sys", "device", "fw_id")})

# Gen2+ settings SetConfig cannot write: a difference is named, not restored
GEN2_FIXED = frozenset({("sys", "device", "profile")})

# Spelling of the Gen2+ namespaces whose name is not the capitalised component key
NAMESPACES = {
    "ble": "BLE", "bthome": "BTHome", "bthomedevice": "BTHomeDevice", "bthomesensor": "BTHomeSensor",
    "cct": "CCT", "devicepower": "DevicePower", "em": "EM", "em1": "EM1", "ht_ui": "HT_UI", "knx": "KNX",
    "mqtt": "MQTT", "plugs_ui": "PLUGS_UI", "pm1": "PM1", "rgb": "RGB", "rgbw": "RGBW", "ui": "UI",
    "wd_ui": "WD_UI", "wifi": "WiFi", "ws": "WS",
}  # fmt: skip

# Gen1 /settings: top-level entries that are no settings of a backup (identity, state,
# the access point and the login, which are never restored; the actions are read apart)
GEN1_IGNORED = frozenset(
    {"device", "login", "wifi_ap", "fw", "build_info", "hwinfo", "time", "unixtime", "actions", "pin_code"}
)

# Gen1 /settings: keys that report a state, not a setting (anywhere in the answer) …
GEN1_STATE = frozenset({
    "ison", "has_timer", "timer_started", "timer_duration", "timer_remaining", "overpower", "overtemperature",
    "is_valid", "source", "connected", "current_pos", "last_direction", "calibrating", "positioning",
    "safety_switch", "stop_reason",
})  # fmt: skip

# … and those that do so only inside the named list of channels
GEN1_CHANNEL_STATE = {
    "rollers": frozenset({"state", "power"}),
    "lights": frozenset({"red", "green", "blue", "white", "gain", "temp", "brightness", "effect"}),
}

# Gen1 settings no endpoint writes: a difference is named, not restored
GEN1_FIXED = frozenset({("mode",)})

# Gen1 part "network": the two Wi-Fi client configurations
GEN1_NETWORK = frozenset({"wifi_sta", "wifi_sta1"})

# Gen1 lists of channels in /settings -> name of their endpoint /settings/<name>/<index>
GEN1_CHANNELS = {
    "relays": "relay", "lights": "light", "rollers": "roller", "inputs": "input", "emeters": "emeter",
    "meters": "meter", "thermostats": "thermostat", "ext_temperature": "ext_temperature",
    "ext_humidity": "ext_humidity", "ext_switch": "ext_switch",
}  # fmt: skip

# Gen1 parameters of /settings whose name is not the path joined with "_"
GEN1_PARAMS: dict[tuple[str, ...], str] = {("coiot", "enabled"): "coiot_enable"}

SCRIPT_READS = 512  # most Script.GetCode requests for one script

MAX_SCRIPT = 256 * 1024  # bytes; a script that reports more is refused

PUT_CHUNK = 1024  # bytes of code per Script.PutCode request

SHOW_LIMIT = 120  # characters of a value in the list of differences

RESTART_FIRST_S = 5  # wait before the first request to a device that was told to restart

RESTART_POLL_S = 3  # pause between the requests while it restarts

RESTART_WAIT_S = 120  # longest wait for a restarted device

Row = dict[str, Any]

# What a row of differences shows (the rest of a row is the instruction for writing it)
ROW_FIELDS = ("key", "state", "device", "backup", "device_lines", "backup_lines", "autostart", "wifi_name")


@dataclass(frozen=True)
class Target:
    """One device as the functions here reach it."""

    session: aiohttp.ClientSession
    gen: int
    origin: str
    auth: DeviceAuth | None

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Gen2+ RPC call."""
        return await shelly.device_rpc(self.session, self.origin, method, params, self.auth)

    async def get(self, path: str, params: Sequence[tuple[str, str]] | None = None) -> dict[str, Any]:
        """Gen1 request (settings are written with query parameters)."""
        return await shelly.device_get(self.session, self.origin, path, self.auth, params)


# --------------------------------------------------------------------------------------------- Reading


def _without_secrets(value: Any) -> Any:
    """The value without any entry named like a password or key."""
    if isinstance(value, dict):
        return {k: _without_secrets(v) for k, v in value.items() if k not in SECRET_KEYS}
    if isinstance(value, list):
        return [_without_secrets(v) for v in value]
    return value


def _is_id(value: object) -> bool:
    """True for the ID of a script, schedule or webhook."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


async def _listed(target: Target, method: str, key: str) -> list[dict[str, Any]]:
    """Entries of a Gen2+ list (scripts, schedules, webhooks); empty if the device
    does not offer the component."""
    try:
        answer = await target.rpc(method)
    except UnsupportedError:
        return []
    rows = answer.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


async def _script_code(target: Target, script_id: int) -> str:
    """Code of one script, read in the parts the device delivers."""
    parts: list[str] = []
    offset = 0
    for _ in range(SCRIPT_READS):
        answer = await target.rpc("Script.GetCode", {"id": script_id, "offset": offset})
        data, left = answer.get("data"), answer.get("left")
        if not isinstance(data, str) or not isinstance(left, int) or isinstance(left, bool):
            raise ShellyError("Invalid response from the device (script code)")
        if left > 0:
            # The offset counts bytes: a character cut by the end of this part arrives
            # as a replacement character and is read again, complete, with the next part
            data = data.rstrip("�")
        size = len(data.encode("utf-8", "surrogatepass"))
        parts.append(data)
        offset += size
        if left <= 0:
            return "".join(parts)
        if not size or offset > MAX_SCRIPT:
            break
    raise ShellyError("The code of a script could not be read completely")


async def read(target: Target) -> dict[str, Any]:
    """Everything a backup of this device holds (see the module text), without
    secrets. Gen2+: {"config", "scripts", "schedules", "webhooks"}; Gen1: {"settings",
    "actions"} – actions None if the device does not let them be read. Raises like
    every device request (ShellyError, TimeoutError, aiohttp.ClientError)."""
    if target.gen >= 2:
        config = _without_secrets(await target.rpc("Shelly.GetConfig"))
        if not config:
            raise ShellyError("The device reported no settings")
        scripts = []
        for row in await _listed(target, "Script.List", "scripts"):
            if row.get("name") == BLE_PROXY_SCRIPT or not _is_id(row.get("id")):
                continue  # the Bluetooth proxy script belongs to Home Assistant, which writes it again itself
            scripts.append(
                {
                    "id": row["id"],
                    "name": str(row.get("name") or ""),
                    "enable": row.get("enable") is True,
                    "running": row.get("running") is True,
                    "code": await _script_code(target, row["id"]),
                }
            )
        return {
            "config": config,
            "scripts": scripts,
            "schedules": await _listed(target, "Schedule.List", "jobs"),
            "webhooks": await _listed(target, "Webhook.List", "hooks"),
        }
    settings = _without_secrets(await target.get("/settings"))
    actions: dict[str, Any] | None
    try:
        answer = await target.get("/settings/actions")
        actions = answer["actions"] if isinstance(answer.get("actions"), dict) else None
    except AuthError:
        raise
    except ShellyError:  # also: the device has no such endpoint
        actions = None
    return {"settings": settings, "actions": actions}


# ------------------------------------------------------------------------------------------- Comparing


def _gen1_settings(value: Any, path: tuple[str, ...] = ()) -> Any:
    """Gen1 /settings reduced to the settings a backup compares (see GEN1_*)."""
    if isinstance(value, dict):
        state = GEN1_STATE | GEN1_CHANNEL_STATE.get(path[0] if path else "", frozenset())
        return {
            k: _gen1_settings(v, (*path, str(k)))
            for k, v in value.items()
            if k not in state and not (not path and k in GEN1_IGNORED)
        }
    if isinstance(value, list):
        return [_gen1_settings(v, (*path, str(i))) for i, v in enumerate(value)]
    return value


def _gen1_actions(actions: object) -> dict[tuple[str, int], dict[str, Any]]:
    """Gen1 actions by (name, index): {"enabled", "urls"}."""
    found: dict[tuple[str, int], dict[str, Any]] = {}
    for name, entries in actions.items() if isinstance(actions, dict) else ():
        for entry in entries if isinstance(entries, list) else ():
            if isinstance(entry, dict) and _is_id(entry.get("index")):
                urls = entry.get("urls")
                found[(str(name), entry["index"])] = {
                    "enabled": entry.get("enabled") is True,
                    "urls": [str(u) for u in urls if u] if isinstance(urls, list) else [],
                }
    return found


def _script_content(script: dict[str, Any]) -> tuple[str, bool, str]:
    """What makes two scripts the same: name, start at boot and code."""
    return str(script.get("name") or ""), script.get("enable") is True, str(script.get("code") or "")


def _comparable(gen: int, data: dict[str, Any]) -> Any:
    """The content of a backup without what changes by itself (revision counters,
    firmware ID, states): two backups with the same result hold the same settings."""
    if gen >= 2:
        config = json.loads(json.dumps(data.get("config") or {}))
        for path in GEN2_IGNORED:
            node = config
            for key in path[:-1]:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, dict):
                node.pop(path[-1], None)
        return [
            config,
            [[s.get("id"), *_script_content(s)] for s in data.get("scripts") or []],
            data.get("schedules") or [],
            data.get("webhooks") or [],
        ]
    actions = data.get("actions")
    listed = sorted((name, index, a["enabled"], a["urls"]) for (name, index), a in _gen1_actions(actions).items())
    return [_gen1_settings(data.get("settings") or {}), None if actions is None else listed]


def digest(gen: int, data: dict[str, Any]) -> str:
    """SHA-256 over the settings of a backup (see _comparable): equal for two backups
    between which nothing was changed on the device."""
    text = json.dumps(_comparable(gen, data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def summary(gen: int, data: dict[str, Any]) -> dict[str, Any]:
    """What a backup contains, for the lists: Gen2+ {"settings", "scripts",
    "schedules", "webhooks"}, Gen1 {"settings", "actions"} plus "partial" if the
    actions could not be read."""
    if gen >= 2:
        return {"settings": True, **{k: len(data.get(k) or []) for k in (PART_SCRIPTS, PART_SCHEDULES, PART_WEBHOOKS)}}
    parts: dict[str, Any] = {
        "settings": True,
        "actions": sum(1 for a in _gen1_actions(data.get("actions")).values() if a["urls"]),
    }
    if data.get("actions") is None:
        parts["partial"] = True
    return parts


def _leaves(value: Any, into_lists: bool, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """(path, value) of every setting below value. A list is one value – except
    (into_lists, Gen1) a list of objects, which is walked by index."""
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from _leaves(item, into_lists, (*path, str(key)))
    elif into_lists and isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for index, item in enumerate(value):
            yield from _leaves(item, into_lists, (*path, str(index)))
    else:
        yield path, value


def _dig(value: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    """(known, value) of a setting on the device. A setting below an empty value
    (null where the backup holds an object) is known and empty."""
    for key in path:
        if value is None:
            return True, None
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return False, None
    return True, value


def _show(value: object) -> str:
    """A value as the list of differences shows it."""
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return clip(value, SHOW_LIMIT)
    return clip(json.dumps(value, separators=(", ", ": "), ensure_ascii=False), SHOW_LIMIT)


def _holder(path: tuple[str, ...], gen: int) -> tuple[str, ...]:
    """Path of the object that holds a setting: the component for Gen2+ ("ble" for
    ble.observer.enable, so a removed sub-object counts as well); for Gen1 the channel
    entry ("relays", "0") of a channel list, else the top-level object, or () – /settings
    itself – for a top-level value."""
    if gen < 2 and len(path) >= 3 and path[0] in GEN1_CHANNELS and path[1].isdigit():
        return path[:2]
    return path[:1] if len(path) >= 2 else ()


def _label(path: tuple[str, ...], gen: int) -> str:
    """Name of a setting with Shelly's own keys: "switch:0 · initial_state" (Gen2+:
    component, then the path inside it), "relays.0 · default_state" (Gen1)."""
    if len(path) < 2:
        return ".".join(path)
    if gen >= 2:
        return f"{path[0]} · {'.'.join(path[1:])}"
    return f"{'.'.join(path[:-1])} · {path[-1]}"


def _setting_rows(gen: int, backup: Any, device: Any) -> dict[str, dict[str, list[Row]]]:
    """Differences of the settings, split into the parts "settings" and "network"."""
    parts: dict[str, dict[str, list[Row]]] = {
        part: {"rows": [], "skipped": []} for part in (PART_SETTINGS, PART_NETWORK)
    }
    network, fixed = (GEN2_NETWORK, GEN2_FIXED) if gen >= 2 else (GEN1_NETWORK, GEN1_FIXED)
    wifi_names = GEN2_WIFI_NAMES if gen >= 2 else GEN1_WIFI_NAMES
    for path, wanted in _leaves(backup, gen < 2):
        if gen >= 2 and (
            len(path) < 2 or path in GEN2_IGNORED or path[0].startswith("script:") or path[-1] in ("id", "is_open")
        ):
            continue  # no setting, or part of "scripts"; is_open follows from the password, which is no part
        part = parts[PART_NETWORK if path[0] in network else PART_SETTINGS]
        known, current = _dig(device, path)
        key = _label(path, gen)
        if not known:
            part["skipped"].append({"key": key, "reason": "unknown"})
        elif current != wanted:
            if path in fixed:
                part["skipped"].append({"key": key, "reason": "fixed"})
            else:
                shown = {"device": _show(current), "backup": _show(wanted)}
                row: Row = {"key": key, **shown, "path": path, "value": wanted}
                if path in wifi_names and isinstance(wanted, str) and wanted:
                    row["wifi_name"] = True  # the backup names another Wi-Fi network
                part["rows"].append(row)
    return parts


def _pairs(
    backup: list[dict[str, Any]],
    device: list[dict[str, Any]],
    same: Callable[[dict[str, Any], dict[str, Any]], bool],
    ident: Callable[[dict[str, Any]], object],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Match the entries of a list in the backup with those on the device: first the
    ones with the same content, then the ones with the same identity (they changed).
    Returns (changed pairs (backup, device), only in the backup, only on the device)."""
    left = list(device)
    open_entries: list[dict[str, Any]] = []
    for entry in backup:
        match = next((i for i, d in enumerate(left) if same(entry, d)), None)
        if match is None:
            open_entries.append(entry)
        else:
            left.pop(match)
    changed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    missing: list[dict[str, Any]] = []
    for entry in open_entries:
        match = next((i for i, d in enumerate(left) if ident(d) is not None and ident(d) == ident(entry)), None)
        if match is None:
            missing.append(entry)
        else:
            changed.append((entry, left.pop(match)))
    return changed, missing, left


def _same_entry(entry: dict[str, Any], current: dict[str, Any]) -> bool:
    """True if the device holds a schedule or webhook as the backup does (its ID
    aside; a field the firmware of the backup did not know yet does not count)."""
    return all(current.get(k) == v for k, v in entry.items() if k != "id")


def _lines(script: dict[str, Any]) -> int:
    """Number of lines of a script."""
    code = str(script.get("code") or "")
    return code.count("\n") + 1 if code else 0


def _script_rows(backup: list[dict[str, Any]], device: list[dict[str, Any]]) -> list[Row]:
    """Differences of the scripts, by name."""
    changed, missing, extra = _pairs(
        backup, device, lambda a, b: _script_content(a) == _script_content(b), lambda s: s.get("name")
    )
    rows: list[Row] = [
        {
            "key": entry["name"], "state": "changed", "device_lines": _lines(current),
            "backup_lines": _lines(entry), "autostart": entry.get("enable") is True, "item": entry,
            "id": current["id"], "code": entry.get("code") != current.get("code"),
        }
        for entry, current in changed
    ]  # fmt: skip
    rows += [
        {
            "key": entry["name"], "state": "missing", "backup_lines": _lines(entry),
            "autostart": entry.get("enable") is True, "item": entry, "id": None, "code": True,
        }
        for entry in missing
    ]  # fmt: skip
    rows += [
        {"key": current["name"], "state": "extra", "device_lines": _lines(current), "id": current["id"]}
        for current in extra
    ]
    return rows


def _schedule_text(job: dict[str, Any]) -> str:
    """One schedule of a device in a line: what it calls (and that it is off)."""
    calls = job.get("calls")
    named = [clip(c.get("method"), 40) for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []
    methods = ", ".join(named) or "–"
    return methods if job.get("enable") is not False else f"{methods} (off)"


def _webhook_text(hook: dict[str, Any]) -> str:
    """One webhook in a line: its event and component (and that it is off)."""
    text = clip(hook.get("event"), 60) or "–"
    if _is_id(hook.get("cid")):
        text += f" · {hook['cid']}"
    return text if hook.get("enable") is not False else f"{text} (off)"


def _list_rows(
    backup: list[dict[str, Any]],
    device: list[dict[str, Any]],
    name: Callable[[dict[str, Any]], str],
    text: Callable[[dict[str, Any]], str],
) -> list[Row]:
    """Differences of the schedules or webhooks."""
    changed, missing, extra = _pairs(backup, device, _same_entry, lambda e: e.get("id"))
    rows: list[Row] = [
        {
            "key": name(entry), "state": "changed", "device": text(current), "backup": text(entry),
            "item": entry, "id": current.get("id"),
        }
        for entry, current in changed
    ]  # fmt: skip
    rows += [{"key": name(entry), "state": "missing", "backup": text(entry), "item": entry} for entry in missing]
    rows += [
        {"key": name(current), "state": "extra", "device": text(current), "id": current["id"]}
        for current in extra
        if _is_id(current.get("id"))
    ]
    return rows


def _action_text(action: dict[str, Any]) -> str:
    """One Gen1 action in a line: its addresses (and that it is off)."""
    urls = clip(", ".join(action["urls"]), SHOW_LIMIT) or "–"
    return urls if action["enabled"] or not action["urls"] else f"{urls} (off)"


def _action_rows(backup: object, device: object) -> dict[str, list[Row]]:
    """Differences of the Gen1 actions, by name and index."""
    part: dict[str, list[Row]] = {"rows": [], "skipped": []}
    if backup is None:
        return part
    current = _gen1_actions(device)
    for (name, index), wanted in sorted(_gen1_actions(backup).items()):
        key = f"{name} ({index})"
        if (name, index) not in current:
            if wanted["urls"]:  # nothing to restore otherwise
                part["skipped"].append({"key": key, "reason": "unknown" if device is not None else "unreadable"})
        elif current[(name, index)] != wanted:
            part["rows"].append(
                {
                    "key": key, "state": "changed", "device": _action_text(current[(name, index)]),
                    "backup": _action_text(wanted), "name": name, "index": index, "item": wanted,
                }
            )  # fmt: skip
    return part


def compare(gen: int, backup: dict[str, Any], device: dict[str, Any]) -> dict[str, dict[str, list[Row]]]:
    """What restoring would change, per part of this generation:
    {part: {"rows": [...], "skipped": [{"key", "reason"}]}}.

    A row names one difference (ROW_FIELDS, see public_rows) and carries what writing
    it needs. state: "changed", "missing" (not on the device) or "extra" (only on the
    device: it is removed); the rows of the settings have none. skipped: settings of
    the backup that are not written – "unknown" (the firmware of the device does not
    have them), "fixed" (cannot be written), "unreadable" (Gen1 actions that cannot be
    read now)."""
    if gen >= 2:
        parts = _setting_rows(gen, backup.get("config") or {}, device.get("config") or {})
        parts[PART_SCRIPTS] = {
            "rows": _script_rows(backup.get("scripts") or [], device.get("scripts") or []),
            "skipped": [],
        }
        parts[PART_SCHEDULES] = {
            "rows": _list_rows(
                backup.get("schedules") or [],
                device.get("schedules") or [],
                lambda job: clip(job.get("timespec"), 60) or "–",
                _schedule_text,
            ),
            "skipped": [],
        }
        parts[PART_WEBHOOKS] = {
            "rows": _list_rows(
                backup.get("webhooks") or [],
                device.get("webhooks") or [],
                lambda hook: clip(hook.get("name"), 60) or f"Webhook {hook.get('id')}",
                _webhook_text,
            ),
            "skipped": [],
        }
        return parts
    parts = _setting_rows(
        gen, _gen1_settings(backup.get("settings") or {}), _gen1_settings(device.get("settings") or {})
    )
    parts[PART_ACTIONS] = _action_rows(backup.get("actions"), device.get("actions"))
    return parts


AFTER_ROWS = 100  # rows of each list of a comparison after an update kept at most


def after_update(gen: int, before: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    """What an update changed, from the backup made right before it and the device read
    again afterwards: {"changed": [{"key", "before", "now"}], "new": [{"key", "value"}],
    "gone": [{"key", "before"}], "changed_count", "new_count", "gone_count",
    "scripts": {"before", "again", "stopped"}}.

    changed: settings with another value now ("now" "–": the whole component or Gen1
    object that held the setting is no longer reported); new: settings the backup did
    not have – the new firmware brought them, with Shelly's default value; gone: settings
    the backup had that the device no longer reports although it still reports their
    holder (_holder) – only the firmware changes the shape of the config, so the new
    firmware removed them (e.g. ble.enable since Gen2+ firmware 2.0.0). Gone settings are
    no changes. Counters that change by themselves (GEN2_IGNORED, e.g. sys.cfg_rev) are
    never compared. scripts (Gen2+, Gen1 has none): how many scripts ran before the
    update, how many of them run again, and the names of the others."""
    if gen >= 2:
        old, new, walk_lists = before.get("config") or {}, now.get("config") or {}, False
    else:
        old, new = _gen1_settings(before.get("settings") or {}), _gen1_settings(now.get("settings") or {})
        walk_lists = True

    def compared(path: tuple[str, ...]) -> bool:
        """False for what is no setting (see _setting_rows) or belongs to the scripts."""
        return gen < 2 or not (len(path) < 2 or path in GEN2_IGNORED or path[0].startswith("script:"))

    changed: list[Row] = []
    gone: list[Row] = []
    for path, value in _leaves(old, walk_lists):
        if not compared(path):
            continue
        known, current = _dig(new, path)
        if not known and new and _dig(new, _holder(path, gen))[0]:
            # The device reports settings and still the holder, only not this field: the new firmware removed it
            gone.append({"key": _label(path, gen), "before": _show(value)})
        elif not known or current != value:
            changed.append({"key": _label(path, gen), "before": _show(value), "now": _show(current) if known else "–"})
    added: list[Row] = []
    for path, value in _leaves(new, walk_lists):
        if compared(path) and not _dig(old, path)[0]:
            added.append({"key": _label(path, gen), "value": _show(value)})
    ran = [str(s.get("name") or s.get("id")) for s in before.get("scripts") or [] if s.get("running") is True]
    running = {str(s.get("name") or s.get("id")) for s in now.get("scripts") or [] if s.get("running") is True}
    stopped = [name for name in ran if name not in running]
    return {
        "changed": sorted(changed, key=lambda r: r["key"])[:AFTER_ROWS],
        "new": sorted(added, key=lambda r: r["key"])[:AFTER_ROWS],
        "gone": sorted(gone, key=lambda r: r["key"])[:AFTER_ROWS],
        "changed_count": len(changed),
        "new_count": len(added),
        "gone_count": len(gone),
        "scripts": {"before": len(ran), "again": len(ran) - len(stopped), "stopped": stopped[:AFTER_ROWS]},
    }


def public_rows(rows: list[Row]) -> list[Row]:
    """Rows as the UI gets them: what a difference shows, without the write instructions."""
    return [{field: row[field] for field in ROW_FIELDS if field in row} for row in rows]


# --------------------------------------------------------------------------------------------- Writing


def _config_of(rows: list[Row]) -> dict[str, Any]:
    """The values of some rows of one component as the nested object SetConfig takes."""
    config: dict[str, Any] = {}
    for row in rows:
        node = config
        *parents, leaf = row["path"][1:]
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = row["value"]
    return config


async def _set_config(target: Target, component: str, rows: list[Row]) -> bool:
    """Write some settings of one Gen2+ component. True if the device asks for a restart."""
    name, _, number = component.partition(":")
    params: dict[str, Any] = {"config": _config_of(rows)}
    if number.isdigit():
        params["id"] = int(number)
    answer = await target.rpc(f"{NAMESPACES.get(name, name.capitalize())}.SetConfig", params)
    return answer.get("restart_required") is True


async def _write_config(target: Target, rows: list[Row], errors: dict[str, str]) -> bool:
    """Write the differing settings component by component (parts "settings" and
    "network" of Gen2+). A component that refuses its values is written again value
    by value, so one refused value does not keep the others from being restored."""
    restart = False
    components: dict[str, list[Row]] = {}
    for row in rows:
        components.setdefault(row["path"][0], []).append(row)
    for component, group in components.items():
        try:
            restart |= await _set_config(target, component, group)
            continue
        except AuthError:
            raise
        except ShellyError as err:
            if len(group) == 1:
                errors[group[0]["key"]] = error_text(err)
                continue
        for row in group:
            try:
                restart |= await _set_config(target, component, [row])
            except AuthError:
                raise
            except ShellyError as err:
                errors[row["key"]] = error_text(err)
    return restart


def _chunks(code: str) -> Iterator[str]:
    """The code of a script in pieces of at most PUT_CHUNK bytes (never inside a character)."""
    start = size = 0
    for index, char in enumerate(code):
        width = 1 if char.isascii() else len(char.encode("utf-8", "surrogatepass"))
        if size + width > PUT_CHUNK:
            yield code[start:index]
            start, size = index, 0
        size += width
    if start < len(code):
        yield code[start:]


async def _stop_script(target: Target, script_id: int) -> None:
    """Stop a script before it is written or removed (one that does not run answers with an error)."""
    try:
        await target.rpc("Script.Stop", {"id": script_id})
    except AuthError:
        raise
    except ShellyError:
        pass


async def _write_scripts(target: Target, rows: list[Row], errors: dict[str, str]) -> bool:
    """Make the scripts equal to the backup: remove, create, write code and the start
    at boot. Written scripts stay stopped – _start_scripts starts what ran in the backup."""
    for row in sorted(rows, key=lambda r: r["state"] != "extra"):  # removing first frees the places
        try:
            script_id = row["id"]
            if row["state"] == "extra":
                await _stop_script(target, script_id)
                await target.rpc("Script.Delete", {"id": script_id})
                continue
            item = row["item"]
            if script_id is None:
                script_id = (await target.rpc("Script.Create", {"name": item["name"]})).get("id")
                if not _is_id(script_id):
                    raise ShellyError("The device did not create the script")
            else:
                await _stop_script(target, script_id)
            if row["code"]:
                for number, chunk in enumerate(_chunks(str(item.get("code") or ""))):
                    await target.rpc("Script.PutCode", {"id": script_id, "code": chunk, "append": number > 0})
            config = {"name": item["name"], "enable": item.get("enable") is True}
            await target.rpc("Script.SetConfig", {"id": script_id, "config": config})
        except AuthError:
            raise
        except ShellyError as err:
            errors[row["key"]] = error_text(err)
    return False


async def _start_scripts(target: Target, backup: dict[str, Any], errors: dict[str, str]) -> int:
    """Start the scripts that ran when the backup was made (after the restart, which
    stops every script that does not start at boot). Returns how many of them run."""
    listed = {row.get("name"): row for row in await _listed(target, "Script.List", "scripts")}
    running = 0
    for script in backup.get("scripts") or []:
        current = listed.get(script.get("name"))
        if not script.get("running") or current is None:
            continue
        if current.get("running") is not True:
            try:
                await target.rpc("Script.Start", {"id": current.get("id")})
            except AuthError:
                raise
            except ShellyError as err:
                errors.setdefault(str(script.get("name")), f"Not started: {error_text(err)}")
                continue
        running += 1
    return running


async def _write_list(target: Target, rows: list[Row], errors: dict[str, str], namespace: str) -> bool:
    """Make the schedules or webhooks equal to the backup (namespace "Schedule" or
    "Webhook"): remove, change, create."""
    for row in sorted(rows, key=lambda r: r["state"] != "extra"):  # removing first frees the places
        try:
            if row["state"] == "extra":
                await target.rpc(f"{namespace}.Delete", {"id": row["id"]})
                continue
            body = {k: v for k, v in row["item"].items() if k != "id"}
            if row["state"] == "changed":
                await target.rpc(f"{namespace}.Update", {**body, "id": row["id"]})
            else:
                await target.rpc(f"{namespace}.Create", body)
        except AuthError:
            raise
        except ShellyError as err:
            errors[row["key"]] = error_text(err)
    return False


def _gen1_place(path: tuple[str, ...]) -> tuple[str, str]:
    """(endpoint, parameter) that writes a Gen1 setting."""
    head = path[0]
    if head in GEN1_CHANNELS and len(path) >= 3 and path[1].isdigit():
        return f"/settings/{GEN1_CHANNELS[head]}/{path[1]}", "_".join(path[2:])
    if head in GEN1_NETWORK and len(path) == 2:
        return f"/settings/{head.removeprefix('wifi_')}", path[1]
    if path == ("cloud", "enabled"):
        return "/settings/cloud", "enabled"
    return "/settings", GEN1_PARAMS.get(path) or "_".join(path)


def _gen1_value(value: object) -> str | None:
    """A setting as the text of a Gen1 query parameter; None if it has no such form."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, list) and all(isinstance(v, (int, float, str)) and not isinstance(v, bool) for v in value):
        return ",".join(str(v) for v in value)
    return None


async def _write_gen1(target: Target, rows: list[Row], errors: dict[str, str]) -> bool:
    """Write the differing Gen1 settings endpoint by endpoint (parts "settings" and
    "network"). An endpoint that refuses its values is written again value by value."""
    places: dict[str, list[tuple[Row, tuple[str, str]]]] = {}
    for row in rows:
        endpoint, parameter = _gen1_place(row["path"])
        value = _gen1_value(row["value"])
        if value is None:
            errors[row["key"]] = "This value cannot be written to a Gen1 device"
        else:
            places.setdefault(endpoint, []).append((row, (parameter, value)))
    for endpoint, group in places.items():
        try:
            await target.get(endpoint, [pair for _row, pair in group])
            continue
        except AuthError:
            raise
        except ShellyError as err:
            if len(group) == 1:
                errors[group[0][0]["key"]] = error_text(err)
                continue
        for row, pair in group:
            try:
                await target.get(endpoint, [pair])
            except AuthError:
                raise
            except ShellyError as err:
                errors[row["key"]] = error_text(err)
    return False


async def _write_actions(target: Target, rows: list[Row], errors: dict[str, str]) -> bool:
    """Write the differing Gen1 actions (an action without addresses is emptied)."""
    for row in rows:
        item = row["item"]
        enabled = "true" if item["enabled"] else "false"
        params = [("index", str(row["index"])), ("name", row["name"]), ("enabled", enabled)]
        params += [("urls[]", url) for url in item["urls"]] or [("urls[]", "")]
        try:
            await target.get("/settings/actions", params)
        except AuthError:
            raise
        except ShellyError as err:
            errors[row["key"]] = error_text(err)
    return False


async def _write(target: Target, part: str, rows: list[Row], errors: dict[str, str]) -> bool:
    """Write the differences of one part. True if the device asks for a restart."""
    if part in (PART_SETTINGS, PART_NETWORK):
        return await (_write_config if target.gen >= 2 else _write_gen1)(target, rows, errors)
    if part == PART_SCRIPTS:
        return await _write_scripts(target, rows, errors)
    if part == PART_ACTIONS:
        return await _write_actions(target, rows, errors)
    return await _write_list(target, rows, errors, "Schedule" if part == PART_SCHEDULES else "Webhook")


async def _restart(target: Target) -> bool:
    """Restart the device and wait until it answers again. False if it does not within RESTART_WAIT_S."""
    try:
        await shelly.restart(target.session, target.gen, target.origin, target.auth)
    except AuthError:
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError, OSError):
        return False
    await asyncio.sleep(RESTART_FIRST_S)
    deadline = time.monotonic() + RESTART_WAIT_S
    while time.monotonic() < deadline:
        try:
            await shelly.device_get(target.session, target.origin, "/shelly")
        except (TimeoutError, ShellyError, aiohttp.ClientError, OSError):
            await asyncio.sleep(RESTART_POLL_S)
        else:
            return True
    return False


async def restore(
    target: Target, backup: dict[str, Any], parts: Sequence[str], step: Callable[[str], None]
) -> dict[str, Any]:
    """Make the chosen parts of the device equal to the backup and read it again.

    Order: the parts as listed in GEN2_PARTS / GEN1_PARTS, a restart if the device
    asks for one, the scripts that ran in the backup are started, then everything is
    read again and compared. step is called with the part or step that begins.

    Returns {"parts": [{"part", "total", "open", "skipped"}], "restart", "running",
    "confirmed", "lost"}: total = differences found before writing; open = what is
    not as in the backup afterwards, [{"key", "error"}] with the answer of the device
    where it refused something; restart None (not needed), True (answers again) or
    False; running = scripts running (None if the scripts were not restored);
    confirmed False if the device could not be read again – then nothing counts as
    applied; lost = the error after which the device stopped answering while it was
    written. Raises if the device cannot be read at the start or rejects the password."""
    order = GEN2_PARTS if target.gen >= 2 else GEN1_PARTS
    chosen = [part for part in order if part in parts]
    before = compare(target.gen, backup, await read(target))
    errors: dict[str, dict[str, str]] = {part: {} for part in chosen}
    restart = False
    lost: str | None = None
    for part in chosen:
        step(part)
        try:
            restart |= await _write(target, part, before[part]["rows"], errors[part])
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            lost = error_text(err) or type(err).__name__
            break
    restarted: bool | None = None
    running: int | None = None
    after: dict[str, dict[str, list[Row]]] | None = None
    try:
        if restart and lost is None:
            step(STEP_RESTART)
            restarted = await _restart(target)
        step(STEP_VERIFY)
        if PART_SCRIPTS in chosen:
            running = await _start_scripts(target, backup, errors[PART_SCRIPTS])
        after = compare(target.gen, backup, await read(target))
    except AuthError:
        raise
    except (TimeoutError, ShellyError, aiohttp.ClientError, OSError) as err:
        lost = lost or error_text(err) or type(err).__name__
    results = []
    for part in chosen:
        rows = before[part]["rows"]
        refused = errors[part]
        if after is None:
            still = [{"key": row["key"], "error": refused.get(row["key"])} for row in rows]
        else:
            still = [{"key": row["key"], "error": refused.pop(row["key"], None)} for row in after[part]["rows"]]
            still += [{"key": key, "error": error} for key, error in refused.items()]  # e.g. a script not started
        results.append({"part": part, "total": len(rows), "open": still, "skipped": before[part]["skipped"]})
    return {"parts": results, "restart": restarted, "running": running, "confirmed": after is not None, "lost": lost}
