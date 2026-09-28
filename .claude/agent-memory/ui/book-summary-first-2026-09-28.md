---
name: book-summary-first-2026-09-28
description: Book tab reads summary first (tiles, By strategy table, Break it down by Commodity | Type | Instrument, then the Positions fold), the research app's Book shape; static placeholders + one callback, memoised gather, share = part / whole, no-marks dashes on Gross / Net
metadata:
  type: project
---

User, 2026-09-28, on Jason's real blotter after the strategy -> pairs -> legs wave: "this is
nothing like the book on the other one, where is the pnl by strat". The Book now follows the
research app's Book (`../Commodity Dashboard/rvapp/app/app.py::_book_tab`): summary first.

Vocabulary since later on 2026-09-28 ([[feedback-strategy-means-spread-type]]): strategy = spread
type, trade = PBRoot name. What is true in `ui/tabs/book.py`:
- Order: tiles (Strategies = spread types present, Trades = PBRoot names, Positions, P&L today,
  P&L since entry, Gross, Net; the two P&L tiles are `pnl.period_rows`' `entry` = the header's
  own value and markers, `_header_figure`), movers, **By strategy** (`summary_table`, id
  `book-summary`, the hero: one line per spread type over the Instrument rows, `GROUP_TYPE`,
  in the research app's order, columns Strategy, Trades (names, grey), P&L today, MTD, Since
  entry, Gross, Net, % of P&L, % of exposure, Next), **Break it down** (`breakdown_table`,
  switch `book-breakdown-by`: Trade (`GROUP_TRADE`, default: `trade_table`, one line per PBRoot
  with Strategy, Legs, Open, Hedged) | Commodity | Instrument), **Positions** (`html.Details`
  open, the Strategy | Commodity | Instrument switch; the Strategy view is `typed_groups`:
  a `strategy_tr` line per spread type, then each trade's group line, pairs -> legs ->
  Outright -> Hedge; the rows with no PBRoot name under "No trade name" per type), then
  Needs you / Last load / drawer.
- `summary_entries(data, rows, by)`: one entry per group from the same `grouped_rows` +
  `group_total` + `group_notional` the group lines use, so every summary's Book line = header
  (checked on the golden book, a labelled golden copy and Jason's book: identity in every
  view, % of P&L sums to 100). Sorted by gross largest first except the By strategy view
  (fixed order); No trade name / Unassigned / Mixed labels / Other then Settled last.
  `_share(part, whole)` is the only arithmetic beyond `sum_known`; `groups_notional` sums the
  group lines' engine figures for the Book line and a strategy line.
- Labels with a capital (`strategy_words`: "Cross exchange") on the hero, the tiles and the
  Positions strategy lines; the small grey words on a trade line stay lower case.
- Shell: the switches are **static** in `layout()` (an Input inside a re-rendered body would
  fire the callback that renders it); the one callback fills placeholders `book-body` (top),
  `book-breakdown`, `book-positions-table`, `book-under` and toggles the sections' style
  (hidden in the empty state). `parts()` returns the pieces; `body()` / `render()` assemble
  one Div for scripts and the smoke test (`TABLE_ID` still the positions table, "= header").
- `gather` is memoised on (db path, mtime, as_of) through `_gather` (`_memo`), shared by the
  body, the detail and the CSV callbacks.
- No-marks rules: every Gross / Net cell a dash (`_notional_cell`: `not marks_on_file` -> dash,
  a flat row's "$0 / $0" gone), no markers, the P&L tiles a dash with the reason; the summary
  still shows Type, Legs, Trades, Hedged (a dash + reason where the engine gives none) and Next.
- `hedge_coverage_children` rounds before formatting (no "−0 %"); `movers_strip` returns None
  when every Daily is zero.
- CSS: `.book-section-title`, `.book-section-row`, `.book-fold`, `.book-summary` (`td.book-legs`
  wraps, `td.book-type` small grey).

Found, not done: in the Commodity breakdown on a day with no exact marks, the netted contract
rows' notional (curve-positions, exact marks only) is None while flat rows are 0, so Gross reads
"$0 excl. N" (the sums-known rule); the Strategy view's engine figure reads through the fill.

**Why:** the user wants Jason's P&L by strategy at a glance, as the research app shows it.
**How to apply:** proof recipe in the scratchpad `verify_summary2.py` (three DBs: a copy of
Jason's `data/raw/risk.db`, the golden book, a golden copy with `trades.strategy` set; every
table as text, column heads asserted, Book line = header in every view, % sums, typed_groups
loses no row) and `shot2.py <db> <port> <png>` (a copy on a spare port, headless Chrome; change
the port on every run). Jason's book has no marks, so its figures are dashes; the golden copies
carry the numbers.
