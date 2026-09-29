"""Risk tab: "How much can I lose, and which trades are using up my risk?" (Phase G round 2b,
2026-09-29; CLAUDE.md "Screens redesign plan -> Phase G", the design doc's "Risk: the per-trade
table" and "Risk folds"). Exposure merged into it: its grid, curves and currency card are the
folds here (`ui/tabs/risk_folds.py`).

Top to bottom: the shared filter bar and Group switch (`ui.tabs.trade_filter`, the same on Book and
P&L, carried across the three tabs); the headline of the rows showing (the VaR recomputed for them,
never summed, beside the vol target; their daily risk together and alone; leftover; FX unhedged;
the lowest hedge %) with one grey line saying whose history the figures come from; the per-trade
table (one row per open trade sorted by its share of the book's VaR, the trades without history at
the bottom with their reasons, a click opens the trade's legs); then the folds (Net by commodity,
Currency, Stress, Price check), the margin line and the one Data issues drawer.

What is read, never recomputed:
  - `engine.risk.trades.trade_risk` (`blotter_pricing.shared_trade_risk`, computed on its own
    thread; the table waits for it with a poll): daily risk, share of book, hedge %, the lot
    ratio against the best fit, the price check;
  - `engine.risk.trades.subset_var` for the headline, the total row and each group: the VaR and
    daily risk of exactly the trades showing (VaR is not additive: never the rows summed);
  - `engine.spreads.trade_book` (`shared_trade_book`): the trade's type, family and legs for the
    filter, its leftover (USD per 1 % move) and its currency hedge (exposure + hedge = unhedged,
    two engine figures added);
  - `engine.limits.liquidity` (through `risk_limits.limits_pass`): days to exit of the weakest leg,
    the legs' open interest and volume.
Every figure drawn from the research app's settlement history carries `trade_filter.research_mark`
while that history is not real Bloomberg data (its sentence on hover and in the grey line).
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dcc, html

from ui.revision import DATA_REVISION_ID
from ui.tabs import risk_folds as rf
from ui.tabs import risk_limits
from ui.tabs import trade_filter as tf
from ui.tabs.book import empty_state, trades_on_file
from ui.tabs.formatting import (
    cap, tidy,
    MINUS, MISSING, about, compact, format_cell, issues_drawer, km_cell, km_text, marker, missing_cell, pct_text,
    plain_words, row_info,
)
from ui.tabs.header import AS_OF_STORE_ID
from ui.tabs.risk_limits import plain_reason  # noqa: F401 -- re-exported: the Risk tab's plain-words pass

log = logging.getLogger(__name__)

TAB = "risk"
BODY_ID = "risk-body"                         # a message or the empty state
CONTENT_ID = "risk-content"                   # the bar, headline, table card, folds, footer (hidden with no book)
HEADLINE_ID = "risk-headline"
TABLE_SLOT_ID = "risk-table-slot"
TABLE_ID = "risk-table"
FOLDS_ID = "risk-folds"
FOOT_ID = "risk-foot"
ISSUES_ID = "risk-issues"
CSV_BUTTON_ID = "risk-csv"
DOWNLOAD_ID = "risk-download"
EXPAND_ALL_ID = "risk-expand-all"
COLLAPSE_ALL_ID = "risk-collapse-all"
OPEN_STORE_ID = "risk-open-trades"            # session: the trades whose legs are open
SORT_STORE_ID = "risk-sort-store"             # session: {"key", "dir"} or None
FOLDS_STORE_ID = "risk-folds-store"           # session: the open folds' keys
COMMODITY_STORE_ID = "risk-commodity-store"   # session: the commodity whose months and curves show
UNIT_STORE_ID = "risk-grid-unit-store"        # session: the month grid's unit
SHOWN_STORE_ID = "risk-shown"                 # the trades showing (Expand all)
RISK_READY_ID = "risk-ready"
RISK_POLL_ID = "risk-poll"
ROW_TYPE = "risk-trade-row"                   # a trade row: {"type", "idx": trade name}
SORT_TYPE = "risk-sort"                       # a column title: {"type", "idx": column key}
NA = MISSING
HIDDEN = {"display": "none"}
QUESTION = "How much can I lose, and which trades are using up my risk?"
COMPUTING = "the risk figures are still being computed"
HEDGE_AMBER = 50.0                            # hedge % under this is amber (the design doc)
RATIO_APART = 0.20                            # his ratio and the best fit more than 20 % apart: amber

# The columns: key, title, alignment ('l' = left), the title's definition, sortable, drawn from the research history.
COLUMNS: Tuple[Tuple[str, str, str, str, bool, bool], ...] = (
    ("trade", "Trade", "l", "Jason's trade name; click a row for its legs.", True, False),
    ("type", "Type", "l", "The trade's type by rule from its legs, as on the Book.", True, False),
    ("risk", "Daily risk", "", "A typical day's move: one standard deviation of the trade's daily P&L over the last 252 "
                               "days, today's position held across the history. Its VaR alone on hover.", True, True),
    ("share", "Share of book", "", "The trade's part of the book's VaR (its component VaR): the shares add up to 100 %, "
                                   "a negative share diversifies the book. The VaR in USD on hover.", True, True),
    ("hedge", "Hedge %", "", "How much of the bigger leg's risk the trade removes: 1 minus the trade's volatility over "
                             "its bigger leg's, over a year (2-day moves for legs closing hours apart). Amber under "
                             "50 %.", True, True),
    ("ratio", "Ratio his / best-fit", "", "The lots held of the second leg per lot of the first, against the lots "
                                          "that would have minimised the pair's variance over a year; amber when more "
                                          "than 20 % apart. Two-leg trades only.", True, True),
    ("leftover", "Leftover", "", "The directional exposure no spread covers: the net USD delta of the unpaired parts "
                                 "per 1 % move; lots and physical by leg on hover.", True, False),
    ("fx", "FX unhedged", "", "The China legs' USD exposure plus their currency hedge (a long China leg is hedged by a "
                              "short USD/CNH position): what a CNH move still reaches. Blank when every leg is in USD.",
     True, False),
    ("exit", "Days to exit", "", "Days to trade out of the weakest leg at 20 % of its 20-day average volume; amber and "
                                 "red at the liquidity check's levels (placeholders). Research volume data.", True, True),
)
SORTABLE = {k for k, _t, _c, _h, s, _r in COLUMNS if s}
LEG_HEAD = (("Leg", "l", "The contract held; hedges last."), ("Lots", "", "Net lots held (at delta for an option)."),
            ("Daily risk", "", "One standard deviation of the leg's own daily P&L in USD (research history)."),
            ("Corr. other side", "", "The leg's daily P&L against the other side's, as held: a working spread reads "
                                     "negative."),
            ("Open interest", "", ""), ("20-day volume", "", ""), ("% of OI", "", ""),
            ("Days to exit", "", "Lots over 20 % of the 20-day average volume."), ("Level", "l", ""))


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _lines(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p)


def days_text(days: float) -> str:
    """Days to exit at one decimal everywhere; under a tenth of a day '< 0.1' (never '0.0')."""
    return "< 0.1" if days < 0.05 else f"{days:,.1f}"


def days_exact(days: float) -> str:
    """The hover's figure: two decimals, 'Under 0.01 days' below that."""
    return f"{days:,.2f} days" if days >= 0.005 else "Under 0.01 days"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def _day(iso: Optional[str]) -> str:
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return str(iso or "")
    return f"{d.day} {d:%b %Y}"


# --------------------------------------------------------------------------- gathering
def _outside_key() -> tuple:
    from ui.tabs.blotter_pricing import research_inputs_key
    return research_inputs_key()


def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """Everything the tab shows but the risk figures (computed apart, so nothing waits for them),
    once per database revision, as-of and research inputs. Shared: never edit."""
    from ui.tabs.blotter_pricing import screen_memo
    return screen_memo("risk-base", conn, as_of, lambda: _gather(conn, as_of), extra=_outside_key())


