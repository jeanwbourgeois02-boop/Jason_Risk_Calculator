---
name: eq-cmdty-loop-guard-2026-09-22
description: Why one QuantLib bootstrap failure zeroed the whole options step on 2026-09-22 (equity loop had no per-trade guard); bootstrap is lazy so it raises at zero_rate_to, not build_curve_set; bootstrap_note provenance
metadata:
  type: project
---

On 2026-09-22 the Bloomberg PC's pull reported the options step as priced 0 / skipped []
with `error: RuntimeError('convergence not reached after 99 iterations ...')` although
the FX loop had priced 9 and skipped 19. Cause: `price_all_and_store_equity` was a bare
list comprehension; the SPX options' USD rate resolution raised and `live._options_step`
does `outcomes += price_all_and_store_equity(...)` AFTER the FX loop, so its step-level
catch discarded everything. Fixed: `equity_commodity._price_all_and_store` mirrors the
FX guard (`store.price_all_and_store`).

**Why it matters for future work:**
- QuantLib's piecewise bootstrap is LAZY: `engine.rates.curves.build_curve_set` returns
  fine; the convergence error only surfaces at the first `zeroRate()` read
  (`rates.zero_rate_to`). So a "curve built" check proves nothing; only a read does.
- Any new `price_all_*` loop in this package must wrap each trade in the same
  `except Exception -> _skip(row, f"pricer error: {exc!r}")` guard; a step-level catch
  upstream (live.py, backfill) throws away every sibling outcome.
- rates-pricer is adding `CurveSet.bootstrap_note: str` / `interpolation: str` (log-linear
  fallback when log-cubic fails). `resolve_ccy_rate_with_source` reads it with `getattr`
  and appends it to `RateInput.detail` ("USD SOFR curve; <note>"). Empty / absent = no
  change to the detail string, and tests pin both.

**How to apply:** when a pull's options block shows priced 0 with a single `error`, look
for an unguarded loop before suspecting the pricer; read the `rates` block of
`data/bbg_snapshot/pull_status.json` for per-trade `failed` entries.
