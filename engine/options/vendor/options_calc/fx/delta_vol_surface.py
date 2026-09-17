"""FX delta-quoted volatility surface.

THE GAP THIS FILLS
-------------------
`vol_surface.py`'s VolSurface is strike-quoted: you supply a grid of
(strike, tenor) -> vol. That matches how equity/index vol is typically
observed. Real FX vol is NOT typically quoted this way -- it's quoted per
tenor as three numbers: the at-the-money vol, the 25-delta risk reversal
(RR, the skew: vol_call - vol_put), and the 25-delta butterfly (BF, the
symmetric smile: the average of vol_call and vol_put above the ATM vol).
This is exactly the ATM/RR/BF quoting convention referenced back in
MODELS.md's Bloomberg ticker notes (EURUSDV1M, EURUSD25R1M, EURUSD25B1M).

This class converts that delta-quoted market data into the strikes and
vols needed to actually price something, then interpolates between them.

THE MATH
--------
Per tenor, from (atm_vol, rr_25, bf_25):
    vol_25_call = atm_vol + bf_25 + 0.5 * rr_25
    vol_25_put  = atm_vol + bf_25 - 0.5 * rr_25
(standard market decomposition: RR = vol_call - vol_put,
BF = (vol_call + vol_put)/2 - atm_vol)

Each of those three vols corresponds to a DIFFERENT delta (0.25 call,
ATM, -0.25 put), which means a DIFFERENT strike (strikes move with vol
and tenor). Finding that strike requires inverting the Black-Scholes
spot-delta formula for K, which -- unlike a generic implied-vol solve --
has a closed form here (no iteration needed), because we already know
sigma; the only unknown is K:

    d1 = N^-1(target_delta * exp(r_f * T))          [call]
    d1 = -N^-1(-target_delta * exp(r_f * T))         [put]
    K = S * exp((r_d - r_f + 0.5*sigma^2)*T - d1*sigma*sqrt(T))

The ATM strike uses the simplest standard convention, "ATM forward"
(K_ATM = S * exp((r_d - r_f)*T), i.e. the forward price) -- one of a few
ATM conventions used in practice (a more elaborate "delta-neutral
straddle" ATM exists too); this is the simpler, still standard, choice.

Because each tenor's three strikes differ from every other tenor's (this
is real, not an artifact), this class does NOT reuse VolSurface's shared
strike-grid assumption. Interpolation is two-step: interpolate within a
tenor's own 3-point smile by strike, at each of the two bracketing
tenors, then interpolate those two results across tenor.

10-DELTA SUPPORT (OPTIONAL)
----------------------------
Real FX vol surfaces are often quoted with a SECOND, more extreme smile
point too -- 10-delta risk reversal and butterfly, alongside the 25-delta
ones -- to capture more of the tail/skew shape than a single 25-delta
point can. `rr_10`/`bf_10` are optional constructor arguments: if given,
the same RR/BF decomposition and delta-to-strike formula are applied at
target delta 0.10 (call) / -0.10 (put), producing a 5-point smile per
tenor (10P, 25P, ATM, 25C, 10C) instead of 3. If omitted, behavior is
identical to the 3-point-only version.

KNOWN LIMITATIONS
-----------------
- Uses raw (non-premium-adjusted) spot delta for the strike solve, for
  every pair uniformly. Real market strike-solving convention varies by
  pair (see fx/g10.py's recommended_delta) -- this is a documented
  simplification, not a per-pair-correct implementation.
- ATM convention is "ATM forward," not the more elaborate
  delta-neutral-straddle convention some desks use.
- The strike solve rounds T to a whole number of calendar days before
  inverting for K, matching the day-rounding every pricer in this package
  applies internally (_engine.py's year_fraction_to_date). Without this,
  the solved strike targets the exact nominal delta at the unrounded T,
  but is then priced by the package's pricers at the rounded T, which
  measurably misses the target delta (up to ~0.7% relative for a 1M
  tenor). Rounding first keeps the two consistent.
"""

import bisect
import math

from scipy.stats import norm


def _rounded_T(T):
    """Match _engine.py's year_fraction_to_date: every pricer in this
    package rounds T to a whole number of calendar days (round-half-up)
    before pricing, via Actual365Fixed. Solving for a strike at the exact
    nominal T (e.g. 1/12 for "1M") finds a K that hits the target delta
    at that T -- but the pricer will actually price it at the ROUNDED T,
    which is a slightly different delta. For short/non-round tenors this
    mismatch was measured up to ~0.7% relative delta error (1M tenor).
    Rounding here first keeps the strike solve consistent with what the
    package's own pricers will actually do with this T."""
    return int(T * 365 + 0.5) / 365


def _strike_from_delta(S, T, r_d, r_f, sigma, target_delta, option_type):
    if option_type == "call":
        d1 = norm.ppf(target_delta * math.exp(r_f * T))
    else:
        d1 = -norm.ppf(-target_delta * math.exp(r_f * T))
    return S * math.exp((r_d - r_f + 0.5 * sigma ** 2) * T - d1 * sigma * math.sqrt(T))


