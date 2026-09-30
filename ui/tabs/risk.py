"""Risk tab: "How much can I lose, and is each trade really hedged?" (rebuilt simple on 2026-09-30,
user: "nothing work - it looks shit and i dont even understand what its trying to do"; the price
history is the book database's own Bloomberg daily history since the same day, no research app).

Top to bottom:
  1. the tab's title;
  2. one headline card, one sentence: "On a bad day (1 in 20) the book can lose about $X" (the
     1-day 95 % VaR of the trades showing, `engine.risk.trades.subset_var`, recomputed for them,
     never the rows added), the limit beside it ("No limit set" while the vol target is a
     placeholder), the trades' daily risks added up and what holding them together saves on hover;
  3. one table, one row per open trade (`engine.risk.trades.trade_risk`): Trade | What it is (the
     Book's own words) | Daily risk (1 sigma USD) | Share of book | Hedged (plain words from the
     engine's hedge %: Well / Partly / Barely hedged, Adds risk, Outright), sorted by daily risk,
     largest first; its first row the trades showing together (daily risk recomputed, never
     added). A row's click opens a small panel: the ratio held against the best fit, the leftover,
     the FX unhedged, the days to exit, the z-score and the worst stresses on that trade;
  4. the worst stresses (`risk_folds.stress_card`): five scenarios, one line each, a click for
     the trades it hits;
  5. three folds, closed by default: Net by commodity, Currency (the one home of FX Net / Gross
     USD delta), Option Greeks (only with an option held);
  6. the one "Data issues (N)" drawer with every reason.
Before the first Bloomberg price history is on file the headline and the table give way to one
card saying so (`empty_card`): never a table of dashes.

What is read, never recomputed: `trade_risk` / `subset_var` (daily risk, share, hedge %, the lot
ratio against the best fit, z), `engine.spreads.trade_book` (`shared_trade_book`: the words, the
leftover per 1 % move and the currency hedge, exposure + hedge = unhedged, two engine figures
added), `engine.limits.liquidity` (`risk_limits.limits_pass`: days to exit), `engine.stress`.
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
    plain_words, row_info, z_text,
)
from ui.tabs.header import AS_OF_STORE_ID
from ui.tabs.risk_limits import plain_reason  # noqa: F401 -- re-exported: the Risk tab's plain-words pass

log = logging.getLogger(__name__)

TAB = "risk"
BODY_ID = "risk-body"                         # a message or the no-book empty state
CONTENT_ID = "risk-content"                   # the headline, table card, stresses, folds, footer (hidden with no book)
HEADLINE_ID = "risk-headline"
CARD_ID = "risk-table-card"                   # the table's card (hidden before any price history)
TABLE_SLOT_ID = "risk-table-slot"
TABLE_ID = "risk-table"
FOLDS_ID = "risk-folds"
FOOT_ID = "risk-foot"
ISSUES_ID = "risk-issues"
CSV_BUTTON_ID = "risk-csv"
DOWNLOAD_ID = "risk-download"
EXPAND_ALL_ID = "risk-expand-all"
COLLAPSE_ALL_ID = "risk-collapse-all"
OPEN_STORE_ID = "risk-open-trades"            # session: the trades whose panel is open
SORT_STORE_ID = "risk-sort-store"             # session: {"key", "dir"} or None
FOLDS_STORE_ID = "risk-folds-store"           # session: the open folds' and stress lines' keys
COMMODITY_STORE_ID = "risk-commodity-store"   # session: the commodity whose months and curves show
UNIT_STORE_ID = "risk-grid-unit-store"        # session: the month grid's unit
SHOWN_STORE_ID = "risk-shown"                 # the trades showing (Expand all)
RISK_READY_ID = "risk-ready"
RISK_POLL_ID = "risk-poll"
ROW_TYPE = "risk-trade-row"                   # a trade row: {"type", "idx": trade name}
SORT_TYPE = "risk-sort"                       # a column title: {"type", "idx": column key}
NA = MISSING
HIDDEN = {"display": "none"}
QUESTION = "How much can I lose, and is each trade really hedged?"
COMPUTING = "the risk figures are still being computed"
RATIO_APART = 0.20                            # his ratio and the best fit more than 20 % apart: amber

# Hedged, in words from the engine's hedge % (1 - the trade's volatility over its bigger leg's):
# (lowest hedge %, words, amber); below the last, "Adds risk"; a one-leg trade "Outright".
HEDGE_BANDS: Tuple[Tuple[float, str, bool], ...] = ((80.0, "Well hedged", False), (40.0, "Partly hedged", False),
                                                     (0.0, "Barely hedged", True))
ADDS_RISK, OUTRIGHT = "Adds risk", "Outright"
HEDGE_ORDER = {"Well hedged": 0, "Partly hedged": 1, "Barely hedged": 2, ADDS_RISK: 3, OUTRIGHT: 4}
HEDGE_METHOD = ("Hedge %: 1 minus the trade's daily P&L volatility over its bigger leg's alone, over the last year "
                "(2-day moves for legs closing hours apart). Well hedged from 80 %, partly from 40 %, barely from 0; "
                "below 0 the other side adds risk.")

# The columns: key, title, alignment ('l' = left), the title's definition, sortable.
COLUMNS: Tuple[Tuple[str, str, str, str, bool], ...] = (
    ("trade", "Trade", "l", "Jason's trade name; click a row for its figures.", True),
    ("what", "What it is", "l", "The trade in words, as on the Book.", False),
    ("risk", "Daily risk", "", "A typical day's move of the trade's P&L: one standard deviation of its daily P&L over "
                               "the last year, today's position held across Bloomberg's price history.", True),
    ("share", "Share of book", "", "The trade's part of the book's bad-day loss (its component VaR): the shares add up "
                                   "to 100 %; a negative share offsets the rest of the book.", True),
    ("hedged", "Hedged", "l", HEDGE_METHOD, True),
)
SORTABLE = {k for k, _t, _c, _h, s in COLUMNS if s}


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


def dollars(value: Optional[float]) -> str:
    """'$29,238' (a positive figure, full, thousands separators)."""
    return f"${format_cell(abs(value))}" if value is not None else MISSING


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


# --------------------------------------------------------------------------- gathering
def _outside_key() -> tuple:
    from ui.tabs.blotter_pricing import config_inputs_key
    return config_inputs_key()


def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """Everything the tab shows but the risk figures (computed apart, so nothing waits for them),
    once per database revision, as-of and config files. Shared: never edit."""
    from ui.tabs.blotter_pricing import screen_memo
    return screen_memo("risk-base", conn, as_of, lambda: _gather(conn, as_of), extra=_outside_key())


def _gather(conn: sqlite3.Connection, as_of: str) -> dict:
    from ui.tabs.blotter_pricing import price_history_summary, shared_curve, shared_trade_book
    data: Dict[str, Any] = {"as_of": as_of, "errors": [], "n_trades": trades_on_file(conn)}
    if not data["n_trades"]:
        return data
    data["history"] = price_history_summary(conn)
    try:
        from data.contracts import load_roots
        data["roots"] = dict(load_roots())
    except Exception:  # noqa: BLE001 -- the ids stand for the names
        data["roots"] = {}
    try:
        tb = shared_trade_book(conn, as_of)
        from ui.tabs.book import _with_words        # What it is in the Book's own words (copies, never edits)
        data["trade_book"] = dict(tb, trades=[_with_words(t, data["roots"]) for t in tb.get("trades") or []])
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
    return data


def has_history(data: dict) -> bool:
    """True once the book database holds any Bloomberg price history (the first pull fetches it)."""
    return bool((data.get("history") or {}).get("rows"))


def stress_result(conn: sqlite3.Connection, as_of: str) -> dict:
    """`engine.stress.commodity_stress` on the shared positions and spreads and the book database's
    price history for the replays, once per revision."""
    from ui.tabs.blotter_pricing import screen_memo, shared_curve, shared_spreads

    def compute():
        from engine.risk.commodity_history import load_commodity_history
        from engine.stress import commodity_stress
        try:
            spreads = shared_spreads(conn, as_of)
        except Exception:  # noqa: BLE001 -- the engine reads them itself and names the failure
            spreads = None
        history = load_commodity_history(conn)
        return commodity_stress(conn, as_of, positions=shared_curve(conn, as_of), spreads=spreads,
                                history=history.window_move if getattr(history, "available", False) else None)
    try:
        return screen_memo("risk-stress", conn, as_of, compute, extra=_outside_key())
    except Exception as exc:  # noqa: BLE001 -- the card says why
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
    name = str(r.get("name") or "")
    return {"trade": r.get("trade"), "pseudo": True, "type": str(r.get("type") or ""), "commodity_family": "",
            "what_it_is": name, "what_title": name, "what_sub": "", "what_others": [], "legs": [], "flags": [],
            "trade_ids": list(r.get("trade_ids") or []), "leftover": {}, "hedge": {}, "status": "open"}


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
             and (not cols or tf.keeps_cols((t, r), cols, lambda item, col: filter_value(data, item[0], item[1], col),
                                            lambda item, col: filter_lists(item[0], item[1], col)))]
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
    """(the China legs' exposure plus their hedge, why none, whether it applies): not applicable
    when every open leg is in USD."""
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


def hedge_word(t: dict, r: Optional[dict]) -> Tuple[Optional[str], bool]:
    """(Hedged in words, amber) from the engine's hedge %; (None, False) when there is no figure
    and the trade is not an outright (the cell is then a dash with the engine's reason)."""
    hp = _num((r or {}).get("hedge_pct"))
    if hp is None and r is not None and not r.get("included", True):
        return None, False                    # not in the risk figures: the dash says why
    if hp is None:
        one_leg = str((r or {}).get("hedge_reason") or "").startswith("one leg: nothing hedges it")
        return (OUTRIGHT, False) if (one_leg or tf.type_code(t) == "OUTRIGHT") and r is not None else (None, False)
    for floor, words, amber in HEDGE_BANDS:
        if hp >= floor:
            return words, amber
    return ADDS_RISK, True


# --------------------------------------------------------------------------- sorting and filters
def sort_value(data: dict, t: dict, r: Optional[dict], key: str) -> Any:
    if key == "trade":
        return str(t.get("trade") or "").lower()
    if key == "risk":
        return _num((r or {}).get("daily_risk_usd"))
    if key == "share":
        return _num((r or {}).get("share_of_book"))
    if key == "hedged":
        word, _a = hedge_word(t, r)
        return None if word is None else (HEDGE_ORDER[word], -(_num((r or {}).get("hedge_pct")) or 0.0))
    return None


def sort_rows(data: dict, rows: Sequence[Tuple[dict, Optional[dict]]], sort: Optional[dict]) -> List[tuple]:
    """By daily risk, largest first (default), the trades with no figure last by name; or by the
    column chosen, a missing value last."""
    key = (sort or {}).get("key")
    if key not in SORTABLE:
        key, desc = "risk", True
    else:
        desc = (sort or {}).get("dir") != "asc"
    have = [x for x in rows if sort_value(data, x[0], x[1], key) is not None]
    rest = [x for x in rows if x not in have]
    have.sort(key=lambda x: sort_value(data, x[0], x[1], key), reverse=desc)
    return have + sorted(rest, key=lambda x: str(x[0].get("trade") or ""))


# The column filters (the funnel in each heading, user 2026-09-29): Trade holds the trade names, What
# it is the types and commodity families (both carried across Book, P&L and Risk); Daily risk and
# Share one comparison each (Share in percent: "> 10" = above 10 %); Hedged a tick list of its words.
LIST_FUNNELS = {"trade": ("trade",), "what": ("type", "commodity")}
NUMBER_FUNNELS = {"risk": "The trade's daily risk in USD.", "share": "The share of the book's bad-day loss, in percent."}
HEDGE_OPTIONS = [{"label": w, "value": w} for w in sorted(HEDGE_ORDER, key=HEDGE_ORDER.get)]


def filter_value(data: dict, t: dict, r: Optional[dict], col: str) -> Optional[float]:
    """A trade's figure for a number column's filter, as its cell shows it (never recomputed)."""
    if col == "risk":
        return _num((r or {}).get("daily_risk_usd"))
    if col == "share":
        v = _num((r or {}).get("share_of_book"))
        return None if v is None else v * 100.0
    return None


def filter_lists(t: dict, r: Optional[dict], col: str) -> List[str]:
    """A trade's values for a tick-list filter of its own column (Hedged)."""
    if col == "hedged":
        word, _a = hedge_word(t, r)
        return [word] if word else []
    return []


def _hedged_funnel(state: Optional[dict]) -> html.Details:
    value = tf.tab_filters(state, TAB).get("hedged")
    ticked = value if isinstance(value, list) else []
    return tf.funnel(f"{TAB}:hedged", tf.pop_list(tf.col_id(TAB, "hedged"), "Hedged", HEDGE_OPTIONS, ticked,
                                                  search=False),
                     bool(ticked), tf.option_words(HEDGE_OPTIONS, ticked))


def head(sort: Optional[dict], state: Optional[dict] = None,
         options: Optional[Dict[str, List[dict]]] = None) -> html.Thead:
    ths = []
    half = len(COLUMNS) // 2
    for i, (key, title, cls, tip, sortable) in enumerate(COLUMNS):
        pop = None
        if key in LIST_FUNNELS:
            pop = tf.trade_funnel(TAB, state, options or {}, LIST_FUNNELS[key], key)
        elif key in NUMBER_FUNNELS:
            pop = tf.number_funnel(TAB, state, key, hint=f"{NUMBER_FUNNELS[key]} {tf.NUMBER_HINT}")
        elif key == "hedged":
            pop = _hedged_funnel(state)
        ths.append(tf.head_th(title, cls, tip, sort_id={"type": SORT_TYPE, "idx": key} if sortable else None,
                              arrow=tf.arrow_of(sort, key), pop=pop, right=i > half))
    return html.Thead(html.Tr(ths))


# --------------------------------------------------------------------------- the cells
def _no_figure(r: Optional[dict], ready: bool) -> str:
    if not ready:
        return COMPUTING
    if r is None:
        return "no risk row for this trade"
    return plain_reason(r.get("reason")) or "no price history"


def _risk_td(r: Optional[dict], ready: bool) -> html.Td:
    v = _num((r or {}).get("daily_risk_usd"))
    if v is None:
        return html.Td(missing_cell((r or {}).get("daily_risk_reason") or _no_figure(r, ready)))
    alone = _num(r.get("standalone_var"))
    hover = _lines(f"On a bad day (1 in 20) on its own: {dollars(alone)}" if alone is not None else "",
                   f"Over {r.get('daily_risk_days')} days" if r.get("daily_risk_days") else "")
    return html.Td(km_cell(v, hover=hover, signed=False, colour=False))


def _share_td(r: Optional[dict], ready: bool) -> html.Td:
    v = _num((r or {}).get("share_of_book"))
    if v is None:
        return html.Td(missing_cell((r or {}).get("share_reason") or _no_figure(r, ready)))
    cv = _num(r.get("contribution_var"))
    hover = _lines(f"{dollars(cv)} of the book's bad-day loss" if cv is not None and cv >= 0 else
                   f"It takes {dollars(cv)} off the book's bad-day loss" if cv is not None else "",
                   "It offsets the rest of the book (it tends to gain on the book's bad days)" if v < 0 else "")
    return html.Td(html.Span(pct_text(v), className="cell-neg" if v < 0 else None, title=plain_words(hover) or None))


def _hedged_td(t: dict, r: Optional[dict], ready: bool) -> html.Td:
    word, amber = hedge_word(t, r)
    if word is None:
        return html.Td(missing_cell((r or {}).get("hedge_reason") or _no_figure(r, ready)), className="l")
    hp = _num((r or {}).get("hedge_pct"))
    corr = _num((r or {}).get("leg_correlation"))
    if word == OUTRIGHT:
        hover = "One leg: nothing hedges it"
    else:
        hover = _lines(f"Hedge %: {pct_text(hp / 100.0)}" if hp is not None else "",
                       f"Bigger leg: {r.get('hedge_leg')}" if r.get("hedge_leg") else "",
                       f"Legs' correlation {corr:.2f}".replace("-", MINUS) if corr is not None else "",
                       plain_reason(r.get("hedge_reason")) or "", HEDGE_METHOD)
    return html.Td(html.Span(word, className="cell-amber" if amber else None, title=plain_words(hover) or None),
                   className="l")


def row_reasons(t: dict, r: Optional[dict], ready: bool) -> List[str]:
    """The reasons behind the row's "i": why the trade is not in the risk figures (its dashes then
    all share that reason), or that it is counted without its currency hedge."""
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


def _what_td(t: dict, r: Optional[dict]) -> html.Td:
    if t.get("pseudo"):
        return html.Td(html.Div(cap(str(t.get("what_it_is") or "")) or MISSING, className="tk-clip tk-what-main"),
                       className="l tk-what")
    from ui.tabs.book import _what_td as book_what
    return book_what(t, r)


def trade_tr(data: dict, t: dict, r: Optional[dict], ready: bool, opened: bool) -> html.Tr:
    name = str(t.get("trade") or "")
    name_cell = [html.Span("▾ " if opened else "▸ ", className="tk-chev"),
                 html.Span(name, className="tk-name"), row_info(row_reasons(t, r, ready))]
    return html.Tr([html.Td(name_cell, className="l"), _what_td(t, r), _risk_td(r, ready), _share_td(r, ready),
                    _hedged_td(t, r, ready)],
                   id={"type": ROW_TYPE, "idx": name}, n_clicks=0,
                   className="tk-row" + (" tk-row--open" if opened else "") + (" tk-row--pseudo" if t.get("pseudo") else "")
                   + ("" if (r or {}).get("included", True) else " risk-row--nohist"))


def total_tr(rows: Sequence[tuple], all_rows: Sequence[tuple], sub: Optional[dict], ready: bool,
             filtered: bool) -> html.Tr:
    """The trades showing together: their daily risk recomputed (never the rows added), their shares
    of the book added (component shares add up), no hedge word (never totalled)."""
    named = [t for t, _r in all_rows if not t.get("pseudo")]
    shown_named = [t for t, _r in rows if not t.get("pseudo")]
    label = (f"Filtered · {len(shown_named)} of {_plural(len(named), 'open trade')}" if filtered
             else f"Book · {_plural(len(named), 'open trade')}")
    from ui.tabs.formatting import sum_known
    share = sum_known([(_num((r or {}).get("share_of_book")), f"{t.get('trade')}: no share") for t, r in rows])
    return html.Tr([html.Td(label, className="l"), html.Td(""), _together_td(sub, ready), _share_sum_td(share),
                    html.Td("")], className="tk-total book-total")


def _together_td(sub: Optional[dict], ready: bool) -> html.Td:
    if not ready:
        return html.Td(missing_cell(COMPUTING))
    v = _num((sub or {}).get("daily_risk_usd"))
    if v is None:
        return html.Td(missing_cell(plain_reason((sub or {}).get("daily_risk_reason")) or "no figure"))
    left = (sub or {}).get("left_out") or []
    alone = _num((sub or {}).get("sum_daily_risk_usd"))
    hover = _lines("The trades' daily P&L added day by day, one standard deviation: recomputed for these trades, "
                   "never the rows added",
                   f"Their daily risks added up: {dollars(alone)}" if alone is not None else "")
    return html.Td([km_cell(v, hover=hover, signed=False, colour=False),
                    marker(f"excl. {len(left)}", _lines(*(f"{x.get('trade')}: {plain_reason(x.get('reason'))}"
                                                         for x in left)), "marker--small") if left else None])


def _share_sum_td(tot: Tuple[Optional[float], int, List[str]]) -> html.Td:
    v, n, why = tot
    if v is None:
        return html.Td(missing_cell(_lines(*why[:8]) or "no figure") if why else "")
    return html.Td([html.Span(pct_text(v), title="The shares of the rows showing added (component shares add up)"),
                    marker(f"excl. {n}", _lines(*why[:8]), "marker--small") if n else None])


# --------------------------------------------------------------------------- the panel
def _kv(title: str, rows: List[Tuple[str, Any, str]], hover: str = "") -> html.Table:
    """A small key / value table under its own title (the panel's two parts)."""
    trs = [html.Tr([html.Td(k, className="tk-kv-k"),
                    html.Td(cap(v) if isinstance(v, str) else v, title=cap(plain_words(h)) or None)])
           for k, v, h in rows]
    return html.Table([html.Thead(html.Tr(html.Th(title, colSpan=2, className="l", title=hover or None))),
                       html.Tbody(trs)], className="tk-table tk-kv risk-kv")


def panel_facts(data: dict, t: dict, r: Optional[dict]) -> List[Tuple[str, Any, str]]:
    """The panel's figures: ratio held against the best fit, leftover, FX unhedged, days to exit, z."""
    out: List[Tuple[str, Any, str]] = []
    ready = r is not None
    his, fit, apart = ratio_parts(r)
    corr = _num((r or {}).get("leg_correlation"))
    if his is not None or fit is not None:
        text = (f"{_ratio_text(his) if his is not None else MISSING} held · "
                f"{_ratio_text(fit) if fit is not None else MISSING} best fit")
        legs = (r or {}).get("ratio_legs") or []
        hover = _lines(f"Lots of {legs[1]} per lot of {legs[0]}" if len(legs) == 2 else "",
                       "More than 20 % apart" if apart else "",
                       "The best fit: the lots that would have minimised the pair's daily P&L swings over the last "
                       "year")
        out.append(("Ratio", html.Span(text, className="cell-amber" if apart else None), hover))
        if corr is not None:
            out.append(("Correlation", f"{corr:.2f}".replace("-", MINUS),
                        "The two legs' daily P&L as held: a working spread reads close to minus one"))
    else:
        out.append(("Ratio", missing_cell((r or {}).get("best_fit_reason") or _no_figure(r, ready)), ""))
    lo, lo_why, lo_lines = leftover_of(t)
    if lo is None:
        out.append(("Leftover", missing_cell(_lines(lo_why, *lo_lines)), ""))
    elif abs(lo) < 0.5:
        out.append(("Leftover", "None (every lot is in a spread)", plain_words(lo_why)))
    else:
        out.append(("Leftover", f"{km_text(lo)} USD per 1 % move", _lines(*lo_lines)))
    fx, fx_why, applies = fx_unhedged_of(t)
    if not applies:
        out.append(("FX unhedged", "None (every leg is in USD)", ""))
    elif fx is None:
        out.append(("FX unhedged", missing_cell(fx_why), ""))
    else:
        out.append(("FX unhedged", f"{km_text(fx)} USD", fx_hover(t)))
    out.append(("Days to exit", *_exit_value(data, t, r, ready)))
    z = _num((r or {}).get("z"))
    if z is None:
        out.append(("Z-score", missing_cell((r or {}).get("z_reason") or _no_figure(r, ready)), ""))
    else:
        pct = _num(r.get("percentile"))
        out.append(("Z-score", z_text(z) + (f" · {pct_text(pct / 100.0)} percentile" if pct is not None else ""),
                    f"The trade's level against its last {r.get('level_days') or ''} daily closes to "
                    f"{r.get('level_date') or ''}"))
    return out


def _ratio_text(v: float) -> str:
    """'1 : 1.84'; a minus when the two legs are held the same way (the engine's sign)."""
    return f"1 : {MINUS if v < 0 else ''}{abs(v):.2f}"


def _exit_value(data: dict, t: dict, r: Optional[dict], ready: bool) -> Tuple[Any, str]:
    liq = data.get("liquidity") or {}
    if not liq.get("available"):
        return missing_cell(plain_reason(liq.get("reason")) or "no liquidity check"), ""
    p = liq_of(data, r)
    if p is None:
        return missing_cell(COMPUTING if not ready else "not in the liquidity check (no exchange contract)"), ""
    days = _num(p.get("days_to_exit"))
    if days is None:
        return missing_cell(_lines("no volume data", plain_reason(p.get("reason")))), ""
    level = str(p.get("level") or "")
    names = {str(lg.get("contract_id")): str(lg.get("name") or "") for lg in t.get("legs") or []}
    weakest = str(p.get("weakest") or "")
    oi, adv = _num(p.get("pct_of_oi")), _num(p.get("pct_of_adv"))
    hover = _lines(days_exact(days), f"Weakest leg: {names.get(weakest) or weakest}",
                   f"{pct_text(oi)} of open interest" if oi is not None else "",
                   f"{pct_text(adv)} of a day's average volume" if adv is not None else "",
                   "Trading 20 % of the leg's 20-day average volume a day"
                   + (" (placeholder thresholds)" if liq.get("placeholder") else ""))
    return html.Span(f"{days_text(days)} days", className={"RED": "cell-red", "AMBER": "cell-amber"}.get(level)), hover


def panel_tr(data: dict, t: dict, r: Optional[dict], stress: Optional[dict]) -> html.Tr:
    name = str(t.get("trade") or "")
    facts = _kv("Figures", panel_facts(data, t, r))
    pid = str(t.get("position_id") or (r or {}).get("position_id") or "")
    hits = rf.trade_stress(stress, pid)
    if hits:
        stress_rows = [(n, km_cell(v, colour=True), "") for n, v in hits]
    elif stress is None:
        stress_rows = [("Stress", missing_cell("the scenarios are still being computed"), "")]
    else:
        stress_rows = [("Stress", missing_cell("no scenario moves this trade"), "")]
    parts: List[Any] = [html.Div([
        facts, _kv("Worst stresses on this trade", stress_rows,
                   "The trade's P&L in each scenario of the stress file, worst first"),
    ], className="tk-panel-legs risk-panel")]
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
          opened: Sequence[str], sub: Optional[dict]) -> Tuple[html.Table, List[str]]:
    ready = risk is not None
    shown = v["shown_rows"]
    opened_set = set(opened or [])
    stress = stress_result(conn, data["as_of"]) if opened_set else None
    body: List[Any] = [total_tr(shown, v["rows"], sub, ready, v["filtered"])]
    for t, r in sort_rows(data, shown, sort):
        name = str(t.get("trade") or "")
        is_open = name in opened_set
        body.append(trade_tr(data, t, r, ready, is_open))
        if is_open:
            try:
                body.append(panel_tr(data, t, r, stress))
            except Exception as exc:  # noqa: BLE001 -- one panel's reason, never the table
                log.exception("Risk: the panel of %s failed", name)
                body.append(html.Tr(html.Td(missing_cell(f"the figures could not be shown ({type(exc).__name__}: {exc})"),
                                            colSpan=len(COLUMNS), className="l"), className="tk-panel"))
    options = tf.options_for((data.get("trade_book") or {}).get("trades") or [])
    return (html.Table([head(sort, v["state"], options), html.Tbody(body)], id=TABLE_ID,
                       className="book-table book-grid tk-table risk-trade-table"),
            [str(t.get("trade")) for t, _r in shown])


# --------------------------------------------------------------------------- the headline
def _var_definition(config: dict) -> str:
    conf = float(config.get("var_confidence") or 0.95) * 100
    window = config.get("var_window_bd") or 252
    return (f"The 1-day VaR at {conf:g} %: the loss the trades' summed daily P&L exceeded on 1 day in 20 over the "
            f"last {window} days, today's positions held across Bloomberg's price history. Recomputed for the trades "
            "showing, never the rows added.")


def limit_words(sub: Optional[dict], ready: bool, config: dict) -> Tuple[Any, str]:
    """(the limit beside the headline, its hover): "No limit set" while the vol target is Jason's
    stand-in; else the trades' blended annual vol as a share of the target (the engine's rule)."""
    sub = sub or {}
    placeholder = bool(sub.get("vol_target_placeholder") if "vol_target_placeholder" in sub
                       else config.get("vol_target_placeholder"))
    target = _num(sub.get("vol_target_usd"))
    if target is None:
        target = _num(config.get("vol_target_usd"))
    if placeholder or target is None:
        return "No limit set", ("No vol target or loss limit is set yet: the one in the risk settings is a stand-in "
                                "until Jason gives his own")
    if not ready:
        return missing_cell(COMPUTING), ""
    vol_pct = _num(sub.get("vol_vs_target_pct"))
    if vol_pct is None:
        return missing_cell(plain_reason(sub.get("vs_target_reason")) or "not given"), ""
    return (html.Span(f"{pct_text(vol_pct / 100.0)} of the vol target", className="cell-red" if vol_pct > 100 else None),
            _lines(f"The trades' yearly volatility {dollars(_num(sub.get('vol_blended_ann_usd')))} against a vol target "
                   f"of {dollars(target)} a year", str(sub.get("vol_note") or "")))


def headline(data: dict, v: dict, risk: Optional[dict], sub: Optional[dict]) -> html.Div:
    """One card, one sentence: what the trades showing can lose on a bad day, the limit beside it."""
    config = data.get("config") or {}
    ready = risk is not None
    named = [t for t, _r in v["rows"] if not t.get("pseudo")]
    shown_named = [t for t, _r in v["shown_rows"] if not t.get("pseudo")]
    who = (f"these {len(shown_named)} of {_plural(len(named), 'trade')}" if v["filtered"] else "the book")
    var = _num((sub or {}).get("var_usd"))
    if not ready:
        figure: Any = missing_cell(COMPUTING)
    elif var is None:
        figure = missing_cell(plain_reason((sub or {}).get("var_reason")) or "no figure")
    else:
        figure = html.Span(dollars(var), className="risk-var-figure")
    left = (sub or {}).get("left_out") or []
    excl = (marker(f"excl. {len(left)}", _lines("Not in this figure:", *(f"{x.get('trade')}: "
                                                                          f"{plain_reason(x.get('reason'))}"
                                                                          for x in left)), "marker--small")
            if ready and left else None)
    alone, together = _num((sub or {}).get("sum_daily_risk_usd")), _num((sub or {}).get("daily_risk_usd"))
    saves = _num((sub or {}).get("diversification_usd"))
    hover = _lines(_var_definition(config),
                   f"The trades' daily risks added up: {dollars(alone)}" if alone is not None else "",
                   f"Held together: {dollars(together)} a typical day" if together is not None else "",
                   f"Diversification saves {dollars(saves)} a typical day" if saves is not None else "")
    limit, limit_hover = limit_words(sub, ready, config)
    return html.Div(className="book-card risk-headline-card", children=[html.Div(className="tk-headline", children=[
        html.Div([f"On a bad day (1 in 20) {who} can lose about ", figure, excl],
                 className="risk-var-line", title=plain_words(hover)),
        html.Div(limit, className="risk-var-limit", title=plain_words(limit_hover) or None),
    ])])


def empty_card(data: dict) -> html.Div:
    """Before the first Bloomberg price history: one card instead of a table of dashes."""
    n = sum(1 for t in (data.get("trade_book") or {}).get("trades") or [] if t.get("status") == "open")
    return html.Div(className="book-card risk-empty-card", children=[
        html.Div("No price history yet", className="book-empty-title"),
        html.Div("Press Pull Bloomberg now: the first pull fetches about two and a half years of daily closes, "
                 "later pulls only the new days.", className="book-empty-text"),
        html.Div(f"{_plural(n, 'open trade')} waiting for it.", className="book-empty-text tk-sub"),
    ])


# --------------------------------------------------------------------------- issues and CSV
def issue_items(data: dict, v: dict, risk: Optional[dict], stress: Optional[dict]) -> List[Any]:
    """The drawer's rows as (kind, where, reason)."""
    items: List[Any] = [(str(label), "", sentence) for label, sentence in data.get("errors") or []]
    no_history = not has_history(data)
    if no_history:
        items.append(("Price history", "", tf.NO_HISTORY_TEXT))
    for n in (data.get("trade_book") or {}).get("notes") or []:
        if not (no_history and tf.is_no_history_reason(n)):
            items.append(("Trades", "", str(n)))
    if not no_history:
        if risk is not None and not risk.get("available"):
            items.append(("Risk", "", f"no risk figures: {plain_reason(risk.get('reason')) or 'no history'}"))
        for t, r in v["shown_rows"]:
            if r is not None and not r.get("included", True):
                items.append(("Not in the risk figures", str(t.get("trade")), plain_reason(r.get("reason")) or "no history"))
            elif r is not None and r.get("partial"):
                items.append(("Without hedge", str(t.get("trade")),
                              "counted without its currency hedge: " + plain_reason(r.get("partial_reason"))))
        if risk is not None and risk.get("partial_note") and not any(r and r.get("partial") for _t, r in v["shown_rows"]):
            items.append(("Without hedge", "", plain_reason(risk["partial_note"])))
    for m in (risk or {}).get("missing") or []:
        items.append(("Positions", "", plain_reason(m)))
    liq = data.get("liquidity") or {}
    if liq and not liq.get("available") and not no_history:
        items.append(("Days to exit", "", plain_reason(liq.get("reason")) or "not computed"))
    # the positions the days-to-exit check leaves out, one row per reason (the FX legs are never
    # exchange contracts: one row for all of them)
    skipped: Dict[str, List[str]] = {}
    for x in liq.get("skipped") or []:
        skipped.setdefault(plain_reason(x.get("reason")) or "no reason given", []).append(
            str(x.get("instrument_id") or x.get("trade_id") or ""))
    for why, ids in skipped.items():
        ids = list(dict.fromkeys(i for i in ids if i))
        items.append(("Days to exit", _plural(len(ids), "position"), f"not in the days-to-exit check: {why}"))
    if liq.get("placeholder"):
        items.append(("Days to exit", "", "the amber and red levels are placeholders until Jason gives his"))
    for reason in (data.get("curve") or {}).get("reasons") or []:
        items.append(("Exposure", "", plain_reason(reason)))
    items += [x for x in rf.stress_issues(v, stress) if not (no_history and tf.is_no_history_reason(x[2]))]
    return items


def csv_frame(data: dict, v: dict, risk: Optional[dict], sort: Optional[dict]) -> pd.DataFrame:
    rows = []
    for t, r in sort_rows(data, v["shown_rows"], sort):
        r = r or {}
        lo_v, lo_why, lo_lines = leftover_of(t)
        fx_v, fx_why, _a = fx_unhedged_of(t)
        p = liq_of(data, r) or {}
        h = t.get("hedge") or {}
        word, _amber = hedge_word(t, r or None)
        rows.append({
            "Trade": t.get("trade"), "Type": tf.trade_type_label(t), "Commodity": tf.family_label(t),
            "What it is": t.get("what_words") or t.get("what_it_is"), "In the risk figures": r.get("included"),
            "Why not": r.get("reason"), "Daily risk USD": r.get("daily_risk_usd"),
            "Bad-day loss alone USD": r.get("standalone_var"), "Share of book": r.get("share_of_book"),
            "Part of the book's bad-day loss USD": r.get("contribution_var"), "Hedged": word,
            "Hedge %": r.get("hedge_pct"), "Bigger leg": r.get("hedge_leg"), "Hedge moves": r.get("hedge_moves"),
            "Lot ratio held": r.get("lot_ratio"), "Best-fit ratio": r.get("best_fit_ratio"),
            "Legs' correlation": r.get("leg_correlation"), "Best-fit R2": r.get("best_fit_r2"),
            "Ratio legs": " / ".join(r.get("ratio_legs") or []), "Z-score": r.get("z"),
            "Percentile": r.get("percentile"), "Z at entry": r.get("z_entry"), "Move in sigma": r.get("move_sigma"),
            "Leftover USD per 1 %": lo_v, "Leftover legs": "; ".join(lo_lines), "Leftover note": lo_why,
            "FX unhedged USD": fx_v, "FX note": fx_why, "China exposure USD": h.get("exposure_usd"),
            "Hedge USD": h.get("hedge_usd"), "Coverage": h.get("coverage"),
            "Days to exit": p.get("days_to_exit"), "Weakest leg": p.get("weakest"), "% of OI": p.get("pct_of_oi"),
            "Liquidity level": p.get("level"),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- render
def render_parts(as_of: Optional[str], db_path, state: Optional[dict] = None, sort: Optional[dict] = None,
                 opened: Sequence[str] = (), wait_risk: bool = False) -> dict:
    """{body, shown, headline, table, card_style, foot, names, risk_ready} for `as_of`."""
    out = {"body": None, "shown": False, "headline": None, "table": None, "card_style": {}, "foot": None,
           "names": [], "risk_ready": True}
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
        stress = stress_result(conn, as_of)
        if not has_history(data):
            v = view(data, None, state)
            out.update(shown=True, headline=empty_card(data), card_style=HIDDEN)
            out["foot"] = html.Div(issues_drawer(issue_items(data, v, None, stress), id=ISSUES_ID), className="tk-foot")
            return out
        risk = _risk(conn, as_of, wait_risk)
        v = view(data, risk, state)
        sub = (subset(conn, as_of, [str(r.get("trade")) for _t, r in v["shown_rows"] if r])
               if risk is not None else None)
        tbl, names = table(conn, data, v, risk, sort, opened, sub)
        out.update(shown=True, headline=headline(data, v, risk, sub), table=tbl, names=names,
                   risk_ready=risk is not None)
        real = risk_limits.margin_limits_real(data.get("margin"), data.get("checks"))
        out["foot"] = html.Div([risk_limits.margin_limits_section(data.get("margin"), data.get("checks")) if real else None,
                                issues_drawer(issue_items(data, v, risk, stress), id=ISSUES_ID)], className="tk-foot")
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("Risk could not be built for %s", as_of)
        out["body"] = html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Risk could not be computed for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
        out["shown"] = False
    finally:
        conn.close()
    from ui.tabs.book import _tidy_parts
    return _tidy_parts(out)


def render_folds(as_of: Optional[str], db_path, state: Optional[dict], folds: Sequence[str],
                 commodity: Optional[str], unit: Optional[str]) -> Any:
    """The worst stresses, then the three folds for the trades showing; each fold's body built only
    when it is open."""
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
        risk = _risk(conn, as_of, False) if has_history(data) else None
        v = view(data, risk, state)
        open_set = set(folds or [])
        n_options = (len({str(r.get("contract_id")) for r in (v["curve"].get("rows") or [])
                          if str(r.get("product") or "") == rf.OPTION})
                     + rf.fx_options_held(conn, as_of))
        kids = []
        for key, build in (("stress", lambda o: rf.stress_card(v, stress_result(conn, as_of), open_set)),
                           (rf.FOLD_NET, lambda o: rf.net_fold(conn, v, o, commodity, unit or rf.DEFAULT_UNIT)),
                           (rf.FOLD_CCY, lambda o: rf.currency_fold(v, o, conn)),
                           (rf.FOLD_GREEKS, lambda o: rf.greeks_fold(conn, v, o, n_options))):
            try:
                built = build(key in open_set)
                if built is not None:
                    kids.append(built)
            except Exception as exc:  # noqa: BLE001 -- one block's reason, never the tab
                log.exception("Risk: the %s block failed for %s", key, as_of)
                kids.append(html.Div(missing_cell(f"the {key} block could not be built ({type(exc).__name__}: {exc})"),
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
    folds = render_folds(as_of, db_path, state, [rf.FOLD_NET, rf.FOLD_CCY, rf.FOLD_GREEKS], None, rf.DEFAULT_UNIT)
    table_card = (html.Div(p["table"], className="book-card book-main tk-card") if p["table"] is not None else None)
    return html.Div([p["headline"], table_card, folds, p["foot"]])


def options_of(as_of: str, db_path) -> Dict[str, List[dict]]:
    """The strip's choices (the trades present, as on the Book)."""
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
    """The shell: the message slot, then (hidden with no book) the headline, the table card, the
    stresses and folds, and the footer; the session stores and the risk poll."""
    return html.Div(className="risk-tab", children=[
        html.Div(about("Risk", QUESTION, level="h2", className="tab-title"), className="tab-header"),
        html.Div(id=BODY_ID, children=[message_box("Loading the risk figures...")]),
        html.Div(id=CONTENT_ID, style=HIDDEN, children=[
            html.Div(id=HEADLINE_ID),
            html.Div(id=CARD_ID, className="book-card book-main tk-card", children=[
                html.Div(className="tk-strip", children=[
                    about("Risk by trade", "One row per open trade, largest daily risk first. The first row is the "
                                           "trades showing together: their daily risk recomputed, never added. Click "
                                           "a row for its ratio, leftover, FX, days to exit, z-score and stresses.",
                          level="span", className="tk-title"),
                    tf.bar_slot(TAB),
                    html.Button("Expand all", id=EXPAND_ALL_ID, n_clicks=0, className="btn btn--ghost"),
                    html.Button("Collapse all", id=COLLAPSE_ALL_ID, n_clicks=0, className="btn btn--ghost"),
                    html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                                title="The rows showing at full figures, every figure of the row and its panel"),
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
        Output(TABLE_SLOT_ID, "children"), Output(CARD_ID, "style"), Output(FOOT_ID, "children"),
        Output(SHOWN_STORE_ID, "data"), Output(RISK_POLL_ID, "disabled"),
        Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(tf.STORE_ID, "data"),
        Input(SORT_STORE_ID, "data"), Input(OPEN_STORE_ID, "data"), Input(RISK_READY_ID, "data"),
    )
    def _render(as_of, _rev, state, sort, opened, _ready):
        p = render_parts(as_of, get_db_path(), state, sort, opened or [])
        if not p["shown"]:
            return p["body"], HIDDEN, None, None, {}, None, [], True
        return (None, {}, compact(p["headline"]), compact(p["table"]), p["card_style"], compact(p["foot"]),
                p["names"], p["risk_ready"])

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
            risk = _risk(conn, as_of, False) if has_history(data) else None
            frame = csv_frame(data, view(data, risk, state), risk, sort)
        finally:
            conn.close()
        return dcc.send_data_frame(frame.to_csv, f"risk_{as_of}.csv", index=False)
