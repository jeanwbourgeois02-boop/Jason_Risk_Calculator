"""Exposure tab (the Curve tab until 2026-09-28): "What am I long or short, in which month?"
Rebuilt on 2026-09-28 (the screens tidy, wave 2; the approved mock is the brief's
Exposure.dc.html). Everything shown is `engine.curve.curve_positions`' output, the official
GAMMA / THETA / VEGA marks on file and `engine.ladder.positions.book_positions`, as given: nothing
here prices a trade, takes a delta or converts a currency (CLAUDE.md "Tabs as views"). Summing the
engine's per-contract figures into a month cell, a sector line or the Book line is display
arithmetic on figures the engine gave, and a line that leaves a figure out says "excl. N".

One screen:
  1. The title line: "Exposure", the question, the two-way switch Lots | USD (both delta-based:
     an option at its official DELTA in futures lots, a monthly-average contract by the pricing
     days left, an LME ticket at its tonnes over the lot size), the one-line note and Download CSV.
  2. The month grid, the hero: one row per contract root (its short name, the exchange and a
     non-USD currency small and grey after it), one column per contract month, then Net, Gross
     and Note, grouped by commodity across exchanges (since 2026-09-28, Jason's real export:
     copper = COMEX + LME, zinc = SHFE + LME; curve-positions' `by_subsector`): each group row
     carries the commodity's net exposure in one physical unit ("net long 35.6 t", the units
     note on hover), then "gross $X · net ±$Y" of delta USD, the known roots summed with
     "excl. N"; the SGX USD/CNH future is its own group, "FX hedges", never netted with a
     commodity; the Book line is every root's known delta USD summed. Cells are shaded green long / red short by the
     cell's rank by size within the view shown (`heat_colour`); a missing figure is an em dash
     with its reason on hover; every cell lists its contracts on hover.
  3. Two cards side by side: Option Greeks (one line per underlying commodity: the options' delta
     in futures lots, gamma, theta per day and vega per vol point, each summed only within one
     unit and scaled to the position, the option legs named small in the row; one line per FX
     option pair with its USD delta) and Currency exposure (one row per currency: delta local,
     delta USD, a From column naming the sources in words; then the Net USD line in words, with
     the gross and a metal named as not in the net; then the P&L the non-USD futures hold in
     their own currency, which is not currency delta).
  4. One Data issues drawer.

Display rules: `ui.tabs.formatting` (2026-09-28). The tab has no date picker and says no as-of of
its own: it follows the header's as-of store and re-renders in place on the data revision and its
safety interval. `layout(default_date)` (alias `build_layout`) and `register_callbacks(app,
get_db_path)` are the shell's interface.
"""
from __future__ import annotations

import bisect
import calendar
import logging
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import Input, Output, State, dcc, html

from engine.curve import curve_positions
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs.formatting import (
    MINUS, MISSING, about, contract_name, full_money, issues_drawer, lme_name, missing_cell, money_cell,
    short_date, short_money, short_root_name, sign_class, signed_money, size_words, sum_known,
)

FX_SECTOR = "fx"
FX_GROUP = "FX hedges"
_SECTOR_ORDER = ("energy", "metals", "agriculture", "ferrous")
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "curve-body"
REFRESH_ID = "curve-refresh"
UNIT_ID = "curve-unit"
GRID_ID = "curve-grid"
GREEKS_ID = "curve-greeks"
FX_EXPOSURE_ID = "curve-fx-exposure"
CSV_BUTTON_ID = "curve-csv"
DOWNLOAD_ID = "curve-download"
ISSUES_ID = "curve-issues"

NA = MISSING
FUTURE, OPTION, LME = "FUTURE", "CMDTY_OPTION", "LME_FWD"
UNITS = ("delta_lots", "delta_usd")
UNIT_LABELS = {"delta_lots": "Lots", "delta_usd": "USD"}
UNIT_TIPS = {"delta_lots": "Delta in futures-equivalent lots: a future as booked, an option at its official DELTA, "
                           "a monthly-average contract by the pricing days left, an LME ticket at its tonnes over the "
                           "lot size (curve-positions' delta lots).",
             "delta_usd": "The same delta in USD: delta lots × multiplier × the future's official price × spot, the "
                          "engine's per-contract figure (k / m, the full figure on hover)."}
