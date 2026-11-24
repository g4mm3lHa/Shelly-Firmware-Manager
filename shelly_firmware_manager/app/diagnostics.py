"""Text of "Details for a bug ticket" (device details): what a report on GitHub or a
question in a forum needs, with names and addresses masked (pure functions; the
manager collects the values, see Manager.device_report).

Masked: every name of the device (Home Assistant name, Shelly name, host name) as
"[Name 1]", IPv4 addresses
to their first two numbers ("192.168.x.x"), IPv6 addresses entirely, MAC addresses and
Shelly device IDs to their last four characters. Passwords never reach this module.
"""

import re
import time
from collections.abc import Iterable
from typing import Any

from shelly import plain

LOG_LINES = 5  # log lines of the device in the text

LINE_MAX = 300  # characters of one log line

# IPv4 address; IPv6 address (at least three groups, so a time like 20:14 stays)
IPV4_RE = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}\b")
IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b|\[[0-9a-fA-F:]+\]")
# MAC address with or without separators, also inside a Shelly ID (shellyplus1-a8032ab12345)
MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-]?[0-9A-Fa-f]{2}){5}\b|(?<=-)[0-9A-Fa-f]{12}\b")

# Fields of a log entry that the text shows (anything else stays out)
LOG_FIELDS = ("event", "old", "new", "version", "offer", "error", "detail", "result", "reason", "message")


class Masker:
    """Replaces names and addresses in texts of the report (see the module text)."""

    def __init__(self, own: Iterable[object]) -> None:
        """own: the names of the device itself, all shown as "[Name 1]"."""
        self.names: dict[str, str] = {}
        for name in own:
            if isinstance(name, str) and name.strip():
                self.names[name.strip()] = "[Name 1]"

    def text(self, value: object) -> str:
        """value with every known name and every address masked."""
        out = plain(value, LINE_MAX)
        for name in sorted(self.names, key=len, reverse=True):  # longest first: no partial replacement
            out = out.replace(name, self.names[name])
        out = MAC_RE.sub(lambda m: "****" + re.sub(r"[:-]", "", m.group(0))[-4:].upper(), out)
        out = IPV6_RE.sub("[IPv6]", out)
        return IPV4_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)}.x.x", out)


def _when(ts: object) -> str:
    """Local time "YYYY-MM-DD HH:MM" of an epoch, "–" without one."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not ts:
        return "–"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def log_lines(entries: Iterable[dict[str, Any]], name: str, masker: Masker) -> list[str]:
    """The newest LOG_LINES lines of the log about the device named name: its own
    entries and its findings in check entries."""
    lines: list[str] = []
    for entry in entries:  # newest first
        kind = str(entry.get("kind") or "")
        parts: list[dict[str, Any]] = []
        if entry.get("device") == name:
            parts.append(entry)
        if kind == "check":
            parts += [f for f in entry.get("findings") or [] if isinstance(f, dict) and f.get("device") == name]
        for part in parts:
            severity = part.get("severity") or entry.get("severity") or ""
            fields = [f"{key}: {part[key]}" for key in LOG_FIELDS if part.get(key) not in (None, "", [])]
            line = f"{_when(entry.get('ts'))} {kind} {severity}" + (" – " + " · ".join(fields) if fields else "")
            lines.append(masker.text(line))
        if len(lines) >= LOG_LINES:
            break
    return lines[:LOG_LINES]


def report(values: dict[str, Any], entries: Iterable[dict[str, Any]]) -> str:
    """The text for a report. values: app, home_assistant, arch, the device entry
    ("device"), whether a password is stored ("password_stored"), the download base
    URL and its last connection test, the options that matter for a report."""
    dev: dict[str, Any] = values["device"]
    masker = Masker([dev.get("name"), dev.get("shelly_name"), dev.get("host_name"), dev.get("shelly_id")])
    gen = dev.get("gen")
    offer = dev.get("offer_version")
    lines = [
        f"App: Firmware Manager for Shelly {values['app']}",
        f"Home Assistant: {values.get('home_assistant') or 'unknown'} · {values.get('arch') or '?'}",
        "",
        f"Device: {dev.get('model') or '?'} · Gen {gen or '?'} · {dev.get('type') or '?'}",
        f"Firmware: {dev.get('version') or '?'}"
        + (f" · offered {offer} ({'beta' if dev.get('beta') else 'stable'})" if offer else ""),
        (
            f"Status: {dev.get('status') or '?'} · last check {_when(dev.get('last_check'))}"
            f" · last success {_when(dev.get('last_success'))}"
        ),
    ]
    if dev.get("message"):
        lines.append(masker.text(f"Message: {dev['message']}"))
    lines += [
        masker.text(
            f"Name: [Name 1] · IP: {dev.get('ip') or '?'} · MAC: {dev.get('mac') or '?'}"
            + (" · battery device" if dev.get("battery") else "")
        ),
        "Password protection: "
        + ("on" if dev.get("auth_enabled") is True else "off" if dev.get("auth_enabled") is False else "unknown")
        + f" · password stored in the app: {'yes' if values.get('password_stored') else 'no'}",
    ]
    test = values.get("connection_test")
    test_text = "not tested"
    if isinstance(test, dict):
        test_text = ("reachable" if test.get("ok") else "not reachable") + f" ({_when(test.get('at'))})"
        if test.get("detail"):  # the device's raw answer of a failed test
            test_text += f" · device answer: {test['detail']}"
    base = values.get("download_base") or "not set"
    lines.append(masker.text(f"Download base URL: {base} · connection test: {test_text}"))
    lines.append("Options: " + " · ".join(f"{k} {v}" for k, v in values.get("options", {}).items()))
    found = log_lines(entries, str(dev.get("name") or ""), masker)
    lines += ["", f"Log (last {len(found)} entries of this device):" if found else "Log: no entries of this device"]
    lines += found
    return "\n".join(lines)