def _gather(conn: sqlite3.Connection, as_of: str) -> dict:
    from ui.tabs.blotter_pricing import shared_curve, shared_trade_book
    data: Dict[str, Any] = {"as_of": as_of, "errors": [], "n_trades": trades_on_file(conn)}
    if not data["n_trades"]:
        return data
    try:
        from data.contracts import load_roots
        data["roots"] = dict(load_roots())
    except Exception:  # noqa: BLE001 -- the ids stand for the names
        data["roots"] = {}
    try:
        data["trade_book"] = shared_trade_book(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the table still shows the risk rows
        log.exception("Risk: the trade book failed for %s", as_of)
        data["trade_book"] = {"trades": [], "notes": []}
        data["errors"].append(("Trades", f"the trades could not be read ({type(exc).__name__}: {exc})"))
    try:
        data["curve"] = shared_curve(conn, as_of)
    except Exception as exc:  # noqa: BLE001
        log.exception("Risk: the curve positions failed for %s", as_of)
        data["curve"] = {"rows": [], "by_subsector": {}}
        data["errors"].append(("Exposure", f"the positions by contract could not be read ({type(exc).__name__}: {exc})"))
    data["margin"], data["checks"], data["liquidity"] = risk_limits.limits_pass(conn, as_of)
    try:
        from engine.risk.config import load_config
        data["config"] = load_config()
    except Exception as exc:  # noqa: BLE001
        data["config"] = {}
        data["errors"].append(("Parameters", f"the risk parameters could not be read ({type(exc).__name__}: {exc})"))
    data["research"] = tf.research_source()
    return data


def stress_result(conn: sqlite3.Connection, as_of: str) -> dict:
    """`engine.stress.commodity_stress` on the shared positions and spreads and the research
    history's replay moves, once per revision (computed when the Stress fold is opened)."""
    from ui.tabs.blotter_pricing import screen_memo, shared_curve, shared_spreads

    def compute():
        from engine.risk.commodity_history import load_commodity_history
        from engine.stress import commodity_stress
        try:
            spreads = shared_spreads(conn, as_of)
        except Exception:  # noqa: BLE001 -- the engine reads them itself and names the failure
            spreads = None
        history = load_commodity_history()
        return commodity_stress(conn, as_of, positions=shared_curve(conn, as_of), spreads=spreads,
                                history=history.window_move if getattr(history, "available", False) else None)
    try:
        return screen_memo("risk-stress", conn, as_of, compute, extra=_outside_key())
    except Exception as exc:  # noqa: BLE001 -- the fold says why
        log.exception("Risk: stress failed for %s", as_of)
        return {"available": False, "scenarios": [], "reasons": [f"the scenarios could not be computed "
                                                                 f"({type(exc).__name__}: {exc})"]}


def _risk(conn: sqlite3.Connection, as_of: str, wait: bool) -> Optional[dict]:
    from ui.tabs.blotter_pricing import shared_trade_risk
    try:
        return shared_trade_risk(conn, as_of, wait=wait)
    except Exception as exc:  # noqa: BLE001 -- the table says why
        log.exception("Risk: trade risk failed for %s", as_of)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "trades": []}


# --------------------------------------------------------------------------- the view (rows showing)
def _pseudo(r: dict) -> dict:
    """A risk row with no trade name on the Book (a position of fills with no PBRoot name)."""
    return {"trade": r.get("trade"), "pseudo": True, "type": str(r.get("type") or ""), "commodity_family": "",
            "what_it_is": str(r.get("name") or ""), "legs": [], "flags": [], "trade_ids": list(r.get("trade_ids") or []),
            "leftover": {}, "hedge": {}, "status": "open"}


def rows_of(data: dict, risk: Optional[dict]) -> List[Tuple[dict, Optional[dict]]]:
    """[(trade, risk row or None)]: one per open trade (a risk row with its trade from the trade
    book, or a pseudo-trade), then the open trades with no risk row yet."""
    tb = [t for t in (data.get("trade_book") or {}).get("trades") or [] if t.get("status") == "open"]
    by_name = {str(t.get("trade")): t for t in tb}
    out: List[Tuple[dict, Optional[dict]]] = []
    seen = set()
    for r in (risk or {}).get("trades") or []:
        name = str(r.get("trade") or "")
        t = by_name.get(name) or _pseudo(r)
        out.append((t, r))
        seen.add(name)
    out += [(t, None) for t in tb if str(t.get("trade")) not in seen]
    return out


def view(data: dict, risk: Optional[dict], state: Optional[dict]) -> dict:
    """The rows and the folds' context under the filter."""
    rows = rows_of(data, risk)
    s = tf.normal(state)
    cols = tf.tab_filters(s, TAB)
    shown = [(t, r) for t, r in rows if tf.keeps(t, s)
             and (not cols or tf.keeps_cols((t, r), cols, lambda item, col: filter_value(data, item[0], item[1], col)))]
    name_of_fill: Dict[str, str] = {}
    for t in (data.get("trade_book") or {}).get("trades") or []:
        for tid in t.get("trade_ids") or []:
            name_of_fill[str(tid)] = str(t.get("trade"))
    for t, r in rows:
        for tid in (r or {}).get("trade_ids") or []:
            name_of_fill.setdefault(str(tid), str(t.get("trade")))
    return {"as_of": data["as_of"], "roots": data.get("roots") or {}, "curve": data.get("curve") or {},
            "rows": rows, "shown_rows": shown, "shown": [t for t, _r in shown],
            "shown_names": {str(t.get("trade")) for t, _r in shown}, "filtered": tf.is_filtered(s, TAB),
            "name_of_fill": name_of_fill, "state": s}


def subset(conn: sqlite3.Connection, as_of: str, names: Sequence[str]) -> Optional[dict]:
    """`subset_var` of the trades named (the engine's block is memoised: a sum and a quantile)."""
    if not names:
        return None
    try:
        from engine.risk.trades import subset_var
        from ui.tabs.blotter_pricing import screen_memo
        key = tuple(sorted(str(n) for n in names))
        # memoised per revision, as-of, research inputs and the set of names (a group switch or a row
        # opened asks the same sets again): the engine's own figure, shared, never edited
        return screen_memo("risk-subset", conn, as_of, lambda: subset_var(conn, as_of, list(key)),
                           extra=(*_outside_key(), key))
    except Exception as exc:  # noqa: BLE001 -- the headline says why
        log.exception("Risk: subset VaR failed for %s", as_of)
        return {"var_usd": None, "var_reason": f"{type(exc).__name__}: {exc}", "daily_risk_usd": None,
                "daily_risk_reason": f"{type(exc).__name__}: {exc}", "sum_daily_risk_usd": None, "left_out": []}


# --------------------------------------------------------------------------- per-trade figures
def leftover_of(t: dict) -> Tuple[Optional[float], str, List[str]]:
    lo = t.get("leftover") or {}
    lines = []
    for leg in lo.get("legs") or []:
        phys = _num(leg.get("physical"))
        lines.append(f"{leg.get('name')}: {rf.lots_text(_num(leg.get('lots')))} lots"
                     + (f", {rf.units_text(phys, str(leg.get('physical_unit') or ''))}" if phys is not None else "")
                     + (f", {km_text(_num(leg.get('usd_per_1pct')))} USD per 1 %"
                        if _num(leg.get("usd_per_1pct")) is not None else ""))
    if t.get("pseudo"):
        return None, "fills on no trade: no leftover read", lines
    v = _num(lo.get("usd_per_1pct"))
    return v, str(lo.get("reason") or ("" if v is not None else "no leftover figure")), lines


def fx_unhedged_of(t: dict) -> Tuple[Optional[float], str, bool]:
    """(the China legs' exposure plus their hedge, why none, whether the cell applies): blank when
    every open leg is in USD."""
    h = t.get("hedge") or {}
    ccys = {str(lg.get("currency") or "") for lg in t.get("legs") or [] if not lg.get("hedge") and lg.get("status") == "open"}
    china = bool(ccys & set(rf.CHINA_CCYS)) or str(h.get("currency") or "") in rf.CHINA_CCYS
    if not china:
        others = sorted(ccys - {"", "USD"})
        if others:
            return None, f"legs in {', '.join(others)}: only the China legs are measured against a hedge", True
        return None, "", False
    exp, hed = _num(h.get("exposure_usd")), _num(h.get("hedge_usd"))
    if exp is None:
        return None, str(h.get("reason") or "no CNY exposure figure"), True
    if h.get("present") and hed is None:
        return None, str(h.get("reason") or "no USD figure for the hedge"), True
    return exp + (hed or 0.0), "", True