DEFAULT_UNIT = "delta_lots"
NOTE_LINE = "delta lots: an option at its delta, a monthly-average contract by the days left"
QUESTION = "what am I long or short, in which month"
TAB_ABOUT = ("What am I long or short, in which month? One row per contract root, one column per contract month, every "
             "product at its delta (curve-positions), grouped by commodity across exchanges; each commodity's line "
             "carries its net exposure in one physical unit and its delta USD, the known figures summed. The Book line "
             "is the engine's delta USD summed. Under the grid the option Greeks and the currency exposure, as the "
             "engine gives them.")
GRID_ABOUT = ("Each month cell is the net delta of that contract month in the view shown, the engine's per-contract "
              "figures summed (the contracts on hover). Green long, red short, deeper for a larger position by its rank "
              "among the cells. A dash is a figure the engine could not give, its reason on hover; an empty cell holds "
              "no position. Net and Gross end the row; Note carries short markers with their sentences on hover.")
GREEKS_ABOUT = ("The open options on futures, one line per underlying commodity, scaled to the position: Delta is "
                "curve-positions' delta lots (lots × the option's official DELTA per lot, futures-equivalent lots); "
                "Gamma, Theta and Vega are the option's official per-lot marks on file for the day × its lots, in the "
                "contract's currency, so a line adds up figures of one unit only. A Greek with no mark is a dash with "
                "its reason, never zero. An FX option pair shows its USD delta (book-positions); its other Greeks are on "
                "the Trades tab, Options. Futures and LME prompts carry no gamma, theta or vega.")
FX_ABOUT = ("Delta by currency at the day's official spot (book-positions): FX forwards' and spot legs still to "
            "settle, FX options at their delta, an LME ticket's USD leg. The From column names the sources in words. "
            "Under the table: the FX net USD delta in words (long USD / short USD) with the gross beside it, a metal "
            "named as not in the net, and the P&L the non-USD futures hold in their own currency, which is not "
            "currency delta.")
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


# --------------------------------------------------------------------------- the grid rows
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
    """{month key: (value or None, hover)} of one commodity's month cells: delta lots are the
    engine's `delta_months` as they stand; delta USD is the commodity's rows' `delta_usd` summed
    per month. A month holding a row with no figure is None with the rows' reasons."""
    rows = [r for r in (result.get("rows") or []) if r.get("root_id") == root_id]
    by_month: Dict[str, List[dict]] = {}
    for r in rows:
        key = _row_month(r)
        if key is not None:
            by_month.setdefault(key, []).append(r)
    c = (result.get("by_commodity") or {}).get(root_id, {})
    engine_map = c.get("delta_months") or {}
    out: Dict[str, Tuple[Optional[float], str]] = {}
    for key in sorted(set(by_month) | (set(engine_map) if unit == "delta_lots" else set())):
        mine = by_month.get(key, [])
        lines = [_contract_line(r, unit, roots) for r in mine]
        if unit == "delta_lots":
            v = _num(engine_map.get(key)) if key in engine_map else None
            if v is None:
                gaps = [f"{plain_name(r, roots)}: {r.get('reason') or 'no delta'}" for r in mine if _num(r.get("delta_lots")) is None] \
                       or [c.get("delta_reason") or "no delta for this month"]
                out[key] = (None, "no delta: " + "; ".join(gaps) + (" | " + "; ".join(lines) if lines else ""))
                continue
        else:
            missing = [r for r in mine if _num(r.get("delta_usd")) is None]
            if missing:
                gaps = [f"{plain_name(r, roots)}: {r.get('reason') or 'no USD delta'}" for r in missing]
                out[key] = (None, "no USD delta: " + "; ".join(gaps) + (" | " + "; ".join(lines) if lines else ""))
                continue
            v = float(sum(float(r["delta_usd"]) for r in mine))
        out[key] = (v, "; ".join(lines) or f"{month_label(key)}: {lots_text(v)}")
    return out


