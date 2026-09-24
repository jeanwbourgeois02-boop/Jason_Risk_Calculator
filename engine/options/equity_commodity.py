"""Options on commodity futures: Greeks at the vol Bloomberg's own price implies.

Owned by the listed-options-pricer lane. The equity-index branch (SPX options, the index
level as underlying, the dividend yield and its `equity_dividend_yields` table) left the
app on 2026-09-24 with the rest of the macro trader's book (CLAUDE.md "Commodity
conversion plan", Phase 2; user yes the same day). What stays is the path Phase 5 wires
options on commodity futures into (product ``'CMDTY_OPTION'``).

**No model enters the P&L.** A listed option's P&L is ``contracts x multiplier x (m - f)``
on Bloomberg's own price of it (official FUTURE_PX on the option's instrument, dated at
its expiry), read by `engine/pnl`, never by this module. What is written here are the
Greeks (and a PREMIUM alongside them), and a missing input blanks those with its reason,
never the P&L.

**Underlying.** A futures contract. The option's own ``instruments.bbg_ticker`` holds the
underlying future's instrument_id (``'CLZ26 Comdty'``), a repurposing of that column
distinct from its meaning on a future's own row. Its price is the future's official
FUTURE_PX of the same day (Bloomberg's, BBG_BDH), at the future's own expiry; with no
such row on file the Greeks are blank with the reason. Never another day's price and
never another source.

**Model.** Black-76 on the futures price (vendored ``options_calc.commodity``: the carry
rate is tied to ``r`` inside the pricer): VANILLA European, AMERICAN (binomial tree),
ASIAN (arithmetic average, 12 fixings). No barrier, digital or touch pricer exists for a
commodity upstream, so those payoffs are skipped, never priced some other way.

**Rate.** One OIS zero rate to the option's expiry off ``instruments.quote_ccy``'s curve
(`engine/options/rates.py::resolve_ccy_rate`; the USD OIS curve for a USD contract). A
currency with no curve blanks the Greeks with that reason.

**Vol**, in priority order, never blended:
  (a) the vol IMPLIED by Bloomberg's own price of the option (the official FUTURE_PX on
      the option's instrument that day), for VANILLA (Black-76) and AMERICAN (Black-76
      under Barone-Adesi-Whaley, the vendored solver with the carry equal to ``r``; the
      tree then prices at that vol, so PREMIUM can differ from Bloomberg's price by the
      BAW-vs-tree gap, about 0.1-1 %). A price outside the no-arbitrage bounds implies no
      vol: the Greeks are blank with that reason, and no other vol is substituted.
  (b) with no Bloomberg price that day (or an ASIAN payoff): ``vol_surface_points``, a
      full strike x tenor grid per (as_of, underlying), when the strike is known;
  (c) else this package's flat ``option_vols`` table (``inputs.py``), its `pair` column
      keyed on the underlying's instrument_id: exact expiry, then the ``'*'`` flat row;
  (d) else skip "no vol".

**Premium unit.** The vendored pricers return an unscaled quote-ccy price per 1 unit of
the underlying; the PREMIUM mark is that price times ``instruments.multiplier`` (the value
of one contract), not the FX base-notional fraction. Delta, gamma, theta, vega and rho are
the vendored pricer's own, per 1 unit of the underlying.
"""
from __future__ import annotations

import datetime
import math
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from . import pricer
from .inputs import get_manual_vol
from .rates import resolve_ccy_rate
from engine.rates.store import snapped_at

PRODUCT = "CMDTY_OPTION"

_MARK_FIELDS = ("PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO")

_COMMODITY_PAYOFFS = {"VANILLA", "AMERICAN", "ASIAN"}  # no barrier / digital / touch upstream
_IMPLIED_VOL_PAYOFFS = ("VANILLA", "AMERICAN")

# --------------------------------------------------------------------------- vol surface

