"""Market-input resolution for engine/options pricers: spot, rates, vol.

Every input here is resolved with the same discipline as the rest of this
app -- missing stays missing, nothing is defaulted or fabricated. A trade
that cannot be priced comes back as a skip reason (see ``store.py``), never
a guessed number.

- **SPOT**: read from ``marks_official`` (never ``marks`` directly, per
  CLAUDE.md "Official marks" -- official source for SPOT is
  ``BBG_BFXFORWARD``), keyed by the PLAIN PAIR instrument_id (e.g.
  'EURSEK' -- the ordinary FX instrument row, base_ccy+quote_ccy). This is
  NOT the option's own instrument_id (which encodes strike/expiry/side,
  e.g. 'EURSEK092326C-197727826', and has no SPOT mark of its own).
  Missing -> ``get_spot`` returns ``None``.

- **Rates (Phase 7, 2026-09-17; manual fallback Phase 7.1, 2026-09-17;
  implied-forward fallback Phase 7.2, 2026-09-18)**:
  ``engine/options/rates.py::resolve_fx_rates`` -- REAL continuously-
  compounded zero rates to the option's own expiry, read off the
  bootstrapped OIS discount curve of each currency (``curve_quotes`` ->
  ``engine.rates.curves.build_curve_set``, same source-preference rule as
  ``engine/rates/store.py::_read_curve_quotes``). domestic = quote ccy,
  foreign = base ccy -- see rates.py's own docstring for why this is exact
  for Garman-Kohlhagen discounting. The vendored
  ``options_calc.fx.rate_curves`` illustrative placeholder is NO LONGER
  used anywhere in this input-resolution path -- deleted from here, not
  patched in the vendored copy (vendor/ is never edited). A currency with
  no OIS convention (``engine/rates/conventions.py::CCY_RFR`` -- seven
  currencies: USD/EUR/GBP/JPY/CHF/CAD/AUD, NOT the full 45-pair G10 set
  ``options_calc.fx.g10`` covers), such as SEK/NOK/TWD/ZAR, or a supported
  currency simply missing ``curve_quotes`` rows on ``as_of``, falls back in
  order to: a manual rate (``rates.py::manual_rates``, ``set_manual_rate``
  -- exact expiry first, then a ccy-wide flat ``'*'`` entry), then covered
  interest parity implied from the pair's own official SPOT + FWD_OUTRIGHT
  marks and the OTHER currency's already-resolved rate (never invoked when
  BOTH currencies are unresolved, and never overriding a curve or manual
  rate that does exist) -- before giving up with reason
  ``"no curve/rate <CCY>"``. Never a fabricated rate: a currency with no
  curve, no manual entry, and no forward to imply from still skips the
  trade, with that same unchanged reason string. Each resolved rate's
  provenance (``rates.py::RateInput.source_kind`` in ``{OIS_CURVE,
  MANUAL_EXPIRY, MANUAL_FLAT, IMPLIED_FORWARD}``) is carried onto
  ``MarketInputs.domestic_rate_source`` / ``.foreign_rate_source`` the same
  way ``VolInput`` is carried onto ``MarketInputs.vol_source`` below -- see
  ``rates.py``'s own docstring for the implied-forward formula and reject
  conditions.

- **Vol**: resolved by ``resolve_vol`` with a documented priority, never
  blending sources for one trade (Phase 5b, 2026-09-17):

  (a) **SMILE** -- ``options_calc.fx.delta_vol_surface.FXDeltaVolSurface``
      built from this pair's ``vol_quotes`` smile
      (``data/bloomberg/vol_marketdata.py::vol_smile``), evaluated at the
      trade's own strike and expiry. Quotes are stored in vol POINTS by
      that module (its own "store raw, scale downstream" rule) and
      converted to decimals (``/100``) here before being fed to the
      surface. Only tried when the option has a genuine positive strike
      and the expiry falls INSIDE the smile's quoted tenor range -- this
      resolver deliberately does not rely on ``FXDeltaVolSurface``'s own
      flat extrapolation for an out-of-range expiry (see ``resolve_vol``).
  (b) **ATM_INTERP** -- ``vol_marketdata.py::atm_vol_for_expiry``
      (variance-time interpolation of the ATM term structure alone), used
      when (a) is unavailable: no ``vol_quotes`` staged for the pair/date,
      no tenor carries an ATM quote, or the expiry is outside the tenor
      range. Also ``/100``-scaled.
  (c) **MANUAL** -- this package's own flat ``option_vols`` table (Phase 2
      original path) -- now the LAST resort, not the first. Exact-expiry
      match first, then the pair's ``'*'`` flat fallback.
  (d) else ``None`` -- the trade is skipped with reason "no vol", same as
      before.

  Every resolved vol carries its provenance as a ``VolInput`` (``vol``,
  ``source_kind`` in ``{SMILE, ATM_INTERP, MANUAL}``, ``detail``) so
  ``store.py`` can record which source fed a given mark.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

# ------------------------------------------------------------------- vol store

_OPTION_VOLS_DDL = """
CREATE TABLE IF NOT EXISTS option_vols (
  as_of_date      TEXT NOT NULL,
  pair            TEXT NOT NULL,
  tenor_or_expiry TEXT NOT NULL,   -- an ISO expiry date, or '*' for a flat pair-wide fallback
  vol             REAL NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, pair, tenor_or_expiry, source)
);
"""

FLAT_TENOR = "*"


def ensure_option_vols_table(conn: sqlite3.Connection) -> None:
    """Create ``option_vols`` if absent -- same defensive pattern as
    ``data/bloomberg/rates_marketdata.py::ensure_curve_quotes_table``."""
    conn.execute(_OPTION_VOLS_DDL)


def set_manual_vol(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    tenor_or_expiry: str,
    vol: float,
    source: str = "MANUAL",
) -> None:
    """Write one manual vol quote. `tenor_or_expiry` is either the option's
    exact ISO expiry date (an expiry-specific quote) or FLAT_TENOR ('*') for
    a pair-wide fallback used when no expiry-specific quote exists."""
    ensure_option_vols_table(conn)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO option_vols (as_of_date, pair, tenor_or_expiry, vol, source) "
            "VALUES (?,?,?,?,?)",
            (as_of, pair, tenor_or_expiry, float(vol), source),
        )


def get_manual_vol(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    expiry_iso: str,
    source: str = "MANUAL",
) -> Optional[float]:
    """Exact (as_of, pair, expiry) match first; else the pair's flat '*'
    entry for the same (as_of, source); else None."""
    ensure_option_vols_table(conn)
    row = conn.execute(
        "SELECT vol FROM option_vols WHERE as_of_date = ? AND pair = ? AND tenor_or_expiry = ? AND source = ?",
        (as_of, pair, expiry_iso, source),
    ).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute(
        "SELECT vol FROM option_vols WHERE as_of_date = ? AND pair = ? AND tenor_or_expiry = ? AND source = ?",
        (as_of, pair, FLAT_TENOR, source),
    ).fetchone()
    return row[0] if row is not None else None


# ------------------------------------------------------------------- spot

def get_spot(conn: sqlite3.Connection, as_of: str, pair: str) -> Optional[float]:
    """SPOT mark for `pair` (the plain 6-char FX instrument_id) from
    marks_official as of `as_of`. None if absent -- never defaulted."""
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, pair),
    ).fetchone()
    return row[0] if row is not None else None


# ------------------------------------------------------------------- vol resolution (smile / ATM-interp / manual)

SMILE = "SMILE"
ATM_INTERP = "ATM_INTERP"
MANUAL = "MANUAL"


@dataclass
class VolInput:
    """One resolved vol plus WHERE it came from -- see module docstring's
    "Vol" section for the full priority. Never blended: one trade gets
    exactly one vol source."""
    vol: float
    source_kind: str  # SMILE | ATM_INTERP | MANUAL
    detail: str = ""
    # SMILE only: the smile's vol at any strike for this option's expiry. A digital is a
    # tight call / put spread, each leg at its own vol, so it needs the smile around its
    # strike and not only the vol at it (pricer.price_fx_digital_on_smile). None when the
    # vol is an ATM interpolation or a manual number: no slope is known there.
    vol_at: Optional[Callable[[float], float]] = None


def _build_fx_delta_vol_surface(conn, as_of, pair, spot, domestic_rate, foreign_rate):
    """Build an FXDeltaVolSurface from this pair's staged vol_quotes smile.

    Returns ``(surface, (min_days, max_days))`` on success, or
    ``(None, None)`` if no usable smile is staged: no vol_quotes rows for
    this (as_of, pair), or no tenor carries an ATM quote (ATM anchors both
    the RR/BF decomposition and the tenor's own K_ATM strike, so a tenor
    without it cannot be used).

    Partial-smile robustness (task requirement):
    - a tenor with ATM but no RR25/BF25 is kept as ATM-ONLY / FLAT for that
      tenor (rr=bf=0), not dropped from the surface;
    - 10-delta (RR10/BF10) is only passed to FXDeltaVolSurface if EVERY
      included tenor has both -- the vendored constructor requires rr_10/
      bf_10 to be full-length (one entry per tenor) or omitted entirely
      (see delta_vol_surface.py), so a partial 10d smile falls back to the
      25d-only constructor path for the WHOLE surface rather than raising.
    """
    from data.bloomberg.vol_marketdata import vol_smile, tenor_to_days
    from .vendor.options_calc.fx.delta_vol_surface import FXDeltaVolSurface

    smile = vol_smile(conn, as_of, pair)
    if not smile:
        return None, None

    entries = []
    for tenor, quotes in smile.items():
        if "ATM" not in quotes:
            continue
        try:
            days = tenor_to_days(tenor)
        except ValueError:
            continue
        entries.append((days, quotes))
    if not entries:
        return None, None
    entries.sort(key=lambda e: e[0])

    tenors_years, atm_vols, rr_25, bf_25 = [], [], [], []
    rr_10, bf_10 = [], []
    has_full_10d = True
    for days, quotes in entries:
        atm_vols.append(quotes["ATM"] / 100.0)
        tenors_years.append(days / 365.0)

        rr, bf = quotes.get("RR25"), quotes.get("BF25")
        if rr is None or bf is None:
            rr_25.append(0.0)  # ATM-only tenor -- flat smile (documented above)
            bf_25.append(0.0)
        else:
            rr_25.append(rr / 100.0)
            bf_25.append(bf / 100.0)

        r10, b10 = quotes.get("RR10"), quotes.get("BF10")
        if r10 is None or b10 is None:
            has_full_10d = False
            rr_10.append(0.0)
            bf_10.append(0.0)
        else:
            rr_10.append(r10 / 100.0)
            bf_10.append(b10 / 100.0)

    kwargs = dict(rr_10=rr_10, bf_10=bf_10) if has_full_10d else {}
    try:
        surface = FXDeltaVolSurface(
            spot, domestic_rate, foreign_rate, tenors_years, atm_vols, rr_25, bf_25, **kwargs
        )
    except ValueError:
        return None, None

    return surface, (entries[0][0], entries[-1][0])


def _cached_surface(conn, as_of, pair, spot, domestic_rate, foreign_rate, surface_cache):
    """Build-once-per-pair memoisation within a single price_and_store /
    price_all_and_store run (``surface_cache`` is a plain dict the caller
    owns for that run's lifetime; ``None`` disables caching). The
    expensive part -- FXDeltaVolSurface's per-tenor delta-to-strike solve
    -- is what this avoids repeating for every trade on the same pair."""
    if surface_cache is None:
        return _build_fx_delta_vol_surface(conn, as_of, pair, spot, domestic_rate, foreign_rate)
    if pair not in surface_cache:
        surface_cache[pair] = _build_fx_delta_vol_surface(conn, as_of, pair, spot, domestic_rate, foreign_rate)
    return surface_cache[pair]


def resolve_vol(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    expiry_iso: str,
    strike: Optional[float],
    spot: float,
    domestic_rate: float,
    foreign_rate: float,
    surface_cache: Optional[dict] = None,
) -> Optional[VolInput]:
    """Resolve one option's vol, priority (a) SMILE, (b) ATM_INTERP,
    (c) MANUAL, (d) None -- see module docstring's "Vol" section for the
    full rationale. `strike` <= 0 or None (barrier/touch payoffs with no
    meaningful strike) skips (a) straight to (b)."""
    from data.bloomberg.vol_marketdata import atm_vol_for_expiry

    if strike is not None and strike > 0:
        surface, day_range = _cached_surface(conn, as_of, pair, spot, domestic_rate, foreign_rate, surface_cache)
        if surface is not None:
            min_days, max_days = day_range
            target_days = (datetime.date.fromisoformat(expiry_iso) - datetime.date.fromisoformat(as_of)).days
            if min_days <= target_days <= max_days:
                T = target_days / 365.0
                vol = surface.get_vol(strike, T)
                return VolInput(
                    vol=vol, source_kind=SMILE,
                    detail=f"{pair} delta smile @ K={strike}, expiry={expiry_iso}",
                    vol_at=lambda k, _surface=surface, _T=T: _surface.get_vol(k, _T),
                )

    atm = atm_vol_for_expiry(conn, as_of, pair, expiry_iso)
    if atm is not None:
        return VolInput(
            vol=atm / 100.0, source_kind=ATM_INTERP,
            detail=f"{pair} ATM term-structure interpolation @ expiry={expiry_iso}",
        )

    manual = get_manual_vol(conn, as_of, pair, expiry_iso)
    if manual is not None:
        return VolInput(vol=manual, source_kind=MANUAL, detail=f"{pair} manual option_vols table")

    return None


# ------------------------------------------------------------------- combined resolution

@dataclass
class MarketInputs:
    spot: float
    domestic_rate: float
    foreign_rate: float
    vol: float
    vol_source: Optional[VolInput] = None
    # Rate provenance (Phase 7.1, 2026-09-17) -- see rates.py::RateInput /
    # module docstring's "Rates" section. None only when resolve_fx_rates
    # was bypassed (no current caller does this).
    domestic_rate_source: "Optional[RateInput]" = None
    foreign_rate_source: "Optional[RateInput]" = None


@dataclass
class InputsResult:
    inputs: Optional[MarketInputs]
    reason: str = ""  # '' when inputs is not None


def resolve_market_inputs(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    expiry_iso: str,
    strike: Optional[float] = None,
    surface_cache: Optional[dict] = None,
    curve_cache: Optional[dict] = None,
) -> InputsResult:
    """Resolve spot/rates/vol for one (as_of, pair, expiry[, strike]).
    Returns InputsResult(None, reason) on the first missing input --
    callers (store.py) turn that straight into a skipped PricingOutcome.
    `strike` and `surface_cache` feed the vol resolver's SMILE path (see
    `resolve_vol`); `curve_cache` feeds the rates resolver's per-(as_of,
    ccy) OIS curve cache (see engine/options/rates.py) -- all optional so
    existing callers keep working unchanged."""
    spot = get_spot(conn, as_of, pair)
    if spot is None:
        return InputsResult(None, "no SPOT mark")

    from .rates import resolve_fx_rates

    fx_rates, reason = resolve_fx_rates(conn, as_of, pair, expiry_iso, curve_cache)
    if fx_rates is None:
        return InputsResult(None, reason)
    domestic_rate, foreign_rate = fx_rates.domestic_rate, fx_rates.foreign_rate

    vol_input = resolve_vol(conn, as_of, pair, expiry_iso, strike, spot, domestic_rate, foreign_rate, surface_cache)
    if vol_input is None:
        return InputsResult(None, "no vol")

    return InputsResult(MarketInputs(
        spot=spot, domestic_rate=domestic_rate, foreign_rate=foreign_rate,
        vol=vol_input.vol, vol_source=vol_input,
        domestic_rate_source=fx_rates.domestic_rate_source,
        foreign_rate_source=fx_rates.foreign_rate_source,
    ))