def note_markers(c: Dict[str, Any], mine: List[dict], roots: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The Note cell of one commodity as (marker, sentence) pairs."""
    out: List[Tuple[str, str]] = []
    no_month = [plain_name(r, roots) for r in mine if _row_month(r) is None]
    if no_month:
        out.append((f"no month {len(no_month)}", f"no contract month for {', '.join(no_month)}: in the totals, in no month column"))
    if c.get("delta_missing"):
        out.append((f"no delta {len(c['delta_missing'])}", f"no delta for {', '.join(c['delta_missing'])}"))
    options = [r for r in mine if _product(r) == OPTION]
    if options:
        words = "; ".join(f"{plain_name(r, roots)} {lots_text(_num(r.get('delta_lots')))} delta lots" for r in options)
        out.append(("options at delta", f"options counted at their official DELTA in futures lots under the underlying's "
                                        f"month: {words}"))
    averaging = [plain_name(r, roots) for r in mine if _product(r) == FUTURE and r.get("note")]
    if averaging:
        out.append(("avg", f"monthly average: the delta shrinks through the pricing month: {', '.join(averaging)}"))
    prompts = [r for r in mine if _product(r) == LME]
    if prompts:
        words = "; ".join(f"{_num(r.get('units')) or 0:g} {r.get('unit') or 't'} on {short_date(r.get('expiry'))}" for r in prompts)
        out.append(("prompts", f"LME prompts at their tonnes over the lot size: {words}"))
    return out


def grid_rows(result: Dict[str, Any], unit: str, roots: Dict[str, Any]) -> List[dict]:
    """One row per commodity in the engine's order (sector, root), with its month cells in `unit`,
    its Net and Gross, its notes and its sector key."""
    rows_all = result.get("rows") or []
    out = []
    for root_id, c in (result.get("by_commodity") or {}).items():
        root = roots.get(root_id)
        mine = [r for r in rows_all if r.get("root_id") == root_id]
        cells = month_cells(result, root_id, unit, roots)
        ccy = str(c.get("currency") or "")
        net, gross, net_hover, gross_why, excl, excl_why = _net_gross(c, cells, unit)
        out.append({
            "root_id": root_id, "sector": str(c.get("sector") or ""), "name": short_root_name(root, root_id),
            "exchange": str(c.get("exchange") or ""), "currency": ccy,
            "name_hover": f"{c.get('name') or root_id} ({root_id}): " + "; ".join(_contract_line(r, unit, roots) for r in mine),
            "cells": cells, "net": net, "net_hover": net_hover, "gross": gross, "gross_hover": gross_why,
            "excl": excl, "excl_hover": excl_why, "notes": note_markers(c, mine, roots),
        })
    return out


def _net_gross(c: Dict[str, Any], cells: Dict[str, Tuple[Optional[float], str]], unit: str) -> tuple:
    """(net, gross, net hover, gross hover, months left out, why) of one commodity's Net and
    Gross cells: the engine's own figures when it has them; when a leg has no delta (an option
    not priced, a contract with no spot) the known month cells are summed instead (display, the
    header's rule) with how many months were left out and their reasons, never a blank."""
    pairs = list(cells.values())
    if unit == "delta_lots":
        net = _num(c.get("net_delta_lots"))
        if net is not None and all(v is not None for v, _h in pairs):
            return (net, float(sum(abs(v) for v, _h in pairs)), "the engine's net delta lots over every month",
                    "the month cells' absolute values added up (display)", 0, "")
    else:
        net, gross = _num(c.get("net_delta_usd")), _num(c.get("gross_delta_usd"))
        if net is not None and gross is not None:
            return net, gross, full_money(net), full_money(gross), 0, ""
    known_net, excl, reasons = sum_known(pairs)
    known_gross, _n, _r = sum_known([(None if v is None else abs(v), h) for v, h in pairs])
    why = (f"{excl} month{'s' if excl != 1 else ''} with no delta left out: " + "; ".join(dict.fromkeys(reasons))
           if excl else str(c.get("delta_reason") or "no delta"))
    if known_net is None:
        return None, None, why, why, excl, why
    what = "delta lots" if unit == "delta_lots" else "delta USD"
    hover = f"the known months' {what} summed (display, not the engine's figure); {why}"
    if unit == "delta_lots":
        return known_net, known_gross, hover, hover, excl, why
    return known_net, known_gross, f"{full_money(known_net)}: {hover}", f"{full_money(known_gross)}: {hover}", excl, why


def _usd_totals(commodities: Dict[str, Any], roots: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], List[str], str]:
    """(gross, net, the commodities left out, their reasons) over `by_commodity` entries: the
    engine's per-commodity delta USD, the known ones summed (display), never a blank."""
    pairs_g, pairs_n, left = [], [], []
    for root_id, c in commodities.items():
        g, n = _num(c.get("gross_delta_usd")), _num(c.get("net_delta_usd"))
        name = short_root_name(roots.get(root_id), root_id)
        why = f"{name}: {c.get('delta_reason') or 'no USD delta'}"
        pairs_g.append((g, why))
        pairs_n.append((n, why))
        if g is None or n is None:
            left.append(name)
    gross, _eg, reasons = sum_known(pairs_g)
    net, _en, _rn = sum_known(pairs_n)
    return gross, net, left, "; ".join(dict.fromkeys(reasons))


