"""Market-input resolution for engine/rates_vol: flat discount/forecast rates derived
from the bootstrapped OIS ``CurveSet``, and manual vol / model-parameter lookup.

See ``engine/rates_vol/__init__.py``'s "Flat-curve approximation" section for what
``derive_curve_inputs`` does and does not model, and its "Numerical quirk" section for
why every read off a cached ``CurveSet`` here resets
``ql.Settings.instance().evaluationDate`` first (the vendored ``options_calc.rates``
pricers force it to the real system date on every call, silently poisoning any curve
object built with a different evaluation date -- QuantLib's evaluation date is a single
global singleton, not scoped to one curve).

Vol resolution priority (mirrors ``engine/options/inputs.py``'s SMILE -> ATM_INTERP ->
MANUAL precedence, adapted for this asset class): for a swaption, (a) an exact
manually-staged flat vol for this option's own strike, (b) the pair's ATM flat vol, (c)
a SABR smile built from staged SABR parameters, evaluated at this option's own strike and
the curve-implied forward, (d) else a skip with reason "no vol". A cap/floor has no SABR
path (the vendored library's ``sabr.py`` only covers swaptions), so it is (a)/(b)/(d)
only. A NORMAL-quoted flat vol is a hard skip at step (a)/(b), never silently used or
converted -- see module docstring "NORMAL (basis-point) vol is REJECTED" in
``engine/rates_vol/__init__.py``.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- rate_vols

_RATE_VOLS_DDL = """
CREATE TABLE IF NOT EXISTS rate_vols (
  as_of_date            TEXT NOT NULL,
  ccy                   TEXT NOT NULL,
  "index"               TEXT NOT NULL,
  expiry_tenor_or_date  TEXT NOT NULL,   -- this option's own ISO expiry date (no cube/
                                        -- interpolation in this phase, so the key is the
                                        -- exact expiry, not a generic tenor bucket)
  underlying_tenor      TEXT NOT NULL,   -- e.g. '10Y' for a swaption; '' for a cap/floor
                                        -- (a strip has no single underlying tenor)
  strike_or_ATM         TEXT NOT NULL,   -- "%.6f" of the option's own strike, or literal
                                        -- 'ATM' for a pair-wide flat fallback
  vol                   REAL NOT NULL,
  vol_type              TEXT NOT NULL,   -- LOGNORMAL | NORMAL (NORMAL is staged but never
                                        -- used -- see module docstring)
  source                TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, "index", expiry_tenor_or_date, underlying_tenor,
               strike_or_ATM, source)
);
"""

ATM_KEY = "ATM"


def ensure_rate_vols_table(conn: sqlite3.Connection) -> None:
    conn.execute(_RATE_VOLS_DDL)


def strike_key(strike: float) -> str:
    """Canonical string key for a numeric strike -- stable across writer/reader float
    formatting, so an exact-strike lookup does not silently miss on a repr difference."""
    return format(float(strike), ".6f")


def set_manual_rate_vol(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    expiry_iso: str,
    underlying_tenor: str,
    strike_or_atm,
    vol: float,
    vol_type: str = "LOGNORMAL",
    source: str = "MANUAL",
) -> None:
    """Write one flat vol quote. `strike_or_atm` is either a numeric strike (converted via
    `strike_key`) or the literal string 'ATM'. `underlying_tenor` is '' for a cap/floor."""
    if vol_type not in ("LOGNORMAL", "NORMAL"):
        raise ValueError(f"vol_type must be 'LOGNORMAL' or 'NORMAL', got {vol_type!r}")
    key = ATM_KEY if strike_or_atm == ATM_KEY else strike_key(strike_or_atm)
    ensure_rate_vols_table(conn)
    with conn:
        conn.execute(
            'INSERT OR REPLACE INTO rate_vols (as_of_date, ccy, "index", expiry_tenor_or_date, '
            'underlying_tenor, strike_or_ATM, vol, vol_type, source) VALUES (?,?,?,?,?,?,?,?,?)',
            (as_of, ccy, index, expiry_iso, underlying_tenor, key, float(vol), vol_type, source),
        )


@dataclass
class RateVolLookup:
    vol: float
    vol_type: str
    source: str
    key_used: str  # the strike_or_ATM key that actually matched


def get_rate_vol(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    expiry_iso: str,
    underlying_tenor: str,
    strike: Optional[float],
) -> Optional[RateVolLookup]:
    """Exact-strike match first, then the 'ATM' fallback, for the same
    (as_of, ccy, index, expiry, underlying_tenor). LOGNORMAL is preferred over NORMAL
    when both are staged for the same key (so a skip only happens when LOGNORMAL is
    genuinely absent). None if nothing at all is staged for either key."""
    ensure_rate_vols_table(conn)
    keys = []
    if strike is not None:
        keys.append(strike_key(strike))
    keys.append(ATM_KEY)
    for key in keys:
        rows = conn.execute(
            'SELECT vol, vol_type, source FROM rate_vols WHERE as_of_date=? AND ccy=? AND "index"=? '
            'AND expiry_tenor_or_date=? AND underlying_tenor=? AND strike_or_ATM=? '
            "ORDER BY CASE vol_type WHEN 'LOGNORMAL' THEN 0 ELSE 1 END",
            (as_of, ccy, index, expiry_iso, underlying_tenor, key),
        ).fetchall()
        if rows:
            vol, vol_type, source = rows[0]
            return RateVolLookup(vol=vol, vol_type=vol_type, source=source, key_used=key)
    return None


# --------------------------------------------------------------------------- rate_model_params

_RATE_MODEL_PARAMS_DDL = """
CREATE TABLE IF NOT EXISTS rate_model_params (
  as_of_date  TEXT NOT NULL,
  ccy         TEXT NOT NULL,
  "index"     TEXT NOT NULL,
  model       TEXT NOT NULL,   -- 'SABR' | 'HULL_WHITE'
  param       TEXT NOT NULL,   -- SABR: alpha|beta|rho|nu. HULL_WHITE: a|sigma.
  value       REAL NOT NULL,
  source      TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, "index", model, param, source)
);
"""


def ensure_rate_model_params_table(conn: sqlite3.Connection) -> None:
    conn.execute(_RATE_MODEL_PARAMS_DDL)


def set_rate_model_param(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    model: str,
    param: str,
    value: float,
    source: str = "MANUAL",
) -> None:
    if model not in ("SABR", "HULL_WHITE"):
        raise ValueError(f"model must be 'SABR' or 'HULL_WHITE', got {model!r}")
    ensure_rate_model_params_table(conn)
    with conn:
        conn.execute(
            'INSERT OR REPLACE INTO rate_model_params (as_of_date, ccy, "index", model, param, value, source) '
            "VALUES (?,?,?,?,?,?,?)",
            (as_of, ccy, index, model, param, float(value), source),
        )


def get_rate_model_params(conn: sqlite3.Connection, as_of: str, ccy: str, index: str, model: str) -> Dict[str, float]:
    """{param: value} for (as_of, ccy, index, model); {} if nothing is staged. Callers
    check the required key set themselves (SABR needs alpha/beta/rho/nu all present;
    HULL_WHITE needs a/sigma) -- a partial set is treated as "missing", never padded with
    a default, by the caller (store.py), not by this function."""
    ensure_rate_model_params_table(conn)
    rows = conn.execute(
        'SELECT param, value FROM rate_model_params WHERE as_of_date=? AND ccy=? AND "index"=? AND model=?',
        (as_of, ccy, index, model),
    ).fetchall()
    return {param: value for param, value in rows}


# --------------------------------------------------------------------------- curve inputs

def _read_curve_quotes(conn: sqlite3.Connection, as_of: str, ccy: str, index: str) -> Tuple[List[Tuple[str, float]], str]:
    """Same source-preference style as ``engine/rates/store.py::_read_curve_quotes``
    (BBG_BDP preferred, else alphabetically first, never blended) -- reimplemented locally
    rather than importing that module's underscore-prefixed function, per this package's
    "reuse the public shape, not the private helper" approach. Returns (quotes, source);
    ([], '') if nothing is staged."""
    rows = conn.execute(
        'SELECT tenor, value, source FROM curve_quotes WHERE as_of_date = ? AND ccy = ? AND "index" = ? '
        "AND quote_type = ?",
        (as_of, ccy, index, "OIS"),
    ).fetchall()
    if not rows:
        return [], ""
    sources = sorted({r[2] for r in rows})
    source = "BBG_BDP" if "BBG_BDP" in sources else sources[0]
    picked = [(tenor, value) for tenor, value, src in rows if src == source]
    return picked, source


def _set_eval_date(as_of_date: datetime.date) -> None:
    """Reset the global QuantLib evaluation date to `as_of_date` -- MUST be called
    immediately before reading anything off a cached CurveSet (zeroRate, rebuilding the
    forward swap for fairRate). See module docstring / __init__.py "Numerical quirk":
    the vendored options_calc.rates engine forces this global to the real system date on
    every pricer call, so a curve read that trusts the evaluation date left over from
    `build_curve_set` can silently be wrong once any vendored pricer has run in between."""
    import QuantLib as ql

    from engine.rates import qlmap

    ql.Settings.instance().evaluationDate = qlmap.ql_date(as_of_date)


def year_fraction(from_date: datetime.date, to_date: datetime.date) -> float:
    """Act/365 year fraction, matching this package's and the vendored engine's day-count
    convention (Actual365Fixed everywhere -- see vendor rates/_engine.py docstring)."""
    return (to_date - from_date).days / 365.0


@dataclass
class CurveInputs:
    ccy: str
    index: str
    as_of: str
    curve_set: object  # engine.rates.curves.CurveSet -- kept live for cache reuse across trades
    discount_rate: float
    forecast_rate: float
    quote_source: str


def _zero_rate(curve_set, as_of_date: datetime.date, target_date: datetime.date) -> float:
    import QuantLib as ql

    from engine.rates import qlmap

    _set_eval_date(as_of_date)
    target_ql = qlmap.ql_date(target_date)
    return curve_set.discount_curve.zeroRate(target_ql, ql.Actual365Fixed(), ql.Continuous).rate()


def _forward_par_rate(curve_set, as_of_date: datetime.date, start_date: datetime.date, end_date: datetime.date) -> float:
    from engine.rates.instruments import build_instrument

    _set_eval_date(as_of_date)
    # fixed_rate/notional/pay_fixed are irrelevant to fairRate() (the floating leg alone
    # determines it) -- see engine/rates/valuation.py::price_swap's par-rate use of the
    # identical ql_swap.fairRate() call.
    built = build_instrument(curve_set, start_date, end_date, fixed_rate=0.0, notional=1.0, pay_fixed=True)
    return built.ql_swap.fairRate()


def derive_curve_inputs(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    expiry_date: datetime.date,
    underlying_start: datetime.date,
    underlying_end: datetime.date,
    curve_set=None,
) -> Optional[CurveInputs]:
    """Bootstrap (or reuse a passed-in) OIS CurveSet for (ccy, index) and derive the flat
    discount_rate (zero rate to `expiry_date`) / forecast_rate (forward par swap rate on
    [underlying_start, underlying_end]) this package's pricers need -- see __init__.py's
    "Flat-curve approximation" section. Returns None if no curve_quotes are staged for
    (as_of, ccy, index) (a structured skip upstream, never a fabricated curve)."""
    from engine.rates.curves import build_curve_set

    quote_source = ""
    if curve_set is None:
        quotes, quote_source = _read_curve_quotes(conn, as_of, ccy, index)
        if not quotes:
            return None
        curve_set = build_curve_set(quotes, datetime.date.fromisoformat(as_of), ccy, index)

    discount_rate = _zero_rate(curve_set, datetime.date.fromisoformat(as_of), expiry_date)
    forecast_rate = _forward_par_rate(curve_set, datetime.date.fromisoformat(as_of), underlying_start, underlying_end)
    return CurveInputs(
        ccy=ccy, index=index, as_of=as_of, curve_set=curve_set,
        discount_rate=discount_rate, forecast_rate=forecast_rate, quote_source=quote_source,
    )


# --------------------------------------------------------------------------- vol resolution

@dataclass
class VolResolution:
    vol: Optional[float]
    source_kind: str = ""  # FLAT | SABR
    detail: str = ""
    reason: str = ""       # non-'' only when vol is None


def _flat_lookup(conn, as_of, ccy, index, expiry_iso, underlying_tenor, strike) -> VolResolution:
    lookup = get_rate_vol(conn, as_of, ccy, index, expiry_iso, underlying_tenor, strike)
    if lookup is None:
        return VolResolution(None)
    if lookup.vol_type != "LOGNORMAL":
        return VolResolution(
            None,
            reason=(
                f"vol for ({ccy}, {index}, expiry={expiry_iso}, tenor={underlying_tenor}, "
                f"key={lookup.key_used}) is {lookup.vol_type}-quoted (normal/bp); the vendored "
                f"engine is lognormal Black-76 only -- refusing to silently convert"
            ),
        )
    return VolResolution(
        lookup.vol, source_kind="FLAT",
        detail=f"rate_vols[{lookup.key_used}] source={lookup.source}",
    )


def resolve_swaption_vol(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    expiry_iso: str,
    underlying_tenor: str,
    strike: float,
    expiry_years: float,
    forward_rate: float,
) -> VolResolution:
    """(a) exact-strike flat vol, (b) ATM flat vol, (c) SABR (needs alpha/beta/rho/nu
    staged in rate_model_params), (d) else a "no vol" skip. A NORMAL-quoted flat vol found
    at (a)/(b) is an immediate, explicit skip (see _flat_lookup) -- it does NOT fall
    through to SABR, since a staged-but-unusable flat vol is a data problem to fix, not a
    reason to silently prefer a different source."""
    flat = _flat_lookup(conn, as_of, ccy, index, expiry_iso, underlying_tenor, strike)
    if flat.vol is not None or flat.reason:
        return flat

    from engine.options.vendor.options_calc.rates.sabr import sabr_swaption_vol

    params = get_rate_model_params(conn, as_of, ccy, index, "SABR")
    required = {"alpha", "beta", "rho", "nu"}
    if required.issubset(params):
        try:
            vol = sabr_swaption_vol(strike, forward_rate, expiry_years, params["alpha"], params["beta"],
                                     params["rho"], params["nu"])
        except ValueError as exc:
            return VolResolution(None, reason=f"SABR vol solve failed: {exc}")
        return VolResolution(
            vol, source_kind="SABR",
            detail=f"SABR(alpha={params['alpha']}, beta={params['beta']}, rho={params['rho']}, nu={params['nu']}) "
                   f"@ strike={strike}, forward={forward_rate:.6f}",
        )
    return VolResolution(None, reason="no vol (no flat rate_vols entry, no complete SABR params)")


def resolve_capfloor_vol(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    index: str,
    expiry_iso: str,
    strike: float,
) -> VolResolution:
    """Flat vol only -- the vendored library has no cap/floor SABR equivalent (sabr.py
    only covers swaptions; see MODELS.md)."""
    flat = _flat_lookup(conn, as_of, ccy, index, expiry_iso, "", strike)
    if flat.vol is not None or flat.reason:
        return flat
    return VolResolution(None, reason="no vol (no flat rate_vols entry for this cap/floor)")
