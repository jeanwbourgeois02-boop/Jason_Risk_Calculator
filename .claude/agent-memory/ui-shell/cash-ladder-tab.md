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