def fx_hover(t: dict) -> str:
    h = t.get("hedge") or {}
    return _lines(f"China legs {format_cell(_num(h.get('exposure_usd')))} USD"
                  + (f" ({h.get('exposure_basis')})" if h.get("exposure_basis") else ""),
                  f"Hedge {format_cell(_num(h.get('hedge_usd')))} USD" if h.get("present") else "No hedge",
                  f"Coverage {pct_text(_num(h.get('coverage')))}" if _num(h.get("coverage")) is not None else "",
                  h.get("direction_note") or "")


def liq_of(data: dict, r: Optional[dict]) -> Optional[dict]:
    if r is None:
        return None
    pid = str(r.get("position_id") or "")
    return next((p for p in (data.get("liquidity") or {}).get("positions") or [] if str(p.get("position_id")) == pid),
                None)


def ratio_parts(r: Optional[dict]) -> Tuple[Optional[float], Optional[float], bool]:
    if r is None:
        return None, None, False
    his, fit = _num(r.get("lot_ratio")), _num(r.get("best_fit_ratio"))
    apart = his is not None and fit is not None and abs(fit) > 1e-12 and abs(his - fit) / abs(fit) > RATIO_APART
    return his, fit, apart


def sort_value(data: dict, t: dict, r: Optional[dict], key: str) -> Any:
    if key == "trade":
        return str(t.get("trade") or "").lower()
    if key == "type":
        code = tf.type_code(t)
        return tf.TYPE_ORDER.index(code) if code in tf.TYPE_ORDER else 99
    if key == "risk":
        return _num((r or {}).get("daily_risk_usd"))
    if key == "share":
        return _num((r or {}).get("share_of_book"))
    if key == "hedge":
        return _num((r or {}).get("hedge_pct"))
    if key == "ratio":
        his, fit, _a = ratio_parts(r)
        return None if his is None or fit is None or not fit else abs(his - fit) / abs(fit)
    if key == "leftover":
        v = leftover_of(t)[0]
        return None if v is None else abs(v)
    if key == "fx":
        v = fx_unhedged_of(t)[0]
        return None if v is None else abs(v)
    if key == "exit":
        return _num((liq_of(data, r) or {}).get("days_to_exit"))
    return None


def sort_rows(data: dict, rows: Sequence[Tuple[dict, Optional[dict]]], sort: Optional[dict]) -> List[tuple]:
    """By share of the book, largest first (default), the trades without history last; or by the
    column chosen, a missing value last."""
    key = (sort or {}).get("key")
    if key not in SORTABLE:
        have = [x for x in rows if _num((x[1] or {}).get("share_of_book")) is not None]
        rest = [x for x in rows if x not in have]
        have.sort(key=lambda x: -_num(x[1]["share_of_book"]))
        return have + sorted(rest, key=lambda x: str(x[0].get("trade") or ""))
    desc = (sort or {}).get("dir") != "asc"
    have = [x for x in rows if sort_value(data, x[0], x[1], key) is not None]
    rest = [x for x in rows if x not in have]
    have.sort(key=lambda x: sort_value(data, x[0], x[1], key), reverse=desc)
    return have + rest


# --------------------------------------------------------------------------- the cells
def _no_history(r: Optional[dict], ready: bool) -> str:
    if not ready:
        return COMPUTING
    if r is None:
        return "no risk row for this trade"
    return plain_reason(r.get("reason")) or "no price history"


def _risk_td(r: Optional[dict], ready: bool) -> html.Td:
    v = _num((r or {}).get("daily_risk_usd"))
    if v is None:
        return html.Td(missing_cell((r or {}).get("daily_risk_reason") or _no_history(r, ready)))
    alone = _num(r.get("standalone_var"))
    hover = _lines(f"Its VaR alone: {format_cell(alone)} USD" if alone is not None
                   else f"Its VaR alone: not given ({r.get('standalone_reason') or 'no figure'})",
                   r.get("daily_risk_reason") or "", f"over {r.get('daily_risk_days')} days" if r.get("daily_risk_days") else "")
    return html.Td(km_cell(v, hover=hover, signed=False, colour=False))


def _share_td(r: Optional[dict], ready: bool) -> html.Td:
    v = _num((r or {}).get("share_of_book"))
    if v is None:
        return html.Td(missing_cell((r or {}).get("share_reason") or _no_history(r, ready)))
    cv = _num(r.get("contribution_var"))
    hover = (f"{format_cell(cv)} USD of the book's VaR" if cv is not None else "") + (
        "; it diversifies the book (it tends to gain on the book's bad days)" if v < 0 else "")
    return html.Td(html.Span(pct_text(v), className="cell-neg" if v < 0 else None, title=plain_words(hover) or None))


def _hedge_td(r: Optional[dict], ready: bool) -> html.Td:
    v = _num((r or {}).get("hedge_pct"))
    if v is None:
        return html.Td(missing_cell((r or {}).get("hedge_reason") or _no_history(r, ready)))
    corr = _num(r.get("leg_correlation"))
    hover = _lines(f"Bigger leg: {r.get('hedge_leg')}" if r.get("hedge_leg") else "",
                   f"{r.get('hedge_moves')} moves over {r.get('hedge_days')} days" if r.get("hedge_days") else "",
                   f"Legs' correlation {corr:.2f}" if corr is not None else "", r.get("hedge_reason") or "")
    return html.Td(html.Span(pct_text(v / 100.0), className="cell-amber" if v < HEDGE_AMBER else None,
                             title=plain_words(hover) or None))


def _ratio_text(v: float) -> str:
    """'1 : 1.84'; a minus when the two legs are held the same way (the engine's sign)."""
    return f"1 : {MINUS if v < 0 else ''}{abs(v):.2f}"


def _ratio_td(r: Optional[dict], ready: bool) -> html.Td:
    his, fit, apart = ratio_parts(r)
    if r is None or (his is None and fit is None):
        return html.Td(missing_cell((r or {}).get("best_fit_reason") or _no_history(r, ready)))
    corr = _num(r.get("leg_correlation"))
    legs = r.get("ratio_legs") or []
    hover = _lines(f"Legs: {' / '.join(str(x) for x in legs)}" if legs else "",
                   f"Held: {rf.lots_text(_num(r.get('lots_a')))} against {rf.lots_text(_num(r.get('lots_b')))} lots"
                   if r.get("lots_a") is not None else "",
                   f"Correlation {corr:.2f}" if corr is not None else "",
                   f"{r.get('best_fit_moves')} moves over {r.get('best_fit_days')} days" if r.get("best_fit_days") else "",
                   r.get("best_fit_reason") or "",
                   f"R² {_num(r.get('best_fit_r2')):.2f} (the fit's, the legs' correlation squared)"
                   if _num(r.get("best_fit_r2")) is not None else "")
    his_text = _ratio_text(his) if his is not None else MISSING
    fit_part: Any = (html.Span(_ratio_text(fit), className="cell-amber" if apart else None) if fit is not None
                     else missing_cell(r.get("best_fit_reason") or "no fit"))
    return html.Td(html.Span([his_text, html.Span(" / ", className="tk-sub"), fit_part], title=plain_words(hover) or None))


def _leftover_td(t: dict) -> html.Td:
    v, why, lines = leftover_of(t)
    if v is None:
        return html.Td(missing_cell(_lines(why, *lines)))
    if abs(v) < 0.5:
        # nothing left over reads blank, as FX unhedged does where it does not apply (one rule per column)
        return html.Td("", title=cap(plain_words(why) or "every lot is in a spread"))
    return html.Td(km_cell(v, hover=_lines("USD per 1 % move", *lines), colour=False))


def _fx_td(t: dict) -> html.Td:
    v, why, applies = fx_unhedged_of(t)
    if not applies:
        return html.Td("")
    if v is None:
        return html.Td(missing_cell(why))
    return html.Td(km_cell(v, hover=fx_hover(t), colour=False))


