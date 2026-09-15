---
name: ladder-simplification-2026-09-15
description: Ladder tab reduced to 3 headlines + 3 tables, upload rewritten to choose/confirm, BNP_BVAL rate fallback, navy/gold CSS pass
metadata:
  type: project
---

Working copy for this task was `C:\Users\jeanw\risk-monitor` (a plain git clone), NOT the
OneDrive project folder that holds CLAUDE.md/agent memory. Always check which working
copy an invocation names before reading/editing.

2026-09-15 same-day layout tightening (came as a coordinator message mid-task, overriding
the initial brief): the Ladder tab must have **zero** dropdowns/toolbars beyond the as-of
date picker, and **no** collapsed "Details" element — the old snapshot cards / metadata
line / legend / alternative views / settlement-cash transpose table are deleted outright,
not moved into a collapsed section. `ui.tabs.exposure.exposure_section` now renders only:
`headline_numbers` (Delta combined / non-forward / forward) + `combined_risk_table` +
`combined_table` (the currency/date grid with summary rows, now including a "Rate source"
row) + `futures_table`.

Rate fallback pattern: `ui.tabs.cash_ladder.bnp_bval_rates(conn, as_of_date)` reads
`marks` directly (never `marks_official`) filtered `source='BNP_BVAL'`, joined to
`instruments` on `asset_class='FX'`, `as_of_date <= given date` (latest wins) — same shape
as `data.bloomberg.live.rates_from_marks`'s currency dict, so it merges into the same
`rates` dict. Fallback currencies are tracked in a `fallback_ccys` set and threaded through
to both `combined_risk_table` (renders `"CCY *"`) and `combined_table` (adds a "Rate
source" row saying "BNP file (not Bloomberg)" vs "Bloomberg"). Never used for P&L —
this tab has none.

Visual palette went through two coordinator revisions in one task: first a neutral
palette, then overridden mid-task to dark-navy/gold (`--navy #0f1f3d`, `--gold #c9a227`).
When a coordinator message arrives mid-task with a "replaces X I gave you" framing, the
later message wins outright — do not try to merge both palettes.

`data.ingest.xlsx_futures.load_futures_fills(xlsx_path, conn)` takes a **file path**, not
bytes — the UI upload control must write the decoded payload to a temp file first (see
`ui.tabs.reconciliation.futures_import_control`'s confirm callback).

[[cash-ladder-tab]] and [[wiring-c5]] are the pre-existing memories this session built on;
both are now partially superseded by this one for the Ladder tab's exact layout.
