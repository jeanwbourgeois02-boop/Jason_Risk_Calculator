---
name: bnp-file-facts
description: Non-obvious facts about the BNP HA_PNL CSV learned during ingest (row counts, netting groups, blank cells, ESU6 Position=0, strategy column) and the decisions taken on them
metadata:
  type: project
---

Reference file `HA_PNL_20260818.csv` (as_of 2026-08-17, NMMF): 229 FORWARD, 9 CURRENCY, 1 FUTURES (ESU6, 27 contracts), 3 INTEREST_RATE_SWAP (skipped for now). All reconciliation tolerances in CLAUDE.md pass; observed max: Local Cost 0.696, MV Local 0.025, MV Base 14.43 USD (71m KRW leg), P&L identities ~1e-4.

Quirks not in CLAUDE.md:
- Strategy code (HAHY7/HACA) is in column `NM Strategy`; the `Strategy` column holds "Hybrid"/"Cash Adj".
- Zero-balance CURRENCY rows (EUR, XAU, HKD, one UBSI USD) have blank `Fx` and `Trade Factor`.
- ESU6 row has `Position = 0.0` but `Quantity = 27`; Quantity is the contract count.
- Multiple forwards share (account, pair, value date), e.g. 3 x USDTRY 09/16/26, so the `positions` PK forces netting (31 position rows from 239 PB rows). Price and Fx are identical within every group.
- Every DTD bucket other than Trading is exactly 0.
- Every FORWARD leg settles 2026-09-08..2026-09-21: nothing settles on or before as_of (2026-08-17), so real-file tests of the `settle_date >= as_of` boundary are vacuous; boundary coverage must be synthetic.
- CASH-USD appears in 6 accounts (6 positions rows for one ccy/date); the ladder sums them per ccy and filters `source = 'BNP'`.
- marks_official is empty after a BNP-only load; every SPOT/DELTA path is synthetic-only until bbg-data lands marks.

**Why:** these drove decisions logged in docs/open-questions.md items 17-22 and the "assumptions in force" list; PB FUTURES rows create no trades (fills come from the xlsx).

**How to apply:** when a later PB file is ingested, re-check these assumptions (esp. netting groups with differing Price/Fx, and whether Position is ever non-zero for futures). See [[build-environment]].
