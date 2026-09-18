---
name: phase-7-2-implied-forward-rate-2026-09-18
description: covered-interest-parity implied-rate fallback in engine/options/rates.py -- read before touching FX rate resolution, forward-mark interpolation, or PricingOutcome's rate-provenance fields.
metadata:
  type: project
---

**Trigger:** on the user's live Bloomberg PC (2026-09-17 evening), the new
live options-pricing step skipped every EURSEK option with "no curve/rate
SEK" (Phase 7.1's manual-rate fallback exists, but the user explicitly does
not want to hand-enter rates). EURSEK carries the book's biggest option
positions. The live pull already writes an official SPOT and FWD_OUTRIGHT
for EURSEK, so the missing SEK rate can be derived instead of asked for.

**Fix, `engine/options/rates.py`:** a fourth resolution rung, tried only
when a currency resolves NEITHER an OIS curve NOR a manual rate (Phase 7 /
7.1) AND the pair's OTHER currency resolves one of those:

```
r_quote = r_base  + ln(F/S) / T
r_base  = r_quote - ln(F/S) / T
```

`domestic` = quote ccy, `foreign` = base ccy (this module's convention
throughout, matches pricer.py's Garman-Kohlhagen orientation). `T` is plain
ACT/365 calendar days from `as_of` to the option's expiry -- deliberately
NOT this package's calendar-aware Business252 day count used elsewhere for
discounting; this is a closed-form rearrangement of the forward-pricing
identity itself, not a discounting choice, so it must use the same day
count the forward-pricing relationship is actually quoted in.

**Forward lookup (`_forward_for_expiry`):** exact `marks_official`
FWD_OUTRIGHT at the expiry date if one exists; else linear interpolation in
forward points between the two bracketing marks (mirrors CLAUDE.md's own
`BBG_INTERP` convention -- same maths as `data/bloomberg/fwd_curve.py`'s
`outright_for_date`, but that module never extrapolates at all, so this
logic is NOT reused from there, it's a fresh implementation with one
difference below); else linear extrapolation from the nearest two marks,
capped at one more "last tenor gap" beyond the final point in that
direction -- e.g. two marks 7 days apart, expiry more than 7 days past the
later one, rejects. Fewer than 2 marks staged (and no exact match) always
rejects -- no slope to extrapolate from.

**Reject behavior is load-bearing:** on ANY reject condition (missing/
non-positive spot, no forward within bracket-or-capped-extrapolation, T<=0),
`_imply_rate_from_forward` returns `None` and **`resolve_fx_rates` keeps the
ORIGINAL pre-existing `"no curve/rate <CCY>"` reason** -- this fallback
never invents its own skip-reason string. Verified by
`test_no_curve_skip_reason_for_currency_without_ois_convention` (Phase 7.1's
own test, unchanged) still passing unmodified, plus three new dedicated
reject tests in the Phase 7.2 section.

**Precedence, confirmed by two dedicated tests:** curve beats implied even
when a wildly-different-implying forward mark is staged
(`test_curve_beats_implied_even_when_a_forward_mark_exists`); manual beats
implied the same way (`test_manual_beats_implied_forward`). This falls out
naturally from the resolution order (`resolve_ccy_rate_with_source` already
tries curve then manual before this fallback is ever invoked per currency)
-- no explicit "don't override" check was needed, just correct sequencing.

**Provenance now reaches `PricingOutcome`, closing the Phase 7.1 gap**
(see [[phase-7-1-manual-rates-fallback-2026-09-17]]): `store.py` was not
off-limits this round, so `domestic_rate_source_kind`/`.domestic_rate_detail`
and `foreign_rate_source_kind`/`.foreign_rate_detail` were added alongside
the existing `vol_source_kind`/`.vol_detail` fields, same pattern. An
`IMPLIED_FORWARD` detail reads like `"implied from EURSEK forward
2026-09-23 and EUR ESTR curve"` -- the OIS_CURVE detail string itself was
upgraded from `"EUR OIS curve"` to `"EUR ESTR curve"` (index name from
`CCY_RFR`) specifically so this composed message reads the index name, not
the generic word "OIS". No test asserted the old detail string, so this was
a safe in-place change.

**Still open, not this package's job (unchanged from Phase 7.1):** a live
deposit-rate feed to reduce reliance on any fallback at all, and adding
SWESTR (SEK) / NOWA (NOK) OIS conventions to
`engine/rates/conventions.py::CCY_RFR` (rates-pricer's file) so those two
currencies get a real curve instead of implied/manual. The implied-forward
fallback only helps a currency whose PAIR partner already resolves a rate
AND has forward marks staged -- a currency with neither a curve/manual rate
of its own NOR a resolvable partner still skips exactly as before.
