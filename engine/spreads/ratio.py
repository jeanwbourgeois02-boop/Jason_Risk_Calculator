"""The price ratio of a two-leg spread (user, 2026-10-01: "price ratio yeah - in the same
currency - always usd - with the chinese [leg] as numerator").

A display and context figure beside the spread's level: never in P&L, delta, a total or
``marks``. Written once, here, so the Book's figure today and risk-metrics' daily history of the
same ratio can never disagree on the definition:

* **Two-leg spreads only.** A spread of three or more legs (a 3-2-1 crack, CATTLE's feeder / live /
  feeder) has no ratio, said so. Hedges are never a leg of it; options and FX trades neither (a
  ratio of two futures or LME prices).
* **Numerator:** the leg on a mainland Chinese exchange (contract-master's ``country`` 'CN': SHFE,
  DCE, ZCE, INE, GFEX) when exactly one leg is. Otherwise the leg the spread's level spec puts
  first: the near month on a calendar, the first-named leg on a cross (``strategies._order``: a
  template's own order, else the long leg).
* **Both prices in USD:** each leg's quoted price x its root's ``price_scale`` (cents become
  dollars) x the USD per unit of its quote currency.
* **One physical unit** where the two legs are one commodity (contract-master's ``subsector``) and
  their quote quantities are of one dimension of contract-master's unit table (mass, volume,
  energy): both in the dimension's common unit when either leg already quotes in it (t, bbl,
  mmbtu), else in the denominator's unit (COMEX copper USD/lb against LME copper USD/t: both USD/t;
  SHFE gold CNY/g against COMEX gold USD/oz: both USD/oz). Two commodities: each leg in USD per its
  own quote unit. The factor is ``data.contracts.universe.quantity_factor``, never a number here.
* **At entry:** each leg's average entry (the open lots' average cost, ``strategies._entry_value``,
  the leg row's ``avg_fill``) at the last official USD spot on or before the leg's first open fill
  (``valuation.usd_per_quote_on_or_before``: exact official rows only, no estimate).
* **Now:** each leg's mark on the as-of date at the spot its own ``value_book`` row used (the spot
  its P&L is converted at).
* A missing price or spot leaves the ratio None with its reason (hard rule 2: never another
  source). A mark or spot that is itself the near-marks estimate (source ``INTERP:``) gives a
  figure, said in ``ratio_estimate_note``.

``ratio_spec`` (a plain, JSON-able dict, carried on each spread as ``ratio_spec``) is everything the
history needs to rebuild the ratio from ``price_history``; ``ratio_value`` is the one formula, for a
pair of floats or of pandas Series alike.

**The USD spread** (user, 2026-10-01: the spread at entry and now, "always usd") is the same two
USD prices' difference, numerator - denominator in USD per the shared unit (``spread_value``,
through the same ``usd_price`` as the ratio): a calendar near - far, a China-against-West pair
China - foreign. Two legs in two units (two commodities quoted per different quantities) have
none, said so (``spread_unit``).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from data.contracts.universe import quantity_factor
from engine.pnl.valuation import usd_per_quote, usd_per_quote_on_or_before
from engine.spreads.levels import spec_from_dict

CHINA = "CN"
RATIO_PRODUCTS = ("FUTURE", "LME_FWD")
COMMON_UNIT = ("t", "bbl", "mmbtu")      # one per dimension of contract-master's unit table
REASON_LEGS = "Ratio is shown for two-leg spreads only"
REASON_SPREAD_LEGS = "The USD spread is shown for two-leg spreads only"
_EPS = 1e-12
_HUNDREDTHS = {"USD": "cents to dollars", "GBP": "pence to pounds", "EUR": "cents to euros"}
_UNIT_TEXT = {"mwh": "MWh", "mmbtu": "MMBtu", "gj": "GJ"}


def _unit_text(unit: str) -> str:
    """A unit as written on screen ('mwh' -> 'MWh'); the spec keeps contract-master's key."""
    return _UNIT_TEXT.get(unit, unit)


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _is_estimate(source) -> bool:
    return str(source or "").startswith("INTERP")


def _qty_unit(root) -> str:
    """The quantity of a root's quote unit: 'USD/lb' -> 'lb'."""
    text = str(getattr(root, "quote_unit", "") or "")
    return text.split("/", 1)[1].strip() if "/" in text else ""


