---
name: phase-7-1-manual-rates-fallback-2026-09-17
description: manual_rates table and resolution order added to engine/options/rates.py -- read before touching rate resolution for a currency without an OIS convention (SEK, NOK, TWD, ZAR, ...).
metadata:
  type: project
---

Phase 7's real-OIS-rates change (see [[phase-7-landed-2026-09-17]]) silently
skipped every option in a currency with no `engine/rates/conventions.py::CCY_RFR`
entry -- SEK/NOK/TWD/ZAR among them, and EURSEK specifically carries the
book's biggest real option positions (`data/raw/new_sample_trades.csv`, Fin
Type OPTION rows). Deleting the illustrative placeholder was correct; losing
real positions with no way to price them was not.

**Fix:** `engine/options/rates.py::manual_rates` table +
`set_manual_rate(conn, as_of, ccy, rate, expiry='*')`. Resolution order per
currency (shared by `resolve_fx_rates` and `resolve_ccy_rate` via the new
`resolve_ccy_rate_with_source`): OIS curve (always wins if it resolves,
manual is ignored even if set) -> manual rate for the option's exact expiry
-> manual flat `'*'` -> skip, reason now `"no curve/rate <CCY>"` (was
`"no curve <CCY>"` before this phase -- any test/consumer doing an exact
string match on the old reason needs updating).

**Provenance stops at `MarketInputs`, not `PricingOutcome`.** `RateInput`
(`source_kind` in `{OIS_CURVE, MANUAL_EXPIRY, MANUAL_FLAT}`) is carried onto
`FxRates.domestic_rate_source`/`.foreign_rate_source` and from there onto
`inputs.py::MarketInputs.domestic_rate_source`/`.foreign_rate_source` --
same pattern as `VolInput`/`vol_source`. It was NOT wired into
`store.py::PricingOutcome` this round because another agent (ui-shell) was
reading `store.py`/`portfolio.py` live and the task explicitly scoped edits
away from that file. Whoever next touches `store.py` should add
`PricingOutcome.domestic_rate_source_kind`/`.foreign_rate_source_kind` the
same way `vol_source_kind`/`vol_detail` already work -- a real gap, not
forgotten.

**Still open, not this package's job:** a Bloomberg deposit-rate feed to
auto-populate `manual_rates` (stopgap only, someone still has to type a
number today), and adding SWESTR (SEK) / NOWA (NOK) OIS conventions to
`engine/rates/conventions.py::CCY_RFR` (rates-pricer's file) would let those
two currencies skip the manual step entirely.
