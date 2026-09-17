---
name: phase-5b-smile-vol-landed-2026-09-17
description: What shipped wiring the Bloomberg FX vol smile (vol_quotes / FXDeltaVolSurface) into engine/options/inputs.py + store.py, and the range-matching gotcha found doing it.
metadata:
  type: project
---

2026-09-17: `inputs.py::resolve_vol` now resolves vol with priority
SMILE -> ATM_INTERP -> MANUAL -> None ("no vol" skip), replacing the old
MANUAL-only path from Phase 2. `store.py::PricingOutcome` gained
`vol_source_kind` / `vol_detail` so a reader can see which of the three fed
a given priced trade. Full suite 587 tests (586 passed / 1 pre-existing
unrelated failure in test_bloomberg_diagnostic.py / 1 skipped).

**Non-obvious finding: SMILE and ATM_INTERP share the same valid day-range.**
Both `_build_fx_delta_vol_surface` (inputs.py) and
`data/bloomberg/vol_marketdata.py::atm_vol_for_expiry` derive their
tenor range from the SAME set of ATM-bearing tenors in `vol_quotes`. So an
expiry that's out-of-range for the smile is *also* out-of-range for
ATM_INTERP (both refuse to extrapolate) -- an expiry beyond the longest
quoted tenor therefore skips straight from SMILE past ATM_INTERP to
MANUAL, never landing on ATM_INTERP. To actually exercise the ATM_INTERP
branch in a test you need a reason SMILE is skipped that does NOT also
disqualify ATM_INTERP -- e.g. the trade has no usable strike (`strike <= 0`
or `None`, meaning the smile is never even attempted, since a delta-vol
surface needs a strike to evaluate `get_vol(K, T)`), while the expiry
itself is still inside the quoted tenor range. See
`tests/test_options_pricing.py::test_missing_strike_skips_smile_falls_back_to_atm_interp`
vs. `::test_expiry_beyond_longest_quoted_tenor_falls_back_to_manual` for
both cases side by side.

**Caching:** one `surface_cache` dict (pair -> built `FXDeltaVolSurface`
or `(None, None)`) is threaded through `resolve_market_inputs` for the
lifetime of one `price_and_store`/`price_all_and_store` call
(`store.py::price_all_and_store` shares one across the whole run) so N
trades on the same pair build that pair's surface once, not N times --
the strike-solve in `FXDeltaVolSurface.__init__` is the expensive part,
per its own module docstring.

**Partial-smile robustness rules actually implemented** (inputs.py's
`_build_fx_delta_vol_surface`): a tenor with ATM but no RR25/BF25 is kept
as ATM-only/flat (rr=bf=0) rather than dropped; 10-delta (RR10/BF10) is
only passed to the vendored constructor if EVERY included tenor has both
-- one tenor missing either drops 10d support for the WHOLE surface
(the vendored `FXDeltaVolSurface` requires `rr_10`/`bf_10` to be
full-length lists or omitted entirely, not per-tenor-optional).

See [[vendor-api-quirks]] for the lazy-import requirement this module
still follows (`resolve_market_inputs` imports `fx.rate_curves` locally,
so anything calling it needs QuantLib installed even though vol
resolution itself is pure arithmetic) and [[premium-delta-conventions]]
for the unrelated PREMIUM/DELTA unit rules. The Bloomberg
ticker/field verification caveat from `data/bloomberg/vol_marketdata.py`
carries forward unchanged: every `vol_quotes` value consumed here is
UNVERIFIED against a live terminal until the user runs `--probe`.