def _ccy_of(root, row: dict) -> str:
    return str(row.get("currency") or getattr(root, "currency", "") or "USD")


def _dimension_unit(a: str, b: str, den: str) -> Optional[str]:
    """The unit both legs are put in when their quote quantities convert (None when they do not)."""
    try:
        quantity_factor(a, b)
    except ValueError:
        return None
    for unit in COMMON_UNIT:
        if unit in (a, b):
            return unit
    return den


def blank(reason: str, spread_reason: Optional[str] = None) -> dict:
    """The ratio and USD spread keys with no figure, and why (``spread_reason`` for the USD spread
    when it differs from the ratio's)."""
    return {"ratio_entry": None, "ratio_now": None, "ratio_basis": "", "ratio_reason": reason,
            "ratio_entry_reason": reason, "ratio_now_reason": reason, "ratio_estimate_note": "",
            "ratio_spec": None, "spread_usd_entry": None, "spread_usd_now": None, "spread_usd_unit": None,
            "spread_usd_reason": reason if spread_reason is None else spread_reason}


def usd_price(side: dict, price, usd=1.0):
    """One leg's QUOTED price in USD per its ``ratio_spec`` side's unit: price x factor x USD per
    unit of its currency. Floats or pandas Series alike. Both ``ratio_value`` and ``spread_value``
    read the legs through it, so the two can never disagree on a leg's USD price."""
    return price * float(side["factor"]) * usd


def spread_unit(spec: dict) -> Tuple[Optional[str], str]:
    """(the USD spread's unit, 'USD/t', or None, why): None when the two legs are in two units
    (two commodities quoted per different quantities), since a difference across units means
    nothing."""
    n, d = spec["numerator"], spec["denominator"]
    if n["unit"] != d["unit"]:
        return None, (f"{n['name']} is per {_unit_text(n['unit'])} and {d['name']} per {_unit_text(d['unit'])}: "
                      f"a difference across two units means nothing, so only the ratio is shown")
    return f"USD/{_unit_text(n['unit'])}", ""


def spread_value(spec: dict, num_price, den_price, num_usd=1.0, den_usd=1.0):
    """The spread in USD per unit from each leg's QUOTED price and the USD per unit of its
    currency: numerator - denominator, each through ``usd_price`` (the same USD prices as
    ``ratio_value``). Floats or pandas Series alike. Only for a spec whose ``spread_unit`` is not
    None (the caller checks)."""
    return usd_price(spec["numerator"], num_price, num_usd) - usd_price(spec["denominator"], den_price, den_usd)


def ratio_value(spec: dict, num_price, den_price, num_usd=1.0, den_usd=1.0):
    """The ratio from each leg's QUOTED price (Bloomberg's units, the marks' and the fills') and
    the USD per unit of each leg's currency (1.0 for USD): (num x factor x USD) / (den x factor x
    USD). Floats or pandas Series alike (a Series divides date by date). The one formula: the
    Book's entry and today figures and the history all go through it."""
    return usd_price(spec["numerator"], num_price, num_usd) / usd_price(spec["denominator"], den_price, den_usd)


def _order(legs: List[Tuple[dict, object]], level: Optional[dict]) -> Tuple[int, str]:
    """(index of the numerator in ``legs``, the rule used)."""
    cn = [i for i, (_r, root) in enumerate(legs) if str(getattr(root, "country", "") or "") == CHINA]
    if len(cn) == 1:
        return cn[0], "the leg on a Chinese exchange on top"
    spec = spec_from_dict((level or {}).get("spec")) if (level or {}).get("spec") else None
    if spec is not None and len(spec.legs) == 2:
        first = spec.legs[0]
        for i, (r, _root) in enumerate(legs):
            if r.get("instrument_id") == first.instrument_id and str(r.get("month") or "")[:7] == first.month_key[:7]:
                return i, "the level's first leg on top"
    (ra, root_a), (rb, root_b) = legs
    if getattr(root_a, "root_id", "a") == getattr(root_b, "root_id", "b"):
        near = 0 if (str(ra.get("prompt") or ra.get("month") or ""), ra.get("contract_id", "")) <= \
            (str(rb.get("prompt") or rb.get("month") or ""), rb.get("contract_id", "")) else 1
        return near, "the near month on top"
    longer = 0 if (_num(ra.get("lots")) or 0.0) > 0 else 1
    return longer, "the long leg on top"


