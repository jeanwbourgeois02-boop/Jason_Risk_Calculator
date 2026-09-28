---
name: screens-tidy-wave1-2026-09-28
description: The display kit of 2026-09-28 (formatting.py helpers, em dash never "n/a", real minus, tick-precision prices, plain names), the header's one picker and marks chip, the Book rebuilt as an html.Table whose Book line equals the header; the conventions wave 2 (Exposure, P&L, Timing, Risk, Data) must follow and the proof recipe
metadata:
  type: project
---

Wave 1 of the screens tidy (user approved the mocks Main / Empty / AfterUpload / Exposure .dc.html
on 2026-09-28). Conventions now binding for every tab (`ui/tabs/formatting.py`):
- `MISSING` (em dash) via `missing_cell(reason)`, never the text "n/a"; `sum_known(pairs)` for
  totals with "excl. N"; `signed_money` / `signed_number` / `money_cell` (real minus, leading "+",
  classes `cell-pos` / `cell-neg`); `price_text(value, unit, fill)` at tick precision (the unit's
  floor, the raw fill's decimals at most half a tick finer: pass a RAW fill, never a weighted
  average); `quoted_unit(root)` gives "¢/bu" / "p/therm" for price_scale 0.01; plain names
  `contract_name` / `spread_name` / `fx_name` / `lme_name` (+ `SHORT_ROOT_NAMES`); `size_words`
  ("long 15 lots", "flat" at 0); `date_cell(iso, bd, estimated, level, hover)` (grey "≈" when
  estimated; colour only for a real RED / AMBER date).
- `ranking.amount` / `amount_short` and options.py's Formats use `Sign.default`: no parentheses
  anywhere any more. Trades tab prices are TEXT columns (fill / mark / prev_close) so they sort
  lexicographically: a known trade-off.
- The header owns the one date picker (`header.DATE_PICKER_ID`, wired in ui/app.py); the Trades,
  Data and Options callbacks read `header.AS_OF_STORE_ID` "data" (options.DEFAULT_DATE_PICKER_ID
  is that store). Never add a per-tab picker or "As of" line again (rule 10 of the brief).
- Header chips: `header.marks_chip` (latest official close's stamp in NY time from
  `latest_mark_time`, the missing count from `needed_marks`; red "No marks yet · N tickers needed"
  from `library.summary`), `trades_count`; an empty database shows dashes + "No book loaded".
- The Book (`ui/tabs/book.py`) is an `html.Table`, not a DataTable (group rows need colspan and
  unit suffixes); rows are clickable via pattern ids `{"type": "book-row", "idx": <row id>}`.
  Its identity: every trade of the as-of book is in exactly one row (spread position, outright
  contract, trade row, or the one "Settled and closed out" line) and each row's Daily / MTD / LTD
  is `pnl.period_rows`' per-trade figure summed, so the Book line = the header (smoke test (d)).
  Sector order is fixed (Energy, Metals, Agriculture, Ferrous, then Options on futures, FX hedges,
  Other, Settled), not by gross exposure (would need a notional the engine does not hand over).
- The empty state's Upload button is a clientside click on the top bar's `report-file` input.
- Known gaps for wave 2: the other tabs still print "n/a" (pnl, curve, expiries, risk, options
  strips); the Book's PAIR? suggestion line has no engine source (spreads-engine review only).

**Why:** the user found the seven tabs confusing and approved this design; the identity Book =
header is what makes the screen trustworthy.

**How to apply:** proof without tests: `ui.sample_book.build_sample_db(<scratch>/w1.db,
through=today_ny())`, then `header._build_figures` inside `pricing_snapshot`, `book.render(as_of,
db, "sector"|"instrument")` (returns body, counts, toolbar style), `book.gather` + `book.book_rows`
+ `book.group_total` against `data["periods"][key].entry`, `create_app(...).server.test_client()`
for `/_dash-layout`; the scratch script was `scratchpad/verify_w1.py`. Set PYTHONIOENCODING=utf-8
on Windows before printing the em dash / minus.
