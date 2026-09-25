"""A spread's level: where the spread is, in its own quote unit (CLAUDE.md "Screens redesign plan",
Phase B). This is display arithmetic that sits beside the P&L. It never feeds it: a spread's P&L
is still its legs' ``value_book`` rows summed (``book.py``), and nothing here changes a figure
of it.

**The formula is the research app's** (``../Commodity Dashboard/rvapp``: the ``spread_def`` /
``spread_daily`` header of ``db/schema.sql``, ``quant/engine.py::build_frame`` and
``quant/units.py::conversion_factor``). It is reimplemented here and the sibling project is never
imported. Using the same formula means a level here can be read against the research app's
statistics of the same spread (z-score, percentile, the day's move in sigma)::

    level = sum_i weight_i x price_i converted to the spread's unit  +  constant

- ``price_i`` is the leg's price as quoted x its root's ``price_scale`` (``config/contracts.csv``):
  the research app's quote-unit price, so RBOB quoted in cents per gallon enters as USD/gal.
- *Converted*: the quantity conversion comes first. It is 1 / the template leg's ``qty_factor``
  when the template gives one (7.45 bbl per t of gasoil). Otherwise it is
  1 / ``quantity_factor(leg quote quantity -> spread quantity)``, contract-master's unit table
  (gal -> bbl, g -> oz, lb -> t). No number per commodity is written here. Then, only when the
  leg's currency is not the currency of the spread's unit, the leg is multiplied by
  (USD per leg currency) / (USD per unit currency).
- ``constant`` is the template's, in its unit (0 for all but one template).
- **A calendar** is near - far (weights +1 / -1, the research app's convention, so
  backwardation is positive), in the root's quote unit (after ``price_scale``, like the research
  app's calendar rows). It needs no conversion.

Which prices each level reads (``book.py`` gathers them; ``level_legs`` shows them per leg):

- ``level_now``: each leg's ``mark`` on the as-of ``value_book`` row, the price its P&L reads.
  That is the official FUTURE_PX, or the near-marks estimate of hard rule 2 whose row source
  starts ``INTERP:``, or a close the screen's filled reader carried. A cross-currency leg is
  converted at that row's own ``spot``, the conversion its USD P&L used. The unit's own
  currency, when it is not USD, is converted at ``engine.pnl.valuation.usd_per_quote`` of the
  same date, the valuation's own rule.
- ``level_prev``: the same, read from the frame the spread's Daily P&L is measured from: the
  previous business day's close, stepped back and filled exactly as the header's Daily. A leg put
  on after that close has no row there. It reads the exact official FUTURE_PX and SPOT of that
  close from ``marks_official`` instead, and its source says so.
- ``level_entry``: the legs' fills, each leg's size-weighted average fill (sum lots x fill / sum
  lots, the cost basis its P&L is measured from). A cross-currency leg converts at the exact
  official SPOT of its trade date. When that spot is not on file the entry is n/a, with the
  reason.

The FX may not match the research app's. The research app converts CNY through USDCNH unless
a template names USDCNY. The book's own conversion is the valuation's USD spot of the currency
(``usd_per_quote``: USD<ccy>, else <ccy>USD, whichever is on file). The level therefore agrees
with the P&L, and it can differ from the research app's level by the CNH-CNY basis.

**The research key** (``research_key``) is the research app's ``spread_id`` and ``instance`` for
this spread. For a template both are its template id and ''. For a calendar the id is
``cal.<exchange>_<code>.<near month code>_<far month code>`` in lower case, and the instance is
the near contract's four-digit year (``rvapp/universe/loader.py``). The far contract must fall
in the instance year, or in the next year when its month code comes before the near one's
(``far_year_offset``). A calendar two years wide has no key. This lane never reads the research
database: risk-history looks the key up there, and a key the research app does not carry (its
calendar rules list only some month pairs) is risk-history's to report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from data.contracts import ContractRoot
from engine.spreads.grouping import CALENDAR, Leg, Shape
from engine.spreads.templates import Template, lot_in_quote_units

_MONTH_CODES = "FGHJKMNQUVXZ"


@dataclass(frozen=True)
class LevelLeg:
    instrument_id: str
    root_id: str
    weight: float
    currency: str               # the leg's quote currency
    price_scale: float          # quoted price x this = price in the root's quote unit
    qty_conv: float             # price per quote quantity -> price per spread quantity
    qty_factor: Optional[float] # the template's own qty_factor, None when it gives none
    lot_units: float            # one lot in the root's quote quantity (1,000 bbl of WTI)
    trade_ids: Tuple[str, ...]
    month_key: str              # '2026-12'


@dataclass(frozen=True)
class LevelSpec:
    kind: str                   # 'calendar' or the template id
    unit: str                   # the level's unit, 'USD/bbl'
    currency: str               # the unit's currency, 'USD'
    constant: float
    legs: Tuple[LevelLeg, ...]  # in the shape's order (a calendar: near, far)
    weights: Tuple[float, ...]
    units_per_lot: Tuple[float, ...]

    @property
    def is_calendar(self) -> bool:
        return self.kind == CALENDAR

    def needs_fx(self, leg: LevelLeg) -> bool:
        return leg.currency != self.currency


def spec_for(shape: Shape, legs: Sequence[Leg], roots: Dict[str, ContractRoot],
             templates: Dict[str, Template]) -> Tuple[Optional[LevelSpec], str]:
    """(spec, '') for a calendar or a template match, or (None, why) when a leg's root or the
    template cannot be read."""
    out = []
    template = None if shape.kind == CALENDAR else templates.get(shape.kind)
    if shape.kind != CALENDAR and template is None:
        return None, f"template {shape.kind} is no longer in config/spreads/, so the spread has no level"
    for n, leg in enumerate(legs):
        root = roots.get(leg.root_id)
        if root is None:
            return None, f"{leg.root_id} is not in config/contracts.csv, so the spread has no level"
        lot_units = lot_in_quote_units(root)
        if template is None:
            qty_conv, qf = 1.0, None
        else:
            tleg = template.legs[n]
            qf = tleg.qty_factor
            # units_per_lot = lot in quote quantity x (qty_factor, else quote -> spread quantity):
            # its inverse per quote unit is the research app's 1 / qty_factor (or 1 / quantity_factor)
            qty_conv = lot_units / float(shape.units_per_lot[n])
        out.append(LevelLeg(leg.instrument_id, leg.root_id, float(shape.weights[n]), root.currency,
                            float(root.price_scale), qty_conv, qf, lot_units, tuple(leg.trade_ids),
                            leg.month_key))
    if template is None:
        unit = shape.unit
        currency = unit.partition("/")[0].strip().upper() or out[0].currency
        constant = 0.0
    else:
        unit, currency, constant = template.unit, template.currency, float(template.constant)
    return LevelSpec(shape.kind, unit, currency, constant, tuple(out), tuple(shape.weights),
                     tuple(shape.units_per_lot)), ""


def spec_to_dict(spec: Optional[LevelSpec]) -> Optional[dict]:
    """The spec as plain JSON-safe data (a screen may keep a position in a dcc.Store), or None."""
    if spec is None:
        return None
    return {
        "kind": spec.kind, "unit": spec.unit, "currency": spec.currency, "constant": spec.constant,
        "weights": list(spec.weights), "units_per_lot": list(spec.units_per_lot),
        "legs": [{"instrument_id": leg.instrument_id, "root_id": leg.root_id, "weight": leg.weight,
                  "currency": leg.currency, "price_scale": leg.price_scale, "qty_conv": leg.qty_conv,
                  "qty_factor": leg.qty_factor, "lot_units": leg.lot_units, "trade_ids": list(leg.trade_ids),
                  "month_key": leg.month_key} for leg in spec.legs],
    }


def spec_from_dict(data: Optional[dict]) -> Optional[LevelSpec]:
    """``spec_to_dict``'s inverse; None for None."""
    if not data:
        return None
    legs = tuple(LevelLeg(str(x["instrument_id"]), str(x["root_id"]), float(x["weight"]), str(x["currency"]),
                          float(x["price_scale"]), float(x["qty_conv"]),
                          None if x.get("qty_factor") is None else float(x["qty_factor"]),
                          float(x["lot_units"]), tuple(x.get("trade_ids") or ()), str(x.get("month_key") or ""))
                 for x in data["legs"])
    return LevelSpec(str(data["kind"]), str(data["unit"]), str(data["currency"]), float(data["constant"]), legs,
                     tuple(float(w) for w in data["weights"]), tuple(float(u) for u in data["units_per_lot"]))


