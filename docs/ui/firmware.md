# UI decisions: Firmware tab

Part of `docs/UI_DECISIONS.md` (approved, binding; change only with approval, in the same commit
as the UI change). Read when you change the Firmware tab.

## Firmware tab
- "Install" (red) behind every device a file is provided for (only those); changelog,
  download and delete icon on one line, on phones in the head of the version tile;
  changelog icon shows the changelog stored with the file (no request).
- Version labels 12 px after the version, centred (`.fw-ver` flex); label
  "intermediate" outlined yellow ("Uploaded" stays filled) with a tooltip without action
  ("Intermediate version: required step before newer firmware can be installed
  safely"); phones: date right-aligned in the size line.
- Column titles (2026-11-17, variant A): one head per card over the version rows
  (`.fw-head`, same grid, icon column 112 px without title, 82 px before the downgrade icon): Version · Size · Stored ·
  SHA-256 · Devices; "Version" left, all other titles and contents centred like the
  Devices table, size and date with tabular digits, the opened device list centred as a
  block with left-aligned lines. Phones: no head, small labels Size, Stored, SHA-256
  inside the version tile (`data-label`).
- "Upload firmware" button: upload icon of its dialog title after the text (16 px,
  `aria-hidden`, vertically centred).
- **Check rule in the texts (2026-11-21):** the tooltip of the info icon and the hint
  of the upload dialog name the rule as it is: Gen2+ – the Shelly update server decides
  which version is next, a stored file of that version (download or upload) is used
  instead of a download; Gen1 – the store comes first. An upload is used when the
  server offers exactly this version, or for a manual installation while the server
  cannot be reached; an older one allows a downgrade.
- Delete dialog: both steps with red button; the note that a file may not be
  restorable is a red warning box (`warnbox danger`, text in the normal colour).
- **Filter (3.5.0, popover since 3.8.0, mockup `3.8.0/06`):** All, Provided (provided to a device), Uploaded,
  Intermediate, Gen1, Unused (no device of the list has the firmware ID; while a
  device's firmware ID is unknown – never reached – nothing is unused and the entry's
  tooltip names the number of these devices, 2026-11-19); counts respect the search; a card shows only its matching versions. Summary "n files · x MB stored · y
  free" (files of the store only; free disk space of the store volume; MB, from 1024 MB
  on GB).
- **Unknown files (3.8.0):** files in the firmware folder the store does not list: last
  card "Unknown Files · n" with the hint "In the firmware folder, not part of the store –
  delete them when they are not needed"; per file name (monospace), size and a red
  delete icon (disabled with the reason during a check); delete dialog "Delete unknown
  file?" with a red box and the acknowledgment checkbox before "Delete". Shown under
  "All" only (search by file name).
- **Sorting (2026-11-19):** the same sort tool as in the Devices tab sorts the cards: Firmware ID, Newest version, Size (all versions), Stored (latest);
  the column titles Version, Size and Stored sort the cards too (coupled, arrow at the
  same column in every card); versions inside a card stay newest first. Kept per
  browser.
- **"For n devices" (2026-11-19, 3.5.0):** no browser marker and no arrow in the
  text; one chevron after the text (down when closed, up when open), like the Details
  button of the phone tiles but without a border.
- **Downgrade icon (2026-11-19):** history icon before the changelog icon on every
  older (not the newest) version of a Gen2+ card; disabled with the reason as title
  while "Allow downgrades" is off (same hint as the row menu) or no device of this type
  has a newer version installed. Dialog "Downgrade to <type> <version>": value list,
  section with a radio list of these devices (name · installed version), downgrade
  warning, Continue → the known second confirmation.