def _exit_td(data: dict, t: dict, r: Optional[dict], ready: bool) -> html.Td:
    liq = data.get("liquidity") or {}
    if not liq.get("available"):
        return html.Td(missing_cell(plain_reason(liq.get("reason")) or "no liquidity check"))
    p = liq_of(data, r)
    if p is None:
        return html.Td(missing_cell(COMPUTING if not ready else "not in the liquidity check (no exchange contract)"))
    days = _num(p.get("days_to_exit"))
    level = str(p.get("level") or "")
    if days is None:
        return html.Td(missing_cell(_lines("no volume data", plain_reason(p.get("reason")))))
    oi, adv = _num(p.get("pct_of_oi")), _num(p.get("pct_of_adv"))
    weakest = str(p.get("weakest") or "")
    hover = _lines(f"Weakest leg: {_leg_names(t).get(weakest) or weakest}", f"{pct_text(oi)} of open interest" if oi is not None else "",
                   f"{pct_text(adv)} of a day's average volume" if adv is not None else "",
                   f"level {level.lower()}" + (" (placeholder thresholds)" if liq.get("placeholder") else ""))
    cls = {"RED": "cell-red", "AMBER": "cell-amber"}.get(level)
    return html.Td(html.Span(days_text(days), className=cls, title=plain_words(_lines(days_exact(days), hover))))


def _type_td(t: dict) -> html.Td:
    return html.Td(html.Span(tf.trade_type_label(t, short=True) if not t.get("pseudo") else NA,
                             title=plain_words(t.get("type_note") or "") or None), className="l")


def row_reasons(t: dict, r: Optional[dict], ready: bool) -> List[str]:
    """The reasons behind the row's "i": why the trade is not in the risk figures (its dashes then
    all share that reason), or that it is counted without its currency hedge (layout wave 2: one
    marker per row, never a badge beside the name)."""
    if not ready:
        return []
    if r is None:
        return ["Not in the risk figures: no risk row for this trade"]
    out = []
    if not r.get("included", True):
        out.append("Not in the risk figures: " + (plain_reason(r.get("reason")) or "no price history"))
    if r.get("partial"):
        out.append("Counted without its currency hedge: " + (plain_reason(r.get("partial_reason")) or "no history"))
    return out


def trade_tr(data: dict, t: dict, r: Optional[dict], ready: bool, opened: bool) -> html.Tr:
    name = str(t.get("trade") or "")
    name_cell = [html.Span("▾ " if opened else "▸ ", className="tk-chev"),
                 html.Span(name, className="tk-name", title=plain_words(t.get("what_it_is") or "") or None),
                 row_info(row_reasons(t, r, ready))]
    return html.Tr([html.Td(name_cell, className="l"), _type_td(t), _risk_td(r, ready), _share_td(r, ready),
                    _hedge_td(r, ready), _ratio_td(r, ready), _leftover_td(t), _fx_td(t), _exit_td(data, t, r, ready)],
                   id={"type": ROW_TYPE, "idx": name}, n_clicks=0,
                   className="tk-row" + (" tk-row--open" if opened else "") + (" tk-row--pseudo" if t.get("pseudo") else "")
                   + ("" if (r or {}).get("included", True) else " risk-row--nohist"))


def _sums(rows: Sequence[Tuple[dict, Optional[dict]]]) -> dict:
    lo = [(leftover_of(t)[0], f"{t.get('trade')}: {leftover_of(t)[1]}") for t, _r in rows]
    fx = [(v, f"{t.get('trade')}: {why}") for t, _r in rows for v, why, applies in [fx_unhedged_of(t)] if applies]
    share = [(_num((r or {}).get("share_of_book")), f"{t.get('trade')}: no share") for t, r in rows]
    return {"leftover": _sum(lo), "fx": _sum(fx), "share": _sum(share)}


def _sum(parts):
    from ui.tabs.formatting import sum_known
    return sum_known(parts) if parts else (None, 0, [])


def _sum_td(tot: Tuple[Optional[float], int, List[str]], hover: str = "", pct: bool = False) -> html.Td:
    v, n, why = tot
    if v is None:
        return html.Td(missing_cell(_lines(*why[:8]) or "no figure") if why else "")
    cell = (html.Span(pct_text(v), title=plain_words(hover) or None) if pct
            else km_cell(v, hover=hover, colour=False))
    return html.Td([cell, marker(f"excl. {n}", _lines(*why[:8]), "marker--small") if n else None])


def _var_td(sub: Optional[dict], ready: bool, key: str = "daily_risk_usd") -> html.Td:
    if not ready:
        return html.Td(missing_cell(COMPUTING))
    v = _num((sub or {}).get(key))
    if v is None:
        return html.Td(missing_cell(plain_reason((sub or {}).get(key.replace("_usd", "_reason"))) or "no figure"))
    left = (sub or {}).get("left_out") or []
    hover = _lines("the trades' summed daily P&L, one standard deviation: recomputed for these trades, never the rows "
                   "added", f"alone, added: {format_cell(_num(sub.get('sum_daily_risk_usd')))} USD"
                   if _num(sub.get("sum_daily_risk_usd")) is not None else "")
    return html.Td([km_cell(v, hover=hover, signed=False, colour=False),
                    marker(f"excl. {len(left)}", _lines(*(f"{x.get('trade')}: {x.get('reason')}" for x in left)),
                           "marker--small") if left else None])


def total_tr(rows: Sequence[tuple], all_rows: Sequence[tuple], sub: Optional[dict], ready: bool, filtered: bool) -> html.Tr:
    named = [t for t, _r in all_rows if not t.get("pseudo")]
    shown_named = [t for t, _r in rows if not t.get("pseudo")]
    label = (f"Filtered · {len(shown_named)} of {len(named)} open {'trade' if len(named) == 1 else 'trades'}"
             if filtered
             else f"Book · {len(named)} open {'trade' if len(named) == 1 else 'trades'}")
    s = _sums(rows)
    return html.Tr([html.Td(label, className="l"), html.Td(""), _var_td(sub, ready),
                    _sum_td(s["share"], "the shares of the rows showing added (component VaR shares add up)", pct=True),
                    html.Td(""), html.Td(""), _sum_td(s["leftover"], "USD per 1 % move, the rows showing added"),
                    _sum_td(s["fx"], "the rows showing added"), html.Td("")], className="tk-total book-total")


def group_tr(label: str, rows: Sequence[tuple], sub: Optional[dict], ready: bool) -> html.Tr:
    s = _sums(rows)
    return html.Tr([html.Td([html.Span(label), html.Span(f" · {_plural(len(rows), 'trade')}", className="tk-sub")],
                            className="l", colSpan=2),
                    _var_td(sub, ready), _sum_td(s["share"], pct=True), html.Td(""), html.Td(""),
                    _sum_td(s["leftover"]), _sum_td(s["fx"]), html.Td("")], className="tk-group")


# The column filters (the funnel in each heading, user 2026-09-29): Trade holds the trade names and
# the commodity families, Type the types (both carried across Book, P&L and Risk); each figure one
# comparison, this tab's own. Share and Hedge % compare in percent ("> 10" = above 10 %).
LIST_FUNNELS = {"trade": ("trade", "commodity"), "type": ("type",)}
NUMBER_FUNNELS = {"risk": "The trade's daily risk in USD.", "share": "The share of the book's VaR, in percent.",
                  "hedge": "The hedge %, in percent.", "leftover": "USD per 1 % move.",
                  "fx": "The China legs' unhedged USD.", "exit": "Days to exit."}


def filter_value(data: dict, t: dict, r: Optional[dict], col: str) -> Optional[float]:
    """A trade's figure for a number column's filter, as its cell shows it (never recomputed)."""
    if col == "risk":
        return _num((r or {}).get("daily_risk_usd"))
    if col == "share":
        v = _num((r or {}).get("share_of_book"))
        return None if v is None else v * 100.0
    if col == "hedge":
        return _num((r or {}).get("hedge_pct"))
    if col == "leftover":
        return leftover_of(t)[0]
    if col == "fx":
        return fx_unhedged_of(t)[0]
    if col == "exit":
        return _num((liq_of(data, r) or {}).get("days_to_exit"))
    return None


