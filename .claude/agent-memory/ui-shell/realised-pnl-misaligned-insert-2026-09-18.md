---
name: realised-pnl-misaligned-insert-2026-09-18
description: Root cause of the Bloomberg-PC "could not convert string to float: '<a date>'" that blanked every Blotter view and headline -- positional INSERT into realised_pnl, whose column order depends on the database's age; plus the one-bad-value-one-trade guards and the bad_stored_values scan that came out of it
metadata:
  type: project
---

2026-09-18, cross-layer diagnosis (lane widened for that task to engine/pnl/valuation.py
and engine/pnl/ledger.py).

**Root cause.** `realised_pnl` was created 2026-09-14 with 12 columns; `product` and
`mark_type` were added 2026-09-15 in the MIDDLE of the DDL, and by
`data/ingest/schema.py::_migrate_columns` (`ALTER TABLE ADD COLUMN`) at the END of any
table that already existed. `engine/pnl/ledger.py::_insert_realised` was a positional
`INSERT ... VALUES (?,x14)` in DDL order, so on an older database every value landed two
columns off: `pnl_usd` <- `spot_as_of_date` (a date string, kept as TEXT in a REAL
column), `local_amount` <- settle date, `currency` <- product. `value_book`'s
`float(row["pnl_usd"])` then raised for every view. Only ever seen with live marks,
because `realise_settled` writes nothing until an official SPOT exists -- which is why the
dev database (zero marks) rendered clean.

**The dev database has the migrated shape too** (`PRAGMA table_info(realised_pnl)` ends
`..., note, product, mark_type`), as does the Bloomberg PC's. It is the only table whose
physical order differs from its DDL order; `instruments` on the dev DB merely has 4 extra
legacy columns. Never positional-INSERT into a table `_migrate_columns` may have touched.

**Ruled out (so do not chase it again):** text reaching the DB through the blotter
upload. `data/ingest/blotter.py::_num` always returns a float or NaN, and an .xlsx whose
Price cell is a real Excel date cell imports with every forward's rate recovered from the
Description (verified: 857 trades, all `typeof(price) = 'real'`, identical prices to the
CSV import). Every `marks` writer (`live.py`, `backfill.py`, `manual.py`, `marks_csv.py`)
goes through `float()`. Manual entry uses `type="number"` inputs and `float()`.

**Which text reproduces which message** (fuzzed one column at a time, '24-Jul'):
`marks.value` and `realised_pnl.pnl_usd` give the user's exact "could not convert string
to float" and blank header + every scope; `trades.price` gives a different TypeError
("unsupported operand ... 'float' and 'str'"); `trades.quantity` /
`instruments.multiplier` give "can't multiply sequence by non-int"; `trade_legs.amount`
breaks only the header (through `cash_ladder.net_gross_usd`); `instrument_options.strike`
only the Options table; `trade_legs.rate` nothing.

**What exists now.** `valuation._number` / `_BadValue` / `_guarded_row`: one bad stored
value leaves ONE trade unpriced, `reason` = "trade <id>: <table.column> is not a number
('<value>')", bad `quantity`/`fill` shown as NaN, never the raw text. An unreadable
realised row is treated as "not yet frozen" (`_provisional` -> `_frozen_row`, same
arithmetic `realise_settled` persists) with a `note`; `ledger.realise_settled` deletes such
rows first (`purge_unreadable_realised`, returned under `repaired`) and re-freezes them in
the same call, so a corrupted database heals on the next Bloomberg pull. UI side:
`blotter_pricing.bad_stored_values` / `describe_bad_stored_values` scan every REAL column
with `typeof(...) NOT IN ('real','integer')`; used by `blotter.bad_values_notice` (red
banner on every sub-tab), `blotter._error_card(label, exc, conn)` and
`header._failure_reason`. `is_bad_value_reason` / `bad_value_note` / `bad_value_detail`
put the bad-value count in the VISIBLE "excludes N of M" caption and the full reason in
its tooltip (header.py and blotter_pricing.py both).

**Gotchas met on the way.** `scope_layout` wraps the body in `Div([notice..., body])`
whenever ANY notice fires -- a test book with an option and no `instrument_options` row
gets the missing-strike banner, so `layout.children[0]` is the banner, not the strip;
walk the tree by id/className instead. The FX sub-tab's strip has no id (it is the
layout's only `.cards` row) and tests in tests/test_ui.py need the FX DataTable to stay a
DIRECT child of the layout, so `blotter_fx.build_layout` isolates strip and table
failures with a flat children list, not nested sections. `blotter.py` had no module-level
`import logging` while `missing_terms_notice` used it (latent NameError; fixed).
See [[header-partial-pricing]] and [[blotter-strips-partial-pricing-2026-09-17]] for the
unpriced-trade conventions these captions extend.