def _totals_children(gross: Optional[float], net: Optional[float], left: Sequence[str], why: str, names: bool = False) -> List[Any]:
    children: List[Any] = []
    if gross is None and net is None:
        children.append(missing_cell(why or "no USD delta"))
    else:
        children.append(html.Span([
            "gross ", html.B(short_money(gross, "$") if gross is not None else NA, title=full_money(gross) if gross is not None else why),
            " · net ", html.B(signed_money(net, "$") if net is not None else NA, className=sign_class(net) or None,
                              title=full_money(net) if net is not None else why)]))
    if left:
        short = f"excl. {len(left)}"
        if names:
            short += f": {', '.join(left[:2])}" + (f" +{len(left) - 2}" if len(left) > 2 else "")
        children.append(html.Span(short, className="marker", title=why))
    return children


def _units_words(value: Optional[float], unit: str) -> str:
    """'long 35.6 t', 'short 7.9m USD', 'flat': a net in physical units, rounded to a tenth."""
    if value is None:
        return NA
    if unit and len(unit) == 3 and unit.isupper() and unit.isalpha():
        return size_words(value, ccy=unit)
    return size_words(round(float(value), 1), unit or "units")


def subsector_words(sub: Dict[str, Any], result: Dict[str, Any], roots: Dict[str, Any]) -> Tuple[List[Any], str]:
    """The children of a commodity group line: the net in one physical unit ("net long 35.6 t",
    curve-positions' `net_units` with its `units_note` on hover; a dash with the note when the
    units do not add), then 'gross $3.08m · net +$2.2k' of delta USD over the commodity's roots
    (the engine's `by_commodity`, the known ones summed, an 'excl. N' marker otherwise), never
    blank (rule 2); and the hover."""
    mine = {r: c for r, c in (result.get("by_commodity") or {}).items() if r in set(sub.get("commodities") or [])}
    gross, net, left, why = _usd_totals(mine, roots)
    net_units, unit = _num(sub.get("net_units")), str(sub.get("unit") or "")
    note = str(sub.get("units_note") or "")
    children: List[Any] = []
    if net_units is None:
        children.append(html.Span(["net ", missing_cell(note or "no net in one unit")]))
    else:
        children.append(html.Span(f"net {_units_words(net_units, unit)}", className=sign_class(net_units) or None,
                                  title=note or f"the roots' units add: {unit}"))
    children.append(" · ")
    children += _totals_children(gross, net, left, why)
    hover = "; ".join(t for t in (note, why or "the roots' delta USD (the engine's) added up") if t)
    return children, hover


def _cell_td(value: Optional[float], hover: str, unit: str, magnitudes: List[float]) -> html.Td:
    if value is None:
        return html.Td(missing_cell(hover))
    colour = heat_colour(value, magnitudes)
    text = lots_text(value) if unit == "delta_lots" else signed_money(value)
    title = hover if unit == "delta_lots" else "\n".join(t for t in (full_money(value), hover) if t)
    style = {"backgroundColor": colour, "color": HEAT_INK} if colour else None
    return html.Td(text, title=title, style=style, className=None if colour else (sign_class(value) or None))


