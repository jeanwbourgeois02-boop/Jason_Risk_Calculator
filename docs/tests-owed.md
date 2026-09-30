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

## Layout waves 1-2 (2026-09-29, 398e4a8, 549fc4c): owed, not run

- Delete, don't re-pin (pinned to rebuilt screens): tests/test_ui_formatting.py 80-105 (drawer `Li` / `issue-label` structure; the drawer is now a Kind | Where | Reason table); tests/test_ui_risk.py (old headline, the not-set `Li` list, price-check "What it means" column; :480 drawer `Li` rows); tests/test_ui_market_data.py (library summary, missing_panel, diagnostics_panel, "ticker(s)"); tests/test_ui_blotter.py 253-369 (`blotter-strip-options`, "Portfolio Totals"); tests/test_ui_options.py 401, 409, 527, 937, 1249-1263, 1335, 1676, 2056 (old DISPLAY_COLUMNS, headline cards, "Portfolio Totals").
- New coverage owed: `py 2_launcher.py ui-check` exit 0 as a smoke test (strict layout included); formatting.issues_drawer triples and same-reason merge; row_info / head_info; P&L split cells blank when empty; options Status / Size / Delta text.

## Book, header and look pass (2026-09-30, e666153, 8c2f0f0 and the brackets commit): owed, not run

- Re-pin or delete: tests pinning "+42.1k" / "−$51.0k" / "USD −6,928" money text, k / m size words ("2.0m USD") and the old Data title (tests/test_header.py, tests/test_ui_smoke.py, tests/test_ui_formatting.py); tests reading `layout().children[0]` of book / pnl / risk / blotter (each now opens on a `tab-header` title).
- New coverage owed: `formatting.paren` / `count_text`; money in brackets, no "+", full figures; CSV keeps plain "-"; the 17:00 NY roll `header.as_of_after_tick` (its picker test was deleted) and `header.as_of_from_query` (`?as_of=`); Book `size_text` forms and `flag_name`.
- Book levels (2026-09-30): re-pin tests/test_spread_levels.py:199 `test_an_entry_whose_fx_is_not_on_file_is_na_with_its_reason` (the entry is now estimated from the near spots, `level_estimated["entry"]` names it; blank only with no spot of that currency on any date). New coverage owed: template levels (3-2-1 crack, board crush), options-only premium levels, the `*_estimated` flags; Book "Size (lots)" and the unit on Now only.

## Launcher self-repair (2026-09-30): owed, not run

- Fix: tests/test_risk_cli.py::test_start_reexecs_in_venv_when_outside must also stub `venv_python_broken` (on a PC with no real `.venv\pyvenv.cfg` it falls back to the mocked call and runs setup).
- New coverage owed: `venv_python_broken` / `_venv_base_present` (cfg names a missing Python, `home` only, no cfg); `cmd_start` rebuilding a broken venv and rebuilding once when the imports fail after pip; the packages stamp written only after setup's import check; the `chelsea` block's PATH refresh and `.venv` fallback.

## LME monthly ticker, two-digit year (2026-09-30): owed, not run

