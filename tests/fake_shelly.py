"""Shellys in memory for the backup tests and the demo of the UI tests: a Gen2+ device
(FakeShelly) answering the RPC methods a backup reads and writes, and a Gen1 device
(FakeGen1) with /settings, its endpoints and /settings/actions. No connection is opened."""

import asyncio
import copy

import shelly
import shelly_backup
from shelly import ShellyError, UnsupportedError

SCRIPT = "// Küche ☀ – light\nlet n = 0;\nfunction tick() { n++; }\nTimer.set(1000, true, tick);\n"


class FakeShelly:
    """A Gen2+ Shelly in memory: answers the RPC methods a backup reads and writes."""

    def __init__(self, mac="AABBCC00000A"):
        self.config = {
            "sys": {
                "device": {
                    "name": "Kitchen",
                    "mac": mac,
                    "fw_id": "20260101-1/1.4.0",
                    "eco_mode": False,
                    "profile": "switch",
                },
                "cfg_rev": 7,
                "sntp": {"server": "time.google.com"},
            },
            "switch:0": {"id": 0, "name": None, "initial_state": "off", "auto_off": False, "auto_off_delay": 60.0},
            "input:0": {"id": 0, "type": "switch"},
            "wifi": {
                "sta": {"ssid": "Home", "is_open": False, "enable": True, "ipv4mode": "dhcp", "ip": None},
                "sta1": {"ssid": None, "is_open": True, "enable": False},
                "ap": {"ssid": "ShellyAP", "is_open": True, "enable": False},
            },
            "mqtt": {"enable": False, "server": None, "user": "mq", "pass": "hunter2"},
        }
        self.scripts = {
            1: {"name": "auto-light.js", "enable": True, "running": True, "code": SCRIPT},
            2: {"name": shelly.BLE_PROXY_SCRIPT, "enable": True, "running": True, "code": "// ha"},
        }
        self.jobs = [
            {
                "id": 1,
                "enable": True,
                "timespec": "0 0 22 * * *",
                "calls": [{"method": "Switch.Set", "params": {"id": 0, "on": False}}],
            }
        ]
        self.hooks = [
            {
                "id": 1,
                "cid": 0,
                "enable": True,
                "event": "input.toggle_on",
                "name": "Motion",
                "urls": ["http://192.168.1.2:8123/api/webhook/abc"],
            }
        ]
        self.calls = []
        self.refused = set()  # leaf names SetConfig refuses
        self.restart_for = set()  # components whose change asks for a restart
        self.down = False  # True: the device does not answer
        self.restarts = 0
        self.chunk = 16  # bytes of code per Script.GetCode answer

    def component(self, namespace, params):
        key = namespace.lower() + (f":{params['id']}" if "id" in params else "")
        if key not in self.config:
            raise UnsupportedError("Not supported by this device")
        return key

    @staticmethod
    def merge(node, values):
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(node.get(key), dict):
                FakeShelly.merge(node[key], value)
            else:
                node[key] = value

    @staticmethod
    def leaves(values):
        for key, value in values.items():
            if isinstance(value, dict):
                yield from FakeShelly.leaves(value)
            else:
                yield key

    def listed(self, rows, key):
        return {key: copy.deepcopy(rows)}

    async def rpc(self, method, params=None):
        await asyncio.sleep(0)
        if self.down:
            raise TimeoutError()
        params = params or {}
        self.calls.append(method)
        namespace, _, verb = method.partition(".")
        if method == "Shelly.GetConfig":
            return copy.deepcopy(self.config)
        if verb == "SetConfig" and namespace != "Script":
            key = self.component(namespace, params)
            refused = self.refused & set(self.leaves(params["config"]))
            if refused:
                raise ShellyError(f"Device reports: invalid value for {sorted(refused)[0]}")
            self.merge(self.config[key], params["config"])
            self.config["sys"]["cfg_rev"] += 1
            return {"restart_required": key in self.restart_for}
        if namespace == "Script":
            return self.script(verb, params)
        rows = {"Schedule": self.jobs, "Webhook": self.hooks}.get(namespace)
        if rows is None:
            raise UnsupportedError("Not supported by this device")
        if verb == "List":
            return self.listed(rows, "jobs" if namespace == "Schedule" else "hooks")
        if verb == "Create":
            if params.get("event") in self.refused:
                raise ShellyError(f"Device reports: unknown event {params['event']}")
            new = max((r["id"] for r in rows), default=0) + 1
            rows.append({**copy.deepcopy(params), "id": new})
            return {"id": new}
        row = next(r for r in rows if r["id"] == params["id"])
        if verb == "Update":
            row.update(copy.deepcopy(params))
        elif verb == "Delete":
            rows.remove(row)
        return {}

    def script(self, verb, params):
        if verb == "List":
            return {
                "scripts": [
                    {"id": i, "name": s["name"], "enable": s["enable"], "running": s["running"]}
                    for i, s in self.scripts.items()
                ]
            }
        if verb == "Create":
            new = max(self.scripts, default=0) + 1
            self.scripts[new] = {"name": params["name"], "enable": False, "running": False, "code": ""}
            return {"id": new}
        script = self.scripts[params["id"]]
        if verb == "GetCode":
            raw = script["code"].encode()
            part = raw[params["offset"] : params["offset"] + self.chunk]
            return {"data": part.decode("utf-8", "replace"), "left": len(raw) - params["offset"] - len(part)}
        if verb == "PutCode":
            assert len(params["code"].encode()) <= shelly_backup.PUT_CHUNK
            script["code"] = (script["code"] if params["append"] else "") + params["code"]
        elif verb == "SetConfig":
            script.update(params["config"])
        elif verb == "Delete":
            del self.scripts[params["id"]]
        elif verb == "Start":
            script["running"] = True
        elif verb == "Stop":
            if not script["running"]:
                raise ShellyError("Device reports: script is not running")
            script["running"] = False
        return {}

    def restart(self):
        self.restarts += 1
        for script in self.scripts.values():
            script["running"] = script["enable"]


