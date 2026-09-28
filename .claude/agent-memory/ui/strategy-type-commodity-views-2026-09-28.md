---
name: strategy-type-commodity-views-2026-09-28
description: Jason's real export wave (2026-09-28): Book views Commodity | Strategy | Type | Instrument with the netted-per-contract Commodity view and by_subsector net lines, the Type column and one-type-per-trade rule (book.trade_types), P&L views, Exposure grouped by subsector, Trades' Strategy / Type columns and filters; conventions and the proof recipe
metadata:
  type: project
---

Wave of 2026-09-28 after Jason's real broker export (`data/template PnL tool.csv`, git-ignored, 89
futures fills: copper COMEX + LME, zinc SHFE + LME, silver SHFE + COMEX, SGX iron ore, CME HRC,
cattle, an SGX USD/CNH FX future). User: "grouped by product type - so hrc, copper etc", "the net
exposure must be seen", sort trades by trade type and instrument type. What is now true:

- **Book** (`ui/tabs/book.py`): `GROUP_OPTIONS` = Commodity (default, `DEFAULT_GROUP`) | Strategy |
  Type | Instrument; `GROUP_SECTOR` is gone (a stale session value falls back to the default).
  `book_rows(data, by)`: the Commodity view is `contract_rows` (one row per open contract, LME per
  prompt, trades netted across strategies, kind "contract", id `CONTRACT-<inst>[-<prompt>]`); every
  other view keeps the position rows (a strategy = kind "spread" with `hand == "strategy"`, id
  `POSITION-STRATEGY-<name>`). `outright_rows` is `contract_rows(kind="outright")`. Counts and the
  movers read the position rows; the identity (every trade in one row, Book line = header) holds
  in every view (proved on both books). Group rows in the Commodity view carry the net line from
  curve-positions' `by_subsector` (`net_exposure_children`: "net long 35.6 t · long COMEX 34 lots ·
  short LME 350 t", `units_note` and USD net / gross on hover, `excl. N`); `gather` now calls
  `curve_positions` (`data["curve"]`) and reads the labels (`data["labels"]`). Columns: Position
  (strategy small in `.name-sub`), Type, Size, Entry, Now, Move, Daily, MTD, LTD, Next; group and
  Book lines `colSpan=6`. `merge_words(report)` adds "89 trades in the file: 12 added, 77 replaced,
  0 removed; 89 on file" to "Last load". A strategy's detail: entries, legs (summed `legs` when
  there are no `level_legs`) and the trade lines.
- **One type per trade everywhere** (`book.trade_types(data)`): the type of the position the trade
  is in (spreads-engine's `trade_type` / `type_source` / `type_note`; the label where the legs agree,
  else inferred), the broker's own label for a trade in no position. The Book's netted rows,
  P&L's Type view and the Trades tab's Type column all read it (`blotter.add_trade_labels(conn,
  df, as_of)` overlays it through the memoised `book._spreads`). A netted row whose trades sit in
  positions of different types shows the amber "mixed" marker (HRC Oct26 on Jason's file: SCO1
  cross exchange + STEEL term structure).
- **Display kit** (`formatting.py`): `trade_type_words`, `type_cell` (words, grey "inferred",
  amber "check" marker when the label and the legs disagree or labels are mixed, note on hover,
  dash "no type: outright"), `TRADE_TYPE_TITLES`, `HAND_KINDS` (a local mirror of
  `engine.spreads.HAND_KINDS`, so `spread_name` names a strategy by its own name), short names for
  Jason's roots (SGX:XUC "USD/CNH", CME:HRC "HRC", CME:GF / CME:LE, SHFE:ZN / SHFE:AG, COMEX:SI
  "COMEX silver"). CSS `.marker--amber`.
- **P&L**: views Position | Commodity | Strategy | Type | Instrument | Trade; `_labelled` adds
  `subsector_label` (engine.curve.subsector_name per trade leg, "FX hedges" for FX products),
  `strategy`, `trade_type`, `instrument_label` (Futures / FX futures for a sector-fx root / Options
  on futures / LME forwards / FX hedges); the Trade view has Strategy and Type columns. `GROUP_SECTOR`
  and `GROUP_PRODUCT` are gone.
- **Exposure**: the grid is grouped by `by_subsector` (`subsector_groups` in the fixed sector order,
  the sector-fx entry last as "FX hedges"), each group line `subsector_words` (net in one unit +
  delta USD gross / net over the roots, excl. N); `sector_tr` / `sector_words` are gone; CSV gains
  `commodity` (the group) and `contract_root`.
- **Trades**: `_DISPLAY_COLUMNS` has `strategy`, `trade_type` after `product`; `TOTAL_FILTER_COLS` =
  commodity, product, strategy, trade_type, status; `_fmt_trade_type`; the Type cell's tooltip
  carries the source and note.

**Why:** the user's three asks above; the engine (spreads-engine, curve-positions, ingest) landed
`trades.strategy` / `trade_type`, the `strategy` spread kind and `by_subsector` the same hour.
**How to apply:** proof without pytest: build `scratch/jason.db` (`schema.connect` + `blotter.load`
of the template CSV, no marks) and `scratch/sample.db` (`ui.sample_book.build_sample_db`), then
`scratchpad/verify_all.py` (create_app duplicate ids / outputs, every tab rendered as the smoke
test does, every Book and P&L view = header, the copper group line and the SCO1 Type cell printed).
A script named `inspect.py` in the scratchpad shadows the stdlib and breaks `import dash`. A bash
heredoc turns `"\\n"` inside a Python string into a real newline: write edit scripts with the
Write tool, not heredocs.
