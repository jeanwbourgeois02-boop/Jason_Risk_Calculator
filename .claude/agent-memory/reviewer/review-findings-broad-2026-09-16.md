---
name: review-findings-broad-2026-09-16
description: 2026-09-16 whole-repo diagnostic pass (post BUILD_PLAN.md rewrite) across engine/pnl, engine/ladder, data/ingest, data/bloomberg, ui; one CRITICAL (delta > vs >= conflation), two documentation-gap warnings, suite 512 passed/1 skipped
metadata:
  type: project
---

Broad read-only pass requested independent of the day's rates-pipeline/blotter-filter work. Repo has grown substantially since the last per-module reviews: BUILD_PLAN.md (authorised 2026-09-15) now defines the headline calculation in `engine/pnl/valuation.py` (`value_book`) + `engine/pnl/ledger.py` (`ltd`/`period_pnl`/`realise_settled`), superseding the old CLAUDE.md-literal `engine/pnl/pnl.py` (now Reconciliation-tab only, confirmed still wired only there via `ui/tabs/reconciliation.py`). `engine/ladder/exposure.py` + `exposure_adapter.py` are now the live Ladder-tab engine; `engine/ladder/ladder.py` (the CLAUDE.md-literal delta SQL) is now dead code for the UI, only reachable via `engine/ladder/views.py` -> `engine/ladder/valuation.py` -> the Reconciliation tab, and directly in tests.

**CRITICAL — FX delta conflates the cash-ladder `>=` rule with the delta `>` rule.**
`engine/ladder/exposure_adapter.py:122` (`records_from_db`'s `_DB_SQL`) filters
`l.settle_date >= :as_of` and feeds `engine.ladder.exposure.build_exposure`, whose
`summary`/`portfolio_totals` (Net USD, Gross USD, per-currency USD delta) sum every leg
in those records regardless of date. CLAUDE.md's data contract is explicit that the cash
ladder uses `>=` (a leg settling today is cash that moves today) but the *delta* query
must use `>` ("a leg settling on `as_of` ... carries no delta by close, matching BNP
dropping settled forwards") — and `engine/ladder/futures_delta.py:19` correctly
implements `>` for the futures side. Because `cash_ladder.py`'s `net_gross_usd()` and the
Ladder tab's per-currency USD delta rows both consume the same `records_from_db` output
that also builds the date x currency grid, FX Net/Gross/per-ccy delta (and hence the
header's "Net USD delta"/"Gross USD delta" cards, `ui/tabs/header.py:143`) overstate
exposure by any leg settling exactly on `as_of` — on any day an FX trade settles. No test
guards this boundary (`tests/test_exposure_adapter.py` has no `>=`/`>` boundary case).
Fix belongs to cash-ladder: either split `records_from_db` into two queries (grid `>=`,
delta `>`) or filter the delta-only consumers before summing.

**WARNING — undocumented "user decision" claims not recorded in docs/open-questions.md.**
Two instances found by grep (`docs/open-questions.md` has no matching text for either):
1. `engine/pnl/valuation.py:44-68` (`_reported_product`) relabels an FX_FWD trade
   settling ≤2 business days after trade_date as "FX_SPOT" for display only, citing
   "user decision 2026-09-15, item 2" — not in docs/open-questions.md or BUILD_PLAN.md.
   Changes what `product` groups mean in `period_pnl_by`/blotter group-by.
2. `data/ingest/blotter.py:52-55` (IRS direction: positive `Notional` = pay fixed) cites
   "user-confirmed 2026-09-16" — also absent from docs/open-questions.md. docs/ is
   housekeeper-owned; these decisions should land there so they survive independent of
   code comments (same pattern as the already-tracked IRS direction #7 gap).

**Verified correct (no findings):**
- `value_book`'s FX/futures formulas match BUILD_PLAN.md section 2 exactly, including
  all four worked examples' arithmetic (checked by hand: EUR fwd +8,000; USDJPY cross
  -13,422.82; EURSEK cross +19,047.62; ES futures +21,075, not divided by mark).
- No must-not-replicate pattern found anywhere live (grepped for `/ m`, `/ mark`,
  `/ price`, `WORKDAY` — only doc/test-name false positives).
- `_mark_at`/`usd_per_quote` read `marks_official` for every official (source=None) path;
  `marks_source` override is an explicit, documented value_book parameter, not a silent
  bypass. `realise_settled` (ledger.py) always reads `marks_official` directly regardless
  of caller, by design (realisation must never use a fallback source).
- Direct `marks` (not `marks_official`) reads outside tests are all legitimate,
  documented exceptions: `ui/tabs/rates.py` (BBG_BDH recon column, confirmed in the
  2026-09-15 rates-pricer review), `ui/tabs/cash_ladder.py:174/209` (BNP_BVAL/forward-
  outright fallback for the delta table, which has no P&L so this is not a headline P&L
  violation), `data/bloomberg/inventory.py:66` (Market Data tab must show every source by
  design), `data/ingest/workbook_rates.py`, `engine/ladder/valuation.py`+`views.py`
  (Reconciliation tab, unchanged), `data/bloomberg/marks_csv.py`/`data/load.py` (bulk
  read/write, not a P&L query).
- `data/ingest/blotter.py` (new file today, 506 lines): FUTURE leg =
  `signed_contracts * multiplier * price`, settle_date=expiry, settles_cash=0; FX_OPTION
  leg = signed notional in base_ccy, settles_cash=0 — both match the CLAUDE.md leg
  layout exactly. Neither product is yet read by `value_book` (FX_OPTION is explicitly
  out of scope per BUILD_PLAN.md section 7 "later"), so this is a real but expected gap,
  not a bug.
- Ownership clean: today's tracked diff (app.py, style.css, tabs/market_data.py,
  test_ui.py) is all ui-shell; untracked `data/ingest/blotter.py` +
  `tests/test_blotter.py` is data-ingest's own directory with a matching test file.
- `py -3 -m pytest tests/ -q`: **512 passed, 1 skipped**.

**Why:** first whole-repo pass since the 2026-09-15 BUILD_PLAN.md rewrite moved the
headline calculation out of `engine/pnl/pnl.py` into `engine/pnl/valuation.py` +
`ledger.py`, and split the Ladder tab onto `engine/ladder/exposure.py`
+ `exposure_adapter.py` instead of the CLAUDE.md-literal `engine/ladder/ladder.py`. Prior
per-module memories ([[review-findings-pnl]], [[review-findings-ladder]]) predate this
split and describe the now-superseded modules; do not apply their open items to
`valuation.py`/`ledger.py`/`exposure.py` without re-checking the file still exists in
that role.
**How to apply:** on the next engine/ladder or ui-shell review, check first whether the
`>=`/`>` conflation above has been fixed (search `exposure_adapter.py`'s `_DB_SQL` for a
second, `>`-filtered query or an explicit delta-only filter before `build_exposure`).
`engine/ladder/ladder.py` and its CLAUDE.md-verbatim `_DELTA_SQL` are now dead code for
the live UI — don't assume matching it against CLAUDE.md is sufficient any more; the
live path is `exposure_adapter.py` + `exposure.py`. Related: [[review-findings-pnl]],
[[review-findings-ladder]], [[review-findings-rates-pricer]].
