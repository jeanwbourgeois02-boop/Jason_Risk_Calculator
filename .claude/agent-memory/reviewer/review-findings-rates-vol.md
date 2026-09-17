---
name: review-findings-rates-vol
description: 2026-09-17 review of engine/rates_vol/ (swaption/cap-floor/SABR/Bermudan Hull-White pricer, Phase 6 of options_calc merge)
metadata:
  type: project
---

Full review 2026-09-17 of engine/rates_vol/ (inputs.py, pricer.py, store.py, __init__.py)
+ tests/test_rates_vol.py. No criticals. Ownership clean (only engine/rates_vol/,
tests/test_rates_vol.py, its own agent-memory dir touched). 21/21 new tests pass;
628/629 full suite passes, 1 pre-existing unrelated failure
(test_bloomberg_diagnostic.py::test_ledger_and_feed_sections..., confirmed via git stash
to fail identically without this package present -- a live Bloomberg-connectivity
diagnostic, not caused by this change).

**Verified correct:**
- Sign: quantity>0 = long the option (bought), independent of PAYER/RECEIVER
  option_type; pricer.py scales unit-notional NPV/Greeks by signed quantity directly.
  test_long_and_short_swaption_have_opposite_signed_pv pins this non-tautologically
  (computed once at +qty, once at -qty, asserts exact negation + long>0).
- DV01 convention matches engine/rates/valuation.py exactly: NPV(+1bp bump) - NPV(base),
  one-sided bump of the two derived flat rates together (not a re-bump of curve_quotes).
- Bermudan >= European sanity test (test_bermudan_swaption_is_worth_at_least_the_
  european_under_the_same_hull_white_model) is genuinely like-for-like: same fixed_rate,
  r, hw_a, hw_sigma, tree_steps=80, same vendored HW engine on both sides (not compared
  against the different Black-76 model) -- correctly avoids the model-inconsistent trap.
- HW sigma is never defaulted: must come from rate_model_params (a/sigma both required,
  KeyError-style skip if either missing); out-of-range sigma raises from the vendored
  engine itself and store.py converts that to a structured skip, never a clamp.
- Bermudan exercise schedule is NOT read from a deal's exercise_dates -- it's
  mechanically regenerated from an inferred average frequency (_infer_exercise_frequency_
  years) because the vendored price_bermudan_swaption takes a frequency, not a date list.
  This is a real limitation but is NOT silent: flagged in three places (__init__.py's
  "Bermudan / Hull-White model risk" section, store.py, _infer_exercise_frequency_years's
  own docstring) with an explicit "do not use for real P&L/hedging without reviewer pass"
  warning. Judged as adequately disclosed, not a defect to re-flag.
- _set_eval_date guard (inputs.py) is called before every zeroRate/fairRate read off a
  cached CurveSet, addressing the vendored engine's global evaluationDate-clobbering
  quirk; correctly does NOT wrap the Bermudan tree pricer itself (vendored engine forces
  its own "today" every call regardless, per its own docstring).
- marks_official discipline clean: the only SPOT/curve reads go through
  engine.pnl.valuation.usd_per_quote (source=None -> queries marks_official, confirmed by
  reading _mark_table/_mark_at) and engine.rates.curves off curve_quotes (a different
  table, not marks). The `FROM marks` hits found by grep are only in
  tests/test_rates_vol.py assertions on what price_and_store wrote, not production reads.
- usd_per_quote confirmed to exist at git HEAD with matching signature (git show
  HEAD:engine/pnl/valuation.py) -- imports cleanly, not just in the uncommitted tree.
- No must-not-replicate items present (no mark-division, no forward-outright FX
  conversion, no single-date marking, no hard-coded ranges).

**Not re-flagged (self-disclosed by the package, not a reviewer finding):**
- Flat-curve approximation (discount/forecast rates are flat, not the full curve term
  structure) -- __init__.py states plainly this is "good enough for risk, NOT good
  enough for a mid quote".
- One-factor Hull-White, uncalibrated hw_mean_reversion/hw_sigma, fixed tree_steps=100
  default -- all called out in __init__.py's "Bermudan / Hull-White model risk" section.
- Bermudan Greeks (vega/gamma/theta) are this package's own bump-and-reprice, not
  vendor-provided; Bermudan "vega" bumps HW sigma, not a market lognormal vol -- stated
  explicitly in both __init__.py and pricer.py docstrings.

**Why:** Phase 6 of a larger options_calc vendor-library merge (see also
review-findings-rates-pricer.md for Phase-1-adjacent engine/rates IRS port). This phase
lands a new options-on-rates capability with no live ingest path yet (no swaption/cap
trades exist in the blotter today) -- a capability landing, explicitly labelled as such.
**How to apply:** on the next engine/rates_vol review (e.g. when a blotter ingest path
for SWAPTION/CAP_FLOOR lands), check that instrument_id/product/payoff values used by
the real parser match what store.py expects (`trades.product IN ('SWAPTION','CAP_FLOOR')`,
`instrument_rate_options.payoff IN (...)`), and that CLAUDE.md actually gets updated with
the new trades.product/instruments.asset_class values this package's own docstring says
are NOT YET applied there.