def ratio_spec(legs: Sequence[Tuple[dict, object]], level: Optional[dict] = None
               ) -> Tuple[Optional[dict], str]:
    """(the ratio's definition, why None) for a spread's two legs: ``legs`` = [(leg row, its
    ContractRoot)], the leg row a ``trade_book`` leg (contract_id, instrument_id, root_id, name,
    month, prompt, currency, lots); ``level`` the spread's level dict (its ``spec`` orders a pair
    with no Chinese leg). The dict, per side ``numerator`` / ``denominator``:
    {contract_id, instrument_id, root_id, name, month, prompt, currency, usd_pair (the pair the
    valuation converts that currency through, '' for USD), quote_unit, price_scale, unit (the
    quantity the price is put in), unit_factor (quote quantities per one of ``unit``), factor
    (price_scale x unit_factor: quoted price -> currency per ``unit``)}; plus ``unit``
    ('USD/t', or 'USD/lb ÷ USD/t' across two units), ``basis`` (the sentence), ``order`` (why the
    numerator is on top). History: per date, each leg's quoted close (``price_history``, an LME
    leg read at its prompt month as the level history reads it) with its currency's USD per unit,
    through ``ratio_value``."""
    if len(legs) != 2:
        return None, REASON_LEGS
    for r, root in legs:
        if root is None:
            return None, f"{r.get('name') or r.get('contract_id')}: not in the contract list, so no ratio"
        if str(r.get("product") or "FUTURE") not in RATIO_PRODUCTS:
            return None, f"{r.get('name') or r.get('contract_id')}: a ratio is of two futures or LME prices"
    top, why = _order(list(legs), level)
    (rn, root_n), (rd, root_d) = legs[top], legs[1 - top]
    qn, qd = _qty_unit(root_n), _qty_unit(root_d)
    if not qn or not qd:
        return None, "a leg's quote unit is not known, so its price cannot be put per unit"
    same = str(root_n.subsector or "") == str(root_d.subsector or "") and bool(root_n.subsector)
    unit = _dimension_unit(qn, qd, qd) if same else None
    sides = {}
    for key, r, root, q in (("numerator", rn, root_n, qn), ("denominator", rd, root_d, qd)):
        to = unit or q
        uf = quantity_factor(to, q)            # quote quantities per one ``to``: lb per t
        ccy = _ccy_of(root, r)
        sides[key] = {
            "contract_id": str(r.get("contract_id") or ""), "instrument_id": str(r.get("instrument_id") or ""),
            "root_id": root.root_id, "name": str(r.get("name") or r.get("contract_id") or ""),
            "month": str(r.get("month") or ""), "prompt": str(r.get("prompt") or ""), "currency": ccy,
            "usd_pair": "" if ccy == "USD" else f"USD{ccy}", "quote_unit": root.quote_unit,
            "price_scale": float(root.price_scale), "unit": to, "unit_factor": float(uf),
            "factor": float(root.price_scale) * float(uf),
        }
    n, d = sides["numerator"], sides["denominator"]
    un, ud = _unit_text(n["unit"]), _unit_text(d["unit"])
    text_unit = f"USD/{un}" if un == ud else f"USD/{un} ÷ USD/{ud}"
    notes = []
    for s in (n, d):
        bits = []
        if s["price_scale"] != 1.0:
            hundredths = _HUNDREDTHS.get(s["quote_unit"].split("/")[0]) if abs(s["price_scale"] - 0.01) < 1e-12 else None
            bits.append(hundredths or f"quoted price x {s['price_scale']:g}")
        if s["unit_factor"] != 1.0:
            bits.append(f"{s['unit_factor']:,.6g} {_unit_text(_qty_from(s['quote_unit']))} per {_unit_text(s['unit'])}")
        if s["currency"] != "USD":
            bits.append(f"{s['currency']} at the USD spot")
        if bits:
            notes.append(f"{s['name']}: {', '.join(bits)}")
    basis = f"{n['name']} in USD/{un} ÷ {d['name']} in USD/{ud}"
    if notes:
        basis += " (" + "; ".join(notes) + ")"
    if not same:
        basis += "; two commodities, so each in USD per its own unit"
    return {"numerator": n, "denominator": d, "unit": text_unit, "basis": basis, "order": why}, ""


