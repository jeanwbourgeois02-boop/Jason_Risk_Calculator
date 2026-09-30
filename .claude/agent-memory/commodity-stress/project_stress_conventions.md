---
name: stress-conventions
description: Conventions of the commodity stress (engine/stress/): delta basis, move units, selector specificity, FX treatment, spread attribution, replay history (book db price_history since 2026-09-30)
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
- Replays (since 2026-09-30, user decision: no more research database) use risk-history's `load_commodity_history(conn).window_move_detail` on the BOOK database's `price_history` table (Bloomberg PX_LAST, about two years, filled by Pull Bloomberg now). `default_history(conn)` takes the book connection. It takes the contract nearest the same months out on the start close, with no roll. A window starting before `CommodityHistory.first_date` (read off the bound method's `__self__`) is n/a at scenario level with "No price history for <start> to <end>: before the history on file" (no length stated: the window grew to ~2.5 years, 900 days, 2026-09-30) -- so all three default replays (2020, 2022) are n/a on any real book. LME prompts are interpolated between cash and 3M pillars. To verify: build a scratch db with tests.golden_book.build_book(conn, through=today) and random-walk price_history rows for `data.bloomberg.library.risk_history_needs(conn, today)`.

- `by_position` (2026-09-29, Phase G, ui request): keyed by `positions_of` ids; a contract's scenario P&L split over its curve row's trades by booked `trades.quantity` share (linear in lots, so exact); fx scenarios split by each trade's own USD P&L (curve-positions' `currency_exposure[ccy].by_trade` if it ever exists, else a `value_book` call on the non-USD trades, checked to the cent). Unattributable parts go to `by_position_detail.unattributed` so the sum always equals `total_usd`. The synthetic sample's marks end 2026-09-18: verify on that as_of, not today (today gives no FUTURE_PX, every scenario n/a).

**Why:** Jason's stress scenarios are a user gate (docs/open-questions.md); the defaults are a starting set for him to edit.
**How to apply:** if the user or Jason gives scenarios, edit the YAML only; revisit the delta basis if the user wants full option repricing (gamma) in stress.
