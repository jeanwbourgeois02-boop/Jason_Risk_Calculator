---
name: screens-tidy-wave2-2026-09-28
description: Wave 2 of the screens tidy (2026-09-28): Exposure as the mock (html.Table grid, Lots | USD delta switch, Greeks and Currency cards), P&L as one five-period attribution table with a group-by switch and the chart open first, Timing & cash's seven-column roll table and netted cash, the Risk drawer split and currency fold, the Data chart without the research curve; conventions, identities and the proof recipe
metadata:
  type: project
---

Wave 2 of the screens tidy (user approved the brief and the Exposure.dc.html mock, 2026-09-28).
What is now true of these tabs, and the conventions wave 3 must keep:

- **Exposure** (`ui/tabs/curve.py`): one `html.Table` grid (`curve-grid`, the Book's table kit),
  view switch `UNIT_ID` = `delta_lots` | `delta_usd` only (an old session value like "lots" falls
  back to delta lots). Sector lines and the Book line sum `by_commodity`'s delta USD (the
  engine's per-commodity figures) with `excl. N` naming the commodities with no USD delta; they
  never show the engine's `by_sector` None as a blank (rule 2). Gross in the Lots view is the
  month cells' absolute values summed (display; said on hover). The Greeks card lists options on
  futures per underlying (delta lots, gamma / theta / vega = per-lot official mark x lots, in the
  contract currency) and one line per FX option pair with its USD delta only (book-positions);
  an FX option's other Greeks are dashes pointing to the Trades tab, Options (a deliberate
  recommended option: the FX option Greek units were not going to be re-derived on this screen).
  The Currency card's "From" column counts the trades on file per currency (`fx_sources`, SQL
  over trades / trade_legs / instruments): forwards, spot trades, calls / puts at delta, LME USD
  legs. The non-USD futures' P&L held in currency is a separate line (it is not currency delta in
  book_positions, whatever the mock's From column implied). No chart, no sector table, no
  contracts table, no `research_curve` import.
- **P&L** (`ui/tabs/pnl.py`): `render(as_of, db, by)` with `GROUP_ID` = position | commodity |
  sector | product | trade; `gather` = `book.gather` + `period_rows` for d5 / ytd, in one
  `pricing_snapshot`. Position view = `book.book_rows` grouped by `book.GROUP_INSTRUMENT` (so
  the P&L names and groups exactly as the Book), every line's five figures via `book._period_of`.
  The Total line is the header's entry per column (`_entry_td`, the header's own markers);
  Realised / Open lines under it for LTD. The chart Details lives in the static layout, open by
  default, height forced to 220 in the callback. Proved: per-trade sums and the Position lines
  equal the header for all five periods on the sample.
- **Timing & cash** (`ui/tabs/expiries.py`): roll table = `html.Table` with seven columns (Level
  chip, In, Position (plain name), Event, Date, Lots, Exchange); `COUNTS_ID` is now the counts
  text span in the section title line ("0 expired · 7 red · ... · 20 on estimated dates"). An
  estimated row: grey `level-chip--estimated` (engine level in the hover), "≈ 3 bd", "≈ 30 Nov".
  Cash to come is netted by (date, currency) with the legs on hover. `margin_line` reads "Initial
  margin: not estimated (margin rates not set)" when the estimate covers nothing.
- **Risk** (`ui/tabs/risk.py`): `NA = MISSING`; the drawer is two drawers, "Not included (N)"
  (`ISSUES_ID`: Not included / Commodity stress / Not in the margin items) and "Notes (N)"
  (`NOTES_ID`); currency rows fold into "N currencies · no FX history on this PC" when none has
  a history (`part_records`); sector labels through `_sector_words`. The engine's scenario reason
  "<name>: n/a, ..." (engine/stress/commodity.py) is reworded to "not valued" at display time.
- **Data**: the research settlement curve left `commodity_figure`; `commodity_view(block, as_of)`,
  `commodity_section(...)` and `futures_curves_panel(...)` lost their `research` argument.
- **Smoke test**: `_drawers` accepts "Data issues (", "Not included (" and "Notes (" and the Risk
  tab may have two drawers. Tests pinned to the removed grid / chart / tables / roll columns /
  "n/a" literals were deleted (test_ui_curve.py and test_ui_expiries.py are down to a few
  survivors); nothing was re-pinned.

**Why:** the user approved the wave 2 brief (one table per tab, the header the only as-of, no
"n/a", every total says what it leaves out). **How to apply:** proof without tests:
`build_sample_db(<scratch>/w2.db, through=today_ny())`, then `curve.render(as_of, db, unit)`,
`pnl.render(as_of, db, by)` for each `by`, `expiries.render`, `risk.render`, the Data body
callback, `create_app` for duplicate ids / outputs; the script was `scratchpad/verify_w2.py`.
A `grep -c $'\r$'` inside `$(...)` in Git Bash misreports CRLF: count `b"\r\n"` in Python instead.
