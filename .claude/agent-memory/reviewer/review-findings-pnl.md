---
name: review-findings-pnl
description: Status of findings from the 2026-09-14 reviews of engine/pnl (pnl.py ltd_per_trade, aggregate.py period_pnl/book_totals) and ui; C-1 (trade_date filter) closed on third pass, warnings 2-7 still open
metadata:
  type: project
---

Reviews on 2026-09-14 of engine/pnl/pnl.py, engine/pnl/aggregate.py, ui/app.py, ui/tabs/cash_ladder.py. Per-trade maths, signs, spot conversion, marks_official reads, Net/Gross sign rule and gold/futures exclusion are all correct; ownership clean; suite 135 passed.

**C-1 CLOSED (third pass 2026-09-14):** `_TRADE_SQL` now has `AND t.trade_date <= :as_of` (inclusive, so trades dated as_of are in LTD(as_of) per the Trading P&L rule). Regression test `test_daily_pnl_excludes_trades_dated_after_reference_date` verified to fail against the pre-fix pnl.py (NEW appears in LTD(t-1bd)) and pass after; suite 136 passed. Original finding kept for context:
- `period_pnl` computes LTD(ref_date) via `ltd_per_trade(conn, ref_date)`, whose `_TRADE_SQL` filters only `settle_date > :as_of`, never `trade_date <= :as_of`. A trade done ON as_of is therefore included in LTD(t-1bd) at t-1 marks, so its daily P&L becomes m_t - m_ref instead of m_t - f. Verified numerically: one USDJPY trade dated 2026-08-17, fill 147, mark 148 (spot 147) / ref mark 146 -> ltd 6,803 but daily 13,652. Fix belongs in pnl.py (`AND t.trade_date <= :as_of`) or in period_pnl. Same defect would hit MTD/YTD for every trade done since the reference date. The existing test `test_period_pnl_daily_crosses_weekend_others_nan` uses trade_date 2026-08-01 so it cannot see this.

**Open WARNINGs (pnl.py, carried):**
2. No synthetic test that `source=None` ignores a BNP_BVAL row when both sources exist for the same key.
3. Grep guard `/\s*(mark|m|spot)\b` is name-based and easily evaded.
4. `_TRADE_SQL` uses `leg_no = 1` only; FX_SPOT / FX_SWAP excluded silently. Consequence in aggregate.py: FX_SWAP USD legs (near+far) would net to ~0 usd_notional if swaps ever enter.
5. Cross pairs with XXXUSD-quoted quote ccy get NaN spot; in `aggregate_by_pair` a cross with no USD leg gets usd_notional 0 (groupby sum skips NaN), not NaN.
6. Per-settle-date marking only covered by real-file USDKRW.
7. Trades whose settle_date falls between ref_date and as_of drop out of LTD(t) entirely (realised P&L vanishes from daily) -- contract-level question, not a code deviation; worth an open-questions entry.

**Closed:** matured-trade exclusion test (second pass); C-1 trade_date filter (third pass). Technique that worked for verifying 'would the test have failed before': copy engine/ + tests/ into scratchpad, drop the pre-fix module in, symlink data/, run pytest -k there.

**UI:** summary computed once at create_app; cash-ladder tab lazy-imports views.py inside the callback (verified tests never import views.py); `format_cell` verified.

**Why:** the parent agent wants explicit closed/open status per finding on re-review.
**How to apply:** on the next engine/pnl review go straight to items 2-7 (esp. 4: leg_no=1 / FX_SPOT+FX_SWAP silently excluded, and 7: trades settling between ref_date and as_of vanish from daily). Related: [[review-findings-ladder]], [[bnp-file-facts]].
