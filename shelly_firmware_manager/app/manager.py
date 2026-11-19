"""Manager, layer 6 of 6: the complete object and its view for the UI.

See manager_core for the layers. Everything outside the manager modules uses this
class only.
"""

import logging
import time
from typing import Any

import schedule
import shelly

from manager_core import DOWNLOAD_BASE_MESSAGES
from manager_installs import Installs

log = logging.getLogger("sfm.manager")


class Manager(Installs):
    """Holds the state of all Shellys and controls checks, storage and installation."""

    def public_state(self) -> dict[str, Any]:
        """Only fields the UI needs: no credentials, builds or download diagnosis.

        The device's private IP address and its web interface URL are included on
        purpose (IP column, link to the device); both come from the validated origin."""
        fields = (
            "name",
            "shelly_name",
            "gen",
            "type",
            "model",
            "version",
            "offer_version",
            "status",
            "message",
            "sha256",
            "size",
            "last_check",
            "last_success",
            "last_update",
            "ntp",
            "eco",
            "ap",
            "ble",
            "debug_ws",
            "actions",
            "schedules",
            "auth_enabled",
            "battery",
            "outputs",
            "source_url",
            "offer_source",
            "updated",
            "install_failed",
            "store_fallback",
            "step_target",
            "info_refreshed",
            "settings_error",
            "error_level",
            "ota_progress",
            "ota_phase",
            "downgrade",
        )
        devices: list[dict[str, Any]] = []
        stored = self._downgrade_candidates()
        for key, dev in self.devices.items():
            origin = dev.get("origin")  # checked by resolve_device_origin: private IP
            devices.append(
                {
                    "id": key,
                    **{f: dev.get(f) for f in fields},
                    # Number of stored older versions (row menu: Downgrade enabled or not)
                    "downgrades": self._downgrade_count(dev, stored),
                    "installing": key in self.installing,
                    "activity": self.activity.get(key),
                    "ignored": bool(dev.get("ignored")),
                    "password_set": self.credentials.has_device_password(key),  # flag only
                    "ip": shelly.origin_ip(origin) if origin else None,
                    "web_url": f"{origin}/" if origin else None,
                }
            )
        return {
            "devices": devices,
            "last_scan": self.last_scan,
            "busy": self.busy,
            "schedule": {
                "days": self.check_days,
                "time": self.check_time,
                "timezone": self.tz_name,
                "next": int(schedule.next_run(self.schedule, time.time(), self.tz)),
            },
            "scanning": self.checking,
            "progress": dict(self.progress) if self.progress else None,
            "storage_error": self.storage_error,
            "gen1_enabled": self.gen1_enabled,
            "allow_downgrade": self.allow_downgrade,
            "first_start_open": self.first_start_open,  # import question of a fresh installation
            "import_max_mb": self.import_max // 1048576,
            "download_base": {
                "url": self.download_base,
                "issue": self.download_base_issue,
                "message": DOWNLOAD_BASE_MESSAGES.get(self.download_base_issue or ""),
            },
        }
