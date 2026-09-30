---
name: contract-dates-empty-state-2026-09-30
description: contract_dates_step remembers a contract Bloomberg answered with no date in <db>.contract_dates_state.json (same book day, or until the ticker changes when rejected); new status keys asked/on_file/empty/empty_tickers/known_empty
metadata:
  type: project
---

User 2026-09-30: "bloomberg needs to store on cache all the data - and only pull any new data".
Handed over by bbg-backfill: a contract whose FUT_LAST_TRADE_DT (option: OPT_EXPIRE_DT /
LAST_TRADEABLE_DT) comes back empty stores nothing in contract_static, so
`library.contract_dates_needed` lists it again and it was asked on every press.

**Rule built:** sidecar `<db>.contract_dates_state.json` ({version, empty: {contract_id:
{ticker, reason, day, rejected}}}), path from `PRAGMA database_list` (in-memory db: nothing
remembered). Skipped when the recorded ticker equals today's request ticker AND (same book day
OR rejected = Bloomberg securityError). Entries for contracts no longer listed are dropped;
a contract that later gets a date is dropped. Request failures (exceptions) are never recorded:
transient, asked next press.

**Status block** `status["contract_dates"]` gained asked (= requested), on_file (in-force
CONTRACT_DATES keys with a ticker, not listed today, present in contract_static), empty,
empty_tickers (fresh first, then known, max 20), known_empty. Known-empty contracts are NOT in
`failed` (else the step judged "failed" every press). Summary adds "; N contracts Bloomberg gave
no date for not asked again today"; with nothing else it is that sentence alone and the step is
"skipped" with it.

**Why:** hard rule 8 (only what the book needs) and the user's cache-only-new-data ask.
**How to apply:** a new book day still re-asks each empty (non-rejected) ticker once, so a
"partial"/"failed" contract-dates step once a day is expected, not a regression.
