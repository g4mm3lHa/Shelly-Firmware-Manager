"""Manager, layer 9 of 9: the complete object and its view for the UI.

See manager_core for the layers. Everything outside the manager modules uses this
class only.
"""

import logging
import platform
import time
from typing import Any

import diagnostics
import schedule
import shelly_net
from backup_store import normalize_mac
from manager_core import BATTERY_FROM_DEVICE, DOWNLOAD_BASE_MESSAGES, section_of
from manager_schedules import Schedules

log = logging.getLogger("sfm.manager")


class Manager(Schedules):
    """Holds the state of all Shellys and controls checks, storage and installation."""

    def public_state(self) -> dict[str, Any]:
        """Only fields the UI needs: no credentials, builds or download diagnosis.

        The device's private IP address and its web interface URL are included on
        purpose (IP column, link to the device); both come from the validated origin.
        Per device "section" names the part of the device list it belongs to
        (manager_core.section_of); "others" is the read-only list of Shellys the app
        cannot update (see manager_checks.other_shellys)."""
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
            "host_name",
            "address_issue",
            "removed_reachable",
            "cloud",
            "cloud_connected",
            "ble_proxy",
            "hold",
            "offer_date",
            "beta",
            "area",
            "unreachable",
            "after_update",
        )
        devices: list[dict[str, Any]] = []
        kept = self._stored_versions()
        stored = self._downgrade_candidates(kept)
        backups = self.backup_counts()
        for key, dev in self.devices.items():
            origin = dev.get("origin")  # checked by resolve_device_origin: private IP
            devices.append(
                {
                    "id": key,
                    **{f: dev.get(f) for f in fields},
                    # Number of stored older versions (row menu: Downgrade enabled or not)
                    "downgrades": self._downgrade_count(dev, stored),
                    # Gen2+: is the installed version stored (way back after the next update)?
                    "way_back": self.way_back(dev, kept),
                    "installing": key in self.installing,
                    # Running step of the installation (status pill and its tooltip) or None
                    "install_step": self.install_step_view(key),
                    "activity": self.activity.get(key),
                    "ignored": bool(dev.get("ignored")),
                    # Removed from Home Assistant: since when (seconds) or None
                    "removed": dev.get("removed") or None,
                    # Where the battery marker comes from: "device" or "home_assistant"
                    "battery_source": dev.get("battery_source") or BATTERY_FROM_DEVICE if dev.get("battery") else None,
                    "section": section_of(dev),
                    # Running downgrade: its target version (the offer of the last check stays offer_version)
                    "target": dev.get("pending_version") if dev.get("downgrade") else None,
                    "password_set": self.credentials.has_device_password(key),  # flag only
                    "ip": shelly_net.origin_ip(origin) if origin else None,
                    "web_url": f"{origin}/" if origin else None,
                    "planned": self.planned_view(dev),  # scheduled installation ("Install at")
                    # Stored backups of the settings: {"count", "kept", "last", "reason", "size"} or None
                    "backups": backups.get(normalize_mac(dev.get("mac")) or ""),
                }
            )
        return {
            "start_id": self.start_id,  # changes with every app start (Settings tab: restart done)
            "devices": devices,
            "others": self.others,
            # Shellys Home Assistant knows but the app does not list (with the reason)
            "listing": self.listing,
            "offer_after_days": self.offer_after_days,
            "last_scan": self.last_scan,
            "busy": self.busy,
            "schedule_running": self.schedule_running,  # name of the schedule whose run is going on
            # mark of the newest log entry (EventLog.latest): the Logs tab reloads and marks unread
            # entries; the red dot of the tab follows the newest error entry
            "log_latest": self.events.latest(),
            "log_latest_error": self.events.latest("error"),
            # Backups of the device settings: options, a change counter of the list (the
            # Backups tab reloads), totals for the Settings tab and the two running jobs
            "backup_before_install": self.backup_before_install,
            "backup_keep": self.backup_keep,
            "backups_rev": self.backups.rev,
            "backup_totals": self.backups.totals(),
            "backup_job": self.backup_job,
            "restore": self.restore_view(),
            "schedule": {
                "days": self.check_days,
                "time": self.check_time,
                "timezone": self.tz_name,
                "next": int(schedule.next_run(self.schedule, time.time(), self.tz)),
            },
            "scanning": self.checking,
            "progress": dict(self.progress) if self.progress else None,
            "storage_error": self.storage_error or self.state_warning or self.settings_warning,
            "gen1_enabled": self.gen1_enabled,
            "allow_downgrade": self.allow_downgrade,
            "beta_firmware": self.beta_firmware,
            "schedules": self.schedules_view(),  # Schedules tab (see manager_schedules)
            "host_names": self.resolve_host_names,  # option "Resolve host names" (Host name column)
            "first_start_open": self.first_start_open,  # import question of a fresh installation
            "import_max_mb": self.import_max // 1048576,
            # Card "Getting started" (fresh installation) and the last connection test of the
            # download server, only while it was made with the current download base URL
            "getting_started": self.getting_started,
            "connection_test": self.current_connection_test(),
            "test_devices": self.test_devices(),  # devices that can run the connection test
            # host port of the download server (None: switched off or not read from the Supervisor)
            "host_port": self.host_port if self.host_port_known else None,
            "download_base": {
                "url": self.download_base,
                "issue": self.download_base_issue,
                "message": DOWNLOAD_BASE_MESSAGES.get(self.download_base_issue or ""),
            },
        }

    def current_connection_test(self) -> dict[str, Any] | None:
        """The last connection test, if it was made with the current download base URL."""
        test = self.connection_test
        return test if test is not None and test.get("url") == self.download_base else None

    def hide_getting_started(self) -> None:
        """"Hide" on the card "Getting started": it does not come back."""
        self.getting_started = False
        self.save()

    async def device_report(self, device_id: str, app_version: str) -> str:
        """Text of "Details for a bug ticket" for one device (see diagnostics): names and
        addresses masked, no passwords. Nothing is sent anywhere."""
        dev = self.devices[device_id]
        origin = dev.get("origin")
        values = {
            "app": app_version,
            "home_assistant": await self.ha_version(),
            "arch": platform.machine(),
            "device": {**dev, "ip": shelly_net.origin_ip(origin) if origin else None},
            "password_stored": self.credentials.has_device_password(device_id),
            "download_base": self.download_base,
            "connection_test": self.current_connection_test(),
            "options": {
                "restrict_downloads": "on" if self.restrict_downloads else "off",
                "backup_before_install": "on" if self.backup_before_install else "off",
                "offer_after_days": self.offer_after_days,
                "beta_firmware": "on" if self.beta_firmware else "off",
                "gen1_updates": "on" if self.gen1_enabled else "off",
            },
        }
        return diagnostics.report(values, self.events.list())

