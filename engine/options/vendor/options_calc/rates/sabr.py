"""SABR-implied swaption vol -- strike-dependent (smile-consistent) vol as
an alternative to the flat sigma swaption.py otherwise requires.

THE GAP THIS FILLS
-------------------
swaption.py (and cap_floor.py) take ONE flat lognormal sigma and apply it
regardless of strike -- explicitly flagged in _engine.py's module
docstring as "no smile, no skew, no SABR." Real swaption desks observe a
smile: the implied vol actually needed to match market prices varies by
strike (and by expiry/tenor), and SABR (Stochastic Alpha Beta Rho, Hagan
et al. 2002) is the market-standard parametric model for that smile,
because it gives a fast, closed-form approximation for the lognormal vol
at any strike from four parameters, without needing a full stochastic-vol
pricing engine.

This module does NOT re-implement SABR's math -- it calls QuantLib's own
`ql.sabrVolatility`, which is exactly Hagan's well-known closed-form
approximation for the SABR-implied lognormal (Black) vol at a given
strike. Given that vol, pricing still goes through the EXISTING flat-vol
Black-76 swaption pricer (`options_calc.rates.swaption`/
`options_calc.rates._engine.price_swaption_only`) -- SABR here supplies a
strike-dependent sigma into that pricer instead of a single flat one, it
does not replace or bypass it. This gives strike-dependent, smile-
consistent swaption pricing without a from-scratch SABR pricing engine.

SABR PARAMETERS, IN PLAIN TERMS
--------------------------------
- alpha: the initial level of the (stochastic) forward-rate volatility.
  Roughly, "how vol is the vol," scaled so bigger alpha means a higher
  vol level overall. Must be > 0.
- beta: the CEV exponent on the forward rate, in [0, 1]. Controls the
  backbone of the smile -- beta=1 is lognormal-like (vol roughly constant
  in rate-relative terms), beta=0 is normal/Bachelier-like (vol roughly
  constant in absolute-rate terms). A common desk choice for rates is
  beta=0.5, but this module does not choose one for you -- the caller
  must supply it.
- rho: correlation between the forward rate and its own stochastic vol,
  in [-1, 1]. Drives the SKEW (asymmetry) of the smile -- rho != 0 means
  up-moves and down-moves in the rate are priced asymmetrically.
- nu ("vol of vol"): the volatility of the volatility process itself,
  >= 0. Drives the CURVATURE of the smile -- nu=0 collapses to no smile
  at all (flat vol equal to a CEV-adjusted alpha), higher nu means a more
  pronounced smile away from the money.

WHAT'S SIMPLIFIED / OUT OF SCOPE HERE
----------------------------------------
- NO CALIBRATION. This module does not fit (alpha, beta, rho, nu) to a
  set of observed market strike/vol pairs -- the caller supplies all four
  numbers directly (e.g. from a desk's own calibration, a vendor feed, or
  hand-picked illustrative values). A "real" SABR desk tool calibrates
  these per expiry/tenor bucket from a live vol cube; that calibration
  routine is a separate, non-trivial least-squares/optimization problem
  and is explicitly not attempted here.
- ONE (expiry, tenor) BUCKET AT A TIME. Real desks maintain a SABR
  surface -- a separate (alpha, beta, rho, nu) fit per expiry/tenor cell
  of the swaption vol cube, sometimes with a "SABR surface" model
  interpolating between cells. This module prices one swaption at a time
  given the SABR parameters for THAT swaption's own expiry/tenor bucket;
  it does not manage or interpolate a cube of buckets.
- SHIFTED SABR (for negative-rate environments) IS NOT USED. QuantLib
  supports a shift parameter for exactly this (rates SABR needs a
  positive shifted forward when true rates can go negative); this module
  calls the unshifted `ql.sabrVolatility` and therefore assumes the
  forward rate and strike are both positive, matching every other flat-
  rate assumption already made across `rates/`.
- VOLATILITY TYPE: `ql.sabrVolatility`'s output here is requested as
  ShiftedLognormal with zero shift (QuantLib's default), i.e. plain
  lognormal Black vol -- consistent with every other `sigma` in `rates/`
  (see _engine.py's module docstring on the lognormal-vs-normal
  convention).

I DID NOT independently re-derive or verify Hagan's SABR approximation
formula against a published reference by hand -- this module relies
entirely on QuantLib's own implementation of it. What I did verify: (1)
that `ql.sabrVolatility` runs and returns a single positive vol number for
reasonable inputs, and (2) the sanity check in
tests/rates/test_sabr.py -- with nu (vol-of-vol) set to 0, the smile
should collapse and the SABR vol at different strikes should be very close
to each other (a flat-ish "backbone" vol), which is the expected nu=0
degenerate case per Hagan's own paper.
"""

