---
name: phase2-macro-removal-2026-09-24
description: Ladder tab after the commodity conversion's Phase 2 removal (user yes 2026-09-24) - no NDF anything, FX_SWAP stays, EQUITY stress move not applied to futures, futures table shows currency + conversion
metadata:
  type: project
---

2026-09-24, Phase 2 of the commodity conversion (the app is now Jason's commodity RV book,
forked from the macro NMMF app). The macro trader's NDFs, IRS/DV01, equity index (ES/SPX)
and the blotter's FX-swap package rule leave the app; the screens stop reading them first,
then the engine lanes remove them.

**Why:** user approved removal 2026-09-24; top-down by layer so no lane removes what another still imports.

**How to apply:**
- Ladder reads only official SPOT (`rates_from_marks`), in both `load_inputs` and
  `net_gross_usd`; the missing-rate sentence is `ui.tabs.exposure.missing_spot_reason`
  ("no official SPOT for <d>: CNH, JPY"), the same wording the header test asserts. Row
  labels are plain codes. Never reintroduce engine.ladder.ndf imports.
- **FX_SWAP stays as a product** (housekeeper relaying the user, 2026-09-24): manual entry
  books FX swaps as FX hedges; only the blotter's package rule left. Keep "swaps" in the
  grid heading and the pair-table empty message ("forward/spot/swap").
- The EQUITY scenario move (engine.pnl.stress.futures_pct_by_scenario) is NOT passed to the
  risk table: futures are commodity contracts. Their scenario cells are "" with
  `FUTURES_NO_SCENARIO` on hover plus a caption; commodity scenarios come with
  commodity-stress (Phase 4). `combined_risk_frame` holds None as NaN, so test `pd.isna`.
- Open futures table columns: instrument, contracts, multiplier, settlement price,
  currency, "USD per unit (spot)", USD delta, all from `futures_usd_delta`'s `details`
  (book-positions); a missing price/conversion is "n/a" with the detail's own `reason` on hover.
- (Pins superseded by [[phase5-lme-and-listed-options-2026-09-24]] for USD.) The tab's tests now pin the synthetic sample `data/sample/blotter_sample.csv` (as of
  2026-09-17: CNH settled -14,330,000, 18 Nov +10,713,750, 20 Jan 27 -7,108,000; EUR settled
  -150,000; USD settled 2,175,350; CLQ26 Comdty named as settled-unrealised).
- `config/stress.yaml` now has CNH moves and no EQUITY key (infra, 2026-09-24);
  `test_stress_yaml_covers_cnh` is a plain test. `engine.pnl.stress` has no futures line
  any more, and `combined_risk_frame` / `combined_risk_table` dropped their
  `futures_pct_by_scenario` parameter (fallback_ccys is now the 4th positional).
- tests/test_ui_ladder.py and tests/test_ui_ladder_view.py ARE my files now (lane system of
  2026-09-24); the old "outside my boundary" note in [[ladder-transposed-and-ndf-display-2026-09-21]] is stale.