def converted(price: float, leg: LevelLeg, spec: LevelSpec, s_leg: Optional[float] = None,
              s_unit: Optional[float] = None) -> float:
    """One leg's quoted price in the spread's unit (the research app's ``conversion_factor``).
    ``s_leg`` / ``s_unit``: USD per unit of the leg's and the unit's currency, read only when the
    two currencies differ."""
    value = price * leg.price_scale * leg.qty_conv
    if spec.needs_fx(leg):
        value = value * float(s_leg) / float(s_unit)
    return value


def level(converted_prices: Sequence[float], spec: LevelSpec) -> float:
    """sum weight x converted price + constant."""
    return float(sum(w * p for w, p in zip(spec.weights, converted_prices)) + spec.constant)


def usd_per_level_unit(open_size: float, spec: LevelSpec, s_unit: float) -> float:
    """USD P&L of a 1.0 move of the level for ``open_size`` of the spread (signed, + = long, so a
    rise is a gain): a template's size is in its quantity unit, so ``size x USD per unit
    currency``; a calendar's size is in lots, so ``lots x one lot in quote quantity x USD per
    currency`` (10 CL lots: 10 x 1,000 bbl x 1 = USD 10,000 per USD 1/bbl). The identity behind
    it: leg lots = size x weight / units per lot, so each leg's P&L for its share of a 1.0 move
    adds up to exactly this, whatever the legs' units."""
    if spec.is_calendar:
        return open_size * spec.legs[0].lot_units * s_unit
    return open_size * s_unit


