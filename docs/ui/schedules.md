# UI decisions: Schedules tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Schedules tab or the schedule dialog.

## Schedules tab and dialog (3.10.0, mockups `3.10.0`, approved 2026-11-22)
- **Tab (F10):** toolbar note "Times in the time zone of Home Assistant" and "Add
  schedule" (plus icon). Card "Schedules · n": on/off checkbox, name (free text; restart:
  pause below it), action with icon, repeat, devices, next run, last run (time, outcome
  tag Success / Warning / Missed, short result), pencil and red trash; switched-off rows
  dimmed. Card "Scheduled Installations · n": device, version (with "Beta" tag), install
  at, approved (time · user), "Cancel". Phones: tiles with labelled lines. Empty state
  names both ways (add a schedule; "Install at" in the installation dialog).
- **Dialog "Add schedule" / "Edit schedule" (F12–F15):** name, action (Restart, Cloud on,
  Cloud off, Bluetooth on, Bluetooth off – one action per schedule), pause between devices
  in seconds (restart only, default 3, 0 = all at once), time, repeat (Daily / On weekdays
  with Mon–Sun / Every n days), devices (All Shellys, new ones included automatically /
  selected list with tags "Ignored", "Battery", "Removed", "No Bluetooth", "No cloud"),
  "Schedule is on"; hint which devices a run leaves out; yellow box for a Bluetooth proxy
  of Home Assistant, saving stays possible. Buttons "Cancel" / "Save" (blue). An error
  (e.g. no name) appears in red below the fields; the dialog stays open with all entries
  (live test rc1, approved 2026-11-22).
- **Run now (F19 b):** play icon before the pencil of each schedule; confirmation dialog
  "Run schedule now?" (blue "Run now"); refused with a message while a check, an
  installation or another schedule runs.
- **Running and last result (live test rc1, approved 2026-11-22):** a run shows "Running …" under
  "Last run" only; no line under the page title. Counts relate to all chosen devices,
  left-out ones included ("2 of 3 restarted · 1 left out").
- **Device removed from the list (review 3.10.0, F3 a, approved 2026-11-22):** it is struck
  from every schedule that names it (log "changed"); a schedule left without devices is
  switched off and shows "No device" until devices are chosen again. A device back in Home
  Assistant with a new ID stays in its schedules.
- **Logs (F18):** kind "Schedule" and filter "Schedules": one entry per run (name, action,
  devices, duration, result; findings expandable), "missed" as warning, creating, changing
  and deleting a schedule; scheduled installations under "Installation".
