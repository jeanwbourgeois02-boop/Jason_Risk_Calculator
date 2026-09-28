---
name: book-strategy-pairs-2026-09-28
description: Book Strategy view reads strategy -> pair rows -> leg rows -> Outright -> Hedge from spreads-engine's `strategies` entry (approved design 2026-09-28); the identity rules (subtotal / no_pnl rows), the hedge coverage chip, the Daily split hover, proof recipe
metadata:
  type: project
---

User-approved definitive Book design (2026-09-28, second wave after the rejig in
[[book-strategy-rejig-2026-09-28]]): in the Strategy view each strategy group reads
strategy -> pairs -> legs -> outright -> hedge, from `book_spreads(conn, as_of)["strategies"]`
(spreads-engine's `engine/spreads/strategies.py`). What is true in `ui/tabs/book.py`:

- `strategy_leg_rows` reads `_strategy_entry(data, name)`: `pair_row` (kind "pair", id
  `PAIR-<pair_id>`, `subtotal=True`), `engine_leg_row` for each pair leg (kind "leg", part "pair",
  id `LEG-<strategy>-<n>-<inst>[-prompt]`) and each contract-shaped residual (part "outright",
  id `LEG-<strategy>-out-...`), `trade_row` for an option / FX residual, `hedge_row` (part
  "hedge", id `HEDGE-<strategy>-<inst>[-settle]`; USD/CNH futures and, since the engine update
  the same evening, the strategy's FX spot / forward / swap trades, named by `fx_name`), then
  the old `contract_rows(kind="leg")` fallback for anything open the engine did not place, and
  the strategy's settled line. Without an engine entry the earlier one-row-per-contract form.
- **Identity:** `summed_rows` drops `subtotal` rows (a pair's figures are its legs') and
  `no_pnl` rows (a contract split over two pairs lists its trades once, on the first pair; the
  other leg row shows a dash with the sentence). `group_total` and `issue_items` use it. The
  Book line still equals the header in every view (verified on the golden book, the real book
  and a labelled golden copy).
- Pair leg lots are the engine's (`leg["lots"]`; LME tonnes = lots x `root.contract_size`);
  Entry / Now / Move are the contract's off the value rows over `contract_trade_ids`; Gross /
  Net from the position's `legs[]` (`gross_usd` / `net_usd`, per strategy) only when the pair
  uses the contract whole (`open_lots` == the leg's qty), else a dash saying it is split.
- Ratio levels at 4 decimals (`level_decimals`, was 3) for every ratio row.
- Strategy line: `hedge_coverage_children` ("hedged 96 %" from `hedge_coverage_net`, 1.0 =
  fully hedged, sign-aware; dash + `hedge_reason` when None; nothing when the reason says "no
  CNY legs"; amber "hedge runs with the exposure" marker when `hedge_reason` is set beside a
  figure). `daily_split_note` on the Daily cell's hover only when `daily.reason` is empty and
  `daily.total` equals the group's Daily to the cent; else "not split: <reason>". Never render
  `daily.total` as the Daily.
- Residual marker on the pair row's Size cell is "residual +1.5 t" alone: the engine's
  `residual_usd` is the pair's net notional (residual and price basis together), so it is not
  shown as the residual's value; it is on hover and in the Net column.
- The "No strategy" group keeps the position rows (spreads / outrights / trades): the engine's
  `''` entry is not drawn as pairs, and its fallback pairs are not in the drawer.
- CSS: `.book-pair`, `.book-row--leg`, `.book-part` (the OUTRIGHT / HEDGE label rows),
  `.book-pair-type`, `.book-pair-tag`, `.marker--small`; Position column 320 px.

**Why:** the user approved the strategy -> pairs -> legs reading of the Book. **How to apply:**
proof recipe in the scratchpad `verify_book2.py` (three views on a read-only copy of
`data/raw/risk.db` and on a golden DB: identity over `summed_rows`, Book line = header, a pair's
legs' Daily = the pair's) and `verify_strat.py` (a golden copy with `trades.strategy` set by SQL
on the scratch copy, so pairs get levels with marks); `shot.py` serves a *copy* on a spare port
for a headless Chrome screenshot (the second run on the same port timed out: change the port).
