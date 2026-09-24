---
name: ndf-removal-2026-09-24
description: Phase 2 removal (user yes 2026-09-24) took the NDF rules out of the ladder adapter; ndf.py survives only for other lanes' imports until a later brief deletes it
metadata:
  type: project
---

On 2026-09-24 (commodity conversion Phase 2, user-approved removal of NDFs, IRS and the
equity index) the adapter lost every NDF rule: records sit on their own value date, a leg
is cash when its stored `settles_cash` says so, and the realised-USD settled path reads
FUTURE and FX_OPTION only. Record keys `value_date`, `fixing_date` and `is_ndf` were
dropped (nobody outside read them); `NDF_FIXED_REASON_PREFIX` is gone. ladder.py and
views.py never had NDF / IRS / equity-index code.

**Why:** Jason's FX book is deliverable hedges; the NDF lanes are being removed top-down
by layer so nothing is deleted while another lane still imports it.

**Update, same day:** `engine/ladder/ndf.py` was deleted once every app importer had gone;
only `tests/golden_book.py` (infra's) still imported `is_ndf_pair`, due to be dropped with the
golden-book regeneration.

**How to apply:** ndf.py no longer exists; do not bring back an NDF rule. Before its deletion it had been imported by engine/pnl/valuation.py and engine/pnl/ledger.py
(fixing_date, is_ndf_pair), data/bloomberg/library.py (fixing_date) and
tests/golden_book.py (is_ndf_pair). Grep before deleting. An old database may still hold
FX legs with settles_cash = 0 (former NDFs): they show on the grid records until their
value date and then vanish (neither settled legs nor realised USD); the next upload
replaces them anyway.

Test facts: `tests/test_ladder.py`'s sample-file tests load `data/sample/blotter_sample.csv`
(tracked, synthetic) at `SAMPLE_AS_OF = 2026-09-14` (its last trade date); the synthetic
tests keep `AS_OF = 2026-08-17`. The sample loads with two parser rejects (ZCZ6 ambiguous
root, QQZ6 unknown root) under strict=False; that is ingest-parser / contract-master's.
All five of my files and both test files are LF (the cash-ladder note saying the adapter
is CRLF is stale).