def head(sort: Optional[dict], research: dict, state: Optional[dict] = None,
         options: Optional[Dict[str, List[dict]]] = None) -> html.Thead:
    ths = []
    half = len(COLUMNS) // 2
    for i, (key, title, cls, tip, sortable, from_history) in enumerate(COLUMNS):
        # one "i" per research column, never a badge per heading or cell (layout wave 2)
        mark = tf.research_head(research) if from_history else None
        pop = None
        if key in LIST_FUNNELS:
            pop = tf.trade_funnel(TAB, state, options or {}, LIST_FUNNELS[key], key)
        elif key in NUMBER_FUNNELS:
            pop = tf.number_funnel(TAB, state, key, hint=f"{NUMBER_FUNNELS[key]} {tf.NUMBER_HINT}")
        ths.append(tf.head_th(title, cls, tip, sort_id={"type": SORT_TYPE, "idx": key} if sortable else None,
                              arrow=tf.arrow_of(sort, key), pop=pop, right=i > half, note=mark))
    return html.Thead(html.Tr(ths))


# --------------------------------------------------------------------------- the panel
def _leg_names(t: dict) -> Dict[str, str]:
    return {str(lg.get("contract_id")): str(lg.get("name") or "") for lg in t.get("legs") or []}


def _leg_risk_cells(lr: Optional[dict], ready: bool) -> List[Any]:
    """The Daily risk and Correlation cells of a leg from `trade_risk`'s `legs_risk` entry."""
    if lr is None:
        why = COMPUTING if not ready else "no risk figure for this leg"
        return [html.Td(missing_cell(why)), html.Td(missing_cell(why))]
    v = _num(lr.get("daily_risk_usd"))
    risk = (km_cell(v, hover=_lines(f"one standard deviation of the leg's own daily P&L over {lr.get('days')} days"
                                    if lr.get("days") else "", f"{lr.get('moves')} moves" if lr.get("moves") else ""),
                    signed=False, colour=False)
            if v is not None else missing_cell(plain_reason(lr.get("daily_risk_reason")) or "no daily risk"))
    c = _num(lr.get("corr_other_side"))
    corr = (html.Span(f"{c:.2f}".replace("-", MINUS),
                      title=plain_words(_lines(f"with {lr.get('corr_with')}" if lr.get("corr_with") else "",
                                               "the P&L as held: a leg the other side offsets reads negative "
                                               "(a working spread about -0.7 to -1)")))
            if c is not None else missing_cell(plain_reason(lr.get("corr_reason")) or "no correlation"))
    return [html.Td(risk), html.Td(corr)]


def legs_table(data: dict, t: dict, r: Optional[dict]) -> Optional[html.Table]:
    """The trade's legs: each leg's own daily risk and its correlation with the other side
    (`trade_risk`'s `legs_risk`), then its open interest, volume, share of the open interest and
    days to exit (`engine.limits.liquidity`); one row per contract, hedges last."""
    ready = r is not None
    names = _leg_names(t)
    legs_risk = {str(x.get("contract_id")): x for x in (r or {}).get("legs_risk") or []}
    p = liq_of(data, r)
    liq = {str(x.get("contract_id")): x for x in (p or {}).get("legs") or []}
    order = list(dict.fromkeys(list(legs_risk) + list(liq)))
    if not order:
        return None
    order.sort(key=lambda cid: bool((legs_risk.get(cid) or {}).get("hedge")))
    body = []
    for cid in order:
        lr, leg = legs_risk.get(cid), liq.get(cid)
        why = plain_reason((leg or {}).get("reason")) or ("not in the liquidity check" if leg is None else "")
        days, oi_pct = _num((leg or {}).get("days_to_exit")), _num((leg or {}).get("pct_of_oi"))
        level = str((leg or {}).get("level") or "")
        note = str((leg or {}).get("note") or "")
        lots = _num((leg or {}).get("lots"))
        if lots is None:
            lots = _num((lr or {}).get("lots"))
        name = names.get(cid) or str((lr or {}).get("name") or "") or cid
        hedge = bool((lr or {}).get("hedge"))
        body.append(html.Tr([
            html.Td(html.Span(name + (" (hedge)" if hedge else ""), title=plain_words(_lines(cid, note)) or None),
                    className="l"),
            html.Td(rf.lots_text(lots)),
            *_leg_risk_cells(lr, ready),
            html.Td(format_cell(_num(leg.get("open_interest"))) if leg and _num(leg.get("open_interest")) is not None
                    else missing_cell(why or "no open interest"),
                    title=f"on {leg.get('oi_date')}" if leg and leg.get("oi_date") else None),
            html.Td(f"{_num(leg.get('adv')):,.0f}" if leg and _num(leg.get("adv")) is not None
                    else missing_cell(why or "no volume"),
                    title=f"over {leg.get('adv_days')} days" if leg and leg.get("adv_days") else None),
            html.Td(pct_text(oi_pct) if oi_pct is not None else missing_cell(why or "no open interest")),
            html.Td(html.Span(days_text(days), title=days_exact(days)) if days is not None
                    else missing_cell(why or "no volume data"),
                    className={"RED": "cell-red", "AMBER": "cell-amber"}.get(level)),
            html.Td(cap(level.lower().replace("no_data", "no data")) or NA, className="l tk-sub"),
        ], className="tk-leg" + (" tk-leg--hedge" if hedge else "")))
    return html.Table([html.Thead(html.Tr([html.Th(x, className=c or None, title=h or None) for x, c, h in LEG_HEAD])),
                       html.Tbody(body)], className="book-table tk-table tk-legs risk-legs")


def panel_tr(data: dict, t: dict, r: Optional[dict]) -> html.Tr:
    name = str(t.get("trade") or "")
    facts: List[Any] = []

    def fact(k, v, hover=""):
        # one row of the key/value facts table (layout wave 2: a table, never a run of loose spans)
        facts.append(html.Tr([html.Td(k, className="tk-kv-k"),
                              html.Td(cap(v) if isinstance(v, str) else v, title=cap(plain_words(hover)) or None)]))

    if r is not None:
        alone, contrib = _num(r.get("standalone_var")), _num(r.get("contribution_var"))
        fact("VaR alone", km_text(alone, signed=False) if alone is not None else NA,
             format_cell(alone) if alone is not None else (r.get("standalone_reason") or "not given"))
        fact("Its part of the book's VaR", km_text(contrib) if contrib is not None else NA,
             format_cell(contrib) if contrib is not None else (r.get("share_reason") or "not given"))
        legs = r.get("ratio_legs") or []
        corr = _num(r.get("leg_correlation"))
        if legs:
            fact("Sides", " / ".join(str(x) for x in legs),
                 f"correlation {corr:.2f} on {r.get('best_fit_moves')} moves" if corr is not None else "")
        if corr is not None:
            fact("Correlation", f"{corr:.2f}".replace("-", MINUS))
        if r.get("hedge_leg"):
            fact("Bigger leg", str(r.get("hedge_leg")), f"hedge % on {r.get('hedge_moves')} moves")
        if not r.get("included", True):
            fact("Not in the VaR", plain_reason(r.get("reason")) or "no history")
        if r.get("partial"):
            fact("Without hedge", plain_reason(r.get("partial_reason")) or "")
    v, _why, lines = leftover_of(t)
    if lines:
        fact("Leftover", km_text(v) + " per 1 %" if v is not None else NA, _lines(*lines))
    h = t.get("hedge") or {}
    if h.get("present"):
        cov = _num(h.get("coverage"))
        fact("Hedge", f"{h.get('currency') or ''} {pct_text(cov)} covered".strip() if cov is not None
             else (h.get("reason") or "coverage not read"), fx_hover(t))
    kv = (html.Table(html.Tbody(facts), className="tk-table tk-kv risk-kv") if facts else None)
    table = legs_table(data, t, r)
    if table is None and r is not None:
        fact("Legs", missing_cell("no leg figures for this trade"))
        kv = html.Table(html.Tbody(facts), className="tk-table tk-kv risk-kv")
    # the legs table and the facts side by side, as on the Book's panel
    parts: List[Any] = [html.Div([html.Div(table, className="tk-legs-main") if table is not None else None, kv],
                                 className="tk-panel-legs")]
    if not t.get("pseudo"):
        n = len(t.get("trade_ids") or [])
        parts.append(html.Div(className="tk-links", children=[
            tf.link("Book", "book", name, "risk-book", "Open the Book filtered to this trade"),
            tf.link("P&L history", "pnl", name, "risk-pnl", "Open the P&L tab filtered to this trade"),
            tf.link(f"See fills ({n})", "blotter", name, "risk-fills", "Open the Blotter filtered to this trade's fills"),
        ]))
    return html.Tr(html.Td(parts, colSpan=len(COLUMNS), className="l tk-panel-cell"), className="tk-panel")


