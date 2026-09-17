---
name: no-bnp-fallback-2026-09-17
description: BNP-rate-fallback and CASH-balance-column removal from the ladder (2026-09-17, "no bnp fall back") — what changed, what broke in off-limits lanes, and the signature/shape changes future work must know about.
metadata:
  type: project
---

2026-09-17, same day as [[dollar_convention_and_gold_2026_09_17]]: coordinator follow-up
mid-task, user decision verbatim "no bnp fall back - that excel and everything linked to
it need to go" (see `docs/bnp-excel-removal.md`, owned by the housekeeper agent, for the
full cross-lane inventory). My slice: remove the BNP_BVAL SPOT/forward-outright rate
fallback from `ui/tabs/cash_ladder.py`, drop the ladder's inert BNP CASH-balance column
(`engine/ladder/ladder.py::cash_ladder`'s `positions` read), and delete
`ui/tabs/cash_ladder.py::reconciliation_panel` + its `engine.pnl.reconcile` import.

**Signature/shape changes (breaking, intentional):**
- `engine.ladder.ladder.cash_ladder(conn, as_of_date)` — the `source` parameter (used
  to pick `positions.source IN ('BNP','CALC')` for the CASH row) is GONE. Calling with
  `source=` now raises `TypeError`. Output is LEG rows only; `positions` is never read.
- `kind` column is KEPT on the output (always `'LEG'` now) even though it's logically
  redundant with only one kind left — **do not drop it** without first fixing
  `tests/test_trades_official.py::test_same_pair_from_both_sources_is_not_double_counted_in_cash_ladder_legs`
  (line ~105, `data/ingest`'s lane, off-limits to cash-ladder), which still filters
  `ladder["kind"] == "LEG"`. Dropping `kind` broke that test on the first attempt;
  restored the column as the minimal fix rather than touch another lane's test file.
- `engine.ladder.views.ladder_table`'s own `source` parameter (forwarded to
  `spot_table`, e.g. `source='BNP_BVAL'`) is UNTOUCHED — it is a generic reconciliation
  lookup into the raw `marks` table, unrelated to the live app's rate path, and still
  legitimately used by reconciliation-only tests against the real historical BNP CSV
  file (`data/ingest/bnp.py` itself was NOT deleted this pass, still blocked by
  `data/ingest/blotter.py`'s shared imports per `docs/bnp-excel-removal.md`). Don't
  confuse this `source` with the one removed from `cash_ladder` above — they were
  always two different axes (spot-mark source vs positions-row source).
- `ui/tabs/cash_ladder.py::bnp_bval_rates`, `bnp_forward_proxy_rates`,
  `BNP_BVAL_SOURCE_LABEL`, `FORWARD_PROXY_SOURCE_LABEL`, `reconciliation_panel` are all
  DELETED (not deprecated/kept-dead). `net_gross_usd()` and `_render()`'s callback body
  now call `rates_from_marks(conn)` only — a missing official SPOT stays missing, never
  substituted. `net_gross_usd()`'s `reason` string is now
  `f"no official SPOT for {as_of_date}: {ccy, ccy, ...}"`.
- `engine.ladder.exposure.build_exposure`'s per-currency MISSING_RATE message changed
  from `"no market-data rate for {ccy}; USD delta not computed"` to `"no official SPOT
  for {ccy}; USD delta not computed"` (no test anywhere asserted the old exact text —
  checked before changing it).

**Deliberately NOT touched, and why:** `ui/tabs/exposure.py`'s `fallback_ccys` /
`forward_proxy_ccys` parameters (on `headline_numbers`, `combined_frame`/
`combined_table`, `combined_risk_frame`/`combined_risk_table`, `exposure_section`) and
the "Rate source" summary row / `'*'`-suffix labelling they drive were **left in
place**, generic and now permanently dormant (always empty sets from the live
`ui/tabs/cash_ladder.py` caller), rather than removed. Reason: `tests/test_ui_ladder.py`
(ui-shell's lane, off-limits) directly unit-tests this machinery by passing a synthetic
`fallback_ccys={"JPY"}` and asserting literal `"BNP file"` text
(`test_combined_table_has_rate_source_row` line ~338,
`test_combined_risk_table_...` line ~332, `exposure_section(..., fallback_ccys=...)`
line ~361) — removing the parameters or relabelling the literal strings would have
broken those tests, which I cannot edit. Only fixed the THREE stale docstrings in
`ui/tabs/exposure.py` that named the now-deleted `ui.tabs.cash_ladder.bnp_bval_rates`
function by name (headline_numbers, combined_frame, exposure_section docstrings) —
doc-only, no behaviour change, safe.

**Left as a flagged item, not done:** `docs/bnp-excel-removal.md` also assigns
`engine/ladder/valuation.py` + `tests/test_cash_ladder_parity.py` deletion to
"cash-ladder agent" (it's pure workbook-parity code blocking `engine/pnl/pnl.py`'s
removal). NOT done in this pass — the coordinator's specific instruction this time was
scoped to exactly three things (rate fallback, CASH column, reconciliation_panel);
deleting a whole module + its test file unprompted felt like overreach given the
explicit scope. Still pending; do it next time it's explicitly asked, or ask first.

**Known, expected, unavoidable test breakage in lanes I cannot edit** (flag to
housekeeper/ui-shell every time this comes up again): `tests/test_ui_ladder.py`
(`test_bnp_bval_rates_*` x3 around line 97-130, `test_reconciliation_panel_*` x4 around
line 451-500) and `tests/test_ui.py::test_bnp_forward_proxy_rates_uses_earliest_settle_date`
(line ~319) directly exercise the now-deleted functions/attribute and will
`AttributeError`/`ImportError` until ui-shell removes or rewrites them. This is not
fixable from within `engine/ladder/**` / `ui/tabs/cash_ladder.py` / `ui/tabs/exposure.py`
/ my test globs — those test files belong to a different lane.

**Unrelated, pre-existing failures seen in the same full-suite run** (not caused by
this pass, confirmed by `git status` showing them mid-edit by another agent):
`tests/test_pnl.py::test_value_book_fallback_source_uses_latest_mark_on_or_before` and
`...test_value_book_settled_unrealised_is_provisional_only_with_fallback_source` —
`engine/pnl/valuation.py`/`aggregate.py` were modified concurrently (a new
`engine/pnl/calendar.py` appeared mid-session, matching the calendar-helper split
`docs/bnp-excel-removal.md` asks the pnl-engine agent to do). Also saw
`tests/test_ledger.py::test_holiday_shifts_prev_business_day` fail once in a full-suite
run and pass every time in isolation — order/shared-state flakiness from concurrent
agents sharing the tree, matching the exact pattern `docs/bnp-excel-removal.md`'s own
"Test run" section documents from a different agent's pass the same day.
