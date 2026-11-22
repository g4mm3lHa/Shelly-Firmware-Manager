# Code map – stored data

Part of `docs/CODEMAP.md`; read when you change stored fields or files below `/data`.

**Stored data (`/data`, private to the app)**

| File | Content |
|---|---|
| `state.json` | `{devices, last_scan, firmware, schedule, schedules, download_base_confirmed, first_start_open, first_start_notified, gen1_imports_marked, unknown_files, others, listing}` (`gen1_imports_marked` missing = state of 3.8.0 or older: all Gen1 entries become `import` once at the start, `mark_gen1_imported`) (`ui`/`ui_version` of versions before 3.3.0 and `keep_unknown_files` are ignored and dropped at the next save); written with fsync. `state.bak` = the save before the last one (loaded when `state.json` is missing or unreadable), `state.damaged` = an unreadable state file set aside at start. `unknown_files`: names of firmware files the store does not list (never deleted automatically; reported in the log once); `others`: read-only list of other Shellys `{id, name, kind, model, firmware, gateway}` (`manager_checks.other_shellys`); `listing`: Shellys of the Shelly integration Home Assistant knows but the app does not list `{listed, missing: [{name, model, reason: disabled\|discovered\|ignored}]}` (`_update_listing`, full checks only) |
| `firmware/` | `<sha256>.<zip\|bin\|gbl>`; temporary `.part-*` (downloads, staged uploads, staged store imports and exports; removed at start) |
| `events.json` | activity log, one JSON object per line with `id` (3.0.0: one JSON array, converted once; IDs added once by 3.1.1) |
| `credentials.json` | `{device id: {"gen": 1, "password"} \| {"gen": 2, "realm", "ha1"}}` |
| `changelog.json` | last fetched page per generation: `{"1"\|"2": {"fetched", "versions": {version: {"date", "entries"}}}}` |

Device entry (`state.devices[<HA device id>]`): identity `name`, `shelly_name`,
`gen`, `type` (firmware ID / app), `model`, `mac`, `shelly_id` (Gen2+ realm),
`origin` (`http://<private ip>:<port>`), `version`, `build`; check `status`
(`up-to-date|available|ready|installing|unconfirmed|error|asleep|unregistered|skipped`; `unregistered` only from older versions, shown as not checked),
`message`, `error_level`, `last_check`, `last_success`, `last_update`,
`info_refreshed`, `offer_version`, `notified_version`; provided update `file`,
`sha256`, `size`, `pending_version`, `pending_build`, `source_url`, `offer_source`
(`manufacturer|store|upload`); settings `ntp`,
`eco`, `ap`, `ble`, `ble_proxy` (Home Assistant's Bluetooth proxy script runs), `cloud`, `cloud_connected`, `debug_ws` (websocket debug log, Gen1 unsupported), `outputs`, `actions`, `schedules`, `auth_enabled`,
`battery` (sleeping battery device, read from the device while awake: Gen2+ `wakeup_period` > 0 in `Sys.GetStatus` (checks and reloads) or the sys config, Gen1
`sleep_mode.period` > 0; kept while the device does not answer; without a value of the device a Shelly battery sensor in HA sets it), `battery_source` (`device|home_assistant`), `settings_error`; `host_name` (host name of `configuration_url`, else `<shelly_id or gen1_hostname>.local`; looked up at every check while option `resolve_host_names` is on, None unless it resolves; dropped at the start while it is off; display only), `gen1_hostname` (Gen1 `/settings` `device.hostname`), `address_issue` (`{reason: missing|unresolved|not_private, message}` while the registry gives no usable address); removed `removed` (time it left the registry; Ignored mark dropped), `removed_reachable` (answered at its last address at the last check); flags `ignored`; runtime only (not trusted after a restart)
`ota_progress`, `ota_phase` ("restarting"), `downgrade`; `updated` (confirmed or unconfirmed installation, until the next full
check or single check of the device), `install_failed` (failed installation, not a downgrade: file stays
provided for a retry, same lifetime as `updated`), `store_fallback` (Gen2+: server not reachable,
newer stored version offered for a manual installation; cleared by the next check of the device),
`step_target` (Gen2+: provided version is an intermediate version; newest version or ""), `beta` (provided version is a beta of the update server), `planned` ("Install at": `{at, by, on, sha256, version, target, beta, restart}`, see `manager_schedules`), `offer_date` (build date of the provided version, ISO date), `hold` (`{version, built, until}` while option `offer_after_days` holds a new version back; the device stays up to date), `hold_released` (version "Offer update now" released for this device). Memory only: `Manager.activity`, `progress`,
`installing`, `reloading`, `device_checks`, `downloads`, `_provisions` / `_provision_waiters` / `_fetching`, `_upload`, `_scan_user` / `_check_users` / `_verifying` /
`_fresh` (running checks only).

Firmware entry (`state.firmware[<file name>]`): `type`, `gen`, `version`, `sha256`,
`size`, `downloaded`, `https` (downloads), `source` (`download|upload|import`; `import`: every
file of a store import, unverified – Gen2+ becomes `download` with `https` once an offer URL names its
SHA-256),
`sha_checked` (uploads), `changelog` (`{"date", "entries"}` of this version, set once
the changelog page lists it; never requested again), `intermediate` (true once a check
provided the file as an intermediate version; never set for older files), `adopted` (read back from the
disk at the start; Gen2+: removed once an offer URL names its SHA-256 – until then not provided, no store
fallback or downgrade).

Schedule entry (`state.schedules[]`, see `timetable.py`): `id` (12 hex), `name`, `on`, `action`
(`restart|cloud_on|cloud_off|ble_on|ble_off`), `pause` (restart only), `time` (HH:MM),
`repeat` (`{kind: daily}`, `{kind: weekdays, days}`, `{kind: every, n}`), `devices` (`"all"` or
device IDs), `anchor` (first day of "every n days"), `next` (epoch, None while off), `last`
(`{at, severity, text}` with `counts` of a run or `reason` of a missed run).
