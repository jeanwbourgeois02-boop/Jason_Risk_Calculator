---
name: review-findings-rates-pricer
description: 2026-09-15 review of the IRS/OIS pricer port (data/ingest/irs.py, data/bloomberg/rates_marketdata.py, engine/rates/, ui/tabs/rates.py) from the external swapcalc reference project
metadata:
  type: project
---

Full review 2026-09-15 of the multi-agent IRS/QuantLib port (data-ingest, bbg-data, new
rates-pricer, ui-shell all touched their own directories only). No criticals found.

**Verified correct:**
- IRS leg convention exactly matches CLAUDE.md: FIXED leg amount = -quantity, FLOAT leg
  amount = +quantity, both settles_cash = 0 (data/ingest/irs.py:133-136). Confirmed by
  test_ingest.py (`trade_legs ... AND settles_cash=1` = 0) and test_exposure_adapter.py
  (IRS rows excluded from FX exposure, 3 unresolved with reason "non-FX product IRS
  excluded").
- OFFICIAL_MARK_SOURCE for PAR_RATE/PV_USD/DV01_USD changed BBG_BDH -> QL_PRICER
  consistently across schema.py, CLAUDE.md, engine/rates/__init__.py docstring, and
  ui/tabs/rates.py. ui/tabs/rates.py reads marks_official for priced columns; the one
  direct `marks` read (source='BBG_BDH' filter) is explicitly for the reconciliation
  column and is correctly justified (marks_official would return QL_PRICER rows for the
  same mark_type and mask the comparison) — not a contract violation.
- Sign chain is internally consistent end to end: BNP Position>0 -> quantity>0 -> pay
  fixed -> ql.Swap.Payer -> NPV positive = asset to fund -> PV_USD mark -> PnL_USD =
  PV_USD(t) - PV_USD(trade_date). Documented as "UNVERIFIED by the PM" (direction only,
  not the sign-chain wiring) in irs.py/store.py/valuation.py/instruments.py docstrings
  and docs/open-questions.md item 7 — appropriately flagged as an assumption, not
  silently asserted.
- Ownership boundaries respected: git diff --stat + untracked file list matches the
  authorized split exactly (data-ingest: irs.py + bnp.py wiring + schema.py DDL +
  test_ingest.py + narrow test_upload.py fix; bbg-data: rates_marketdata.py + fixtures/
  + test_bloomberg.py; rates-pricer: engine/rates/* + test_rates_pricing.py + one
  requirements.txt line (QuantLib==1.43); cash-ladder: test_exposure_adapter.py only,
  no engine/ladder changes; ui-shell: ui/tabs/rates.py + blotter.py wiring + their test
  files).
- No must-not-replicate items reintroduced (grepped engine/rates for WORKDAY/hardcoded
  ranges/mark-division patterns — none found).
- Real-file coverage: test_ingest.py real-file tests parse all 3 real IRSOIS rows from
  HA_PNL_20260818.csv (not mocked); test_rates_pricing.py and test_bloomberg.py skip
  cleanly (pytest.mark.skipif) when QuantLib/blpapi aren't installed, never error;
  confirmed both ARE installed in this env and 451/451 tests pass for real (QuantLib
  1.43, 15/15 test_rates_pricing.py tests actually exercised, not skipped).

**Open items already tracked in docs/open-questions.md (not re-flagged as new):**
- #7: IRS direction sign is ported from the reference project's own convention, which
  that project itself flags as unconfirmed — still needs independent PM confirmation.
- #53: non-USD-notional IRS PV_USD/DV01_USD not spot-converted yet (Phase 1 gap,
  doesn't bite on current reference data — all 3 real IRS rows are USD notional).
- #51: rates_marketdata.py opens its own blpapi.Session separate from pull_marks.py's
  (deliberate, not merged to avoid destabilizing the working FX pull).

**Minor (warning-level, not written to open-questions):**
- data/bloomberg/rates_marketdata.py:840-864 (`_CURVE_QUOTES_DDL` /
  `ensure_curve_quotes_table`) duplicates the `curve_quotes` DDL that data/ingest/
  schema.py already owns as the canonical source, as a defensive create-if-missing.
  Column-for-column identical today; a future schema.py change (e.g. a new column)
  could drift silently since nothing keeps the two DDL strings in sync.

**Why:** this port pulled a large external codebase (swapcalc) into strict per-agent
ownership + contract conventions in one pass; worth recording that it landed cleanly so
future rates-pricer reviews can start from "sign chain and ownership were clean at
2026-09-15" rather than re-deriving it.
**How to apply:** on the next engine/rates review, start from the two tracked gaps
(#53 FX conversion, #7 direction sign) rather than re-auditing the whole sign chain;
spot-check whether the curve_quotes DDL duplication has drifted.