def commodity_tr(row: dict, months: Sequence[str], unit: str, magnitudes: List[float]) -> html.Tr:
    sub = row["exchange"] + (f" · {row['currency']}" if row["currency"] and row["currency"] != "USD" else "")
    name: List[Any] = [row["name"]]
    if sub.strip():
        name.append(html.Span(sub, className="name-sub"))
    cells: List[Any] = [html.Td(name, className="l book-name", title=row["name_hover"])]
    for key in months:
        v, hover = row["cells"].get(key, (None, ""))
        if key not in row["cells"]:
            cells.append(html.Td(""))
        else:
            cells.append(_cell_td(v, hover, unit, magnitudes))
    net, gross = row["net"], row["gross"]
    # A leg with no delta: the known months summed, "excl. N" beside the Net with the reasons on hover.
    excl = [html.Span(f"excl. {row['excl']}", className="marker", title=row["excl_hover"])] if row.get("excl") else []
    if net is None:
        cells.append(html.Td([missing_cell(row["net_hover"])] + excl))
    else:
        cells.append(html.Td([html.Span(lots_text(net) if unit == "delta_lots" else signed_money(net),
                                        className=sign_class(net) or None, title=row["net_hover"])] + excl,
                             style={"fontWeight": 600}))
    if gross is None:
        cells.append(html.Td(missing_cell(row["gross_hover"])))
    else:
        cells.append(html.Td(f"{gross:,.2f}".rstrip("0").rstrip(".") if unit == "delta_lots" else short_money(gross),
                             title=row["gross_hover"]))
    notes = []
    for i, (short, sentence) in enumerate(row["notes"]):
        if i:
            notes.append(" · ")
        notes.append(html.Span(short, className="marker", title=sentence))
    cells.append(html.Td(notes, className="l grid-note"))
    return html.Tr(cells, className="curve-row")


def subsector_tr(label: str, sub: Dict[str, Any], result: Dict[str, Any], roots: Dict[str, Any], span: int) -> html.Tr:
    children, why = subsector_words(sub, result, roots)
    return html.Tr([html.Td(label, className="l", title=f"{sub.get('name') or label}: {', '.join(sub.get('commodities') or [])}"),
                    html.Td(children, className="l", colSpan=span, title=why or None)],
                   className="book-group")