import QuantLib as ql

from ._engine import price_swaption_only


def sabr_swaption_vol(strike, forward, expiry, alpha, beta, rho, nu):
    """SABR-implied lognormal (Black) vol at `strike`, for a swaption
    whose underlying forward swap rate is `forward` and whose time to
    expiry (years) is `expiry`.

    strike, forward: annual decimal rates (e.g. 0.04 = 4%). Must both be
        > 0 -- see module docstring's "SHIFTED SABR" note.
    expiry: time to expiry in years, > 0.
    alpha, beta, rho, nu: SABR parameters -- see module docstring.

    Returns the lognormal vol as a decimal, suitable to feed straight
    into options_calc.rates.swaption/cap_floor's `sigma` argument.
    """
    if strike <= 0 or forward <= 0:
        raise ValueError(
            f"sabr_swaption_vol requires strike > 0 and forward > 0 (unshifted SABR -- "
            f"see module docstring); got strike={strike}, forward={forward}"
        )
    if expiry <= 0:
        raise ValueError(f"expiry must be > 0, got {expiry}")

    vol = ql.sabrVolatility(strike, forward, expiry, alpha, beta, nu, rho)

    if not (vol > 0):
        raise ValueError(
            f"SABR (Hagan approximation) produced a non-positive vol ({vol!r}) for "
            f"strike={strike}, forward={forward}, expiry={expiry}, alpha={alpha}, "
            f"beta={beta}, rho={rho}, nu={nu}. This is a known breakdown mode of "
            f"Hagan's closed-form approximation -- it can happen well within the "
            f"'valid' parameter ranges documented above, typically for long expiries "
            f"combined with high nu (vol-of-vol) and strongly negative rho. It is not "
            f"a bug in this wrapper; it means these particular parameters are outside "
            f"the region where the approximation is trustworthy. Try reducing nu, "
            f"moving rho closer to 0, or using a shorter expiry."
        )

    return vol


def price_swaption_sabr(fixed_rate, forward_rate, expiry, swap_tenor, r, notional, option_type,
                         alpha, beta, rho, nu, discount_rate=None, forecast_rate=None):
    """Price a European swaption using a SABR-implied vol at the given
    strike (`fixed_rate`) instead of a directly-supplied flat sigma.

    fixed_rate: the swaption's strike (annual, decimal).
    forward_rate: the forward swap rate to evaluate the SABR smile at --
        this is NOT derived from the curve automatically (this module
        does not build the actual forward swap to solve for its own fair
        rate); the caller supplies it directly, e.g. from
        options_calc.rates._engine.build_forward_swap's swap.fairRate(),
        or a market-observed forward. Keeping this explicit avoids
        silently mixing "the rate used to discount/forecast" with "the
        rate the smile is centered on," which are conceptually different
        even though they are often close in practice.
    expiry, swap_tenor, r, notional, option_type, discount_rate,
        forecast_rate: same meaning as options_calc.rates.swaption.price
        / options_calc.rates._engine.price_swaption_only.
    alpha, beta, rho, nu: SABR parameters for THIS swaption's own
        expiry/tenor bucket -- see module docstring. No calibration is
        performed; these are used as given.

    Returns the swaption price only (no Greeks) -- this is a pricing
    primitive, analogous to _engine.price_swaption_only, not a full Greeks
    wrapper. Greeks under a SABR smile would need to decide whether
    (alpha, beta, rho, nu) are held fixed while bumping the rate (a
    "sticky-SABR-params" assumption) or re-solving the smile at each
    bumped rate -- a real modeling choice this module does not make for
    you. Callers wanting Greeks under a fixed smile can bump `forward_rate`
    themselves (recomputing the SABR vol at each bump) and finite-
    difference the resulting prices.
    """
    sigma = sabr_swaption_vol(fixed_rate, forward_rate, expiry, alpha, beta, rho, nu)
    return price_swaption_only(
        fixed_rate, expiry, swap_tenor, r, sigma, notional, option_type,
        discount_rate=discount_rate, forecast_rate=forecast_rate,
    )
