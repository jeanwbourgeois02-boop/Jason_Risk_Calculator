"""Glue between engine/options' pricers and this app's SQLite schema: reads
FX_OPTION trades from ``trades_official`` / ``instruments`` /
``instrument_options``, writes ``PREMIUM`` / ``DELTA`` / ``GAMMA`` / ``THETA``
/ ``VEGA`` / ``RHO`` marks (``source='QL_OPTIONS_PRICER'``) -- the same shape
of glue ``engine/rates/store.py`` provides for IRS.

Two entry points (task spec, mirrors engine/rates/store.py):
  - ``price_and_store(conn, as_of, trade_id)``: price one FX_OPTION trade,
    write its marks, return a ``PricingOutcome``.
  - ``price_all_and_store(conn, as_of)``: same, for every FX_OPTION trade in
    ``trades_official`` as of that date.

**Conventions restated here (see CLAUDE.md, pricer.py, inputs.py for the
full detail):**
  - ``trades_official`` is read, never raw ``trades`` -- avoids double-
    counting a trade the BNP snapshot also carries under a different
    trade_id scheme (schema.py's view docstring).
  - ``trades.quantity > 0`` = long base currency (CLAUDE.md sign
    convention). This module does not apply that sign to PREMIUM/DELTA/etc:
    every mark written here is PER 1 UNIT of ``trades.quantity`` (pricer.py's
    own convention, matching the marks table's DELTA comment), so the sign
    is applied downstream by whoever reads the mark and multiplies by
    ``trades.quantity`` (engine/ladder's delta query does exactly this).
  - A trade that cannot be priced (missing strike, missing barrier_level,
    missing market inputs, unsupported payoff, or an expiry that has
    already passed) writes NO marks and comes back as a ``PricingOutcome``
    with ``priced=False`` and a ``reason`` -- never a fabricated number.
  - ``instrument_options.strike``/``barrier_level`` use the schema's own
    "0 = not known" sentinel (schema.py comment) -- real blotter rows with
    no strike embedded in their free-text Description land here as strike
    0.0, and this module treats that exactly as "not known", not as a
    genuine zero strike.
  - Vol provenance (Phase 5b, 2026-09-17): ``resolve_market_inputs``
    (inputs.py) resolves each priced trade's vol via SMILE / ATM_INTERP /
    MANUAL, in that priority. ``PricingOutcome.vol_source_kind`` /
    ``.vol_detail`` record which one actually fed the marks written for
    that trade, so a reader never has to re-derive it. One `surface_cache`
    dict is shared across every trade in a single ``price_all_and_store``
    run so trades sharing a pair don't each rebuild that pair's
    FXDeltaVolSurface (see inputs.py::_cached_surface).
  - Rate provenance (Phase 7.1, 2026-09-17; wired onto PricingOutcome Phase
    7.2, 2026-09-18): ``PricingOutcome.domestic_rate_source_kind`` /
    ``.domestic_rate_detail`` and ``.foreign_rate_source_kind`` /
    ``.foreign_rate_detail`` mirror the vol fields above, one pair per
    currency leg -- ``OIS_CURVE`` / ``MANUAL_EXPIRY`` / ``MANUAL_FLAT`` /
    ``IMPLIED_FORWARD`` (``engine/options/rates.py::RateInput.source_kind``).
    An ``IMPLIED_FORWARD`` detail reads e.g. "implied from EURSEK forward
    2026-11-25 and EUR ESTR curve" -- this is the string a diagnostics
    screen should surface for a covered-interest-parity-implied rate.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import pricer
from .inputs import resolve_market_inputs
from engine.rates.store import snapped_at

# Payoffs this phase can dispatch. AMERICAN/ASIAN/BARRIER_KI/BARRIER_KO/
# ONE_TOUCH/NO_TOUCH landed Phase 4; anything else (or an unrecognized
# string) is a structured skip, not an error, so a bad/blank payoff value
# never silently falls through to VANILLA.
_STRIKE_PAYOFFS = {"VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO"}
_BARRIER_PAYOFFS = {"BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH"}

_MARK_FIELDS = ("PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO")


@dataclass
class PricingOutcome:
    trade_id: str
    instrument_id: str
    package_id: str
    quantity: float
    priced: bool
    reason: str = ""
    result: Optional[pricer.OptionPriceResult] = None
    # Base-six-Greek dict {'price', 'delta', 'gamma', 'theta', 'vega', 'rho'}
    # in the vendored pricer's own pre-conversion units (quote ccy for
    # 'price'), kept so engine/options/structures.py can combine legs
    # without re-pricing them.
    raw: Optional[Dict[str, float]] = None
    # Provenance of the vol that fed this outcome's marks -- SMILE |
    # ATM_INTERP | MANUAL (inputs.py::VolInput.source_kind), or None for a
    # skipped trade (no vol was resolved at all). See inputs.py's "Vol"
    # docstring section for the full priority. `vol_detail` is the paired
    # human-readable detail string, "" when vol_source_kind is None.
    vol_source_kind: Optional[str] = None
    vol_detail: str = ""
    # Rate provenance (Phase 7.2, 2026-09-18) -- see module docstring's
    # "Rate provenance" bullet. None/"" for a skipped trade (inputs never
    # resolved), same convention as the vol fields above.
    domestic_rate_source_kind: Optional[str] = None
    domestic_rate_detail: str = ""
    foreign_rate_source_kind: Optional[str] = None
    foreign_rate_detail: str = ""


def _read_option_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT t.trade_id, t.instrument_id, t.product, t.package_id, t.quantity,
               i.base_ccy, i.quote_ccy, i.expiry_date,
               o.strike, o.option_type, o.barrier_level, o.avg_start_date, o.payoff
        FROM trades_official t
        JOIN instruments i ON i.instrument_id = t.instrument_id
        LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id
        WHERE t.trade_id = ?
        """,
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    (trade_id, instrument_id, product, package_id, quantity,
     base_ccy, quote_ccy, expiry_date,
     strike, option_type, barrier_level, avg_start_date, payoff) = row
    return dict(
        trade_id=trade_id, instrument_id=instrument_id, product=product, package_id=package_id,
        quantity=quantity, base_ccy=base_ccy, quote_ccy=quote_ccy, expiry_date=expiry_date,
        strike=strike, option_type=option_type, barrier_level=barrier_level,
        avg_start_date=avg_start_date, payoff=payoff,
    )


