---
name: review-findings-series-explain-2026-09-29
description: 2026-09-29 two passes over engine/pnl/series.py, period_explain, hedges, strategies._split, scorecard, carry: pass-1 warnings closed; pass 2 finds scorecard closed+open != LTD on flat non-USD ideas
metadata:
  type: project
---

Read-only reviews of the uncommitted daily series + period explain (user yes under rule 7, 2026-09-29).

**Pass 1 findings, all closed in pass 2:** weekend as-of KeyError (as-of now appended to the series);
explain realised = SETTLED only (LTD realised -18,843.39 on sample 09-18 = P&L tab); Book daily split now
runs `period_explain.classify_trades` (same bucket order, per-trade agreement proven); track record uses
header Daily rule per day (proven with an injected never-priced future: no days dropped, excl listed);
MIN(trade_date) from trades_official; NaN-with-empty-reason rows unpriced in period_pnl.

**Proven pass 2 (sample.db ro + scratch copies, mtime unchanged):** every period == header
`_priced_diff`/`_priced_single` to the cent on 13 dates incl. Sat/Sun and holidays 07-03, 09-07;
components sum to total, one bucket per trade, positions sum to total; Book strategy split == explain
per trade and bucket; FX-option hedge USD leg == ladder `_DELTA_SQL` rule per open option (USD-quoted:
-qty*DELTA*SPOT; USD-based: qty*DELTA); XAUUSD in spread bucket and a residual; carry read-only, rendered
per row only (ui/tabs/book.py), never summed.

**Open after pass 2.**
- CRIT (user call): scorecard freezes a closed idea at its LTD on the close date; a flat but unsettled
  non-USD future pair keeps re-converting daily in the engine, so closed+open != book LTD (scratch: flat
  TZT EUR pair, -$35.10). Fix: closed idea P&L = LTD on as-of (or show FX drift since close as a line).
- W: period_explain docstring lines 80-81, 99, 283-284 still say "settled or closed out"; ui pnl.py
  tooltips 106/110 the same (ui lane).
- W: no tests for series/explain/hedges/scorecard/carry; series.py not in CLAUDE.md lane table.
- S: sample.db carries DELTA marks for the CLOSED EURUSD put pair, so book_positions EURUSD includes
  -878k USD the hedge row (correctly) excludes; ladder SQL has no CLOSED filter (only bites on synthetic data).
- S: FX-option hedge size None on every weekend / before the day's pull (exact DELTA), coverage n/a then.
- S: scorecard holding_days counts an appended weekend as-of as a business day; book_day() still sums
  reason=='' rows with NaN silently (as header does).

**How to apply:** next pass, check the scorecard identity choice the user made and the docstring drift.
