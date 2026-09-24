---
name: phase2-rates-removal-2026-09-24
description: What bbg-curves removed in the commodity conversion Phase 2 (swaption/cap vols, OIS fixings) and what stays (OIS quotes, FX fwd curves, FX smiles) and why
metadata:
  type: project
---

Commodity conversion Phase 2 (user approved 2026-09-24: rates / IRS and NDFs leave the app).
bbg-curves removed:
- `data/bloomberg/rates_vol_marketdata.py` and `fixtures/rate_vol_snapshot_v1.json` (swaption / cap vols,
  `rate_vol_quotes` staging) -- deleted outright; its tests left `tests/test_bloomberg.py`.
- Overnight fixings in `rates_marketdata.py`: `Fixing`, `fixing_ticker` in `OIS_CURVES`, `ois_fixing_ticker`,
  `get_fixings` on both sources, `_fetch_historical_series` (only fixings used it), `OisSnapshot.fixings`,
  `write_fixings` / `index_fixings` DDL. `OisSnapshot.from_dict` ignores an old `fixings` key, so
  `fixtures/ois_snapshot_v1.json` (still carrying USD fixings) loads unchanged.

Kept: OIS quotes (`curve_quotes`) because the option pricers discount on OIS curves; `fwd_curve.py` (Jason's
FX hedges, LME prompt forwards later, Phase 5); `vol_marketdata.py` (FX options dormant, not approved for
removal). Neither fwd_curve nor vol_marketdata had any NDF-specific code.

**Why:** the rates book was the macro trader's; fixings only served seasoned swaps.
**How to apply:** don't reintroduce fixings or rate vols without a new user decision; if LME forwards
(Phase 5) need prompt-date curves, extend `fwd_curve.py`, not a new rates module. Prior notes on these
modules live in `.claude/agent-memory/bbg-data/` ([[rates_marketdata_ois]], [[vol_marketdata_fx_vol_feed]]).