_VOL_SURFACE_POINTS_DDL = """
CREATE TABLE IF NOT EXISTS vol_surface_points (
  as_of_date      TEXT NOT NULL,
  underlying      TEXT NOT NULL,
  tenor_days      INTEGER NOT NULL,
  strike          REAL NOT NULL,
  vol             REAL NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, underlying, tenor_days, strike, source)
);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(_VOL_SURFACE_POINTS_DDL)


def write_vol_surface_points(conn: sqlite3.Connection, as_of: str, underlying: str, points, source: str = "MANUAL") -> None:
    """`points`: iterable of (tenor_days, strike, vol) tuples. They must form a full
    rectangular grid (every strike quoted at every tenor) for `_build_vol_surface` to
    use them; a partial grid is refused there, not guessed at."""
    ensure_tables(conn)
    rows = [(as_of, underlying, int(td), float(k), float(v), source) for td, k, v in points]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO vol_surface_points "
            "(as_of_date, underlying, tenor_days, strike, vol, source) VALUES (?,?,?,?,?,?)",
            rows,
        )


def _build_vol_surface(conn: sqlite3.Connection, as_of: str, underlying: str, source: str = "MANUAL"):
    """An options_calc.vol_surface.VolSurface from vol_surface_points, or None with no
    points staged or a grid that is not a full rectangle (refused rather than guessed)."""
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT DISTINCT tenor_days, strike, vol FROM vol_surface_points "
        "WHERE as_of_date = ? AND underlying = ? AND source = ? ORDER BY tenor_days, strike",
        (as_of, underlying, source),
    ).fetchall()
    if not rows:
        return None
    tenors = sorted({r[0] for r in rows})
    strikes = sorted({r[1] for r in rows})
    grid = {(td, k): v for td, k, v in rows}
    try:
        vols = [[grid[(td, k)] for k in strikes] for td in tenors]
    except KeyError:
        return None
    from .vendor.options_calc.vol_surface import VolSurface

    return VolSurface(strikes=strikes, tenors=[td / 365.0 for td in tenors], vols=vols)


# --------------------------------------------------------------------------- inputs

@dataclass
class CommodityInputs:
    future_price: float
    r: float
    vol: float
    vol_source: str  # 'IMPLIED' | 'SURFACE' | 'MANUAL'


@dataclass
class CommodityInputsResult:
    inputs: Optional[CommodityInputs]
    reason: str = ""


def _number(value) -> Optional[float]:
    """`value` as a finite float, or None when it is not a number (a data error)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _future_price(conn: sqlite3.Connection, as_of: str, underlying: str):
    """(price, '') of the underlying future on `as_of`, Bloomberg's official FUTURE_PX, or
    (None, reason). The row at the future's own expiry wins; a single row at another date
    is the same contract after its expiry was moved to Bloomberg's own date."""
    rows = conn.execute(
        "SELECT m.settle_date, m.value, i.expiry_date FROM marks_official m "
        "LEFT JOIN instruments i ON i.instrument_id = m.instrument_id "
        "WHERE m.as_of_date = ? AND m.instrument_id = ? AND m.mark_type = 'FUTURE_PX'",
        (as_of, underlying),
    ).fetchall()
    exact = [r for r in rows if r[0] == r[2]]
    picked = exact[0] if exact else (rows[0] if len(rows) == 1 else None)
    if picked is None:
        if rows:
            return None, (f"{len(rows)} Bloomberg prices of the underlying future {underlying} on {as_of}, "
                          "none at its expiry")
        return None, f"no Bloomberg price of the underlying future {underlying} on {as_of}"
    price = _number(picked[1])
    if price is None or price <= 0:
        return None, f"Bloomberg's price of the underlying future {underlying} on {as_of} is not usable ({picked[1]!r})"
    return price, ""


def _listed_price(conn: sqlite3.Connection, as_of: str, instrument_id: str, expiry_iso: str):
    """(price, '') Bloomberg's own price of the listed option on `as_of` (official
    FUTURE_PX on the option's instrument at its expiry, per unit of the underlying),
    (None, '') when there is none, or (None, reason) when the stored value is not a
    number."""
    hit = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND settle_date = ? "
        "AND mark_type = 'FUTURE_PX'", (as_of, instrument_id, expiry_iso),
    ).fetchone()
    if hit is None:
        return None, ""
    price = _number(hit[0])
    if price is None:
        return None, f"Bloomberg's price of the option {instrument_id} on {as_of} is not a number ({hit[0]!r})"
    return price, ""


