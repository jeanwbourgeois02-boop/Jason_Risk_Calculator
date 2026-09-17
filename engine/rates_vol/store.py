"""Glue between engine/rates_vol's pricers and this app's SQLite schema: this package's
OWN ``instrument_rate_options`` table (created defensively, NOT in
``data/ingest/schema.py`` -- see ``engine/rates_vol/__init__.py``), reads
``trades_official`` / ``instruments`` / ``instrument_rate_options``, writes ``PV_USD`` /
``DV01_USD`` (source ``QL_PRICER``) and ``VEGA`` / ``GAMMA`` / ``THETA`` (source
``QL_OPTIONS_PRICER``) marks. Mirrors the shape of ``engine/rates/store.py`` (IRS) and
``engine/options/store.py`` (FX options).

Dispatch is entirely by ``instrument_rate_options.payoff``:
  - ``SWAPTION``          -> ``pricer.price_swaption`` (vol resolved flat-first, SABR
                              fallback -- see ``inputs.py::resolve_swaption_vol``), priced
                              off the bootstrapped OIS curve directly (see ``inputs.py``'s
                              "Curve inputs" section -- no more flat-rate derivation).
  - ``BERMUDAN_SWAPTION``  -> ``pricer.price_bermudan_swaption`` (needs Hull-White
                              ``a``/``sigma`` staged in ``rate_model_params`` -- MANUAL
                              preferred over CALIBRATED, see ``inputs.py::get_rate_model_
                              params`` -- AND a non-empty ``exercise_dates`` staged on the
                              trade's own ``instrument_rate_options`` row, passed straight
                              through to the vendored engine; empty -> skip "no exercise
                              dates", NEVER mechanically generated -- see
                              ``_parse_exercise_dates``).
  - ``CAP`` / ``FLOOR``    -> ``pricer.price_cap_floor`` (flat vol only).

A trade that cannot be priced (no ``instrument_rate_options`` row, unsupported product/
payoff, zero quantity, expiry not in the future, no curve_quotes, no vol, no exercise
dates (Bermudan only), no Hull-White params, an out-of-range Hull-White sigma, or no
SPOT to convert a non-USD notional) writes NO marks and comes back as a
``PricingOutcome`` with ``priced=False`` and a ``reason`` -- never a fabricated number,
matching every other pricer/store module in this app.

Non-USD notional: converted via ``engine.pnl.valuation.usd_per_quote`` -- the SAME
function ``engine/rates/store.py`` uses for IRS (as of its 2026-09-17 fix), which raises
... actually returns NaN / skips, never silently substitutes 1.0. Note this closes the
gap ``engine/rates/__init__.py``'s OLDER docstring still describes as an open "known
gap" -- that docstring appears to predate the 2026-09-17 fix landing in
``engine/rates/store.py`` itself (which already calls ``usd_per_quote`` and raises if it
is missing); this module's behaviour is NOT materially stricter than current
``engine/rates/store.py``, just consistent with it. Flagged in the final report as a
housekeeper doc-freshness follow-up, not asserted here as a unique improvement.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from engine.rates.store import snapped_at

from . import pricer
from .inputs import derive_curve_inputs, resolve_capfloor_vol, resolve_swaption_vol, year_fraction
from .inputs import get_rate_model_params

# --------------------------------------------------------------------------- instrument_rate_options

_INSTRUMENT_RATE_OPTIONS_DDL = """
CREATE TABLE IF NOT EXISTS instrument_rate_options (
  instrument_id     TEXT PRIMARY KEY REFERENCES instruments,
  payoff            TEXT NOT NULL,             -- SWAPTION | BERMUDAN_SWAPTION | CAP | FLOOR
  option_type       TEXT NOT NULL DEFAULT '',  -- PAYER | RECEIVER | '' (CAP/FLOOR carry '')
  strike            REAL NOT NULL,             -- fixed rate / cap-floor strike, decimal
  "index"           TEXT NOT NULL,             -- 'SOFR', 'ESTR', ... (must match curve_quotes."index")
  underlying_start  TEXT NOT NULL,             -- ISO date
  underlying_end    TEXT NOT NULL,             -- ISO date (cap/floor: last accrual period end)
  exercise_dates    TEXT NOT NULL DEFAULT '',  -- ';'-joined ISO dates, BERMUDAN_SWAPTION only; '' otherwise
  fixed_freq        TEXT NOT NULL DEFAULT '',  -- e.g. '1Y' -- informational only, see module docstring
  float_freq        TEXT NOT NULL DEFAULT ''   -- e.g. '3M','6M' -- drives CAP/FLOOR freq_months; blank
                                               -- defaults to the vendored pricer's own 6M default, NOT a
                                               -- fabricated market datum (see _freq_to_months)
);
"""
# fixed_freq/float_freq are stored for completeness and future-proofing (a real deal's
# actual payment frequencies) but only float_freq is currently consumed: the vendored
# swaption/Bermudan engine's underlying swap construction is fixed internally (annual
# fixed leg, semiannual-index float leg -- see options_calc/rates/_engine.py's
# build_forward_swap) regardless of what is stored here. Only CAP/FLOOR's freq_months
# parameter (derived from float_freq) actually changes the vendored pricer's behaviour.


def ensure_instrument_rate_options_table(conn: sqlite3.Connection) -> None:
    conn.execute(_INSTRUMENT_RATE_OPTIONS_DDL)


def write_instrument_rate_option(
    conn: sqlite3.Connection,
    instrument_id: str,
    payoff: str,
    strike: float,
    index: str,
    underlying_start: str,
    underlying_end: str,
    option_type: str = "",
    exercise_dates: str = "",
    fixed_freq: str = "",
    float_freq: str = "",
) -> None:
    """One entry point for tests and a future data-ingest parser (task requirement).
    `payoff` in {'SWAPTION','BERMUDAN_SWAPTION','CAP','FLOOR'}; `option_type` in
    {'PAYER','RECEIVER',''} ('' only valid for CAP/FLOOR, which have no payer/receiver
    concept). No value here is defaulted from market data -- every argument must be
    supplied by the caller."""
    if payoff not in ("SWAPTION", "BERMUDAN_SWAPTION", "CAP", "FLOOR"):
        raise ValueError(f"payoff must be one of SWAPTION/BERMUDAN_SWAPTION/CAP/FLOOR, got {payoff!r}")
    if payoff in ("SWAPTION", "BERMUDAN_SWAPTION") and option_type not in ("PAYER", "RECEIVER"):
        raise ValueError(f"{payoff} requires option_type 'PAYER' or 'RECEIVER', got {option_type!r}")
    if payoff in ("CAP", "FLOOR") and option_type != "":
        raise ValueError(f"{payoff} carries no option_type (PAYER/RECEIVER apply to swaptions only), got {option_type!r}")
    ensure_instrument_rate_options_table(conn)
    with conn:
        conn.execute(
            'INSERT OR REPLACE INTO instrument_rate_options (instrument_id, payoff, option_type, strike, '
            '"index", underlying_start, underlying_end, exercise_dates, fixed_freq, float_freq) '
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (instrument_id, payoff, option_type, float(strike), index, underlying_start, underlying_end,
             exercise_dates, fixed_freq, float_freq),
        )


# --------------------------------------------------------------------------- helpers

_FREQ_DEFAULT_MONTHS = 6


def _freq_to_months(freq: str) -> int:
    """'3M' -> 3, '6M' -> 6, '1Y' -> 12; '' -> the vendored pricer's own 6-month default
    (options_calc.rates.cap_floor._DEFAULT_FREQ_MONTHS) -- a structural convention
    default, not a fabricated market rate/tenor."""
    if not freq:
        return _FREQ_DEFAULT_MONTHS
    freq = freq.strip().upper()
    if freq.endswith("M") and freq[:-1].isdigit():
        return int(freq[:-1])
    if freq.endswith("Y") and freq[:-1].isdigit():
        return int(freq[:-1]) * 12
    raise ValueError(f"Cannot parse frequency {freq!r} (expected e.g. '3M', '6M', '1Y')")


def _parse_exercise_dates(exercise_dates: str) -> List[datetime.date]:
    """';'-joined ISO dates (``instrument_rate_options.exercise_dates``) -> a sorted list
    of ``datetime.date``, passed straight through to the vendored engine's
    ``exercise_dates=`` parameter (see ``pricer.py::price_bermudan_swaption``). Replaces
    the pre-2026-09-17 ``_infer_exercise_frequency_years`` average-spacing approximation
    -- a Bermudan trade's OWN staged dates are used exactly, never mechanically
    regenerated. Empty string -> empty list (the caller, ``_price_row``, skips with
    reason "no exercise dates" before this is ever called on an empty string -- see
    that function)."""
    if not exercise_dates:
        return []
    return sorted(datetime.date.fromisoformat(d) for d in exercise_dates.split(";") if d)


def _usd_per_ccy(conn: sqlite3.Connection, ccy: str, as_of: str):
    """(fx, reason). fx is None (with a reason) if `ccy` != USD and no SPOT mark is on
    file -- mirrors engine/rates/store.py::_usd_per_ccy's raise, but returns a skip
    reason instead of raising (this module never raises for a per-trade pricing
    failure -- see PricingOutcome)."""
    if ccy == "USD":
        return 1.0, ""
    from engine.pnl.valuation import usd_per_quote

    s, _pair, _src = usd_per_quote(conn, ccy, as_of)
    if s != s:  # NaN sentinel
        return None, f"no official SPOT on {as_of} to convert {ccy} to USD"
    return float(s), ""


# --------------------------------------------------------------------------- read / dispatch

@dataclass
class PricingOutcome:
    trade_id: str
    instrument_id: str
    package_id: str
    quantity: float
    priced: bool
    reason: str = ""
    result: Optional[pricer.RateOptionResult] = None
    vol_source_kind: str = ""   # FLAT | SABR | '' (Bermudan: '', vol not used the same way)
    vol_detail: str = ""


def _read_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT t.trade_id, t.instrument_id, t.product, t.package_id, t.quantity,
               i.base_ccy, i.expiry_date,
               o.payoff, o.option_type, o.strike, o."index", o.underlying_start, o.underlying_end,
               o.exercise_dates, o.fixed_freq, o.float_freq
        FROM trades_official t
        JOIN instruments i ON i.instrument_id = t.instrument_id
        LEFT JOIN instrument_rate_options o ON o.instrument_id = t.instrument_id
        WHERE t.trade_id = ?
        """,
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    (trade_id, instrument_id, product, package_id, quantity, ccy, expiry_date,
     payoff, option_type, strike, index, underlying_start, underlying_end,
     exercise_dates, fixed_freq, float_freq) = row
    return dict(
        trade_id=trade_id, instrument_id=instrument_id, product=product, package_id=package_id,
        quantity=quantity, ccy=ccy, expiry_date=expiry_date,
        payoff=payoff, option_type=option_type, strike=strike, index=index,
        underlying_start=underlying_start, underlying_end=underlying_end,
        exercise_dates=exercise_dates or "", fixed_freq=fixed_freq or "", float_freq=float_freq or "",
    )


