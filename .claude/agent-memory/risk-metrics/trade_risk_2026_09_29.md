---
name: trade-risk-2026-09-29
description: Phase G risk by trade (engine/risk/trades.py): which definitions were chosen where the brief left room (sides, 2-day moves, best-fit convention, level choice, constant-maturity splice for z), the memo pitfall, and Jason's real-book figures.
metadata:
  type: project
---

`trade_risk` / `subset_var` (Phase G, 2026-09-29) sit on `position_risk`, which gained a `detail=` hook
(series, leg_series, by_contract; output unchanged). A trade = a position_risk row (one per PBRoot name).

Choices made where the brief left room (reported, not yet contradicted by the user):
- "Legs" for hedge % / best fit = sides: one per root when the non-hedge legs cover 2+ roots, else per
  contract month. hedge_pct in PERCENT, share_of_book a FRACTION, missing = None (not NaN) in this module.
- 2-day overlapping moves when an in-series leg's country is in `asian_close_countries` and another's is
  not (SGX vs DCE stays 1-day).
- best_fit_ratio = cov(a,b)/var(b) of per-lot P&L on common settlement days; lot_ratio = -lots_B/lots_A;
  leg A = the first leg of the trade's level spec, else the larger-sigma side.
- Level = the one spreads-engine strategy pair whose legs are all open (SCO1 has 3: None, request to
  spreads-engine for one level_spec per trade). A pair with exactly one CN leg = converted ratio CN/foreign
  in USD via research USDCNH; else sum w x converted + constant (levels.converted reused).
- z history: own contract settlements only reached 41-54 days on Jason's book, so before a leg's first
  settlement the constant-maturity contract at its as_of rank is spliced (the P&L history's own rule);
  `level_own_from` / `level_note` say so. Now or entry = research settlement on/before the date.
- Memo pitfall: the first book_spreads on a db creates tables and moves the file mtime, so the memo is
  stored under the key read AFTER the build too (warm 0.02-0.04 s, subsets 15-30 ms).

The research DB on this PC is MOCK (its job table's provider is `mock`; HRC 650 vs Jason's 1,285;
research_price_check flags 7 of 8 roots). I first called it real from its date range: wrong. Always
read `CommodityHistory.source_kind` / `research_source()` before saying whose history it is. Both results
and every trade row carry `source_kind` / `source_note`; trade_risk passes `price_check` through.
Jason's export 2026-09-29 on that MOCK history (layout only, not his risk): CATTLE 44 % share, hedge 24 %,
fit 1.13 vs held 1.84, z 0.39; ZNA1 z 1.67 (ratio 1.152); COPAR3 z 1.59; SCO1 no level; SILARB1 one open
leg. subset_var(all) = book_var exactly.

**Why:** Phase G brief from the housekeeper, 2026-09-29.
**How to apply:** if spreads-engine adds a per-trade level_spec, read it first in `_level_spec`; keep the
sums of shares exact via position_risk, never recompute component VaR here.
