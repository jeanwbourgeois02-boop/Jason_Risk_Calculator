"""Book-level Greek aggregation across FX/equity/commodity option
positions (Phase 7, options_calc merge).

Builds vendored ``options_calc.portfolio.Position`` objects from already-
priced outcomes (``store.py``'s ``PricingOutcome`` for FX_OPTION,
``equity_commodity.py``'s ``EqCmdtyOutcome`` for EQ_OPTION/CMDTY_OPTION --
both expose ``.instrument_id``, ``.package_id``, ``.quantity``, ``.priced``,
``.result``, so either feeds this module interchangeably), aggregates via
``Portfolio.total()`` / ``by_asset_class()`` / ``by_label()``, and converts
every Greek to a USD-equivalent number before summing.

**Why conversion is needed at all (MODELS.md's own documented caveat).**
The vendored ``portfolio.py`` will happily sum Greeks across positions on
entirely different underlyings once ``fx_rate_to_base`` currency-converts
them, but that's as far as it goes: "portfolio delta/gamma/vega" beyond
that point is each leg's own NATIVE-unit Greek added together (FX pips,
index points, commodity dollars) -- not one coherent number. This module
is the "real desk normalizes to a common basis" step MODELS.md says the
vendored package deliberately leaves to the caller.

**The conversion, stated once (applies uniformly to price/delta/gamma/
theta/vega/rho).** Every Greek the vendored FX/equity/commodity pricers
return is already a QUOTE-CURRENCY-denominated sensitivity per 1 unit of
underlying (delta = dPrice/dSpot, gamma = d^2Price/dSpot^2, vega =
dPrice/dVol, theta = dPrice/dt, rho = dPrice/dRate -- every one of these
is "quote ccy per (unit move)", not itself a price level needing its own
separate FX conversion). So the SAME single factor converts all six:

    usd_equivalent = native_greek * (quantity * multiplier) * spot_to_usd

where `spot_to_usd` is the position's own `quote_ccy` -> USD rate (this
package's `Position.fx_rate_to_base`, read from `marks_official`'s
official SPOT per CLAUDE.md: quote-ccy P&L converts to USD at SPOT, never
the forward outright -- matching the FX leg P&L rule CLAUDE.md already
documents, extended here to every asset class since they're all
quote-ccy-denominated the same way). `quantity * multiplier` is this
package's own scaling: FX's `multiplier` is always 1.0 (schema.py), so FX
positions scale by `quantity` (base-ccy notional) alone, unchanged from
how `store.py`'s DELTA mark is already meant to be read
(`t.quantity * m.value`, per CLAUDE.md's delta query); equity/commodity
positions additionally multiply by `instruments.multiplier` (e.g. 100
shares/contract), since their Greeks are per 1 unit of the UNDERLYING,
and one contract covers `multiplier` units of it.

**Missing conversion rate -> skipped, not defaulted.** A priced outcome
whose `quote_ccy` has no official SPOT on `as_of` (no `quote_ccy+USD` or
`USD+quote_ccy` pair marked) is excluded from the Portfolio and reported
separately in `skipped` -- never silently assumed 1.0.

**Grouping / UI hierarchy.** ``Position.label`` is set to `package_id`, so
`Portfolio.by_label()` gives the "package" level of the "Portfolio Totals
-> asset class -> package -> leg" hierarchy the UI needs;
`by_asset_class()` gives the middle level; `.total()` gives the top row.
The vendored `Portfolio`/`Position` classes carry no per-instrument detail
below the label they're grouped by, so the bottom "leg" level is the
`legs` list this module returns alongside the `Portfolio` -- each
`PortfolioLeg` keeps `instrument_id` next to its own (already USD-
converted) vendored `Position`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


@dataclass
class PortfolioLeg:
    instrument_id: str
    package_id: str
    asset_class: str
    quantity: float
    position: object  # options_calc.portfolio.Position, already USD-converted


def _quote_ccy_to_usd(conn: sqlite3.Connection, as_of: str, quote_ccy: str) -> Tuple[Optional[float], str]:
    """USD per 1 unit of `quote_ccy` from the official SPOT (CLAUDE.md:
    quote-ccy P&L converts to USD at spot, never the forward outright).
    1.0 for USD itself. (None, reason) if neither `quote_ccy+USD` nor
    `USD+quote_ccy` has an official SPOT on `as_of`."""
    if quote_ccy == "USD":
        return 1.0, ""
    for pair in (quote_ccy + "USD", "USD" + quote_ccy):
        row = conn.execute(
            "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
            (as_of, pair),
        ).fetchone()
        if row is not None:
            spot = row[0]
            # `quote_ccy+USD`: spot is already USD per 1 quote_ccy.
            # `USD+quote_ccy`: spot is quote_ccy per 1 USD -> invert.
            return (spot if pair == quote_ccy + "USD" else 1.0 / spot), ""
    return None, f"no official SPOT to convert {quote_ccy} to USD"


def build_positions(conn: sqlite3.Connection, as_of: str, outcomes: Iterable) -> Tuple[List[PortfolioLeg], List[dict]]:
    """Build one `PortfolioLeg` per priced outcome (unpriced/skipped
    outcomes are silently excluded here -- their own `reason` was already
    surfaced by store.py/equity_commodity.py; this module's `skipped` list
    is only for a PRICED outcome that then can't be USD-converted).
    `outcomes`: PricingOutcome (FX) or EqCmdtyOutcome (equity/commodity)
    instances, or any object exposing the same `.instrument_id` /
    `.package_id` / `.quantity` / `.priced` / `.result` shape."""
    from .vendor.options_calc.portfolio import Position

    legs: List[PortfolioLeg] = []
    skipped: List[dict] = []
    for outcome in outcomes:
        if not outcome.priced:
            continue
        row = conn.execute(
            "SELECT asset_class, quote_ccy, multiplier FROM instruments WHERE instrument_id = ?",
            (outcome.instrument_id,),
        ).fetchone()
        if row is None:
            skipped.append({"instrument_id": outcome.instrument_id, "reason": "no instruments row"})
            continue
        asset_class, quote_ccy, multiplier = row

        spot_to_usd, reason = _quote_ccy_to_usd(conn, as_of, quote_ccy)
        if spot_to_usd is None:
            skipped.append({"instrument_id": outcome.instrument_id, "reason": reason})
            continue

        result = outcome.result
        raw = {
            "price": result.quote_price,
            "delta": result.delta,
            "gamma": result.gamma,
            "theta": result.theta,
            "vega": result.vega,
            "rho": result.rho,
        }
        position = Position(
            label=outcome.package_id,
            asset_class=asset_class,
            result=raw,
            quantity=outcome.quantity * multiplier,
            fx_rate_to_base=spot_to_usd,
        )
        legs.append(PortfolioLeg(
            instrument_id=outcome.instrument_id, package_id=outcome.package_id,
            asset_class=asset_class, quantity=outcome.quantity, position=position,
        ))
    return legs, skipped


def build_portfolio(legs: List[PortfolioLeg]):
    from .vendor.options_calc.portfolio import Portfolio

    return Portfolio([leg.position for leg in legs])


def portfolio_summary(conn: sqlite3.Connection, as_of: str, outcomes: Iterable):
    """One-shot convenience: build positions and the vendored Portfolio
    together. Returns (portfolio, legs, skipped) -- `portfolio.total()` /
    `.by_asset_class()` / `.by_label()` (package-level) give the top two
    rollup levels of "Portfolio Totals -> asset class -> package -> leg";
    `legs` gives the bottom (per-instrument) level; `skipped` lists any
    priced outcome excluded for lacking a USD conversion rate."""
    legs, skipped = build_positions(conn, as_of, outcomes)
    portfolio = build_portfolio(legs)
    return portfolio, legs, skipped
