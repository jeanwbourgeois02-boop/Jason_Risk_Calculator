---
name: review-findings-closed-out-delta-2026-09-29
description: 2026-09-29 review of closed-out FX options out of currency delta (ladder.py / exposure_adapter.py, committed in bb0cde3 mid-review) - no criticals; open follow-ups
metadata:
  type: project
---

Closed-out FX option groups (engine.pnl.valuation.closed_out_options) leave `_DELTA_SQL`, `_OPTION_MISSING_SPOT_SQL`
and `option_records_from_db`. Reviewed 2026-09-29: no criticals. Verified on data/raw/sample.db read-only at
2026-09-18: only 910000044/45 leave (EUR 3,224,717 -> 2,469,753, USD -1,670,878 -> -792,815); json_each '[]' and
20k ids fine; LIST SUBQUERY + bloom filter (evaluated once); missing-SPOT raise still names the live option only;
part sell-back keeps both live.

**Why:** the lane committed while the review ran (bb0cde3), so check `git status` before diffing.

**How to apply / open items:**
- CLAUDE.md "Delta per currency" SQL block and "A closed-out option is not live" do not mention the delta exclusion (doc drift, session + user's yes).
- ui/tabs/curve.py `fx_sources` still counts closed-out options as "N puts at delta" in the From column.
- No test pins the exclusion (user's no-tests-per-step rule; list as found-not-done).
- Pre-existing: `_DELTA_SQL` has no `trade_date <= :as_of` (option_records_from_db has); golden fixture writes DELTA for every option on every date, so a not-yet-traded sell-back counts there.
- Pre-existing perf: `_DELTA_SQL` joins the marks_official view per trade (SCAN m) - ~1-2 s at 800 options, superlinear; the new filter costs ~0.07 s.
- Golden book: pin at 2026-09-18 moves (EUR/USD) from this change; the uncommitted golden_book.py put-DELTA sign fix moves all three days' delta_per_ccy too (JPY flips sign). Re-pin needs the user's yes.
- sample.db's put DELTA marks are positive (built with the old fixture), so figures quoted from it carry that.
Related: [[review-findings-ladder]], [[review-findings-guards-digitals-expiry-irs]].
