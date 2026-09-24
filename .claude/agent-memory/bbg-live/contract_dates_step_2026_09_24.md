---
name: contract-dates-step-2026-09-24
description: Commodity Phase 1 (2026-09-24) - live.contract_dates_step asks FUT_LAST_TRADE_DT/FUT_NOTICE_FIRST before build_requests, stores via data.contracts, calls ingest-booking's apply_contract_dates; status["contract_dates"] / ["not_requestable"] shapes; library API it relies on; test fakes and traps
metadata:
  type: project
---

Housekeeper brief 2026-09-24 (commodity conversion Phase 1 step 2). Commodity futures carry an
ESTIMATED expiry (last weekday of the month) until Bloomberg's dates are stored; FUTURE_PX is
keyed on that expiry, so the dates must land BEFORE the price request.

**Order in pull_once (connected path only):** connect -> `contract_dates_step` (opens the
shared session only if something is to be asked; sets session_opened) -> `build_requests` ->
`not_requestable_futures` -> the rest. apply_contract_dates updates trade_legs -> the schema
trigger marks bbg_library dirty -> build_requests' needed_on resyncs and reads the moved
settle dates. No terminal: nothing asked, nothing applied, no block written.

**Library API used (bbg-library landed it the same day):** `library.contract_dates_needed(conn,
as_of)` -> [{contract_id, bbg_ticker, fields, needed_until, trades}] (open, contract root,
verified ticker, dates NOT yet in contract_static - the library filters met needs itself);
rows carry `requestable` / `reason`; `needed_on(..., include_unrequestable=True)` shows the
blank-ticker futures (reason "no verified Bloomberg ticker for NYMEX:XX"). The apply runs
whenever any CONTRACT_DATES row is in force (met or not, read from `library.rows`), so a
stored-but-unapplied future still moves; a book with no commodity future pays nothing (a
test pins timings["futures"] == 0.0 for such a book).

**Status shapes (ui-market-data reads them):**
- `status["contract_dates"]` = {requested (tickers asked), stored, failed [{ticker, reason}],
  applied (apply's dict, {} if not run, {"error"}), summary ("N contract date(s) stored, M
  future(s) moved to Bloomberg's expiry" [+ "; K tickers gave no date"]), error? }.
- `status["not_requestable"]` = [{instrument_id, settle_date, trade_ids, reason}] + a warning
  line each; never in `items` / `requested`. build_requests also drops any blank-ticker row.
- Timing under timings["futures"] (TIMING_KEYS unchanged: tests pin the nine keys).

**Tests:** tests/test_live_contract_dates.py fakes apply via
`monkeypatch.setitem(sys.modules, "data.ingest.contract_dates", module)`; futures need
base_ccy like 'NYMEX:CL' (library's contract-root regex) to get CONTRACT_DATES rows.

**Traps:** status["warnings"] is overwritten by `status.update(warnings=...)` late in the
connected path - earlier warnings must be folded into that list. pytest start-up on this PC
took minutes while other agents ran suites in parallel; the tests themselves take seconds.