def subsector_groups(result: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """[(group label, by_subsector entry)] in the fixed sector order (energy, metals, agriculture,
    ferrous, then any other sector), the SGX USD/CNH future last under 'FX hedges'."""
    subs = result.get("by_subsector") or {}
    order = list(_SECTOR_ORDER) + sorted({str(v.get("sector") or "") for v in subs.values()} - set(_SECTOR_ORDER) - {FX_SECTOR})
    out: List[Tuple[str, Dict[str, Any]]] = []
    for sector in order:
        out += [(str(v.get("name") or k), v) for k, v in subs.items() if str(v.get("sector") or "") == sector]
    out += [(FX_GROUP, v) for v in subs.values() if str(v.get("sector") or "") == FX_SECTOR]
    return out


def book_tr(result: Dict[str, Any], roots: Dict[str, Any], span: int) -> html.Tr:
    """The Book line: every commodity's delta USD (the engine's) summed, 'excl. N' with the names
    when a commodity has no USD delta."""
    gross, net, left, why = _usd_totals(result.get("by_commodity") or {}, roots)
    return html.Tr([html.Td("Book", className="l"),
                    html.Td(_totals_children(gross, net, left, why, names=True), className="l", colSpan=span,
                            title="The commodities' delta USD (the engine's) added up; what is left out is named.")],
                   className="book-total")


def grid_section(result: Dict[str, Any], unit: str, roots: Dict[str, Any]) -> html.Div:
    unit = unit if unit in UNITS else DEFAULT_UNIT
    months = list(result.get("months") or [])
    rows = grid_rows(result, unit, roots)
    magnitudes = sorted(abs(v) for r in rows for v, _h in r["cells"].values() if v is not None and v != 0)
    head = html.Thead(html.Tr(
        [html.Th("Commodity", className="l", title="The commodity across exchanges as a group line with its net exposure; "
                                                   "under it each contract root's short name, exchange and non-USD "
                                                   "currency, the contracts on hover.")]
        + [html.Th(month_label(k), title=f"contract month {k}, in {UNIT_LABELS[unit].lower()} of delta") for k in months]
        + [html.Th("Net", title="The commodity's net delta over every month (the engine's)."),
           html.Th("Gross", title="Lots: the month cells' absolute values added up (display). USD: the engine's gross delta USD."),
           html.Th("Note", className="l", title="Short markers, their sentences on hover.")]))
    body: List[Any] = []
    span = len(months) + 3
    by_root = {r["root_id"]: r for r in rows}
    placed: set = set()
    for label, sub in subsector_groups(result):
        body.append(subsector_tr(label, sub, result, roots, span))
        for root_id in sub.get("commodities") or []:
            if root_id in by_root and root_id not in placed:
                body.append(commodity_tr(by_root[root_id], months, unit, magnitudes))
                placed.add(root_id)
    left = [r for r in rows if r["root_id"] not in placed]
    if left:   # a root the engine put in no subsector: still shown, never dropped
        body.append(html.Tr([html.Td("Other", className="l"), html.Td("", colSpan=span)], className="book-group"))
        body.extend(commodity_tr(r, months, unit, magnitudes) for r in left)
    body.append(book_tr(result, roots, span))
    table = html.Table([head, html.Tbody(body)], id=GRID_ID, className="book-table curve-grid")
    return html.Div(className="book-card", children=[table], title=None)


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
            pairs["delta"].append((dl, f"{name}: {r.get('reason') or 'no DELTA mark'}" if dl is None else ""))
            for col, mt in GREEK_MARK_TYPES:
                per_lot = marks.get((cid, mt))
                if per_lot is None or lots is None:
                    pairs[col].append((None, f"{name}: no {mt} mark on file for this date" if lots is not None
                                       else f"{name}: {r.get('reason') or 'no lots'}"))
                else:
                    pairs[col].append((per_lot * lots, ""))
        for col in pairs:
            total, excluded, reasons = sum_known(pairs[col])
            line[col] = None if excluded else total
            line[f"{col}_hover"] = ("; ".join(reasons) if excluded else
                                    ("lots × the option's official DELTA per lot, futures-equivalent lots" if col == "delta"
                                     else f"the per-lot {col.upper()} mark × lots, in {ccy or 'the contract currency'}"))
        lines.append(line)
    for pair, usd in sorted(((fx_options or {}).get("by_pair") or {}).items()):
        note = "an FX option's gamma, theta and vega are on the Trades tab, Options; not summed here"
        lines.append({"label": f"{pair} options", "legs": "FX options at delta", "ccy": "USD", "kind": "fx",
                      "hover": f"the open FX options on {pair}, their USD delta as book-positions gives it",
                      "delta": _num(usd), "delta_hover": "USD delta of the pair's open FX options (book-positions)"
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
    return html.Td(children, className=sign_class(value) or None, title=hover)


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
        html.Th("Delta", title="Options on futures: futures-equivalent lots (lots × the official DELTA per lot). FX options: USD delta."),
        html.Th("Gamma", title="Per-lot GAMMA mark × lots, in the contract's currency, per 1.0 move of the future."),
        html.Th("Theta / day", title="Per-lot THETA mark × lots, in the contract's currency, per calendar day."),
        html.Th("Vega / vol pt", title="Per-lot VEGA mark × lots, in the contract's currency, per vol point.")]))
    body = []
    for line in lines:
        name: List[Any] = [line["label"], html.Span(line["legs"], className="name-sub")]
        cells: List[Any] = [html.Td(name, className="l book-name", title=line["hover"])]
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


def fx_card(pos: Optional[dict], sources: Dict[str, List[str]], result: Dict[str, Any], error: str = "") -> html.Div:
    head = html.Div(className="card-head", children=[
        about("Currency exposure", FX_ABOUT, level="div", className="book-h"),
        html.Span("FX hedges, FX options at delta", className="book-counts")])
    if error or not pos:
        return html.Div(id=FX_EXPOSURE_ID, className="book-card card-pad", children=[
            head, html.P(missing_cell(error or "book-positions gave nothing"), className="section-kicker")])
    fx = pos.get("fx") or {}
    by_ccy = [c for c in fx.get("by_ccy") or [] if c.get("ccy") != "USD" and not c.get("metal")]
    metals = [c for c in fx.get("by_ccy") or [] if c.get("metal")]
    children: List[Any] = [head]
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
                        title=f"{ccy} {f'{local:,.0f}'.replace('-', MINUS)}" if local is not None else (reason or "no local delta")),
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
                             title="The FX net USD delta, + = long USD, FX options' delta included (book-positions); "
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
                                                          className=sign_class(local) or "cell-missing")], title=hover))
        parts.append(html.Span(" (the non-USD futures' P&L, not currency delta)", className="grid-note"))
        children.append(html.Div(parts, className="fx-net-line"))
    return html.Div(id=FX_EXPOSURE_ID, className="book-card card-pad", children=children)


