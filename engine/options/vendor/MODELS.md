# Models Reference

A file-by-file summary of every option type implemented in `options_calc/`,
and how each one is calculated. Update this whenever a new file is added.

## Taxonomy

Every option type here is defined by three independent choices:

- **Exercise style** — *when* it can be exercised (European: at expiry
  only; American: any time up to expiry; Bermudan: on specific discrete
  dates — not yet implemented)
- **Payoff type** — *what* the payoff is based on (vanilla: spot at
  exercise; Asian: average spot over a period; barrier: conditional on
  touching a level during the option's life; digital: a fixed cash amount
  if in-the-money, not a scaling payoff)
- **Underlying / asset class** — *what* it's written on (equity/index, FX,
  rates, commodities)

"Vanilla" refers only to the payoff type (a plain call/put) — it says
nothing about exercise style or asset class. A vanilla option can be
European or American; on equity or FX.

Every pricer in this package returns a dict with at least
`{price, delta, gamma, theta, vega, rho}` — one instrument's price and
sensitivities per unit of underlying. This is *not* a portfolio-level tool
on its own: position sizing (notional), aggregation across a book, and live
market data are separate layers (see `portfolio.py` below and Roadmap in
README.md).

**Every equity pricer also returns `rho_dividend`** (sensitivity to the
dividend yield) and **every FX pricer also returns `rho_foreign`**
(sensitivity to the foreign rate) and **`delta_premium_adjusted`** (the FX
market convention delta) alongside the base six fields -- see
`fx/_conventions.py` below. `rho` alone is domestic-rate-only for FX; it
is not an aggregate of both rates (a common point of confusion -- the two
rates enter the pricing formula in genuinely different roles, not as one
combined number).

Every pricer still takes `sigma` as a single flat number per call --
`vol_surface.py` (below) exists to source that number correctly (per
strike and expiry, from real market data) rather than removing the
parameter or changing any pricer's signature.

## Files

### `options_calc/_engine.py`
Internal shared module (leading underscore = not part of the public API).
Contains:
- `build_process()` — constructs the QuantLib dates and
  `BlackScholesMertonProcess` common to every pricer here.
