---
name: period-explain
description: 2026-09-29 period_explain (P&L tab headline): bucket order (now shared with the Book's strategy Daily split), LTD rule, positions_of ids, the hedge test in hedges.py, what the sample shows
metadata:
  type: project
---

Built 2026-09-29 (user yes under hard rule 7): `engine/spreads/period_explain.py::period_explain(conn, as_of, key, series, spreads)`
over pnl-series' `period_pnl` (engine/pnl/series.py). The total is `period_pnl(...).total`, never re-summed from frames.

- Bucket order, first match wins: kind 'new trade' -> new_trades; `realised` flag -> realised; `strategies.is_hedge`
  (fx-sector root or FX_SPOT/FWD/SWAP; extracted 2026-09-29 from `_build_legs`, the one test) -> hedge; else
  `daily_split` identity -> spread + fx (rounding on spread); a row with no pnl_local/spot -> `other`, named in
  `other_trades` when >= 1 cent. **Why:** brief said a residual never goes silently into spread (daily_split itself
  puts it there, so period_explain overrides that one case).
- LTD: realised + open only, other components None with `split_reason`.
- by_position ids match the Book's rows: `POSITION-...` from book_spreads positions (open or closed), `OUTRIGHT-<inst>`
  per outright contract, `TRADE-<tid>` for everything else. Default `spreads` is built off the series' filled frames.
- Sample (2026-09-18): YTD is all new_trades (every trade dealt this year) - expected, MTD is the useful split.
- Doubts settled by the user 2026-09-29: a precious-metal pair (XAU/XAG/XPT/XPD either side) is NOT a hedge
  (a position of its own); an FX option on a currency pair IS a hedge. The one test is `hedges.is_hedge(root,
  product, base, quote, instrument_id)`; pass the currencies, or an XAU trade reads as a hedge by product alone.
- One bucket order everywhere (user yes 2026-09-29): `classify_trades(pp, roots, ccys, ltd)` is the classifier;
  `strategies._split` builds a two-frame `DailySeries` (the strategy's own Daily reference rows + as-of rows),
  runs `period_pnl` on it and classifies, so kind / realised come from pnl-series as they are. Guard: if
  `pp.ref_used` differs from the Book's Daily date, no split (a step-back there would read an empty frame).
  Verified on the sample and a labelled copy: per-trade parts identical to the explain's Daily, 0 mismatches.
- `positions_of(spreads, trade_rows)` is public (risk-metrics and scorecard import it); `_positions_of` alias.
- pnl-series changed `realised` to SETTLED only on 2026-09-29: a closed-out option group is not realised
  until expiry (sample MTD realised -5713.74 -> -1501.70: trade 910000044 moved out).

**How to verify:** open `data/raw/sample.db` read-only (`file:...?mode=ro`, PYTHONPATH=.), `daily_series` once, each key:
components (+ open for LTD) minus `period_pnl(...).total` and the by_position sum, both ~1e-11.
