"""Volatility surface: strike- and expiry-dependent implied volatility,
replacing the flat single-sigma assumption every pricer in this package
otherwise relies on.

THE PROBLEM THIS SOLVES
------------------------
Every pricer in equity/ and fx/ takes a single `sigma` and treats it as
constant no matter which strike or expiry you're pricing. Real markets
don't work that way: implied volatility varies by strike (the "smile" or
"skew" -- e.g. downside puts trade at richer vol than calls, reflecting
crash/hedging demand) and by time to expiry (the "term structure" -- e.g.
a 1-week option reacts sharply to a known near-term event without moving
a 1-year option's vol nearly as much).

A vol surface is a grid of independently observed implied vols, one per
(strike, expiry) pair, each backed out from a real market price via the
implied_vol solvers in equity/ and fx/. This module does NOT change the
pricing formula at all -- Black-Scholes-Merton and Garman-Kohlhagen are
untouched. It only changes *where sigma comes from*: instead of typing one
flat guess, you look up the correct vol for your exact strike/expiry from
market-observed data, then feed that into the pricer exactly as before.

USAGE
-----
    from options_calc.vol_surface import VolSurface
    from options_calc.equity.european import price

    surface = VolSurface(
        strikes=[5300, 5500, 5700],
        tenors=[1/12, 0.25, 0.5, 1.0],   # years: 1mo, 3mo, 6mo, 1yr
        vols=[
            [0.18, 0.14, 0.12],   # 1 month row
            [0.17, 0.145, 0.13],  # 3 month row
            [0.16, 0.15, 0.135],  # 6 month row
            [0.155, 0.15, 0.14],  # 1 year row
        ],
    )

    K, T = 5410, 0.13   # a strike/expiry that isn't exactly on the grid
    sigma = surface.get_vol(K, T)
    result = price(S=5500, K=K, T=T, r=0.045, sigma=sigma, option_type="call")

This is deliberately source-agnostic: `strikes`/`tenors`/`vols` can come
from hand-typed test data, a CSV, or (later) a live feed such as Bloomberg
-- this class has no opinion about where the numbers came from, only about
how to interpolate between them. See MODELS.md for the Bloomberg-specific
notes on why that connection isn't built here yet.
"""

import bisect


class VolSurface:
    """A strike x tenor grid of implied volatilities, with bilinear
    interpolation between grid points and flat extrapolation beyond the
    grid's edges.

    strikes: list of strike prices, any order (sorted internally)
    tenors: list of times to expiry in years, any order (sorted internally)
    vols: 2D list of implied vols, vols[i][j] = vol at tenors[i], strikes[j]
        (rows are tenors, columns are strikes -- matches how you'd read a
        vol grid off a screen: one row per expiry)
    """

    def __init__(self, strikes, tenors, vols):
        if len(vols) != len(tenors):
            raise ValueError(
                f"vols has {len(vols)} rows but there are {len(tenors)} tenors"
            )
        for row in vols:
            if len(row) != len(strikes):
                raise ValueError(
                    f"a vols row has {len(row)} entries but there are "
                    f"{len(strikes)} strikes"
                )

        # Sort everything by strike / tenor so bisect-based lookup works,
        # keeping vols aligned to the same reordering.
        strike_order = sorted(range(len(strikes)), key=lambda i: strikes[i])
        tenor_order = sorted(range(len(tenors)), key=lambda i: tenors[i])

        self._strikes = [strikes[i] for i in strike_order]
        self._tenors = [tenors[i] for i in tenor_order]
        self._vols = [
            [vols[t_i][k_i] for k_i in strike_order] for t_i in tenor_order
        ]

    def get_vol(self, K, T):
        """Return the implied volatility at strike K, tenor T (years),
        interpolating between grid points or extrapolating flatly beyond
        the grid's edges (the standard, simplest-defensible choice --
        assumes the smile/term-structure shape at the nearest edge holds
        beyond it, rather than guessing at a trend that isn't in the data).
        """
        k_lo, k_hi, k_frac = self._bracket(self._strikes, K)
        t_lo, t_hi, t_frac = self._bracket(self._tenors, T)

        v_lo_lo = self._vols[t_lo][k_lo]
        v_lo_hi = self._vols[t_lo][k_hi]
        v_hi_lo = self._vols[t_hi][k_lo]
        v_hi_hi = self._vols[t_hi][k_hi]

        # interpolate across strike at each of the two bracketing tenors...
        v_at_t_lo = v_lo_lo + (v_lo_hi - v_lo_lo) * k_frac
        v_at_t_hi = v_hi_lo + (v_hi_hi - v_hi_lo) * k_frac

        # ...then interpolate across tenor between those two results
        return v_at_t_lo + (v_at_t_hi - v_at_t_lo) * t_frac

    @staticmethod
    def _bracket(sorted_values, x):
        """Find the two grid indices bracketing x, and how far between them
        x sits (0.0 = exactly at the lower one, 1.0 = exactly at the
        upper). Clamps to the nearest edge if x falls outside the grid
        (flat extrapolation)."""
        if len(sorted_values) == 1:
            return 0, 0, 0.0

        i = bisect.bisect_left(sorted_values, x)
        if i == 0:
            return 0, 0, 0.0  # at or below the lowest grid value
        if i >= len(sorted_values):
            last = len(sorted_values) - 1
            return last, last, 0.0  # at or above the highest grid value

        lo, hi = i - 1, i
        span = sorted_values[hi] - sorted_values[lo]
        frac = 0.0 if span == 0 else (x - sorted_values[lo]) / span
        return lo, hi, frac
