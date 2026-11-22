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
  in seconds (restart only, default 10, 0 = all at once), time, repeat (Daily / On weekdays
  with Mon–Sun / Every n days), devices (All Shellys, new ones included automatically /
  selected list with tags "Ignored", "Battery", "Removed", "No Bluetooth", "No cloud"),
  "Schedule is on"; hint which devices a run leaves out; yellow box for a Bluetooth proxy
  of Home Assistant, saving stays possible. Buttons "Cancel" / "Save" (blue).
- **Run now (F19 b):** play icon before the pencil of each schedule; confirmation dialog
  "Run schedule now?" (blue "Run now"); refused with a message while a check, an
  installation or another schedule runs.
- **Logs (F18):** kind "Schedule" and filter "Schedules": one entry per run (name, action,
  devices, duration, result; findings expandable), "missed" as warning, creating, changing
  and deleting a schedule; scheduled installations under "Installation".
