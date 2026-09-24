---
name: contract-static-in-snapshot-2026-09-24
description: Why the snapshot import applies Bloomberg's contract dates BEFORE realise_settled, how contract_static is created on the importing PC, and the fixture-count trap in test_snapshot.py
metadata:
  type: project
---

contract_static (Bloomberg's FUT_LAST_TRADE_DT / FUT_NOTICE_FIRST per commodity contract, contract-master's table) is in MARKET_TABLES since 2026-09-24 (commodity conversion Phase 1 step 2).

- Import creates it with `data.contracts.ensure_static_table` (contract-master's own DDL), not the manifest's copied DDL; the copied DDL is only the fallback.
- The import calls `data.ingest.contract_dates.apply_contract_dates` after the load and BEFORE `realise_settled`; result under `out["contract_dates"]`.
  **Why:** an upload on the non-Bloomberg PC books each future at contract-master's estimated expiry (last weekday of the month, usually later than Bloomberg's); the imported FUTURE_PX rows are keyed at Bloomberg's date, and the ledger would leave an expired future open (or freeze it against the wrong date) if the dates came after.
  **How to apply:** keep that order if the import is reworked; the test "writes_bloombergs_expiry_onto_this_pcs_future_before_the_freeze" pins it (as_of between the two dates).
- Snapshot instruments are only those with marks, and a local instrument is never overwritten, so a future's expiry on the importing PC only moves through apply_contract_dates.
- Test trap: `_bloomberg_pc` counts (2 instruments, 5 marks) are pinned in several tests and in save_after_pull message strings; add new market rows to the fixture only in tables without such pins, and put extra instruments/marks in a per-test helper.

Related: [[bbg-data MEMORY]] notes on the snapshot's MANUAL-drop rule.
