"""European swaptions -- an option to enter an interest rate swap.

Model: Black-76 lognormal-forward-swap-rate model (the market-standard
"physical/cash-settled swaption" model long before SABR became standard for
handling the smile). The forward swap rate observed at expiry is assumed
lognormal; QuantLib's ql.BlackSwaptionEngine prices off that assumption
directly, closed-form (no tree/Monte Carlo needed for a EUROPEAN swaption).
Exercise: European only (exercisable at expiry only -- Bermudan swaptions,
which allow exercise at multiple dates, are out of scope here).

`sigma` is LOGNORMAL vol as a decimal (e.g. 0.20 = 20%), NOT normal/bp vol.
See options_calc/rates/_engine.py's module docstring for why that
distinction matters and for the single-flat-curve simplification this
pricer makes (no separate discount/forecast curves -- see that same
docstring for exactly what that means for 'delta' vs 'rho' below).
"""

from ._engine import (
    build_curve,
    build_swaption,
    price_swaption_only,
    finite_difference_multi_curve_greeks,
    finite_difference_curve_greeks,
    evaluation_date_scope,
    _resolve_curve_rates,
    _validate_curve_and_rate,
)

_DEFAULT_NOTIONAL = 1_000_000.0


def price(fixed_rate, expiry, swap_tenor, r, sigma, notional=_DEFAULT_NOTIONAL, option_type="payer",
          discount_rate=None, forecast_rate=None, discount_curve=None, forecast_curve=None,
          evaluation_date=None):
    """Price a European swaption.

    fixed_rate: the fixed rate of the underlying swap (annual, decimal --
        e.g. 0.04 = 4%). This is the swaption's "strike".
    expiry: time to the swaption's expiry, in years (this is also when the
        underlying swap starts, i.e. this prices a "T-into-N" swaption --
        expiry-into-swap_tenor).
    swap_tenor: length of the underlying swap, in years, starting at
        `expiry`.
    r: flat rate (annual, decimal) used for BOTH discounting and
        forecasting when none of discount_rate/forecast_rate/
        discount_curve/forecast_curve are given (the original
        single-flat-curve behavior).
    sigma: LOGNORMAL swaption vol (annual, decimal, e.g. 0.20 = 20%). NOT
        normal/bp vol -- see module docstring.
    notional: swap notional.
    option_type: 'payer' (option to pay fixed / receive float -- valuable
        if rates rise) or 'receiver' (option to receive fixed / pay float
        -- valuable if rates fall). This is this asset class's analogue of
        'call'/'put'.
    discount_rate: optional override for the rate used to discount both
        legs / the swaption itself (e.g. an OIS/SOFR-style rate). Defaults
        to `r` when omitted. Mutually exclusive with discount_curve.
    forecast_rate: optional override for the rate used to forecast the
        underlying swap's floating leg (e.g. a term-SOFR/LIBOR-style
        rate). Defaults to `r` when omitted. Supplying a forecast_rate
        different from discount_rate introduces a genuine
        discounting/forecasting basis -- see _engine.py's MULTI-CURVE
        SUPPORT docstring section for exactly what is and isn't modeled
        by this (still both flat curves, not bootstrapped). Mutually
        exclusive with forecast_curve.
    discount_curve, forecast_curve: optional `ql.YieldTermStructureHandle`
        overrides -- when given, used DIRECTLY (relinked into the pricing
        engine / floating-rate index) instead of a flat FlatForward built
        from discount_rate/forecast_rate/r. See _engine.py's CURVE-INPUT
        SUPPORT docstring section. Passing a curve together with its flat-
        rate counterpart (e.g. discount_curve and discount_rate) raises
        ValueError.
    evaluation_date: optional ql.Date; None reproduces the historical
        "evaluate as of today" behavior. The global QuantLib evaluation
        date is always restored to whatever it was before this call on
        exit, regardless of what is passed here -- see _engine.py's
        evaluation_date_scope.

    Returns {price, delta, gamma, theta, vega, rho}.

    Greeks are ALL bump-and-reprice (finite differences) here, even though
    QuantLib's Swaption object does expose analytic delta()/vega() for the
    Black-76 closed form. Bump-and-reprice was used instead so all five
    Greeks are computed the same way, on the same footing, and so gamma/
    theta (which QuantLib's Swaption object does NOT expose analytically)
    are directly comparable to delta/vega rather than mixing an analytic
    number with finite-difference ones.

    'delta' is d(price)/d(forecast_rate or forecast_curve) and 'rho' is
    d(price)/d(discount_rate or discount_curve), each holding the other
    fixed -- see _engine.py's finite_difference_multi_curve_greeks (flat-
    rate path) / finite_difference_curve_greeks (curve-input path, used
    whenever either discount_curve or forecast_curve is given -- see that
    function's docstring for the spread-bump method used there instead of
    rebuilding a flat curve). These are genuinely separable sensitivities
    whenever discount and forecast differ (or are supplied/defaulted from
    the same `r` but evaluated as two independent bumps).
    """
    _validate_curve_and_rate(discount_rate, discount_curve, "discount")
    _validate_curve_and_rate(forecast_rate, forecast_curve, "forecast")

    if discount_curve is not None or forecast_curve is not None:
        with evaluation_date_scope(evaluation_date) as today:
            d_curve0 = discount_curve if discount_curve is not None else build_curve(
                today, r if discount_rate is None else discount_rate)
            f_curve0 = forecast_curve if forecast_curve is not None else build_curve(
                today, r if forecast_rate is None else forecast_rate)

            def price_only(T_, d_, f_, sigma_):
                return price_swaption_only(
                    fixed_rate, T_, swap_tenor, r, sigma_, notional, option_type,
                    discount_curve=d_, forecast_curve=f_, evaluation_date=today,
                )

            return finite_difference_curve_greeks(price_only, expiry, d_curve0, f_curve0, sigma)

    d_rate, f_rate = _resolve_curve_rates(r, discount_rate, forecast_rate)

    def price_only(T_, d_, f_, sigma_):
        return price_swaption_only(
            fixed_rate, T_, swap_tenor, d_, sigma_, notional, option_type,
            discount_rate=d_, forecast_rate=f_, evaluation_date=evaluation_date,
        )

    return finite_difference_multi_curve_greeks(price_only, expiry, d_rate, f_rate, sigma)