def _implied_vol(price, payoff, future_price, strike, T, r, option_type):
    """(vol, '') implied by a listed option's market `price` under Black-76, or (None, reason)."""
    try:
        if payoff == "AMERICAN":
            from .vendor.options_calc.equity.implied_vol import implied_volatility_american

            # Black-76 American: the carry (dividend) rate equal to r cancels the drift.
            vol = float(implied_volatility_american(price, future_price, strike, T, r, option_type.lower(), r))
        else:
            from .vendor.options_calc.commodity.implied_vol import implied_volatility

            vol = float(implied_volatility(price, future_price, strike, T, r, option_type.lower()))
    except Exception as exc:  # noqa: BLE001 -- a price outside the no-arbitrage bounds has no vol
        return None, (f"no vol is implied by Bloomberg's price {price:g} with the future at "
                      f"{future_price:g} ({exc})")
    if not (math.isfinite(vol) and vol > 0):
        return None, f"no vol is implied by Bloomberg's price {price:g} with the future at {future_price:g}"
    return vol, ""


def _resolve_vol(conn, as_of, underlying, expiry_iso, strike, as_of_date, expiry_date):
    surface = _build_vol_surface(conn, as_of, underlying)
    if surface is not None and strike is not None and strike > 0:
        T = (expiry_date - as_of_date).days / 365.0
        return surface.get_vol(strike, T), "SURFACE"
    manual = get_manual_vol(conn, as_of, underlying, expiry_iso)
    if manual is not None:
        return manual, "MANUAL"
    return None, ""


def resolve_commodity_inputs(conn, as_of, underlying, quote_ccy, expiry_iso, strike, curve_cache=None,
                             listed: Optional[tuple] = None) -> CommodityInputsResult:
    """The inputs of one option on a future on `as_of`, each from that day alone.

    `listed` = (Bloomberg's price of the option, payoff, option_type): with a VANILLA or
    AMERICAN payoff the vol is then the one that price implies (module docstring)."""
    future_price, reason = _future_price(conn, as_of, underlying)
    if future_price is None:
        return CommodityInputsResult(None, reason)

    r, reason = resolve_ccy_rate(conn, as_of, quote_ccy, expiry_iso, curve_cache)
    if r is None:
        return CommodityInputsResult(None, reason)

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry_date = datetime.date.fromisoformat(expiry_iso)
    if listed is not None and listed[1] in _IMPLIED_VOL_PAYOFFS:
        price, payoff, option_type = listed
        vol, reason = _implied_vol(price, payoff, future_price, strike,
                                   pricer.year_fraction(as_of_date, expiry_date), r, option_type)
        if vol is None:
            return CommodityInputsResult(None, reason)
        vol_source = "IMPLIED"
    else:
        vol, vol_source = _resolve_vol(conn, as_of, underlying, expiry_iso, strike, as_of_date, expiry_date)
    if vol is None:
        return CommodityInputsResult(None, "no vol")

    return CommodityInputsResult(CommodityInputs(future_price=future_price, r=r, vol=vol, vol_source=vol_source))


# --------------------------------------------------------------------------- store

@dataclass
class CommodityOutcome:
    trade_id: str
    instrument_id: str
    package_id: str
    quantity: float
    priced: bool
    reason: str = ""
    result: Optional[pricer.OptionPriceResult] = None
    vol_source: str = ""


