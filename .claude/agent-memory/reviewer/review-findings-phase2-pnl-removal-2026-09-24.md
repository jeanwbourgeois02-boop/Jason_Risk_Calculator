---
name: review-findings-phase2-pnl-removal-2026-09-24
description: Phase 2 IRS/NDF/equity-stress removal from engine/pnl (valuation, ledger, stress) reviewed 2026-09-24; no criticals; retired-row guard gaps
metadata:
  type: project
---

Phase 2 commodity conversion: IRS + NDF valuation, IRS/NDF freezes, EQUITY stress move removed from engine/pnl (working tree on top of e660974). No critical: the 8 valuation pins and 9 ledger pins reproduce bit for bit on a `git archive HEAD` copy (verified by loading the working tests' pin books against the HEAD engine).

Open findings at review time:
- W: `ledger._retired_row` only catches `product='IRS'` or `mark_type in (NDF_FIX, PV_USD)`. An NDF frozen at the fixing-date SPOT substitute (mark_type 'SPOT', spot_as_of_date = fixing date, spot_usd_per_local = 1/FIX) is re-frozen by the deliverable FX rule: probe USDBRL b1 1m @5.20, SPOT 09-14 5.25 / 09-16 5.35, no fix -> 9,523.81 -> 28,037.38, reported under `refrozen` with a why that reads like a new close ("SPOT dated 2026-09-16 replaced the one dated 2026-09-14").
- W: "kept until the next upload replaces the book" is false for MANUAL trades (they survive uploads); a leftover open IRS just vanishes from value_book with no reason row (hard rule 2 "never a silent drop").
- N: purge_unreadable_realised runs before the retired guard, so an unreadable retired row is deleted (IRS never re-frozen; NDF re-frozen deliverable).

**Why:** future reviews of the remaining phases (pricers, market data, ingest) will touch the same retired rows.
**How to apply:** when re-reviewing, check whether the retired guard learned to spot NDF rows by `instruments.is_ndf` / note "(NDF fixing)" rather than mark_type alone. Probe recipe: scratchpad probe.py with root arg (HEAD copy vs working tree), write DB to a file with HEAD, re-open with working tree. Related: [[review-findings-ndf-fix-2026-09-22]].