# --------------------------------------------------------------------------- body, CSV, shell
def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """Every lane's output the tab reads, each in its own try."""
    from ui.tabs.book import trades_on_file
    data: Dict[str, Any] = {"as_of": as_of, "n_trades": trades_on_file(conn), "result": curve_positions(conn, as_of),
                            "roots": _roots()}
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
    if not result.get("available"):
        items.append("Curve positions unavailable.")
    for key in ("marks_error", "pos_error"):
        if data.get(key):
            items.append(("Exposure", data[key]))
    for line in greeks_lines(result, data.get("marks") or {}, data["roots"], (data.get("pos") or {}).get("fx_options")):
        if line["kind"] != "commodity":
            continue
        for col in ("delta", "gamma", "theta", "vega"):
            if line[col] is None:
                items.append((line["label"], f"no {col}: {line[f'{col}_hover']}"))
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
    children: List[Any] = []
    if result.get("by_commodity"):
        children.append(grid_section(result, unit, roots))
    else:
        children.append(message_box(f"No commodity position to show: {result.get('note') or 'see the Data issues'}."))
    pos = data.get("pos") or {}
    children.append(html.Div(className="cards-row", children=[
        greeks_card(result, data.get("marks") or {}, roots, pos.get("fx_options")),
        fx_card(data.get("pos"), data.get("sources") or {}, result, data.get("pos_error", "")),
    ]))
    drawer = issues_drawer(all_issues(data), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
    return html.Div(className="curve-body", children=children)


def csv_frame(result: Dict[str, Any], unit: str, roots: Dict[str, Any]) -> pd.DataFrame:
    months = list(result.get("months") or [])
    records = []
    group_of = {rid: label for label, sub in subsector_groups(result) for rid in (sub.get("commodities") or [])}
    for r in grid_rows(result, unit, roots):
        rec = {"commodity": group_of.get(r["root_id"], "Other"), "sector": r["sector"], "contract_root": r["name"],
               "root_id": r["root_id"], "exchange": r["exchange"], "currency": r["currency"], "view": UNIT_LABELS[unit]}
        for k in months:
            rec[k] = r["cells"].get(k, (None, ""))[0]
        rec["net"], rec["gross"] = r["net"], r["gross"]
        rec["note"] = " · ".join(s for s, _h in r["notes"])
        records.append(rec)
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
        frame = csv_frame(curve_positions(conn, as_of), unit if unit in UNITS else DEFAULT_UNIT, _roots())
        return dcc.send_data_frame(frame.to_csv, f"exposure-{as_of}.csv", index=False)
    except Exception:  # noqa: BLE001
        log.exception("exposure csv failed for %s", as_of)
        return None
    finally:
        conn.close()


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: the title line (the question, the Lots | USD switch, the note, Download
    CSV), the body the callback fills and the safety interval. No date picker."""
    return html.Div(className="curve-tab", children=[
        html.Div(className="book-title-row", children=[
            about("Exposure", TAB_ABOUT, level="h3"),
            html.Span(QUESTION, className="book-counts"),
            dcc.RadioItems(id=UNIT_ID, className="book-switch",
                           options=[{"label": html.Span(UNIT_LABELS[u], title=UNIT_TIPS[u]), "value": u} for u in UNITS],
                           value=DEFAULT_UNIT, inline=True, persistence=True, persistence_type="session"),
            html.Span(NOTE_LINE, className="book-counts", title=UNIT_TIPS["delta_lots"]),
            html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                        title="The grid as shown, at full figures, one line per commodity"),
            dcc.Download(id=DOWNLOAD_ID),
        ]),
        html.Div(id=BODY_ID, children=[message_box("Loading the exposure...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body on the header's as-of, every data revision, the safety interval and the view
    switch; the CSV on its button."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        Input(UNIT_ID, "value"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, unit=DEFAULT_UNIT):
        return render(as_of, get_db_path(), unit or DEFAULT_UNIT)

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(UNIT_ID, "value"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, unit):
        if not n_clicks:
            return dash.no_update
        return render_csv(as_of, get_db_path(), unit or DEFAULT_UNIT)