- `price_european_vanilla()` — the closed-form analytic European pricer,
  used by both equity and FX European files. Returns `rho` (via
  QuantLib's `option.rho()`, sensitivity to the `r` argument) AND
  `rho_dividend` (via `option.dividendRho()`, sensitivity to the
  `dividend_rate` argument) as two separate, independently computed
  analytic fields -- not one combined number.
- `finite_difference_greeks()` — bump-and-reprice Greeks for pricers with
  no closed form (American, Asian, barrier, digital). See its docstring
  for why the bump sizes are deliberately large (1% of spot / 1 vol point
  / 100bp of rate) rather than infinitesimal. Bumps `dividend_rate`
  exactly the same way it bumps every other parameter, producing
  `rho_dividend` alongside `rho` -- every non-European pricer gets both
  fields the same way the analytic European pricer does, rather than only
  the closed-form pricer having complete Greeks.

### `options_calc/equity/european.py`
**Exercise:** European. **Payoff:** vanilla call/put. **Underlying:** equity
or equity index.

- **Model:** Black-Scholes-Merton (Black-Scholes with a continuous dividend
  yield term).
- **Engine:** QuantLib `AnalyticEuropeanEngine` — closed-form, so Greeks are
  analytic (exact, not approximated).
- **Inputs:** spot (S), strike (K), time to expiry in years (T), risk-free
  rate (r), volatility (sigma), option type, dividend yield (0 for a
  non-dividend payer).
- **Known limitation:** flat volatility only (no skew/smile); dividend
  yield is a single continuous rate, not a discrete dividend schedule.
- **Output includes `rho_dividend`**, a legitimate second Greek
  (sensitivity to the dividend yield itself) alongside `rho`.

### `options_calc/fx/european.py`
**Exercise:** European. **Payoff:** vanilla call/put. **Underlying:** FX
pair.

- **Model:** Garman-Kohlhagen — the FX-specific case of Black-Scholes-Merton
  where the foreign risk-free rate is substituted for the dividend yield.
- **Engine:** same `AnalyticEuropeanEngine` as the equity pricer, via the
  shared `_engine.py`. Analytic Greeks.
- **Inputs:** spot exchange rate (S), strike (K), T, domestic rate, foreign
  rate, volatility, option type.
- **Output fields beyond the base six:** `rho_foreign` (sensitivity to
  `foreign_rate`, computed via `option.dividendRho()`/100 -- previously
  entirely missing; `rho` alone only ever measured domestic-rate
  sensitivity, not an aggregate of both rates) and
  `delta_premium_adjusted` (the FX market convention delta, via
  `fx/_conventions.py`, used when premium is paid in the base/foreign
  currency). Raw `delta` is still returned too.
- **Known limitation:** flat vol only; forward-delta quoting (a further,
  distinct FX convention) is not yet implemented.

### `options_calc/equity/american.py`
**Exercise:** American. **Payoff:** vanilla call/put. **Underlying:** equity
or equity index.

- **Model:** Black-Scholes-Merton dynamics; early exercise allowed at any
  time. No closed-form solution exists in general (it's a free-boundary
  problem), so it's priced numerically.
- **Engine:** Cox-Ross-Rubinstein binomial tree (`BinomialVanillaEngine`,
  "crr", 800 steps) — the standard textbook method for American options.
- **Greeks:** bump-and-reprice (`finite_difference_greeks`), since a tree
  yields a price, not an analytic sensitivity.
- **Sanity check built into tests:** with zero dividends, an American call
  is never optimal to exercise early and must price identically to its
  European counterpart.

### `options_calc/fx/american.py`
Same as `equity/american.py`, with the foreign rate substituted for the
dividend yield (Garman-Kohlhagen-style). Same binomial tree engine, same
bump-and-reprice Greeks -- including `rho_foreign` via the bumped
dividend_rate, and `delta_premium_adjusted` via `fx/_conventions.py`, same
as every other FX file.

### `options_calc/equity/asian.py`
**Exercise:** European (you cannot early-exercise against an average that's
still being computed). **Payoff:** Asian (arithmetic average of spot over a
set of fixing dates, not spot at a single point). **Underlying:** equity or
equity index.

- **Model:** Black-Scholes-Merton dynamics. The sum of lognormal variables
  is not itself lognormal, so there is no closed-form solution for an
  arithmetic average — priced by Monte Carlo simulation, the standard
  industry approach.
- **Engine:** `MCDiscreteArithmeticAPEngine` ("PseudoRandom", 20,000
  samples, fixed seed for reproducibility).
- **Inputs:** same as the European pricer, plus `n_fixings` (number of
  evenly spaced averaging dates between today and expiry).
- **Greeks:** bump-and-reprice.
- **Sanity check built into tests:** an Asian call must always be cheaper
  than an otherwise-identical European call (averaging reduces effective
  volatility exposure).

### `options_calc/fx/asian.py`
Same as `equity/asian.py`, with the foreign rate substituted for the
dividend yield. Same Monte Carlo engine, same bump-and-reprice Greeks
(`rho_foreign`, `delta_premium_adjusted` included, same as every FX file).
Relevant for corporate FX hedging, where a company wants protection on its
*average* conversion rate over a period.

### `options_calc/equity/barrier.py`
**Exercise:** European. **Payoff:** barrier (vanilla call/put, conditional
on touching a specified level during the option's life). **Underlying:**
equity or equity index.

- **Model:** Black-Scholes-Merton. A European barrier option has a
  closed-form solution (the Reiner-Rubinstein formulas), unlike American
  exercise or Asian averaging.
- **Engine:** QuantLib `AnalyticBarrierEngine`. Greeks computed by
  bump-and-reprice for consistency with the rest of the non-vanilla
  pricers, rather than relying on engine-specific analytic Greek support.
- **Inputs:** same as the European pricer, plus `barrier` (the trigger
  level) and `barrier_type` (one of `up-and-out`, `down-and-out`,
  `up-and-in`, `down-and-in`), plus an optional `rebate` (cash paid if
  knocked out).
- **Sanity check built into tests:** knock-in + knock-out (same barrier,
  otherwise identical) must sum exactly to the vanilla price -- a genuine
  no-arbitrage identity (exactly one of "touched the barrier" or "didn't"
  happens), not an approximation.

### `options_calc/fx/barrier.py`
Same as `equity/barrier.py`, with the foreign rate substituted for the
dividend yield (`rho_foreign`, `delta_premium_adjusted` included). This is
the "KIKO" (knock-in-knock-out) structure -- cheaper than a vanilla,
standard for macro FX views and corporate hedging where giving up (or
requiring) a barrier touch is an acceptable trade-off for lower premium.

### `options_calc/equity/digital.py`
**Exercise:** European. **Payoff:** digital/cash-or-nothing (a FIXED cash
amount if in-the-money at expiry, not a payoff that scales with how far
ITM). **Underlying:** equity or equity index.

- **Model:** Black-Scholes-Merton. Closed-form (essentially the N(d2) term
  from the vanilla formula, scaled by the cash payout).
- **Engine:** QuantLib `AnalyticEuropeanEngine` with a `CashOrNothingPayoff`.
  Greeks by bump-and-reprice.
- **Inputs:** same as the European pricer, plus `cash_payout` (the fixed
  amount paid if ITM) in place of the payoff scaling with moneyness.
- **Known caveat:** gamma near expiry, right at the strike, is extremely
  large -- the payoff jumps from 0 to the full cash amount over an
  infinitesimal move, so Greeks here should be read with that in mind.
- **Sanity check built into tests:** a call and put digital (same
  strike/expiry) must sum to the discounted cash payout -- exactly one of
  "finishes above K" or "finishes below K" happens, a no-arbitrage
  identity analogous to put-call parity for vanillas.

### `options_calc/fx/digital.py`
Same as `equity/digital.py`, with the foreign rate substituted for the
dividend yield (`rho_foreign`, `delta_premium_adjusted` included). Common
around known FX-moving events (a central bank decision, an election) for a
sharp, capped directional bet.

### `options_calc/equity/one_touch.py`
**Exercise:** constructed as American internally (required by QuantLib's
binary-barrier engine, since the touch condition needs continuous
monitoring through the option's life), with `payoffAtExpiry=True` for
deferred payout. **Payoff:** one-touch/no-touch -- a FIXED cash amount
depending on whether the underlying EVER touches a barrier during the
option's life, genuinely distinct from both `barrier.py` (touch
activates/deactivates a further vanilla payoff) and `digital.py` (only
checks the level at expiry, ignoring the path). **Underlying:** equity or
equity index.

- **`one_touch()`** — pays if the barrier IS touched at any point before
  expiry. **`no_touch()`** — pays if it is NEVER touched. Complementary:
  exactly one of the two outcomes happens, so their prices sum to the
  discounted cash payout -- verified in tests, the same no-arbitrage
  pattern as barrier knock-in/knock-out parity.
- **Model:** Black-Scholes-Merton, closed-form via QuantLib's
  `AnalyticBinaryBarrierEngine`. Internally built as a
  `CashOrNothingPayoff` struck at a nominal near-zero level (so it's
  always in-the-money once the touch condition activates it) combined
  with a barrier condition -- an implementation detail, not a
  user-configurable strike. Greeks by bump-and-reprice, same as
  `barrier.py` and `digital.py`.
- **Payout timing convention, stated explicitly:** this implements the
  "deferred" convention (cash paid at expiry if triggered), not the
  "immediate" convention (paid right at the moment of touch) -- a
  different, related instrument this does not implement. Deferred is
  simpler to price (discounting is always from expiry back to today) and
  is common market practice.
- **Inputs:** spot (S), `barrier` (trigger level), T, r, sigma,
  `cash_payout`, `direction` (`'up'` or `'down'`), dividend yield.

### `options_calc/fx/one_touch.py`
Same as `equity/one_touch.py`, with the foreign rate substituted for the
dividend yield (`rho_foreign`, `delta_premium_adjusted` included).
Genuinely standard in FX specifically -- "will EUR/USD touch 1.20 before
expiry" is a classic, liquidly-traded macro FX structure.

### `options_calc/fx/_conventions.py`
Internal shared module. Contains `add_premium_adjusted_delta(result, S)`
and `add_forward_delta(result, T, foreign_rate)`, called at the end of
every `fx/*.py` pricer (`european`, `american`, `asian`, `barrier`,
`digital`, `one_touch`) to add `delta_premium_adjusted`, `delta_forward`,
and `delta_forward_premium_adjusted` alongside raw `delta`.

- **Premium-adjusted formula:** `delta - price / S`. Real FX desks use
  this number instead of raw delta whenever an option's premium is paid
  in the base/foreign currency (the market convention for many pairs,
  e.g. EUR, GBP, AUD as base against USD) -- receiving/paying premium in
  the foreign currency already creates foreign-currency exposure separate
  from the option's own payoff, which raw delta ignores and this
  correction nets out. Correct for both calls and puts.
- **Forward-delta formula:** `delta * exp(foreign_rate * T)`. Spot delta
  includes the foreign-currency discount factor `exp(-foreign_rate*T)`
  (since a move in spot doesn't map 1:1 to a move in the forward rate);
  forward delta removes it, measuring sensitivity to the FORWARD rate
  instead. Verified against the standard reference formula for FX delta
  conventions (Reiswich & Wystup, "FX Volatility Smile Construction").
  `delta_forward_premium_adjusted` applies the same scaling to the
  premium-adjusted number, following the same pattern the reference
  literature uses -- this specific piece (scaling the premium-adjusted
  number, as opposed to the base forward-delta formula) was not
  independently re-derived from first principles to the same level of
  scrutiny as the rest.

### `options_calc/equity/implied_vol.py`
Given a market-observed price, solves for the volatility that reproduces
it -- now covering ALL SIX option types this package prices
(`equity/european.py`, `barrier.py`, `digital.py`, `american.py`,
`asian.py`, `one_touch.py`), each inverted via whichever method is
actually reliable for that pricer (QuantLib's own closed-form Newton
solver where the pricer is closed-form; a fast approximation engine plus
Newton where the real pricer is too slow to re-run repeatedly; bisection
where QuantLib's automatic solver doesn't support the exercise style).

- **`implied_volatility()`** — European vanilla. Validated by round-trip
  tests: price at a known vol, solve backward, confirm the same vol comes
  back out.
- **`implied_volatility_digital()`** — digital/cash-or-nothing. Also
  closed-form under Black-Scholes-Merton, so just as cheap and stable to
  invert as the vanilla case. Round-trip tested the same way.
- **`implied_volatility_barrier()`** — European barrier. Closed-form
  (Reiner-Rubinstein), so mechanically the same solver applies -- BUT
  with a real, documented caveat: **knock-out barrier prices are NOT
  monotonic in volatility** (vega changes sign, as noted against
  `equity/barrier.py` -- price rises then falls as vol increases). This
  means a target price can genuinely correspond to two different vols, or
  fall outside what a simple bracketing search finds, and the solve can
  legitimately fail with a `ValueError` (verified in tests, with an
  informative error message explaining why). **Knock-in barriers do not
  have this problem** -- price is monotonic in vol, verified by round-trip
  test, exactly like the vanilla/digital case.
- **`implied_volatility_american()`** — American exercise. Uses
  `_baw_engine.py` (see below), NOT the 800-step Cox-Ross-Rubinstein tree
  `equity/american.py` uses for actual pricing -- the tree is deterministic
  and could in principle sit inside a root-finder, but rebuilding an
  800-step tree on every Newton iteration is too slow to be practical.
  Barone-Adesi-Whaley is a fast, well-known quasi-analytic approximation
  used specifically to make repeated re-pricing cheap. **Important
  caveat:** the vol this returns is implied relative to BAW's price, NOT
  the tree's -- they are close (see `_baw_engine.py` below for measured
  discrepancies) but not identical. Round-trip precision is ~1e-3, looser
  than the ~1e-6 achieved for the closed-form solvers above, because
  QuantLib estimates BAW's vega by internally bumping its own price
  (BAW has no analytic vega).
- **`implied_volatility_asian()`** — arithmetic-average Asian. Uses
  `_asian_approx_engine.py`'s Turnbull-Wakeman approximation (see below),
  NOT `equity/asian.py`'s Monte Carlo engine, for the same "too
  expensive/noisy to re-run inside a solver" reason American has BAW.
  Same "implied relative to the approximation, not the real pricer"
  caveat as American.
- **`implied_volatility_one_touch()` / `implied_volatility_no_touch()`**
  — one-touch/no-touch. Uses the SAME exact pricer/engine as
  `one_touch.py` (no approximation here) via a custom bisection solver
  instead of QuantLib's own Newton solver, since QuantLib's automatic
  engine selection doesn't support the `AmericanExercise` construction
  one-touch/no-touch requires internally. See `_engine.py`'s
  `implied_volatility_one_touch()` below for the full explanation,
  including a real numerical edge case found while building this and a
  price/vol direction that's the opposite of every other instrument.

### `options_calc/_baw_engine.py`
**Not a new pricer for general use** -- an internal, asset-class-agnostic
module (alongside `_engine.py`) providing a Barone-Adesi-Whaley (1987)
American option pricer and implied-vol solver, used ONLY by
`implied_volatility_american()` in `equity/implied_vol.py` and
`fx/implied_vol.py`, specifically because it's fast enough to call
hundreds of times inside a Newton solve where the real tree pricer isn't.

- **Engine:** QuantLib's built-in `ql.BaroneAdesiWhaleyApproximationEngine`
  -- deliberately used instead of hand-deriving the BAW formula, matching
  this project's general preference for relying on QuantLib's own engines
  over reimplementing Black-Scholes-family math from scratch.
- **Accuracy, measured against the tree pricer (equity/american.py,
  fx/american.py) across varied cases** (see
  `tests/equity/test_implied_vol_american.py` and
  `tests/fx/test_implied_vol_american.py` for the full numbers): mostly
  under 1% divergence for near-ATM, moderate-maturity options; growing to
  ~2.7% for a 5-year-dated put; one deep-OTM equity put case showed ~17%
  *relative* divergence, but the absolute prices involved were
  economically negligible (~$0.005-0.006 on a $100 spot) -- treat that as
  a reminder to read relative-error numbers in context, not as evidence
  BAW is unreliable near the money.
- **Known limitations:** BAW is a known-imperfect approximation, not
  exact -- accuracy degrades for long-dated (multi-year) options and deep
  in/out-of-the-money strikes, consistent with its documented weaknesses
  in the literature (it approximates the early-exercise boundary
  quadratically). Not independently verified against the original 1987
  paper's worked examples -- validated instead by cross-checking against
  this project's own already-tested tree pricer. Only tested for
  maturities up to 5 years and moneyness from ~60% to ~140% of spot;
  behavior outside that range (very short-dated, extreme moneyness) is
  unverified.

### `options_calc/_asian_approx_engine.py`
Same role as `_baw_engine.py`, for Asian options: an internal, fast
pricer used ONLY by `implied_volatility_asian()` in `equity/implied_vol.py`
and `fx/implied_vol.py`. Equity/asian.py's/fx/asian.py's Monte Carlo
pricer has genuine simulation noise -- not a smooth, deterministic
function of sigma -- which can confuse a root-finder chasing a precise
target price.

- **Engine:** QuantLib's built-in `ql.TurnbullWakemanAsianEngine`
  (Turnbull & Wakeman, 1991 -- a standard moment-matching approximation
  for arithmetic-average Asian prices), used instead of re-deriving the
  moment-matching formula by hand.
- **`DiscreteAveragingAsianOption` does not expose QuantLib's own
  `impliedVolatility()`** the way `VanillaOption`/`BarrierOption` do, so
  this module implements its own bisection solver
  (`scipy.optimize.brentq`) rather than delegating to QuantLib's Newton
  solver the way the other `implied_volatility_*` functions do.
- **Accuracy, measured against equity/asian.py's Monte Carlo pricer** for
  a representative case (S=100, K=105, T=0.5, r=5%, sigma=20%, 12 monthly
  fixings): Turnbull-Wakeman gives 2.007, Monte Carlo gives 1.970 --
  roughly 1.9% apart. This is the expected level of agreement between two
  genuinely different methods (a moment-matching approximation vs. a
  sampling method with its own statistical noise), not evidence either
  one is wrong.
- **Known limitation:** an approximation, not the exact arithmetic-Asian
  price -- the vol this returns is implied relative to Turnbull-Wakeman,
  not Monte Carlo.

### `options_calc/_engine.py`'s `implied_volatility_one_touch()`
Shared solver for `implied_volatility_one_touch()`/
`implied_volatility_no_touch()` in `equity/implied_vol.py` and
`fx/implied_vol.py`.

- **Does NOT use QuantLib's own `impliedVolatility()`** -- QuantLib's
  automatic engine selection for that method only supports EUROPEAN-
  exercise barrier options, and one-touch/no-touch are built internally
  on `AmericanExercise` (required for the touch condition to be monitored
  continuously -- see `equity/one_touch.py`). Calling it raises "engine
  not available for non-European barrier option." Solves via bisection
  (`scipy.optimize.brentq`) instead -- against the EXACT SAME pricer/
  engine `one_touch.py` uses (`AnalyticBinaryBarrierEngine`), not an
  approximation the way `_baw_engine.py`/`_asian_approx_engine.py` are.
- **Why bisection is reliable here, unlike knock-out vanilla barriers:**
  one-touch and no-touch prices are cleanly MONOTONIC in volatility
  (touch probability strictly increases with vol, so one-touch price
  strictly increases and no-touch strictly decreases) -- verified by
  scanning price across a wide vol range while building this. Knock-out
  VANILLA barriers do NOT have this property (see
  `implied_volatility_barrier` above), which is why that solver can
  legitimately fail while this one is reliable.
- **A real numerical edge case found and fixed while building this:**
  QuantLib's `AnalyticBinaryBarrierEngine` returns NaN below roughly 0.8%
  volatility for typical inputs (verified empirically) -- a QuantLib
  numerical edge case at near-zero vol, not something this package can
  fix. The solver uses a 1% minimum vol bound (`_TOUCH_MIN_VOL`) instead
  of the 0.01% bound the other solvers use, comfortably above that
  boundary and still far below any vol level that occurs in practice.
- **Important, easy-to-get-backwards direction, confirmed in tests:** for
  a no-touch specifically, a HIGHER market price implies a LOWER implied
  vol (since no-touch survival probability decreases as vol rises) -- the
  opposite direction from every other instrument in this package.

### `options_calc/fx/implied_vol.py`
Same full solver set as `equity/implied_vol.py` (European vanilla,
barrier, digital, American via `implied_volatility_american()`, Asian via
`implied_volatility_asian()`, one-touch/no-touch via
`implied_volatility_one_touch()`/`implied_volatility_no_touch()`),
inverting `fx/european.py` (Garman-Kohlhagen), `fx/barrier.py`,
`fx/digital.py`, `fx/american.py`, `fx/asian.py`, and `fx/one_touch.py`
instead. Same shared solvers, same caveats (knock-out non-monotonicity,
BAW-vs-tree for American, Turnbull-Wakeman-vs-Monte-Carlo for Asian,
one-touch's inverted price/vol direction).

### `options_calc/fx/g10.py`
**Not a new pricing model** -- market-convention reference data for the
ten G10 currencies (USD, EUR, JPY, GBP, CHF, CAD, AUD, NZD, SEK, NOK) and
all 45 pairs formed from them (9 standard USD pairs + 36 crosses). Every
`fx/*.py` pricer already works for any currency pair mechanically (they
just take numbers); this module encodes the convention knowledge needed
to use them CORRECTLY for a specific named pair, which is easy to get
backwards:

- **`pair_convention(pair)`** — returns which currency is base/quote for
  a G10 pair, and which currency premium is conventionally paid in.
  Quoting orientation is NOT consistent across pairs: EUR/USD quotes USD
  per 1 EUR (EUR is base), but USD/JPY quotes JPY per 1 USD (USD is
  base) -- the opposite orientation. Covers the 9 pairs against USD
  (individually verified, well-documented convention) plus the 36 cross
  pairs (base/quote via a standard FX market hierarchy: EUR > GBP > AUD >
  NZD > USD > CAD > CHF > SEK > NOK > JPY).
- **`domestic_and_foreign_currency(pair)`** — translates a pair into
  which currency code belongs in this package's `domestic_rate` vs.
  `foreign_rate` arguments (domestic = quote currency, foreign = base
  currency), to prevent silently swapping them.
- **`recommended_delta(result, pair)`** — every `fx/*.py` pricer always
  computes both `delta` and `delta_premium_adjusted` (see
  `fx/_conventions.py`), but only one is the market-standard number for a
  given pair: premium-adjusted for EUR/GBP/AUD/NZD pairs (premium
  conventionally paid in the base currency), raw delta for USD/JPY,
  USD/CHF, USD/CAD, USD/SEK, USD/NOK (premium conventionally paid in
  USD). This picks the correct one instead of requiring the caller to
  know the convention.
- **Known limitation:** the 9 USD pairs' `premium_currency` is verified,
  standard convention; the 36 crosses' `premium_currency` is a
  SIMPLIFIED DEFAULT ("base" for every cross), not individually verified
  per pair -- a specific desk's actual convention for a specific cross
  should be checked before relying on this for real trading. Does not
  fetch real interest rates, vol, or spot -- purely convention lookup on
  top of numbers the caller still supplies.

### `options_calc/fx/calendars.py`
**Not a new pricing model** -- correct FX settlement calendar and spot-date
reference functions, using QuantLib's real holiday calendars for each G10
market (`ql.TARGET` for EUR, `ql.UnitedStates(Settlement)` for USD,
`ql.Japan`, `ql.UnitedKingdom`, `ql.Switzerland`, `ql.Canada`,
`ql.Australia`, `ql.NewZealand`, `ql.Sweden`, `ql.Norway`).

- **`joint_calendar(pair)`** — the correct calendar for FX settlement: a
  day only counts as a business day if it's a business day in BOTH
  currencies' home markets. Verified in tests to be strictly more
  restrictive than either single calendar (e.g. Dec 26 is a EUR/USD
  holiday via TARGET's Boxing Day closure, even though it's not
  necessarily a US-only holiday).
- **`spot_lag_days(pair)`** — the standard FX spot settlement lag: 2
  business days for every G10 pair except USD/CAD, a well-known standard
  exception at 1 business day (North American payment systems allow
  next-day settlement for these two).
- **`spot_date(pair, trade_date)`** — advances a trade date by the
  pair's spot lag using its joint calendar, correctly skipping weekends
  and holidays in either currency.
- **Scope limitation, stated deliberately:** this module provides correct
  reference functions but does NOT yet change how any pricer computes
  dates internally. Every pricer in `equity/` and `fx/` still takes a
  plain `T` in years and measures it from "today" via `_engine.py`'s
  `year_fraction_to_date()`, using `NullCalendar()` (no holidays at all).
  Wiring real spot-date-aware, holiday-aware dates into the pricers
  themselves would require changing their interface (taking explicit
  trade/expiry dates rather than a plain T) -- a real design decision,
  not just a calendar lookup, deliberately left as a follow-up rather
  than decided unilaterally here.

### `options_calc/fx/rate_curves.py`
**PLACEHOLDER DATA, explicitly not live or current.** A mutable,
in-memory lookup of an illustrative short-term rate per G10 currency, so
pricers can be called by pair name (`get_domestic_and_foreign_rates(pair)`)
instead of the caller supplying two rate numbers from memory every time.

- **`get_rate(currency)`** / **`set_rate(currency, rate)`** — read/write
  the in-memory table. `set_rate()` is the intended way to feed in a real
  rate (from a file, an API, or eventually a live feed) without changing
  this module's interface or any pricer.
- **`get_domestic_and_foreign_rates(pair)`** — resolves a pair (via
  `g10.py`'s `domestic_and_foreign_currency()`) to the correctly-oriented
  `(domestic_rate, foreign_rate)` tuple, ready to pass straight into any
  `fx/*.py` pricer.
- **Explicitly NOT real data:** the numbers baked in are plausible-looking
  placeholders (roughly representative of what each currency's
  policy-adjacent short rate might be), not a snapshot of any actual date.
  Interest rates move constantly; every value here needs to be overwritten
  via `set_rate()` before being used for anything beyond testing the
  plumbing. This is the same category of limitation as `VolSurface`'s
  missing live feed -- see MODELS.md's Bloomberg notes.

### `options_calc/vol_surface.py`
**Not a new option type or pricing model** -- a data structure that fixes
the flat-vol limitation listed against every file above. `VolSurface` holds
a strike x tenor grid of implied vols (each one independently backed out
from a real market price via an `implied_vol.py` solver) and interpolates
between grid points to answer "what vol applies at this exact strike and
expiry?" via `get_vol(K, T)`.

- Bilinear interpolation between grid points; flat extrapolation beyond
  the grid's edges (assumes the nearest edge's vol holds beyond it, rather
  than guessing at an unobserved trend).
- Public class (not underscore-prefixed) -- callers construct their own
  surface from whatever data they have, then read `get_vol(K, T)` and pass
  the result into any pricer's `sigma` parameter exactly as before. No
  pricer file needs to change to use this.
- Source-agnostic by design: the constructor takes plain Python lists, with
  no opinion about whether the data was typed by hand, read from a file,
  or (eventually) pulled from a live feed such as Bloomberg. See the
  Roadmap entry below for why the actual Bloomberg connection isn't built
  yet, and the ticker conventions to use when it is (`{PAIR}V{TENOR}
  Curncy` for FX ATM vol, e.g. `EURUSDV1M Curncy`; skew via
  `{PAIR}25R{TENOR} Curncy` / `{PAIR}25B{TENOR} Curncy` risk-reversal/
  butterfly quotes).
- Strike-native. For FX's native delta-space quoting convention, see
  `fx/delta_vol_surface.py` below instead.

### `options_calc/fx/delta_vol_surface.py`
**Not a new pricing model** -- `FXDeltaVolSurface`, the FX-native
counterpart to `VolSurface`: takes market data in the way FX vol is
actually quoted (per tenor: ATM vol, 25-delta risk reversal, 25-delta
butterfly, optionally also 10-delta RR/BF) instead of a strike grid, and
still answers `get_vol(K, T)`.

- **The math:** `vol_X_call = atm_vol + bf_X + 0.5*rr_X`,
  `vol_X_put = atm_vol + bf_X - 0.5*rr_X` (standard RR/BF decomposition,
  applied at X=25 always, and at X=10 too if `rr_10`/`bf_10` are given).
  Each vol corresponds to a different delta and therefore a different
  strike; finding that strike is a CLOSED-FORM inversion of the
  Black-Scholes spot-delta formula (no iterative solver needed, since
  sigma is already known and only K is unknown) -- verified in tests by
  pricing at the solved strike/vol and confirming the resulting delta
  actually comes back to ~0.25.
- **Bug found and fixed (audit):** the strike solve used to use the exact
  nominal T, but every pricer in this package rounds T to a whole
  calendar day first (`_engine.py`'s `year_fraction_to_date`). For
  non-round tenors this made the "25-delta" strike NOT actually price to
  0.25 delta once run through `fx/european.py`'s pricer -- worst for
  short tenors (measured ~0.7% relative delta error at 1M). Fixed by
  rounding T the same way before solving for K (`_rounded_T` in
  `delta_vol_surface.py`); see
  `tests/fx/test_delta_vol_surface.py::test_25_delta_strikes_actually_price_to_25_delta_short_tenor`.
- **10-delta support (optional):** passing `rr_10`/`bf_10` (both, or
  neither) produces a 5-point smile per tenor (10P, 25P, ATM, 25C, 10C)
  instead of 3, capturing more of the tail shape. **Real, non-obvious
  finding from testing this:** whether the 10-delta wing ends up richer
  than the 25-delta wing is NOT automatic just because the risk reversal
  is more negative further out -- it depends on whether the butterfly
  (curvature) term grows enough to offset the skew's pull. A strong
  enough skew can make the call wing's vol *decrease* further
  out-of-the-money even with a typical negative RR; the put-side
  direction (richer further out, given negative RR) is the more robust
  one, since the RR and BF terms there don't offset each other the same
  way. See `tests/fx/test_delta_vol_surface_10d.py` for both cases
  demonstrated explicitly with real numbers.
- **ATM convention:** "ATM forward" (`K = S * exp((r_d - r_f)*T)`), the
  simpler of a few standard ATM conventions (a more elaborate
  delta-neutral-straddle convention exists and isn't used here).
- **Interpolation:** two-step, since each tenor's strikes genuinely
  differ from every other tenor's (real, not an artifact) -- interpolate
  within a tenor's own smile by strike at each of the two bracketing
  tenors, then interpolate those two results across tenor. Does not
  reuse `VolSurface`'s shared-strike-grid machinery, since that
  assumption doesn't hold here.
- **Known limitations:** uses raw (non-premium-adjusted) delta for the
  strike solve uniformly across all pairs, not the per-pair-correct
  convention from `fx/g10.py`'s `recommended_delta()`; "ATM forward"
  only, not delta-neutral-straddle ATM.

### `options_calc/structures.py`
**Not a new pricing model** -- generic machinery for combining multiple
already-priced option legs into one position. `combine()` takes any number
of `(result_dict, quantity)` pairs (quantity: +1.0 long, -1.0 short, or any
other signed multiple) and sums each field across all legs. This is plain
addition, not new math -- every leg is priced by an existing pricer in
`equity/` or `fx/` first.

`combine()` sums the UNION of keys present across all legs, not a fixed
set of six -- previously it only ever summed `{price, delta, gamma, theta,
vega, rho}` and silently dropped every extra field (`rho_foreign`,
`rho_dividend`, `delta_premium_adjusted`) when building a structure or
portfolio position. Fixed: a leg missing a given key (e.g. an equity leg
has no `rho_foreign`) now contributes 0 for it -- correct, since an equity
position genuinely has no foreign-rate risk -- rather than the field
disappearing from the combined result entirely. All of these fields are
linear in each leg's own values for legs on the same underlying/spot, so
summing after scaling by quantity is exact, not an approximation.

### `options_calc/equity/structures.py` and `options_calc/fx/structures.py`
Named multi-leg structures built on `combine()` + each asset class's
`european.py` pricer:

- **`straddle`** — long call + long put, same strike/expiry. A pure bet
  on the SIZE of a move (either direction); long gamma, long vega,
  negative theta, since both legs are long options and their sensitivities
  add rather than offset.
- **`strangle`** — long put (lower strike) + long call (higher strike).
  Same directional idea as a straddle, cheaper to put on since both legs
  start out-of-the-money, but needs a larger move to profit.
- **`risk_reversal`** — long one side, short the other, at two different
  strikes. A directional bet financed by selling the opposite side's
  optionality. This is literally the FX market's skew-quoting instrument
  (Bloomberg's `EURUSD25R1M Curncy` is this exact structure's price,
  quoted directly as a vol difference rather than a dollar price).
- **`collar`** — long a protective put (lower strike) + short a call
  (higher strike) to help finance it. Mechanically identical to a bearish
  `risk_reversal`, named separately because it signals the hedging use
  case (protecting an existing position) rather than a standalone
  directional bet.
- **`call_spread`** — long call at a lower strike, short call at a higher
  strike. Cheaper, capped-upside way to express a bullish view than an
  outright long call.
- **`put_spread`** — long put at a higher strike, short put at a lower
  strike. The bearish mirror of `call_spread`.
- **`butterfly`** — long 1 at K_low, short 2 at K_mid, long 1 at K_high
  (three legs, all the same option type). The opposite bet from a
  straddle: profits if spot PINS near K_mid, loses (capped at net
  premium) if spot moves far either way -- short gamma, short vega,
  positive theta near K_mid, with strictly bounded risk (unlike an
  outright short straddle's unlimited risk).

All structures return the same shape as the pricer(s) they're built from --
`{price, delta, gamma, theta, vega, rho}` plus whichever extra fields that
pricer includes (`rho_dividend` for equity structures; `rho_foreign` and
`delta_premium_adjusted` for FX structures), correctly summed across every
leg by `combine()`'s union-of-keys behavior. Tested against structural
properties (straddle price = sum of its two legs exactly; strangle
cheaper than straddle; bullish risk reversal has positive delta; collar
cheaper than the protective put alone; spreads cheaper than the outright
option; butterfly is short gamma/vega, and its price equals the exact
weighted sum of its three legs) rather than fixed reference
numbers, the same style used for American/Asian pricers.

### `options_calc/portfolio.py`
**Not a new pricing model** -- the layer above everything else in this
file, turning per-unit pricer/structure output into position-level and
book-level risk. Two classes:

- **`Position`** — one instrument held in some quantity: a `label`, an
  `asset_class` (grouping key, e.g. "equity" or "fx"), the `result` dict
  from any pricer or structure, a signed `quantity` (positive long,
  negative short), and `fx_rate_to_base` (default 1.0). `.scaled()`
  returns that position's contribution to book Greeks -- the per-unit
  result multiplied by `quantity * fx_rate_to_base`, via `structures.py`'s
  `combine()`.
- **`Portfolio`** — a list of `Position`s. `.total()` sums every
  position's scaled Greeks (each already converted via its own
  `fx_rate_to_base`) into one net dict (the "Portfolio Totals" row in a
  risk grid). `.by_asset_class()` and `.by_label()` produce subtotals
  grouped by those fields (e.g. separate "Equity"/"FX" rows).

**Currency normalization mechanism:** `fx_rate_to_base` on `Position` is
the fix for the cross-currency issue found while first testing this --
every position converts into the portfolio's reporting currency via its
own supplied rate before being summed. This is a manual, caller-supplied
mechanism, not automatic: the package does not fetch or know real FX
rates itself (no live market data connection exists yet -- see Roadmap),
so it's on the caller to supply the correct rate for each position. The
default of 1.0 is only correct when every position already shares one
common quoting currency.

**Documentation gap noted (audit):** `.total()`/`by_asset_class()`/
`by_label()` will happily sum Greeks across positions on entirely
different underlyings (an SPX delta + an EURUSD delta + a gold delta),
correctly currency-converted. That's intentional and tested, but once
you're summing across asset classes, "portfolio delta/gamma/vega" is
just each leg's own native-unit Greek added together (index points, FX
pips, commodity dollars) -- not a single coherent risk number the way it
is within one underlying. A real desk normalizes to a common basis (e.g.
$-delta-per-1%-move) before summing across asset classes; this package
does not do that normalization for you.

### `options_calc/commodity/european.py`, `american.py`, `asian.py`
**Exercise/payoff:** European, American, and Asian respectively -- same
taxonomy as equity/FX. **Underlying:** a commodity futures/forward price
(gold, silver, oil, etc.), not a spot price.

- **Model:** Black-76 -- the standard model for options on a futures
  price. Mathematically identical to this package's existing
  Black-Scholes-Merton engine with the dividend/cost-of-carry rate set
  EQUAL to the discount rate (`dividend_rate = r`): the `r - q` drift term
  in `d1` cancels exactly, which is economically correct since a futures
  price has no further cost-of-carry drift under the risk-neutral measure.
  These three files are thin wrappers around the same shared `_engine.py`
  every equity/FX pricer uses, passing the futures price `F` in place of
  `S` and `dividend_rate=r`.
- **Engines:** `AnalyticEuropeanEngine` (European, closed-form), the same
  Cox-Ross-Rubinstein binomial tree (American), the same Monte Carlo
  engine (Asian) as their equity/FX counterparts.
- **A real bug found and fixed while building this:** the shared engine's
  `rho`/`rho_dividend` split treats the two rates as independently
  bumpable (correct for equity/FX, which genuinely have two separate
  rates). Here they're tied to the SAME value (`r`), so bumping one alone
  while holding the other fixed produces a partial derivative that
  doesn't correspond to any real scenario. Fixed by summing the two raw
  partials into a single, verified-correct `rho` (checked against a
  direct finite-difference bump of `r` with both curves moved together);
  the raw `rho_dividend` field is removed from commodity output entirely
  rather than exposed misleadingly.
- **Asian is genuinely realistic here**, more so than for equity: real
  commodity hedges (an airline hedging average jet fuel cost, a producer
  hedging average oil revenue over a quarter) are natively Asian, since
  the exposure itself is an average, not a single date's price.
- **Known limitation:** many real commodity futures options are
  American-style in practice (e.g. NYMEX crude oil options) -- use
  `american.py` for those rather than defaulting to `european.py`.

### `options_calc/commodity/implied_vol.py`, `structures.py`, `__init__.py`
Same pattern as equity/FX: `implied_vol.py` inverts `european.py` (closed
form, cheap and stable); `structures.py` provides the same named
multi-leg positions (`straddle`, `strangle`, `risk_reversal`, `collar`,
`call_spread`, `put_spread`, `butterfly`) built via `combine()`.

### `options_calc/rates/_engine.py`
Internal shared module, DELIBERATELY SEPARATE from `options_calc/_engine.py`
(not built on top of it) -- rates derivatives have no "spot": a swaption
pays off on a forward SWAP rate, a cap/floor is a strip of options on
forward LIBOR-style rates. Both priced with the market-standard Black-76
lognormal-forward-rate model.

- **Multi-curve support.** `discount_rate` and `forecast_rate` are
  independent optional inputs across `swaption.py`/`cap_floor.py` (both
  default to `r` when omitted, so every single-rate call site keeps
  working unchanged). `discount_rate` drives discounting; `forecast_rate`
  drives only the floating-index/caplet forecast -- making `delta`
  (forecast-rate sensitivity) and `rho` (discount-rate sensitivity)
  genuinely separable for the first time (see
  `finite_difference_multi_curve_greeks`). Both curves are STILL flat
  `FlatForward` curves, not bootstrapped from real market instruments --
  this models a *level* discounting/forecasting basis, not real curve
  shape. No convexity adjustment, no CMS measure change, no cross-currency
  basis.
- **Volatility convention: LOGNORMAL, not normal/bp.** Rates markets quote
  vol both ways (lognormal "Black" vol as a decimal, or normal "Bachelier"
  vol in basis points on the rate). Every `sigma` in `rates/` is lognormal
  (0.20 = 20%), fed into `ql.BlackSwaptionEngine`/`ql.BlackCapFloorEngine`
  with zero displacement. Silently mixing the two conventions is a
  classic, real desk-blowup-grade bug -- worth double-flagging since
  equity/FX's `sigma` is also lognormal, but on a spot price rather than a
  forward rate, so the number looks familiar while meaning something
  different.
- **Flat vol (no vol-cube) remains true for `swaption.py`/`cap_floor.py`
  directly** -- strike-dependent smile pricing now exists via
  `sabr.py` (below), layered on top rather than built into the base
  pricer.
- **Day count/calendar:** `Actual365Fixed()`/`NullCalendar()`, matching
  the rest of `options_calc` -- a project-wide simplifying convention, not
  a currency-correct market convention (real USD swaps use Act/360
  floating legs, 30/360 fixed legs, currency-specific holiday calendars).
- **Known quirk:** a schedule starting within a few days of "today" is
  floored forward (`_MIN_START_LAG_DAYS`) to avoid QuantLib needing a
  historical fixing this package doesn't store. Invisible for any
  realistic forward-starting instrument; only matters for an
  immediately-starting cap/swaption.

### `options_calc/rates/swaption.py`
Prices a European swaption (payer/receiver -- this asset class's
call/put analogue). **Exercise:** European only (Bermudan swaptions, which
allow exercise on multiple dates, are out of scope).

- **Inputs:** `fixed_rate` (the swaption's strike), `expiry` (years --
  also when the underlying swap starts, i.e. this prices a "T-into-N"
  swaption), `swap_tenor` (years), `r`, `sigma` (lognormal), `notional`,
  `option_type` ('payer' or 'receiver').
- **Simplification:** the underlying swap's floating leg is assumed to
  reprice at par -- standard for a first-pass swaption pricer, not a
  fully modeled floating leg with real spreads.
- **Greeks:** ALL bump-and-reprice, even though QuantLib's `Swaption`
  object exposes analytic `delta()`/`vega()` for Black-76 -- a deliberate
  choice so all five Greeks (gamma/theta have no QuantLib analytic form
  here) are computed the same way and are directly comparable to each
  other, rather than mixing analytic and finite-difference numbers
  silently.
- **Verified via a real no-arbitrage identity:** payer swaption value
  minus receiver swaption value equals the forward-starting swap's own
  NPV (confirmed to within $1 on a $1,000,000-notional 5y-into-10y
  swaption) -- the swaption analogue of put-call parity.

### `options_calc/rates/cap_floor.py`
Prices an interest rate cap/floor -- a strip of caplets/floorlets, each
effectively a call/put on the forward rate resetting for its own accrual
period, summed by `ql.BlackCapFloorEngine`.

- **Inputs:** `strike`, `start` (years to first accrual period), `tenor`
  (years, split into `freq_months`-long periods, default 6), `r`, `sigma`,
  `notional`, `freq_months`.
- **Known limitation, stated plainly: FLAT VOL ACROSS THE ENTIRE STRIP.**
  One `sigma` is applied to every caplet regardless of its own expiry.
  Real caps are risk-managed off a vol cube (different vol per caplet
  expiry, often per strike). This is a materially simplified starting
  point, not a production-grade cap pricer -- a real version needs
  `ql.OptionletVolatilityStructure` as an input, explicitly out of scope
  here.
- **Known quirk:** `theta` comes back as exactly 0.0 for a `start` value
  within a few days of "now" -- an artifact of the fixing-lag floor
  described in `_engine.py` (the 1-day bump used for theta lands on the
  same floored start date before and after). Does not affect any
  realistic cap/floor starting more than a few days out.
- No amortizing/accreting notional support (one flat notional throughout).

### `options_calc/rates/implied_vol.py`
`implied_volatility_swaption()`, `implied_volatility_cap()`,
`implied_volatility_floor()` -- inverts the pricers above via QuantLib's
own `Swaption.impliedVolatility`/`CapFloor.impliedVolatility` (Newton's
method), the same closed-form-backed pattern `_engine.py`'s
`implied_volatility_european()` uses. Every value is lognormal vol,
matching `swaption.py`/`cap_floor.py`. Inherits every limitation of the
underlying pricer it inverts (single flat curve, flat vol across a cap's
strip, at-par floating leg) -- the vol recovered is "the flat lognormal
vol consistent with this simplified model reproducing that price," not a
market-standard, SABR-consistent implied vol.

### `options_calc/rates/sabr.py`
Strike-dependent (smile-consistent) swaption vol, as an alternative to
`swaption.py`'s flat sigma. `sabr_swaption_vol(strike, forward, expiry,
alpha, beta, rho, nu)` wraps QuantLib's own `ql.sabrVolatility` (Hagan's
closed-form SABR approximation) to get a lognormal vol at a given strike;
`price_swaption_sabr(...)` feeds that vol straight into the existing
flat-vol Black-76 pricer -- SABR supplies the sigma, it does not replace
or bypass the pricer.

- **Parameters, in plain terms:** `alpha` (vol level, >0), `beta` (CEV
  backbone exponent in [0,1] -- 1 is lognormal-like, 0 is normal/
  Bachelier-like; a common desk choice is 0.5, but nothing is chosen for
  you), `rho` (correlation of rate and its own vol, in [-1,1] -- drives
  the smile's skew/asymmetry), `nu` (vol-of-vol, >=0 -- drives the
  smile's curvature; nu=0 collapses to no smile at all).
- **Known limitations, stated plainly: NO CALIBRATION** (the caller
  supplies all four SABR parameters directly -- fitting them to observed
  market strike/vol pairs is a separate, non-trivial optimization problem,
  explicitly out of scope); **one (expiry, tenor) bucket at a time**, not
  a managed SABR surface across a full vol cube; **unshifted SABR only**
  (assumes strike and forward are both positive -- no support for
  negative-rate shifted SABR).
- Hagan's formula itself was not independently re-derived by hand --
  relies entirely on QuantLib's own implementation, cross-checked via the
  nu=0 degenerate case (smile collapses to a flat-ish backbone vol, the
  expected behavior per Hagan's own paper) and confirming
  `price_swaption_sabr` reproduces the flat-vol pricer bit-for-bit when
  fed the same vol (proving SABR only supplies sigma, doesn't alter the
  pricer).
- **Critical bug found and fixed (audit):** Hagan's approximation can
  return a NEGATIVE vol for parameter combinations well within the
  documented "valid" ranges above -- not exotic misuse, but plain desk
  values like alpha=0.04/beta=0.5 at long expiry with high nu and
  strongly negative rho (found starting at expiry=10y, nu=2.0, rho=-0.9;
  a grid scan found ~14% of combinations across the full documented
  valid-parameter space go negative). This used to flow straight into
  `price_swaption_sabr` -> `ql.BlackSwaptionEngine` with no error,
  producing a price that happened to look normal (Black-76 uses sigma^2)
  but with Greeks silently wrong-signed for anyone finite-differencing
  around it. Fixed: `sabr_swaption_vol` now raises `ValueError` if the
  result isn't strictly positive, explaining this is a known Hagan
  breakdown regime, not a bug in the wrapper. See
  `tests/rates/test_sabr.py::test_breakdown_regime_raises_instead_of_returning_negative_vol`.

### `options_calc/rates/bermudan_swaption.py`
Bermudan swaptions (exercisable on a discrete date set, not just a single
final expiry). **Model: one-factor Hull-White (`ql.HullWhite`) +
`ql.TreeSwaptionEngine`** (trinomial tree, backward induction) -- a
genuinely different model from `swaption.py`'s closed-form Black-76,
because pricing early-exercise value needs the continuation value of
waiting at each exercise date, which requires modeling the evolution of
the WHOLE term structure, not just one forward rate's distribution at one
date. Black-76 has no notion of continuation value.

- **Model choice, stated plainly:** one-factor, not two-factor (G2) --
  all points on the curve move perfectly correlated (one driving Brownian
  motion), so this cannot represent a genuine curve twist/steepening
  independent of a roughly-parallel shift. A real desk pricing Bermudans,
  especially longer-dated or curve-shape-sensitive ones, would typically
  use a two-factor model for that reason. One-factor Hull-White is the
  deliberately simplest defensible choice here, not the most accurate one.
- **Known limitations:** `hw_mean_reversion`/`hw_volatility` (Hull-White's
  `a` and `sigma`) are NOT calibrated to market swaption/cap vols --
  supplied directly by the caller; a real desk calibrates these to a
  basket of co-terminal European swaptions first. Exercise dates are
  mechanically generated (evenly spaced from `exercise_frequency`), not
  read off a real deal's actual coupon schedule. `tree_steps` controls
  discretization error with no auto-convergence check (default 100 is
  reasonable-but-arbitrary, not tuned per input).
- **Moderate bug found and fixed (audit):** with no calibration, nothing
  previously stopped an implausible `hw_volatility` (e.g. a typo'd 1.0
  instead of 0.01 -- real one-factor Hull-White calibrations are
  typically 0.5%-2%/year) from silently producing a structurally
  normal-looking but economically nonsensical price (at hw_volatility=1.0,
  a swaption "priced" at 4.68x its own notional, with no error). Fixed:
  `hw_volatility` is now checked against a plausible range
  `(0, 0.05]` and raises `ValueError` outside it, both in
  `price_bermudan_swaption` and `price_european_swaption_hw` (both go
  through the same `_hull_white_tree_engine` helper). See
  `tests/rates/test_bermudan_swaption.py::test_implausible_hw_volatility_raises`.
- **Verified via the model-consistent form of "more exercise can only add
  value":** `price_european_swaption_hw()` exists specifically so a
  Bermudan can be compared against a European swaption under the SAME
  Hull-White model and tree engine (rather than comparing against
  `swaption.py`'s different Black-76 model, which would make the
  comparison model-inconsistent) -- confirmed Bermudan >= European under
  that fair comparison, plus that the price is stable (doesn't blow up)
  as `tree_steps` increases.
- Hull-White's bond-pricing formula and the trinomial-tree construction
  were not independently re-derived by hand -- both rely entirely on
  QuantLib's own implementation.

## Planned (not yet built)

- Rates: SABR calibration to market data (parameters are supplied
  directly today, not fit); a two-factor (G2) Bermudan model instead of
  one-factor Hull-White; Hull-White calibration to a co-terminal European
  swaption basket; a proper caplet vol term structure
  (`ql.OptionletVolatilityStructure`) instead of one flat sigma per cap;
  genuinely bootstrapped (not flat) discount/forecast curves.
- Commodity: an implied vol solver for American/Asian commodity options
  (only European is covered today, matching the same American/Asian
  exclusion equity/FX have without the BAW workaround); real
  cost-of-carry modeling for commodities with storage costs/convenience
  yield distinct from the discount rate (the `dividend_rate = r`
  substitution is exact for a pure futures option, but a real physical
  commodity's own carry cost is a separate, unmodeled input here).
- Individually verify `premium_currency` for each of the 36 G10 cross
  pairs in `fx/g10.py` -- currently a simplified "base" default for all
  of them, not checked pair-by-pair the way the 9 USD pairs are.
- Replace `fx/rate_curves.py`'s illustrative placeholder rates with real
  curves per G10 currency (e.g. SOFR for USD, €STR for EUR, SONIA for
  GBP, TONA for JPY) via `set_rate()` fed from a real source -- the
  plumbing exists (lookup, override, pair resolution), only the actual
  rate values are placeholders.
- Wire `fx/calendars.py`'s correct spot-date/holiday-calendar logic into
  the actual pricers -- the reference functions exist and are tested, but
  every pricer still measures a plain T-in-years from "today" via
  `NullCalendar()` (no holidays). Doing this properly means changing
  pricer interfaces to take explicit trade/expiry dates, a real design
  decision beyond the calendar lookup itself.
- Automatic currency conversion for `Position.fx_rate_to_base` -- it
  exists and works, but requires the caller to supply the rate manually.
  Wiring it to a live rate feed depends on the same live market data
  connection as `VolSurface` below.
- Live Bloomberg (or other) market data connection to populate a
  `VolSurface` (and FX rates for `portfolio.py`) automatically instead of
  hand-typed data. Deliberately not built yet: `blpapi` only works against
  a running Bloomberg Terminal session, which isn't available in this
  environment to test against. The `VolSurface` constructor is
  intentionally source-agnostic (plain Python lists in) so this can be
  added later as a standalone adapter function that builds a surface from
  live data, with no changes needed to `VolSurface` itself or any pricer.
- Per-pair-correct delta convention in `fx/delta_vol_surface.py`'s strike
  solve (uses raw delta uniformly today; should use `fx/g10.py`'s
  `recommended_delta()` logic per pair).
- Delta-neutral-straddle ATM convention as an alternative to "ATM
  forward" in `fx/delta_vol_surface.py`.
- A more robust solver for knock-out barrier implied vol that handles
  the non-monotonic (hump-shaped) price-vs-vol relationship -- e.g.
  scanning for a bracket first, or returning both solutions when two
  exist, instead of failing outright.
- "Immediate" one-touch payout timing (paid at the moment of touch,
  rather than deferred to expiry) as an alternative to the deferred
  convention `one_touch.py` implements today.
- Iron condors and other 4+ leg structures (`combine()` already supports
  any number of legs; `call_spread`/`put_spread`/`butterfly` now cover
  the 2-3 leg cases).
