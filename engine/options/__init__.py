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
- Phase 5a (bbg-data, landed 2026-09-17): live FX vol feed
  (`data/bloomberg/vol_marketdata.py`, new `vol_quotes` staging table,
  `vol_smile`/`atm_vol_for_expiry` read helpers).
- Phase 5b (options-pricer, landed 2026-09-17): `inputs.py::resolve_vol`
  now prices off `vol_quotes` via `FXDeltaVolSurface` first (SMILE), falls
  back to `atm_vol_for_expiry` (ATM_INTERP), then the Phase 2 flat
  `option_vols` table (MANUAL, now last resort not first), else the trade
  is skipped "no vol" as before; `store.py`'s `PricingOutcome` records
  which of the three fed each priced trade. Bloomberg-verification caveat
  carries forward unchanged: every `vol_quotes` ticker/field guess in
  `data/bloomberg/vol_marketdata.py`'s module docstring is UNVERIFIED
  until the user runs `--probe` on a live terminal -- nothing computed
  from a SMILE/ATM_INTERP vol should be read as production-accurate until
  then.
- Phase 6 (rates-exotics): swaptions / cap-floor / SABR / Bermudan
  (`options_calc.rates`) as an extension alongside `engine/rates/`'s existing OIS-NPV
  scope -- a genuinely separate Black-76 / Hull-White model family, not folded into
  `engine/rates/` itself.