def _skip(row: dict, reason: str) -> PricingOutcome:
    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=False, reason=reason,
    )


def _price_row(conn: sqlite3.Connection, as_of: str, row: dict, curve_cache: Optional[dict] = None) -> PricingOutcome:
    if row["product"] not in ("SWAPTION", "CAP_FLOOR"):
        return _skip(row, f"product {row['product']!r} is not SWAPTION or CAP_FLOOR")
    if row["payoff"] is None:
        return _skip(row, "no instrument_rate_options row")
    payoff = row["payoff"]
    if payoff not in ("SWAPTION", "BERMUDAN_SWAPTION", "CAP", "FLOOR"):
        return _skip(row, f"unknown payoff {payoff!r}")
    if row["quantity"] == 0:
        return _skip(row, "quantity is 0 -- no position to price")

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry_date = datetime.date.fromisoformat(row["expiry_date"])
    if expiry_date <= as_of_date:
        return _skip(row, f"expiry {row['expiry_date']} is not after as_of {as_of}")

    underlying_start = datetime.date.fromisoformat(row["underlying_start"])
    underlying_end = datetime.date.fromisoformat(row["underlying_end"])

    cache_key = (row["ccy"], row["index"])
    cached_curve_set = curve_cache.get(cache_key) if curve_cache is not None else None
    curve_inputs = derive_curve_inputs(
        conn, as_of, row["ccy"], row["index"], expiry_date, underlying_start, underlying_end,
        curve_set=cached_curve_set,
    )
    if curve_inputs is None:
        return _skip(row, f"no curve_quotes for ({row['ccy']}, {row['index']}) as of {as_of}")
    if curve_cache is not None:
        curve_cache[cache_key] = curve_inputs.curve_set

    quantity, strike = row["quantity"], row["strike"]
    vol_source_kind, vol_detail = "", ""

    if payoff == "SWAPTION":
        if row["option_type"] not in ("PAYER", "RECEIVER"):
            return _skip(row, f"unrecognized option_type {row['option_type']!r}")
        expiry_years = year_fraction(as_of_date, expiry_date)
        swap_tenor_years = year_fraction(underlying_start, underlying_end)
        underlying_tenor = f"{round(swap_tenor_years)}Y"

        vol_res = resolve_swaption_vol(
            conn, as_of, row["ccy"], row["index"], row["expiry_date"], underlying_tenor, strike,
            expiry_years, curve_inputs.forward_rate,
        )
        if vol_res.vol is None:
            return _skip(row, vol_res.reason)
        vol_source_kind, vol_detail = vol_res.source_kind, vol_res.detail

        result = pricer.price_swaption(
            quantity, strike, row["option_type"], expiry_years, swap_tenor_years,
            curve_inputs.curve, vol_res.vol, as_of_date,
        )
    elif payoff == "BERMUDAN_SWAPTION":
        # No Black-76 vol lookup here at all -- a Bermudan is priced under Hull-White,
        # which takes its own (a, sigma) model parameters, not a market lognormal vol.
        if row["option_type"] not in ("PAYER", "RECEIVER"):
            return _skip(row, f"unrecognized option_type {row['option_type']!r}")
        exercise_dates = _parse_exercise_dates(row["exercise_dates"])
        if not exercise_dates:
            return _skip(row, "no exercise dates")
        expiry_years = year_fraction(as_of_date, expiry_date)
        swap_tenor_years = year_fraction(underlying_start, underlying_end)

        hw = get_rate_model_params(conn, as_of, row["ccy"], row["index"], "HULL_WHITE")
        if not {"a", "sigma"}.issubset(hw):
            return _skip(row, "no Hull-White (a, sigma) params staged in rate_model_params")
        try:
            result = pricer.price_bermudan_swaption(
                quantity, strike, row["option_type"], expiry_years, swap_tenor_years, exercise_dates,
                curve_inputs.curve, hw["a"], hw["sigma"], as_of_date,
            )
        except ValueError as exc:
            return _skip(row, f"Hull-White pricing failed: {exc}")
    else:  # CAP or FLOOR
        start_years = year_fraction(as_of_date, underlying_start)
        tenor_years = year_fraction(underlying_start, underlying_end)
        vol_res = resolve_capfloor_vol(conn, as_of, row["ccy"], row["index"], row["expiry_date"], strike)
        if vol_res.vol is None:
            return _skip(row, vol_res.reason)
        vol_source_kind, vol_detail = vol_res.source_kind, vol_res.detail
        freq_months = _freq_to_months(row["float_freq"])
        result = pricer.price_cap_floor(
            quantity, payoff, strike, start_years, tenor_years,
            curve_inputs.curve, vol_res.vol, as_of_date, freq_months,
        )

    fx, fx_reason = _usd_per_ccy(conn, row["ccy"], as_of)
    if fx is None:
        return _skip(row, fx_reason)

    snapped = snapped_at(as_of_date)
    settle_date = row["expiry_date"]
    mark_rows = [
        (as_of, row["instrument_id"], settle_date, "PV_USD", result.npv_total * fx, "QL_PRICER", snapped),
        (as_of, row["instrument_id"], settle_date, "DV01_USD", result.dv01 * fx, "QL_PRICER", snapped),
        (as_of, row["instrument_id"], settle_date, "VEGA", result.vega * fx, "QL_OPTIONS_PRICER", snapped),
        (as_of, row["instrument_id"], settle_date, "GAMMA", result.gamma * fx, "QL_OPTIONS_PRICER", snapped),
        (as_of, row["instrument_id"], settle_date, "THETA", result.theta * fx, "QL_OPTIONS_PRICER", snapped),
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            mark_rows,
        )

    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=quantity, priced=True, result=result,
        vol_source_kind=vol_source_kind, vol_detail=vol_detail,
    )


