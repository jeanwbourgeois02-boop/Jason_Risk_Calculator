---
name: cash-ladder-tab
description: ui/tabs/cash_ladder.py design -- lazy import of engine.ladder.views.ladder_table, formatting convention, dropdown/date-picker IDs
metadata:
  type: project
---

`ui/tabs/cash_ladder.py` (created 2026-09-14) is the first tab moved off the
`ui/app.py` placeholder, per [[app-structure]].

- `engine/ladder/views.py` (owned by cash-ladder agent) is imported **lazily inside the
  Dash callback body**, never at module top level, specifically `from engine.ladder.views
  import ladder_table`. Wrapped in try/except ImportError -> shows a grey status message
  in the table container instead of raising. This was written before views.py existed in
  the working tree; by the time the suite ran, the cash-ladder agent had landed it
  concurrently with matching signature `ladder_table(conn, as_of_date, source=None) ->
  DataFrame` (columns: ccy, one column per ISO settle-date ascending, total, usd) --
  confirms the lazy-import contract works both when the file is absent and once it lands.
- Tests in `tests/test_ui.py` for this tab must NOT import `engine.ladder.views` (task
  constraint) -- they build the DataTable via `cash_ladder.table_from_ladder(df)` from a
  hand-built DataFrame instead of hitting the DB/engine layer.
- Number formatting convention (`format_cell` / `format_ladder_frame`): round to whole
  units, thousands separators, negatives in parentheses (`-1234567.8` -> `"(1,234,568)"`),
  blank `""` for NaN/None. `ccy` column passes through unformatted. This is a
  display-only helper -- no P&L/rounding logic beyond display lives in ui/.
- Dropdown sentinel: `SOURCE_OFFICIAL = "OFFICIAL"` maps to `source=None` via
  `source_value_to_param`; any other dropdown value (e.g. `"BNP_BVAL"`) passes through
  unchanged to `ladder_table`'s `source` param.
- `register_callbacks(app, get_db_path)` takes `get_db_path` as an injected zero-arg
  callable (not imported at module scope) so `create_app`'s resolved `db_path` override
  is honoured by the callback too -- `create_app` passes `lambda: resolved`, not
  `ui.app.get_db_path` directly, otherwise an explicit `db_path=` argument to
  `create_app()` would be silently ignored by the callback.
- Dash's `app.callback_map` is keyed by strings like `"<component_id>.<prop>"` (verified
  empirically on dash 4.4.1: `Output('x','children')` -> key `"x.children"`), not by
  component-id objects -- useful for asserting a callback was registered without
  triggering it.
- `dash_table.DataTable` emits a `DeprecationWarning` on every construction in dash 4.4.1
  (recommends dash-ag-grid) -- harmless, expected, don't try to silence it.

Update 2026-09-14 (second session): the tab display is now a TRANSPOSE of
`ladder_table`'s frame -- rows = settle dates ascending + a `Total` row, columns =
currencies (ordered by |usd| descending, then usd=NaN alphabetically -- same rule
`ladder_table` already uses for its own row order) + a `usd_equivalent` column. Pure
transform lives in `cash_ladder.transpose_ladder(df) -> df`, applied in the callback
before `table_from_ladder`. `ladder_table` itself is untouched (not owned by ui-shell).

`usd_equivalent` blank rule (a judgement call documented in the module docstring, not
in CLAUDE.md): per date, sum over currencies of `amount x implied_spot` where
`implied_spot = usd / total` (recovered from ladder_table's own `usd = total x spot`);
blank if (a) any currency with a non-zero amount that date has `usd` NaN (no official
SPOT), (b) any such currency has `total == 0` (0/0 not recoverable even if `usd`
happens to be 0), or (c) the amount cell itself is NaN (unknown flow, distinct from
ladder_table's explicit-zero "no flow" convention). Currencies with zero/NaN amount on
a date that don't trip these conditions are simply skipped, not blocking.

`ui/tabs/formatting.py` (format_cell, format_frame) and `ui/tabs/controls.py`
(SOURCE_OFFICIAL, SOURCE_OPTIONS, source_value_to_param, build_source_dropdown,
build_date_picker) were factored out of this module once `ui/tabs/pnl.py` needed the
identical source-dropdown/date-picker pair and number formatting. `cash_ladder.py`
still re-exports `format_cell`, `format_ladder_frame` (= `formatting.format_frame`),
`source_value_to_param`, `SOURCE_OFFICIAL`, `SOURCE_OPTIONS` by importing them into its
own namespace, so existing tests referencing `cash_ladder.<name>` keep working
unchanged -- didn't rename call sites in tests/test_ui.py for those.

[[pnl-tab]] is the sibling module built on the same controls/formatting helpers.