# --------------------------------------------------------------------------- the table
def table(conn: sqlite3.Connection, data: dict, v: dict, risk: Optional[dict], sort: Optional[dict],
          opened: Sequence[str]) -> Tuple[html.Table, List[str]]:
    ready = risk is not None
    shown = v["shown_rows"]
    opened_set = set(opened or [])
    names = [str(r.get("trade")) for _t, r in shown if r]
    sub = subset(conn, data["as_of"], names) if ready else None
    body: List[Any] = [total_tr(shown, v["rows"], sub, ready, v["filtered"])]

    def add(t, r):
        name = str(t.get("trade") or "")
        is_open = name in opened_set
        body.append(trade_tr(data, t, r, ready, is_open))
        if is_open:
            try:
                body.append(panel_tr(data, t, r))
            except Exception as exc:  # noqa: BLE001 -- one panel's reason, never the table
                log.exception("Risk: the panel of %s failed", name)
                body.append(html.Tr(html.Td(missing_cell(f"the legs could not be shown ({type(exc).__name__}: {exc})"),
                                            colSpan=len(COLUMNS), className="l"), className="tk-panel"))

    group = v["state"]["group"]
    if group == tf.GROUP_NONE:
        for t, r in sort_rows(data, shown, sort):
            add(t, r)
    else:
        by: Dict[str, List[tuple]] = {}
        for t, r in shown:
            by.setdefault(tf.group_of(t, group), []).append((t, r))
        for label in tf.group_order(by, group):
            rows = by[label]
            gsub = subset(conn, data["as_of"], [str(r.get("trade")) for _t, r in rows if r]) if ready else None
            body.append(group_tr(label, rows, gsub, ready))
            for t, r in sort_rows(data, rows, sort):
                add(t, r)
    options = tf.options_for((data.get("trade_book") or {}).get("trades") or [])
    return (html.Table([head(sort, data.get("research") or {}, v["state"], options), html.Tbody(body)], id=TABLE_ID,
                       className="book-table book-grid tk-table risk-trade-table"),
            [str(t.get("trade")) for t, _r in shown])


# --------------------------------------------------------------------------- the headline
def _var_definition(config: dict) -> str:
    conf = float(config.get("var_confidence") or 0.95) * 100
    window = config.get("var_window_bd") or 252
    return (f"1-day VaR at {conf:g} %: minus the {100 - conf:g}th percentile of the trades' summed daily P&L over the "
            f"last {window} days, today's positions held across the history; a typical bad day, a positive figure is "
            "a loss. Recomputed for the trades showing, never the rows added.")


def vs_target(sub: Optional[dict], ready: bool, config: dict) -> Tuple[Any, str]:
    """(the headline's "Of vol target" cell, its hover): the engine's `subset_var` against the vol
    target for the trades showing: their blended annual vol as a share of the target (the book's
    `vol_vs_target_pct` rule), the 1-day VaR's share on hover; "placeholder" while the target is
    Jason's stand-in."""
    if not ready:
        return missing_cell(COMPUTING), "the risk figures are still being computed"
    sub = sub or {}
    target = _num(sub.get("vol_target_usd"))
    if target is None:
        target = _num(config.get("vol_target_usd"))
    placeholder = bool(sub.get("vol_target_placeholder") if "vol_target_placeholder" in sub
                       else config.get("vol_target_placeholder"))
    note = str(sub.get("vol_target_note") or config.get("vol_target_note") or "")
    vol_pct, var_pct = _num(sub.get("vol_vs_target_pct")), _num(sub.get("var_vs_target_pct"))
    vol = _num(sub.get("vol_blended_ann_usd"))
    why = plain_reason(sub.get("vs_target_reason")) or "not given"
    of = html.Span(f" of {km_text(target, signed=False)}" if target is not None else "", className="tk-sub")
    tag = html.Span(" placeholder", className="risk-grey", title=plain_words(note)) if placeholder else None
    main = (html.Span(pct_text(vol_pct / 100.0)) if vol_pct is not None else missing_cell(why))
    hover = _lines(
        f"The trades showing: blended annual vol {format_cell(vol)} USD against a vol target of {format_cell(target)} USD "
        "a year" if vol is not None and target is not None else f"Vol against target: {why}",
        f"The 1-day VaR is {pct_text(var_pct / 100.0)} of the target" if var_pct is not None else "",
        str(sub.get("vol_note") or ""),
        "blended vol: 2/3 the trailing 500 days + 1/3 the 2008-2010 window (trailing alone where the history "
        "does not reach it)", note)
    return html.Span([main, of, tag]), hover


def headline(data: dict, v: dict, risk: Optional[dict], sub: Optional[dict]) -> html.Div:
    """One light line of what neither the header nor the table's Book row shows (layout wave 2,
    one place per number): the VaR of the rows showing, its share of the vol target, how much the
    trades diversify each other and the lowest hedge %. The trade count, the daily risk together,
    the leftover and the FX unhedged are the Book row's; the mock-history caveat is said once, in
    the grey history line under it."""
    config = data.get("config") or {}
    ready = risk is not None

    def pending():
        return missing_cell(COMPUTING)

    def history_figure(key, reason_key, excl=True):
        if not ready:
            return pending()
        val = _num((sub or {}).get(key))
        if val is None:
            return missing_cell(plain_reason((sub or {}).get(reason_key)) or "no figure")
        left = (sub or {}).get("left_out") or []
        return html.Span([km_cell(val, signed=False, colour=False),
                          marker(f"excl. {len(left)}", _lines(*(f"{x.get('trade')}: {plain_reason(x.get('reason'))}"
                                                               for x in left)), "marker--small")
                          if (left and excl) else None])

    target_value, target_hover = vs_target(sub, ready, config)
    lowest: Any = NA
    lowest_hover = "no hedge % among the rows showing"
    known = [(t, _num(r.get("hedge_pct"))) for t, r in v["shown_rows"] if r and _num(r.get("hedge_pct")) is not None]
    if not ready:
        lowest = pending()
    elif known:
        t, hp = min(known, key=lambda x: x[1])
        lowest = html.Span([f"{t.get('trade')} ", html.Span(pct_text(hp / 100.0),
                                                           className="cell-amber" if hp < HEDGE_AMBER else None)])
        lowest_hover = "the single worst row's hedge %, named (hedge % is never totalled)"
    alone, together = _num((sub or {}).get("sum_daily_risk_usd")), _num((sub or {}).get("daily_risk_usd"))
    div_hover = _lines("How much the trades offset each other: each trade's daily risk on its own, added up, less "
                       "their daily risk together (the Book row)",
                       f"Alone, added: {format_cell(alone)} USD" if alone is not None else "",
                       f"Together: {format_cell(together)} USD" if together is not None else "")
    items = [
        ("VaR 1 day 95 %", history_figure("var_usd", "var_reason"), _var_definition(config)),
        ("Of vol target", target_value, target_hover),
        ("Diversification", history_figure("diversification_usd", "sum_daily_risk_reason", excl=False), div_hover),
        ("Lowest hedge", lowest, lowest_hover),
    ]
    reach = source_line(data, risk, sub)
    return html.Div([tf.headline(items), reach], className="risk-headline-block")


def source_line(data: dict, risk: Optional[dict], sub: Optional[dict]) -> html.Div:
    """One grey line: the history's reach and source; the mock-data sentence while it is not real."""
    research = data.get("research") or {}
    parts = []
    if sub and sub.get("first_date"):
        parts.append(f"history {_day(sub.get('first_date'))} to {_day(sub.get('last_date'))} ({sub.get('days')} days), "
                     "research app's settlements")
    elif risk is None:
        parts.append("computing the risk figures")
    elif not (risk or {}).get("available"):
        parts.append(f"no risk history: {plain_reason((risk or {}).get('reason')) or 'not available'}")
    kind = (risk or {}).get("source_kind") or research.get("kind")
    note = (risk or {}).get("source_note") or research.get("note")
    if kind != "real" and note:
        parts.append(str(note))
    return html.Div(" · ".join(cap(str(p)) for p in parts), className="risk-reach",
                    title=plain_words("The price history every risk figure here is drawn from: the research app's "
                                      "daily settlements, read-only, never a mark or a P&L figure."))


