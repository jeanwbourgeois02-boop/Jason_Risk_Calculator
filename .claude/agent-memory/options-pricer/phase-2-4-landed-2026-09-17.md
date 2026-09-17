---
name: phase-2-4-landed-2026-09-17
description: Snapshot of what shipped in the options_calc merge Phase 2 + Phase 4, and the exact follow-ups owed to other agents -- check these are still open before assuming they are.
metadata:
  type: project
---

2026-09-17: built engine/options/pricer.py, inputs.py, store.py,
structures.py + tests/test_options_pricing.py (19 new tests, full suite
556 passed / 1 known pre-existing unrelated failure in
test_bloomberg_diagnostic.py). Scope ledger in engine/options/__init__.py
updated in place -- check that file first for current phase status rather
than trusting this snapshot, since Phase 5+ will change it.

**Landed:** VANILLA/DIGITAL/AMERICAN/ASIAN/BARRIER_KI/BARRIER_KO/
ONE_TOUCH/NO_TOUCH FX payoffs dispatched from `instrument_options.payoff`,
writing PREMIUM/DELTA/GAMMA/THETA/VEGA/RHO marks
(`source='QL_OPTIONS_PRICER'`); `structures.combine_package` for multi-leg
package_id groups.

**Follow-ups reported, not yet actioned as of 2026-09-17 (verify still open
before relying on this):**

1. **housekeeper**: CLAUDE.md's marks table mark_type list needs GAMMA,
   THETA, VEGA, RHO added (currently only PREMIUM/DELTA are named for
   options). `marks_official`'s CASE-based view has no branch for these
   four, so they're readable from raw `marks` only until schema.py's
   `OFFICIAL_MARK_SOURCE` gains entries for them too (that edit is
   data-ingest's/schema owner's job, not mine -- I only write to `marks`,
   never touch schema.py).

2. **data-ingest**: `data/ingest/blotter.py::_parse_option` does not
   populate `barrier_level`/`avg_start_date`/`payoff` (always defaults to
   VANILLA via the schema's own DEFAULT) -- so AMERICAN/ASIAN/BARRIER_*/
   ONE_TOUCH/NO_TOUCH dispatch in store.py is exercised only by synthetic
   test rows today, never by real blotter data, until the parser is
   extended to read those fields (likely from the option's free-text
   Description, same place STRIKE_RE already looks).

3. **data-ingest**: no rule exists yet for grouping multiple FX_OPTION legs
   (e.g. a straddle's call+put) under one shared `package_id` --
   `_parse_option` sets `package_id = trade_id` for every row (one leg per
   package). `engine/options/structures.py::combine_package` is written to
   handle a real multi-leg package correctly the moment such a rule lands,
   but does not invent it. Precedent to follow: CLAUDE.md's FX-swap
   `package_id` rule.

4. **cash-ladder** (Phase 3, listed in the scope ledger, not done by me):
   `engine/ladder/ladder.py`'s option DELTA branch still needs the LEFT
   JOIN + raise-on-NULL form from CLAUDE.md's delta query now that real
   DELTA marks exist to consume.

5. **rates data**: `engine/options/vendor/options_calc/fx/rate_curves.py`
   rates are illustrative G10 placeholders, not live -- Phase 5/7 replace
   them; nothing computed with today's rates should be read as
   production-accurate P&L in the meantime.

See [[vendor-api-quirks]] and [[premium-delta-conventions]] for the
technical detail behind these.
