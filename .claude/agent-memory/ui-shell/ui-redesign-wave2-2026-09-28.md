---
name: ui-redesign-wave2-2026-09-28
description: UI redesign wave 2 (2026-09-28): Exposure (curve.py), P&L (new ui/tabs/pnl.py), Timing & cash (expiries.py) built by one agent; header LTD chart moved to P&L; Trades Summaries dropped; what was verified without tests, what to re-pin, gotchas
metadata:
  type: project
---

Wave 2 of the six-tab redesign (see [[ui-redesign-wave1-2026-09-28]]) was one agent owning
ui-shell + ui-header + ui-curve + ui-expiries + ui-blotter files (lane rule set aside by the
user for the job, "no tests per wave" still in force). Tab order after it: Book, Exposure
(key `curve`), P&L (new key `pnl`), Timing & cash (key `expiries`), Risk, Trades, Spreads,
FX & cash, Data; wave 3 retires the last three.

What landed: Exposure = Curve tab with delta lots as the default view, an "Option Greeks"
section (delta from curve-positions' `delta_lots`; gamma/theta/vega = official per-lot mark x
lots, one convention, summed per underlying) and "Currency and FX exposure"
(`blotter.fx_positions_table`, split out of `positions_table`); the Net-outright-by-sector and
Commodities tables were NOT added again (curve.py's `sector_section` and grid already show the
same figures from the same engine). P&L tab (`ui/tabs/pnl.py`): period selector, total = the
header's own `_priced_single`/`_priced_diff` entry, per-trade period figures by the same
split (`period_rows`), attribution by spread position / commodity / sector / product (the
Trades tab's `asset_class_pnl_table`, imported) / trade, realised vs open for LTD, the LTD
chart (`header._build_chart` rendered under pnl's own Details + clientside open-mirror). Timing
& cash: a Plotly timeline of the next 60 business days (one row per event kind, level colours,
estimated hollow, cash legs navy) above the roll table, plus a Cash section (settled cash by
currency from `settled_records_from_db`, dated cash legs from `records_from_db(include_settled=
False)`, `margin_estimate` book line, tab link to FX & cash).

**Why:** the user wants six screens, one question each; the engine and every figure unchanged.

**How to apply:** verification without tests = `ruff check ui/`, `import ui.app`, `create_app`
on a scratch DB built with `tests.golden_book.build_book` (backup to a file; `sqlite3.connect`
+ `mem.backup(disk)`), direct `render(as_of, db)` calls, and checking the P&L tab's per-trade
sums equal the header entry per period. Gotchas: the synthetic sample prices every day, so
deleting a whole day's marks does NOT force a step-back (the near-marks rule interpolates in
time); delete an instrument's marks entirely to get an n/a row. The dev `data/raw/risk.db` is
the old macro book with zero marks. Tests to re-pin at batch end: test_header.py (no
DETAILS_ID/chart in the header layout or callbacks), test_ui.py/test_app.py (TAB_KEYS order and
labels), test_ui_blotter*.py (no Summaries; `positions_table` still exists), test_ui_curve.py
(DEFAULT_UNIT delta_lots, UNIT_LABELS, title Exposure, `body(..., extra)`), test_ui_expiries.py
(title, timeline + cash sections, `body(result, cash)`). Deferred: P&L by contract month.