def _skip(row: dict, reason: str) -> PricingOutcome:
    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=False, reason=reason,
    )


def _dispatch(row: dict, as_of_date: datetime.date, expiry: datetime.date, inputs, pair: str) -> pricer.OptionPriceResult:
    """Call the payoff-appropriate pricer.py wrapper. Raises only for
    programmer error (unreachable payoff values are filtered by callers
    before this is invoked). `pair` is passed through to every pricer.py
    call so T uses the calendar-aware year fraction and delta_convention/
    delta_premium_adjusted are populated -- see pricer.py / calendars.py
    (Phase 7)."""
    S = inputs.spot
    K = row["strike"]
    option_type = row["option_type"]
    dr, fr, vol = inputs.domestic_rate, inputs.foreign_rate, inputs.vol
    payoff = row["payoff"]

    if payoff == "VANILLA":
        return pricer.price_fx_vanilla(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff == "DIGITAL":
        return pricer.price_fx_digital(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff == "AMERICAN":
        return pricer.price_fx_american(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff == "ASIAN":
        # avg_start_date read into `row` above but not passed through -- see
        # pricer.py::price_fx_asian's docstring for why (vendored engine has
        # no such parameter).
        return pricer.price_fx_asian(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff in ("BARRIER_KI", "BARRIER_KO"):
        barrier_level = row["barrier_level"]
        # Derivation rule (task instruction: "derive from barrier vs spot"):
        # the vendored barrier pricer needs an explicit up/down direction
        # ('up-and-*' requires barrier > S, 'down-and-*' requires barrier <
        # S -- QuantLib's AnalyticBarrierEngine assumes this ordering and
        # gives nonsense otherwise). Barrier ABOVE current spot -> "up";
        # barrier BELOW spot -> "down". KI (knock-in) suffix '-and-in', KO
        # (knock-out) suffix '-and-out'.
        direction = "up" if barrier_level > S else "down"
        suffix = "-and-in" if payoff == "BARRIER_KI" else "-and-out"
        barrier_type = f"{direction}{suffix}"
        return pricer.price_fx_barrier(
            S, K, barrier_level, expiry, as_of_date, dr, fr, vol, option_type, barrier_type, pair=pair,
        )
    if payoff in ("ONE_TOUCH", "NO_TOUCH"):
        barrier_level = row["barrier_level"]
        # Same up/down derivation as the barrier branch above.
        direction = "up" if barrier_level > S else "down"
        fn = pricer.price_fx_one_touch if payoff == "ONE_TOUCH" else pricer.price_fx_no_touch
        return fn(S, barrier_level, expiry, as_of_date, dr, fr, vol, direction, pair=pair)
    raise ValueError(f"unreachable payoff {payoff!r}")  # pragma: no cover


def _price_row(
    conn: sqlite3.Connection,
    as_of: str,
    row: dict,
    surface_cache: Optional[dict] = None,
    curve_cache: Optional[dict] = None,
) -> PricingOutcome:
    if row["product"] != "FX_OPTION":
        return _skip(row, f"product {row['product']!r} is not FX_OPTION")
    if row["payoff"] is None:
        return _skip(row, "no instrument_options row")

    payoff = row["payoff"]
    supported = _STRIKE_PAYOFFS | _BARRIER_PAYOFFS
    if payoff not in supported:
        return _skip(row, f"payoff {payoff!r} not supported")

    if payoff in _STRIKE_PAYOFFS and row["strike"] == 0:
        return _skip(row, "strike is 0 (not known)")
    if payoff in _BARRIER_PAYOFFS and row["barrier_level"] == 0:
        return _skip(row, "barrier_level is 0 (not known)")
    if payoff != "ONE_TOUCH" and payoff != "NO_TOUCH":
        if row["option_type"] not in ("CALL", "PUT"):
            return _skip(row, f"unrecognized option_type {row['option_type']!r}")

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry = datetime.date.fromisoformat(row["expiry_date"])
    if expiry <= as_of_date:
        return _skip(row, f"expiry {row['expiry_date']} is not after as_of {as_of}")

    pair = row["base_ccy"] + row["quote_ccy"]
    inputs_result = resolve_market_inputs(
        conn, as_of, pair, row["expiry_date"], strike=row["strike"],
        surface_cache=surface_cache, curve_cache=curve_cache,
    )
    if inputs_result.inputs is None:
        return _skip(row, inputs_result.reason)
    inputs = inputs_result.inputs

    result = _dispatch(row, as_of_date, expiry, inputs, pair)

    snapped = snapped_at(as_of_date)
    settle_date = row["expiry_date"]
    values = {
        "PREMIUM": result.premium,
        "DELTA": result.delta,
        "GAMMA": result.gamma,
        "THETA": result.theta,
        "VEGA": result.vega,
        "RHO": result.rho,
    }
    mark_rows = [
        (as_of, row["instrument_id"], settle_date, mark_type, values[mark_type], "QL_OPTIONS_PRICER", snapped)
        for mark_type in _MARK_FIELDS
    ]
    # DELTA_PA (Phase 7): only for a pair whose market convention is
    # premium-adjusted delta (pricer.py::_delta_convention /
    # options_calc.fx.g10.recommended_delta) -- for a RAW-convention or
    # UNKNOWN (non-G10) pair, no DELTA_PA row is written; DELTA (raw spot
    # delta) is unconditionally written above regardless, unchanged from
    # Phase 2. NOTE: 'DELTA_PA' is not yet in data/ingest/schema.py's
    # OFFICIAL_MARK_SOURCE mapping (out of this package's ownership), so
    # it is written to `marks` but does not appear in `marks_official`
    # until that mapping is extended -- reported to housekeeper.
    if result.delta_convention == "PREMIUM_ADJUSTED":
        mark_rows.append(
            (as_of, row["instrument_id"], settle_date, "DELTA_PA", result.delta_premium_adjusted,
             "QL_OPTIONS_PRICER", snapped)
        )
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks "
            "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            mark_rows,
        )

    raw = {
        "price": result.quote_price,
        "delta": result.delta,
        "gamma": result.gamma,
        "theta": result.theta,
        "vega": result.vega,
        "rho": result.rho,
    }
    vol_source = inputs.vol_source
    dom_rate_source = inputs.domestic_rate_source
    for_rate_source = inputs.foreign_rate_source
    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=True, result=result, raw=raw,
        vol_source_kind=vol_source.source_kind if vol_source is not None else None,
        vol_detail=vol_source.detail if vol_source is not None else "",
        domestic_rate_source_kind=dom_rate_source.source_kind if dom_rate_source is not None else None,
        domestic_rate_detail=dom_rate_source.detail if dom_rate_source is not None else "",
        foreign_rate_source_kind=for_rate_source.source_kind if for_rate_source is not None else None,
        foreign_rate_detail=for_rate_source.detail if for_rate_source is not None else "",
    )


VALID_PAYOFFS = ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH")


def set_option_terms(conn: sqlite3.Connection, instrument_id: str, strike: float, option_type: str,
                     payoff: str = "VANILLA", barrier_level: float = 0.0) -> None:
    """Record (or correct) an option's terms in `instrument_options`, for options whose
    blotter export does not carry them (a digital with no STRIKE clause in its
    Description, a barrier level, a mis-detected payoff). The next pricing run reads them.
    Validates: instrument must be an option on file; payoff in VALID_PAYOFFS; option_type
    CALL/PUT; strike > 0 for strike payoffs; barrier_level > 0 for barrier/touch payoffs.
    Raises ValueError with a plain message otherwise. Commits."""
    row = conn.execute("SELECT asset_class FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if row is None or row[0] not in ("FX_OPTION", "EQ_OPTION", "CMDTY_OPTION"):
        raise ValueError(f"{instrument_id!r} is not an option instrument on file")
    payoff = (payoff or "VANILLA").upper()
    if payoff not in VALID_PAYOFFS:
        raise ValueError(f"payoff must be one of {', '.join(VALID_PAYOFFS)}")
    option_type = (option_type or "").upper()
    if option_type not in ("CALL", "PUT"):
        raise ValueError("option type must be CALL or PUT")
    strike = float(strike or 0.0)
    barrier_level = float(barrier_level or 0.0)
    if payoff in _STRIKE_PAYOFFS and strike <= 0:
        raise ValueError("strike must be greater than 0 for this payoff")
    if payoff in ("BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH") and barrier_level <= 0:
        raise ValueError("barrier / touch level must be greater than 0 for this payoff")
    with conn:
        conn.execute(
            "INSERT INTO instrument_options (instrument_id, strike, option_type, barrier_level, avg_start_date, payoff) "
            "VALUES (?, ?, ?, ?, '9999-12-31', ?) ON CONFLICT(instrument_id) DO UPDATE SET "
            "strike = excluded.strike, option_type = excluded.option_type, "
            "barrier_level = excluded.barrier_level, payoff = excluded.payoff",
            (instrument_id, strike, option_type, barrier_level, payoff))


def price_and_store(conn: sqlite3.Connection, as_of: str, trade_id: str) -> PricingOutcome:
    """Price one FX_OPTION trade (read from ``trades_official``) as of
    ``as_of`` (ISO date string) and write its marks. Raises ValueError if no
    such trade exists; returns a PricingOutcome with ``priced=False`` (never
    raises) for every other reason a trade cannot be priced."""
    row = _read_option_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    # Fresh, single-trade surface/curve cache -- no reuse across calls, but
    # keeps the same code path as price_all_and_store below.
    return _price_row(conn, as_of, row, surface_cache={}, curve_cache={})


def price_all_and_store(conn: sqlite3.Connection, as_of: str) -> List[PricingOutcome]:
    """Price every FX_OPTION trade in ``trades_official`` as of ``as_of``.
    One `surface_cache` dict is shared across the whole run (see
    inputs.py::_cached_surface) so that N trades on the same pair build
    that pair's FXDeltaVolSurface once, not N times; likewise one
    `curve_cache` dict (see engine/options/rates.py) so N trades sharing a
    currency build that currency's OIS bootstrap once, not N times."""
    trade_ids = [
        r[0] for r in conn.execute(
            "SELECT trade_id FROM trades_official WHERE product = 'FX_OPTION' ORDER BY trade_id"
        ).fetchall()
    ]
    surface_cache: dict = {}
    curve_cache: dict = {}
    outcomes = []
    for trade_id in trade_ids:
        row = _read_option_trade(conn, trade_id)
        try:
            outcomes.append(_price_row(conn, as_of, row, surface_cache=surface_cache, curve_cache=curve_cache))
        except Exception as exc:  # noqa: BLE001
            # One option's pricer blowing up (an edge-case payoff / vol / rate combination
            # inside the vendored QuantLib wrappers) must not zero the whole options step
            # for every other trade in the book: before 2026-09-18 the exception escaped
            # to live._options_step's step-level guard and no option priced that cycle.
            # The failing trade is reported like any other skip, with the error text.
            reason = f"pricer error: {exc!r}"
            if row:
                outcomes.append(_skip(row, reason))
            else:
                outcomes.append(PricingOutcome(trade_id=trade_id, instrument_id="", package_id=trade_id,
                                               quantity=0.0, priced=False, reason=reason))
    return outcomes