class FXDeltaVolSurface:
    """A tenor-by-tenor 25-delta smile (ATM / risk reversal / butterfly),
    converted to strikes internally, with two-step interpolation (within
    a tenor's smile by strike, then across tenors).

    S: spot exchange rate
    domestic_rate, foreign_rate: flat rates used for the delta-to-strike
        conversion (matching fx/*.py's convention)
    tenors: list of times to expiry, in years
    atm_vols, rr_25, bf_25: lists (same length/order as tenors) of the
        at-the-money vol, 25-delta risk reversal, and 25-delta butterfly
        for each tenor, in decimal vol terms (e.g. 0.10 for 10%)
    rr_10, bf_10: optional lists (same length/order as tenors) of the
        10-delta risk reversal and butterfly -- see module docstring's
        "10-DELTA SUPPORT" section. Both must be given together, or
        both omitted.
    """

    def __init__(self, S, domestic_rate, foreign_rate, tenors, atm_vols, rr_25, bf_25,
                 rr_10=None, bf_10=None):
        lengths = {len(tenors), len(atm_vols), len(rr_25), len(bf_25)}
        if len(lengths) != 1:
            raise ValueError(
                f"tenors, atm_vols, rr_25, bf_25 must all be the same length "
                f"(got {len(tenors)}, {len(atm_vols)}, {len(rr_25)}, {len(bf_25)})"
            )

        has_10d = rr_10 is not None or bf_10 is not None
        if has_10d:
            if rr_10 is None or bf_10 is None:
                raise ValueError("rr_10 and bf_10 must be given together, or both omitted")
            if len(rr_10) != len(tenors) or len(bf_10) != len(tenors):
                raise ValueError(
                    f"rr_10, bf_10 must be the same length as tenors "
                    f"(got {len(rr_10)}, {len(bf_10)}, expected {len(tenors)})"
                )

        order = sorted(range(len(tenors)), key=lambda i: tenors[i])
        self._tenors = [tenors[i] for i in order]
        self._smiles = []  # one (sorted_strikes, aligned_vols) pair per tenor

        for i in order:
            T = _rounded_T(tenors[i])
            atm_vol, rr, bf = atm_vols[i], rr_25[i], bf_25[i]

            vol_25call = atm_vol + bf + 0.5 * rr
            vol_25put = atm_vol + bf - 0.5 * rr

            K_atm = S * math.exp((domestic_rate - foreign_rate) * T)
            K_25call = _strike_from_delta(S, T, domestic_rate, foreign_rate, vol_25call, 0.25, "call")
            K_25put = _strike_from_delta(S, T, domestic_rate, foreign_rate, vol_25put, -0.25, "put")

            strikes_raw = [K_25put, K_atm, K_25call]
            vols_raw = [vol_25put, atm_vol, vol_25call]

            if has_10d:
                rr10, bf10 = rr_10[i], bf_10[i]
                vol_10call = atm_vol + bf10 + 0.5 * rr10
                vol_10put = atm_vol + bf10 - 0.5 * rr10
                K_10call = _strike_from_delta(S, T, domestic_rate, foreign_rate, vol_10call, 0.10, "call")
                K_10put = _strike_from_delta(S, T, domestic_rate, foreign_rate, vol_10put, -0.10, "put")
                strikes_raw += [K_10put, K_10call]
                vols_raw += [vol_10put, vol_10call]

            points = sorted(zip(strikes_raw, vols_raw))
            strikes = [k for k, _ in points]
            vols = [v for _, v in points]
            self._smiles.append((strikes, vols))

    def get_vol(self, K, T):
        """Return the implied volatility at strike K, tenor T (years)."""
        t_lo, t_hi, t_frac = self._bracket(self._tenors, T)

        v_lo = self._interp_smile(self._smiles[t_lo], K)
        v_hi = self._interp_smile(self._smiles[t_hi], K)
        return v_lo + (v_hi - v_lo) * t_frac

    @staticmethod
    def _interp_smile(smile, K):
        strikes, vols = smile
        lo, hi, frac = FXDeltaVolSurface._bracket(strikes, K)
        return vols[lo] + (vols[hi] - vols[lo]) * frac

    @staticmethod
    def _bracket(sorted_values, x):
        """Same bracket-and-fraction logic as VolSurface._bracket -- see
        that class for the reasoning (flat extrapolation beyond edges)."""
        if len(sorted_values) == 1:
            return 0, 0, 0.0

        i = bisect.bisect_left(sorted_values, x)
        if i == 0:
            return 0, 0, 0.0
        if i >= len(sorted_values):
            last = len(sorted_values) - 1
            return last, last, 0.0

        lo, hi = i - 1, i
        span = sorted_values[hi] - sorted_values[lo]
        frac = 0.0 if span == 0 else (x - sorted_values[lo]) / span
        return lo, hi, frac
