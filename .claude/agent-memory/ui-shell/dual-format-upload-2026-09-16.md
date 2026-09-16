---
name: dual-format-upload-2026-09-16
description: ui/uploads.py now dispatches BNP vs blotter uploads via data.ingest.upload.detect_format; blotter path skips the as_of date gate entirely
metadata:
  type: project
---

`ui/uploads.py` upload control accepts two file shapes behind one button ("Upload trade
file", renamed from "Upload BNP report"): a BNP EOD position-report snapshot and a trade
blotter (`data/ingest/blotter.py`, owned by data-ingest). Format is detected once at
file-select time via `data.ingest.upload.detect_format(payload, filename)` (column
signature only, never filename) and stashed in a new `dcc.Store` (`FORMAT_STORE_ID =
"report-format"`), read as `State` by the Confirm callback.

Key behavior difference: a blotter carries its own per-row `TradeDate`/settle dates, so
there is **no snapshot `as_of` date** — the date-picker store and manual-date input stay
hidden for a `'BLOTTER'` detection, and `_confirm`'s `as_of`-gate ("Choose the snapshot
date before confirming.") is bypassed entirely for that branch, calling
`import_blotter(payload, filename, db_path)` directly. `'BNP'` (and `None`/legacy, for
backward safety) keeps the exact original as_of-gated `import_report` flow unchanged.

`describe_source()` was rewritten to read honestly off `ui.app.summary()`'s three
possible states rather than assuming "BNP" was the last thing loaded: `positions > 0` →
"Loaded: BNP report as of {date} ({trades} trades, {positions} positions)."; `positions
== 0 and trades > 0` → "Loaded: {trades} trades in database (no BNP position
snapshot)."; both zero → "No data loaded yet." A blotter import always leaves
`positions` at 0 (writes 0 positions rows by design), so this three-way split matters.

Test pattern for locating the `_selected` (file-picked) callback by prefix: multi-output
callback keys start with `..<first-output-id>.<prop>` — matching just the STAGE_ID
output prefix (`"..report-stage.style"`) is sufficient and more robust than trying to
match multiple output ids in sequence (Dash's key format concatenates ALL outputs with
`...` in between, not just two, so a partial multi-id startswith guess is likely to
miss — verify with a one-off `for k in app.callback_map: print(k)` if unsure rather than
guessing the exact concatenation).
