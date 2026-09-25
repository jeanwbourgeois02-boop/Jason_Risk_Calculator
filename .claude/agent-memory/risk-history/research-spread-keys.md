---
name: research-spread-keys
description: rv.sqlite spread_def / spread_stats conventions research_spreads.py relies on: calendar id and instance rule, tracked instances, units in quote units, template ids missing
metadata:
  type: project
---

Checked 2026-09-25 on the dev PC's mock `../Commodity Dashboard/var/rv.sqlite`:

- **Calendar ids** are `cal.<root_id lower, ':'->'_'>.<near>_<far>` ('cal.nymex_cl.z_f'); for all 1194 calendar defs this equals `exchange_exchangecode` from `config/contracts.csv` too. Only 141 of 202 roots have calendars (no ICE:NBP, for one). Only adjacent pairs (6 months out for CL) plus a few named ones (m_z, z_z): a non-adjacent book calendar (Z/H) is not in the universe.
- **Instance = the NEAR leg's year**, 4-digit text ('2026' for CLZ26/CLF27; `far_year_offset` 1). Templates use instance ''.
- **Stats only for tracked instances** (the near ones): 879 of 1194 calendar defs have no stats row at all; `spread_daily` keeps older instances.
- **Units**: spread values are in quote units (raw x price_scale): cal.cbot_zc is 0.02 USD/bu, not 2 cents. A move from raw Bloomberg prices on a 0.01-scale root is 100x off against dvol_20d.
- `spread_stats` keeps every run (the engine writes it and `spread_stats_latest` together), so a past as-of reads its own run. Dev copy has only 2 runs (2026-09-21, 22).
- 5 `config/spreads/` template ids are not in the research spread_def (bench.ore.dce_vs_sgx65, bench.ore.dce_vs_cme, bench.al.shfe_vs_comex, bench.au.shfe_vs_ose, bench.pt.gfex_vs_ose); 5 more have no stats.

**Why:** these decide whether a book spread finds its research row or shows a reason.
**How to apply:** re-check on the Bloomberg PC's copy; see [[research-db-quirks]].
