---
name: review-findings-flat-forwards-2026-09-22
description: 2026-09-22 review of the OIS flat-forwards default (engine/rates/curves.py), recalc_on_file rerun and `2_launcher.py reprice`; no criticals; facts that shortcut future engine/rates reviews
metadata:
  type: project
---

Review 2026-09-22 of the uncommitted switch of `build_curve_set` to `LogLinear` (flat
forwards) plus `engine/rates/store.py::recalc_on_file` and the `reprice` launcher
subcommand. No criticals: IRS formula untouched (engine/pnl not in diff), marks written
only under QL_PRICER, a non-converging curve raises `CurveBuildError`.

**Facts worth keeping (verified, save re-deriving next time):**
- `engine/options/rates.py` has NO `except` anywhere: a `CurveBuildError` from
  `build_curve_set` propagates to the per-trade guard in `engine/options/store.py`, so
  the option is skipped with its reason; it never drops to the manual-rate or CIP
  fallback. The CIP path is reached only on "no curve <CCY>" (no quotes / no convention).
- `CurveSet.bootstrap_note` and `.interpolation` were KEPT (always "" / the built
  interpolation) so `engine/options/rates.py:315` (getattr) and `store.py:289` still
  work; nothing in `ui/` or `live.py` displays them (live's rates step reads only
  ok/error), so docstrings claiming "the pull status shows them" overclaim.
- Nobody reads the `curves` table (`grep "FROM curves "` empty); it is write-only
  provenance, so stale node rows from a previous tenor set are harmless.
- `tests/golden_book.py` writes synthetic QL_PRICER PV/DV01/CF/PAR marks directly
  (lines ~109-113), no bootstrap: an interpolation change cannot move the golden pin.
  Its failure on this Mac at HEAD is the pre-existing NDF-fix pin, not the rates change.
- `price_all_and_store` filters `NOT IN realised_pnl`, so a rerun never touches a
  matured swap's past-day marks or its frozen figure: a convention switch leaves any
  matured swap on the old curve unless the ledger row is purged and re-frozen (needs
  the user's yes, hard rule 7).
- On a day where a swap fails (missing fixing, no quotes for its ccy) INSERT OR REPLACE
  never runs, so the OLD marks stay on file silently under the same keys; the recalc
  only reports "failed".
- `_OIS_QUOTE_DAYS_SQL` is not per currency: a day with quotes for one ccy only makes
  every other ccy's swap fail and the launcher exits 1 as "every swap failed".
- Rates marks are stamped `snapped_at(as_of)` = 15:00 NY for every day, today included
  (a live pull's own rates rows do not carry the real press time, unlike options'
  `live_stamp`). Pre-existing, not from this diff.
- QuantLib 1.43 is installed in this Mac .venv, so `needs_quantlib` tests really run;
  the only skips in test_rates_pricing / test_options_pricing / test_live are
  reference-CSV-absent skips.
- Ownership drift seen: rates-pricer's diff touched `2_launcher.py`, `docs/README.md`,
  `tests/test_risk_cli.py` (infra / housekeeper files).

**Why:** the switch was the user's explicit yes ("yes switch to flat forwards and rerun
the past days"), so the review question was consistency of the rerun, not the formula.
**How to apply:** next engine/rates review, start from the two rerun gaps (matured
swaps not re-frozen; failed days keep old marks) and check whether they were closed.
See also [[review-findings-rates-pricer]].
