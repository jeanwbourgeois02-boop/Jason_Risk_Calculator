---
name: ndf-removal-2026-09-24
description: NDFs left exposure.py / usd_marks.py on 2026-09-24 (commodity conversion Phase 2); what went, what stays, and the leftover NDF code in other lanes
metadata:
  type: project
---

User yes 2026-09-24: NDFs, rates / IRS and the equity index leave the app (commodity
conversion Phase 2, the fork for Jason's commodity book). In my files:

- `usd_marks.BASIS_NDF_1M` and the NDF_1M branch of `forward_usd_rates` are gone; the
  entry's `ticker` key (only that branch set it) is gone too. KRW / BRL / ... follow the
  ordinary rule: spot to the spot date, official outright, interpolated, flat beyond.
- `exposure.py` no longer imports `engine.ladder.ndf`. A missing rate always reads
  "no official SPOT for <ccy>; USD delta not computed"; `_suspect_reason` always names
  "official SPOT <pair>", ignoring any leftover `mark_type` / `label` on the entry.
- Kept: `COMMODITY_CCYS` (metals keep their sign, out of FX Net/Gross), the dollar
  convention (portfolio_totals = net non-USD, callers negate once), FX_SWAP in
  `_FX_PRODUCTS` (manual entry books swaps), the KRW rate-guard tests (KRW is just a
  spot currency now).

**Why:** the ladder-exposure lane must not depend on `engine/ladder/ndf.py`, which
ladder-grid deletes in a later wave.

**How to apply:** if NDFs ever come back, it is a new user decision, not a restore
from git. `tests/test_ladder.py` still holds ladder-grid's NDF tests (ndf.py,
exposure_adapter) and real-file tests on `data/raw/new_sample_trades.csv`; those are
theirs to remove / re-pin.
