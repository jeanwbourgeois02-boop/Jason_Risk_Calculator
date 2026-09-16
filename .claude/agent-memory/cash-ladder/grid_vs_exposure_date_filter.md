---
name: grid-vs-exposure-date-filter
description: records_from_db now has a for_exposure flag (>= for grid, > for delta) — ui/tabs callers must be checked/switched to the right one
metadata:
  type: project
---

`engine/ladder/exposure_adapter.py`'s `records_from_db` used a single `settle_date >= as_of`
SQL filter for everything, which is correct for the Cash ladder grid but wrong for
delta/exposure aggregation (Net USD, Gross USD, per-currency delta via
`build_exposure`/`summary`/`portfolio_totals`) — CLAUDE.md is explicit that the delta
side must use `>` (a leg settling on as_of carries no delta by close, matching BNP
dropping settled forwards, and mirroring `engine/ladder/futures_delta.py`'s `>`).

Fixed 2026-09-16: `records_from_db(conn, as_of, book_mapping=None, *, for_exposure=False)`.
`for_exposure=False` (default, unchanged) = grid rule `>=`. `for_exposure=True` = delta
rule `>`. Added `exposure_records_from_db(conn, as_of, book_mapping=None)` as an explicit
alias for the delta path so callers can't default into the wrong filter by accident.

**Why:** overstated Net USD / Gross USD / per-currency delta on any day an FX trade
settles, because the settling leg was wrongly included in exposure aggregation.

**How to apply:** any new engine/ladder code that feeds `build_exposure` /
`portfolio_totals` / `summary` must use `exposure_records_from_db` (or
`records_from_db(..., for_exposure=True)`), never the plain grid call. `engine/ladder`
itself does not own the UI call sites — `ui/tabs/cash_ladder.py`'s `net_gross_usd()`,
`ui/tabs/header.py`'s Net/Gross USD cards, and `ui/tabs/exposure.py` (build_exposure
call around line 716-723) still called plain `records_from_db(conn, as_of)` (the old
`>=` grid default) as of this fix — they need to switch to `exposure_records_from_db`
to actually pick up the corrected filter. That's outside `engine/ladder/`'s ownership;
flag it to ui-shell/housekeeper if it hasn't been done yet.
