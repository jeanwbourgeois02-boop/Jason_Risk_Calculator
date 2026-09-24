---
name: contract-dates
description: Why contract_dates.py may re-key FUTURE_PX marks (the one marks write outside Bloomberg lanes) and when the upload re-applies Bloomberg's contract dates
metadata:
  type: project
---

`data/ingest/contract_dates.py::apply_contract_dates` (2026-09-24, commodity conversion Phase 1 step 2)
moves a commodity future's expiry from contract-master's estimate (last weekday of the month) to
Bloomberg's stored `FUT_LAST_TRADE_DT` (`contract_static` table), with its NOTIONAL legs and the
settle_date key of its FUTURE_PX marks. It is the one write to `marks` outside the Bloomberg lanes and
is allowed only because it moves a key, never a value (a row already under the new key wins).

**Why:** FUTURE_PX is keyed on settle_date = expiry, so a moved expiry without moved marks would orphan
the price history. The parser books the estimate again on every upload (the instrument upsert overwrites
expiry_date), so the upload must re-apply the dates after publishing and before the library sync.

**How to apply:** only FUTURE rows whose base_ccy is a contract root (`data.contracts.load_roots`) are
in scope; the old ES/macro futures are ignored. Callers: bbg-live after `store_static_dates`, and
`upload._contract_dates_sentence` (never fails an upload). The trade_legs UPDATE fires the bbg_library
dirty trigger. Related: [[upload-full-replace]].

Since 2026-09-24 the parser itself reads `contract_static` (`blotter.load` hands its conn to `parse`);
the upload's staged in-memory DB is a whole-file `backup()` of live, so it sees the table and the
post-publish `apply_contract_dates` normally finds nothing to move (a test pins that).
