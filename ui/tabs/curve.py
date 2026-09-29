"""Exposure tab (the Curve tab until 2026-09-28): "Where is my risk, and is each spread actually as
hedged as I think?" The Book shows what is held (lots as held, fills, marks, P&L); Exposure shows
the risk shape, delta by contract month. Layout rebuilt on 2026-09-29 (user-approved brief).
Everything shown is `engine.curve.curve_positions`' output (with the spreads engine's strategies
passed in, for the leftover), the official GAMMA / THETA / VEGA marks on file,
`engine.ladder.positions.book_positions` and the spreads engine's hedges and coverage, as given:
nothing here prices a trade, takes a delta, converts a unit or a currency (CLAUDE.md "Tabs as
views"). Summing the engine's figures of one unit into a subtotal is display arithmetic, and a
subtotal that leaves a figure out says "excl. N".

Top to bottom:
  1. The title line: "Exposure" (definitions on hover), the switch Physical | Lots | USD (Physical
     the default, kept for the session) and Download CSV (the grid as shown, full figures).
  2. Tiles: gross and net delta USD (the commodities' roots summed, "excl. N"), the largest net
     commodity in its physical unit, and the CNH hedge coverage when the book has China legs (the
     spreads engine's `hedge_coverage_net`; several strategies: one line each on hover).
  3. Sector tilt: one small bar per sector (Energy, Metals, Agriculture, Ferrous), right for net
     long, left for net short, by the sector's net delta USD.
  4. The grid: one bold row per commodity (`by_subsector`, the fixed sector order, the FX hedges
     last), its exchange rows (`split`) indented under it when it trades on more than one; one
     column per month held up to 12 months after the as-of, then "Later", Net, Gross and
     Leftover (the part of the net in no spread, in the commodity's physical unit in every view,
     amber when not zero). Physical: the engine's `months_units`; Lots: the exchange rows' delta
     lots (the commodity row shows no month cells: lots do not add across exchanges); USD: the
     engine's delta USD. A Book line of gross and net delta USD. A commodity row opens a panel
     under the grid on click: each exchange's futures curve on the as-of (official marks, the
     previous close dashed) with the book's positions as bars.
  5. Two cards: Currency (the non-USD legs' delta USD, the FX hedge against them, unhedged and
     coverage; then the FX forwards and options by currency with the Net USD line, and the P&L
     the non-USD futures hold in their currency) and Option Greeks (only when an option is held).
  6. One Data issues drawer.

The tab has no date picker and says no as-of of its own: it follows the header's as-of store and
re-renders in place on the data revision and its safety interval. `layout(default_date)` (alias
`build_layout`) and `register_callbacks(app, get_db_path)` are the shell's interface.
"""
from __future__ import annotations

import bisect
import calendar
import logging
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dcc, html

from engine.curve import curve_positions
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs.formatting import (
    MINUS, MISSING, about, contract_name, full_money, issues_drawer, lme_name, marker, missing_cell, money_cell,
    parse_contract_id, plain_words, quoted_unit, short_date, short_money, short_root_name, sign_class, signed_money, size_words, sum_known,
    unit_suffix,
)
from ui.tabs.header import AS_OF_STORE_ID

FX_SECTOR = "fx"
FX_GROUP = "FX hedges"
_SECTOR_ORDER = ("energy", "metals", "agriculture", "ferrous")
_SECTOR_WORDS = {"energy": "Energy", "metals": "Metals", "agriculture": "Agriculture", "ferrous": "Ferrous"}
CHINA_CCYS = ("CNY", "CNH")

log = logging.getLogger(__name__)

BODY_ID = "curve-body"
REFRESH_ID = "curve-refresh"
UNIT_ID = "curve-unit"
GRID_ID = "curve-grid"
TILES_ID = "curve-tiles"
TILT_ID = "curve-tilt"
GREEKS_ID = "curve-greeks"
FX_EXPOSURE_ID = "curve-fx-exposure"
CSV_BUTTON_ID = "curve-csv"
DOWNLOAD_ID = "curve-download"
ISSUES_ID = "curve-issues"
PANEL_ID = "curve-chart"              # the row-click panel under the grid (inside the body)
SELECTED_ID = "curve-chart-data"      # the commodity chosen for the panel (a static store)
ROW_TYPE = "curve-row"                # a commodity row's pattern id: {"type": ROW_TYPE, "idx": subsector}

NA = MISSING
FUTURE, OPTION, LME = "FUTURE", "CMDTY_OPTION", "LME_FWD"
UNITS = ("physical", "delta_lots", "delta_usd")
UNIT_LABELS = {"physical": "Physical", "delta_lots": "Lots", "delta_usd": "USD"}
UNIT_TIPS = {"physical": "Delta in each commodity's one physical unit (bbl, t, bu, oz), the exchanges added where their "
                         "units convert: a future as booked, an option at its official delta, a monthly-average "
                         "contract by the pricing days left, an LME ticket at its tonnes.",
             "delta_lots": "Delta in futures-equivalent lots per exchange: a future as booked, an option at its official "
                           "delta, a monthly-average contract by the pricing days left, an LME ticket at its tonnes over "
                           "the lot size. Lots do not add across exchanges, so a commodity row carries no month cells.",
             "delta_usd": "The same delta in USD: delta lots × multiplier × the future's official price × spot, the "
                          "engine's figure (k / m, the full figure on hover)."}
DEFAULT_UNIT = "physical"
TAB_ABOUT = ("Where is my risk, and is each spread actually as hedged as I think? Delta by contract month, every product "
             "at its delta, one row per commodity across exchanges. Leftover is the part of a commodity's net that "
             "belongs to no spread: the relative-value leak. The Book tab shows what is held; this tab the risk shape.")
GRID_ABOUT = ("Each month cell is the net delta of that contract month in the view shown (the engine's figures). Green "
              "long, red short, deeper for a larger position by its rank among the cells. A dash is a figure the "
              "engine could not give, its reason on hover; an empty cell holds no position. Later sums the months more "
              "than 12 months out. Net and Gross end the row; Leftover is the part of the net in no spread, in the "
              "commodity's physical unit in every view. Click a commodity for its futures curve.")
TILT_ABOUT = "Each sector's net delta USD: a bar to the right is net long, to the left net short, scaled to the largest."
GREEKS_ABOUT = ("The open options on futures, one line per underlying commodity, scaled to the position: Delta is "
                "the delta lots (lots × the option's official delta per lot, futures-equivalent lots); "
                "Gamma, Theta and Vega are the option's official per-lot marks on file for the day × its lots, in the "
                "contract's currency, so a line adds up figures of one unit only. A Greek with no mark is a dash with "
                "its reason, never zero. An FX option pair shows its USD delta; its other Greeks are on "
                "the Trades tab, Options. Futures and LME prompts carry no gamma, theta or vega.")
FX_ABOUT = ("First the legs priced in a currency other than USD: their delta USD (the engine's, per exchange, added up), "
            "the FX hedge against them (the spreads engine's hedges: the USD notional of the USD/CNH position, "
            "short = minus, the SGX USD/CNH future included), unhedged (the two added: a long China leg is hedged by a "
            "short USD/CNH position) and the coverage the spreads engine gives. Then delta by currency at the day's "
            "official spot: FX forwards' and spot legs still to settle, FX options at their delta, an LME ticket's "
            "USD leg; the FX net USD delta in words (long USD / short USD) with the gross beside it; and the P&L "
            "the non-USD futures hold in their own currency, which is not currency delta.")
HEAT_POS = (26, 127, 75)       # --pos of ui/assets/style.css
HEAT_NEG = (192, 57, 43)       # --neg
HEAT_MIN, HEAT_MAX = 0.10, 0.60
HEAT_INK = "#1b2333"
LINES_ON_HOVER = 12
_NEG_STYLE = {"color": "var(--neg)"}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _product(row: Dict[str, Any]) -> str:
    return row.get("product") or FUTURE


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def month_label(key: str) -> str:
    """'2026-12' -> 'Dec 26'."""
    try:
        year, month = (int(p) for p in key.split("-"))
        return f"{calendar.month_abbr[month]} {year % 100:02d}"
    except (ValueError, IndexError):
        return key


def _row_month(row: Dict[str, Any]) -> Optional[str]:
    if row.get("year") is None or row.get("month") is None:
        return None
    return f"{int(row['year']):04d}-{int(row['month']):02d}"


def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the root id stands for its name
        return {}


def plain_name(row: Dict[str, Any], roots: Dict[str, Any]) -> str:
    """A contract row's plain name: 'WTI Dec26', 'WTI Dec26 75c', 'LME copper 10 Dec'."""
    root_id = str(row.get("root_id") or "")
    root = roots.get(root_id)
    cid = str(row.get("contract_id") or "")
    if _product(row) == LME:
        prompt = row.get("expiry") or (cid.split(" ")[-1] if " " in cid else None)
        return lme_name(root, root_id, prompt)
    return contract_name(cid, root, root_id)


def lots_text(v: Optional[float]) -> str:
    """'+10', '−3', '+5.2', '0': a delta in lots with its sign, a real minus, no stray decimals."""
    if v is None:
        return NA
    body = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    if float(body.replace(",", "") or 0) == 0:
        return "0"
    return (MINUS if v < 0 else "+") + body


def heat_level(v: float, magnitudes: List[float]) -> float:
    """The quantile of |v| among `magnitudes` (sorted): by rank, so one huge cell does not wash
    the rest out."""
    if not magnitudes:
        return 1.0
    return bisect.bisect_right(magnitudes, abs(v)) / len(magnitudes)


def heat_colour(v: float, magnitudes: List[float]) -> Optional[str]:
    """Green for long, red for short, the opacity by `heat_level`; None (white) at zero."""
    if not v:
        return None
    alpha = HEAT_MIN + (HEAT_MAX - HEAT_MIN) * heat_level(v, magnitudes)
    r, g, b = HEAT_POS if v > 0 else HEAT_NEG
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


# --------------------------------------------------------------------------- data issues
def issue_items(result: Dict[str, Any]) -> List[Any]:
    """The engine's `reasons`, once each, in its order: a reason that opens with a contract on
    the tab is a (contract, sentence) pair, anything else the sentence as it stands."""
    contracts = {r.get("contract_id") for r in (result.get("rows") or []) if r.get("contract_id")}
    items: List[Any] = []
    seen = set()
    for reason in result.get("reasons") or []:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        head, sep, rest = reason.partition(": ")
        items.append((head, rest) if sep and head in contracts else reason)
    return items