def _read_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT t.trade_id, t.instrument_id, t.product, t.package_id, t.quantity,
               i.quote_ccy, i.multiplier, i.bbg_ticker, i.expiry_date,
               o.strike, o.option_type, o.payoff
        FROM trades_official t
        JOIN instruments i ON i.instrument_id = t.instrument_id
        LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id
        WHERE t.trade_id = ?
        """,
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    keys = ("trade_id", "instrument_id", "product", "package_id", "quantity", "quote_ccy", "multiplier",
            "underlying", "expiry_date", "strike", "option_type", "payoff")
    return dict(zip(keys, row))


def _skip(row: dict, reason: str) -> CommodityOutcome:
    return CommodityOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=False, reason=reason,
    )


def _dispatch_commodity(row: dict, as_of_date, expiry, inputs: CommodityInputs) -> pricer.OptionPriceResult:
    return pricer.price_commodity_option(
        row["payoff"], inputs.future_price, row["strike"], expiry, as_of_date, inputs.r, inputs.vol,
        row["option_type"],
    )


def _price_row(conn: sqlite3.Connection, as_of: str, row: dict, curve_cache: Optional[dict] = None) -> CommodityOutcome:
    if row["product"] != PRODUCT:
        return _skip(row, f"product {row['product']!r} is not {PRODUCT}")
    payoff = row["payoff"]
    if payoff is None:
        return _skip(row, "no instrument_options row")
    if payoff not in _COMMODITY_PAYOFFS:
        return _skip(row, f"payoff {payoff!r} not supported for {PRODUCT}")
    # Same wording as the FX path (store.py::NO_STRIKE_REASON; not imported, this module
    # stays independent of store.py).
    if not (row["strike"] and row["strike"] > 0):
        return _skip(row, "no strike on file: enter the strike under Option terms")
    if row["option_type"] not in ("CALL", "PUT"):
        return _skip(row, f"unrecognized option_type {row['option_type']!r}")

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry = datetime.date.fromisoformat(row["expiry_date"])
    if expiry <= as_of_date:
        return _skip(row, f"expiry {row['expiry_date']} is not after as_of {as_of}")

    price, reason = _listed_price(conn, as_of, row["instrument_id"], row["expiry_date"])
    if reason:
        return _skip(row, reason)
    inputs_result = resolve_commodity_inputs(
        conn, as_of, row["underlying"], row["quote_ccy"], row["expiry_date"], row["strike"], curve_cache,
        listed=None if price is None else (price, payoff, row["option_type"]))
    if inputs_result.inputs is None:
        return _skip(row, inputs_result.reason)
    inputs = inputs_result.inputs

    result = _dispatch_commodity(row, as_of_date, expiry, inputs)

    snapped = snapped_at(as_of_date)
    # Premium unit: quote-ccy price per unit x multiplier (module docstring), not the FX
    # base-notional fraction.
    values = {
        "PREMIUM": result.premium * row["multiplier"],
        "DELTA": result.delta,
        "GAMMA": result.gamma,
        "THETA": result.theta,
        "VEGA": result.vega,
        "RHO": result.rho,
    }
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks "
            "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [(as_of, row["instrument_id"], row["expiry_date"], mt, values[mt], "QL_OPTIONS_PRICER", snapped)
             for mt in _MARK_FIELDS],
        )

    return CommodityOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=True, result=result, vol_source=inputs.vol_source,
    )


def price_and_store_commodity(conn: sqlite3.Connection, as_of: str, trade_id: str) -> CommodityOutcome:
    row = _read_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    return _price_row(conn, as_of, row, curve_cache={})


def price_all_and_store_commodity(conn: sqlite3.Connection, as_of: str) -> List[CommodityOutcome]:
    """Price every CMDTY_OPTION trade on file, one outcome per trade, never an exception.

    Same per-trade guard as the FX loop (store.py::price_all_and_store): one trade's
    pricer blowing up is that trade's own skip, reason ``"pricer error: <exc!r>"``, and
    every other outcome is still returned (2026-09-22: an unguarded loop let one OIS
    bootstrap failure escape to the pull's step-level catch and throw away every FX
    option's outcome with it). A trade that cannot be read gets an outcome too. One
    `curve_cache` is shared across the run so trades sharing a currency bootstrap its
    OIS curve once."""
    trade_ids = [r[0] for r in conn.execute(
        "SELECT trade_id FROM trades_official WHERE product = ? ORDER BY trade_id", (PRODUCT,)
    ).fetchall()]
    curve_cache: dict = {}
    outcomes: List[CommodityOutcome] = []
    for trade_id in trade_ids:
        row = None
        try:
            row = _read_trade(conn, trade_id)
            if row is None:
                outcomes.append(CommodityOutcome(trade_id=trade_id, instrument_id="", package_id=trade_id,
                                                 quantity=0.0, priced=False,
                                                 reason="trade could not be read from trades_official"))
                continue
            outcomes.append(_price_row(conn, as_of, row, curve_cache))
        except Exception as exc:  # noqa: BLE001 -- one trade's blow-up is its own skip, as in the FX loop
            reason = f"pricer error: {exc!r}"
            outcomes.append(_skip(row, reason) if row else
                            CommodityOutcome(trade_id=trade_id, instrument_id="", package_id=trade_id,
                                             quantity=0.0, priced=False, reason=reason))
    return outcomes
