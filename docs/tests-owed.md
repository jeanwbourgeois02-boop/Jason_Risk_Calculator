# Tests owed

User, 2026-09-29: no tests while the site is being shaped; this list is run once, when the
user says the site looks and works as wanted. Add to it instead of running anything.

## Delete (pinned to screens that were rebuilt or removed)
- `tests/test_ui_market_data.py` (the old Data tab: Pull now, chips, manual marks)
- `tests/test_header.py`: the Pull-now and `_build_chart` tests
- `tests/test_ui_blotter.py`, `tests/test_ui_blotter_commodity.py` (the old Trades tab)
- `tests/test_ui_bundles.py` (the Bundles sub-tab left)
- `tests/test_ui_risk.py`: the parts pinned to the dropped Risk cards
- `tests/test_ui.py`: stale component ids

## Fix
- Positional `INSERT INTO trades VALUES` (the trades table gained `broker_symbol`, `broker_price`):
  test_header (2), test_ui (3), test_ui_options (4), test_ingest (5), test_trades_official (1)
- `tests/test_ui_smoke.py`: tab label "Trades" → "Blotter"; the Blotter `_update` now has 3
  outputs; the drawer expectation via `blotter.fills_frame`

## Delete / fix after the performance pass (ui, 2026-09-29)
- The per-tab 15-minute safety `dcc.Interval`s left (book, curve, pnl, risk, market_data): the
  `REFRESH_ID` constants stay but no layout holds them and no callback reads them. Delete the
  assertions on them: `test_app.py` (lines ~125, ~138: `risk.REFRESH_ID` / `curve.REFRESH_ID` among
  the callback inputs), `test_ui_risk.py` (~188, ~203), `test_ui_market_data.py` (~145, ~186, the timer).
  The callbacks' signatures lost the `_n_intervals` argument (curve / risk / book `_update`, book
  `_grid`, pnl `_update`): a test calling them positionally drops that argument.
- Hidden tabs are unmounted: `ui.app.build_tab_bodies(selected)` (one argument now) returns every
  body's children (the selected tab's layout, `[]` for the others) then every body's style;
  `TAB_BUILT_ID` ("tab-built") is gone. Any test of the lazy build (`build_tab_bodies(sel, built)`,
  the store) is deleted.
- One poll: `revision._build_check` now answers only a page of an older build (input
  `LEGACY_POLL_ID`); `_poll` has 4 outputs (+ the header's day roll, 2 more, when the shell passes
  `tick`) and 4 states; `ui.app._roll_to_today` is no longer a callback of its own. `POLL_MS` 15 000.
  Tests in `test_ui_revision.py` / `test_app.py` on the poll's outputs, `_build_check` on
  `POLL_ID`, or the roll callback are re-pinned or deleted.
- `ui.tabs.curve` no longer imports `curve_positions` (it reads `blotter_pricing.shared_curve`): a
  test that monkeypatches `curve.curve_positions` patches `ui.tabs.blotter_pricing.shared_curve`
  or the engine instead. Same for `risk.limits_pass` (`shared_curve`, `shared_spreads`).
- The screens memoise per database revision (`blotter_pricing.screen_memo`: Book gather, Exposure
  gather, P&L gather and base, Risk gather, `header.needed_marks`, the shared spreads and curve):
  a test that writes to a database file and re-renders within the same second must bump the file
  (the key is its mtime), or call `blotter_pricing._clear_cache()`-style reset
  (`blotter_pricing._MEMOS.clear()`).
- Callback outputs pass through `formatting.compact` (plain rows drawn as one `dcc.Markdown`,
  class `static-block`): a test walking a callback's output for `html.Td` / `html.Li` text reads the
  Markdown's source instead; direct `render` / `render_parts` calls are unchanged. The issues
  drawer's plain items are one `dcc.Markdown` inside the `Ul` (`formatting.static_runs`): tests
  counting `html.Li` in `issues_drawer(...)` read the Markdown source.

## New rules with no test yet
- Closed-out FX options carry no currency delta (`engine/ladder/ladder.py`, `exposure_adapter.py`)
- Series weekend as-of (`engine/pnl/series.py`)
- Scorecard: closed + open = book LTD (`engine/spreads/scorecard.py`)
- Rolls: out / in split (`engine/spreads/rolls.py`)

## Known to fail on this PC only (environment)
- `test_risk.py`: no pyarrow
- `test_live.py::test_availability_no_blpapi_never_touches_the_socket`

## Then
- `py -3 -m pytest tests/ -q` once; the golden book (`tests/test_golden_book.py`) included.

## Phase G (2026-09-29): owed, not written or run (user rule: no tests until the site is final)

- Breaking now: tests/test_ui_curve.py and tests/test_ui_risk.py (old Exposure / Risk screens: delete); tests/test_app.py (curve ids, old risk ids); tests/test_ui.py (curve reference); tests/test_ui_market_data.py and the old-fills parts of tests/test_ui_blotter*.py; tests/test_blotter.py, test_commodity_ingest.py, test_bloomberg.py, test_upload.py (assert rejects / "not loaded": every row now loads); test_live.py recalc assertion (connected); test_backfill_options.py and test_auto_backfill.py (fault isolation); any positional `INSERT INTO trades VALUES` fixture (trades has 19 columns with fin_type); test_golden_book.py (the sample now has PBRoot names and 52 trades incl. 2 UNRECOGNISED; curve_positions has new keys: re-pin needs the user's yes).
- New coverage owed, by lane: trade_book (types, month-pair rule, flags, closed levels, fx_name, unrecognised legs); trade_risk / subset_var (hedge % rules, best fit, z, level_sd, vol target, legs_risk); research provenance and price check (futures, LME, FX); mark_checks (four checks, usual move); every-row-loads (UNRECOGNISED write, merge, reasons across uploads, re-resolve, NOID ids); fault isolation (live, curves, backfill); value_fn once-per-date (daily_series, curve_positions, book_positions, book_spreads); warm-up (debounce, file-stamp keys); stress by_position sums; liquidity single book_spreads.

## Display-text pass (2026-09-29, ca67759): owed, not run

- Breaking now: every test pinned to an old lowercase or "n/a" string: tests/test_ui_smoke.py:238 ("5d n/a: …" now "5d not available: …"), and likely tests/test_header.py, test_ui_formatting.py, test_ui_blotter*.py, test_ui_market_data.py, test_ui_options.py, test_ui_ranking.py (markers now "Excl. N" / "Filled N" / "Ref <date>", sizes "Long 15 lots", "None" cells now "Not held" / "No hedge").
- New coverage owed: `py 2_launcher.py ui-check` exit 0 as a smoke test; formatting.cap / tidy / plain_ids; size_words(capital=False).
