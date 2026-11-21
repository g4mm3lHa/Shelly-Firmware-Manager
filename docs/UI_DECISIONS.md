# UI decisions (approved)

Binding detail decisions for the web UI; change only with approval, in the same
commit as the UI change. Read the part of the area you change; not needed for
backend-only work. Decisions that also define backend behaviour (checks, installs,
store, downloads) are not part of these files.

| Part | Content | Read when you change |
|---|---|---|
| `docs/ui/devices.md` | Devices table, filter chips, sorting, battery devices, action icons, device settings dialog, phone tiles | Devices tab, `devices.js`, `device-settings.js` |
| `docs/ui/firmware.md` | Firmware tab: cards, filters, sorting, upload, downgrade icon | Firmware tab, `firmware.js` |
| `docs/ui/settings.md` | Settings cards, changelog hint, firmware store export/import, device passwords for several devices, setting help and download base URL hints | Settings tab, `app-settings.js` |
| `docs/ui/common.md` | Dialogs (title icons, message dialogs, password dialog, row menu), toolbar and card heads, tooltips on touch | any dialog, toolbar, tooltips, `core.js`, `main.js` |
