---
name: phase2-removal-2026-09-24
description: Commodity conversion Phase 2 (rates/IRS, NDFs, FX-swap package rule, equity index removed) left store.py functionally untouched; what was kept and why
metadata:
  type: project
---

Phase 2 removal pass (user approval 2026-09-24): store.py had no code tie to IRS, NDFs, swaps.py, irs_direction.py or SPX / dividend yield. Only comments named them (irs_direction guard, "the IRS pricer"), reworded.

Kept on purpose:
- `package_id` on `PricingOutcome` and the trade rows: option structures (risk reversals, strangles) group by it; it is not the FX-swap rule.
- `from engine.rates.store import snapped_at`: rates-pricer keeps it (brief of 2026-09-24).
- EQ_OPTION / CMDTY_OPTION in `set_option_terms` and the "equity digital" comment: the listed-option path stays for Phase 5 (options on commodity futures).
- The `data/raw/new_sample_trades.csv` comment by `CASH_PAYOUT_CCY`: it is the evidence for the user-confirmed BASE payout convention, not a test input.

**How to apply:** a later removal pass that deletes engine/rates/ must bring `snapped_at` (15:00 NY close stamp) into this lane or elsewhere first; `close_stamp` depends on it. See [[past-close-pricing-and-stamps]] in options-pricer's memory.
