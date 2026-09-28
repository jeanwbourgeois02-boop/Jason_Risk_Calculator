---
name: book-strategy-rejig-2026-09-28
description: Book rejig of 2026-09-28 evening on Jason's real blotter: Strategy the default view with legs under each strategy group, Type column gone (hover + drawer), Gross / Net columns from the engine's notional, four tiles, marker hygiene with no marks, Dash 4.4's own date picker (CSS classes and format tokens)
metadata:
  type: project
---

User, 2026-09-28 evening, on Jason's real blotter in `data/raw/risk.db` (89 fills, 6 strategies):
"it kind of looks like shit", "add remove some tables rejig stuff", "he does need to be able to
sort by strategy". What is now true in `ui/tabs/book.py`:

- **Views:** `GROUP_OPTIONS` = Strategy (default) | Commodity | Instrument; the Type view and the
  Type column are gone (`GROUP_TYPE`, `NO_TYPE_GROUP`, `type_cell` no longer imported). The type is
  `type_sentence(...)` on hover of the Position cell, in words on the strategy group line
  (`.book-strategy-type`, "inferred" only on hover), and a label that disagrees with the legs is a
  "Type check" line of the Data issues drawer (`type_disagrees`).
- **Strategy view:** `strategy_leg_rows` = `contract_rows(kind="leg", strategy=name)` over the
  strategy's open trade ids (id `LEG-<strategy>-<inst>[-<prompt>]`, `row["group"]` = the strategy,
  a contract in two strategies appears under each with its own lots), `trade_row` for any other
  product in the strategy, and `settled_row(..., strategy=name)` (id `SETTLED-<name>`) for the
  strategy's non-open trades, so the group subtotal is the whole strategy. Everything without a
  strategy label stays as position rows under "No strategy". `_group_key` reads `row["group"]`
  first; inside a strategy the rows sort by exchange then month (`_curve_order`).
- **Strategy group line:** `strategy_line_children`: name, type words, " · ", `strategy_legs_words`
  (per root net open lots from the position's `legs`, LME legs are tonnes; a root that nets to zero
  says "HRC flat (long 200, short 200 lots)"; "net long 35.6 t" only when curve-positions'
  `by_subsector` line has exactly this strategy's roots at the same net, prefixed with the commodity
  when the strategy spans several), the engine's `gross_usd` / `net_usd` of the position, subtotals.
- **Gross / Net columns** after Size: a spread / strategy position's `gross_usd` / `net_usd`
  (book_spreads); an outright contract row `_outright_notional` (net summed over its outright
  trades, gross = |net|); a netted contract or leg row `contract_notional`: curve-positions' row
  `notional_usd` only when its `trade_ids` equal the row's (else the dash: "held in another strategy
  too"), 0 for flat lots, options a dash (value, not notional), FX hedges a dash. Group lines:
  `group_notional` (a strategy's own engine figure, never its legs summed); Book line
  `total_notional` over the group lines. Never lots × multiplier × mark × spot in the UI. spreads-engine
  gives no per-leg notional: requested in the Handoff (legs would fill in once it lands: read
  `leg["gross_usd"]` then).
- **Tiles** (`tiles_row`, `.book-tiles`): Strategies, Positions (rows of the view shown), Gross and
  Net exposure (`notional_total` over the position rows, excl. N). No P&L tiles.
- **Marker hygiene:** `marks_on_file` False -> no "excl. N" anywhere (`_money_td(markers=False)`,
  `_notional_cell(markers=False)`, `net_exposure_children(markers=False)`), and a partial notional sum
  with no marks is a dash (`NO_MARKS_REASON`), never the flat rows' "$0". Group labels are followed
  by " · " before their words ("Copper · net long 35.6 t"; the "Coppernet" bug).
- **Column layout:** Position, Size, Gross, Net, Entry, Now, Move, Daily, MTD, LTD, Next; group and
  Book lines: label colSpan 2, Gross, Net, blank colSpan 3, the three periods, Next.
- **Dash 4.4's date picker** (found on the way): `dcc.DatePickerSingle` renders
  `.dash-datepicker` / `.dash-datepicker-input-wrapper` / `.dash-datepicker-input` (no react-dates
  `.DateInput_input`), its calendar in a portal; `display_format` maps only D, DD, Do, YY, YYYY, dd
  and hands the rest to date-fns, so the weekday token is `EEE` ("ddd" rendered "Mo28"). The header's
  CSS block and `header.py` use these now; `controls.build_date_picker` (unused) and `.date-picker`
  CSS still name the old classes.

**Why:** the user wants to sort by strategy and see gross / net; the Type column and chips
cluttered the table. **How to apply:** proof recipe in the scratchpad `verify_book.py` (three views
on `data/raw/risk.db` read-only and on a golden DB; identity + header per view; no excl. with no
marks) and `shot.py` (serve a *copy* of the DB: `create_app` runs `create_schema`, which writes).