def research_key(spec: Optional[LevelSpec]) -> Tuple[str, str, str]:
    """(research_id, research_instance, reason): the research app's spread_id and instance of this
    spread, or ('', '', why)."""
    if spec is None:
        return "", "", "no calendar or template fits this group's legs, so it has no research spread"
    if not spec.is_calendar:
        return spec.kind, "", ""
    near, far = spec.legs[0], spec.legs[1]
    try:
        # a month key is '2026-12'; a leg whose month contract-master could not say carries its
        # expiry date instead, which is not its contract month
        if len(near.month_key.split("-")) != 2 or len(far.month_key.split("-")) != 2:
            raise ValueError(near.month_key)
        ny, nm = (int(x) for x in near.month_key.split("-"))
        fy, fm = (int(x) for x in far.month_key.split("-"))
    except ValueError:
        return "", "", (f"the contract month of {near.instrument_id} or {far.instrument_id} is not known, "
                        f"so the research calendar cannot be named")
    exchange, _, code = near.root_id.partition(":")
    offset = 0 if fm > nm else 1
    if fy != ny + offset:
        return "", "", (f"{near.instrument_id} against {far.instrument_id} is wider than the research app's "
                        f"calendars (the far month in the near month's year, or the next year when it comes "
                        f"first): no research spread")
    sid = f"cal.{exchange.lower()}_{code.lower()}.{_MONTH_CODES[nm - 1].lower()}_{_MONTH_CODES[fm - 1].lower()}"
    return sid, f"{ny:04d}", ""