# --------------------------------------------------------------------------- issues and CSV
def issue_items(data: dict, v: dict, risk: Optional[dict], sub: Optional[dict]) -> List[Any]:
    """The drawer's rows as (kind, where, reason): the kind of problem, the trade or contract it is
    about, the reason (`formatting.issues_drawer` draws them as its table)."""
    items: List[Any] = [(str(label), "", sentence) for label, sentence in data.get("errors") or []]
    for n in (data.get("trade_book") or {}).get("notes") or []:
        items.append(("Trades", "", str(n)))
    if risk is not None and not risk.get("available"):
        items.append(("Risk", "", f"no risk figures: {plain_reason(risk.get('reason')) or 'no history'}"))
    for t, r in v["shown_rows"]:
        if r is not None and not r.get("included", True):
            items.append(("Not in the VaR", str(t.get("trade")), plain_reason(r.get("reason")) or "no history"))
        elif r is not None and r.get("partial"):
            items.append(("Without hedge", str(t.get("trade")),
                          "counted without its currency hedge: " + plain_reason(r.get("partial_reason"))))
    if risk is not None and risk.get("partial_note"):
        items.append(("Without hedge", "", plain_reason(risk["partial_note"])))
    for m in (risk or {}).get("missing") or []:
        items.append(("Positions", "", plain_reason(m)))
    liq = data.get("liquidity") or {}
    if liq and not liq.get("available"):
        items.append(("Liquidity", "", plain_reason(liq.get("reason")) or "not computed"))
    items += [("Liquidity", str(x.get("instrument_id") or x.get("trade_id")),
               "not in the liquidity check: " + plain_reason(x.get("reason")))
              for x in liq.get("skipped") or []]
    if liq.get("placeholder"):
        items.append(("Liquidity", "", "the thresholds are placeholders until Jason gives his"))
    for reason in (data.get("curve") or {}).get("reasons") or []:
        items.append(("Exposure", "", plain_reason(reason)))
    pc = (risk or {}).get("price_check") or {}
    if pc.get("flagged"):
        items.append(("Research prices", "", str(pc.get("sentence") or "")))
    research = data.get("research") or {}
    if research.get("kind") != "real":
        items.append(("Research", "", research.get("note") or "the research history is not verified as real"))
    if data.get("margin") is not None and risk_limits.margin_limits_real(data.get("margin"), data.get("checks")):
        items += [("Not in the margin", "", plain_reason(x)) for x in (data.get("margin") or {}).get("reasons") or [] if x]
    return items