- `engine.lme.monthly_ticker` now builds 'LPV26 Comdty' (Bloomberg's LME page), not 'LPV6'. Re-pin the one-digit literals: tests/test_lme.py:187,192 ('LPV26', 'LPZ28'); tests/test_ticker_check.py:536,549,572; tests/test_live.py:1942-1945 ('LPZ26 Comdty'); tests/test_backfill_commodity.py:387 ('LPX26 Comdty').

## Bloomberg errors in one place, parsing and Bloomberg checks (2026-09-30): owed, not run

- Re-pin to the pointer wording ("See Bloomberg on the Data tab"; the top bar a tab-link button, not a span): tests/test_header.py::test_reference_reason_keeps_its_head_and_the_needed_marks_detail, ::test_blotter_strip_missing_close_sentence_says_the_same_thing; tests/test_uploads.py::test_pull_button_with_no_feed_says_so_plainly_and_asks_for_nothing, ::test_poll_gives_up_after_the_timeout_instead_of_spinning_forever ("has not reported" is now NO_REPORT_HEAD); tests/test_ui_market_data.py::test_not_connected_line_says_what_the_press_did_in_the_pulls_own_words_said_once (the not_connected_message part); tests/test_ui_options.py::test_a_failed_options_step_is_named_on_every_leg_it_left_unpriced.
- New coverage owed: `data.ingest.parse_check.check_file` (every row status, merge counts, read-only on the database, the parse lock); `ticker_check.check_book` with a fake client (unreachable, refused ticker, no answer, progress); the Data tab's Bloomberg card (pull problems only there) and the diagnostics runner (one run at a time, refused during a pull).
- Delete (the code left): tests/test_ui.py::test_render_bbg_results_shows_status_name_and_message, ::test_render_bbg_results_handles_empty_list, ::test_run_bloomberg_diagnostics_safe_never_raises_and_returns_list, ::test_run_bloomberg_diagnostics_safe_rejects_non_list_result (replaced by `data_checks.connection_table` and `ui/diagnostics_runner.py`). Already stale before: tests/test_header.py:626 ("Pull now" on the Data layout), tests/test_ui_market_data.py:1121 (`MANUAL_INSTRUMENT_ID`).
- Re-pin (bbg-curves): tests/test_bbg_diagnostics.py::test_check_unverified_assumptions_warning_when_no_record_yet expects the Warning on a book with no FX options; it is now Pass ("No FX options in the book; nothing to check."). Give the test an open FX option so the Warning stays covered, and add the no-FX-option Pass.

## Book By contract view, P&L Contract slice (2026-09-30): owed, not run

- Re-pin to the renamed P&L slice and headings (Trade → Spread, Type → Strategy, Commodity → Commodity family; the P&L CSV's "Type" column is now "Strategy") wherever a test names them.
- New coverage owed: `ui/tabs/book_contracts.py` (one row per contract, never netted across exchanges, a net-zero contract kept, the clearer map and a two-clearer split, the total equal to the trade view and the header); the P&L Contract slice (every fill once, each row's parts add up, the total equal to `period_pnl` for every period).
- infra: `tools/ui_check.py` should also render the Book's By contract view (`view="contract"`) and the P&L Contract slice (`state={"group": "contract"}`).

## Upload result box in two lines (2026-09-30): owed, not run

- Delete (the box was rebuilt, not re-pinned), in `tests/test_uploads.py`: `test_headline_keeps_whatever_the_list_does_not_reproduce`, `test_notes_render_as_a_list_under_the_headline_with_nothing_lost_or_doubled`, `test_real_ingest_report_on_the_sample_names_its_two_rejects_and_stays_open`, `test_real_ingest_report_on_the_clean_sample_is_structured_clean_and_lists_its_notes`, `test_real_ingest_report_with_one_unreadable_row_is_sticky`.
- New coverage owed: `report_result` on a clean report (one line, --info) and a problem report (two lines, --warning, the Data tab link).

## Rebuild on `price_history` (Phase H, 2026-09-30: no research app, no nm-dashboard folder)
- `tests/test_risk_history.py`: every test errors at setup (the `_no_env` fixture deletes the removed
  `ENV_VAR`); the history.py tests use the parquet folder (`FILES`, `SPOT_FILE`); the commodity tests
  build the research app's `price_daily` / `fx_daily`. Rebuild on synthetic `price_history` rows;
  delete the four `research_curve` tests (the function is gone).
- `tests/test_risk.py` (risk-metrics): the parquet fixtures (`history_mod.SPOT_FILE`, `history_dir`,
  `spot_last_date`, `RISK_HISTORY_DIR`) and the commodity section's `write_research_db` / `research`
  fixture (~490-870); rebuild on `price_history`.
- `tests/test_commodity_stress.py`: `_research_db` and the two replay tests setting
  `COMMODITY_HISTORY_DB`; rebuild on `price_history` (a replay before the history reads
  "No price history for <start> to <end>: before the history on file").
- `tests/test_spread_levels.py`: its `research_id` / `research_instance` checks, if those fields go.
- `tests/test_ui_risk.py`: pinned to the old Risk layout (seven columns, Price check fold); delete
  what the slim Risk tab no longer has.
- `tests/test_research_spreads.py`: deleted with its module.
- `tests/test_ui_risk.py`: its 11 tests of the old Risk layout (the two cards and worst-day line, the
  margin and limits tables, the not-set drawer) are deleted, not re-pinned; the Risk parts of
  `tests/test_ui_curve.py` likewise. `tests/test_ui_smoke.py` still calls `risk.render` (kept).