# --------------------------------------------------------------------------- the grid's figures
def _contract_line(r: Dict[str, Any], unit: str, roots: Dict[str, Any]) -> str:
    name = plain_name(r, roots)
    tag = " (option)" if _product(r) == OPTION else " (LME prompt)" if _product(r) == LME else ""
    dl = _num(r.get("delta_lots"))
    if dl is None:
        return f"{name}{tag}: no delta ({r.get('reason') or 'no reason given'})"
    s = f"{name}{tag}: {lots_text(dl)} delta lot(s)"
    lots, factor = _num(r.get("lots")), _num(r.get("delta_factor"))
    if lots is not None and factor is not None and factor != 1.0:
        s += f" = {lots:g} lot(s) × {factor:.4g}"
    if unit == "delta_usd":
        du = _num(r.get("delta_usd"))
        s += f", {full_money(du)}" if du is not None else f", no USD delta ({r.get('reason') or 'no reason given'})"
    if r.get("note") and _product(r) != OPTION:
        s += f" ({r['note']})"
    return s


def month_cells(result: Dict[str, Any], root_id: str, unit: str, roots: Dict[str, Any]) -> Dict[str, Tuple[Optional[float], str]]:
    """{month key: (value or None, hover)} of one contract root's month cells from its rows: delta
    USD is the rows' `delta_usd` summed per month (the engine's per-contract figures). A month
    holding a row with no figure is None with the rows' reasons."""
    rows = [r for r in (result.get("rows") or []) if r.get("root_id") == root_id]
    by_month: Dict[str, List[dict]] = {}
    for r in rows:
        key = _row_month(r)
        if key is not None:
            by_month.setdefault(key, []).append(r)
    field = "delta_usd" if unit == "delta_usd" else "delta_lots"
    out: Dict[str, Tuple[Optional[float], str]] = {}
    for key, mine in sorted(by_month.items()):
        lines = [_contract_line(r, unit, roots) for r in mine]
        missing = [r for r in mine if _num(r.get(field)) is None]
        if missing:
            gaps = [f"{plain_name(r, roots)}: {r.get('reason') or 'no delta'}" for r in missing]
            out[key] = (None, "; ".join(gaps))
            continue
        out[key] = (float(sum(float(r[field]) for r in mine)), "; ".join(lines))
    return out


def _contracts_hover(result: Dict[str, Any], root_ids: Sequence[str], roots: Dict[str, Any]) -> Dict[str, str]:
    """{month key: the contracts in that month, in words}: the hover of a month cell."""
    out: Dict[str, List[str]] = {}
    wanted = set(root_ids)
    for r in result.get("rows") or []:
        key = _row_month(r)
        if key is not None and r.get("root_id") in wanted:
            out.setdefault(key, []).append(_contract_line(r, "delta_lots", roots))
    return {k: "; ".join(v[:LINES_ON_HOVER]) + (f" (+{len(v) - LINES_ON_HOVER} more)" if len(v) > LINES_ON_HOVER else "")
            for k, v in out.items()}


def _cells(mapping: Optional[Dict[str, Any]], reason: str, hovers: Optional[Dict[str, str]] = None) -> Dict[str, Tuple[Optional[float], str]]:
    """{month: (value or None, hover)} from an engine month map: a None month carries `reason`."""
    out: Dict[str, Tuple[Optional[float], str]] = {}
    for k, v in (mapping or {}).items():
        f = _num(v)
        out[k] = (f, (hovers or {}).get(k, "")) if f is not None else (None, reason or "no figure for this month")
    return out


def _total(engine_value: Any, parts: Sequence[Tuple[Optional[float], str]] = (), reason: str = "") -> dict:
    """A Net / Gross / Leftover figure: the engine's own when it gives one; else the known parts
    (the exchanges' own engine figures) summed with how many were left out, when there are two or
    more parts and one is known (display, the header's rule); else None with the reason."""
    v = _num(engine_value)
    if v is not None:
        return {"v": v, "excl": 0, "why": ""}
    total, excl, reasons = sum_known(parts)
    why = "; ".join(dict.fromkeys(r for r in reasons if r)) or reason
    if total is None:
        return {"v": None, "excl": 0, "why": why or reason or "no figure"}
    return {"v": total, "excl": excl, "why": why}


def _abs_total(cells: Dict[str, Tuple[Optional[float], str]]) -> dict:
    """The month cells' absolute values summed (the Lots view's gross: display), 'excl. N' when a
    month has no figure."""
    total, excl, reasons = sum_known([(None if v is None else abs(v), h) for v, h in cells.values()])
    why = "; ".join(dict.fromkeys(reasons))
    if total is None:
        return {"v": None, "excl": 0, "why": why or "no figure"}
    return {"v": total, "excl": excl, "why": why}


def _root_units_months(result: Dict[str, Any], root_id: str, roots: Dict[str, Any]) -> Tuple[Dict[str, Tuple[Optional[float], str]], str, dict, dict]:
    """(cells, unit, net, gross) of one root in its own physical unit, the rows' `delta_units`
    summed per month (one root, one unit): the exchange rows of a commodity whose exchanges'
    units do not add (UK gas in therm against TTF in MWh)."""
    rows = [r for r in (result.get("rows") or []) if r.get("root_id") == root_id]
    unit = str(next((r.get("unit") for r in rows if r.get("unit")), "") or "")
    by_month: Dict[str, List[dict]] = {}
    for r in rows:
        key = _row_month(r)
        if key is not None:
            by_month.setdefault(key, []).append(r)
    cells: Dict[str, Tuple[Optional[float], str]] = {}
    for key, mine in sorted(by_month.items()):
        gaps = [f"{plain_name(r, roots)}: {r.get('reason') or 'no delta'}" for r in mine if _num(r.get("delta_units")) is None]
        cells[key] = (None, "; ".join(gaps)) if gaps else (
            float(sum(float(r["delta_units"]) for r in mine)), "; ".join(_contract_line(r, "delta_lots", roots) for r in mine))
    per_position = [(_num(r.get("delta_units")), f"{plain_name(r, roots)}: {r.get('reason') or 'no delta'}") for r in rows]
    known = all(v is not None for v, _h in per_position) and all(_row_month(r) is not None for r in rows)
    why = "; ".join(h for v, h in per_position if v is None) or "a position with no contract month"
    net = {"v": float(sum(v for v, _h in per_position)) if known else None, "excl": 0, "why": "" if known else why}
    gross = {"v": float(sum(abs(v) for v, _h in per_position)) if known else None, "excl": 0, "why": "" if known else why}
    return cells, unit, net, gross