def csv_frame(data: dict, v: dict, risk: Optional[dict], sort: Optional[dict]) -> pd.DataFrame:
    rows = []
    for t, r in sort_rows(data, v["shown_rows"], sort):
        r = r or {}
        lo_v, lo_why, lo_lines = leftover_of(t)
        fx_v, fx_why, _a = fx_unhedged_of(t)
        p = liq_of(data, r) or {}
        h = t.get("hedge") or {}
        rows.append({
            "Trade": t.get("trade"), "Type": tf.trade_type_label(t), "Commodity": tf.family_label(t),
            "What it is": t.get("what_it_is"), "In the VaR": r.get("included"), "Why not": r.get("reason"),
            "Daily risk USD": r.get("daily_risk_usd"), "VaR alone USD": r.get("standalone_var"),
            "Share of book": r.get("share_of_book"), "Part of book VaR USD": r.get("contribution_var"),
            "Hedge %": r.get("hedge_pct"), "Bigger leg": r.get("hedge_leg"), "Hedge moves": r.get("hedge_moves"),
            "Lot ratio held": r.get("lot_ratio"), "Best-fit ratio": r.get("best_fit_ratio"),
            "Legs' correlation": r.get("leg_correlation"), "Best-fit R2": r.get("best_fit_r2"),
            "Ratio legs": " / ".join(r.get("ratio_legs") or []),
            "Legs' daily risk USD": "; ".join(f"{x.get('name') or x.get('contract_id')}: {x.get('daily_risk_usd')}"
                                              for x in r.get("legs_risk") or []),
            "Legs' correlation with the other side": "; ".join(
                f"{x.get('name') or x.get('contract_id')}: {x.get('corr_other_side')}" for x in r.get("legs_risk") or []),
            "Move in sigma": r.get("move_sigma"),
            "Leftover USD per 1 %": lo_v, "Leftover legs": "; ".join(lo_lines), "Leftover note": lo_why,
            "FX unhedged USD": fx_v, "FX note": fx_why, "China exposure USD": h.get("exposure_usd"),
            "Hedge USD": h.get("hedge_usd"), "Coverage": h.get("coverage"),
            "Days to exit": p.get("days_to_exit"), "Weakest leg": p.get("weakest"), "% of OI": p.get("pct_of_oi"),
            "Liquidity level": p.get("level"), "History source": r.get("source_kind"),
            "History note": r.get("source_note"),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- render
def render_parts(as_of: Optional[str], db_path, state: Optional[dict] = None, sort: Optional[dict] = None,
                 opened: Sequence[str] = (), wait_risk: bool = False) -> dict:
    """{body, shown, headline, table, foot, names, risk_ready} for `as_of`."""
    out = {"body": None, "shown": False, "headline": None, "table": None, "foot": None, "names": [], "risk_ready": True}
    if not as_of:
        out["body"] = message_box("No as-of date available.")
        return out
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        out["body"] = message_box(f"Database not available ({exc}).")
        return out
    try:
        if trades_on_file(conn) == 0:
            out["body"] = empty_state(idx=TAB)
            return out
        data = gather(conn, as_of)
        risk = _risk(conn, as_of, wait_risk)
        v = view(data, risk, state)
        tbl, names = table(conn, data, v, risk, sort, opened)
        sub = (subset(conn, as_of, [str(r.get("trade")) for _t, r in v["shown_rows"] if r])
               if risk is not None else None)
        out.update(shown=True, headline=headline(data, v, risk, sub), table=tbl, names=names,
                   risk_ready=risk is not None)
        # margin and limits: their block once either is real, else one row of the drawer (never a loose line)
        real = risk_limits.margin_limits_real(data.get("margin"), data.get("checks"))
        items = issue_items(data, v, risk, sub)
        if not real:
            items.append(("Margin and limits", "", "Shown once real margin rates or limits are set: the margin rates "
                          "in the limits file are placeholders and no desk or exchange limit is set yet"))
        out["foot"] = html.Div([risk_limits.margin_limits_section(data.get("margin"), data.get("checks")) if real else None,
                                issues_drawer(items, id=ISSUES_ID)], className="tk-foot")
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("Risk could not be built for %s", as_of)
        out["body"] = html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Risk could not be computed for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    from ui.tabs.book import _tidy_parts
    return _tidy_parts(out)


def render_folds(as_of: Optional[str], db_path, state: Optional[dict], folds: Sequence[str],
                 commodity: Optional[str], unit: Optional[str]) -> Any:
    """The four folds for the trades showing; each body built only when its fold is open."""
    if not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        if trades_on_file(conn) == 0:
            return None
        data = gather(conn, as_of)
        risk = _risk(conn, as_of, False)
        v = view(data, risk, state)
        open_set = set(folds or [])
        n_options = (len({str(r.get("contract_id")) for r in (v["curve"].get("rows") or [])
                          if str(r.get("product") or "") == rf.OPTION})
                     + rf.fx_options_held(conn, as_of))
        kids = []
        for key, build in ((rf.FOLD_NET, lambda o: rf.net_fold(conn, v, o, commodity, unit or rf.DEFAULT_UNIT)),
                           (rf.FOLD_CCY, lambda o: rf.currency_fold(v, o, conn)),
                           (rf.FOLD_GREEKS, lambda o: rf.greeks_fold(conn, v, o, n_options)),
                           (rf.FOLD_STRESS, lambda o: rf.stress_fold(v, stress_result(conn, as_of) if o else None, o,
                                                                    rf.FOLD_STRESS_MORE in open_set)),
                           (rf.FOLD_PRICE, lambda o: rf.price_check_fold((risk or {}).get("price_check"), o))):
            try:
                built = build(key in open_set)
                if built is not None:
                    kids.append(built)
            except Exception as exc:  # noqa: BLE001 -- one fold's reason, never the tab
                log.exception("Risk: the %s fold failed for %s", key, as_of)
                kids.append(html.Div(missing_cell(f"the {key} fold could not be built ({type(exc).__name__}: {exc})"),
                                     className="book-card risk-fold"))
        return tidy(html.Div(kids, className="risk-folds"))
    except Exception as exc:  # noqa: BLE001
        log.exception("Risk folds could not be built for %s", as_of)
        return message_box(f"The folds could not be built ({type(exc).__name__}: {exc}).")
    finally:
        conn.close()


def render(as_of: Optional[str], db_path, state: Optional[dict] = None) -> html.Div:
    """The whole body for a direct render (the risk figures waited for, every fold open)."""
    p = render_parts(as_of, db_path, state, wait_risk=True)
    if not p["shown"]:
        return html.Div([p["body"]])
    folds = render_folds(as_of, db_path, state, [rf.FOLD_NET, rf.FOLD_CCY, rf.FOLD_GREEKS, rf.FOLD_STRESS, rf.FOLD_PRICE], None,
                         rf.DEFAULT_UNIT)
    return html.Div([p["headline"], html.Div(p["table"], className="book-card book-main tk-card"), folds, p["foot"]])


def options_of(as_of: str, db_path) -> Dict[str, List[dict]]:
    """The filter bar's choices (the trades present, as on the Book)."""
    conn = _open(db_path)
    try:
        if trades_on_file(conn) == 0:
            return {}
        data = gather(conn, as_of)
        return tf.options_for([t for t in (data.get("trade_book") or {}).get("trades") or []])
    finally:
        conn.close()


# --------------------------------------------------------------------------- layout and callbacks
def layout(default_date: Optional[str] = None) -> html.Div:
    """The shell: the message slot, then (hidden with no book) the bar, the headline, the table
    card, the folds and the footer; the session stores and the risk poll."""
    return html.Div(className="risk-tab", children=[
        html.Div(id=BODY_ID, children=[message_box("Loading the risk figures...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            html.Div(id=HEADLINE_ID),
            html.Div(className="book-card book-main tk-card", children=[
                html.Div(className="tk-strip", children=[
                    about("Risk by trade", f"{QUESTION} One row per open trade, by its share of the book's VaR; the "
                                           "trades with no price history at the bottom, their reason on the row's "
                                           "\"i\". Click a row for its legs. The first row is the rows showing "
                                           "together: their daily risk recomputed, never added.", level="span",
                          className="tk-title"),
                    tf.bar_slot(TAB),
                    html.Button("Expand all", id=EXPAND_ALL_ID, n_clicks=0, className="btn btn--ghost"),
                    html.Button("Collapse all", id=COLLAPSE_ALL_ID, n_clicks=0, className="btn btn--ghost"),
                    html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                                title="The rows showing at full figures, every column and every hover figure"),
                    dcc.Download(id=DOWNLOAD_ID)]),
                html.Div(id=TABLE_SLOT_ID, className="tk-table-slot"),
            ]),
            html.Div(id=FOLDS_ID),
            html.Div(id=FOOT_ID),
        ]),
        dcc.Store(id=OPEN_STORE_ID, storage_type="session"),
        dcc.Store(id=SORT_STORE_ID, storage_type="session"),
        dcc.Store(id=FOLDS_STORE_ID, storage_type="session"),
        dcc.Store(id=COMMODITY_STORE_ID, storage_type="session"),
        dcc.Store(id=UNIT_STORE_ID, storage_type="session"),
        dcc.Store(id=SHOWN_STORE_ID),
        dcc.Store(id=RISK_READY_ID),
        dcc.Interval(id=RISK_POLL_ID, interval=1500, n_intervals=0, disabled=True),
    ])


build_layout = layout


def _clicked() -> bool:
    trig = dash.ctx.triggered or []
    return bool(trig and trig[0].get("value"))


def _toggle(items: Optional[list], key: str) -> list:
    cur = list(items or [])
    return [x for x in cur if x != key] if key in cur else cur + [key]


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    tf.register_bar(app, TAB, get_db_path, options_of)

    @app.callback(
        Output(BODY_ID, "children"), Output(CONTENT_ID, "style"), Output(HEADLINE_ID, "children"),
        Output(TABLE_SLOT_ID, "children"), Output(FOOT_ID, "children"), Output(SHOWN_STORE_ID, "data"),
        Output(RISK_POLL_ID, "disabled"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(SORT_STORE_ID, "data"), Input(OPEN_STORE_ID, "data"), Input(RISK_READY_ID, "data"),
    )
    def _render(as_of, _rev, state, sort, opened, _ready):
        p = render_parts(as_of, get_db_path(), state, sort, opened or [])
        if not p["shown"]:
            return p["body"], HIDDEN, None, None, None, [], True
        return (None, {}, compact(p["headline"]), compact(p["table"]), compact(p["foot"]), p["names"],
                p["risk_ready"])

    @app.callback(
        Output(FOLDS_ID, "children"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(FOLDS_STORE_ID, "data"), Input(COMMODITY_STORE_ID, "data"), Input(UNIT_STORE_ID, "data"),
        Input(RISK_READY_ID, "data"),
    )
    def _folds(as_of, _rev, state, folds, commodity, unit, _ready):
        return compact(render_folds(as_of, get_db_path(), state, folds or [], commodity, unit))

    @app.callback(Output(RISK_READY_ID, "data"), Input(RISK_POLL_ID, "n_intervals"), State(AS_OF_STORE_ID, "data"),
                  prevent_initial_call=True)
    def _risk_poll(_n, as_of):
        if not as_of:
            return dash.no_update
        conn = _open(get_db_path())
        try:
            ready = _risk(conn, as_of, False) is not None
        finally:
            conn.close()
        return f"{as_of}-{dt.datetime.now().timestamp()}" if ready else dash.no_update

    @app.callback(Output(OPEN_STORE_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  Input(EXPAND_ALL_ID, "n_clicks"), Input(COLLAPSE_ALL_ID, "n_clicks"),
                  State(OPEN_STORE_ID, "data"), State(SHOWN_STORE_ID, "data"), prevent_initial_call=True)
    def _open_rows(_rows, _all, _none, current, shown):
        if not _clicked():
            return dash.no_update
        trig = dash.ctx.triggered_id
        if trig == EXPAND_ALL_ID:
            return list(shown or [])
        if trig == COLLAPSE_ALL_ID:
            return []
        if isinstance(trig, dict):
            return _toggle(current, str(trig.get("idx") or ""))
        return dash.no_update

    @app.callback(Output(SORT_STORE_ID, "data"), Input({"type": SORT_TYPE, "idx": ALL}, "n_clicks"),
                  State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _sort(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        from ui.tabs.data_kit import next_sort
        return next_sort(current, str(trig.get("idx") or ""))

    @app.callback(Output(FOLDS_STORE_ID, "data"), Input({"type": rf.FOLD_TYPE, "idx": ALL}, "n_clicks"),
                  State(FOLDS_STORE_ID, "data"), prevent_initial_call=True)
    def _fold(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        return _toggle(current, str(trig.get("idx") or ""))

    @app.callback(Output(COMMODITY_STORE_ID, "data"), Input({"type": rf.COMMODITY_ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(COMMODITY_STORE_ID, "data"), prevent_initial_call=True)
    def _commodity(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        key = str(trig.get("idx") or "")
        return None if current == key else key

    @app.callback(Output(UNIT_STORE_ID, "data"), Input({"type": rf.UNIT_TYPE, "idx": ALL}, "value"),
                  State(UNIT_STORE_ID, "data"), prevent_initial_call=True)
    def _unit(values, current):
        value = next((x for x in values or [] if x), None)
        if not value or value == (current or rf.DEFAULT_UNIT):
            return dash.no_update
        return value

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(tf.STORE_ID, "data"), State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, state, sort):
        if not n_clicks or not as_of:
            return dash.no_update
        conn = _open(get_db_path())
        try:
            data = gather(conn, as_of)
            risk = _risk(conn, as_of, False)
            frame = csv_frame(data, view(data, risk, state), risk, sort)
        finally:
            conn.close()
        return dcc.send_data_frame(frame.to_csv, f"risk_{as_of}.csv", index=False)