def _qty_from(quote_unit: str) -> str:
    return quote_unit.split("/", 1)[1] if "/" in quote_unit else quote_unit


def _first_fill(book, row: dict) -> str:
    ids = list(row.get("open_trade_ids") or [])
    days = [str(book.by_id[t]["trade_date"])[:10] for t in ids if t in book.by_id]
    return min(days) if days else ""


def _now_spot(book, row: dict, ccy: str) -> Tuple[Optional[float], str, str]:
    """(USD per unit of ``ccy`` the leg's own value_book row used on the as-of, its source, why None)."""
    if ccy == "USD":
        return 1.0, "identity", ""
    for t in row.get("open_trade_ids") or []:
        r = book.today.get(t) or {}
        if _num(r.get("mark")) is not None:
            s = _num(r.get("spot"))
            if s:
                return s, str(r.get("spot_source") or ""), ""
            return None, "", f"{row.get('name')}: no USD spot for {ccy} on its {book.as_of} valuation"
    return None, "", f"{row.get('name')}: no price on {book.as_of}"


def _entry_spot(book, ccy: str, day: str) -> Tuple[Optional[float], str]:
    if ccy == "USD":
        return 1.0, ""
    if not day:
        return None, "no open fill to date the entry"
    try:
        s, _pair, _src, _when = usd_per_quote_on_or_before(book.conn, ccy, day)
    except Exception as exc:  # noqa: BLE001 -- a stored spot that is not a number: named, not raised
        return None, f"the {ccy} USD spot on or before {day} could not be read ({exc})"
    s = _num(s)
    return (s, "") if s else (None, f"no official {ccy} USD spot on or before {day}, the first fill")


def _pair_used(book, ccy: str) -> str:
    """The pair the valuation converts ``ccy`` through on the as-of (USD<ccy> first, as it tries)."""
    if ccy == "USD":
        return ""
    try:
        _s, pair, _src = usd_per_quote(book.conn, ccy, book.as_of)
    except Exception:  # noqa: BLE001 -- only a label: the default form stands
        pair = None
    return str(pair or f"USD{ccy}")


