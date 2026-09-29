---
name: risk-four-blocks-2026-09-29
description: Risk tab rebuilt 2026-09-29 as headline / risk by position (+ liquidity) / correlation heat map / one ranked stress list; the user's cull of the old cards, tables and folds; clientside group switch with persistence; margin hidden while placeholder; proof recipe
metadata:
  type: project
---

User, 2026-09-29, after putting every Risk measure through four tests (answers a real question,
changes a decision, not derivable from another figure on screen, trustworthy): "How much can I
lose, and which trades are using up my risk?" `ui/tabs/risk.py` is now, top to bottom:
1. headline cards: 1-day VaR = `position_risk.headline_var` (fallback `book.var95_1d_usd`) and
   "Vol target used" (`book.vol_vs_target_pct`, grey "placeholder"); one reach line (commodity
   prices to <date>, research app · FX history).
2. Risk by position from `position_risk.positions` (included only): Contribution, Share, a bar
   (neg drawn left = hedges the book), standalone VaR on the contribution cell's hover only,
   "without hedge" grey marker for `partial`. Switch Position | Spread type | Commodity: all three
   tables rendered, a **clientside** callback toggles their style; the RadioItems lives inside the
   re-rendered body with `persistence=True` so the choice survives refreshes and the
   `risk-body.children` callback keeps its single output (test_app pins that key).
   Lines under: diversification, partial_note, excluded (counted by `_cause`).
   Liquidity block (`engine.limits.liquidity`, computed in `limits_pass` with the same curve /
   spreads as margin): sentence, RED/AMBER table, GREEN folded, NO_DATA one quiet line.
3. Correlation: plotly heatmap, red -1 / white 0 / blue +1, values in cells.
4. Stress: commodity scenarios + FX scenarios + the worst day in history in one list, worst
   first, 5 shown, rest in a fold; hit hardest from by_spread + by_contract not in a spread (fx
   kinds: by_currency), FX scenarios the worst currency.
Margin and limits: hidden (one quiet line) until a limit is set or margin says
`rates_placeholder` False (`margin_limits_real`). One Data issues drawer at the bottom.

Placeholder rule on the scenario kind: `s.placeholder`, else `cs.placeholder`, else True for
every non-replay commodity scenario (no engine flag yet; a Request went out 2026-09-29).
Spread type grouping reads `p.trade_type` when the engine gives it, else the broker's
`trades.trade_type` labels (the sample has none: one "No spread type" group).

**Why:** the user culled blended vol / worst-day cards, the per-underlyer table, Views, the FX
fold and matrix, the margin fold while it is all placeholders.
**How to apply:** proof recipe in scratchpad `riskwave/render.py <db> <as_of>` (text dump) and
`riskwave/shots_risk.py <url> <outdir>` (Edge DevTools on port 9338, clicks `#risk-group-by label`).
Old tests in `tests/test_ui_risk.py` pinning the cards / worst line / underlyer table / margin in
the body are to be deleted, not re-pinned.
