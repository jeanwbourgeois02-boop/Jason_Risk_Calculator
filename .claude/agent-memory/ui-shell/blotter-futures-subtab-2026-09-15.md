---
name: blotter-futures-subtab-2026-09-15
description: Blotter gets a Futures sub-tab (own columns, BNP netted-position caveat) and near-dated FX_FWD reports as FX_SPOT; title row now matches the Ladder's
metadata:
  type: project
---

2026-09-15 (second same-day Blotter change, after [[blotter-subtabs-2026-09-15]]):

**Sub-tab order** is now Total book | FX | Futures | Rates | Options | Bundles
(`blotter.SCOPE_ORDER`). "FX" scope narrowed to `FX_SPOT/FX_FWD/FX_SWAP` only (FUTURE
moved out). "Futures" scope = `("FUTURE",)`.

**Futures is a real view, not a "not built yet" placeholder** like Rates/Options: it has
its own column set (`blotter._FUTURES_DISPLAY_COLUMNS` /
`_FUTURES_COLUMN_LABELS` — Trade date/Contract/Side/Contracts/Fill/Expiry/Status/
Settlement/P&L (USD)/Strategy/Bundle/Trade id). `detail_table` now takes optional
`display_columns`/`column_labels` args so Futures can reuse it without duplicating the
formatting logic. On this analyst's PC there are no futures *trades* loaded (BNP gives
one netted position row per contract with no fill/trade_date; fills only arrive via the
Reconciliation tab's workbook import), so the scope is always empty today —
`blotter.FUTURES_NO_TRADES_REASON` is shown in place of a hollow zero, both in the
initial `scope_layout` render and in the `derived_virtual_data` strip callback (which
re-checks the DB directly since a genuinely empty scope never gets new
`derived_virtual_data` to re-trigger it).

**"Settlement" column has no engine field**: mapped to `mark_date` as the closest
existing column since no futures trade exists yet to observe an authoritative meaning —
flagged as a default guess in the code comment, not a confirmed contract.

**`engine/pnl/valuation.py` product relabelling** (the one authorised change outside
`ui/`, alongside [[wiring-c5]]'s `period_reference_dates` precedent): `value_book`'s
`product` column now reports `"FX_SPOT"` for an `FX_FWD` trade whose `settle_date` is
at most 2 business days after `trade_date` (`_business_days_between` /
`_reported_product`, using `engine.pnl.aggregate._is_business_day` +
`load_holidays()`). `trades.product` on file is never touched — this is purely a
value_book/display relabel. The Blotter's own Product column already showed friendly
names (Spot/Forward/Swap/Future); this just makes "FX_SPOT" the value that maps to
"Spot" for near-dated forwards too.

**Title row**: Blotter's toolbar was rebuilt to match the Ladder's exactly — same class
names (`ladder-title-row` / `ladder-title-row-heading` / `ladder-title-row-right`),
reusing `ui.tabs.cash_ladder.heading_date_text` and `today_ny` (imported locally to
avoid a module-level cross-tab import cycle) rather than duplicating date-formatting
logic. `blotter._today_default` already ignored its `default_date` argument in favour of
`today_ny()` unconditionally (pre-existing behaviour, not something this change
introduced) — a title-row test must assert against `today_ny()`, not a fixed date.

**Screenshot verification quirk**: headless Edge via `msedge.exe --headless=new
--screenshot=...` hung indefinitely when many old `msedge.exe`/`msedgewebview2.exe`
processes from prior sessions were still alive (it silently attaches to an existing
instance rather than running a fresh headless one). Fix: `taskkill //F //IM msedge.exe`
and `//IM msedgewebview2.exe` first, then run with an explicit fresh
`--user-data-dir=<scratch>/edge-profile-...` and a `timeout N` wrapper (no
`--virtual-time-budget`, which does not reliably end the process either).