- Phase 7 (options-pricer, landed 2026-09-17): real rates, calendars, equity/commodity
  pricers, and Position/Portfolio aggregation.
  - `engine/options/rates.py` (new): `resolve_fx_rates` / `resolve_ccy_rate` replace
    `options_calc.fx.rate_curves`' illustrative placeholder as `inputs.py`'s rate source
    with continuously-compounded OIS zero rates to the option's own expiry (exact for
    Garman-Kohlhagen -- see that module's docstring), read via
    `engine.rates.curves.build_curve_set` off `curve_quotes`. The illustrative fallback
    is DELETED from this package's input-resolution path (the vendored copy itself is
    untouched). Only currencies with an OIS convention in
    `engine/rates/conventions.py::CCY_RFR` (USD/EUR/GBP/JPY/CHF/CAD/AUD -- seven, NOT the
    full 45-pair G10 set) can resolve real rates; any other currency, or a supported
    currency simply missing `curve_quotes` on the day, skips the trade with reason
    "no curve <CCY>" -- this closes EURSEK's (SEK has no OIS convention anywhere in this
    codebase) previous illustrative-rate path, so the test fixture pair changed from
    EURSEK to EURUSD (both currencies covered) -- see tests/test_options_pricing.py.
    `curve_cache`, keyed `(as_of, ccy)`, is threaded through `resolve_market_inputs` /
    `store.py` the same way `surface_cache` already is.
  - `engine/options/calendars.py` (new): `calendar_year_fraction(pair, as_of, expiry)`
    (Business252 on the pair's joint settlement calendar,
    `options_calc.fx.calendars.joint_calendar`) and `spot_date` (correct spot lag,
    T+1 for USD/CAD). `pricer.py`'s `price_fx_*` functions take an optional `pair`
    (used by `store.py`'s dispatch, always) and `calendar_aware` (default True; the
    plain Act/365 day count is now reached only via `pair=None`, the old signature every
    pre-Phase-7 call site keeps using unchanged, or `calendar_aware=False` explicitly).
    Non-G10 pairs fall back to plain days automatically.
  - `delta_convention` / `DELTA_PA` (Phase 7): `OptionPriceResult` now carries
    `delta_premium_adjusted` (always) and `delta_convention` ('RAW' | 'PREMIUM_ADJUSTED'
    for a recognized G10 pair via `options_calc.fx.g10.recommended_delta`'s underlying
    convention table, else 'UNKNOWN'). `store.py` writes a `DELTA_PA` mark (in addition
    to the unchanged, always-written raw `DELTA` mark -- CLAUDE.md's DELTA meaning is NOT
    changed) only when `delta_convention == 'PREMIUM_ADJUSTED'`. **Follow-up for
    housekeeper**: `DELTA_PA` is not yet in `data/ingest/schema.py`'s
    `OFFICIAL_MARK_SOURCE` mapping (out of this package's ownership), so it is written to
    `marks` but does not surface via `marks_official` until that mapping is extended.
  - `engine/options/pricer.py::price_equity_option` / `price_commodity_option` (new):
    same `OptionPriceResult` dataclass, but `premium` is UNSCALED quote-ccy price per 1
    unit of underlying (no FX base-notional-fraction conversion -- documented on the
    dataclass itself). Commodity only supports VANILLA/AMERICAN/ASIAN (no barrier/
    digital/one-touch commodity pricer exists upstream, per MODELS.md's "Planned"
    section -- not a gap introduced here).
  - `engine/options/equity_commodity.py` (new): read/dispatch/mark-write glue for two
    NEW `instruments.asset_class` (and `trades.product`) values, `EQ_OPTION` and
    `CMDTY_OPTION` -- **report to housekeeper for CLAUDE.md's "Tables" section**. No live
    trade feed exists for either (tests seed synthetic trades). Underlying identity:
    `instruments.bbg_ticker` on the OPTION's own row is repurposed to hold the
    underlying's instrument_id (e.g. `'SPX Index'`, `'GC1 Comdty'`) -- SPOT is then read
    from `marks_official` keyed by that id. **Known quirk, flagged for housekeeper**:
    `marks_official`'s SPOT is only official under `source='BBG_BFXFORWARD'`
    (schema.py has no equity/commodity-specific SPOT source yet), so an equity/commodity
    underlying's SPOT mark currently has to be stamped that same (FX-flavored) source
    string to be visible here. Two new manual tables: `equity_dividend_yields`
    (defensive DDL, no default row -- every equity trade needs an explicit entry, even
    0.0) and `vol_surface_points` (strike x tenor grid per underlying, built into an
    `options_calc.vol_surface.VolSurface`; falls back to this package's own flat
    `option_vols` table, keyed by underlying instead of an FX pair, when no surface is
    staged). Rate is a single OIS zero rate off `instruments.quote_ccy` (no domestic/
    foreign split -- FX-only concept). PREMIUM mark = pricer's unscaled premium x
    `instruments.multiplier`.
  - `engine/options/portfolio.py` (new): builds vendored `Position`/`Portfolio` objects
    from priced `PricingOutcome` (FX) / `EqCmdtyOutcome` (equity/commodity) outcomes,
    converting every Greek to USD via `quantity * multiplier * spot_to_usd` (spot_to_usd
    from `marks_official`'s official SPOT of `quote_ccy`, CLAUDE.md's "convert at spot,
    never the forward outright" rule extended uniformly to every asset class) -- the
    "normalize to a common basis before summing across asset classes" step
    MODELS.md's `portfolio.py` section documents as deliberately NOT done by the
    vendored package itself. `Position.label = package_id` for `by_label()`'s package-
    level rollup; the returned `legs` list carries the bottom (per-instrument) level of
    "Portfolio Totals -> asset class -> package -> leg" for Phase 8's UI. A priced
    outcome whose `quote_ccy` has no official SPOT is excluded and reported in
    `skipped`, never defaulted to 1.0.
  - What remains manual / out of scope this phase: equity/commodity dividend yields and
    vol surfaces are hand-entered (no live feed, same as FX's Phase 2 `option_vols`
    before Phase 5a/5b landed live vol); no data-ingest parser exists for EQ_OPTION/
    CMDTY_OPTION trades; the 36 G10 cross pairs' premium-currency convention (feeding
    `delta_convention`) is `options_calc.fx.g10`'s own simplified "base" default, not
    individually verified per pair (MODELS.md's own caveat, unchanged here); non-G10
    pairs' calendar year fraction and delta_convention both fall back with 'UNKNOWN'
    documented on the outcome, not computed some other way.
- Phase 7.1 (options-pricer, landed 2026-09-17): closed a real gap left by Phase 7's
  real-OIS-rates change -- ``engine/rates/conventions.py::CCY_RFR`` covers only seven
  currencies (USD/EUR/GBP/JPY/CHF/CAD/AUD), so every EURSEK, USDTWD, USDZAR, etc. option
  in the actual blotter (EURSEK carries the book's biggest option positions --
  ``data/raw/new_sample_trades.csv``, Fin Type OPTION rows) silently skipped with
  "no curve <CCY>" once the illustrative placeholder was deleted. Deleting the
  placeholder was correct (no invented numbers); silently losing real positions was not.
  Fix: a new ``manual_rates`` table in ``engine/options/rates.py`` (``set_manual_rate(conn,
  as_of, ccy, rate, expiry='*')``, same shape as ``inputs.py``'s ``option_vols``).
  Resolution order per currency (both ``resolve_fx_rates`` and ``resolve_ccy_rate``, via
  the new shared ``resolve_ccy_rate_with_source``): OIS curve (unchanged, always wins when
  it resolves) -> manual rate for the option's exact expiry -> manual flat ``'*'`` ->
  skip, reason now ``"no curve/rate <CCY>"`` (was ``"no curve <CCY>"``). Provenance
  (``RateInput.source_kind`` in ``{OIS_CURVE, MANUAL_EXPIRY, MANUAL_FLAT}``) is carried
  onto ``FxRates.domestic_rate_source``/``.foreign_rate_source`` and from there onto
  ``inputs.py::MarketInputs.domestic_rate_source``/``.foreign_rate_source``, the same
  pattern ``VolInput``/``vol_source`` already uses. **Not done this phase, and not carried
  further than MarketInputs**: propagating rate provenance onto
  ``store.py::PricingOutcome`` (the way ``vol_source_kind``/``vol_detail`` already are) --
  ``store.py`` was off-limits for this follow-up (another agent was reading it), so a
  reader wanting rate provenance today has to call ``resolve_market_inputs`` directly
  rather than reading it off a stored outcome; whoever next touches ``store.py`` should
  wire ``PricingOutcome.domestic_rate_source_kind`` / ``.foreign_rate_source_kind`` the
  same way. **Also not in scope, flagged for rates-pricer**: a Bloomberg deposit-rate
  feed to auto-populate ``manual_rates`` is a stopgap, not built here; more directly,
  adding SWESTR (SEK) and NOWA (NOK) OIS conventions to
  ``engine/rates/conventions.py::CCY_RFR`` would let those two currencies skip the manual
  step entirely and resolve real curves like the other seven -- that file is
  rates-pricer's, not this package's, to extend.
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
