"""Risk tab: the nm-dashboard's risk metrics on this book, for the whole book and per
underlyer (user request 2026-09-22), rendered from `engine.risk.book_risk` and nothing
else. Every figure on the tab is that dict's; nothing here recomputes a metric, a delta
or P&L (CLAUDE.md "Tabs as views"). The definitions in the hovers and the Definitions
block are the engine's (`engine/risk/metrics.py` docstring), with the parameters read
from the result's `config` (`config/risk.yaml`) so the text never drifts from the numbers.

Layout, top to bottom (`body`):
  1. `caption_block`: as-of, the history folder used with its last close and the lag-2
     date (or, with no history, its reason and every figure below n/a), the parameters
     in one line, the config and history notes, and the engine's `missing` list.
  2. `book_cards`: Net USD, Gross USD, DV01, Blended vol (% of target, flagged when
     `over_vol_target`), 1y 95 % VaR (1-day), Worst day ex shocks (its date, % of cap,
     flagged when `over_cap`), Worst day raw (its date). The definition is the card's
     hover; a NaN figure reads "n/a" with its reason as the hover and as the note.
  3. `underlyer_section`: the key table, one row per underlyer in the engine's order
     (gross USD desc, rates last) and the Book pinned under it (`ranking.with_footer`).
     Ranked (`ui.tabs.ranking`): numbers stored as numbers; a missing figure is the
     string "n/a" (ranks last) with its reason as the cell's tooltip; a figure that does
     not apply to the row (Net USD on a rates row, DV01 on a currency) is None, blank.
  4. `scenario_section`: one row per scenario of config/stress.yaml (Total, FX total,
     Equity move, Equity P&L) and a collapsible currency x scenario matrix from `fx_pnl`.
  5. `definitions_block`: the formulas and the parameters used.

The tab has no date picker: it follows the header's as-of store
(`ui.tabs.header.AS_OF_STORE_ID`) and re-renders in place on the data revision
(`ui/revision.py`) and on its own safety interval (the history is parquet outside the
database, so a fresh nm-dashboard pull moves no database revision). `layout(default_date)`
and `register_callbacks(app, get_db_path)` are the shell's interface, the Ladder's shape.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from dash import Input, Output, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Symbol

from engine.risk import book_risk
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import format_cell
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "risk-body"
REFRESH_ID = "risk-refresh"
CARDS_ID = "risk-cards"
TABLE_ID = "risk-underlyer-table"
SCENARIO_TABLE_ID = "risk-scenario-table"
MATRIX_TABLE_ID = "risk-scenario-matrix"
DEFINITIONS_ID = "risk-definitions"

NA = "n/a"
KIND_LABELS = {"FX": "FX", "METAL": "Metal", "EQUITY_INDEX": "Equity index", "RATES": "Rates", "BOOK": "Book"}
METRICS = ("vol_blended_ann_usd", "vol_trailing_ann_usd", "vol_crisis_ann_usd", "var95_1d_usd",
           "worst_1d_ex_shocks_usd", "worst_1d_raw_usd")
_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_FLAG_STYLE = {"color": "var(--neg)", "fontWeight": "700"}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    """A finite float, or None for None / NaN / a non-number."""
    v = rk.value(value)
    return v if isinstance(v, float) and math.isfinite(v) else None


def _usd(value: Any) -> str:
    v = _num(value)
    return NA if v is None else format_cell(v)


def _pct(value: Any, decimals: int = 1) -> str:
    v = _num(value)
    return NA if v is None else f"{v:,.{decimals}f}%"


def _why(entry: Dict[str, Any], metric: str) -> str:
    """Why `metric` is NaN on a row or the book: the metric's own reason, else the row's."""
    reasons = entry.get("reasons") or {}
    return reasons.get(metric) or reasons.get("all") or entry.get("reason") or ""


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%A} {d.day} {d:%B %Y} ({iso})"


def _pct_format(decimals: int = 1, nully: str = "") -> dict:
    """An unsigned per cent at fixed decimals: 27.8% (a share of a target is never signed)."""
    return Format(precision=decimals, scheme=Scheme.fixed, nully=nully).symbol(Symbol.yes).symbol_suffix("%").to_plotly_json()


def _fraction_format(decimals: int = 1, nully: str = "") -> dict:
    """A move stored as a fraction (-0.10) printed as a per cent (-10.0%)."""
    return Format(precision=decimals, scheme=Scheme.percentage, nully=nully).to_plotly_json()


def _na_styles(columns) -> List[dict]:
    return [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE} for c in columns]


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


# --------------------------------------------------------------------------- definitions
def definitions(config: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, str]:
    """The metrics' definitions (engine/risk/metrics.py) worded with the parameters in
    `config`, keyed by the book / row key they describe; the hovers and the Definitions
    block both read them."""
    b = config.get("blended") or {}
    target = config.get("vol_target_usd")
    cap = config.get("stress_cap_usd")
    pct = config.get("stress_pct")
    conf = float(config.get("var_confidence") or 0.95) * 100
    window = config.get("var_window_bd")
    shocks = ", ".join(f"{d.get('name', '')} {d.get('date', '')}".strip() for d in (config.get("shock_dates") or [])) or "none"
    lag2 = history.get("lag2_date") or "the history's last date less 2 business days"
    return {
        "daily_pnl": ("Daily $ P&L of the book held constant across the history: each currency, metal or equity-index "
                      "row's USD delta x its daily move (the log change of the USD-per-unit close plus carry, the "
                      "lagged yield differential per calendar day); a rates row DV01 x the change of the currency's "
                      "10Y par swap rate in bp. The book is the rows' series summed date by date, so correlation is "
                      "embedded; the per-underlyer figures are standalone."),
        "net_usd": ("Net USD delta: the sum of the currency, metal and equity-index rows' USD delta, + = long the "
                    "underlyer (the nm-dashboard's FX-legs net). The header's FX net USD (+ = long USD, metals out) "
                    "is the note underneath."),
        "gross_usd": "Gross USD delta: the sum of |USD delta| over the currency, metal and equity-index rows.",
        "dv01_usd": ("DV01: USD per +1bp parallel shift, the day's official DV01_USD marks summed (positive for a "
                     "payer). Mark-to-market only, no coupon accrual; the 10Y par swap rate stands in for the swaps' "
                     "own maturities in the history."),
        "vol_blended_ann_usd": (f"Blended annual vol = {_weight(b.get('w_trail'))} x trailing {b.get('trail_window_bd')}-day vol "
                                f"+ {_weight(b.get('w_stress'))} x crisis vol ({b.get('stress_start')} to {b.get('stress_end')}), "
                                f"each the daily $ P&L's standard deviation x sqrt(252), evaluated at the lag-2 date ({lag2}); "
                                f"before {b.get('cutover')} the crisis leg is the trailing vol. Fewer than "
                                f"{b.get('trail_window_bd')} observations to the lag-2 date = n/a. Shown against the vol "
                                f"target of {_usd(target)}."),
        "vol_trailing_ann_usd": f"Trailing vol: the standard deviation of the last {b.get('trail_window_bd')} daily $ P&Ls to the lag-2 date x sqrt(252).",
        "vol_crisis_ann_usd": f"Crisis vol: the standard deviation of the daily $ P&L over {b.get('stress_start')} to {b.get('stress_end')} x sqrt(252).",
        "var95_1d_usd": (f"1y {conf:g}% VaR (1-day) = minus the {100 - conf:g}th percentile of the last {window} daily "
                         f"$ P&Ls (the full series, not lag-2 limited): a typical bad day, positive = loss. Fewer than "
                         f"{window} observations = n/a."),
        "worst_1d_ex_shocks_usd": (f"Worst day ex shocks = the worst daily $ P&L from {config.get('worst_day_start')} to the "
                                   f"lag-2 date with the shock dates ({shocks}) set to 0: the stress-cap basis. "
                                   f"Cap = {_pct(pct, 0)} of the vol target = {_usd(cap)}."),
        "worst_1d_raw_usd": "Worst day raw = the worst daily $ P&L over all history, nothing excluded.",
        "worst_day_ex_vs_target_pct": f"Worst ex vs target: the worst day ex shocks as a share of the vol target ({_usd(target)}); for the Book, the card measures it against the cap.",
        "scenarios": ("Scenario stress: delta x move, the scenarios of config/stress.yaml on the book's USD delta by "
                      "currency (metals included) with the equity index (ES futures + SPX options) as the futures "
                      "line, the same scenarios as the Ladder's. No correlation, no vol. A scenario without an EQUITY "
                      "move leaves its Equity cells blank."),
    }


def _weight(w: Any) -> str:
    v = _num(w)
    if v is None:
        return NA
    for num, den in ((1, 2), (1, 3), (2, 3), (1, 4), (3, 4)):
        if abs(v - num / den) < 1e-6:
            return f"{num}/{den}"
    return f"{v:.2f}"


# --------------------------------------------------------------------------- 1. caption
def caption_lines(result: Dict[str, Any]) -> List[str]:
    """The caption's sentences, in order: as-of, the history used (or why none), the
    parameters, the config note, the history note."""
    h = result.get("history") or {}
    c = result.get("config") or {}
    b = c.get("blended") or {}
    lines = [f"As of {_date_words(result.get('as_of'))}."]
    if h.get("available"):
        s = f"History: {h.get('path')}, last close {h.get('last_date')}"
        if h.get("used_to"):
            s += f"; used to {h['used_to']}, lag-2 date {h.get('lag2_date')}"
        lines.append(s + ".")
    else:
        lines.append(f"Market history unavailable: {h.get('reason') or 'no reason given'}. Every risk figure below "
                     "is n/a; the positions and the scenarios stand.")
    lines.append(f"Parameters: vol target {_usd(c.get('vol_target_usd'))}; stress cap {_usd(c.get('stress_cap_usd'))} "
                 f"({_pct(c.get('stress_pct'), 0)} of the target); blended vol {_weight(b.get('w_trail'))} trailing "
                 f"({b.get('trail_window_bd')} bd) + {_weight(b.get('w_stress'))} crisis ({b.get('stress_start')} to "
                 f"{b.get('stress_end')}); VaR window {c.get('var_window_bd')} bd at "
                 f"{float(c.get('var_confidence') or 0) * 100:g}%.")
    if c.get("note"):
        lines.append(f"Config: {c['note']}.")
    if h.get("note"):
        lines.append(f"History: {h['note']}.")
    return lines


def caption_block(result: Dict[str, Any]) -> html.Div:
    children: List[Any] = [html.Div(className="meta-line", children=[html.Span(line) for line in caption_lines(result)])]
    missing = [m for m in (result.get("missing") or []) if m]
    if missing:
        children.append(html.Div(className="section-kicker", children=[
            html.Span("Not included: "),
            html.Ul([html.Li(m) for m in missing], style={"margin": "2px 0 0 16px", "padding": 0})]))
    return html.Div(children, className="risk-caption")


# --------------------------------------------------------------------------- 2. cards
def _summarise(entries: List[str], fallback: str) -> Tuple[str, str]:
    """(the note, the hover) for a list of reasons: one reason reads as it is; several
    read as the first with a count, the whole list on hover; none reads `fallback`."""
    entries = [e for e in entries if e]
    if not entries:
        return fallback, fallback
    if len(entries) == 1:
        return entries[0], entries[0]
    return f"{entries[0]} (+{len(entries) - 1} more on hover)", "; ".join(entries)


def _card(label: str, value: Any, *, definition: str, reason: str = "", short: str = "", note: str = "",
          flag: str = "", colour: bool = True) -> html.Div:
    """One card: the definition is the card's hover. A missing value reads n/a, muted,
    with its reason as the value's hover and as the note (`short` when the full reason
    is a list); a flagged card carries the flag word in the app's negative colour, so
    the flag reads without the colour too."""
    v = _num(value)
    if v is None:
        why = reason or "not available"
        children = [html.Span(label, className="card-label"),
                    html.Span(NA, className="card-value card-value--muted", title=why),
                    html.Span(short or why, className="card-note", title=why)]
        return html.Div(children, className="card", title=definition)
    style = dict(_FLAG_STYLE) if flag else ({"color": "var(--neg)"} if colour and v < 0 else {})
    children = [html.Span(label, className="card-label"),
                html.Span(format_cell(v), className="card-value", style=style)]
    if note:
        children.append(html.Span(note, className="card-note"))
    if flag:
        children.append(html.Span(flag, className="tag", style={"background": "#fdecea", **_FLAG_STYLE}))
    return html.Div(children, className="card", title=definition)


def book_cards(result: Dict[str, Any]) -> html.Div:
    book = result.get("book") or {}
    config = result.get("config") or {}
    defs = definitions(config, result.get("history") or {})
    missing = result.get("missing") or []
    conf = float(config.get("var_confidence") or 0.95) * 100
    fx_net = _num(book.get("fx_net_usd"))
    fx_gross = _num(book.get("fx_gross_usd"))
    fx_why = "; ".join(m for m in missing if m.startswith("FX positions")) or "no FX position priced"
    delta_short, delta_full = _summarise(missing, "no currency, metal or equity-index position with a USD delta")
    dv01_short, dv01_full = _summarise([m for m in missing if m.startswith("rates")], "no open swap with a DV01 mark")

    cards = [
        _card("Net USD delta", book.get("net_usd"), definition=defs["net_usd"], reason=delta_full, short=delta_short,
              note="+ = long the underlyer; header's FX net USD (+ = long USD): "
                   + (format_cell(fx_net) if fx_net is not None else f"n/a ({fx_why})")),
        _card("Gross USD delta", book.get("gross_usd"), definition=defs["gross_usd"], colour=False,
              reason=delta_full, short=delta_short,
              note="sum of |USD delta|; header's FX gross: "
                   + (format_cell(fx_gross) if fx_gross is not None else f"n/a ({fx_why})")),
        _card("DV01 (USD/bp)", book.get("dv01_usd"), definition=defs["dv01_usd"], reason=dv01_full, short=dv01_short,
              note="net, +1bp parallel; positive for a payer"),
        _card("Blended vol (annual)", book.get("vol_blended_ann_usd"), definition=defs["vol_blended_ann_usd"],
              reason=_why(book, "vol_blended_ann_usd"), colour=False,
              note=f"{_pct(book.get('vol_vs_target_pct'))} of the {_usd(config.get('vol_target_usd'))} target",
              flag="over vol target" if book.get("over_vol_target") else ""),
        _card(f"1y {conf:g}% VaR (1-day)", book.get("var95_1d_usd"), definition=defs["var95_1d_usd"],
              reason=_why(book, "var95_1d_usd"), colour=False,
              note=f"a typical bad day over the last {config.get('var_window_bd')} bd; positive = loss"),
        _card("Worst day ex shocks", book.get("worst_1d_ex_shocks_usd"), definition=defs["worst_1d_ex_shocks_usd"],
              reason=_why(book, "worst_1d_ex_shocks_usd"),
              note=f"{book.get('worst_1d_ex_shocks_date') or NA}; {_pct(book.get('worst_day_ex_vs_cap_pct'))} of the "
                   f"{_usd(config.get('stress_cap_usd'))} cap",
              flag="over cap" if book.get("over_cap") else ""),
        _card("Worst day raw", book.get("worst_1d_raw_usd"), definition=defs["worst_1d_raw_usd"],
              reason=_why(book, "worst_1d_raw_usd"),
              note=f"{book.get('worst_1d_raw_date') or NA}; nothing excluded"),
    ]
    return html.Div(id=CARDS_ID, className="cards", children=cards)


# --------------------------------------------------------------------------- 3. the key table
def _columns(config: Dict[str, Any]) -> List[dict]:
    conf = int(round(float(config.get("var_confidence") or 0.95) * 100))
    usd = rk.amount(nully="")
    return [rk.text("Underlyer", "underlyer"), rk.text("Kind", "kind"),
            rk.numeric("Net USD", "net_usd", usd), rk.numeric("Gross USD", "gross_usd", usd),
            rk.numeric("DV01 (USD/bp)", "dv01_usd", usd),
            rk.numeric("Blended vol", "vol_blended_ann_usd", usd), rk.numeric("Trailing vol", "vol_trailing_ann_usd", usd),
            rk.numeric("Crisis vol", "vol_crisis_ann_usd", usd), rk.numeric(f"VaR{conf} 1d", "var95_1d_usd", usd),
            rk.numeric("Worst ex shocks", "worst_1d_ex_shocks_usd", usd), rk.text("Date", "worst_1d_ex_shocks_date"),
            rk.numeric("Worst raw", "worst_1d_raw_usd", usd), rk.text("Date", "worst_1d_raw_date"),
            rk.numeric("Worst ex vs target %", "worst_day_ex_vs_target_pct", _pct_format()),
            rk.text("Note", "note")]


def underlyer_record(row: Dict[str, Any], *, book: bool = False, missing: Optional[List[str]] = None) -> Tuple[dict, dict]:
    """(record, tooltips) of one table row. A number is stored as a number; a missing one
    is the string "n/a" with its reason in the tooltip; one that does not apply to the
    row is None (blank). For the Book row (`book`), `missing` (the engine's list) is the
    reason behind a missing delta or DV01."""
    rec: Dict[str, Any] = {}
    tip: Dict[str, dict] = {}
    missing = missing or []

    def put(col: str, value: Any, reason: str, applicable: bool = True) -> None:
        if not applicable:
            rec[col] = None
            return
        v = rk.value(value)
        if v is None:
            rec[col] = NA
            if reason:
                tip[col] = {"value": reason, "type": "text"}
        else:
            rec[col] = v

    kind = "BOOK" if book else (row.get("kind") or "")
    is_rates = kind == "RATES"
    rec["underlyer"] = "Book" if book else row.get("underlyer", "")
    rec["kind"] = KIND_LABELS.get(kind, kind)
    if book:
        delta_why = "; ".join(missing) or "no currency, metal or equity-index position with a USD delta"
        dv01_why = "; ".join(m for m in missing if m.startswith("rates")) or "no open swap with a DV01 mark"
    else:
        delta_why = dv01_why = row.get("reason", "")
    put("net_usd", row.get("net_usd"), delta_why, applicable=not is_rates)
    put("gross_usd", row.get("gross_usd"), delta_why, applicable=not is_rates)
    put("dv01_usd", row.get("dv01_usd"), dv01_why, applicable=is_rates or book)
    for metric in METRICS:
        put(metric, row.get(metric), _why(row, metric))
    put("worst_1d_ex_shocks_date", row.get("worst_1d_ex_shocks_date"), _why(row, "worst_1d_ex_shocks_usd"))
    put("worst_1d_raw_date", row.get("worst_1d_raw_date"), _why(row, "worst_1d_raw_usd"))
    if book:
        rec["worst_day_ex_vs_target_pct"] = None
        tip["worst_day_ex_vs_target_pct"] = {"value": "the Book is measured against the stress cap: "
                                                       f"{_pct(row.get('worst_day_ex_vs_cap_pct'))} of the cap (the card above)",
                                              "type": "text"}
        n = len(row.get("rows_in_series") or [])
        parts = [f"{n} row(s)' daily P&L summed date by date, correlation embedded: "
                 + (", ".join(row.get("rows_in_series") or []) or "none")]
    else:
        put("worst_day_ex_vs_target_pct", row.get("worst_day_ex_vs_target_pct"), _why(row, "worst_1d_ex_shocks_usd"))
        # `carry` is the engine's "a carry series exists" flag; it is only news on a row that has figures
        parts = ["carry included"] if row.get("carry") and row.get("days") else []
    if row.get("reason"):
        parts.insert(0, row["reason"])
    if row.get("note"):
        parts.append(row["note"])
    rec["note"] = "; ".join(parts)
    return rec, tip


def underlyer_section(result: Dict[str, Any]) -> html.Div:
    config = result.get("config") or {}
    defs = definitions(config, result.get("history") or {})
    rows = result.get("underlyers") or []
    body_recs = [underlyer_record(r) for r in rows]
    book_rec, book_tip = underlyer_record(result.get("book") or {}, book=True, missing=result.get("missing"))
    columns = _columns(config)
    left = ("underlyer", "kind", "worst_1d_ex_shocks_date", "worst_1d_raw_date", "note")
    signed = ["net_usd", "dv01_usd"]
    losses = ["worst_1d_ex_shocks_usd", "worst_1d_raw_usd"]
    numeric_cols = [c["id"] for c in columns if c["type"] == "numeric"]
    table = dash_table.DataTable(
        id=TABLE_ID,
        columns=columns,
        data=[r for r, _ in body_recs],
        tooltip_data=[t for _, t in body_recs],
        tooltip_header={k: defs[k] for k in defs if any(c["id"] == k for c in columns)},
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in left]
                               + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "220px", "maxWidth": "420px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(signed, bold=True) + rk.sign_styles(losses, pos="inherit")
                               + _na_styles(numeric_cols + ["worst_1d_ex_shocks_date", "worst_1d_raw_date"]),
    )
    footer_style = [{"if": {"filter_query": "{kind} = 'Book'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    caption = ("One row per underlyer, largest gross USD first, rates last; the Book, pinned underneath, is the rows' "
               "daily P&L summed date by date (correlation embedded), the per-underlyer figures standalone. A figure "
               "the engine could not compute reads n/a with its reason on hover; a blank cell does not apply to that "
               "row. Column headers carry the definitions on hover.")
    return html.Div(className="section", children=[
        html.H4("Risk by underlyer"),
        html.P(caption, className="section-kicker"),
        rk.with_footer(table, [book_rec], footer_style=footer_style, footer_tooltips=[book_tip],
                       skip_widths=("note",))])


# --------------------------------------------------------------------------- 4. scenarios
def scenario_records(scenarios: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips) of the scenario table: Total, FX total, the equity move and the
    equity P&L. The equity cells are None (blank) when the scenario has no EQUITY move,
    "n/a" with the reason when the engine could not value it, else numbers."""
    records, tips = [], []
    for name, s in (scenarios or {}).items():
        rec: Dict[str, Any] = {"scenario": name}
        tip: Dict[str, dict] = {}
        reason = s.get("reason") or ""
        for col, key in (("total", "total"), ("fx_total", "fx_total")):
            v = rk.value(s.get(key))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = {"value": reason or "not computed", "type": "text"}
        if s.get("equity_pct") is None:
            rec["equity_pct"] = None
            rec["equity_pnl"] = None
        else:
            rec["equity_pct"] = rk.value(s.get("equity_pct"))
            v = rk.value(s.get("futures_pnl"))
            rec["equity_pnl"] = NA if v is None else v
            if v is None:
                tip["equity_pnl"] = {"value": reason or "not computed", "type": "text"}
        records.append(rec)
        tips.append(tip)
    return records, tips


def matrix_records(scenarios: Dict[str, dict], order: List[str]) -> Tuple[List[dict], dict]:
    """(one record per currency with a cell per scenario, the FX-total footer record).
    Currencies follow `order` (the underlyers' order) then the rest alphabetically; a
    currency a scenario does not move is None (blank), never zero."""
    names = list(scenarios or {})
    seen: Dict[str, None] = {}
    for s in (scenarios or {}).values():
        for ccy in (s.get("fx_pnl") or {}):
            seen[ccy] = None
    ordered = [c for c in order if c in seen] + sorted(c for c in seen if c not in order)
    records = []
    for ccy in ordered:
        rec: Dict[str, Any] = {"ccy": ccy}
        for name in names:
            v = rk.value((scenarios[name].get("fx_pnl") or {}).get(ccy))
            rec[name] = v
        records.append(rec)
    footer: Dict[str, Any] = {"ccy": "FX total"}
    for name in names:
        v = rk.value(scenarios[name].get("fx_total"))
        footer[name] = NA if v is None else v
    return records, footer


def scenario_section(result: Dict[str, Any]) -> html.Div:
    scenarios = result.get("scenarios") or {}
    defs = definitions(result.get("config") or {}, result.get("history") or {})
    if not scenarios:
        return html.Div(className="section", children=[
            html.H4("Scenario stress"),
            html.P("No scenarios on file (config/stress.yaml is missing or empty).", className="section-kicker")])
    records, tips = scenario_records(scenarios)
    usd = rk.amount(nully="")
    table = dash_table.DataTable(
        id=SCENARIO_TABLE_ID,
        columns=[rk.text("Scenario", "scenario"), rk.numeric("Total", "total", usd), rk.numeric("FX total", "fx_total", usd),
                 rk.numeric("Equity move", "equity_pct", _fraction_format()), rk.numeric("Equity P&L", "equity_pnl", usd)],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(SCENARIO_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": "scenario"}, "textAlign": "left"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["total", "fx_total", "equity_pnl"]) + _na_styles(["total", "fx_total", "equity_pnl"]),
    )
    order = [r.get("underlyer") for r in (result.get("underlyers") or [])]
    m_records, m_footer = matrix_records(scenarios, order)
    names = list(scenarios)
    matrix = dash_table.DataTable(
        id=MATRIX_TABLE_ID,
        columns=[rk.text("Currency", "ccy")] + [rk.numeric(name, name, usd) for name in names],
        data=m_records,
        **rk.sortable(MATRIX_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell={**_MONO, "minWidth": "110px", "width": "110px", "maxWidth": "160px"},
        style_cell_conditional=[{"if": {"column_id": "ccy"}, "textAlign": "left", "fontWeight": "600", "minWidth": "90px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto", "lineHeight": "14px"},
        style_data_conditional=rk.sign_styles(names) + _na_styles(names),
    )
    total_style = [{"if": {"filter_query": "{ccy} = 'FX total'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    return html.Div(className="section", children=[
        html.H4("Scenario stress"),
        html.P(defs["scenarios"], className="section-kicker"),
        table,
        html.Details(className="details details--compact", open=False, children=[
            html.Summary("Currency x scenario matrix (USD)"),
            html.P("Each cell is the currency's USD delta x the scenario's move; a blank cell is a currency the "
                   "scenario does not move.", className="section-kicker"),
            rk.with_footer(matrix, [m_footer], widths=False, footer_style=total_style)])])


# --------------------------------------------------------------------------- 5. definitions
def definitions_block(result: Dict[str, Any]) -> html.Details:
    config = result.get("config") or {}
    history = result.get("history") or {}
    b = config.get("blended") or {}
    defs = definitions(config, history)
    items = [("Daily $ P&L", defs["daily_pnl"]), ("Net / Gross USD delta", defs["net_usd"] + " " + defs["gross_usd"]),
             ("DV01", defs["dv01_usd"]), ("Blended vol", defs["vol_blended_ann_usd"]),
             ("VaR", defs["var95_1d_usd"]), ("Worst day ex shocks", defs["worst_1d_ex_shocks_usd"]),
             ("Worst day raw", defs["worst_1d_raw_usd"]), ("Scenarios", defs["scenarios"])]
    shocks = "; ".join(f"{d.get('date', '')} {d.get('name', '')}".strip() for d in (config.get("shock_dates") or [])) or "none"
    params = (f"vol target {_usd(config.get('vol_target_usd'))}; stress cap {_usd(config.get('stress_cap_usd'))} "
              f"({_pct(config.get('stress_pct'), 0)}); trailing window {b.get('trail_window_bd')} bd, weights "
              f"{_weight(b.get('w_trail'))} / {_weight(b.get('w_stress'))}, crisis window {b.get('stress_start')} to "
              f"{b.get('stress_end')}, cutover {b.get('cutover')}; VaR window {config.get('var_window_bd')} bd at "
              f"{float(config.get('var_confidence') or 0) * 100:g}%; worst day from {config.get('worst_day_start')}; "
              f"shock dates: {shocks}. Read from {config.get('file')}"
              + (" (loaded)" if config.get("loaded") else f" (not loaded: {config.get('note') or 'defaults in use'})") + ".")
    files = []
    for key, f in (history.get("files") or {}).items():
        state = (f"{f.get('first_date')} to {f.get('last_date')}, {f.get('rows')} rows" if f.get("loaded")
                 else f"not loaded ({f.get('reason') or 'no reason given'})")
        files.append(f"{key}: {f.get('file')} ({state})")
    hist = (f"{history.get('path') or 'no folder'}: " + ("; ".join(files) if files else (history.get("reason") or "no files")))
    items += [("Parameters", params), ("History", hist)]
    return html.Details(id=DEFINITIONS_ID, className="section section--secondary details", open=False, children=[
        html.Summary("Definitions"),
        html.Dl(className="legend", children=[html.Div([html.Dt(label), html.Dd(text)]) for label, text in items])])


# --------------------------------------------------------------------------- body and shell
def body(result: Dict[str, Any]) -> html.Div:
    """The whole tab body from one `book_risk` result."""
    return html.Div(className="risk-body", children=[
        caption_block(result), book_cards(result), underlyer_section(result), scenario_section(result),
        definitions_block(result)])


def render(as_of: Optional[str], db_path) -> Any:
    """The body for `as_of` from the database at `db_path`: one `book_risk` call on a
    read-only connection, closed straight after. A problem is a message where the body
    would be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local, as the Ladder does: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        result = book_risk(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Risk could not be computed for {as_of} ({type(exc).__name__}: {exc}).", className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result)


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="risk-tab", children=[
        html.Div(className="ladder-title-row", children=[
            html.H3("Risk", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(f"Follows the header's as-of date{f' ({default_date})' if default_date else ''}",
                        className="section-title")])]),
        html.Div(id=BODY_ID, children=[message_box("Loading the risk metrics...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """One callback: the body re-renders on the header's as-of, on every data revision
    and on the safety interval (the market history lives outside the database)."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path())
