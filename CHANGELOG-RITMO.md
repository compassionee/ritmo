# Ritmo changelog

Changes of Ritmo (the `helio` flavor). The Gadgetbridge changelog is in [CHANGELOG.md](CHANGELOG.md).
Every version is in [Releases](https://github.com/compassionee/ritmo/releases), with its APK and SHA-256.

## 273 — 2026-11-03
- Automatic backup: pick a folder once; when you open Ritmo it saves the same archive as the manual
  export (daily, weekly or monthly) and keeps the last 5. Local only.
- Optional reminders, off by default: in the evening "Mark the day as complete" if the diary is not
  marked; in the morning, on the days you choose, "Weigh yourself" if there is no weigh-in yet. Local
  notifications only.

## 272 — 2026-11-02
- Barcode scanner: a code is accepted only after 3 matching reads (4 for EAN-8/UPC-E), each checked for
  check digit, quiet zones, contrast and agreement across rows; 1280×720 frames, guide line, torch.
- After scanning, the number read is shown and can be corrected, with "Scan again". A code of one of
  your foods opens your food directly. UPC-E codes are looked up correctly.
- GS1 check digit verified on typed codes too.
- "Also save to my foods" when adding a database product; one food per barcode in any form.
- The "Add" panel starts with the 10 most recent items, then saved meals and foods by use.
- Startup timings split the page scripts and show barcode scan statistics (memory only, no digits).

## 271 — 2026-11-01
- Faster start: past days are kept on the phone and recomputed only when their data, labels, profile,
  plan or the app version change; days older than 400 are removed.
- Python starts while the screen is created; the page's requests run one at a time. On a test phone,
  opening went from 5.0 to 1.2 seconds.
- Long operations (food database import, data export/import, CSV, search speed test) no longer block
  the page and show their progress.
- Profile → About → "Startup timings", copyable, memory only.

## 270 — 2026-11-01
- Food: cumulative balance (week, last week, 28 days, month, since a date) with its range and the
  most fat that balance can correspond to.
- Food: composition estimate — change in weight, fat and lean mass with ranges (Gallagher 2000 or your
  DEXA/BIA measurement; Forbes's curve as extended by Hall 2007), energy check, protein and strength
  signals.
- Every tab simplified: one line per card, explanations and sources in "Details".
- Chart labels kept inside the charts; items still collecting data grouped in one card.
- Italian: elided dates, thousands always grouped, singular/plural everywhere.

## 269 — 2026-10-27
- Saved meals can include food database products; Ritmo asks once before saving them to your foods.
- Saving or deleting foods, recipes and saved meals keeps data written by newer versions.

## 268 — 2026-10-27
- After saving or deleting a weight entry twice in a row, the profile no longer shows the old value.

## 267 — 2026-10-27
- First public release. No network permission; food catalog installed only from a file.