def ordered_subsectors(result: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """[(subsector key, by_subsector entry)] in the fixed sector order (energy, metals, agriculture,
    ferrous, then any other sector), the SGX USD/CNH future last (its own line, 'FX hedges')."""
    subs = result.get("by_subsector") or {}
    order = list(_SECTOR_ORDER) + sorted({str(v.get("sector") or "") for v in subs.values()} - set(_SECTOR_ORDER) - {FX_SECTOR})
    out: List[Tuple[str, Dict[str, Any]]] = []
    for sector in order + [FX_SECTOR]:
        out += [(k, v) for k, v in subs.items() if str(v.get("sector") or "") == sector]
    return out


def subsector_groups(result: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """[(label, by_subsector entry)] in `ordered_subsectors`' order; the FX hedges' label 'FX hedges'."""
    return [(FX_GROUP if str(v.get("sector") or "") == FX_SECTOR else str(v.get("name") or k), v)
            for k, v in ordered_subsectors(result)]


def _month_parts(entries: Sequence[dict], field: str, reason_field: str, roots: Dict[str, Any]) -> List[Tuple[Optional[float], str]]:
    """(value, reason) per (exchange, month) of `field` ({'YYYY-MM': figure}) over `split` entries:
    the finest known figures, for a Net or Leftover the engine could not give (display, excl. N)."""
    out: List[Tuple[Optional[float], str]] = []
    for c in entries:
        name = short_root_name(roots.get(c["root_id"]), c["root_id"])
        for k, v in sorted((c.get(field) or {}).items()):
            out.append((_num(v), f"{name} {month_label(k)}: {c.get(reason_field) or 'no figure'}"))
    return out


def _exchange_words(c: Dict[str, Any]) -> str:
    ccy = str(c.get("currency") or "")
    return str(c.get("exchange") or "") + (f" · {ccy}" if ccy and ccy != "USD" else "")


def _leftover_hover(months: Optional[Dict[str, Any]], unit: str, reason: str) -> str:
    """Where the leftover sits, month by month, and why a month has no figure."""
    parts = [f"{month_label(k)} {qty_text(_num(v))} {unit}".strip() for k, v in sorted((months or {}).items())
             if _num(v) is not None and abs(_num(v) or 0.0) > 1e-9]
    words = "the part of the net that belongs to no spread" + (f": {'; '.join(parts)}" if parts else "")
    return "; ".join(t for t in (words, reason) if t)


def _physical(sub: Dict[str, Any], c: Optional[Dict[str, Any]], split: List[dict], result: Dict[str, Any],
              roots: Dict[str, Any]) -> dict:
    """Cells, Net, Gross and unit of one line in physical units: the commodity line (c None) or one
    exchange row (c a `split` entry)."""
    unit = str(sub.get("unit") or "")
    names = {x["root_id"]: short_root_name(roots.get(x["root_id"]), x["root_id"]) for x in split}
    if c is None:
        hovers = _contracts_hover(result, [x["root_id"] for x in split], roots)
        cells = _cells(sub.get("months_units"), str(sub.get("months_units_reason") or sub.get("units_note") or ""), hovers)
        reason = str(sub.get("months_units_reason") or sub.get("units_note") or "")
        net = _total(sub.get("net_delta_units"), _month_parts(split, "months_units", "months_units_reason", roots)
                     if unit else (), reason)
        gross = _total(sub.get("gross_units"), (), reason)
        del names
        return {"cells": cells, "net": net, "gross": gross, "unit": unit}
    if not unit:        # the exchanges' units do not add: each exchange in its own unit
        cells, own, net, gross = _root_units_months(result, c["root_id"], roots)
        note = f"in {own}, the exchange's own unit: {sub.get('units_note') or 'the units do not add'}"
        return {"cells": cells, "net": {**net, "hover": note}, "gross": {**gross, "hover": note}, "unit": own, "own": True}
    hovers = _contracts_hover(result, [c["root_id"]], roots)
    reason = str(c.get("months_units_reason") or "")
    return {"cells": _cells(c.get("months_units"), reason, hovers),
            "net": _total(c.get("net_delta_units"), _month_parts([c], "months_units", "months_units_reason", roots), reason),
            "gross": _total(c.get("gross_units"), (), reason), "unit": unit}


def _leftover(sub: Dict[str, Any], c: Optional[Dict[str, Any]], split: List[dict], roots: Dict[str, Any]) -> dict:
    unit = str(sub.get("unit") or "")
    if c is None:
        fig = _total(sub.get("leftover_units"), _month_parts(split, "leftover_months_units", "leftover_reason", roots)
                     if unit else (), str(sub.get("leftover_reason") or ""))
        fig["hover"] = _leftover_hover(sub.get("leftover_months_units"), unit, fig["why"])
    else:
        fig = _total(c.get("leftover_units"), _month_parts([c], "leftover_months_units", "leftover_reason", roots)
                     if unit else (), str(c.get("leftover_reason") or ""))
        fig["hover"] = _leftover_hover(c.get("leftover_months_units"), unit, fig["why"])
    fig["unit"] = unit
    return fig


def _line(kind: str, key: str, label: str, sub_text: str, view: dict, leftover: dict, hover: str, **extra) -> dict:
    return {"kind": kind, "key": key, "label": label, "sub": sub_text, "cells": view["cells"], "net": view["net"],
            "gross": view["gross"], "unit": view.get("unit", ""), "leftover": leftover, "hover": hover,
            "no_months": bool(view.get("no_months")), "own": bool(view.get("own")), **extra}


def grid_lines(result: Dict[str, Any], unit: str, roots: Dict[str, Any]) -> List[dict]:
    """The grid's lines in `unit`: per commodity (`ordered_subsectors`) a commodity line, then its
    exchange lines when it trades on more than one exchange (a one-exchange commodity is one line,
    its exchange small after the name). Each line: cells {month: (value or None, hover)}, net /
    gross / leftover figures ({v, excl, why}), the unit, and for a commodity line its key."""
    unit = unit if unit in UNITS else DEFAULT_UNIT
    out: List[dict] = []
    prev_sector = None
    for key, sub in ordered_subsectors(result):
        split = list(sub.get("split") or [])
        sector = str(sub.get("sector") or "")
        multi = len(split) > 1
        label = FX_GROUP if sector == FX_SECTOR else str(sub.get("name") or key)
        sub_text = "" if multi else (_exchange_words(split[0]) if split else "")
        if sector == FX_SECTOR and split:
            sub_text = short_root_name(roots.get(split[0]["root_id"]), split[0]["root_id"]) + " · " + sub_text
        hover = f"{sub.get('name') or key}: " + ", ".join(
            f"{short_root_name(roots.get(x['root_id']), x['root_id'])} ({x['root_id']})" for x in split)
        base = {"sector": sector, "first_in_sector": sector != prev_sector, "roots": [x["root_id"] for x in split]}
        prev_sector = sector
        leftover = _leftover(sub, None, split, roots)
        if unit == "physical":
            view = _physical(sub, None, split, result, roots)
        elif unit == "delta_usd":
            reason = str(sub.get("delta_reason") or "")
            view = {"cells": _cells(sub.get("delta_months"), reason, _contracts_hover(result, base["roots"], roots)),
                    "net": _total(sub.get("net_delta_usd"), [cell for x in split for cell in
                                                             month_cells(result, x["root_id"], "delta_usd", roots).values()], reason),
                    "gross": _total(sub.get("gross_delta_usd"), (), reason),
                    "unit": "USD"}
        elif multi:      # lots do not add across exchanges: the physical figures, no month cells
            phys = _physical(sub, None, split, result, roots)
            note = f"in {phys['unit'] or 'physical units'}: lots do not add across exchanges"
            view = {"cells": {}, "net": {**phys["net"], "hover": note}, "gross": {**phys["gross"], "hover": note},
                    "unit": phys["unit"], "no_months": True}
        else:
            c = split[0] if split else {}
            cells = _cells(c.get("delta_months"), str(c.get("delta_reason") or ""), _contracts_hover(result, base["roots"], roots))
            view = {"cells": cells, "net": _total(c.get("net_delta_lots"), list(cells.values()), str(c.get("delta_reason") or "")),
                    "gross": _abs_total(cells), "unit": "lots"}
        out.append(_line("commodity", key, label, sub_text, view, leftover, hover, **base))
        if not multi:
            continue
        for c in split:
            rid = c["root_id"]
            name = short_root_name(roots.get(rid), rid)
            if unit == "physical":
                view = _physical(sub, c, split, result, roots)
            elif unit == "delta_usd":
                reason = str(c.get("delta_reason") or "")
                usd_cells = month_cells(result, rid, "delta_usd", roots)
                view = {"cells": usd_cells, "net": _total(c.get("net_delta_usd"), list(usd_cells.values()), reason),
                        "gross": _total(c.get("gross_delta_usd"), (), reason),
                        "unit": "USD"}
            else:
                cells = _cells(c.get("delta_months"), str(c.get("delta_reason") or ""), _contracts_hover(result, [rid], roots))
                view = {"cells": cells, "net": _total(c.get("net_delta_lots"), list(cells.values()), str(c.get("delta_reason") or "")),
                        "gross": _abs_total(cells), "unit": "lots"}
            out.append(_line("exchange", key, name, _exchange_words(c), view, _leftover(sub, c, split, roots),
                             f"{c.get('name') or name} ({rid})", sector=sector, first_in_sector=False, roots=[rid]))
    return out


def book_totals(result: Dict[str, Any], roots: Dict[str, Any]) -> Tuple[dict, dict]:
    """(gross, net) delta USD of the commodities: every contract root's engine figure (the exchange
    rows of `by_subsector`, the FX hedges left out: never netted into a commodity), the known ones
    summed with how many were left out and why."""
    pg, pn = [], []
    for _key, sub in ordered_subsectors(result):
        if str(sub.get("sector") or "") == FX_SECTOR:
            continue
        for c in sub.get("split") or []:
            why = f"{short_root_name(roots.get(c['root_id']), c['root_id'])}: {c.get('delta_reason') or 'no USD delta'}"
            pg.append((c.get("gross_delta_usd"), why))
            pn.append((c.get("net_delta_usd"), why))
    out = []
    for parts in (pg, pn):
        total, excl, reasons = sum_known(parts)
        out.append({"v": total, "excl": excl, "why": "; ".join(dict.fromkeys(reasons)) or ("no commodity position" if not parts else "")})
    return out[0], out[1]


# --------------------------------------------------------------------------- the grid's cells
def qty_text(v: Optional[float]) -> str:
    """'+50,000', '−150', '+45.4', '+600k', '0': a quantity in physical units with its sign, a
    real minus, a tenth at most under 100, k / m from 100,000."""
    if v is None:
        return NA
    a = abs(v)
    if a < 1e-9:
        return "0"
    if a >= 100_000:
        body = short_money(a)
    elif a >= 100:
        body = f"{a:,.0f}"
    else:
        body = f"{a:,.1f}".rstrip("0").rstrip(".")
    if float(body.replace(",", "").rstrip("kmbn") or 0) == 0:
        return "0"
    return (MINUS if v < 0 else "+") + body


def _text(v: float, unit: str, gross: bool = False) -> str:
    if unit == "delta_usd":
        return short_money(v) if gross else signed_money(v)
    text = lots_text(v) if unit == "delta_lots" else qty_text(v)
    return text.lstrip("+") if gross else text


def _full(v: float, unit: str, unit_word: str) -> str:
    if unit == "delta_usd":
        return full_money(v)
    if unit == "delta_lots":
        return f"{v:,.4g} delta lots".replace("-", MINUS)
    return f"{v:,.2f} {unit_word}".replace("-", MINUS).strip()


def _cell_td(cell: Optional[Tuple[Optional[float], str]], unit: str, unit_word: str, magnitudes: List[float]) -> html.Td:
    if cell is None:
        return html.Td("")                      # no position in that month
    value, hover = cell
    if value is None:
        return html.Td(missing_cell(hover))
    colour = heat_colour(value, magnitudes)
    title = "\n".join(t for t in (_full(value, unit, unit_word), hover) if t)
    style = {"backgroundColor": colour, "color": HEAT_INK} if colour else None
    return html.Td(_text(value, unit), title=plain_words(title), style=style,
                   className=None if colour else (sign_class(value) or None))


def _later(cells: Dict[str, Tuple[Optional[float], str]], later: Sequence[str]) -> Optional[Tuple[Optional[float], str]]:
    """The Later cell: the known months after the cut-off summed; a dash with the reasons when one
    of them has no figure (never a partial sum); None when the row holds none of them."""
    mine = [(k, cells[k]) for k in later if k in cells]
    if not mine:
        return None
    gaps = [f"{month_label(k)}: {h}" for k, (v, h) in mine if v is None]
    if gaps:
        return None, "; ".join(gaps)
    return float(sum(v for _k, (v, _h) in mine)), "; ".join(f"{month_label(k)} {h}" for k, (_v, h) in mine)


def _figure_td(fig: dict, unit: str, unit_word: str, gross: bool = False, suffix: str = "", bold: bool = False) -> html.Td:
    extra = [marker(f"excl. {fig['excl']}", fig.get("why"))] if fig.get("excl") else []
    if fig.get("v") is None:
        return html.Td([missing_cell(fig.get("why") or "no figure")] + extra)
    v = fig["v"]
    shown_unit = unit
    if fig.get("physical"):
        shown_unit = "physical"
    title = "\n".join(t for t in (_full(v, shown_unit, unit_word), fig.get("hover", ""),
                                  "the exchanges' known figures added up" if fig.get("excl") else "") if t)
    children: List[Any] = [html.Span(_text(v, shown_unit, gross), className=None if gross else (sign_class(v) or None))]
    if suffix:
        children.append(unit_suffix(suffix))
    return html.Td(children + extra, title=plain_words(title), style={"fontWeight": 600} if bold else None)


def _leftover_td(fig: dict) -> html.Td:
    v, unit = fig.get("v"), fig.get("unit") or ""
    extra = [marker(f"excl. {fig['excl']}", fig.get("why"))] if fig.get("excl") else []
    if v is None:
        return html.Td([missing_cell(fig.get("hover") or fig.get("why"))] + extra)
    if abs(v) < 1e-9:
        return html.Td([html.Span("0", className="curve-leftover-zero")] + extra,
                       title=plain_words(fig.get("hover") or "") or None)
    return html.Td([html.Span(qty_text(v), className="curve-leftover-hot"), unit_suffix(unit)] + extra,
                   title=plain_words("\n".join(t for t in (f"{v:,.2f} {unit}".replace('-', MINUS), fig.get("hover")) if t)))


def _line_tr(line: dict, near: Sequence[str], later: Sequence[str], unit: str, magnitudes: List[float]) -> html.Tr:
    commodity = line["kind"] == "commodity"
    name: List[Any] = [line["label"]]
    if line["sub"]:
        name.append(html.Span(line["sub"], className="name-sub"))
    tip = line["hover"] + ("\nClick for the futures curve and the positions under it." if commodity else "")
    cells: List[Any] = [html.Td(name, className="l book-name", title=plain_words(tip))]
    unit_word = line["unit"]
    if line.get("no_months"):
        note = "lots do not add across exchanges: see the exchange rows"
        cells += [html.Td("", title=note, className="curve-no-add") for _k in near] + ([html.Td("", title=note)] if later else [])
    else:
        cells += [_cell_td(line["cells"].get(k), unit, unit_word, magnitudes) for k in near]
        if later:
            cells.append(_cell_td(_later(line["cells"], later), unit, unit_word, magnitudes))
    net, gross = dict(line["net"]), dict(line["gross"])
    physical_in_lots = unit == "delta_lots" and bool(line.get("no_months"))
    if physical_in_lots:
        net["physical"] = gross["physical"] = True
    suffix = unit_word if (unit == "physical" and (commodity or line.get("own"))) or physical_in_lots else ""
    cells.append(_figure_td(net, unit, unit_word, suffix=suffix, bold=True))
    cells.append(_figure_td(gross, unit, unit_word, gross=True))
    cells.append(_leftover_td(line["leftover"]))
    classes = ["curve-row", "curve-commodity" if commodity else "curve-exchange"]
    if commodity and line.get("first_in_sector"):
        classes.append("curve-sector-first")
    if commodity:
        classes.append("curve-click")
        return html.Tr(cells, className=" ".join(classes), id={"type": ROW_TYPE, "idx": line["key"]}, n_clicks=0)
    return html.Tr(cells, className=" ".join(classes))


def _book_tr(result: Dict[str, Any], roots: Dict[str, Any], span: int) -> html.Tr:
    gross, net = book_totals(result, roots)
    cells: List[Any] = [html.Td("Book", className="l", title="The commodities' delta USD (the engine's, per exchange) added "
                                                           "up; the FX hedges are their own line and in the Currency card.")]
    cells.append(html.Td(html.Span("delta USD, the FX hedges apart", className="grid-note"), colSpan=span, className="l"))
    for fig, is_gross in ((net, False), (gross, True)):
        extra = [marker(f"excl. {fig['excl']}", fig["why"])] if fig["excl"] else []
        if fig["v"] is None:
            cells.append(html.Td([missing_cell(fig["why"])] + extra))
        else:
            text = short_money(fig["v"], "$") if is_gross else signed_money(fig["v"], "$")
            cells.append(html.Td([html.Span(text, className=None if is_gross else (sign_class(fig["v"]) or None))] + extra,
                                 title=full_money(fig["v"])))
    cells.append(html.Td("", title="The leftover is in each commodity's own unit: it does not add across commodities."))
    return html.Tr(cells, className="book-total")


def column_months(result: Dict[str, Any], as_of: str) -> Tuple[List[str], List[str]]:
    """(the months shown as columns, the months gathered in "Later"): every month a row holds, in
    order; a month more than 12 months after the as-of's month goes to Later."""
    months = set(result.get("months") or [])
    for sub in (result.get("by_subsector") or {}).values():
        months |= set(sub.get("months_units") or {}) | set(sub.get("delta_months") or {})
    try:
        cut = f"{int(as_of[:4]) + 1:04d}-{int(as_of[5:7]):02d}"
    except (TypeError, ValueError):
        cut = "9999-12"
    ordered = sorted(months)
    return [k for k in ordered if k <= cut], [k for k in ordered if k > cut]


def grid_section(result: Dict[str, Any], unit: str, roots: Dict[str, Any], as_of: str = "") -> html.Div:
    unit = unit if unit in UNITS else DEFAULT_UNIT
    near, later = column_months(result, as_of)
    lines = grid_lines(result, unit, roots)
    magnitudes = sorted(abs(v) for line in lines for v, _h in line["cells"].values() if v is not None and v != 0)
    head = html.Thead(html.Tr(
        [html.Th(about("Commodity", GRID_ABOUT, level="span"), className="l")]
        + [html.Th(month_label(k), title=f"contract month {k}, delta in {UNIT_LABELS[unit]}") for k in near]
        + ([html.Th("Later", title="The months more than 12 months after the as-of, added up: "
                                   + ", ".join(month_label(k) for k in later))] if later else [])
        + [html.Th("Net", title="The net delta over every month (the engine's; a commodity row whose figure is missing "
                                "adds up its exchanges' known figures, excl. N)."),
           html.Th("Gross", title="Physical and USD: the engine's gross (the size of every position, whatever its sign). "
                                  "Lots: the month cells' absolute values added up (display)."),
           html.Th("Leftover", title="The part of the net that belongs to no spread, in the commodity's physical unit in "
                                     "every view: 0 is fully paired; anything else is the relative-value leak.")]))
    body = [_line_tr(line, near, later, unit, magnitudes) for line in lines]
    body.append(_book_tr(result, roots, len(near) + (1 if later else 0)))
    table = html.Table([head, html.Tbody(body)], id=GRID_ID, className="book-table curve-grid")
    return html.Div(className="book-card curve-grid-card", children=[table])


# --------------------------------------------------------------------------- tiles and the sector tilt
def _tile(label: str, value: List[Any], hover: str = "") -> html.Div:
    return html.Div(className="book-tile", title=plain_words(hover) or None, children=[
        html.Div(label, className="k"), html.Div(value, className="v")])


def china_strategies(result: Dict[str, Any], strategies: Sequence[dict]) -> Tuple[bool, List[dict]]:
    """(the book has China legs, the strategies holding one): a China leg is a commodity row priced
    in CNY / CNH (the FX hedges are not legs); a strategy holds one when a trade of its is on such
    a row."""
    china = {str(t) for r in result.get("rows") or [] if str(r.get("currency") or "") in CHINA_CCYS
             and str(r.get("sector") or "") != FX_SECTOR for t in (r.get("trade_ids") or [])}
    has = any(str(r.get("currency") or "") in CHINA_CCYS and str(r.get("sector") or "") != FX_SECTOR
              for r in result.get("rows") or [])
    return has, [s for s in strategies or [] if china & {str(t) for t in s.get("trade_ids") or []}]


def _strategy_name(s: dict) -> str:
    return str(s.get("name") or "") or "No trade name"


def coverage_figure(strategies: Sequence[dict]) -> Tuple[List[Any], str]:
    """The coverage tile / cell: one strategy with China legs -> its `hedge_coverage_net` as a
    percentage (a dash with its reason when the engine gives none); several -> "see hover" with
    one line per strategy; never an average of ratios."""
    if not strategies:
        return [missing_cell("no strategy holds a China leg")], ""
    lines = []
    for s in strategies:
        cov = _num(s.get("hedge_coverage_net"))
        lines.append(f"{_strategy_name(s)}: " + (f"{cov * 100:.0f}% hedged" if cov is not None else
                                                  f"no coverage ({s.get('hedge_reason') or 'not given'})")
                     + (f"; {s['hedge_reason']}" if cov is not None and s.get("hedge_reason") else ""))
    if len(strategies) == 1:
        cov = _num(strategies[0].get("hedge_coverage_net"))
        if cov is None:
            return [missing_cell(strategies[0].get("hedge_reason") or "the spreads engine gives no coverage")], lines[0]
        warn = [marker("check", strategies[0]["hedge_reason"])] if strategies[0].get("hedge_reason") else []
        return [f"{cov * 100:.0f}%"] + warn, lines[0]
    return [html.Span("see hover", className="grid-note")], "\n".join(lines)


def largest_net(result: Dict[str, Any]) -> Tuple[List[Any], str]:
    """The commodity with the largest |net delta USD| among those the engine gives one for, in words
    in its physical unit ('Copper net short 5 t'); the others named on hover."""
    best, left = None, []
    for _key, sub in ordered_subsectors(result):
        if str(sub.get("sector") or "") == FX_SECTOR:
            continue
        usd = _num(sub.get("net_delta_usd"))
        if usd is None:
            left.append(str(sub.get("name") or _key))
            continue
        if best is None or abs(usd) > abs(best[0]):
            best = (usd, sub)
    if best is None:
        return [missing_cell("no commodity has a full net delta USD" + (f": {', '.join(left)}" if left else ""))], ""
    usd, sub = best
    units, unit = _num(sub.get("net_delta_units")), str(sub.get("unit") or "")
    words = (f"net {size_words(round(units, 1), unit)}" if units is not None
             else f"net {'long' if usd > 0 else 'short' if usd < 0 else 'flat'} {short_money(abs(usd), '$')}")
    hover = f"{full_money(usd)} net delta" + (f"; not ranked (no full USD delta): {', '.join(left)}" if left else "")
    if units is None:
        hover += f"; no net in one unit: {sub.get('units_note') or sub.get('months_units_reason') or 'not given'}"
    return [str(sub.get("name") or ""), html.Span(f" {words}", className=sign_class(usd) or None)], hover


def tiles(result: Dict[str, Any], roots: Dict[str, Any], strategies: Sequence[dict]) -> html.Div:
    gross, net = book_totals(result, roots)
    items = []
    for label, fig, is_gross in (("Gross delta", gross, True), ("Net delta", net, False)):
        extra = [marker(f"excl. {fig['excl']}", fig["why"])] if fig["excl"] else []
        if fig["v"] is None:
            items.append(_tile(label, [missing_cell(fig["why"])] + extra))
        else:
            text = short_money(fig["v"], "$") if is_gross else signed_money(fig["v"], "$")
            items.append(_tile(label, [html.Span(text, className=None if is_gross else (sign_class(fig["v"]) or None))] + extra,
                               f"{full_money(fig['v'])}: the commodities' delta USD, the FX hedges apart"))
    value, hover = largest_net(result)
    items.append(_tile("Largest net", value, hover))
    has_china, china = china_strategies(result, strategies)
    if has_china:
        value, hover = coverage_figure(china)
        items.append(_tile("CNH hedge coverage", value, "The spreads engine's coverage: minus the hedge over the China "
                                                        "legs' net notional (100% = fully hedged).\n" + hover))
    return html.Div(id=TILES_ID, className="book-tiles", children=items)


def sector_figures(result: Dict[str, Any], roots: Dict[str, Any]) -> List[dict]:
    """[{sector, label, v, excl, why}] in the fixed order: the engine's `by_sector` net delta USD,
    or, when it gives none, its contract roots' known figures summed with 'excl. N'."""
    by_sector = result.get("by_sector") or {}
    by_commodity = result.get("by_commodity") or {}
    out = []
    for sector in _SECTOR_ORDER:
        s = by_sector.get(sector)
        if not s:
            continue
        v = _num(s.get("net_delta_usd"))
        excl, why = 0, ""
        if v is None:
            parts = [(c.get("net_delta_usd"), f"{short_root_name(roots.get(rid), rid)}: {c.get('delta_reason') or 'no USD delta'}")
                     for rid, c in by_commodity.items() if str(c.get("sector") or "") == sector]
            v, excl, reasons = sum_known(parts)
            why = "; ".join(dict.fromkeys(reasons)) or str(s.get("delta_reason") or "")
        out.append({"sector": sector, "label": _SECTOR_WORDS.get(sector, sector.title()), "v": v, "excl": excl, "why": why})
    return out


def sector_tilt(result: Dict[str, Any], roots: Dict[str, Any]) -> Optional[html.Div]:
    figures = sector_figures(result, roots)
    if not figures:
        return None
    top = max((abs(f["v"]) for f in figures if f["v"] is not None), default=0.0) or 1.0
    items: List[Any] = [about("Sector tilt", TILT_ABOUT, level="span", className="curve-tilt-title")]
    for f in figures:
        v = f["v"]
        if v is None:
            bar: List[Any] = []
            figure: Any = missing_cell(f["why"])
        else:
            width = f"{abs(v) / top * 50:.1f}%"
            style = {"left": "50%", "width": width} if v >= 0 else {"right": "50%", "width": width}
            bar = [html.Div(className=f"curve-tilt-bar curve-tilt-bar--{'pos' if v >= 0 else 'neg'}", style=style)]
            figure = html.Span(signed_money(v, "$"), className=sign_class(v) or None)
        extra = [marker(f"excl. {f['excl']}", f["why"])] if f["excl"] else []
        title = (f"{f['label']}: {full_money(v)} net delta" if v is not None else f"{f['label']}: {f['why']}")
        items.append(html.Div(className="curve-tilt-item", title=plain_words(title), children=[
            html.Span(f["label"], className="curve-tilt-label"),
            html.Div(className="curve-tilt-track", children=bar),
            figure, *extra]))
    return html.Div(id=TILT_ID, className="curve-tilt", children=items)


# --------------------------------------------------------------------------- the row-click panel
_FUTURES_OF_ROOT_SQL = "SELECT instrument_id, expiry_date FROM instruments WHERE asset_class = 'FUTURE' AND base_ccy = ?"
_PX_ON_SQL = ("SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND settle_date = ? "
              "AND mark_type = 'FUTURE_PX'")
_PX_BEFORE_SQL = ("SELECT as_of_date, value FROM marks_official WHERE as_of_date < ? AND instrument_id = ? "
                  "AND settle_date = ? AND mark_type = 'FUTURE_PX' ORDER BY as_of_date DESC LIMIT 1")
_LME_CURVE_SQL = ("SELECT settle_date, value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
                  "AND ((mark_type = 'SPOT' AND settle_date = as_of_date) OR mark_type = 'FWD_OUTRIGHT') "
                  "ORDER BY settle_date")
_LME_PREVIOUS_DAY_SQL = ("SELECT MAX(as_of_date) FROM marks_official WHERE as_of_date < ? AND instrument_id = ? "
                         "AND mark_type IN ('SPOT', 'FWD_OUTRIGHT')")


def futures_points(conn: sqlite3.Connection, as_of: str, root_id: str, held: Dict[str, dict]) -> List[dict]:
    """One contract root's futures curve on `as_of`, read from `marks_official` as it stands:
    [{x ('YYYY-MM'), label, price, previous, previous_date, held, why}] for every contract of the
    root on file that is held, or not expired with an official FUTURE_PX on the day or an earlier
    close. A held contract with no price on the day keeps its point with the reason (a gap in the
    line); nothing is estimated or filled."""
    out = []
    for instrument_id, expiry in conn.execute(_FUTURES_OF_ROOT_SQL, (root_id,)).fetchall():
        row = held.get(instrument_id)
        expiry = str((row or {}).get("expiry") or expiry or "")
        if row is None and expiry < as_of:
            continue
        hit = conn.execute(_PX_ON_SQL, (as_of, instrument_id, expiry)).fetchone()
        before = conn.execute(_PX_BEFORE_SQL, (as_of, instrument_id, expiry)).fetchone()
        price = _num(hit[0]) if hit else None
        previous = _num(before[1]) if before else None
        if row is None and price is None and previous is None:
            continue
        month = _row_month(row) if row else None
        if month is None:
            parsed = parse_contract_id(instrument_id)
            month = f"{parsed['year']:04d}-{parsed['month']:02d}" if parsed else expiry[:7]
        why = "" if price is not None else (
            f"the official price on file is not a number ({hit[0]!r})" if hit else f"no official price on file for {as_of}")
        out.append({"x": month, "label": instrument_id, "price": price, "previous": previous,
                    "previous_date": before[0] if before else None, "held": row is not None, "why": why})
    return sorted(out, key=lambda p: (p["x"], p["label"]))


def lme_points(conn: sqlite3.Connection, as_of: str, root_id: str, prompts: Sequence[str]) -> List[dict]:
    """An LME metal's curve on `as_of` from `marks_official` (the cash SPOT at the day, the
    FWD_OUTRIGHTs at their prompt dates) and the latest earlier day's curve as the previous close;
    each held prompt with no outright on the day is a point with its reason."""
    today = {str(d): _num(v) for d, v in conn.execute(_LME_CURVE_SQL, (as_of, root_id)).fetchall()}
    prev_day = (conn.execute(_LME_PREVIOUS_DAY_SQL, (as_of, root_id)).fetchone() or (None,))[0]
    before = ({str(d): _num(v) for d, v in conn.execute(_LME_CURVE_SQL, (prev_day, root_id)).fetchall()}
              if prev_day else {})
    out = []
    for day in sorted(set(today) | set(before) | set(prompts)):
        if day < as_of and day not in today:
            continue
        price = today.get(day)
        out.append({"x": day, "label": f"{root_id} {day}", "price": price, "previous": before.get(day),
                    "previous_date": prev_day, "held": day in prompts,
                    "why": "" if price is not None else f"no official outright at the prompt {day} on {as_of}"})
    return out


def _x_label(key: str) -> str:
    """'2026-12' -> 'Dec 26'; '2026-12-10' (an LME prompt) -> '10 Dec 26'."""
    if len(key) == 10:
        try:
            return f"{int(key[8:10])} {calendar.month_abbr[int(key[5:7])]} {key[2:4]}"
        except (ValueError, IndexError):
            return key
    return month_label(key)


def _bar_range(values: Sequence[float]) -> Optional[List[float]]:
    """The position axis from zero to the largest bar either way, with a little headroom (the
    axis only: the bars are the engine's figures)."""
    if not values:
        return None
    low, high = min(0.0, min(values)), max(0.0, max(values))
    pad = (high - low) * 0.15 or 1.0
    return [low - (pad if low < 0 else 0.0), high + (pad if high > 0 else 0.0)]


def _panel_figure(points: List[dict], bars: Dict[str, Tuple[Optional[float], str]], bar_unit: str, as_of: str):
    """One exchange's chart on a category axis of contract months (an LME metal: its prompt dates):
    our official price of `as_of` (a gap where it is missing, 'missing' there with the reason on
    hover), the previous close dashed, and the book's positions as bars on the right-hand axis
    (an LME month's bar at its first held prompt; a month with no figure says why on hover)."""
    import plotly.graph_objects as go
    by_month: Dict[str, str] = {}
    for p in points:            # a month's bar sits on its held prompt (LME) or on the month itself
        if p["held"] and p["x"][:7] not in by_month:
            by_month[p["x"][:7]] = p["x"]
    bar_x = {k: by_month.get(k, k) for k in bars}
    keys = sorted(set(p["x"] for p in points) | set(bar_x.values()))
    labels = {k: _x_label(k) for k in keys}
    fig = go.Figure()
    known_bars = [(bar_x[k], v) for k, (v, _h) in sorted(bars.items()) if v is not None]
    if known_bars:
        fig.add_trace(go.Bar(
            x=[labels[k] for k, _v in known_bars], y=[v for _k, v in known_bars], yaxis="y2", name=f"position ({bar_unit})",
            marker=dict(color=["rgba(26,127,75,0.35)" if v >= 0 else "rgba(192,57,43,0.35)" for _k, v in known_bars]),
            hovertemplate="%{x}: %{y:,.2f} " + bar_unit + "<extra></extra>", width=0.5))
    for k, (v, h) in sorted(bars.items()):
        if v is None:
            fig.add_annotation(x=labels[bar_x[k]], y=1, yref="paper", yanchor="top", showarrow=False,
                               text="position " + MISSING, hovertext=plain_words(h) or "no figure",
                               font=dict(color="#8a919c", size=10))
    if points:
        xs = [labels[p["x"]] for p in points]
        fig.add_trace(go.Scatter(
            x=xs, y=[p["price"] for p in points], mode="lines+markers", name=f"official {short_date(as_of)}",
            connectgaps=False, text=[p["label"] for p in points], hovertemplate="%{text}<br>%{y}<extra></extra>",
            line=dict(color="#1f5fbf", width=2), marker=dict(size=[9 if p["held"] else 5 for p in points])))
        if any(p["previous"] is not None for p in points):
            fig.add_trace(go.Scatter(
                x=xs, y=[p["previous"] for p in points], mode="lines+markers", name="previous close", connectgaps=False,
                text=[f"{p['label']}<br>close of {p['previous_date']}" for p in points],
                hovertemplate="%{text}<br>%{y}<extra></extra>", marker=dict(size=4, color="#8a94a6"),
                line=dict(color="#8a94a6", width=1.5, dash="dash")))
        for p in points:
            if p["price"] is None:
                fig.add_annotation(x=labels[p["x"]], y=0, yref="paper", yanchor="bottom", showarrow=False, text="missing",
                                   hovertext=f"{p['label']}: {p['why']}", font=dict(color="#b42318", size=10))
    fig.update_layout(height=250, margin=dict(t=30, b=30, l=56, r=56),
                      legend=dict(orientation="h", y=1.18, x=0, font=dict(size=10.5)),
                      xaxis=dict(type="category", categoryorder="array", categoryarray=[labels[k] for k in keys],
                                 showgrid=False),
                      yaxis=dict(showgrid=True, gridcolor="#eef0f3", title=dict(text="price as quoted", font=dict(size=11))),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, zeroline=True, zerolinecolor="#c9ced6",
                                  range=_bar_range([v for _k, v in known_bars]),
                                  title=dict(text=f"position ({bar_unit})", font=dict(size=11))),
                      plot_bgcolor="#fff", paper_bgcolor="#fff")
    return fig


def commodity_panel(conn: sqlite3.Connection, as_of: str, sub_key: str, result: Optional[Dict[str, Any]] = None) -> html.Div:
    """The panel a commodity row opens: per exchange of that commodity, its futures curve on `as_of`
    (our official prices by contract month, an LME metal by prompt date, the previous close dashed;
    a missing price a gap with its reason) and the book's positions under each month (the
    engine's `months_units` in the commodity's physical unit; the exchange's own unit when the
    units do not add; delta lots when neither is given)."""
    result = result if result is not None else curve_positions(conn, as_of)
    sub = (result.get("by_subsector") or {}).get(sub_key)
    roots = _roots()
    if not sub:
        return html.Div(className="book-card card-pad curve-panel", children=[
            html.P(missing_cell(f"{sub_key} is not held on {as_of}"), className="section-kicker")])
    unit = str(sub.get("unit") or "")
    charts: List[Any] = []
    for c in sub.get("split") or []:
        rid = c["root_id"]
        root = roots.get(rid)
        name = short_root_name(root, rid)
        mine = [r for r in result.get("rows") or [] if r.get("root_id") == rid]
        if unit:
            bars, bar_unit = _cells(c.get("months_units"), str(c.get("months_units_reason") or "")), unit
        else:
            bars, bar_unit, _n, _g = _root_units_months(result, rid, roots)
        if not any(v is not None for v, _h in bars.values()):
            bars, bar_unit = _cells(c.get("delta_months"), str(c.get("delta_reason") or "")), "delta lots"
        if any(_product(r) == LME for r in mine):
            points = lme_points(conn, as_of, rid, sorted({str(r.get("expiry") or "") for r in mine if _product(r) == LME}))
        else:
            held = {str(r.get("contract_id")): r for r in mine if _product(r) == FUTURE}
            for r in mine:          # an option's underlying future is on the curve too
                if _product(r) == OPTION and r.get("underlying_id"):
                    held.setdefault(str(r["underlying_id"]), {k: v for k, v in r.items() if k != "expiry"})
            points = futures_points(conn, as_of, rid, held)
        title = [name, html.Span(f"{_exchange_words(c)} · {quoted_unit(root) or 'price as quoted'}", className="name-sub")]
        if not points:
            part: Any = html.P(missing_cell(f"no official price on file for {name} on or before {as_of}"),
                               className="section-kicker")
        else:
            part = dcc.Graph(figure=_panel_figure(points, bars, bar_unit, as_of), config={"displayModeBar": False})
        charts.append(html.Div(className="curve-panel-chart", children=[html.Div(title, className="book-h"), part]))
    head = html.Div(className="card-head", children=[
        about(str(sub.get("name") or sub_key), "Each exchange's futures curve on the as-of from our official marks (the "
              "previous close dashed; an LME metal by prompt date), the book's positions as bars under each month on "
              "the right-hand axis. Click the row again to close.", level="div", className="book-h"),
        html.Span("futures curve and positions", className="book-counts")])
    return html.Div(className="book-card card-pad curve-panel", children=[
        head, html.Div(className="curve-panel-charts", children=charts)])


def render_panel(sub_key: Optional[str], as_of: Optional[str], db_path) -> Any:
    """The panel for the chosen commodity, from one read-only connection; nothing when none is chosen."""
    if not sub_key or not as_of:
        return []
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        return commodity_panel(conn, as_of, sub_key)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen
        log.exception("exposure panel failed for %s %s", sub_key, as_of)
        return message_box(f"The futures curve could not be drawn ({type(exc).__name__}: {exc}).")
    finally:
        conn.close()


# --------------------------------------------------------------------------- the Greeks card
GREEK_MARK_TYPES = (("gamma", "GAMMA"), ("theta", "THETA"), ("vega", "VEGA"))


def _option_greek_marks(conn: sqlite3.Connection, as_of: str, instruments: List[str]) -> Dict[Tuple[str, str], float]:
    """{(instrument, mark type): value}: the official GAMMA / THETA / VEGA marks of `instruments`
    on `as_of`, read as they are."""
    if not instruments:
        return {}
    placeholders = ",".join("?" * len(instruments))
    rows = conn.execute(
        f"SELECT instrument_id, mark_type, value FROM marks_official WHERE as_of_date = ? "
        f"AND mark_type IN ('GAMMA','THETA','VEGA') AND instrument_id IN ({placeholders})",
        (as_of, *instruments)).fetchall()
    return {(str(i), str(mt)): float(v) for i, mt, v in rows if v is not None}


def greeks_lines(result: Dict[str, Any], marks: Dict[Tuple[str, str], float], roots: Dict[str, Any],
                 fx_options: Optional[dict] = None) -> List[dict]:
    """One line per underlying commodity with open options on futures (the figures summed over
    the options that have them, None with the reasons otherwise), then one per FX option pair."""
    options = [r for r in (result.get("rows") or []) if _product(r) == OPTION]
    by_root: Dict[str, List[dict]] = {}
    for r in options:
        by_root.setdefault(str(r.get("root_id") or ""), []).append(r)
    lines = []
    for root_id, rows in by_root.items():
        root = roots.get(root_id)
        ccy = str(rows[0].get("currency") or "")
        legs = ", ".join(f"{contract_name(r.get('contract_id'), root, root_id)} {size_words(_num(r.get('lots')))}" for r in rows)
        line = {"label": short_root_name(root, root_id), "legs": legs, "ccy": ccy, "kind": "commodity",
                "hover": f"{_plural(len(rows), 'option')} on {root_id}: {legs}"}
        pairs = {k: [] for k in ("delta", "gamma", "theta", "vega")}
        for r in rows:
            cid = str(r.get("contract_id") or "")
            lots, dl = _num(r.get("lots")), _num(r.get("delta_lots"))
            name = contract_name(cid, root, root_id)
            pairs["delta"].append((dl, f"{name}: {plain_words(r.get('reason')) or 'no delta mark'}" if dl is None else ""))
            for col, mt in GREEK_MARK_TYPES:
                per_lot = marks.get((cid, mt))
                if per_lot is None or lots is None:
                    pairs[col].append((None, f"{name}: no {mt.lower()} mark on file for this date" if lots is not None
                                       else f"{name}: {r.get('reason') or 'no lots'}"))
                else:
                    pairs[col].append((per_lot * lots, ""))
        for col in pairs:
            total, excluded, reasons = sum_known(pairs[col])
            line[col] = None if excluded else total
            line[f"{col}_hover"] = ("; ".join(reasons) if excluded else
                                    ("lots × the option's official delta per lot, futures-equivalent lots" if col == "delta"
                                     else f"the per-lot {col.upper()} mark × lots, in {ccy or 'the contract currency'}"))
        lines.append(line)
    for pair, usd in sorted(((fx_options or {}).get("by_pair") or {}).items()):
        note = "an FX option's gamma, theta and vega are on the Trades tab, Options; not summed here"
        lines.append({"label": f"{pair} options", "legs": "FX options at delta", "ccy": "USD", "kind": "fx",
                      "hover": f"the open FX options on {pair}, their USD delta as the book gives it",
                      "delta": _num(usd), "delta_hover": "USD delta of the pair's open FX options"
                      if _num(usd) is not None else ((fx_options or {}).get("reason") or "no delta"),
                      "gamma": None, "gamma_hover": note, "theta": None, "theta_hover": note, "vega": None, "vega_hover": note,
                      "delta_usd": True})
    return lines


def _greek_td(value: Optional[float], hover: str, ccy: str, money: bool = False, decimals: int = 2) -> html.Td:
    if value is None:
        return html.Td(missing_cell(hover))
    if money:
        return html.Td(money_cell(value, hover=hover))
    text = f"{abs(value):,.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{abs(value):,.0f}"
    if float(text.replace(",", "") or 0) == 0:
        text = "0"
    else:
        text = (MINUS if value < 0 else "+") + text
    children: List[Any] = [text]
    if ccy:
        children.append(html.Span(ccy, className="cell-unit"))
    return html.Td(children, className=sign_class(value) or None, title=plain_words(hover))


def greeks_card(result: Dict[str, Any], marks: Dict[Tuple[str, str], float], roots: Dict[str, Any],
                fx_options: Optional[dict] = None) -> html.Div:
    lines = greeks_lines(result, marks, roots, fx_options)
    head = html.Div(className="card-head", children=[
        about("Option Greeks", GREEKS_ABOUT, level="div", className="book-h"),
        html.Span("per position", className="book-counts")])
    if not lines:
        return html.Div(id=GREEKS_ID, className="book-card card-pad", children=[
            head, html.P("No open option: the book carries no gamma, theta or vega.", className="section-kicker")])
    thead = html.Thead(html.Tr([
        html.Th("Underlying", className="l"),
        html.Th("Delta", title="Options on futures: futures-equivalent lots (lots × the official delta per lot). FX options: USD delta."),
        html.Th("Gamma", title="The gamma mark per lot × lots, in the contract's currency, per 1.0 move of the future."),
        html.Th("Theta / day", title="The theta mark per lot × lots, in the contract's currency, per calendar day."),
        html.Th("Vega / vol pt", title="The vega mark per lot × lots, in the contract's currency, per vol point.")]))
    body = []
    for line in lines:
        name: List[Any] = [line["label"], html.Span(line["legs"], className="name-sub")]
        cells: List[Any] = [html.Td(name, className="l book-name", title=plain_words(line["hover"]))]
        if line.get("delta_usd"):
            cells.append(_greek_td(line["delta"], line["delta_hover"], "", money=True))
        else:
            cells.append(_greek_td(line["delta"], line["delta_hover"], "lots", decimals=2))
        for col in ("gamma", "theta", "vega"):
            cells.append(_greek_td(line[col], line[f"{col}_hover"], line["ccy"], decimals=2 if col == "gamma" else 0))
        body.append(html.Tr(cells))
    return html.Div(id=GREEKS_ID, className="book-card card-pad", children=[
        head, html.Table([thead, html.Tbody(body)], className="book-table")])


# --------------------------------------------------------------------------- the currency card
def fx_sources(conn: sqlite3.Connection, as_of: str) -> Dict[str, List[str]]:
    """{currency: ['2 forwards', '1 put at delta', ...]}: what the trades on file contribute to
    each currency's delta, counted (display) from the same rows the engine reads: FX legs still
    to settle, FX options open on the pair, an LME ticket's USD leg."""
    out: Dict[str, List[str]] = {}
    words = {"FX_FWD": ("forward", "forwards"), "FX_SPOT": ("spot trade", "spot trades"), "FX_SWAP": ("swap leg", "swap legs")}
    try:
        for ccy, product, n in conn.execute(
                "SELECT l.ccy, t.product, COUNT(DISTINCT t.trade_id) FROM trade_legs l JOIN trades t USING (trade_id) "
                "WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.settle_date > ? GROUP BY l.ccy, t.product",
                (as_of,)):
            one, many = words.get(str(product), (str(product).lower(), str(product).lower()))
            out.setdefault(str(ccy), []).append(f"{n} {one if n == 1 else many}")
        for base, quote, otype, n in conn.execute(
                "SELECT i.base_ccy, i.quote_ccy, COALESCE(o.option_type, ''), COUNT(*) FROM trades t "
                "JOIN instruments i USING (instrument_id) LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id "
                "WHERE t.product = 'FX_OPTION' AND i.expiry_date >= ? GROUP BY 1, 2, 3", (as_of,)):
            kind = {"CALL": "call", "PUT": "put"}.get(str(otype).upper(), "option")
            text = f"{n} {kind}{'' if n == 1 else 's'} at delta"
            for ccy in (str(base), str(quote)):
                out.setdefault(ccy, []).append(text)
        (n_lme,) = conn.execute(
            "SELECT COUNT(DISTINCT t.trade_id) FROM trade_legs l JOIN trades t USING (trade_id) "
            "WHERE t.product = 'LME_FWD' AND l.settles_cash = 1 AND l.settle_date > ?", (as_of,)).fetchone()
        if n_lme:
            out.setdefault("USD", []).append(f"{n_lme} LME USD leg{'' if n_lme == 1 else 's'}")
    except sqlite3.Error as exc:
        out["__error__"] = [f"the sources could not be counted ({exc})"]
    return out


def _delta_local_text(v: Optional[float], ccy: str) -> str:
    if v is None:
        return NA
    text = short_money(v)
    return text if text.startswith(MINUS) or text == "0" else "+" + text


def _ccy_group(ccy: str) -> str:
    return "CNY" if ccy in CHINA_CCYS else ccy


def currency_lines(result: Dict[str, Any], strategies: Sequence[dict], roots: Dict[str, Any]) -> List[dict]:
    """One line per currency other than USD that a commodity leg is priced in (CNY and CNH as one):
    the legs' delta USD (each exchange's engine figure, the known ones summed, 'excl. N'), the FX
    hedge against them (the spreads engine's open hedges in that currency: their USD notional, the
    USD/CNH position's sign, short = minus; the SGX USD/CNH future among them), unhedged (the two
    added, for the China legs: a long China leg is hedged by a short USD/CNH position, the
    engine's convention) and the coverage the spreads engine gives for the strategies holding
    China legs."""
    legs: Dict[str, List[Tuple[Optional[float], str]]] = {}
    exchanges: Dict[str, List[str]] = {}
    for _key, sub in ordered_subsectors(result):
        if str(sub.get("sector") or "") == FX_SECTOR:
            continue
        for c in sub.get("split") or []:
            ccy = str(c.get("currency") or "")
            if not ccy or ccy == "USD":
                continue
            name = short_root_name(roots.get(c["root_id"]), c["root_id"])
            legs.setdefault(_ccy_group(ccy), []).append((c.get("net_delta_usd"), f"{name}: {c.get('delta_reason') or 'no USD delta'}"))
            exchanges.setdefault(_ccy_group(ccy), []).append(name)
    _has, china = china_strategies(result, strategies)
    out = []
    for ccy in sorted(legs, key=lambda k: (k != "CNY", k)):
        leg_total, leg_excl, leg_reasons = sum_known(legs[ccy])
        parts, words = [], []
        for s in strategies or []:
            for h in s.get("hedges") or []:
                if not h.get("open_trade_ids"):
                    continue
                if _ccy_group(str(h.get("currency") or "")) != ccy and _ccy_group(str(h.get("root_id") or "")) != ccy:
                    continue
                usd = _num(h.get("usd_notional"))
                why = str(h.get("notional_reason") or "no USD notional")
                parts.append((usd, why if why.startswith(str(h.get("instrument_id") or "")) else f"{h.get('instrument_id')}: {why}"))
                if usd is not None:
                    words.append(f"{h.get('instrument_id')} {str(h.get('product') or '').replace('_', ' ').lower()} "
                                 f"{signed_money(usd, '$')}" + (f" ({_strategy_name(s)})" if s.get("name") else ""))
        hedge, hedge_excl, hedge_reasons = sum_known(parts)
        line = {"ccy": "CNY / CNH" if ccy == "CNY" else ccy, "exchanges": exchanges[ccy],
                "legs": {"v": leg_total, "excl": leg_excl, "why": "; ".join(dict.fromkeys(leg_reasons))},
                "hedge": {"v": hedge if parts else 0.0, "excl": hedge_excl, "why": "; ".join(dict.fromkeys(hedge_reasons)),
                          "hover": "; ".join(words) if parts else f"no open FX hedge in {ccy} on file"},
                "china": ccy == "CNY"}
        if ccy == "CNY":
            if leg_total is not None and (hedge is not None or not parts):
                v = leg_total + (hedge or 0.0)
                line["unhedged"] = {"v": v, "excl": leg_excl + hedge_excl,
                                    "why": "; ".join(dict.fromkeys(leg_reasons + hedge_reasons)),
                                    "hover": "the legs' delta USD plus the hedge's USD notional (a long China leg is "
                                             "hedged by a short USD/CNH position)"}
            else:
                line["unhedged"] = {"v": None, "excl": 0, "why": "; ".join(dict.fromkeys(leg_reasons + hedge_reasons))
                                    or "no figure"}
            line["coverage"] = coverage_figure(china)
        else:
            line["unhedged"] = {"v": None, "excl": 0,
                                "why": f"not added for {ccy}: the hedge's sign convention is set for the China legs "
                                       "(a long China leg against a short USD/CNH position) and not yet for other "
                                       "currencies"}
            line["coverage"] = ([missing_cell(f"the spreads engine gives coverage for the China legs only, not {ccy}")], "")
        out.append(line)
    return out


def _money_td(fig: dict, symbol: str = "$") -> html.Td:
    extra = [marker(f"excl. {fig['excl']}", fig.get("why"))] if fig.get("excl") else []
    if fig.get("v") is None:
        return html.Td([missing_cell(fig.get("why") or "no figure")] + extra)
    v = fig["v"]
    title = "\n".join(t for t in (full_money(v), fig.get("hover", "")) if t)
    return html.Td([html.Span(signed_money(v, symbol), className=sign_class(v) or None)] + extra, title=plain_words(title))


def legs_table(lines: List[dict]) -> Optional[html.Table]:
    if not lines:
        return None
    thead = html.Thead(html.Tr([
        html.Th("Currency", className="l", title="The currency the commodity legs are priced in (CNY and CNH as one)."),
        html.Th("Legs", title="The legs' net delta USD: each exchange's engine figure, added up."),
        html.Th("FX hedge", title="The open FX hedges in that currency: the USD notional of the USD/CNH position (short = "
                                  "minus), the SGX USD/CNH future included (the spreads engine's hedges)."),
        html.Th("Unhedged", title="Legs plus hedge: a long China leg is hedged by a short USD/CNH position."),
        html.Th("Coverage", title="The spreads engine's coverage of the China legs: minus the hedge over their net "
                                  "notional, 100% = fully hedged; several strategies on hover.")]))
    body = []
    for line in lines:
        cov, cov_hover = line["coverage"]
        body.append(html.Tr([
            html.Td([line["ccy"], html.Span(", ".join(line["exchanges"]), className="name-sub")], className="l book-name"),
            _money_td(line["legs"]), _money_td(line["hedge"]), _money_td(line["unhedged"]),
            html.Td(cov, title=plain_words(cov_hover) or None)]))
    return html.Table([thead, html.Tbody(body)], className="book-table")


def fx_forwards_children(pos: Optional[dict], sources: Dict[str, List[str]], result: Dict[str, Any], error: str = "") -> List[Any]:
    """The FX forwards and options by currency (book-positions), the Net USD line in words, and
    the P&L the non-USD futures hold in their currency."""
    if error or not pos:
        return [html.P(missing_cell(error or "no currency figures"), className="section-kicker")]
    fx = pos.get("fx") or {}
    by_ccy = [c for c in fx.get("by_ccy") or [] if c.get("ccy") != "USD" and not c.get("metal")]
    metals = [c for c in fx.get("by_ccy") or [] if c.get("metal")]
    children: List[Any] = []
    if by_ccy:
        thead = html.Thead(html.Tr([html.Th("Currency", className="l"), html.Th("Delta (local)"), html.Th("Delta (USD)"),
                                    html.Th("From", className="l")]))
        body = []
        for c in by_ccy:
            ccy = str(c.get("ccy") or "")
            local, usd = _num(c.get("local_delta")), _num(c.get("usd_delta"))
            reason = str(c.get("reason") or "")
            rate = f"{c.get('label') or ''} {c.get('quoted'):,.4f}".strip() if _num(c.get("quoted")) is not None else ""
            body.append(html.Tr([
                html.Td(ccy, className="l", title=f"delta at the day's official spot{f' ({rate})' if rate else ''}"),
                html.Td(_delta_local_text(local, ccy), className=sign_class(local) or None,
                        title=f"{ccy} {f'{local:,.0f}'.replace('-', MINUS)}" if local is not None else plain_words(reason or "no local delta")),
                html.Td(money_cell(usd, reason=reason or "no USD delta", hover=rate)),
                html.Td(", ".join(sources.get(ccy) or []) or "FX legs", className="l grid-note",
                        title="what the trades on file contribute to this currency's delta (counted from the book)"),
            ]))
        children.append(html.Table([thead, html.Tbody(body)], className="book-table"))
    else:
        children.append(html.P(fx.get("reason") or "No open FX leg or FX option: no currency delta.", className="section-kicker"))
    net, gross = _num(fx.get("net_usd")), _num(fx.get("gross_usd"))
    line: List[Any] = [html.B("Net USD ")]
    if net is None:
        line.append(missing_cell(fx.get("reason") or "no FX net"))
    else:
        line.append(html.B(signed_money(net), className=sign_class(net) or None, title=full_money(net)))
        line.append(html.Span(f" {'long USD' if net > 0 else 'short USD' if net < 0 else 'flat'}"))
    if gross is not None:
        line.append(html.Span(f" · gross {short_money(gross, '$')}", title=full_money(gross)))
    for m in metals:
        units = _num(m.get("local_delta"))
        unit = {"XAU": "oz", "XAG": "oz", "XPT": "oz", "XPD": "oz"}.get(str(m.get("ccy")), "units")
        line.append(html.Span(f" · {m.get('ccy')} {size_words(units, unit) if units is not None else NA}, not in the net",
                              className="grid-note", title=m.get("reason") or "a metal's delta is reported apart: it is not "
                                                                                 "in the FX net or gross"))
    children.append(html.Div(line, className="fx-net-line",
                             title="The FX net USD delta, + = long USD, FX options' delta included; "
                                   "gross = the sum of |per-pair USD delta|."))
    if sources.get("__error__"):
        children.append(html.P(sources["__error__"][0], className="section-kicker"))
    held = result.get("currency_exposure") or {}
    if held:
        parts: List[Any] = [html.Span("P&L held in currency: ", className="grid-note")]
        for i, (ccy, e) in enumerate(held.items()):
            if i:
                parts.append(" · ")
            local, usd = _num(e.get("pnl_local")), _num(e.get("pnl_usd"))
            hover = (f"{ccy} {f'{local:,.0f}'.replace('-', MINUS)} = {full_money(usd)}" if local is not None and usd is not None
                     else e.get("reason") or "no P&L for this currency") + f"; contracts {', '.join(e.get('contracts') or [])}"
            parts.append(html.Span([f"{ccy} ", html.Span(_delta_local_text(local, ccy) if local is not None else NA,
                                                          className=sign_class(local) or "cell-missing")], title=plain_words(hover)))
        parts.append(html.Span(" (the non-USD futures' P&L, not currency delta)", className="grid-note"))
        children.append(html.Div(parts, className="fx-net-line curve-held-line"))
    return children


def fx_card(pos: Optional[dict], sources: Dict[str, List[str]], result: Dict[str, Any], error: str = "",
            strategies: Sequence[dict] = (), roots: Optional[Dict[str, Any]] = None) -> html.Div:
    head = html.Div(className="card-head", children=[
        about("Currency", FX_ABOUT, level="div", className="book-h"),
        html.Span("the non-USD legs and their hedge, then the FX forwards", className="book-counts")])
    children: List[Any] = [head]
    table = legs_table(currency_lines(result, strategies, roots or {}))
    if table is not None:
        children.append(table)
    children.append(html.Div("FX forwards and options by currency", className="curve-subhead"))
    children += fx_forwards_children(pos, sources, result, error)
    return html.Div(id=FX_EXPOSURE_ID, className="book-card card-pad", children=children)


# --------------------------------------------------------------------------- body, CSV, shell
def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """Every lane's output the tab reads, each in its own try: the spreads engine's strategies read
    once and handed to the curve for its leftover."""
    from ui.tabs.book import trades_on_file
    data: Dict[str, Any] = {"as_of": as_of, "n_trades": trades_on_file(conn), "roots": _roots()}
    try:
        from engine.spreads import book_spreads
        spreads, data["spreads_error"] = book_spreads(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001 -- the curve reads them itself and says why the leftover is missing
        spreads, data["spreads_error"] = None, f"the spreads could not be read ({type(exc).__name__}: {exc})"
    data["result"] = curve_positions(conn, as_of, spreads=spreads)
    data["strategies"] = list((spreads or {}).get("strategies") or [])
    option_ids = [r["contract_id"] for r in (data["result"].get("rows") or []) if _product(r) == OPTION and r.get("contract_id")]
    try:
        data["marks"], data["marks_error"] = _option_greek_marks(conn, as_of, option_ids), ""
    except sqlite3.Error as exc:
        data["marks"], data["marks_error"] = {}, f"the option Greeks could not be read ({exc})"
    try:
        from engine.ladder.positions import book_positions
        data["pos"], data["pos_error"] = book_positions(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001 -- the card says why
        data["pos"], data["pos_error"] = None, f"the currency exposure could not be built ({type(exc).__name__}: {exc})"
    data["sources"] = fx_sources(conn, as_of)
    return data


def all_issues(data: dict) -> List[Any]:
    result = data["result"]
    items: List[Any] = issue_items(result)
    seen = {r for r in result.get("reasons") or [] if r}
    if not result.get("available"):
        items.append("Curve positions unavailable.")
    for key in ("marks_error", "pos_error", "spreads_error"):
        if data.get(key):
            items.append(("Exposure", data[key]))
    for _key, sub in ordered_subsectors(result):
        name = str(sub.get("name") or _key)
        for field in ("units_note", "months_units_reason", "leftover_reason"):
            text = str(sub.get(field) or "")
            if not text or (field == "units_note" and sub.get("unit")) or "not commodity leftover" in text:
                continue
            fresh = [p for p in text.split("; ") if p and p not in seen]
            seen.update(fresh)
            if fresh:
                items.append((name, "; ".join(fresh) if field != "leftover_reason" else f"leftover: {'; '.join(fresh)}"))
    for line in greeks_lines(result, data.get("marks") or {}, data["roots"], (data.get("pos") or {}).get("fx_options")):
        if line["kind"] != "commodity":
            continue
        for col in ("delta", "gamma", "theta", "vega"):
            if line[col] is None:
                items.append((line["label"], f"no {col}: {line[f'{col}_hover']}"))
    for line in currency_lines(result, data.get("strategies") or [], data["roots"]):
        if line["hedge"].get("excl") and line["hedge"].get("why"):
            items.append((f"{line['ccy']} hedge", line["hedge"]["why"]))
    has_china, china = china_strategies(result, data.get("strategies") or [])
    if has_china:
        for s in china:
            if _num(s.get("hedge_coverage_net")) is None and s.get("hedge_reason"):
                items.append((f"Coverage, {_strategy_name(s)}", str(s["hedge_reason"])))
    fx = (data.get("pos") or {}).get("fx") or {}
    for c in fx.get("by_ccy") or []:
        if c.get("reason"):
            items.append((str(c.get("ccy")), str(c["reason"])))
    if fx.get("reason") and not fx.get("by_ccy"):
        items.append(("FX", str(fx["reason"])))
    for r in (data.get("sources") or {}).get("__error__") or []:
        items.append(("Sources", r))
    return items


def body(data: dict, unit: str = DEFAULT_UNIT) -> html.Div:
    if data.get("n_trades") == 0:                # no blotter loaded: the Book's card (user, 2026-09-28)
        from ui.tabs.book import empty_state
        return html.Div(className="curve-body", children=[empty_state(idx="curve")])
    result, roots = data["result"], data["roots"]
    unit = unit if unit in UNITS else DEFAULT_UNIT
    strategies = data.get("strategies") or []
    children: List[Any] = []
    if result.get("by_subsector"):
        children.append(tiles(result, roots, strategies))
        tilt = sector_tilt(result, roots)
        if tilt is not None:
            children.append(tilt)
        children.append(grid_section(result, unit, roots, data.get("as_of") or ""))
        children.append(html.Div(id=PANEL_ID, className="curve-panel-slot"))
    else:
        children.append(message_box(f"No commodity position to show: {result.get('note') or 'see the Data issues'}."))
    pos = data.get("pos") or {}
    cards: List[Any] = [fx_card(data.get("pos"), data.get("sources") or {}, result, data.get("pos_error", ""),
                                strategies, roots)]
    if greeks_lines(result, data.get("marks") or {}, roots, pos.get("fx_options")):
        cards.append(greeks_card(result, data.get("marks") or {}, roots, pos.get("fx_options")))
    children.append(html.Div(className="cards-row", children=cards))
    drawer = issues_drawer(all_issues(data), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
    return html.Div(className="curve-body", children=children)


def csv_frame(result: Dict[str, Any], unit: str, roots: Dict[str, Any], as_of: str = "") -> pd.DataFrame:
    """The grid as shown, one line per grid row, at full figures (the Later months each in their
    own column: the CSV hides nothing)."""
    unit = unit if unit in UNITS else DEFAULT_UNIT
    near, later = column_months(result, as_of)
    names = {k: str(v.get("name") or k) for k, v in (result.get("by_subsector") or {}).items()}
    records = []
    for line in grid_lines(result, unit, roots):
        phys = unit == "delta_lots" and line.get("no_months")
        rec = {"view": UNIT_LABELS[unit], "level": line["kind"], "commodity": names.get(line["key"], line["key"]),
               "line": line["label"], "exchange": line["sub"], "roots": " ".join(line["roots"]),
               "unit": line["unit"] if unit != "delta_usd" else "USD"}
        for k in near + later:
            rec[k] = None if phys else line["cells"].get(k, (None, ""))[0]
        rec["net"], rec["gross"] = line["net"].get("v"), line["gross"].get("v")
        rec["net_excluded"] = line["net"].get("excl") or 0
        rec["leftover"], rec["leftover_unit"] = line["leftover"].get("v"), line["leftover"].get("unit")
        records.append(rec)
    gross, net = book_totals(result, roots)
    records.append({"view": UNIT_LABELS[unit], "level": "book", "line": "Book (delta USD, FX hedges apart)", "unit": "USD",
                    "net": net["v"], "gross": gross["v"], "net_excluded": net["excl"]})
    return pd.DataFrame(records)


def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render(as_of: Optional[str], db_path, unit: str = DEFAULT_UNIT) -> Any:
    """The body for `as_of`, from one read-only connection closed straight after. A problem is a
    message where the body would be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        return body(gather(conn, as_of), unit or DEFAULT_UNIT)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("exposure tab failed for %s", as_of)
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Curve positions could not be computed for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()


def render_csv(as_of: Optional[str], db_path, unit: str):
    if not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        frame = csv_frame(curve_positions(conn, as_of), unit if unit in UNITS else DEFAULT_UNIT, _roots(), as_of)
        return dcc.send_data_frame(frame.to_csv, f"exposure-{as_of}.csv", index=False)
    except Exception:  # noqa: BLE001
        log.exception("exposure csv failed for %s", as_of)
        return None
    finally:
        conn.close()


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: the title line (the switch Physical | Lots | USD, Download CSV), the body
    the callback fills, the chosen commodity's store and the safety interval. No date picker."""
    return html.Div(className="curve-tab", children=[
        html.Div(className="book-title-row", children=[
            about("Exposure", TAB_ABOUT, level="h3"),
            dcc.RadioItems(id=UNIT_ID, className="book-switch",
                           options=[{"label": html.Span(UNIT_LABELS[u], title=UNIT_TIPS[u]), "value": u} for u in UNITS],
                           value=DEFAULT_UNIT, inline=True, persistence=True, persistence_type="session"),
            html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                        title="The grid as shown, at full figures, one line per grid row"),
            dcc.Download(id=DOWNLOAD_ID),
        ]),
        html.Div(id=BODY_ID, children=[message_box("Loading the exposure...")]),
        dcc.Store(id=SELECTED_ID, data=None),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def next_selection(current: Optional[str], clicked: str) -> Optional[str]:
    """A click on the open commodity closes its panel; any other opens that one."""
    return None if current == clicked else clicked


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body on the header's as-of, every data revision, the safety interval and the view
    switch; the chosen commodity from a row click; its panel; the CSV on its button."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        Input(UNIT_ID, "value"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, unit=DEFAULT_UNIT):
        return render(as_of, get_db_path(), unit or DEFAULT_UNIT)

    @app.callback(Output(SELECTED_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(SELECTED_ID, "data"), prevent_initial_call=True)
    def _select(_clicks, current):
        trig = dash.ctx.triggered_id
        fired = dash.ctx.triggered or []
        if not isinstance(trig, dict) or not (fired and fired[0].get("value")):
            return dash.no_update       # a re-rendered row comes back with n_clicks 0: not a click
        return next_selection(current, str(trig.get("idx") or ""))

    @app.callback(Output(PANEL_ID, "children"), Input(SELECTED_ID, "data"), Input(AS_OF_STORE_ID, "data"),
                  Input(DATA_REVISION_ID, "data"))
    def _panel(sub_key, as_of, _data_rev=None):
        return render_panel(sub_key, as_of, get_db_path())

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(UNIT_ID, "value"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, unit):
        if not n_clicks:
            return dash.no_update
        return render_csv(as_of, get_db_path(), unit or DEFAULT_UNIT)