def price_and_store(conn: sqlite3.Connection, as_of: str, trade_id: str) -> PricingOutcome:
    """Price one SWAPTION/CAP_FLOOR trade (read from `trades_official`) as of `as_of`
    and write its marks. Raises ValueError if no such trade exists; returns a
    PricingOutcome with priced=False (never raises) for every other reason a trade
    cannot be priced."""
    row = _read_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    return _price_row(conn, as_of, row, curve_cache={})


def price_all_and_store(conn: sqlite3.Connection, as_of: str) -> List[PricingOutcome]:
    """Price every SWAPTION/CAP_FLOOR trade in `trades_official` as of `as_of`. One
    curve_cache dict is shared across the run so trades sharing a (ccy, index) reuse the
    same bootstrapped CurveSet rather than rebuilding it per trade (mirrors
    engine/rates/store.py::price_all_and_store's per-currency curve cache)."""
    trade_ids = [
        r[0] for r in conn.execute(
            "SELECT trade_id FROM trades_official WHERE product IN ('SWAPTION','CAP_FLOOR') ORDER BY trade_id"
        ).fetchall()
    ]
    curve_cache: Dict = {}
    outcomes = []
    for trade_id in trade_ids:
        row = _read_trade(conn, trade_id)
        outcomes.append(_price_row(conn, as_of, row, curve_cache=curve_cache))
    return outcomes
