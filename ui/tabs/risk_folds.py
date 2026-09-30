"""The Risk tab's folds (Phase G round 2b, 2026-09-29; the design doc's "Risk folds"): Net by
commodity (with the month grid and the commodity's curves on a click: the Exposure tab's grid and
curve, moved here when Exposure merged into Risk), Currency, Stress and the research Price check.
Each is folded, opens on a click of its title (the open folds are a session store, so they survive
a refresh and a tab switch) and follows the shared filter: it reads only the trades showing.

What is read, never recomputed:
  - `engine.curve.curve_positions` (`blotter_pricing.shared_curve`): each open contract's delta in
    lots, physical units and USD, and the trades on it. The fold sums the rows of the trades
    showing per commodity and per sign (display); a contract two trades share is counted only
    when both are showing, else left out with its reason ("excl. N"): the engine does not split
    a contract's delta per trade. "USD per 1 % move" is the engine's own `usd_per_pct` per row;
  - `engine.spreads.trade_book` (`shared_trade_book`): the leftover per leg (USD per 1 % move),
    the currency hedge per trade (exposure, hedge, coverage) and the legs' USD values;
  - `engine.stress.commodity_stress` on the same positions and spreads: each scenario's P&L per
    contract, summed over the contracts of the trades showing; the hardest-hit trade is the
    trade whose contracts lose most (contracts two trades share are not attributed);
  - `engine.risk.commodity_history.research_price_check`, through `trade_risk`'s `price_check`.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dash import dcc, html

from ui.tabs import trade_filter as tf
from ui.tabs.formatting import (
    cap, cap_parts, plain_ids, MINUS, MISSING, about, format_cell, km_cell, km_text, marker, missing_cell, pct_text, plain_words, price_text,
    short_date, sum_known, contract_name, is_fx_pair, quoted_unit, count_text,
)

log = logging.getLogger(__name__)

NA = MISSING
FUTURE, OPTION, LME = "FUTURE", "CMDTY_OPTION", "LME_FWD"
FX_SECTOR = "fx"
SECTOR_ORDER = ("energy", "metals", "agriculture", "ferrous", "softs", "livestock")
CHINA_CCYS = ("CNY", "CNH")
STRESS_SHOWN = 5

FOLD_TYPE = "risk-fold"                  # a fold's title: {"type", "idx": fold key}
COMMODITY_ROW_TYPE = "risk-commodity-row"   # a commodity row of the Net fold: {"type", "idx": subsector}
UNIT_TYPE = "risk-grid-unit"             # the month grid's unit switch: {"type", "idx": "grid"}
FOLD_NET, FOLD_CCY, FOLD_STRESS, FOLD_PRICE, FOLD_STRESS_MORE = "net", "currency", "stress", "price", "stress-more"
UNITS = (("lots", "Lots"), ("usd", "USD"), ("physical", "Physical"))
DEFAULT_UNIT = "lots"

NET_ABOUT = ("The book's exposure by commodity across exchanges, from the trades showing: long and short in USD per 1 % "
             "move of the price (the engine's USD per 1 % move; lots and physical units on hover), net, the part of "
             "the net that no spread covers (leftover) and gross. Click a commodity for its months and curves.")
GRID_ABOUT = ("One row per exchange of the commodity, one column per contract month held (months more than 12 months "
              "out added under Later), then Net. Every product at its delta: an option at its official delta, an LME "
              "ticket in its prompt month. Pale green long, pale red short; a dash has its reason on hover.")
CCY_ABOUT = ("One row per currency the trades showing hold other than USD (CNY and CNH together): the USD value of the "
             "legs priced in it (full size), the currency hedge against them, what is left unhedged and the coverage "
             "(amber under 80 % or over 150 %). Only the China legs have a hedge convention (a long China leg is "
             "hedged by a short USD/CNH position); other currencies show their exposure only.")
STRESS_ABOUT = ("Each scenario of the stress file applied to the trades showing, first order on today's delta (an option "
                "at its delta), worst first: the P&L of the rows showing and the trade hit hardest. Designed scenarios "
                "are placeholders until Jason sets his own; a replay is today's book over a past window of the research "
                "history.")
PRICE_ABOUT = ("Whether the research app's prices, which every risk figure here is drawn from, are the same market as ours: "
               "per commodity our latest official price (or the average fill before a pull) against the research "
               "app's settle, flagged beyond 20 % (2 % for the USD/CNH rate, which converts every China trade's history).")
PLACEHOLDER_WORDS = ("placeholder: the stress file holds stand-in scenarios until Jason sets his own "
                     "(docs/open-questions.md)")
KIND_WORDS = {"outright": "Designed", "curve": "Designed", "spread": "Designed", "replay": "Historical replay",
              "fx": "Currency"}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _lines(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _row_month(row: Dict[str, Any]) -> Optional[str]:
    if row.get("year") is None or row.get("month") is None:
        return None
    return f"{int(row['year']):04d}-{int(row['month']):02d}"


def month_words(key: str) -> str:
    """'2026-12' -> 'Dec 26'."""
    try:
        year, month = (int(p) for p in key.split("-")[:2])
        return f"{calendar.month_abbr[month]} {year % 100:02d}"
    except (ValueError, IndexError):
        return key


def lots_text(v: Optional[float]) -> str:
    """'+10', '−3', '+5.2', '0'."""
    if v is None:
        return NA
    body = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    if float(body.replace(",", "") or 0) == 0:
        return "0"
    return (MINUS if v < 0 else "+") + body


def units_text(v: Optional[float], unit: str) -> str:
    if v is None:
        return NA
    a = abs(v)
    body = f"{a:,.0f}" if a >= 100 else f"{a:,.1f}".rstrip("0").rstrip(".")
    if body in ("0", ""):
        return f"0 {unit}".strip()
    return f"{MINUS if v < 0 else '+'}{body} {unit}".strip()


def exchange_name(root, root_id: str) -> str:
    """'SHFE Zinc', 'COMEX Copper': the exchange and the commodity."""
    try:
        from engine.spreads.trades import leg_name
        return leg_name(root, "") or root_id
    except Exception:  # noqa: BLE001 -- the id stands for the name
        return root_id


def root_labels(rids: Sequence[str], roots: Dict[str, Any]) -> Dict[str, str]:
    """{root_id: 'ICE Natural gas'}; two roots that read the same get their code ('ICE Natural gas
    (TFM)')."""
    names = {rid: exchange_name(roots.get(rid), rid) for rid in rids}
    seen: Dict[str, int] = {}
    for n in names.values():
        seen[n] = seen.get(n, 0) + 1
    return {rid: (f"{n} ({rid.split(':', 1)[-1]})" if seen[n] > 1 else n) for rid, n in names.items()}


def fold(key: str, title: str, count: Any, hover: str, is_open: bool, body: Any) -> html.Div:
    """A fold of the tab: a title line (click: open / close; the open folds are the session store's)
    with its count or one-line figure beside it, the body under it only when open."""
    head = html.Div([html.Span("▾ " if is_open else "▸ ", className="tk-chev"),
                     html.Span(title, className="risk-fold-title", title=plain_words(hover)),
                     html.Span(count, className="risk-fold-count") if count not in (None, "") else None],
                    id={"type": FOLD_TYPE, "idx": key}, n_clicks=0, className="risk-fold-head")
    return html.Div([head, html.Div(body, className="risk-fold-body") if is_open else None],
                    className="book-card risk-fold" + (" risk-fold--open" if is_open else ""))


# --------------------------------------------------------------------------- whose rows
def names_of(row_trade_ids: Sequence[Any], name_of_fill: Dict[str, str]) -> List[str]:
    """The trade names a curve row (or a contract) belongs to, from its fills."""
    return sorted({name_of_fill.get(str(t), tf.UNASSIGNED) for t in row_trade_ids or []})


def row_in_view(names: Sequence[str], ctx: dict) -> Tuple[bool, bool]:
    """(counted, left out): a row is counted when every trade on it is showing; left out (said so)
    when only some are; neither when none is."""
    if not ctx["filtered"]:
        return True, False
    shown = ctx["shown_names"]
    inside = [n for n in names if n in shown]
    if not inside:
        return False, False
    if len(inside) == len(names):
        return True, False
    return False, True


def _shared_reason(names: Sequence[str], ctx: dict) -> str:
    others = [n for n in names if n not in ctx["shown_names"]]
    return (f"also held by {', '.join(others)}, which is not showing: the engine does not split a contract's "
            "figure per trade")


# --------------------------------------------------------------------------- Net by commodity
def _subsector_order(curve: dict) -> List[str]:
    subs = curve.get("by_subsector") or {}
    keys = list(subs)
    rank = {s: n for n, s in enumerate(SECTOR_ORDER)}

    def key(k):
        sector = str((subs.get(k) or {}).get("sector") or "")
        return (sector == FX_SECTOR, rank.get(sector, 50), keys.index(k))
    return sorted(keys, key=key)


def _curve_rows(ctx: dict) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """(the curve rows counted, [(row, why)] left out) under the filter."""
    counted, left = [], []
    for r in (ctx.get("curve") or {}).get("rows") or []:
        names = names_of(r.get("trade_ids") or [], ctx["name_of_fill"])
        inside, partial = row_in_view(names, ctx)
        if inside:
            counted.append(r)
        elif partial:
            left.append((r, _shared_reason(names, ctx)))
    return counted, left


def _leftover_by_sub(ctx: dict) -> Dict[str, dict]:
    """{subsector: {parts [(usd per 1 %, why)], lines [hover lines]}} from the showing trades'
    leftover legs (`trade_book`)."""
    sub_of = {str(r.get("contract_id")): str(r.get("subsector") or "") for r in (ctx.get("curve") or {}).get("rows") or []}
    out: Dict[str, dict] = {}
    for t in ctx["shown"]:
        for leg in (t.get("leftover") or {}).get("legs") or []:
            sub = sub_of.get(str(leg.get("contract_id")), "")
            if not sub:
                continue
            slot = out.setdefault(sub, {"parts": [], "lines": []})
            usd = _num(leg.get("usd_per_1pct"))
            slot["parts"].append((usd, f"{t.get('trade')}: {leg.get('reason') or 'no USD figure'}"))
            phys = _num(leg.get("physical"))
            slot["lines"].append(f"{t.get('trade')}: {leg.get('name')} {lots_text(_num(leg.get('lots')))} lots"
                                 + (f", {units_text(phys, str(leg.get('physical_unit') or ''))}" if phys is not None else "")
                                 + (f", {km_text(usd)} USD per 1 %" if usd is not None else ""))
    return out


def _side_sums(rows: Sequence[dict]) -> dict:
    """Long, short, net and gross of the rows in USD per 1 % move (the engine's `usd_per_pct` of
    each row, summed by sign: display), each with its excluded count and reasons; lots and
    physical per sign for the hover."""
    longs, shorts, whys = [], [], []
    for r in rows:
        d = _num(r.get("usd_per_pct"))
        if d is None:
            whys.append(f"{r.get('contract_id')}: {r.get('usd_per_pct_reason') or r.get('reason') or 'no USD figure (no price or conversion)'}")
            continue
        (longs if d > 0 else shorts).append(d)
    n = len(whys)
    long_v = float(sum(longs)) if longs or not n else None
    short_v = float(sum(shorts)) if shorts or not n else None
    if long_v is None and short_v is None:
        return {"long": None, "short": None, "net": None, "gross": None, "excl": n, "why": whys}
    lv, sv = long_v or 0.0, short_v or 0.0
    return {"long": lv, "short": sv, "net": lv + sv, "gross": abs(lv) + abs(sv), "excl": n, "why": whys}


def _physical_lines(rows: Sequence[dict], roots: Dict[str, Any], sign: int) -> List[str]:
    """Per exchange, the lots and physical units of the rows on one side (sign +1 long, -1 short)."""
    per: Dict[str, List[float]] = {}
    units: Dict[str, str] = {}
    for r in rows:
        lots = _num(r.get("delta_lots"))
        if lots is None or (lots > 0) != (sign > 0) or not lots:
            continue
        rid = str(r.get("root_id") or "")
        acc = per.setdefault(rid, [0.0, 0.0, 1.0])
        acc[0] += lots
        u = _num(r.get("delta_units"))
        if u is None:
            acc[2] = 0.0
        else:
            acc[1] += u
        units[rid] = str(r.get("unit") or "")
    return [f"{exchange_name(roots.get(rid), rid)}: {lots_text(v[0])} lots"
            + (f", {units_text(v[1], units[rid])}" if v[2] else "") for rid, v in per.items()]


def commodity_lines(ctx: dict) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """One line per commodity (subsector) the showing trades hold, fixed sector order, the FX hedges
    last; each with its exchange lines when it trades on two or more."""
    curve = ctx.get("curve") or {}
    subs = curve.get("by_subsector") or {}
    roots = ctx.get("roots") or {}
    counted, left = _curve_rows(ctx)
    leftover = _leftover_by_sub(ctx)
    out = []
    for key in _subsector_order(curve):
        rows = [r for r in counted if str(r.get("subsector") or "") == key]
        if not rows:
            continue
        sub = subs.get(key) or {}
        line = {"key": key, "name": str(sub.get("name") or key), "sector": str(sub.get("sector") or ""),
                "rows": rows, **_side_sums(rows),
                "long_lines": _physical_lines(rows, roots, 1), "short_lines": _physical_lines(rows, roots, -1),
                "trades": sorted({n for r in rows for n in names_of(r.get("trade_ids"), ctx["name_of_fill"])})}
        lo = leftover.get(key)
        if line["sector"] == FX_SECTOR:
            line["leftover"] = (None, 0, ["an FX hedge: not commodity leftover"], [])
        elif lo:
            v, n, r = sum_known(lo["parts"])
            line["leftover"] = (v, n, r, lo["lines"])
        else:
            line["leftover"] = (0.0, 0, [], [])
        order = [c.get("root_id") for c in sub.get("split") or []]
        by_root: Dict[str, List[dict]] = {}
        for r in rows:
            by_root.setdefault(str(r.get("root_id") or ""), []).append(r)
        rids = [x for x in order if x in by_root] + [x for x in by_root if x not in order]
        labels = root_labels(rids, roots)
        line["exchanges"] = ([{"root_id": rid, "name": labels[rid], **_side_sums(by_root[rid]),
                               "long_lines": _physical_lines(by_root[rid], roots, 1),
                               "short_lines": _physical_lines(by_root[rid], roots, -1)} for rid in rids]
                             if len(rids) > 1 else [])
        out.append(line)
    return out, left


def _usd_td(v: Optional[float], excl: int, whys: Sequence[str], hover_lines: Sequence[str] = (),
            signed: bool = True, colour: bool = False, amber: bool = False) -> html.Td:
    if v is None:
        return html.Td(missing_cell(_lines(*list(dict.fromkeys(whys))[:8]) or "no USD figure"))
    hover = _lines(*hover_lines)
    cell = km_cell(v, hover=hover, signed=signed, colour=colour, className="cell-amber" if amber else "")
    return html.Td([cell, marker(f"excl. {excl}", _lines(*list(dict.fromkeys(whys))[:8]), "marker--small")
                    if excl else None])


def _net_tr(line: dict, selected: bool, sub: bool = False) -> html.Tr:
    lo_v, lo_n, lo_r, lo_lines = line.get("leftover") or (None, 0, [], [])
    name_cell: List[Any] = ([html.Span("▾ " if selected else "▸ ", className="tk-chev")] if not sub else []) + [
        html.Span(line["name"], className="tk-name" if not sub else None,
                  title=plain_words(f"Trades: {', '.join(line.get('trades') or [])}") if line.get("trades") else None)]
    if sub:
        lo_td = html.Td("")
    elif lo_v is None:
        lo_td = html.Td(missing_cell(_lines(*lo_r) or "no leftover figure"))
    else:
        lo_td = _usd_td(lo_v, lo_n, lo_r, lo_lines or ["nothing left over: every lot is in a spread"],
                        amber=abs(lo_v) >= 0.5)
    tds = [html.Td(name_cell, className="l" + (" tk-indent" if sub else "")),
           _usd_td(line["long"], line["excl"], line["why"], line.get("long_lines") or []),
           _usd_td(line["short"], line["excl"], line["why"], line.get("short_lines") or []),
           _usd_td(line["net"], line["excl"], line["why"], colour=False),
           lo_td,
           _usd_td(line["gross"], line["excl"], line["why"], signed=False)]
    if sub:
        return html.Tr(tds, className="tk-leg")
    return html.Tr(tds, id={"type": COMMODITY_ROW_TYPE, "idx": line["key"]}, n_clicks=0,
                   className="tk-row" + (" tk-row--open" if selected else ""))


NET_COLUMNS = (("name", "Commodity", "l", "The commodity across its exchanges (click for its months and curves)."),
               ("long", "Long", "", "The long contracts' delta in USD per 1 % move of their price; lots and physical "
                                    "units per exchange on hover."),
               ("short", "Short", "", "The short contracts' delta in USD per 1 % move; lots and physical on hover."),
               ("net", "Net", "", "Long plus short: what 1 % on the commodity's price is worth to the trades showing."),
               ("leftover", "Of which leftover", "", "The part of the net that belongs to no spread (the spreads "
                                                     "engine's leftover legs), USD per 1 %; amber when not zero."),
               ("gross", "Gross", "", "|Long| + |short|, USD per 1 %."))


def net_table(lines: Sequence[dict], selected: Optional[str]) -> html.Table:
    head = html.Thead(html.Tr([html.Th(t, className=c or None, title=plain_words(h)) for _k, t, c, h in NET_COLUMNS]))
    body: List[Any] = []
    for line in lines:
        body.append(_net_tr(line, selected == line["key"]))
        for ex in line.get("exchanges") or []:
            body.append(_net_tr(ex, False, sub=True))
    return html.Table([head, html.Tbody(body)], className="book-table book-grid tk-table risk-net-table")


def net_summary(lines: Sequence[dict]) -> str:
    net = [(line["net"], "") for line in lines if line["sector"] != FX_SECTOR]
    v, n, _r = sum_known(net)
    return f"{_plural(len(lines), 'commodity').replace('commoditys', 'commodities')}" + (
        f" · net {km_text(v)} per 1 %" if v is not None else "")


# --------------------------------------------------------------------------- the month grid
def _months(rows: Sequence[dict], as_of: str) -> Tuple[List[str], List[str]]:
    keys = sorted({m for m in (_row_month(r) for r in rows) if m})
    try:
        d = dt.date.fromisoformat(as_of)
        cutoff = f"{d.year + (d.month + 11) // 12:04d}-{(d.month + 11) % 12 + 1:02d}"
    except ValueError:
        cutoff = "9999-12"
    near = [k for k in keys if k <= cutoff]
    return near, [k for k in keys if k > cutoff]


def _unit_value(r: dict, unit: str) -> Tuple[Optional[float], str]:
    if unit == "usd":
        return _num(r.get("usd_per_pct")), (r.get("usd_per_pct_reason") or r.get("reason")
                                             or "no USD figure (no price or conversion)")
    if unit == "physical":
        return _num(r.get("delta_units")), (r.get("reason") or "no physical figure")
    return _num(r.get("delta_lots")), (r.get("reason") or "no delta in lots")


def _cell(rows: Sequence[dict], unit: str) -> Tuple[Optional[float], str, List[str], int]:
    """(the known figures of the rows in `unit` summed, None when none is known; why a row has none;
    the contracts in it; how many rows are left out of the sum): a display sum with its "Excl. N"."""
    total, whys, names, known = 0.0, [], [], 0
    for r in rows:
        v, why = _unit_value(r, unit)
        names.append(f"{plain_ids(str(r.get('contract_id') or ''))}: {lots_text(_num(r.get('delta_lots')))} delta lots")
        if v is None:
            whys.append(f"{plain_ids(str(r.get('contract_id') or ''))}: {why}")
        else:
            total += v
            known += 1
    return (total if known else None), "\n".join(whys), names, len(whys)


def grid_cells(ctx: dict, sub_key: str, unit: str) -> Tuple[List[dict], List[str], List[str]]:
    """([{root_id, name, unit_word, cells {month: (value, why, names)}, later, net}], near months, later months)
    for one commodity, the trades showing."""
    counted, _left = _curve_rows(ctx)
    rows = [r for r in counted if str(r.get("subsector") or "") == sub_key]
    roots = ctx.get("roots") or {}
    near, later = _months(rows, ctx["as_of"])
    order: List[str] = []
    for r in rows:
        rid = str(r.get("root_id") or "")
        if rid not in order:
            order.append(rid)
    out = []
    labels = root_labels(order, roots)
    for rid in order:
        mine = [r for r in rows if str(r.get("root_id") or "") == rid]
        word = {"usd": "USD per 1 %", "lots": "lots"}.get(unit) or str(mine[0].get("unit") or "")
        cells = {m: _cell([r for r in mine if _row_month(r) == m], unit) for m in near}
        cells = {m: c for m, c in cells.items() if c[2]}
        late_rows = [r for r in mine if (_row_month(r) or "") in later]
        out.append({"root_id": rid, "name": labels[rid], "unit_word": word, "cells": cells,
                    "later": _cell(late_rows, unit) if late_rows else None, "net": _cell(mine, unit)})
    return out, near, later


def _grid_text(v: float, unit: str, word: str) -> str:
    if unit == "usd":
        return km_text(v)
    if unit == "physical":
        return units_text(v, "")
    return lots_text(v)


def _grid_td(cell: Optional[tuple], unit: str, word: str) -> html.Td:
    if cell is None:
        return html.Td("")
    v, why, names, n_excl = (tuple(cell) + (0,))[:4]
    if v is None:
        return html.Td(missing_cell(why))
    cls = "risk-grid-long" if v > 0 else ("risk-grid-short" if v < 0 else "")
    # money in full is the cell itself (2026-09-30): its hover names the unit; a quantity keeps its figure
    full = "" if unit == "usd" else (lots_text(v) if unit == "lots" else count_text(v))
    return html.Td([html.Span(_grid_text(v, unit, word),
                              title=plain_words(_lines(f"{full} {word}".strip(), *names[:8]))),
                    marker(f"Excl. {n_excl}", _lines(f"Excludes {_plural(n_excl, 'contract')} with no figure", why),
                           "marker--small") if n_excl else None], className=cls or None)


def month_grid(ctx: dict, sub_key: str, unit: str) -> html.Div:
    lines, near, later = grid_cells(ctx, sub_key, unit)
    switch = dcc.RadioItems(id={"type": UNIT_TYPE, "idx": "grid"}, className="book-switch",
                            options=[{"label": lb, "value": v} for v, lb in UNITS], value=unit, inline=True)
    name = str(((ctx.get("curve") or {}).get("by_subsector") or {}).get(sub_key, {}).get("name") or sub_key)
    head_cells = [html.Th("Exchange", className="l")] + [html.Th(month_words(m)) for m in near]
    if later:
        head_cells.append(html.Th("Later", title=plain_words(f"Months more than 12 months out: "
                                                             f"{', '.join(month_words(m) for m in later)}")))
    head_cells += [html.Th("Net"), html.Th("Unit", className="l")]
    body = []
    for line in lines:
        tds = [html.Td(line["name"], className="l tk-name")] + [_grid_td(line["cells"].get(m), unit, line["unit_word"])
                                                                for m in near]
        if later:
            tds.append(_grid_td(line["later"], unit, line["unit_word"]))
        tds.append(_grid_td(line["net"], unit, line["unit_word"]))
        tds.append(html.Td(line["unit_word"], className="l tk-sub"))
        body.append(html.Tr(tds))
    table = (html.Table([html.Thead(html.Tr(head_cells)), html.Tbody(body)],
                        className="book-table book-grid tk-table risk-grid-table")
             if lines else html.Div(missing_cell("no position of the trades showing in this commodity"), className="risk-quiet"))
    return html.Div(className="risk-grid", children=[
        html.Div([about(f"{name} by month", GRID_ABOUT, level="span", className="tk-title"), switch], className="tk-strip"),
        html.Div(table, className="tk-table-slot")])


# --------------------------------------------------------------------------- the commodity's curves (from Exposure)
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
    """One root's futures curve on `as_of` from `marks_official` as it stands: [{x ('YYYY-MM'),
    label, price, previous, previous_date, held, why}] for every contract of the root on file that
    is held, or not expired with an official price on the day or an earlier close. Nothing is
    estimated or filled: a held contract with no price is a gap with its reason."""
    from ui.tabs.formatting import parse_contract_id
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
    """An LME metal's curve on `as_of` from `marks_official` (cash at the day, the outrights at their
    prompt dates) and the latest earlier day's curve as the previous close."""
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
    return month_words(key)


def _bar_range(values: Sequence[float]) -> Optional[List[float]]:
    if not values:
        return None
    low, high = min(0.0, min(values)), max(0.0, max(values))
    pad = (high - low) * 0.15 or 1.0
    return [low - (pad if low < 0 else 0.0), high + (pad if high > 0 else 0.0)]


def curve_figure(points: List[dict], bars: Dict[str, Tuple[Optional[float], str]], bar_unit: str, as_of: str):
    """One exchange's curve on a category axis of contract months (an LME metal: its prompt dates):
    our official price of the day (a gap where missing), the previous close dashed, the positions of
    the trades showing as bars on the right-hand axis."""
    import plotly.graph_objects as go
    by_month: Dict[str, str] = {}
    for p in points:
        if p["held"] and p["x"][:7] not in by_month:
            by_month[p["x"][:7]] = p["x"]
    bar_x = {k: by_month.get(k, k) for k in bars}
    keys = sorted(set(p["x"] for p in points) | set(bar_x.values()))
    labels = {k: _x_label(k) for k in keys}
    fig = go.Figure()
    known = [(bar_x[k], v) for k, (v, _h) in sorted(bars.items()) if v is not None]
    if known:
        fig.add_trace(go.Bar(
            x=[labels[k] for k, _v in known], y=[v for _k, v in known], yaxis="y2", name=f"position ({bar_unit})",
            marker=dict(color=["rgba(26,127,75,0.35)" if v >= 0 else "rgba(192,57,43,0.35)" for _k, v in known]),
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
    fig.update_layout(height=240, margin=dict(t=30, b=30, l=56, r=56),
                      legend=dict(orientation="h", y=1.18, x=0, font=dict(size=10.5)),
                      xaxis=dict(type="category", categoryorder="array", categoryarray=[labels[k] for k in keys],
                                 showgrid=False),
                      yaxis=dict(showgrid=True, gridcolor="#eef0f3", title=dict(text="price as quoted", font=dict(size=11))),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, zeroline=True, zerolinecolor="#c9ced6",
                                  range=_bar_range([v for _k, v in known]),
                                  title=dict(text=f"position ({bar_unit})", font=dict(size=11))),
                      plot_bgcolor="#fff", paper_bgcolor="#fff")
    return fig


def curve_charts(conn: sqlite3.Connection, ctx: dict, sub_key: str, unit: str) -> html.Div:
    """Each exchange's curve of the commodity from our official marks, the positions of the trades
    showing as bars (in the grid's unit)."""
    from ui.tabs.formatting import quoted_unit
    lines, near, later = grid_cells(ctx, sub_key, unit)
    counted, _left = _curve_rows(ctx)
    roots = ctx.get("roots") or {}
    charts: List[Any] = []
    for line in lines:
        rid = line["root_id"]
        mine = [r for r in counted if str(r.get("root_id") or "") == rid and str(r.get("subsector") or "") == sub_key]
        bars = {m: (c[0], c[1]) for m, c in line["cells"].items()}
        for r in mine:
            m = _row_month(r)
            if m in later:
                bars.setdefault(m, _cell([x for x in mine if _row_month(x) == m], unit)[:2])
        try:
            if any((r.get("product") or FUTURE) == LME for r in mine):
                points = lme_points(conn, ctx["as_of"], rid,
                                    sorted({str(r.get("expiry") or "") for r in mine if (r.get("product") or FUTURE) == LME}))
            else:
                held = {str(r.get("contract_id")): r for r in mine if (r.get("product") or FUTURE) == FUTURE}
                for r in mine:
                    if (r.get("product") or "") == OPTION and r.get("underlying_id"):
                        held.setdefault(str(r["underlying_id"]), {k: v for k, v in r.items() if k != "expiry"})
                points = futures_points(conn, ctx["as_of"], rid, held)
        except sqlite3.Error as exc:
            points = []
            log.warning("risk: curve of %s could not be read: %s", rid, exc)
        root = roots.get(rid)
        title = [line["name"], html.Span(f" · {quoted_unit(root) or 'price as quoted'}", className="tk-sub")]
        part: Any = (dcc.Graph(figure=curve_figure(points, bars, line["unit_word"], ctx["as_of"]),
                               config={"displayModeBar": False}, style={"height": "240px"}) if points
                     else html.Div(missing_cell(f"no official price on file for {line['name']} on or before {ctx['as_of']}"),
                                   className="risk-quiet"))
        charts.append(html.Div(className="risk-curve-chart", children=[html.Div(title, className="book-h"), part]))
    return html.Div(charts, className="risk-curve-charts")


def net_fold(conn: sqlite3.Connection, ctx: dict, is_open: bool, selected: Optional[str], unit: str) -> html.Div:
    lines, left = commodity_lines(ctx)
    count = net_summary(lines) if lines else "nothing showing"
    body: List[Any] = []
    if is_open:
        if not lines:
            body.append(html.Div(missing_cell("no commodity position among the trades showing"), className="risk-quiet"))
        else:
            body.append(html.Div(net_table(lines, selected), className="tk-table-slot"))
        if left:
            body.append(html.Div(f"{_plural(len(left), 'contract')} left out: held by a trade that is not showing as well",
                                 className="risk-quiet",
                                 title=plain_words(_lines(*(f"{r.get('contract_id')}: {why}" for r, why in left)))))
        if selected and any(line["key"] == selected for line in lines):
            body.append(month_grid(ctx, selected, unit))
            try:
                body.append(curve_charts(conn, ctx, selected, unit))
            except Exception as exc:  # noqa: BLE001 -- the grid still shows
                log.exception("risk: curve charts failed for %s", selected)
                body.append(html.Div(missing_cell(f"the curves could not be drawn ({type(exc).__name__}: {exc})"),
                                     className="risk-quiet"))
    return fold(FOLD_NET, "Net by commodity", count, NET_ABOUT, is_open, body)


# --------------------------------------------------------------------------- Currency
def currency_lines(ctx: dict) -> List[dict]:
    """One line per non-USD currency of the showing trades (CNY and CNH as one)."""
    per: Dict[str, dict] = {}
    for t in ctx["shown"]:
        if t.get("pseudo"):
            continue
        name = str(t.get("trade") or "")
        h = t.get("hedge") or {}
        hcur = str(h.get("currency") or "")
        china_legs = [lg for lg in t.get("legs") or [] if not lg.get("hedge") and lg.get("status") == "open"
                      and str(lg.get("currency") or "") in CHINA_CCYS]
        if china_legs or hcur in CHINA_CCYS:
            slot = per.setdefault("CNY", {"ccy": "CNY / CNH", "china": True, "exp": [], "hedge": [], "un": [], "cov": [],
                                          "trades": []})
            exp, hed = _num(h.get("exposure_usd")), _num(h.get("hedge_usd"))
            why = str(h.get("reason") or "")
            slot["exp"].append((exp, f"{name}: {why or 'no CNY exposure figure'}"))
            if h.get("present"):
                slot["hedge"].append((hed, f"{name}: {why or 'no USD figure for the hedge'}"))
            un = None if exp is None else exp + (hed or 0.0) if (hed is not None or not h.get("present")) else None
            slot["un"].append((un, f"{name}: {why or 'no figure'}"))
            cov = _num(h.get("coverage"))
            slot["cov"].append((name, cov, why, h.get("exposure_basis") or ""))
            slot["trades"].append(f"{name}: exposure {km_text(exp)}, hedge {km_text(hed) if h.get('present') else 'none'}"
                                  + (f" ({h.get('exposure_basis')})" if h.get("exposure_basis") else ""))
        others: Dict[str, List[dict]] = {}
        for lg in t.get("legs") or []:
            ccy = str(lg.get("currency") or "")
            if lg.get("hedge") or lg.get("status") != "open" or ccy in ("", "USD") or ccy in CHINA_CCYS:
                continue
            others.setdefault(ccy, []).append(lg)
        for ccy, legs in others.items():
            slot = per.setdefault(ccy, {"ccy": ccy, "china": False, "exp": [], "hedge": [], "un": [], "cov": [], "trades": []})
            v, n, r = sum_known((_num(lg.get("value_usd")), f"{name} {lg.get('name')}: {lg.get('value_reason') or 'no value'}")
                                for lg in legs)
            slot["exp"].append((v, "; ".join(r) or f"{name}: no value"))
            if hcur == ccy and h.get("present"):
                slot["hedge"].append((_num(h.get("hedge_usd")), f"{name}: {h.get('reason') or 'no USD figure'}"))
            slot["trades"].append(f"{name}: {km_text(v)} USD in {ccy}")
    out = []
    for key in sorted(per, key=lambda k: (k != "CNY", k)):
        s = per[key]
        line = {"ccy": s["ccy"], "china": s["china"], "trades": s["trades"],
                "exposure": sum_known(s["exp"]), "hedge": sum_known(s["hedge"]) if s["hedge"] else (0.0, 0, []),
                "has_hedge": bool(s["hedge"])}
        if s["china"]:
            line["unhedged"] = sum_known(s["un"])
            line["coverage"] = s["cov"]
        else:
            line["unhedged"] = (None, 0, [f"not added for {s['ccy']}: the hedge convention is set for the China legs "
                                          "only (a long China leg against a short USD/CNH position)"])
            line["coverage"] = []
        out.append(line)
    return out


def _coverage_td(covs: Sequence[tuple], china: bool, ccy: str) -> html.Td:
    if not china:
        return html.Td(missing_cell(f"coverage is read for the China legs only, not {ccy}"))
    known = [(n, c) for n, c, _w, _b in covs if c is not None]
    lines = [f"{n}: {pct_text(c) + ' hedged' if c is not None else 'no coverage (' + (w or 'not given') + ')'}"
             + (f", {b}" if c is not None and b else "") for n, c, w, b in covs]
    if not known:
        return html.Td(missing_cell(_lines(*lines) or "no coverage"))
    amber = any(c < 0.8 or c > 1.5 for _n, c in known)
    if len(known) == 1:
        text = pct_text(known[0][1])
    else:
        lo, hi = min(c for _n, c in known), max(c for _n, c in known)
        text = f"{pct_text(lo)} to {pct_text(hi)}"
    return html.Td(html.Span(text, className="cell-amber" if amber else None,
                             title=plain_words(_lines(*lines, "never an average of the trades' ratios"))))


CCY_COLUMNS = (("ccy", "Currency", "l", "The currency the legs are priced in (CNY and CNH as one)."),
               ("exposure", "Exposure", "", "The USD value of the legs priced in it, full size (the China legs: the "
                                            "engine's CNY exposure the hedge is measured against)."),
               ("hedge", "Hedge", "", "The currency hedges of the trades showing, USD notional (a short USD/CNH is "
                                      "minus)."),
               ("unhedged", "Unhedged", "", "Exposure plus hedge (China legs only)."),
               ("coverage", "Coverage", "", "Minus the hedge over the exposure, per trade (100 % = fully hedged); "
                                            "several trades show their range, each on hover; amber under 80 % or "
                                            "over 150 %."))


def currency_fold(ctx: dict, is_open: bool, conn: Optional[sqlite3.Connection] = None) -> html.Div:
    lines = currency_lines(ctx)
    china = next((line for line in lines if line["china"]), None)
    count: Any = (f"{len(lines)} {'currency' if len(lines) == 1 else 'currencies'}" if lines else "USD only")
    if china and china["unhedged"][0] is not None:
        count = f"{count} · CNY / CNH unhedged {km_text(china['unhedged'][0])}"
    body: List[Any] = []
    if is_open:
        if not lines:
            body.append(html.Div("Every leg of the trades showing is priced in USD.", className="risk-quiet"))
        else:
            head = html.Thead(html.Tr([html.Th(t, className=c or None, title=plain_words(h)) for _k, t, c, h in CCY_COLUMNS]))
            rows = []
            for line in lines:
                ex_v, ex_n, ex_r = line["exposure"]
                h_v, h_n, h_r = line["hedge"]
                u_v, u_n, u_r = line["unhedged"]
                rows.append(html.Tr([
                    html.Td(html.Span(line["ccy"], className="tk-name", title=plain_words(_lines(*line["trades"]))),
                            className="l"),
                    _usd_td(ex_v, ex_n, ex_r),
                    _usd_td(h_v, h_n, h_r) if line["has_hedge"] else html.Td(html.Span("No hedge", className="tk-sub",
                                                                                       title="No currency hedge in "
                                                                                             "these trades")),
                    _usd_td(u_v, u_n, u_r),
                    _coverage_td(line["coverage"], line["china"], line["ccy"])]))
            body.append(html.Div(html.Table([head, html.Tbody(rows)], className="book-table book-grid tk-table risk-ccy-table"),
                                 className="tk-table-slot"))
        if conn is not None:
            pos, err = book_positions(conn, ctx["as_of"])
            body.append(fx_positions_block(pos, err, ctx["filtered"]))
    return fold(FOLD_CCY, "Currency", count, CCY_ABOUT, is_open, body)


# --------------------------------------------------------------------------- FX forwards (the Currency fold)
FX_POS_ABOUT = ("The whole book's currency delta at the day's official spot (book-positions): FX forwards' and spot legs "
                "still to settle, FX options at their delta, an LME ticket's USD leg. Net USD is the USD position "
                "(+ = long USD), gross the sum of each pair's USD delta. A futures leg is not currency delta: it is in "
                "the table above. Not split per trade.")
METAL_UNITS = {"XAU": "oz", "XAG": "oz", "XPT": "oz", "XPD": "oz"}


def book_positions(conn: sqlite3.Connection, as_of: str) -> Tuple[Optional[dict], str]:
    """(`engine.ladder.positions.book_positions`, why None): the FX net / gross USD delta, the
    delta by currency and the FX options' USD delta by pair, once per database revision."""
    from ui.tabs.blotter_pricing import screen_memo
    try:
        from engine.ladder.positions import book_positions as build
        from ui.tabs.blotter_pricing import raw_value_book, shared_curve, shared_spreads

        def compute():
            # the shared valuation, curve and spreads the tab already holds: no date valued twice (S6)
            return build(conn, as_of, value_fn=raw_value_book, curve=shared_curve(conn, as_of),
                         spreads=shared_spreads(conn, as_of))
        return screen_memo("risk-book-positions", conn, as_of, compute), ""
    except Exception as exc:  # noqa: BLE001 -- the fold says why
        log.exception("risk: book positions failed for %s", as_of)
        return None, f"the currency delta could not be built ({type(exc).__name__}: {exc})"


def _local_text(v: Optional[float]) -> str:
    if v is None:
        return NA
    text = km_text(v)
    return text


def fx_positions_block(pos: Optional[dict], error: str, filtered: bool) -> html.Div:
    """The FX forwards and options by currency: one row per currency (local delta, USD delta), then
    the Net USD line in words with the gross beside it and any metal reported apart. The engine's
    `fx.net_usd` is already the USD position (+ = long USD: book-positions negates
    `portfolio_totals`' net non-USD delta once); shown as it is, never negated here."""
    head = html.Div([about("FX forwards and options", FX_POS_ABOUT, level="span", className="risk-fold-title"),
                     html.Span(" · whole book, not split per trade" if filtered else " · whole book",
                               className="risk-fold-count")], className="risk-subhead")
    if error or not pos:
        return html.Div([head, html.Div(missing_cell(error or "no currency delta"), className="risk-quiet")])
    fx = pos.get("fx") or {}
    by_ccy = [c for c in fx.get("by_ccy") or [] if c.get("ccy") != "USD" and not c.get("metal")]
    kids: List[Any] = [head]
    rows = []
    for c in by_ccy:
        ccy = str(c.get("ccy") or "")
        local, usd = _num(c.get("local_delta")), _num(c.get("usd_delta"))
        reason = plain_words(c.get("reason") or "")
        rate = (f"{c.get('label') or ''} {_num(c.get('quoted')):,.4f}".strip()
                if _num(c.get("quoted")) is not None else "")
        rows.append(html.Tr([
            html.Td(html.Span(ccy, className="tk-name", title=f"At the day's official spot{f' ({rate})' if rate else ''}"),
                    className="l"),
            html.Td(html.Span(_local_text(local), title=(f"The delta in {ccy}" if local is not None
                                                         else reason or "no local delta"))),
            html.Td(km_cell(usd, reason=reason or "no USD delta", hover=rate, colour=False)),
        ]))
    # a metal's delta is reported apart: its own row, in ounces, outside the net and gross
    for m in fx.get("metals") or []:
        units = _num(m.get("units"))
        unit = METAL_UNITS.get(str(m.get("ccy")), "units")
        why = plain_words(m.get("reason") or "a metal's delta is reported apart: not in the FX net or gross")
        rows.append(html.Tr([
            html.Td(html.Span(str(m.get("ccy") or ""), className="tk-name", title=cap(why)), className="l"),
            html.Td(html.Span(f"{count_text(units)} {unit}", title=cap(why)) if units is not None
                    else missing_cell(why)),
            html.Td(html.Span("Not in the net", className="tk-sub", title=cap(why)))]))
    if not rows:
        rows.append(html.Tr([html.Td(cap(plain_words(fx.get("reason") or "") or "No open FX forward, spot leg or FX "
                                     "option: no currency delta beyond the futures legs above"),
                                     className="l tk-sub", colSpan=3)]))
    # the totals: Net USD (+ = long USD, the engine's sign, never negated here) and gross
    net, gross = _num(fx.get("net_usd")), _num(fx.get("gross_usd"))
    net_words = ("" if net is None else "Long USD" if net > 0.5 else "Short USD" if net < -0.5 else "Flat")
    net_cell = (missing_cell(plain_words(fx.get("reason") or "") or "no FX net") if net is None
                else html.Span(km_text(net), title="Long USD when plain, short USD in brackets; FX options' delta included"))
    totals = [html.Tr([html.Td("Net USD", className="l"), html.Td(net_words, className="tk-sub"), html.Td(net_cell)])]
    if gross is not None:
        totals.append(html.Tr([html.Td("Gross", className="l"), html.Td(""),
                               html.Td(html.Span(km_text(gross, signed=False),
                                                 title="The sum of each pair's |USD delta|"))]))
    headrow = html.Thead(html.Tr([html.Th("Currency", className="l"),
                                  html.Th("Delta (local)", title="The currency's delta in its own units."),
                                  html.Th("Delta (USD)", title="The same at the day's official spot.")]))
    kids.append(html.Div(html.Table([headrow, html.Tbody(rows), html.Tfoot(totals)],
                                    className="book-table book-grid tk-table tk-small risk-fx-table"),
                         className="tk-table-slot"))
    return html.Div(kids, className="risk-fx-block")


# --------------------------------------------------------------------------- Option Greeks
FOLD_GREEKS = "greeks"
GREEKS_ABOUT = ("The open options, one line per underlying commodity, scaled to the position: Delta the delta lots (lots x "
                "the option's official delta per lot, futures-equivalent lots); Gamma, Theta and Vega the option's "
                "official per-lot marks x its lots, in the contract's currency. A Greek with no mark is a dash with its "
                "reason, never zero. An FX option pair shows its USD delta (its other Greeks are on the Blotter's "
                "Options sub-tab).")
GREEK_MARK_TYPES = (("gamma", "GAMMA"), ("theta", "THETA"), ("vega", "VEGA"))


def option_rows(ctx: dict) -> Tuple[List[dict], List[Tuple[dict, str]]]:
    """(the curve's option rows of the trades showing, [(row, why)] left out)."""
    counted, left = _curve_rows(ctx)
    return ([r for r in counted if str(r.get("product") or "") == OPTION],
            [(r, w) for r, w in left if str(r.get("product") or "") == OPTION])


def fx_options_held(conn: sqlite3.Connection, as_of: str) -> int:
    """How many FX option trades are on file and not expired on `as_of` (only to show the fold)."""
    try:
        (n,) = conn.execute("SELECT COUNT(*) FROM trades t JOIN instruments i USING (instrument_id) "
                            "WHERE t.product = 'FX_OPTION' AND t.trade_date <= ? AND i.expiry_date >= ?",
                            (as_of, as_of)).fetchone()
        return int(n or 0)
    except sqlite3.Error:
        return 0


def greek_marks(conn: sqlite3.Connection, as_of: str, instruments: Sequence[str]) -> Dict[Tuple[str, str], float]:
    """{(instrument, mark type): value}: the official GAMMA / THETA / VEGA marks on `as_of`, as they are."""
    ids = sorted({str(i) for i in instruments if i})
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT instrument_id, mark_type, value FROM marks_official WHERE as_of_date = ? "
        f"AND mark_type IN ('GAMMA','THETA','VEGA') AND instrument_id IN ({','.join('?' * len(ids))})",
        (as_of, *ids)).fetchall()
    return {(str(i), str(mt)): float(v) for i, mt, v in rows if _num(v) is not None}


def greeks_lines(ctx: dict, marks: Dict[Tuple[str, str], float], fx_options: Optional[dict]) -> List[dict]:
    """One line per underlying commodity of the options showing (each Greek summed over the options
    that have it: a line with one missing is a dash with the reasons, never a partial figure), then
    one per FX option pair (its USD delta, whole book)."""
    from ui.tabs.formatting import contract_name, short_root_name, size_words
    rows, _left = option_rows(ctx)
    roots = ctx.get("roots") or {}
    by_root: Dict[str, List[dict]] = {}
    for r in rows:
        by_root.setdefault(str(r.get("root_id") or ""), []).append(r)
    lines = []
    for root_id, rs in by_root.items():
        root = roots.get(root_id)
        ccy = str(rs[0].get("currency") or "")
        legs = ", ".join(f"{contract_name(r.get('contract_id'), root, root_id)} {size_words(_num(r.get('lots')), capital=False)}" for r in rs)
        line = {"label": short_root_name(root, root_id), "legs": legs, "ccy": ccy, "kind": "commodity",
                "hover": f"{_plural(len(rs), 'option')} on {root_id}: {legs}"}
        pairs: Dict[str, list] = {k: [] for k in ("delta", "gamma", "theta", "vega")}
        for r in rs:
            cid = str(r.get("contract_id") or "")
            lots, dl = _num(r.get("lots")), _num(r.get("delta_lots"))
            name = contract_name(cid, root, root_id)
            pairs["delta"].append((dl, f"{name}: {plain_words(r.get('reason')) or 'no delta mark'}"))
            for col, mt in GREEK_MARK_TYPES:
                per_lot = marks.get((cid, mt))
                if per_lot is None or lots is None:
                    pairs[col].append((None, f"{name}: no {mt.lower()} mark on file for this date" if lots is not None
                                       else f"{name}: {r.get('reason') or 'no lots'}"))
                else:
                    pairs[col].append((per_lot * lots, ""))
        for col, items in pairs.items():
            total, excluded, reasons = sum_known(items)
            line[col] = None if excluded else total
            line[f"{col}_hover"] = (_lines(*reasons) if excluded else
                                    ("lots x the option's official delta per lot, futures-equivalent lots" if col == "delta"
                                     else f"the per-lot {col} mark x lots, in {ccy or 'the contract currency'}"))
        lines.append(line)
    for pair, usd in sorted(((fx_options or {}).get("by_pair") or {}).items()):
        note = "an FX option's gamma, theta and vega are on the Blotter's Options sub-tab; not summed here"
        v = _num(usd)
        lines.append({"label": f"{pair} options", "legs": "FX options at delta, whole book", "ccy": "USD", "kind": "fx",
                      "hover": f"the open FX options on {pair}, their USD delta as the book gives it",
                      "delta": v, "delta_hover": "USD delta of the pair's open FX options" if v is not None
                      else ((fx_options or {}).get("reason") or "no delta"),
                      "gamma": None, "gamma_hover": note, "theta": None, "theta_hover": note, "vega": None,
                      "vega_hover": note, "delta_usd": True})
    return lines


def _greek_td(value: Optional[float], hover: str, unit: str, money: bool = False, decimals: int = 2) -> html.Td:
    if value is None:
        return html.Td(missing_cell(hover))
    if money:
        return html.Td(km_cell(value, hover=hover, colour=False))
    text = f"{abs(value):,.{decimals}f}" if decimals else f"{abs(value):,.0f}"
    if float(text.replace(",", "") or 0) == 0:
        text = "0"
    else:
        text = (MINUS if value < 0 else "+") + text
    return html.Td([text, html.Span(unit, className="cell-unit") if unit else None], title=plain_words(hover))


def greeks_fold(conn: sqlite3.Connection, ctx: dict, is_open: bool, n_options: int) -> Optional[html.Div]:
    """The Option Greeks fold, only when an option is held (None otherwise)."""
    if not n_options:
        return None
    count = _plural(n_options, "option") + " held"
    body: List[Any] = []
    if is_open:
        rows, left = option_rows(ctx)
        pos, err = book_positions(conn, ctx["as_of"]) if fx_options_held(conn, ctx["as_of"]) else (None, "")
        try:
            marks = greek_marks(conn, ctx["as_of"], [r.get("contract_id") for r in rows])
        except sqlite3.Error as exc:
            marks = {}
            body.append(html.Div(missing_cell(f"the option Greeks could not be read ({exc})"), className="risk-quiet"))
        lines = greeks_lines(ctx, marks, (pos or {}).get("fx_options"))
        if err:
            body.append(html.Div(missing_cell(err), className="risk-quiet"))
        if not lines:
            body.append(html.Div("No option among the trades showing.", className="risk-quiet"))
        else:
            head = html.Thead(html.Tr([
                html.Th("Underlying", className="l"),
                html.Th("Delta", title="Options on futures: futures-equivalent lots (lots x the official delta per lot). "
                                       "FX options: USD delta."),
                html.Th("Gamma", title="The gamma mark per lot x lots, in the contract's currency, per 1.0 move of the "
                                       "future."),
                html.Th("Theta / day", title="The theta mark per lot x lots, in the contract's currency, per calendar day."),
                html.Th("Vega / vol pt", title="The vega mark per lot x lots, in the contract's currency, per vol point.")]))
            trs = []
            for line in lines:
                cells: List[Any] = [html.Td([html.Span(line["label"], className="tk-name"), " ",
                                             html.Span(line["legs"], className="tk-sub")], className="l",
                                            title=plain_words(line["hover"]))]
                if line.get("delta_usd"):
                    cells.append(_greek_td(line["delta"], line["delta_hover"], "", money=True))
                else:
                    cells.append(_greek_td(line["delta"], line["delta_hover"], "lots"))
                for col in ("gamma", "theta", "vega"):
                    cells.append(_greek_td(line[col], line[f"{col}_hover"], line["ccy"], decimals=2 if col == "gamma" else 0))
                trs.append(html.Tr(cells))
            body.append(html.Div(html.Table([head, html.Tbody(trs)], className="book-table book-grid tk-table tk-greeks"),
                                 className="tk-table-slot"))
        if left:
            body.append(html.Div(missing_cell(_lines(*(f"{r.get('contract_id')}: {w}" for r, w in left))),
                                 className="risk-quiet"))
    return fold(FOLD_GREEKS, "Option Greeks", count, GREEKS_ABOUT, is_open, body)


# --------------------------------------------------------------------------- Stress
def shown_positions(ctx: dict) -> Dict[str, str]:
    """{position_id: trade name} of the rows showing (the trade book's position id, else the risk
    row's for a position with no trade name)."""
    out: Dict[str, str] = {}
    for t, r in ctx.get("shown_rows") or []:
        pid = str(t.get("position_id") or (r or {}).get("position_id") or "")
        if pid:
            out[pid] = str(t.get("trade") or "")
    return out


def _stress_by_position(e: dict, s: dict, ctx: dict, shown_pids: Dict[str, str]) -> None:
    """A positional scenario's P&L of the rows showing: the engine's `by_position` (a shared
    contract already split by each trade's own quantity) summed over the positions showing
    (display); unfiltered the whole book, the contracts the engine could not attribute to one
    position added (so the line is the scenario's total). A position with a contract the scenario
    could not price is "excl."; the hardest hit is the worst position showing, named."""
    bp = s.get("by_position")
    det = s.get("by_position_detail") or {}
    names = det.get("names") or {}
    filtered = ctx["filtered"]
    if bp is None:
        why = det.get("reason") or "the scenario's P&L per position is not known"
        if filtered:
            e["why"] = [f"not split per trade: {why}"]
        else:
            e["total"] = _num(s.get("total_usd"))
            e["why"] = [s.get("reason") or "no figure"] if e["total"] is None else []
        e["hit_reason"] = f"not split per trade: {why}"
        return
    keep = [pid for pid in bp if (not filtered or pid in shown_pids)]
    parts = [_num(bp[pid]) for pid in keep]
    known = [v for v in parts if v is not None]
    whys = [f"{names.get(m.get('position_id'), m.get('position_id'))}: {m.get('contract_id') or 'a contract'}: "
            f"{m.get('reason') or 'no figure'}" for m in det.get("missing") or []
            if not filtered or str(m.get("position_id") or "") in shown_pids]
    notes = []
    total = float(sum(known)) if known else None
    if not filtered:
        un = [(_num(u.get("pnl_usd")), u) for u in det.get("unattributed") or []]
        un_known = [v for v, _u in un if v is not None]
        if un_known:
            total = (total or 0.0) + float(sum(un_known))
            notes.append(f"includes {_plural(len(un_known), 'contract')} not attributed to one position: "
                         f"{format_cell(float(sum(un_known)))} USD ("
                         + "; ".join(f"{u.get('contract_id')}: {u.get('reason') or 'not attributed'}" for _v, u in un[:4])
                         + ")")
    e["total"], e["excl"], e["why"] = total, len(whys), whys
    e["note"] = _lines(*notes)
    if e["total"] is None and not whys:
        e["why"] = [s.get("reason") or "the scenario moves none of the trades showing"]
    losers = [(pid, _num(bp[pid])) for pid in keep if _num(bp[pid]) is not None and _num(bp[pid]) <= -0.5]
    if losers:
        pid, v = min(losers, key=lambda x: x[1])
        e["hit"], e["hit_value"] = shown_pids.get(pid) or names.get(pid) or pid, v
    else:
        e["hit_reason"] = ("no trade loses in this scenario" if known
                           else "the scenario moves none of the trades showing")


def stress_entries(ctx: dict, stress: Optional[dict]) -> List[dict]:
    """Every scenario, worst P&L of the rows showing first (none last)."""
    shown_pids = shown_positions(ctx)
    out = []
    for s in (stress or {}).get("scenarios") or []:
        kind = str(s.get("kind") or "")
        placeholder = s.get("placeholder")
        if placeholder is None:
            placeholder = (stress or {}).get("placeholder")
        if placeholder is None:
            placeholder = kind != "replay"
        e = {"name": str(s.get("name") or ""), "kind": KIND_WORDS.get(kind, kind), "placeholder": bool(placeholder),
             "hover": " ".join(x for x in (s.get("description"), (f"{s.get('start')} to {s.get('end')}"
                                                                   if s.get("start") else "")) if x),
             "total": None, "excl": 0, "why": [], "hit": "", "hit_value": None, "hit_reason": ""}
        if kind == "fx":
            by_ccy = [(str(c.get("currency") or ""), _num(c.get("pnl_change_usd"))) for c in s.get("by_currency") or []]
            valued = [(c, v) for c, v in by_ccy if v is not None]
            if ctx["filtered"]:
                e["why"] = [f"a currency scenario is not split per trade: the whole book's figure is "
                            f"{format_cell(_num(s.get('total_usd')))}" if _num(s.get("total_usd")) is not None
                            else "a currency scenario is not split per trade"]
            else:
                e["total"] = _num(s.get("total_usd"))
                e["why"] = [s.get("reason") or "no figure"] if e["total"] is None else []
            worst = min(valued, key=lambda x: x[1]) if valued else None
            if worst and worst[1] <= -0.5:
                e["hit"], e["hit_value"] = f"{worst[0]} held", worst[1]
            else:
                e["hit_reason"] = "no currency loses in this scenario" if valued else "no currency figure"
            out.append(e)
            continue
        _stress_by_position(e, s, ctx, shown_pids)
        out.append(e)
    return sorted(out, key=lambda e: (e["total"] is None, e["total"] if e["total"] is not None else 0.0))


def _stress_tr(e: dict) -> html.Tr:
    kind = [e["kind"], html.Span(" placeholder", className="risk-grey", title=PLACEHOLDER_WORDS) if e["placeholder"] else None]
    if e["total"] is None:
        total = html.Td(missing_cell(_lines(*e["why"][:8]) or "no figure"))
    else:
        total = html.Td([km_cell(e["total"], hover=e.get("note") or ""),
                         marker(f"excl. {e['excl']}", _lines(*e["why"][:8]), "marker--small") if e["excl"] else None])
    if e["hit_value"] is None:
        hit = html.Td(missing_cell(e["hit_reason"] or "no figure"), className="l")
    else:
        hit = html.Td([html.Span(e["hit"], className="tk-name"), " ", km_cell(e["hit_value"])], className="l",
                      title=plain_words(_lines(f"{e['hit']}: {format_cell(e['hit_value'])} USD", e.get("hit_note", ""))))
    return html.Tr([html.Td(e["name"], className="l", title=plain_words(e["hover"]) or None),
                    html.Td(kind, className="l tk-sub"), total, hit])


def stress_table(entries: Sequence[dict]) -> html.Table:
    head = html.Thead(html.Tr([
        html.Th("Scenario", className="l", title="The scenario; its definition on hover."),
        html.Th("Kind", className="l", title="Designed (a move of the stress file), historical replay (a past window) or "
                                             "currency."),
        html.Th("P&L", title="The trades showing, first order on today's delta; a currency scenario only for the whole "
                             "book."),
        html.Th("Hardest hit", className="l", title="The trade (or, for a currency scenario, the currency) that loses "
                                                    "most.")]))
    return html.Table([head, html.Tbody([_stress_tr(e) for e in entries])],
                      className="book-table book-grid tk-table risk-stress-table")


def stress_fold(ctx: dict, stress: Optional[dict], is_open: bool, more_open: bool) -> html.Div:
    entries = stress_entries(ctx, stress) if stress is not None else []
    priced = [e for e in entries if e["total"] is not None and not (abs(e["total"]) < 0.5 and e["excl"])]
    worst = priced[0] if priced else None
    if stress is None:
        count: Any = "Click to run the scenarios"
    elif not entries:
        count = "No scenario on file"
    elif worst is None:
        count = f"{_plural(len(entries), 'scenario')} · no scenario priced yet"
    else:
        count = f"{_plural(len(entries), 'scenario')} · worst {km_text(worst['total'])} ({worst['name']})"
    body: List[Any] = []
    if is_open:
        if stress is None:
            body.append(html.Div("Computing the scenarios...", className="risk-quiet"))
        elif not entries:
            why = "; ".join((stress or {}).get("reasons") or []) or "no scenario in the stress file"
            body.append(html.Div(missing_cell(why), className="risk-quiet"))
        else:
            body.append(html.Div(stress_table(entries[:STRESS_SHOWN]), className="tk-table-slot"))
            rest = entries[STRESS_SHOWN:]
            if rest:
                body.append(html.Div([html.Span("▾ " if more_open else "▸ ", className="tk-chev"),
                                      f"{len(rest)} more"], id={"type": FOLD_TYPE, "idx": FOLD_STRESS_MORE}, n_clicks=0,
                                     className="risk-more"))
                if more_open:
                    body.append(html.Div(stress_table(rest), className="tk-table-slot"))
    return fold(FOLD_STRESS, "Stress", count, STRESS_ABOUT, is_open, body)


# --------------------------------------------------------------------------- Price check
def _pc_unit(r: dict, roots: Dict[str, Any]) -> str:
    """The unit a price-check row's prices are quoted in (`price_text` reads its tick from it): the
    contract's quote unit, the pair for an FX rate."""
    if str(r.get("kind")) == "fx":
        pair = str(r.get("contract_id") or r.get("root_id") or "")[:6]
        return pair if is_fx_pair(pair) else ""
    return quoted_unit(roots.get(str(r.get("root_id") or ""))) if roots.get(str(r.get("root_id") or "")) else ""


def _pc_name(r: dict) -> str:
    cid = str(r.get("contract_id") or "")
    name = contract_name(cid) if cid else ""
    return name or plain_ids(cid or str(r.get("root_id") or ""))


def _gap_td(r: dict, flag: bool) -> html.Td:
    """The engine's gap in percent, signed at one decimal, amber when flagged."""
    gap = _num(r.get("gap_pct"))
    if gap is None:
        return html.Td(missing_cell(r.get("reason") or "not compared"))
    body = f"{abs(gap):.1f} %"
    text = (MINUS if gap < 0 and float(f"{abs(gap):.1f}") else "+" if gap > 0 else "") + body
    return html.Td(html.Span(text, className="cell-amber" if flag else None))


def price_check_fold(pc: Optional[dict], is_open: bool) -> html.Div:
    pc = pc or {}
    try:
        from data.contracts import load_roots
        roots = dict(load_roots())
    except Exception:  # noqa: BLE001 -- the prices then show at their own decimals
        roots = {}
    rows = pc.get("rows") or []
    flagged = sum(1 for r in rows if r.get("flagged"))
    # the engine's sentence (how many are off, the rule) is the count's hover, never a loose line
    sentence = cap(plain_ids(plain_words(str(pc.get("sentence") or "")))) or None
    if not pc:
        count: Any = "Not run yet"
    elif not rows:
        count = html.Span(cap(plain_words(str(pc.get("reason") or ""))) or "Nothing to compare", title=sentence)
    else:
        count = html.Span(f"{flagged} of {len(rows)} off" if flagged else f"{len(rows)} checked, all within range",
                          className="cell-amber" if flagged else None, title=sentence)
    body: List[Any] = []
    if is_open:
        if rows:
            head = html.Thead(html.Tr([html.Th(t, className=c or None, title=plain_words(h)) for t, c, h in (
                ("Check", "l", "Off: the research price is further from ours than allowed."),
                ("Kind", "l", "A future, an LME metal or the USD/CNH rate."),
                ("Price", "l", "Our contract (the research contract on hover)."),
                ("Ours", "", "Our latest official price, or the average fill before a pull."),
                ("Research", "", "The research app's latest settle or rate."),
                ("Factor", "", "Research over ours: 1.00 is the same price."),
                ("Gap", "", "How far the research price is from ours, in percent (the factor less one)."),
                ("Allowed", "", "The gap allowed before the check flags it: 20 % for futures and LME, 2 % for the "
                                "USD/CNH rate."))]))
            body_rows = []
            for r in rows:
                flag = bool(r.get("flagged"))
                factor = _num(r.get("factor"))
                ours_hover = _lines(f"{r.get('ours_kind') or ''} of {r.get('ours_date') or ''}".strip(), r.get("note") or "")
                unit = _pc_unit(r, roots)
                body_rows.append(html.Tr([
                    html.Td(html.Span("Off" if flag else ("OK" if factor is not None else MISSING),
                                      className="cell-amber tk-bold" if flag else "tk-sub",
                                      title=cap_parts(plain_ids(str(r.get("sentence") or r.get("reason") or "")))
                                      or None), className="l"),
                    html.Td({"future": "Future", "lme": "LME", "fx": "FX"}.get(str(r.get("kind")), str(r.get("kind") or "")),
                            className="l"),
                    html.Td(html.Span(_pc_name(r),
                                      title=_lines(f"Bloomberg: {r.get('contract_id') or r.get('root_id') or ''}",
                                                   f"Research: {r.get('research_contract_id') or ''}")), className="l"),
                    html.Td(html.Span(price_text(_num(r.get("ours")), unit), title=plain_words(ours_hover) or None)
                            if _num(r.get("ours")) is not None else missing_cell(r.get("reason") or "no price of ours")),
                    html.Td(html.Span(price_text(_num(r.get("research")), unit),
                                      title=plain_words(f"settle of {r.get('research_date') or ''}"))
                            if _num(r.get("research")) is not None else missing_cell(r.get("reason") or "no research price")),
                    html.Td(html.Span(f"×{factor:.2f}", className="cell-amber" if flag else None,
                                      title=plain_words(r.get("hint") or "") or None) if factor is not None
                            else missing_cell(r.get("reason") or "not compared")),
                    _gap_td(r, flag),
                    html.Td(f"{_num(r.get('threshold')) * 100:g} %" if _num(r.get("threshold")) is not None
                            else missing_cell("no threshold"), className="tk-sub"),
                ]))
            body.append(html.Div(html.Table([head, html.Tbody(body_rows)],
                                            className="book-table book-grid tk-table risk-price-table"),
                                 className="tk-table-slot"))
        elif pc.get("reason"):
            body.append(html.Div(missing_cell(pc["reason"]), className="risk-quiet"))
    return fold(FOLD_PRICE, "Price check (research)", count, PRICE_ABOUT, is_open, body)