def spread_ratio(book, legs: Sequence[dict], rows_by_cid: Dict[str, dict], kind: str = "",
                 level: Optional[dict] = None) -> dict:
    """The ratio keys of one spread (``trade_book``'s ``sub_spreads`` entry): ``legs`` its legs
    ({contract_id, lots}), ``rows_by_cid`` the trade's leg rows, ``kind`` its type, ``level`` its
    level dict. {ratio_entry, ratio_now, ratio_basis, ratio_reason (why there is no ratio at all,
    or why one of the two is missing, '' when both are there), ratio_entry_reason,
    ratio_now_reason, ratio_estimate_note, ratio_spec}. Display only."""
    if kind == "UNMATCHED":
        return blank("Unmatched legs: no spread, so no ratio", "Unmatched legs: no spread, so no USD spread")
    rows = []
    for leg in legs:
        r = rows_by_cid.get(leg.get("contract_id"))
        if r is None or r.get("hedge"):
            continue
        rows.append({**r, "lots": leg.get("lots", r.get("lots"))})
    if len(rows) != 2:
        if len(rows) > 2:
            return blank(REASON_LEGS, REASON_SPREAD_LEGS)
        lead = "An outright" if kind == "OUTRIGHT" else "One leg open, the other side flat"
        return blank(f"{lead}: {REASON_LEGS[0].lower()}{REASON_LEGS[1:]}",
                     f"{lead}: {REASON_SPREAD_LEGS[0].lower()}{REASON_SPREAD_LEGS[1:]}")
    pairs = [(r, book.roots.get(r.get("root_id"))) for r in rows]
    spec, why = ratio_spec(pairs, level)
    if spec is None:
        return blank(why[:1].upper() + why[1:])
    if spec["numerator"]["contract_id"] == rows[0]["contract_id"]:
        rn, rd = rows
    else:
        rd, rn = rows
    n, d = spec["numerator"], spec["denominator"]
    for side in (n, d):
        side["usd_pair"] = _pair_used(book, side["currency"])
    out = {"ratio_basis": spec["basis"], "ratio_spec": spec, "ratio_estimate_note": ""}
    s_unit, s_unit_why = spread_unit(spec)
    out["spread_usd_unit"] = s_unit
    # at entry: the open lots' average entry at the spot of the leg's first open fill
    entry_why = []
    px, fx = [], []
    for r, side in ((rn, n), (rd, d)):
        p = _num(r.get("avg_fill"))
        if p is None:
            entry_why.append(f"{side['name']}: no entry price ({r.get('avg_fill_reason') or 'no open lots'})")
            continue
        s, why_s = _entry_spot(book, side["currency"], _first_fill(book, r))
        if s is None:
            entry_why.append(f"{side['name']}: {why_s}")
            continue
        px.append(p)
        fx.append(s)
    out["spread_usd_entry"] = (spread_value(spec, px[0], px[1], fx[0], fx[1])
                               if not entry_why and s_unit is not None else None)
    if entry_why:
        out.update(ratio_entry=None, ratio_entry_reason="; ".join(entry_why))
    elif abs(px[1] * d["factor"] * fx[1]) < _EPS:
        out.update(ratio_entry=None, ratio_entry_reason=f"{d['name']}: entry price 0")
    else:
        out.update(ratio_entry=ratio_value(spec, px[0], px[1], fx[0], fx[1]), ratio_entry_reason="")
    # now: each leg's mark at the spot its own valuation used
    now_why, est = [], []
    px, fx = [], []
    for r, side in ((rn, n), (rd, d)):
        m = _num(r.get("mark"))
        if m is None:
            now_why.append(f"{side['name']}: no price on {book.as_of} ({r.get('mark_reason') or 'not valued'})")
            continue
        s, src, why_s = _now_spot(book, r, side["currency"])
        if s is None:
            now_why.append(why_s)
            continue
        if _is_estimate(r.get("mark_source")):
            est.append(f"{side['name']} price estimated ({r.get('mark_source')})")
        if _is_estimate(src):
            est.append(f"{side['currency']} spot estimated ({src})")
        px.append(m)
        fx.append(s)
    out["spread_usd_now"] = (spread_value(spec, px[0], px[1], fx[0], fx[1])
                             if not now_why and s_unit is not None else None)
    if now_why:
        out.update(ratio_now=None, ratio_now_reason="; ".join(now_why))
    elif abs(px[1] * d["factor"] * fx[1]) < _EPS:
        out.update(ratio_now=None, ratio_now_reason=f"{d['name']}: price 0")
    else:
        out.update(ratio_now=ratio_value(spec, px[0], px[1], fx[0], fx[1]), ratio_now_reason="",
                   ratio_estimate_note="; ".join(est))
    out["ratio_reason"] = "; ".join(w for w in (out["ratio_entry_reason"] and f"Entry: {out['ratio_entry_reason']}",
                                                out["ratio_now_reason"] and f"Now: {out['ratio_now_reason']}") if w)
    if s_unit is None:
        out["spread_usd_reason"] = s_unit_why
    else:
        # the same missing inputs as the ratio (a 0 denominator only stops the ratio)
        out["spread_usd_reason"] = "; ".join(w for w in (
            entry_why and "Entry: " + "; ".join(entry_why), now_why and "Now: " + "; ".join(now_why)) if w)
    return out


def trade_ratio(subs: Sequence[dict], name: str) -> dict:
    """The trade's own ratio keys: its one spread's when it holds exactly one, else blank with why."""
    spreads = [s for s in subs if s.get("type") not in ("OUTRIGHT", "UNMATCHED")]
    if len(subs) == 1 and len(spreads) == 1:
        return {k: spreads[0].get(k) for k in blank("")}
    if not spreads:
        return blank("No spread open, so no ratio", "No spread open, so no USD spread")
    if len(spreads) == 1:
        return blank(f"{name} holds a spread and legs outside it: the spread's ratio is on its own row",
                     f"{name} holds a spread and legs outside it: the spread's USD spread is on its own row")
    return blank(f"{name} holds {len(spreads)} spreads: each has its own ratio",
                 f"{name} holds {len(spreads)} spreads: each has its own USD spread")