class FakeGen1:
    """A Gen1 Shelly in memory: /settings and its endpoints, /settings/actions."""

    def __init__(self, actions=True):
        self.settings = {
            "device": {"type": "SHSW-1", "mac": "AABBCC00000B"},
            "name": "Hall",
            "fw": "20230913-112003/v1.14.0-gcb84623",
            "time": "12:00",
            "unixtime": 1700000000,
            "login": {"enabled": False, "username": "admin", "password": "old"},
            "wifi_ap": {"enabled": False, "ssid": "shelly1", "key": "apkey"},
            "wifi_sta": {"enabled": True, "ssid": "Home", "ipv4_method": "dhcp", "ip": None},
            "mqtt": {"enable": False, "server": "192.168.1.3:1883", "user": ""},
            "coiot": {"enabled": True, "update_period": 15},
            "sntp": {"server": "time.google.com", "enabled": True},
            "cloud": {"enabled": False, "connected": False},
            "led_status_disable": False,
            "mode": "relay",
            "relays": [
                {
                    "name": None,
                    "ison": False,
                    "has_timer": False,
                    "default_state": "off",
                    "auto_off": 0.0,
                    "schedule_rules": ["0700-0123456-on"],
                }
            ],
        }
        self.actions = {"out_on_url": [{"index": 0, "urls": [], "enabled": False}]} if actions else None
        self.calls = []

    async def get(self, path, params=None):
        await asyncio.sleep(0)
        pairs = list(params or [])
        self.calls.append((path, pairs))
        if path == "/settings/actions":
            if self.actions is None:
                raise UnsupportedError("Not supported by this device")
            if pairs:
                values = dict(p for p in pairs if p[0] != "urls[]")
                self.actions[values["name"]][int(values["index"])] = {
                    "index": int(values["index"]),
                    "urls": [v for k, v in pairs if k == "urls[]" and v],
                    "enabled": values["enabled"] == "true",
                }
            return {"actions": copy.deepcopy(self.actions)}
        target = self.settings
        if path.startswith("/settings/relay/"):
            target = self.settings["relays"][int(path.rsplit("/", 1)[1])]
        elif path in ("/settings/sta", "/settings/cloud"):
            target = self.settings["wifi_sta" if path.endswith("sta") else "cloud"]
        for key, value in pairs:
            if key == "bad":
                raise ShellyError("Shelly rejected the request (HTTP 400)")
            typed = {"true": True, "false": False}.get(value, value)
            if key == "schedule_rules":
                typed = value.split(",") if value else []
            elif key in ("auto_off", "coiot_update_period"):
                typed = float(value) if key == "auto_off" else int(value)
            if key.startswith("mqtt_"):
                self.settings["mqtt"][key[5:]] = typed
            elif key == "coiot_update_period":
                self.settings["coiot"]["update_period"] = typed
            elif key in target:
                target[key] = typed  # an unknown parameter is ignored, as on a real device
        return copy.deepcopy(target)
