---
name: book-one-grid-2026-09-29
description: The Book is one grouped tree table (groups → positions → legs → fills) with a view switch, chevrons, a static filter row, sibling sort, inline detail; replaces the summary tables and the Positions fold; the ids, stores, identity rule and proof recipe
metadata:
  type: project
---

2026-09-29, user's boss via the coordinator: "one big grouped table, as granular as possible in a logical way",
built for a very large screen (2560 px). Replaced in the same job a first brief (summary table + flat Open
trades list). What is true in `ui/tabs/book.py`:

- Top: tiles (Open trades, LTD, P&L today, Gross, Net; "CNH hedged · <trade>" only when exactly one trade has
  `hedge_coverage_net`), Needs you strip (≤ 4 lines: RED/AMBER expiry with a real date, MARKS → Data, PAIRS
  (legs left over + review sets) → Trades, LIQUIDITY from `engine.limits.liquidity` → Risk, hidden when empty),
  the pre-pull line, then the grid, then Last load + drawer (`book-under--stack`).
- Views (`VIEW_ID` "book-view"): Spread type (default) | Commodity | Instrument (value "contract") | Sector |
  Trade. Tree built once per view and `gather` (`grid_tree`, cached on data): Spread type/Sector/Trade =
  group → position (`open_rows`: `book_rows(GROUP_STRATEGY)` flattened, a pair keeps `leg_rows`) → legs → fills;
  Commodity = group → contract (`book_rows(GROUP_COMMODITY)`) → fills (spread name small); Instrument = contract
  → fills. Settled group last (closed by default), then the Book line.
- Every node's P&L = its fills' `period_rows` figures summed (`period_sum` over a per-trade lookup, same rule as
  `_period_of`), so every level sums to its parent and the Book line = header (Daily/MTD/YTD/LTD; PERIODS gained
  "ytd"). Filtered → Book line says "filtered".
- Static thead (titles with `{"type":"book-sort"}` + arrow spans, and the filter row `book-f-<key>`) so filter
  boxes keep focus; only `book-grid-body` re-renders. Stores (session): `book-open-rows` {base, flip},
  `book-sort-store`, `book-detail-open` (inline detail row under the clicked name). Chevron and name are sibling
  spans (a click on a Tr child bubbles to the Tr's n_clicks, so the Tr has no id).
- Links from other tabs: `data-position` = period_explain's id on the unit row (Trade-view group line for
  `POSITION-STRATEGY-<name>`, a spread `POSITION-...`, `OUTRIGHT-<inst>`, `TRADE-<tid>`); `data-member-of` on rows
  below a unit (`row_link_attrs`, `explain_ids`).
- Net / Gross lots are "as held" (options at option lots, not delta). Group lines add only one-root contracts,
  else "mixed" with the split on hover; a Commodity group shows curve-positions' `by_subsector` net_units.

**Why:** the boss wants mark-to-market per instrument and net long/short per instrument in one table.
**How to apply:** proof recipe in the scratchpad `rejig/verify.py` (sample, a labelled copy with
PBRoot names set by SQL, Jason's risk.db copy: every view covers every fill once, Book = header entry, parents =
children, filters, detail) and `rejig/shot_book.py` (attach to a headless Chrome started with
`--remote-debugging-port=<p> --remote-allow-origins=*` and a SHORT user-data-dir; Edge headless hands off to the
user's running Edge and never opens the port).
