"""Multi-leg FX_OPTION structure summaries: group already-priced legs by
``trades.package_id`` and combine their Greeks via the vendored
``options_calc.structures.combine()``.

**Grouping rule -- not invented here.** This module does NOT decide which
legs share a package_id; it only groups by whatever ``package_id`` is
already on each trade. Today, ``data/ingest/blotter.py::_parse_option``
sets ``package_id = trade_id`` for every FX_OPTION row (one leg per
package) -- there is no data-ingest rule yet for grouping, say, a
straddle's two legs (long call + long put, same strike/expiry) under one
shared package_id, the way CLAUDE.md's "package_id rule (FX swaps)" groups
two FX forward legs. Extending that rule to option structures is a
data-ingest follow-up (reported in this agent's final report, not decided
unilaterally here); this module is written so a multi-leg package combines
correctly the moment such a package_id grouping exists upstream.

**Combined fields.** Only the base six Greeks
``{price, delta, gamma, theta, vega, rho}`` that
``engine/options/store.py::PricingOutcome.raw`` carries are combined --
the vendored FX pricers' extra fields (``rho_foreign``,
``delta_premium_adjusted``, ``delta_forward``, ...) are not persisted as
marks by store.py and so are not available here to combine. ``combine()``'s
own union-of-keys behavior means adding those fields later (were store.py
extended to keep them) would flow through this module with no change.

**Quantity convention.** Each leg is combined at its own ``trades.quantity``
(signed base-ccy notional; CLAUDE.md: ``> 0`` = long base currency), so the
combined result lands in book (base-ccy) units for delta/gamma/etc --
exactly the way engine/ladder's delta query scales one leg
(``t.quantity * m.value``), extended here to every leg in a package.

**Lazy vendor import.** ``combine()`` lives in
``engine/options/vendor/options_calc/structures.py``, which has zero
dependencies of its own -- but reaching it via
``engine.options.vendor.options_calc.structures`` still executes
``options_calc/__init__.py`` first (which imports the QuantLib-dependent
``fx``/``equity``/``rates``/``commodity`` subpackages), so the import is
kept local to ``combine_package`` rather than at module top, matching
``pricer.py``'s / ``engine/rates/store.py``'s pattern.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List

from .store import PricingOutcome, price_and_store

_LEG_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}


@dataclass
class StructureSummary:
    package_id: str
    leg_count: int
    priced_leg_count: int
    label: str
    combined: Dict[str, float]
    outcomes: List[PricingOutcome] = field(default_factory=list)


def _label(leg_count: int) -> str:
    word = _LEG_WORDS.get(leg_count, str(leg_count))
    return f"{word} Leg"


def _package_trade_ids(conn: sqlite3.Connection, package_id: str) -> List[str]:
    return [
        r[0] for r in conn.execute(
            "SELECT trade_id FROM trades_official WHERE package_id = ? AND product = 'FX_OPTION' "
            "ORDER BY trade_id",
            (package_id,),
        ).fetchall()
    ]


def combine_package(conn: sqlite3.Connection, as_of: str, package_id: str) -> StructureSummary:
    """Price (and store marks for) every FX_OPTION leg sharing `package_id`,
    then combine their raw Greeks into one structure-level summary via the
    vendored combine(). Legs that could not be priced (see store.py's skip
    reasons) are excluded from `combined` but still listed in `outcomes` so
    the caller can see why."""
    from .vendor.options_calc.structures import combine

    trade_ids = _package_trade_ids(conn, package_id)
    if not trade_ids:
        raise ValueError(f"No FX_OPTION trades with package_id {package_id!r}")

    outcomes = [price_and_store(conn, as_of, trade_id) for trade_id in trade_ids]
    legs = [(o.raw, o.quantity) for o in outcomes if o.priced]
    combined = combine(*legs) if legs else {}

    return StructureSummary(
        package_id=package_id,
        leg_count=len(trade_ids),
        priced_leg_count=len(legs),
        label=_label(len(trade_ids)),
        combined=combined,
        outcomes=outcomes,
    )
