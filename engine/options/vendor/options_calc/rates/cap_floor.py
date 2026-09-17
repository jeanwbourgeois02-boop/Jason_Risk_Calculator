"""Interest rate caps and floors -- a strip of caplets/floorlets, each an
option on the forward rate that resets for its own accrual period.

Model: Black-76 lognormal-forward-rate model, applied independently to each
caplet/floorlet in the strip (the standard market convention before SABR/
vol-cube approaches: one flat vol here, applied to every caplet in the
structure -- see FLAT VOL ACROSS THE STRIP below). QuantLib's
ql.BlackCapFloorEngine sums the closed-form Black-76 value of every
caplet/floorlet in the leg.

A cap pays max(forward_rate - strike, 0) x notional x accrual, per period
(the buyer is protected against rates rising above `strike`); a floor pays
max(strike - forward_rate, 0) x notional x accrual per period (protection
against rates falling below `strike`).

`sigma` is LOGNORMAL vol as a decimal (e.g. 0.20 = 20%), NOT normal/bp vol
-- see options_calc/rates/_engine.py's module docstring.

FLAT VOL ACROSS THE STRIP (a real simplification, stated plainly)
-------------------------------------------------------------------
A real cap/floor desk uses a different vol for each caplet (a "vol cube":
different vols by caplet expiry, and often by strike too). This pricer
takes ONE sigma and applies it to every caplet in the strip. This is a
materially simplified starting point -- it is fine for a first-pass sanity
check or an at-the-money-ish flat assumption, but it is NOT how a real cap
is actually quoted or risk-managed. A "real" version would need a full
caplet vol term structure (ql.OptionletVolatilityStructure) as an input
instead of one flat sigma. Flagged as a roadmap item, not attempted here
(see MODELS.md).
"""

from ._engine import price_cap_floor_only, finite_difference_multi_curve_greeks, _resolve_curve_rates

_DEFAULT_NOTIONAL = 1_000_000.0
_DEFAULT_FREQ_MONTHS = 6


def _price(notional, strike, start, tenor, r, sigma, freq_months, cap, discount_rate=None, forecast_rate=None):
    d_rate, f_rate = _resolve_curve_rates(r, discount_rate, forecast_rate)

    def price_only(T_, d_, f_, sigma_):
        return price_cap_floor_only(
            notional, strike, T_, tenor, d_, sigma_, freq_months, cap,
            discount_rate=d_, forecast_rate=f_,
        )

    return finite_difference_multi_curve_greeks(price_only, start, d_rate, f_rate, sigma)


def price_cap(strike, start, tenor, r, sigma, notional=_DEFAULT_NOTIONAL, freq_months=_DEFAULT_FREQ_MONTHS,
              discount_rate=None, forecast_rate=None):
    """Price an interest rate cap.

    strike: the cap rate (annual, decimal, e.g. 0.04 = 4%).
    start: time from today to the cap's first accrual period start, in
        years (0.0 for a cap starting essentially immediately -- see
        _engine.py's floored_start_date for why very-near-zero values get
        floored to a few days out).
    tenor: total length of the cap, in years, starting at `start` --
        internally split into `freq_months`-long caplet periods (default
        6-month periods, e.g. tenor=5 with the default freq gives 10
        caplets).
    r: flat rate (annual, decimal) used for both discounting and
        forecasting when discount_rate/forecast_rate are not given.
    sigma: flat LOGNORMAL vol applied to every caplet (see module
        docstring's "FLAT VOL ACROSS THE STRIP" caveat).
    notional: total notional (same for every period -- amortizing/
        accreting notional schedules are not supported).
    freq_months: caplet reset frequency, in months (6 = semiannual, the
        default; 3 = quarterly, etc).
    discount_rate: optional override for the discounting rate (e.g. an
        OIS/SOFR-style rate). Defaults to `r`.
    forecast_rate: optional override for the rate used to forecast the
        floating index each caplet resets against (e.g. a term-SOFR/
        LIBOR-style rate). Defaults to `r`. See _engine.py's MULTI-CURVE
        SUPPORT docstring section for what this does and doesn't model.

    Returns {price, delta, gamma, theta, vega, rho}, all bump-and-reprice
    (see _engine.py's finite_difference_multi_curve_greeks docstring).
    'delta' is sensitivity to forecast_rate, 'rho' to discount_rate, each
    holding the other fixed -- genuinely separable risks whenever the two
    rates differ.

    KNOWN QUIRK: theta will come back as exactly 0.0 for `start` values
    very close to 0 (an "immediately starting" cap). That is because
    _engine.py's floored_start_date() clamps any start date within
    _MIN_START_LAG_DAYS of today up to that same floor -- so the 1-day
    time bump used to compute theta lands on the identical floored start
    date both before and after the bump, and the price genuinely does not
    move. This is an artifact of the fixing-lag floor, not a claim that a
    just-starting cap has zero time decay. It does not affect caps/floors
    with a `start` more than a few days out (i.e. essentially all
    realistic ones).
    """
    return _price(notional, strike, start, tenor, r, sigma, freq_months, cap=True,
                  discount_rate=discount_rate, forecast_rate=forecast_rate)


def price_floor(strike, start, tenor, r, sigma, notional=_DEFAULT_NOTIONAL, freq_months=_DEFAULT_FREQ_MONTHS,
                 discount_rate=None, forecast_rate=None):
    """Price an interest rate floor. Same inputs/outputs as price_cap --
    see its docstring. A floor pays off when the forward rate falls below
    `strike`, a cap when it rises above -- the strip-of-options structure
    and every simplification/caveat are otherwise identical."""
    return _price(notional, strike, start, tenor, r, sigma, freq_months, cap=False,
                  discount_rate=discount_rate, forecast_rate=forecast_rate)
