# Code map – stored data

Part of `docs/CODEMAP.md`; read when you change stored fields or files below `/data`.

**Stored data (`/data`, private to the app)**

| File | Content |
|---|---|
| `state.json` | `{devices, last_scan, firmware, schedule, download_base_confirmed, first_start_open, first_start_notified, keep_unknown_files}` (`ui`/`ui_version` of versions before 3.3.0 are ignored and dropped at the next save); written with fsync. `state.bak` = the save before the last one (loaded when `state.json` is missing or unreadable), `state.damaged` = an unreadable state file set aside at start. `keep_unknown_files`: no readable state was found once – files the registry does not know are never removed |
| `firmware/` | `<sha256>.<zip\|bin\|gbl>`; temporary `.part-*` (downloads, staged uploads, staged store imports and exports; removed at start) |
| `events.json` | activity log, one JSON object per line with `id` (3.0.0: one JSON array, converted once; IDs added once by 3.1.1) |
| `credentials.json` | `{device id: {"gen": 1, "password"} \| {"gen": 2, "realm", "ha1"}}` |
| `changelog.json` | last fetched page per generation: `{"1"\|"2": {"fetched", "versions": {version: {"date", "entries"}}}}` |

Device entry (`state.devices[<HA device id>]`): identity `name`, `shelly_name`,
`gen`, `type` (firmware ID / app), `model`, `mac`, `shelly_id` (Gen2+ realm),
`origin` (`http://<private ip>:<port>`), `version`, `build`; check `status`
(`up-to-date|available|ready|installing|unconfirmed|error|asleep|unregistered|skipped`),
`message`, `error_level`, `last_check`, `last_success`, `last_update`,
`info_refreshed`, `offer_version`, `notified_version`; provided update `file`,
`sha256`, `size`, `pending_version`, `pending_build`, `source_url`, `offer_source`
(`manufacturer|store|upload`); settings `ntp`,
`eco`, `ap`, `ble`, `debug_ws` (websocket debug log, Gen1 unsupported), `outputs`, `actions`, `schedules`, `auth_enabled`,
`battery` (sleeping battery device, read from the device while awake: Gen2+ `wakeup_period` > 0 in `Sys.GetStatus` (checks and reloads) or the sys config, Gen1
`sleep_mode.period` > 0; kept while the device does not answer), `settings_error`; flags `ignored`; runtime only (not trusted after a restart)
`ota_progress`, `ota_phase` ("restarting"), `downgrade`; `updated` (confirmed or unconfirmed installation, until the next full
check or single check of the device), `install_failed` (failed installation, not a downgrade: file stays
provided for a retry, same lifetime as `updated`), `store_fallback` (Gen2+: server not reachable,
newer stored version offered for a manual installation; cleared by the next check of the device),
`step_target` (Gen2+: provided version is an intermediate version; newest version or ""). Memory only: `Manager.activity`, `progress`,
`installing`, `reloading`, `device_checks`, `downloads`, `_provisions` / `_provision_waiters` / `_fetching`, `_upload`, `_scan_user` / `_check_users` / `_verifying` /
`_fresh` (running checks only).

Firmware entry (`state.firmware[<file name>]`): `type`, `gen`, `version`, `sha256`,
`size`, `downloaded`, `https` (downloads), `source` (`download|upload`),
`sha_checked` (uploads), `changelog` (`{"date", "entries"}` of this version, set once
the changelog page lists it; never requested again), `intermediate` (true once a check
provided the file as an intermediate version; never set for older files).
