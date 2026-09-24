---
name: stress-conventions
description: Conventions chosen for the commodity stress (engine/stress/), 2026-09-24: delta basis, move units, selector specificity, FX treatment, spread attribution, replay history source
metadata:
  type: project
---

Built 2026-09-24 (Phase 4), second pass the same day once risk-history and spreads-engine landed. Choices made on the housekeeper's brief, not yet seen by the user:

- Basis is first-order delta (`basis: 'delta'`): P&L = curve-positions' `delta_usd` x move, for every product. Options have no notional by design (their `notional_usd` is always None); averaging contracts carry `delta_factor` < 1 in their pricing month; LME forwards are metals rows. Using notional would have blanked every total once an option was in the book (curve-positions' request).
- Moves are fractions (-0.05 = -5 %); the loader refuses |move| > 5 as a likely percent typo (negative WTI was about -3.06).
- Specificity: spread > root > template > subsector > family > exchange > sector > all. The spread / template / family selectors come from spreads-engine's `book_spreads` (open spreads, legs with open lots). They move the named contract's whole curve position, not only the spread's lots.
- A zero move needs no price.
- FX kind: P&L = `currency_exposure` pnl_usd x move; the USD delta change is reported beside it, not counted as P&L. A CNH move stands in for CNY.
- `by_spread`: leg = open_lots x (the contract's scenario P&L / its lots). The leftover = its `usd_notional` x the root's move. Per-lot P&L differs between calendar months (different prices), so a per-lot leftover is wrong. Under a curve scenario a calendar's leftover is None ("month not known").
- Replays use `load_commodity_history().window_move_detail` (env `COMMODITY_HISTORY_DB`, else `../Commodity Dashboard/var/rv.sqlite`). It takes the contract nearest the same months out on the start close, with no roll. This PC's research history starts 2020-09-21, so the negative-WTI replay is n/a here.

**Why:** Jason's stress scenarios are a user gate (docs/open-questions.md); the defaults are a starting set for him to edit.
**How to apply:** if the user or Jason gives scenarios, edit the YAML only; revisit the delta basis if the user wants full option repricing (gamma) in stress.
