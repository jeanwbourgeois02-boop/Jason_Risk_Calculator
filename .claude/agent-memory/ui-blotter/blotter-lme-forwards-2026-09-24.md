---
name: blotter-lme-forwards-2026-09-24
description: Phase 5 on the Blotter - LME_FWD is its own "LME forwards" asset class, the Futures sub-tab became "Futures & LME" with LME tickets under their metal, Quantity + Unit (lots / t) + Product columns, LME local P&L = USD
metadata:
  type: project
---

Phase 5 follow-up (2026-09-24), ui/tabs/blotter.py:

- `ASSET_CLASS_OF["LME_FWD"] = "LME forwards"`, order FX, Futures, LME forwards, Options. Never "Other".
- `SCOPE_PRODUCTS["futures"] = ("FUTURE", "LME_FWD")`, label "Futures & LME" (scope key stays "futures", so every component id is unchanged).
- An LME ticket's instrument is the metal's root id ('LME:CA'), so `add_future_fields` finds it in `load_roots()` and groups it under that root's name (its own commodity row under Metals, next to COMEX / SHFE copper, like any root).
- Quantity is tonnes for LME_FWD (the parser converts lots with `engine.lme.lot_tonnes`) but contracts for FUTURE: the column is "Quantity" with a "Unit" column (`QUANTITY_UNIT_OF`: lots / t) and a "Product" column ("Future" / "LME forward"). "Expiry" became "Expiry / prompt".
- `value_book` leaves `pnl_local` NaN on a settled LME row (the frozen row is USD only); since LME is USD-quoted the screen shows `pnl_usd` there (brief: "their local P&L = USD"). A settled USD future still shows the SETTLED_LOCAL_REASON hover instead (not changed; inconsistency flagged to the housekeeper).
- The sample has no marks, so every sample figure is n/a; "check a futures figure did not move" = compare the futures rows (reason text included) between `git show HEAD:data/sample/blotter_sample.csv` and the new one, loaded into two in-memory DBs.

**Why:** housekeeper brief 2026-09-24 (pnl-valuation now values LME_FWD; a commodity trader reads LME with the futures).
**How to apply:** new LME tests live in `tests/test_ui_blotter_commodity.py` (`_lme` helper books like the parser). Never run `py -3 -` with a heredoc, not even as a placeholder: it spins (happened again this session; killed by PID, not by name).
