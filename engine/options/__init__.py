"""FX/equity/commodity/rates-vol option pricing, vendored from the standalone
``options_calc`` project (``C:\\Users\\jeanw\\Jean Bourgeois``) and wired into this app's
schema. Full merge plan: ``docs/open-questions.md`` item 61 and the housekeeper's
2026-09-17 phased plan (agent memory ``options-calc-merge-2026-09-17``).

Unlike ``engine/rates/`` (which hand-ported and trimmed a reference project function by
function), ``options_calc`` is vendored as-is under ``engine/options/vendor/`` --
byte-identical to the standalone project, auditable line-for-line against its own
``vendor/MODELS.md``. Everything in this package is a thin wrapper around that vendored
copy: read/write glue to this app's `trades` / `trade_legs` / `instruments` / `marks`
tables, never a reimplementation of the pricing math itself. Do not edit anything under
``vendor/`` -- if the upstream pricer is wrong, fix it upstream and re-vendor, don't
patch the copy in place (the whole point of vendoring as-is is that a diff against
``vendor/MODELS.md`` stays meaningful).

Scope ledger -- updated as each phase of the merge plan lands, not a limitations list:

- Phase 0 (2026-09-17, this commit): library vendored, package scaffolded, no pricer
  wired to the schema yet. Nothing in this package is callable from the rest of the app.
  The original ``options_calc`` test suite (220 tests) is vendored alongside it at
  ``engine/options/vendor/tests/`` and passes unmodified against the vendored copy
  (``py -3 -m pytest engine/options/vendor/tests/ -q``) -- a standalone regression check
  for the library itself, separate from this app's own ``tests/`` suite, which never
  collects it.
- Phase 1 (data-ingest, landed 2026-09-17): new `instrument_options` table (strike /
  option_type / barrier_level / avg_start_date / payoff, kept beside `instruments` so
  non-option rows carry no sentinel columns) and `data/ingest/blotter.py::_parse_option`
  now records call/put from the Symbol and strike from the free-text Description
  (`'<N.NNNNNN> STRIKE'`; rows without it keep the 0 sentinel). barrier_level /
  avg_start_date / payoff are not yet populated by the parser (data-ingest follow-up).
- Phase 2 (options-pricer, landed 2026-09-17): `engine/options/pricer.py` (scalar
  wrappers over `options_calc.fx.european`/`digital`, premium converted quote-ccy ->
  base-notional-fraction to match the blotter's `trades.price` units),
  `engine/options/inputs.py` (SPOT from `marks_official`, rates from
  `options_calc.fx.rate_curves` illustrative placeholders, vol from this package's own
  `option_vols` manual table), and `engine/options/store.py::price_and_store` /
  `price_all_and_store` (read `trades_official` + `instruments` + `instrument_options`,
  write `PREMIUM`/`DELTA`/`GAMMA`/`THETA`/`VEGA`/`RHO` marks, `source='QL_OPTIONS_PRICER'`).
  VANILLA and DIGITAL payoffs only; strike-0 / missing-SPOT / missing-vol / expired
  trades come back as a structured skip, never a fabricated mark. Tests:
  `tests/test_options_pricing.py`.
- Phase 3 (cash-ladder, landed 2026-09-17): `engine/ladder/ladder.py`'s option DELTA
  branch now LEFT JOINs the pair's SPOT (`i.base_ccy || i.quote_ccy`, not the option's
  own instrument_id) and raises before aggregating if any option has a DELTA mark but no
  SPOT (open-questions item 25 closed; CLAUDE.md SQL corrected the same way).
- Phase 4 (options-pricer, landed 2026-09-17): `pricer.py`/`store.py` extended to
  AMERICAN (binomial tree), ASIAN (Monte Carlo; `avg_start_date` read but not fed to the
  vendored engine, which has no custom-averaging-start parameter -- documented
  limitation), BARRIER_KI/BARRIER_KO and ONE_TOUCH/NO_TOUCH (barrier up/down direction
  derived from barrier level vs spot). `engine/options/structures.py::combine_package`
  groups `FX_OPTION` legs by `trades.package_id` and combines their Greeks via
  `options_calc.structures.combine()` -- does NOT invent the option-structure
  package_id grouping rule itself (data-ingest follow-up, see final report).
- Phase 5 (bbg-data): live FX vol feed (`data/bloomberg/vol_marketdata.py`, new
  `vol_quotes` staging table) replacing Phase 2/4's flat manual vol; unlocks
  `options_calc.fx.delta_vol_surface.FXDeltaVolSurface`.
- Phase 6 (rates-exotics): swaptions / cap-floor / SABR / Bermudan
  (`options_calc.rates`) as an extension alongside `engine/rates/`'s existing OIS-NPV
  scope -- a genuinely separate Black-76 / Hull-White model family, not folded into
  `engine/rates/` itself.
- Phase 7 (options-pricer + rates-exotics): equity/commodity pricers, `Position`/
  `Portfolio` aggregation, full `VolSurface`/`FXDeltaVolSurface` wiring, `fx.calendars`/
  `fx.g10` wired into pricer date math. No live equity/commodity trade feed exists yet in
  this app; this phase lands the capability so a future trade type prices on day one.
- Phase 8 (ui-shell): options shown inside the Blotter tab (not a standalone tab -- see
  CLAUDE.md's "Six tabs as views" note and `ui/app.py`'s docstring, both updated
  2026-09-17 to agree on this) as a grouped, collapsible summary: Portfolio Totals ->
  asset class -> structure/package -> individual legs, per the user's Bloomberg
  MARS-style reference layout.

Nothing above is silently dropped scope -- every `options_calc` module has a named
phase. Sign convention, once Phase 2 lands, will follow CLAUDE.md's existing rule:
`trades.quantity > 0` = long base currency; a long position profits when the mark moves
in its favour, matching the FX/futures convention already documented there. Record any
convention decision in the module that introduces it, not here.
"""
