# UI decisions (approved)

Binding detail decisions for the web UI; change only with approval, in the same
commit as the UI change. Read the part of the area you change; not needed for
backend-only work. Decisions that also define backend behaviour (checks, installs,
store, downloads) are not part of these files.

| Part | Content | Read when you change |
|---|---|---|
| `docs/ui/devices.md` | Devices table, sections, filter, removed devices, no usable address, Other Shellys, host name column, sorting, battery devices, action icons, device settings dialog, phone tiles, Getting started, Security strip, areas, not reachable, bug ticket text, comparison after update, CSV | Devices tab, `devices.js`, `device-settings.js`, `diagnosis.js`, `overview.js` |
| `docs/ui/firmware.md` | Firmware tab: cards, filter, unknown files, sorting, upload, downgrade icon | Firmware tab, `firmware.js` |
| `docs/ui/schedules.md` | Schedules tab: schedules card, scheduled installations card, schedule dialog, Run now, log entries of runs | Schedules tab, `schedules.js` |
| `docs/ui/backups.md` | Backups tab: cards, reasons, kept, download, import, Back up now, restore (steps, progress, result) | Backups tab, `backups.js` |
| `docs/ui/settings.md` | Settings cards, changelog hint, firmware store export/import, device passwords for several devices, setting help and download base URL hints, connection test | Settings tab, `app-settings.js` |
| `docs/ui/common.md` | Tabs, language, start section of the installation dialog, dialogs (title icons, dialog rules, tables in dialogs, message dialogs, password dialog, row menu), toolbar (search, sort, filter popovers) and card heads, beta mark, spelling, controls (field borders, heights), design tokens, tooltips | any dialog, toolbar, tooltips, `core.js`, `main.js` |
