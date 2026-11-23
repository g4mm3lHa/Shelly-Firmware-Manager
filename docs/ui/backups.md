# UI decisions: Backups tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Backups tab, a backup dialog or the restore.

## Tab "Backups" (3.11.0, mockups `3.11.0-backups`, approved 2026-11-22)
- **Tabs (F1):** Devices, Firmware, Schedules, Backups, Logs, Settings; phones label only
  the active tab (see `common.md`).
- **Layout (F2):** toolbar with search and filter (All, With backup, No backup, Older than
  30 days), summary "n backups of m devices · size" + lock "Stored encrypted", right
  "Back up now". Card "Backups · n" ("The newest N per device are kept"): one row per
  device (name, model · Gen n, latest backup with reason tag, firmware, "n of N", size,
  icon "Back up <name> now"), a chevron opens its backups (date, reason, firmware,
  contents, size, "Restore", download icon, keep icon, red delete). Card "No Backup · n"
  with the reason per device and "Back up now" (disabled for a removed device and for a
  protected one whose password is not stored). Phones: tiles. No backup at all: one card
  with the explanation.
- **Reasons:** tags Manual, Before update / Before downgrade (with "a → b"), Schedule (with
  its name), Before restore; "Imported" as a second tag. Gen 1 without readable actions:
  yellow tag "Actions not readable".
- **Kept (F8):** pin icon button per backup: grey = "Keep – never remove this backup
  automatically", primary colour = kept (pin + "Kept" after the date); a kept backup is
  never removed automatically and does not count ("n of N + k kept"). The pin replaced
  the lock, which read as password or encryption (live test rc2, approved 2026-11-23).
- **Retention (F7):** one counter for all reasons, setting "Keep backups per device".
- **Removed devices:** a device removed from Home Assistant keeps its backups (tag
  "Removed", no new backup). Backups of a device that is no longer listed stay as a row
  "no longer listed": download and delete only.
- **Download (F12 b):** download icon per backup → dialog "Download backup": value list
  (device, backup, contents, file), generated password in a read-only field with copy
  icon, yellow box (sealed with it, shown only here, stored nowhere; as in the store
  export), yellow box about secrets in scripts and webhooks, checkbox "I have saved the password"; blue "Download" enabled only after it.
- **Import:** button "Import backup" in the device's "Backups" dialog. Step 1: file +
  password, blue "Check file" (enabled with both). Step 2: success icon, value list, check
  list (Seal intact, Same device, Not stored yet – all three green), blue "Import
  backup". A file the check refuses (wrong password, no backup file) brings back step 1
  with the file, the password and the reason (`common.md`, input errors); a file of
  another device or one stored already shows the reason in step 2 ("Close"). Imported backups are marked "Imported" and "Kept"; nothing is changed on the
  device.
- **Back up now (F14):** tab button opens the dialog "Back up now" (All Shellys / Selected
  devices, hint text, blue "Back up n devices"). While it runs: progress line with bar and
  the devices done so far; then the result per device as check list (green stored, yellow
  stored with a gap or battery device asleep, red not stored). One device: check line
  with spinner, then a success message.
- **Log (F9 a, approved 2026-11-23):** "Back up now" for several devices writes one log
  entry per run ("Backup run · manual · n devices · duration · result"); it opens to one
  line per device (stored with size and parts, asleep, not backed up with the reason) and
  takes the worst result as its colour, an interrupted run is at least a warning. One
  device from its row or dialog keeps its own entry.
- **Running in the background:** a dialog of a run or restore may be closed; a bar above
  the cards names it with "Show" (running) or "Show result" (finished, until the result
  was closed).
- **Delete:** "Delete backup?" with the red trash icon, Cancel / red Delete.

## Restore (3.11.0, mockups `3.11.0-backups`, approved 2026-11-22)
- One device at a time. Title "Restore backup?" with the backup icon in all steps.
- **Step 1:** section Backup (device, backup, firmware then/now), section Check as check
  list ("Same device", "Firmware differs" yellow – allowed, unknown settings are skipped
  and named, F13; "Device reachable"), section "What to restore" with one tick per part and
  its count of differences; "Show differences" opens the table Setting / On the device /
  In the backup with Shelly's own keys in every language (F10); identical parts are greyed
  out. Section "Network and access": "Wi-Fi and network settings" unticked by default (F3);
  ticked → red box; if the backup names another Wi-Fi (SSID) than the device, the box
  gets one more paragraph per name: both names, the Wi-Fi password is not part of the
  backup and stays as it is on the device, which may become unreachable (F5 a, approved
  2026-11-23). "Password protection" only shows identical / differs, never restored.
  Hint: passwords are never part of a backup. Blue "Continue".
- **Removal (F4):** a chosen part is made equal to the backup; what exists only on the
  device is listed as "is removed".
- **Step 2:** value list of the changes, yellow box (backup "Before restore" is stored
  first, F5; restart, scripts), with the network tick a required checkbox; "Back" / red
  "Restore".
- **Progress (no mockup):** check list with one line per step (backup "Before restore", the chosen
  parts, restart if needed, reading the settings again): done green, running with
  spinner, waiting grey – like the lines of the installation dialog.
- **Result:** message icon, check list per part (applied and read back / not applied with
  reason / restart), hint naming the "Before restore" backup.
- **Another device:** red check line "Another device", only "Close". Device not
  reachable: red check line, only "Close" (the comparison needs the device).

## German (F17)
Backups, Jetzt sichern, Wiederherstellen, Anlass, Behalten, Vor Update, Kein Backup.
