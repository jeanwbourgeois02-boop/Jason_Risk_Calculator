"""Risk tab: the nm-dashboard's risk metrics on this book, for the whole book and per
underlyer (user request 2026-09-22), with the commodity book's rows, stress, margin and
limits (commodity conversion, Phases 4 and 5), rendered from the engine and nothing else.
Every figure on the tab is `engine.risk.book_risk`'s dict, `engine.limits.margin_estimate`'s
or `engine.limits.limit_checks`'; nothing here recomputes a metric, a delta, a scenario,
a margin or P&L (CLAUDE.md "Tabs as views"). The definitions in the hovers and the
Definitions block are the engines' (`engine/risk/metrics.py`, `engine/risk/commodity.py`,
`engine/stress/commodity.py` and `engine/limits/` docstrings), with the parameters read
from the result's `config` (`config/risk.yaml`) so the text never drifts from the numbers.

Layout, top to bottom (`body`):
  1. `caption_block`: as-of, the FX history folder with its last close and the lag-2 date,
     the commodity settlement history with its dates (or, with either missing, its reason),
     the parameters in one line, the vol target's note (a placeholder until Jason's is
     set), the config and history notes, and the engine's `missing` list.
  2. `book_cards`: Net USD, Gross USD, Commodity net USD, Commodity gross USD, Blended vol
     (% of target, flagged when `over_vol_target`, with the book's `vol_note`), 1y 95 % VaR
     (1-day), Worst day ex shocks (its date, % of cap, flagged when `over_cap`), Worst day
     raw (its date). The definition is the card's hover; a NaN figure reads "n/a" with its
     reason as the hover and as the note.
  3. `underlyer_section`: the key table of the PARTS (the rows the Book sums): the currency
     and metal rows in the engine's order, then the COMMODITY rows grouped by sector, and
     the Book pinned under them (`ranking.with_footer`). Under it, apart, the VIEWS (SECTOR
     and SPREAD rows, which re-add parts) in a table headed "Views (not added to the
     Book)". Ranked (`ui.tabs.ranking`): numbers stored as numbers; a missing figure is the
     string "n/a" (ranks last) with its reason as the cell's tooltip; a figure that does
     not apply to the row is None, blank. A commodity row's contracts and a view's parts
     or legs are the underlyer cell's hover.
  4. `scenario_section`: one row per scenario of config/stress.yaml (Total, FX total) and a
     collapsible currency x scenario matrix from `fx_pnl`.
  5. `commodity_scenario_section`: one row per scenario of config/commodity_stress.yaml
     (`commodity_scenarios`, commodity-stress's result untouched): kind, dates of a replay,
     total USD and USD per sector; below it one collapsible block per scenario with its
     P&L by root, by contract, by spread and, for an fx scenario, by currency, and the
     positions it could not value.
  6. `margin_limits_section`: margin-limits' estimate by sector with the Book pinned, by
     commodity and the spread credits (labelled "estimate, not exchange SPAN"), and the
     limit checks with their levels coloured.
  7. `definitions_block`: the formulas and the parameters used.

The macro trader's rates (DV01, par swap rates) and equity-index underlyers left the app
with the commodity conversion (user, 2026-09-24, CLAUDE.md "Commodity conversion plan",
Phase 2): the tab never shows a `RETIRED_KINDS` row, its DV01, its scenario line or its
`missing` entries, even while `book_risk` still returns them.

The tab has no date picker: it follows the header's as-of store
(`ui.tabs.header.AS_OF_STORE_ID`) and re-renders in place on the data revision
(`ui/revision.py`) and on its own safety interval (the histories live outside the
database, so a fresh history pull moves no database revision). `layout(default_date)`
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
VIEWS_TABLE_ID = "risk-views-table"
SCENARIO_TABLE_ID = "risk-scenario-table"
MATRIX_TABLE_ID = "risk-scenario-matrix"
COMMODITY_SCENARIO_TABLE_ID = "risk-commodity-scenario-table"
COMMODITY_SCENARIO_DETAIL_ID = "risk-commodity-scenario-detail"
MARGIN_TABLE_ID = "risk-margin-table"
MARGIN_ROOT_TABLE_ID = "risk-margin-root-table"
MARGIN_SPREAD_TABLE_ID = "risk-margin-spread-table"
LIMITS_TABLE_ID = "risk-limits-table"
MARGIN_LIMITS_ID = "risk-margin-limits"
DEFINITIONS_ID = "risk-definitions"

NA = "n/a"
KIND_LABELS = {"FX": "FX", "METAL": "Metal", "COMMODITY": "Commodity", "SECTOR": "Sector (view)",
               "SPREAD": "Spread (view)", "BOOK": "Book"}
VIEW_KINDS = ("SECTOR", "SPREAD")
# underlyer kinds of the macro book, removed 2026-09-24: never rendered, nor their `missing` entries
RETIRED_KINDS = ("RATES", "EQUITY_INDEX")
RETIRED_MISSING_PREFIXES = ("rates:", "equity index:")
RETIRED_HISTORY_FILES = ("swap_rates",)      # the rates rows' par swap rate history
METRICS = ("vol_blended_ann_usd", "vol_trailing_ann_usd", "vol_crisis_ann_usd", "var95_1d_usd",
           "worst_1d_ex_shocks_usd", "worst_1d_raw_usd")
SCENARIO_KIND_LABELS = {"outright": "Outright", "curve": "Curve", "spread": "Spread", "fx": "FX", "replay": "Replay"}
MARGIN_BASIS = "estimate, not exchange SPAN"
# limit levels (engine.limits.checks) as shown, and their colours: the Expiries tab's palette
LEVEL_TEXT = {"BREACH": "BREACH", "WARN": "WARN", "OK": "OK", "NOT_SET": "not set in config/limits.yaml", "N/A": NA}
LEVEL_STYLES = {
    "BREACH": {"backgroundColor": "#c62828", "color": "#ffffff", "fontWeight": "700"},
    "WARN": {"backgroundColor": "#fff3e0", "color": "#b26a00", "fontWeight": "700"},
    "OK": {"backgroundColor": "#f1f5f1", "color": "#1a7f4b"},
    "NOT_SET": {"backgroundColor": "#eef0f3", "color": "#6b7280", "fontStyle": "italic"},
    "N/A": {"color": "var(--muted)", "fontStyle": "italic"},
}
_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_FLAG_STYLE = {"color": "var(--neg)", "fontWeight": "700"}
_VIEW_ROW_STYLE = {"backgroundColor": "var(--page)", "fontStyle": "italic"}


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


def _lots(value: Any) -> str:
    v = _num(value)
    return NA if v is None else f"{v:,.2f}".rstrip("0").rstrip(".")


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


def _na_styles(columns) -> List[dict]:
    return [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE} for c in columns]


def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def is_view(row: Dict[str, Any]) -> bool:
    """A SECTOR or SPREAD row: it re-adds rows already in the Book and is never summed."""
    return row.get("role") == "view" or row.get("kind") in VIEW_KINDS


def shown_underlyers(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The engine's underlyer rows, in its order, less the retired kinds."""
    return [r for r in (result.get("underlyers") or []) if r.get("kind") not in RETIRED_KINDS]


def part_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The rows the Book sums: the currency and metal rows in the engine's order, then the
    COMMODITY rows grouped by sector (the sectors in the order their largest commodity
    comes, each sector's commodities in the engine's order, gross USD first)."""
    parts = [r for r in shown_underlyers(result) if not is_view(r)]
    others = [r for r in parts if r.get("kind") != "COMMODITY"]
    commodities = [r for r in parts if r.get("kind") == "COMMODITY"]
    sectors = list(dict.fromkeys(r.get("sector") or "" for r in commodities))
    return others + [r for s in sectors for r in commodities if (r.get("sector") or "") == s]


def view_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The SECTOR and SPREAD views, in the engine's order (sectors, then spreads)."""
    return [r for r in shown_underlyers(result) if is_view(r)]


def shown_missing(result: Dict[str, Any]) -> List[str]:
    """The engine's `missing` list less the retired rows' entries: those with a retired
    prefix, and those naming a retired underlyer ("SPX: not in the scenarios (...)")."""
    retired = tuple(f"{r.get('underlyer')}:" for r in (result.get("underlyers") or [])
                    if r.get("kind") in RETIRED_KINDS and r.get("underlyer"))
    return [m for m in (result.get("missing") or [])
            if m and not m.startswith(RETIRED_MISSING_PREFIXES) and not (retired and m.startswith(retired))]


def delta_missing(result: Dict[str, Any]) -> List[str]:
    """The `missing` entries behind the currency and metal delta (Net / Gross USD): the FX
    positions' and FX options' reasons and the currency or metal rows' own, never the
    commodity rows' (their reason is `book.commodity_reason`)."""
    own = tuple(f"{r.get('underlyer')}:" for r in shown_underlyers(result)
                if r.get("kind") in ("FX", "METAL") and r.get("underlyer"))
    return [m for m in shown_missing(result) if m.startswith(("FX positions", "FX option")) or (own and m.startswith(own))]


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
        "daily_pnl": ("Daily $ P&L of the book held constant across the history: each currency or metal row's USD "
                      "delta x its daily move (the log change of the USD-per-unit close plus carry, the lagged yield "
                      "differential per calendar day); each commodity row's contracts at their delta lots x the daily "
                      "settlement change x the multiplier x USD per quote unit that day, from the research app's "
                      "settlement history (a risk input only, never a mark). The book is the parts' series summed "
                      "date by date, so correlation is embedded; the per-underlyer figures are standalone."),
        "net_usd": ("Net USD delta: the sum of the currency and metal rows' USD delta, + = long the underlyer (the "
                    "nm-dashboard's FX-legs net). The header's FX net USD (+ = long USD, metals out) is the note "
                    "underneath. The commodity rows are on their own card."),
        "gross_usd": "Gross USD delta: the sum of |USD delta| over the currency and metal rows.",
        "commodity_net_usd": ("Commodity net USD delta: the sum of the commodity rows' USD delta (each commodity's "
                              "net delta in USD from the Curve tab's positions, options at their delta), + = long. "
                              "Kept apart from the currency and metal net."),
        "commodity_gross_usd": "Commodity gross USD delta: the sum of |USD delta| over the commodity rows.",
        "vol_blended_ann_usd": (f"Blended annual vol = {_weight(b.get('w_trail'))} x trailing {b.get('trail_window_bd')}-day vol "
                                f"+ {_weight(b.get('w_stress'))} x crisis vol ({b.get('stress_start')} to {b.get('stress_end')}), "
                                f"each the daily $ P&L's standard deviation x sqrt(252), evaluated at the lag-2 date ({lag2}); "
                                f"before {b.get('cutover')} the crisis leg is the trailing vol, and so it is when the history "
                                f"holds nothing in the crisis window. Fewer than "
                                f"{b.get('trail_window_bd')} observations to the lag-2 date = n/a. Shown against the vol "
                                f"target of {_usd(target)}."),
        "vol_trailing_ann_usd": f"Trailing vol: the standard deviation of the last {b.get('trail_window_bd')} daily $ P&Ls to the lag-2 date x sqrt(252).",
        "vol_crisis_ann_usd": (f"Crisis vol: the standard deviation of the daily $ P&L over {b.get('stress_start')} to "
                               f"{b.get('stress_end')} x sqrt(252); the trailing vol where the history does not reach that window."),
        "var95_1d_usd": (f"1y {conf:g}% VaR (1-day) = minus the {100 - conf:g}th percentile of the last {window} daily "
                         f"$ P&Ls (the full series, not lag-2 limited): a typical bad day, positive = loss. Fewer than "
                         f"{window} observations = n/a."),
        "worst_1d_ex_shocks_usd": (f"Worst day ex shocks = the worst daily $ P&L from {config.get('worst_day_start')} to the "
                                   f"lag-2 date with the shock dates ({shocks}) set to 0: the stress-cap basis. "
                                   f"Cap = {_pct(pct, 0)} of the vol target = {_usd(cap)}."),
        "worst_1d_raw_usd": "Worst day raw = the worst daily $ P&L over all history, nothing excluded.",
        "worst_day_ex_vs_target_pct": f"Worst ex vs target: the worst day ex shocks as a share of the vol target ({_usd(target)}); for the Book, the card measures it against the cap.",
        "scenarios": ("Scenario stress: delta x move, the scenarios of config/stress.yaml on the book's USD delta by "
                      "currency (metals included), the same scenarios as the Ladder's. No correlation, no vol."),
        "parts_views": ("The Book's series is the sum of the parts: the currency, metal and commodity rows. A sector "
                        "row re-adds its commodities and a spread row its futures legs, which are already in the "
                        "commodity rows: they are views, shown apart and never added to the Book, so nothing is "
                        "counted twice. A spread's net / gross USD is its leftover outright (a clean spread has none)."),
        "crisis_fallback": (f"Crisis-window fallback: when the lag-2 date is past the cutover ({b.get('cutover')}) but the "
                            f"history holds nothing in the crisis window ({b.get('stress_start')} to {b.get('stress_end')}), "
                            "as with the research settlement history, which starts later, the blended vol is the trailing "
                            "vol alone; the row or the card says 'crisis window not in history: trailing vol only'."),
        "shock_days": (f"Shock days, set to 0 in worst day ex shocks for every row alike (currencies, metals and "
                       f"commodities): {shocks}."),
        "commodity_scenarios": ("Commodity scenario stress: each scenario of the commodity stress file on the book's "
                                "commodity positions, first order on delta: a position's P&L is its USD delta x the "
                                "scenario's move. Options count at their delta; their gamma is not in it. Outright and "
                                "curve moves, spread legs moved against each other, a currency against the USD applied "
                                "to the P&L the non-USD positions hold, and replays of past windows from the research "
                                "history. n/a = nothing the scenario touches has a figure."),
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
def commodity_history_line(result: Dict[str, Any]) -> Optional[str]:
    """The commodity settlement history's sentence, or None when the engine gave no block."""
    ch = result.get("commodity_history")
    if not ch:
        return None
    if ch.get("available"):
        s = (f"Commodity history: {ch.get('path')}, settlements {ch.get('first_date') or NA} to "
             f"{ch.get('last_date') or NA} ({ch.get('roots', 0)} roots, {ch.get('contracts', 0)} contracts)")
        if ch.get("used_to"):
            s += f"; used to {ch['used_to']}, lag-2 date {ch.get('lag2_date')}"
        return s + "."
    return (f"Commodity history unavailable: {ch.get('reason') or 'no reason given'}. The commodity rows' risk "
            "figures are n/a; their positions, the margin and the scenarios that need no history stand.")


def caption_lines(result: Dict[str, Any]) -> List[str]:
    """The caption's sentences, in order: as-of, the FX history used (or why none), the
    commodity history (or why none), the parameters, the vol target's note, the config
    note, the history notes, the commodity positions' note."""
    h = result.get("history") or {}
    c = result.get("config") or {}
    ch = result.get("commodity_history") or {}
    b = c.get("blended") or {}
    lines = [f"As of {_date_words(result.get('as_of'))}."]
    if h.get("available"):
        s = f"History: {h.get('path')}, last close {h.get('last_date')}"
        if h.get("used_to"):
            s += f"; used to {h['used_to']}, lag-2 date {h.get('lag2_date')}"
        lines.append(s + ".")
    else:
        lines.append(f"Market history unavailable: {h.get('reason') or 'no reason given'}. Every currency and metal "
                     "risk figure below is n/a; the positions and the scenarios stand.")
    cm_line = commodity_history_line(result)
    if cm_line:
        lines.append(cm_line)
    lines.append(f"Parameters: vol target {_usd(c.get('vol_target_usd'))}; stress cap {_usd(c.get('stress_cap_usd'))} "
                 f"({_pct(c.get('stress_pct'), 0)} of the target); blended vol {_weight(b.get('w_trail'))} trailing "
                 f"({b.get('trail_window_bd')} bd) + {_weight(b.get('w_stress'))} crisis ({b.get('stress_start')} to "
                 f"{b.get('stress_end')}); VaR window {c.get('var_window_bd')} bd at "
                 f"{float(c.get('var_confidence') or 0) * 100:g}%.")
    if c.get("vol_target_note"):
        lines.append(f"Vol target {_usd(c.get('vol_target_usd'))}: {c['vol_target_note']}.")
    if c.get("note"):
        lines.append(f"Config: {c['note']}.")
    if h.get("note"):
        lines.append(f"History: {h['note']}.")
    if ch.get("note"):
        lines.append(f"Commodity history: {ch['note']}.")
    if ch.get("positions_note"):
        lines.append(f"Commodity positions: {ch['positions_note']}.")
    return lines


def caption_block(result: Dict[str, Any]) -> html.Div:
    children: List[Any] = [html.Div(className="meta-line", children=[html.Span(line) for line in caption_lines(result)])]
    missing = shown_missing(result)
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
          flag: str = "", colour: bool = True, note_title: str = "") -> html.Div:
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
        children.append(html.Span(note, className="card-note", **({"title": note_title} if note_title else {})))
    if flag:
        children.append(html.Span(flag, className="tag", style={"background": "#fdecea", **_FLAG_STYLE}))
    return html.Div(children, className="card", title=definition)


def book_cards(result: Dict[str, Any]) -> html.Div:
    book = result.get("book") or {}
    config = result.get("config") or {}
    defs = definitions(config, result.get("history") or {})
    missing = delta_missing(result)
    conf = float(config.get("var_confidence") or 0.95) * 100
    fx_net = _num(book.get("fx_net_usd"))
    fx_gross = _num(book.get("fx_gross_usd"))
    fx_why = "; ".join(m for m in missing if m.startswith("FX positions")) or "no FX position priced"
    delta_short, delta_full = _summarise(missing, "no currency or metal position with a USD delta")
    cm_why = book.get("commodity_reason") or ""
    cm_short, cm_full = _summarise([cm_why], "no commodity position with a USD delta")
    target_words = f"the {_usd(config.get('vol_target_usd'))} target" + (" (placeholder)" if config.get("vol_target_placeholder") else "")
    vol_note = book.get("vol_note") or ""

    cards = [
        _card("Net USD delta", book.get("net_usd"), definition=defs["net_usd"], reason=delta_full, short=delta_short,
              note="+ = long the underlyer; header's FX net USD (+ = long USD): "
                   + (format_cell(fx_net) if fx_net is not None else f"n/a ({fx_why})")),
        _card("Gross USD delta", book.get("gross_usd"), definition=defs["gross_usd"], colour=False,
              reason=delta_full, short=delta_short,
              note="sum of |USD delta|; header's FX gross: "
                   + (format_cell(fx_gross) if fx_gross is not None else f"n/a ({fx_why})")),
        _card("Commodity net USD", book.get("commodity_net_usd"), definition=defs["commodity_net_usd"],
              reason=cm_full, short=cm_short,
              note="+ = long" + (f"; {cm_why}" if cm_why else ""), note_title=cm_why),
        _card("Commodity gross USD", book.get("commodity_gross_usd"), definition=defs["commodity_gross_usd"],
              colour=False, reason=cm_full, short=cm_short,
              note="sum of |USD delta|" + (f"; {cm_why}" if cm_why else ""), note_title=cm_why),
        _card("Blended vol (annual)", book.get("vol_blended_ann_usd"), definition=defs["vol_blended_ann_usd"],
              reason=_why(book, "vol_blended_ann_usd"), colour=False,
              note=f"{_pct(book.get('vol_vs_target_pct'))} of {target_words}" + (f"; {vol_note}" if vol_note else ""),
              note_title=config.get("vol_target_note") or "",
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
    return [rk.text("Underlyer", "underlyer"), rk.text("Kind", "kind"), rk.text("Sector", "sector"),
            rk.numeric("Net USD", "net_usd", usd), rk.numeric("Gross USD", "gross_usd", usd),
            rk.numeric("Blended vol", "vol_blended_ann_usd", usd), rk.numeric("Trailing vol", "vol_trailing_ann_usd", usd),
            rk.numeric("Crisis vol", "vol_crisis_ann_usd", usd), rk.numeric(f"VaR{conf} 1d", "var95_1d_usd", usd),
            rk.numeric("Worst ex shocks", "worst_1d_ex_shocks_usd", usd), rk.text("Date", "worst_1d_ex_shocks_date"),
            rk.numeric("Worst raw", "worst_1d_raw_usd", usd), rk.text("Date", "worst_1d_raw_date"),
            rk.numeric("Worst ex vs target %", "worst_day_ex_vs_target_pct", _pct_format()),
            rk.text("Note", "note")]


def _contract_line(c: Dict[str, Any]) -> str:
    """One contract of a COMMODITY row, for the underlyer cell's hover."""
    head = f"{c.get('contract_id')} ({c.get('product') or 'n/a'}): {_lots(c.get('delta_lots'))} delta lots"
    if c.get("in_series"):
        body = f"in the series, {c.get('days', 0)} days"
        if c.get("history_contract") and c.get("history_contract") != c.get("contract_id"):
            body += f", history of {c['history_contract']}"
    else:
        body = f"not in the series: {c.get('reason') or 'no reason given'}"
    return f"{head}, {body}" + (f" ({c['note']})" if c.get("note") else "")


def _leg_line(leg: Dict[str, Any]) -> str:
    """One leg of a SPREAD row, for the underlyer cell's hover."""
    head = f"{leg.get('contract_id')} ({leg.get('product') or 'n/a'}): {_lots(leg.get('open_lots'))} open lots"
    return head + (f", in the series, {leg.get('days', 0)} days" if leg.get("in_series")
                   else f", not in the series: {leg.get('reason') or 'no reason given'}")


def underlyer_hover(row: Dict[str, Any]) -> str:
    """The underlyer cell's hover: a commodity's name, sector, delta lots and contracts; a
    sector's commodities; a spread's legs; and the row's reason."""
    kind = row.get("kind")
    parts: List[str] = []
    if kind == "COMMODITY":
        parts.append(f"{row.get('name') or row.get('underlyer')} ({row.get('underlyer')}), "
                     f"{row.get('sector') or 'no sector'}; net {_lots(row.get('net_delta_lots'))} delta lots")
        contracts = row.get("contracts") or []
        if contracts:
            parts.append(f"{len(contracts)} contract(s): " + "; ".join(_contract_line(c) for c in contracts))
    elif kind == "SECTOR":
        parts.append(f"View, not added to the Book: the sum of {', '.join(row.get('parts') or []) or 'no commodity series'}")
    elif kind == "SPREAD":
        what = ", ".join(x for x in (row.get("spread_kind"), row.get("family")) if x)
        parts.append(f"View, not added to the Book: spread {row.get('spread_id') or row.get('underlyer')}"
                     + (f" ({what})" if what else ""))
        legs = row.get("legs") or []
        if legs:
            parts.append(f"{len(legs)} leg(s): " + "; ".join(_leg_line(leg) for leg in legs))
    if row.get("reason"):
        parts.append(row["reason"])
    return ". ".join(parts)


def _display_name(row: Dict[str, Any]) -> str:
    """A commodity reads 'WTI crude (NYMEX:CL)'; any other row as the engine names it."""
    u = row.get("underlyer", "")
    name = row.get("name")
    if row.get("kind") == "COMMODITY" and name and name != u:
        return f"{name} ({u})"
    return u


def underlyer_record(row: Dict[str, Any], *, book: bool = False, missing: Optional[List[str]] = None) -> Tuple[dict, dict]:
    """(record, tooltips) of one table row. A number is stored as a number; a missing one
    is the string "n/a" with its reason in the tooltip; one that does not apply to the
    row is None (blank). For the Book row (`book`), `missing` (the currency and metal
    rows' reasons, `delta_missing`) is the reason behind a missing delta."""
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
                tip[col] = _tip(reason)
        else:
            rec[col] = v

    kind = "BOOK" if book else (row.get("kind") or "")
    rec["underlyer"] = "Book" if book else _display_name(row)
    rec["kind"] = KIND_LABELS.get(kind, kind)
    if kind == "COMMODITY":
        rec["sector"] = row.get("sector") or "no sector"
    elif kind in VIEW_KINDS:
        rec["sector"] = row.get("sector") or row.get("family") or None
    else:
        rec["sector"] = None
    if not book:
        hover = underlyer_hover(row)
        if hover:
            tip["underlyer"] = _tip(hover)
    if book:
        delta_why = "; ".join(missing) or "no currency or metal position with a USD delta"
    else:
        delta_why = row.get("reason", "")
    put("net_usd", row.get("net_usd"), delta_why)
    put("gross_usd", row.get("gross_usd"), delta_why)
    for metric in METRICS:
        put(metric, row.get(metric), _why(row, metric))
    put("worst_1d_ex_shocks_date", row.get("worst_1d_ex_shocks_date"), _why(row, "worst_1d_ex_shocks_usd"))
    put("worst_1d_raw_date", row.get("worst_1d_raw_date"), _why(row, "worst_1d_raw_usd"))
    if row.get("vol_note"):
        for col in ("vol_blended_ann_usd", "vol_crisis_ann_usd"):
            if col not in tip:
                tip[col] = _tip(row["vol_note"])
    if book:
        rec["worst_day_ex_vs_target_pct"] = None
        tip["worst_day_ex_vs_target_pct"] = _tip("the Book is measured against the stress cap: "
                                                 f"{_pct(row.get('worst_day_ex_vs_cap_pct'))} of the cap (the card above)")
        n = len(row.get("rows_in_series") or [])
        parts = [f"{n} row(s)' daily P&L summed date by date, correlation embedded: "
                 + (", ".join(row.get("rows_in_series") or []) or "none")]
        views = row.get("views") or []
        if views:
            parts.append(f"{len(views)} view(s) below not added")
        if _num(row.get("commodity_net_usd")) is not None and "net_usd" not in tip:
            tip["net_usd"] = _tip("the currency and metal rows only; the commodity rows' net USD is "
                                  f"{_usd(row.get('commodity_net_usd'))} (the Commodity net USD card)")
            tip["gross_usd"] = _tip("the currency and metal rows only; the commodity rows' gross USD is "
                                    f"{_usd(row.get('commodity_gross_usd'))} (the Commodity gross USD card)")
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


def _underlyer_table(table_id: str, records: List[Tuple[dict, dict]], columns: List[dict], defs: Dict[str, str],
                     extra_styles: Optional[List[dict]] = None) -> dash_table.DataTable:
    left = ("underlyer", "kind", "sector", "worst_1d_ex_shocks_date", "worst_1d_raw_date", "note")
    signed = ["net_usd"]
    losses = ["worst_1d_ex_shocks_usd", "worst_1d_raw_usd"]
    numeric_cols = [c["id"] for c in columns if c["type"] == "numeric"]
    return dash_table.DataTable(
        id=table_id,
        columns=columns,
        data=[r for r, _ in records],
        tooltip_data=[t for _, t in records],
        tooltip_header={k: defs[k] for k in defs if any(c["id"] == k for c in columns)},
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(table_id),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in left]
                               + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "220px", "maxWidth": "420px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(signed, bold=True) + rk.sign_styles(losses, pos="inherit")
                               + _na_styles(numeric_cols + ["worst_1d_ex_shocks_date", "worst_1d_raw_date"])
                               + list(extra_styles or []),
    )


def underlyer_section(result: Dict[str, Any]) -> html.Div:
    config = result.get("config") or {}
    defs = definitions(config, result.get("history") or {})
    body_recs = [underlyer_record(r) for r in part_rows(result)]
    book_rec, book_tip = underlyer_record(result.get("book") or {}, book=True, missing=delta_missing(result))
    columns = _columns(config)
    table = _underlyer_table(TABLE_ID, body_recs, columns, defs)
    footer_style = [{"if": {"filter_query": "{kind} = 'Book'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    caption = ("One row per underlyer the Book sums: the currencies and metals, largest gross USD first, then the "
               "commodities grouped by sector. The Book, pinned underneath, is these rows' daily P&L summed date by "
               "date (correlation embedded), the per-underlyer figures standalone; its Net / Gross USD are the "
               "currency and metal rows' (the commodities' are on their own cards). A figure the engine could not "
               "compute reads n/a with its reason on hover; a blank cell does not apply to that row. A commodity's "
               "contracts are on its name's hover; column headers carry the definitions.")
    children: List[Any] = [
        html.H4("Risk by underlyer"),
        html.P(caption, className="section-kicker"),
        rk.with_footer(table, [book_rec], footer_style=footer_style, footer_tooltips=[book_tip],
                       skip_widths=("note",))]
    views = view_rows(result)
    if views:
        view_recs = [underlyer_record(r) for r in views]
        view_table = _underlyer_table(VIEWS_TABLE_ID, view_recs, columns, defs,
                                      extra_styles=[{"if": {"column_id": c["id"]}, **_VIEW_ROW_STYLE}
                                                    for c in columns if c["id"] in ("underlyer", "kind", "sector")])
        children += [
            html.H4("Views (not added to the Book)", style={"marginTop": "14px"}),
            html.P("Each sector row re-adds its commodities and each spread row its futures legs, which are already "
                   "in the rows above: these are other ways of looking at the same risk and must never be summed "
                   "with the Book or with each other. A spread's Net / Gross USD is its leftover outright. The parts "
                   "or legs are on the name's hover.", className="section-kicker"),
            view_table]
    return html.Div(className="section", children=children)


# --------------------------------------------------------------------------- 4. FX scenarios
def scenario_records(scenarios: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips) of the scenario table: Total and FX total as the engine gives
    them, "n/a" with the reason when it could not value one. The engine's equity-index
    line (`futures_pnl`, `equity_pct`) is retired and not shown."""
    records, tips = [], []
    for name, s in (scenarios or {}).items():
        rec: Dict[str, Any] = {"scenario": name}
        tip: Dict[str, dict] = {}
        reason = s.get("reason") or ""
        for col, key in (("total", "total"), ("fx_total", "fx_total")):
            v = rk.value(s.get(key))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(reason or "not computed")
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
        columns=[rk.text("Scenario", "scenario"), rk.numeric("Total", "total", usd), rk.numeric("FX total", "fx_total", usd)],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(SCENARIO_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": "scenario"}, "textAlign": "left"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["total", "fx_total"]) + _na_styles(["total", "fx_total"]),
    )
    order = [r.get("underlyer") for r in shown_underlyers(result)]
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


# --------------------------------------------------------------------------- 5. commodity scenarios
def _missing_line(m: Dict[str, Any]) -> str:
    return f"{m.get('contract_id') or m.get('currency') or m.get('instrument_id') or 'a position'}: {m.get('reason') or 'no reason given'}"


def commodity_scenario_records(cs: Dict[str, Any]) -> Tuple[List[dict], List[dict], List[str]]:
    """(records, tooltips, sectors) of the commodity scenario table, one record per
    scenario of `commodity_scenarios`: kind, the replay's dates, total USD ("n/a" with the
    reason when the engine has none) and one cell per sector (`sector_<i>`, the sectors in
    `sectors`; blank = the scenario moves nothing held in that sector). What the scenario
    could not value is on the total's hover, and the note says how many."""
    scenarios = cs.get("scenarios") or []
    sectors = list(dict.fromkeys(sec for s in scenarios for sec in (s.get("by_sector") or {})))
    records, tips = [], []
    for s in scenarios:
        rec: Dict[str, Any] = {"scenario": s.get("name", ""),
                               "kind": SCENARIO_KIND_LABELS.get(s.get("kind"), s.get("kind") or ""),
                               "dates": f"{s['start']} to {s['end']}" if s.get("start") or s.get("end") else None}
        tip: Dict[str, dict] = {}
        if s.get("description"):
            tip["scenario"] = _tip(s["description"])
        missing = [_missing_line(m) for m in (s.get("missing") or [])]
        total = _num(s.get("total_usd"))
        if total is None:
            rec["total_usd"] = NA
            tip["total_usd"] = _tip(s.get("reason") or "; ".join(missing) or "not computed")
        else:
            rec["total_usd"] = total
            if missing:
                tip["total_usd"] = _tip(f"excludes {len(missing)} position(s) with no figure: " + "; ".join(missing))
            elif s.get("reason"):
                tip["total_usd"] = _tip(s["reason"])
        by_sector = s.get("by_sector") or {}
        for i, sec in enumerate(sectors):
            rec[f"sector_{i}"] = rk.value(by_sector.get(sec)) if sec in by_sector else None
        notes = []
        if missing:
            notes.append(f"excludes {len(missing)} position(s) with no figure (on the total's hover)")
        elif s.get("reason") and total is not None:
            notes.append(s["reason"])
        if s.get("kind") == "fx":
            notes.append("P&L by currency, no sector split")
        rec["note"] = "; ".join(notes)
        records.append(rec)
        tips.append(tip)
    return records, tips, sectors


def _small_table(columns: List[dict], records: List[dict], tips: Optional[List[dict]] = None,
                 left: Tuple[str, ...] = ()) -> dash_table.DataTable:
    """A detail table with no id of its own (it lives inside a collapsed block), ranked
    natively, with n/a muted."""
    numeric_cols = [c["id"] for c in columns if c["type"] == "numeric"]
    return dash_table.DataTable(
        columns=columns, data=records,
        **({"tooltip_data": tips} if tips else {}), tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in left]
                               + [{"if": {"column_id": "reason"}, "whiteSpace": "normal", "minWidth": "200px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles([c for c in numeric_cols if "pnl" in c]) + _na_styles(numeric_cols),
    )


def _value_or_na(rec: Dict[str, Any], tip: Dict[str, dict], col: str, value: Any, reason: str) -> None:
    v = rk.value(value)
    rec[col] = NA if v is None else v
    if v is None:
        tip[col] = _tip(reason or "not computed")


def commodity_scenario_detail(s: Dict[str, Any]) -> html.Details:
    """One scenario's breakdown, collapsed: by root, by contract, by spread, by currency (an
    fx scenario), and the positions it could not value."""
    usd = rk.amount(nully="")
    move = rk.percentage(2, nully="")
    lots = rk.amount(2, nully="", trim=True)
    parts: List[Any] = []
    if s.get("description"):
        parts.append(html.P(s["description"], className="section-kicker"))
    if s.get("by_root"):
        parts += [html.H5("By commodity"),
                  _small_table([rk.text("Root", "root_id"), rk.numeric("P&L USD", "pnl_usd", usd)],
                               [{"root_id": r.get("root_id", ""), "pnl_usd": rk.value(r.get("pnl_usd"))}
                                for r in s["by_root"]], left=("root_id",))]
    if s.get("by_contract"):
        recs, tips = [], []
        for c in s["by_contract"]:
            recs.append({"contract_id": c.get("contract_id", ""), "root_id": c.get("root_id", ""),
                         "sector": c.get("sector", ""), "product": c.get("product", ""), "expiry": c.get("expiry", ""),
                         "lots": rk.value(c.get("lots")), "delta_lots": rk.value(c.get("delta_lots")),
                         "delta_usd": rk.value(c.get("delta_usd")), "move": rk.value(c.get("move")),
                         "pnl_usd": rk.value(c.get("pnl_usd"))})
            hist = c.get("history")
            tips.append({"move": _tip("history: " + ", ".join(f"{k} {v}" for k, v in hist.items()))}
                        if isinstance(hist, dict) and hist else {})
        parts += [html.H5("By contract"),
                  _small_table([rk.text("Contract", "contract_id"), rk.text("Root", "root_id"), rk.text("Sector", "sector"),
                                rk.text("Product", "product"), rk.text("Expiry", "expiry"),
                                rk.numeric("Lots", "lots", lots), rk.numeric("Delta lots", "delta_lots", lots),
                                rk.numeric("Delta USD", "delta_usd", usd), rk.numeric("Move", "move", move),
                                rk.numeric("P&L USD", "pnl_usd", usd)],
                               recs, tips, left=("contract_id", "root_id", "sector", "product", "expiry"))]
    if s.get("by_spread"):
        recs, tips = [], []
        for sp in s["by_spread"]:
            rec: Dict[str, Any] = {"name": sp.get("name") or sp.get("spread_id", ""), "family": sp.get("family", ""),
                                   "reason": sp.get("reason", "")}
            tip: Dict[str, dict] = {}
            why = sp.get("reason") or "; ".join(f"{x.get('root_id')}: {x.get('reason')}" for x in (sp.get("leftover") or [])
                                                if x.get("reason"))
            _value_or_na(rec, tip, "pnl_usd", sp.get("pnl_usd"), why)
            _value_or_na(rec, tip, "spread_pnl_usd", sp.get("spread_pnl_usd"), why)
            _value_or_na(rec, tip, "leftover_pnl_usd", sp.get("leftover_pnl_usd"), why)
            recs.append(rec)
            tips.append(tip)
        parts += [html.H5("By spread (the spread's legs, already in the contracts above: not additive)"),
                  _small_table([rk.text("Spread", "name"), rk.text("Family", "family"),
                                rk.numeric("P&L USD", "pnl_usd", usd), rk.numeric("Spread P&L", "spread_pnl_usd", usd),
                                rk.numeric("Leftover P&L", "leftover_pnl_usd", usd), rk.text("Reason", "reason")],
                               recs, tips, left=("name", "family", "reason"))]
    if s.get("by_currency"):
        recs, tips = [], []
        for c in s["by_currency"]:
            rec = {"currency": c.get("currency", ""), "move": rk.value(c.get("move")),
                   "pnl_local": rk.value(c.get("pnl_local")), "delta_usd": rk.value(c.get("delta_usd")),
                   "delta_usd_change": rk.value(c.get("delta_usd_change"))}
            tip = {}
            why = next((_missing_line(m) for m in (s.get("missing") or []) if m.get("currency") == c.get("currency")), "")
            _value_or_na(rec, tip, "pnl_usd", c.get("pnl_usd"), why)
            _value_or_na(rec, tip, "pnl_change_usd", c.get("pnl_change_usd"), why)
            recs.append(rec)
            tips.append(tip)
        parts += [html.H5("By currency"),
                  _small_table([rk.text("Currency", "currency"), rk.numeric("Move", "move", move),
                                rk.numeric("P&L held (local)", "pnl_local", usd), rk.numeric("P&L held USD", "pnl_usd", usd),
                                rk.numeric("P&L change USD", "pnl_change_usd", usd), rk.numeric("Delta USD", "delta_usd", usd),
                                rk.numeric("Delta USD change", "delta_usd_change", usd)],
                               recs, tips, left=("currency",))]
        parts.append(html.P(f"USD delta change of the non-USD positions: {_usd(s.get('delta_usd_change'))} (it moves the "
                            "exposure, not the P&L).", className="section-kicker"))
    missing = [_missing_line(m) for m in (s.get("missing") or [])]
    if missing:
        parts.append(html.Div(className="section-kicker", children=[
            html.Span(f"Not in the total ({len(missing)}): "),
            html.Ul([html.Li(m) for m in missing], style={"margin": "2px 0 0 16px", "padding": 0})]))
    if not parts or (s.get("reason") and _num(s.get("total_usd")) is None):
        parts.append(html.P(s.get("reason") or "Nothing held is moved by this scenario.", className="section-kicker"))
    total = _num(s.get("total_usd"))
    summary = f"{s.get('name', '')}: " + (format_cell(total) if total is not None else f"n/a ({s.get('reason') or 'not computed'})")
    return html.Details(className="details details--compact", open=False, children=[html.Summary(summary)] + parts)


def commodity_scenario_section(result: Dict[str, Any]) -> html.Div:
    cs = result.get("commodity_scenarios")
    defs = definitions(result.get("config") or {}, result.get("history") or {})
    head = [html.H4("Commodity scenario stress"), html.P(defs["commodity_scenarios"], className="section-kicker")]
    if not cs:
        return html.Div(className="section", children=head + [
            html.P("The commodity scenarios were not computed (the engine returned none).", className="section-kicker")])
    config_file = cs.get("config") or "config/commodity_stress.yaml"
    reasons = [r for r in (cs.get("reasons") or []) if r]
    scenarios = cs.get("scenarios") or []
    if not scenarios:
        why = "; ".join(reasons) or f"no scenarios in {config_file}"
        return html.Div(className="section", children=head + [
            html.P(f"Commodity stress unavailable: {why}." if not cs.get("available", True) else f"No commodity scenarios: {why}.",
                   className="section-kicker")])
    records, tips, sectors = commodity_scenario_records(cs)
    usd = rk.amount(nully="")
    sector_cols = [f"sector_{i}" for i in range(len(sectors))]
    columns = ([rk.text("Scenario", "scenario"), rk.text("Kind", "kind"), rk.text("Dates", "dates"),
                rk.numeric("Total USD", "total_usd", usd)]
               + [rk.numeric(sec, f"sector_{i}", usd) for i, sec in enumerate(sectors)]
               + [rk.text("Note", "note")])
    table = dash_table.DataTable(
        id=COMMODITY_SCENARIO_TABLE_ID,
        columns=columns, data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(COMMODITY_SCENARIO_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("scenario", "kind", "dates", "note")]
                               + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "200px", "maxWidth": "380px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(["total_usd"] + sector_cols) + _na_styles(["total_usd"] + sector_cols),
    )
    children: List[Any] = head + [
        html.P(f"Scenarios from {config_file}, on the book's commodity positions of {cs.get('as_of') or result.get('as_of')}. "
               "A sector cell is blank when the scenario moves nothing held in it; n/a = no position it touches has a "
               "figure (the reason on hover); what a total leaves out is on its hover and in the scenario's detail.",
               className="section-kicker"),
        table]
    if reasons:
        children.append(html.Div(className="section-kicker", children=[
            html.Span("Not valued: "),
            html.Ul([html.Li(r) for r in reasons], style={"margin": "2px 0 0 16px", "padding": 0})]))
    children.append(html.Div(id=COMMODITY_SCENARIO_DETAIL_ID, children=[
        html.H5("Each scenario by commodity, contract, spread and currency")]
        + [commodity_scenario_detail(s) for s in scenarios]))
    return html.Div(className="section", children=children)


# --------------------------------------------------------------------------- 6. margin and limits
def _roll_record(label: str, roll: Dict[str, Any]) -> Tuple[dict, dict]:
    """(record, tooltips) of one margin roll-up (a sector, a root or the book). Every
    position excluded = n/a with the reason (a sum over nothing is not a zero margin);
    some excluded = the sum with the engine's caption in the note and the reason on hover."""
    rec: Dict[str, Any] = {"label": label, "positions": roll.get("positions")}
    tip: Dict[str, dict] = {}
    positions = int(roll.get("positions") or 0)
    excluded = int(roll.get("excluded_count") or 0)
    why = roll.get("reason") or roll.get("caption") or ""
    all_out = positions > 0 and excluded >= positions
    for col in ("gross_charge_usd", "spread_credit_usd", "margin_usd"):
        v = None if all_out else _num(roll.get(col))
        rec[col] = NA if v is None else v
        if v is None:
            tip[col] = _tip(why or "no margin figure")
        elif excluded and col == "margin_usd":
            tip[col] = _tip(why)
    rec["note"] = roll.get("caption") or ("no open commodity position" if positions == 0 else "")
    if all_out and not rec["note"]:
        rec["note"] = f"excludes {excluded} of {positions} positions with no margin figure"
    return rec, tip


def _rate_text(roll: Dict[str, Any]) -> str:
    kind, rate = roll.get("rate_kind"), _num(roll.get("rate"))
    if kind == "rate" and rate is not None:
        return f"{rate * 100:g}% of |delta USD|"
    if kind == "per_lot" and rate is not None:
        return f"{format_cell(rate)} USD per lot"
    return "not set in config/limits.yaml"


def margin_section(margin: Optional[Dict[str, Any]]) -> html.Div:
    head = [html.H4(f"Margin ({MARGIN_BASIS})")]
    if not margin:
        return html.Div(className="section", children=head + [
            html.P("The margin estimate was not computed.", className="section-kicker")])
    reasons = [r for r in (margin.get("reasons") or []) if r]
    kicker = [margin.get("note") or "", f"Basis: {margin.get('basis') or MARGIN_BASIS}.",
              f"Rates from {margin.get('config_file') or 'config/limits.yaml'}; they are placeholders until they are set there."]
    if margin.get("config_note"):
        kicker.append(f"Config: {margin['config_note']}.")
    head.append(html.P(" ".join(k for k in kicker if k), className="section-kicker"))
    if not margin.get("available", False):
        return html.Div(className="section", children=head + [
            html.P("Margin estimate unavailable: " + ("; ".join(reasons) or "no reason given") + ".",
                   className="section-kicker")])
    usd = rk.amount(nully="")
    by_sector = margin.get("by_sector") or {}
    recs = [_roll_record(sec or "no sector", roll) for sec, roll in by_sector.items()]
    book_rec, book_tip = _roll_record("Book", margin.get("book") or {})
    columns = [rk.text("Sector", "label"), rk.numeric("Gross charge USD", "gross_charge_usd", usd),
               rk.numeric("Spread credit USD", "spread_credit_usd", usd), rk.numeric("Margin USD (estimate)", "margin_usd", usd),
               rk.numeric("Positions", "positions", rk.count()), rk.text("Note", "note")]
    num_cols = ["gross_charge_usd", "spread_credit_usd", "margin_usd"]
    table = dash_table.DataTable(
        id=MARGIN_TABLE_ID, columns=columns, data=[r for r, _ in recs], tooltip_data=[t for _, t in recs],
        tooltip_delay=0, tooltip_duration=None, **rk.sortable(MARGIN_TABLE_ID),
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("label", "note")]
                               + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "200px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=_na_styles(num_cols),
    )
    footer_style = [{"if": {"filter_query": "{label} = 'Book'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    children: List[Any] = head + [rk.with_footer(table, [book_rec], footer_style=footer_style, footer_tooltips=[book_tip],
                                                 skip_widths=("note",))]

    by_root = margin.get("by_root") or {}
    if by_root:
        root_recs = []
        for root_id, roll in by_root.items():
            rec, tip = _roll_record(f"{roll.get('name') or root_id} ({root_id})" if roll.get("name") and roll.get("name") != root_id
                                    else root_id, roll)
            rec["sector"] = roll.get("sector") or ""
            rec["rate"] = _rate_text(roll)
            if roll.get("rate_source"):
                tip["rate"] = _tip(roll["rate_source"])
            root_recs.append((rec, tip))
        children.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"By commodity ({len(by_root)})"),
            dash_table.DataTable(
                id=MARGIN_ROOT_TABLE_ID,
                columns=[rk.text("Commodity", "label"), rk.text("Sector", "sector"), rk.text("Rate", "rate")] + columns[1:],
                data=[r for r, _ in root_recs], tooltip_data=[t for _, t in root_recs],
                tooltip_delay=0, tooltip_duration=None, **rk.sortable(MARGIN_ROOT_TABLE_ID),
                style_table={"overflowX": "auto"}, style_cell=_MONO,
                style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("label", "sector", "rate", "note")],
                style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
                style_data_conditional=_na_styles(num_cols)
                                       + [{"if": {"column_id": "rate", "filter_query": "{rate} contains 'not set'"}, **_NA_STYLE}])]))

    spreads = margin.get("spreads") or []
    if spreads:
        sp_recs, sp_tips = [], []
        for sp in spreads:
            pct = _num(sp.get("credit_pct"))
            rec = {"name": sp.get("name") or sp.get("spread_id", ""),
                   "kind": ", ".join(x for x in (sp.get("kind"), sp.get("family")) if x),
                   "credit_key": sp.get("credit_key") or "none",
                   "credit_pct": pct if pct is not None else "not set",
                   "charge_on_matched_usd": rk.value(sp.get("charge_on_matched_usd")),
                   "credit_usd": rk.value(sp.get("credit_usd")),
                   "leftover_charge_usd": rk.value(sp.get("leftover_charge_usd")),
                   "note": sp.get("note") or ""}
            tip = {}
            if pct is None:
                tip["credit_pct"] = _tip(f"no {sp.get('credit_key') or 'spread'} credit set in config/limits.yaml: "
                                         "every lot at the outright rate")
            if sp.get("excluded_count"):
                tip["credit_usd"] = _tip(f"excludes {sp['excluded_count']} leg(s) with no margin figure: "
                                         + ", ".join(sp.get("excluded") or []))
            sp_recs.append(rec)
            sp_tips.append(tip)
        children.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"Spread credits ({len(spreads)})"),
            html.P("The credit each open spread takes on its matched lots (inside the sector's spread credit above, "
                   "not added again); the lots it leaves outright stay charged at the outright rate.",
                   className="section-kicker"),
            dash_table.DataTable(
                id=MARGIN_SPREAD_TABLE_ID,
                columns=[rk.text("Spread", "name"), rk.text("Kind", "kind"), rk.text("Credit key", "credit_key"),
                         rk.numeric("Credit %", "credit_pct", rk.percentage(0, nully="")),
                         rk.numeric("Charge on matched USD", "charge_on_matched_usd", usd),
                         rk.numeric("Credit USD", "credit_usd", usd),
                         rk.numeric("Leftover charge USD", "leftover_charge_usd", usd), rk.text("Note", "note")],
                data=sp_recs, tooltip_data=sp_tips, tooltip_delay=0, tooltip_duration=None,
                **rk.sortable(MARGIN_SPREAD_TABLE_ID),
                style_table={"overflowX": "auto"}, style_cell=_MONO,
                style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("name", "kind", "credit_key", "note")]
                                       + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "200px"}],
                style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
                style_data_conditional=[{"if": {"column_id": "credit_pct", "filter_query": "{credit_pct} = 'not set'"}, **_NA_STYLE}])]))
    if reasons:
        children.append(html.Div(className="section-kicker", children=[
            html.Span("Not in the margin: "),
            html.Ul([html.Li(r) for r in reasons], style={"margin": "2px 0 0 16px", "padding": 0})]))
    return html.Div(className="section", children=children)


def limit_records(checks: List[Dict[str, Any]]) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips) of the limit checks table, in the engine's order. The level is
    shown as the engine names it ("not set in config/limits.yaml" for NOT_SET); a value the
    engine could not measure is n/a with the reason; a limit not set reads "not set"."""
    records, tips = [], []
    for c in checks or []:
        level = c.get("level") or "N/A"
        rec: Dict[str, Any] = {"level": LEVEL_TEXT.get(level, level),
                               "limit": str(c.get("limit") or "").replace("_", " "), "scope": c.get("scope") or "",
                               "source": c.get("source") or "", "unit": c.get("unit") or "",
                               "reason": c.get("reason") or ""}
        tip: Dict[str, dict] = {}
        if c.get("basis"):
            tip["limit"] = _tip(c["basis"])
        v = rk.value(c.get("value"))
        rec["value"] = NA if v is None else v
        if v is None:
            tip["value"] = _tip(c.get("reason") if level == "N/A" and c.get("reason") else "the position has no figure")
        lv = rk.value(c.get("limit_value"))
        rec["limit_value"] = "not set" if lv is None else lv
        if lv is None:
            tip["limit_value"] = _tip(c.get("reason") or "no limit set in config/limits.yaml")
        used = rk.value(c.get("used_pct"))
        rec["used_pct"] = used if used is not None else (NA if level == "N/A" else None)
        if c.get("reason"):
            tip["level"] = _tip(c["reason"])
        records.append(rec)
        tips.append(tip)
    return records, tips


def limits_section(checks: Optional[List[Dict[str, Any]]]) -> html.Div:
    head = [html.H4("Limits")]
    if checks is None:
        return html.Div(className="section", children=head + [
            html.P("The limit checks were not computed.", className="section-kicker")])
    if not checks:
        return html.Div(className="section", children=head + [
            html.P("No limit checks: no open commodity position to measure.", className="section-kicker")])
    records, tips = limit_records(checks)
    counts = {lvl: sum(1 for c in checks if (c.get("level") or "N/A") == lvl) for lvl in LEVEL_TEXT}
    summary = ", ".join(f"{n} {LEVEL_TEXT[lvl] if lvl != 'NOT_SET' else 'not set'}" for lvl, n in counts.items() if n)
    columns = [rk.text("Level", "level"), rk.text("Limit", "limit"), rk.text("Scope", "scope"), rk.text("Source", "source"),
               rk.numeric("Position", "value", rk.amount(2, nully="", trim=True)),
               rk.numeric("Limit", "limit_value", rk.amount(2, nully="", trim=True)), rk.text("Unit", "unit"),
               rk.numeric("Used %", "used_pct", _pct_format()), rk.text("Reason", "reason")]
    level_styles = [{"if": {"column_id": "level", "filter_query": f"{{level}} = '{LEVEL_TEXT[lvl]}'"}, **style}
                    for lvl, style in LEVEL_STYLES.items()]
    table = dash_table.DataTable(
        id=LIMITS_TABLE_ID, columns=columns, data=records, tooltip_data=tips,
        tooltip_delay=0, tooltip_duration=None, **rk.sortable(LIMITS_TABLE_ID),
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("level", "limit", "scope", "source", "unit", "reason")]
                               + [{"if": {"column_id": "reason"}, "whiteSpace": "normal", "minWidth": "220px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=level_styles + _na_styles(["value", "used_pct"])
                               + [{"if": {"column_id": "limit_value", "filter_query": "{limit_value} = 'not set'"}, **_NA_STYLE}],
    )
    return html.Div(className="section", children=head + [
        html.P(f"The book against the desk's own limits and the exchanges' position limits, all from "
               f"config/limits.yaml: {summary}. BREACH above the limit, WARN from the warn fraction of it, OK under; "
               "a limit left empty in the file is not set, with the position still measured. Lots count options at "
               "their delta; the definition of each limit is on its name's hover.", className="section-kicker"),
        table])


def margin_limits_section(margin: Optional[Dict[str, Any]], checks: Optional[List[Dict[str, Any]]]) -> html.Div:
    return html.Div(id=MARGIN_LIMITS_ID, children=[margin_section(margin), limits_section(checks)])


def margin_and_limits(conn: sqlite3.Connection, as_of: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """margin-limits' two results for `as_of`, on one computation of the curve positions and
    the spreads. A failure is a reason in the result's shape, never a crash of the tab."""
    from engine.curve import curve_positions
    from engine.limits import limit_checks, margin_estimate
    try:
        curve = curve_positions(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen
        why = f"the commodity positions could not be computed ({type(exc).__name__}: {exc})"
        return ({"available": False, "reasons": [why]},
                [{"limit": "limit checks", "scope": "", "level": "N/A", "reason": why}])
    spreads = None
    if curve.get("rows"):
        try:
            from engine.spreads import book_spreads
            spreads = book_spreads(conn, as_of)
        except Exception:  # noqa: BLE001 -- margin_estimate reads them itself and names the failure
            spreads = None
    try:
        margin = margin_estimate(conn, as_of, curve=curve, spreads=spreads)
    except Exception as exc:  # noqa: BLE001
        margin = {"available": False, "reasons": [f"the margin estimate could not be computed ({type(exc).__name__}: {exc})"]}
    try:
        checks = limit_checks(conn, as_of, curve=curve)
    except Exception as exc:  # noqa: BLE001
        checks = [{"limit": "limit checks", "scope": "", "level": "N/A",
                   "reason": f"the limit checks could not be computed ({type(exc).__name__}: {exc})"}]
    return margin, checks


# --------------------------------------------------------------------------- 7. definitions
def definitions_block(result: Dict[str, Any]) -> html.Details:
    config = result.get("config") or {}
    history = result.get("history") or {}
    ch = result.get("commodity_history") or {}
    b = config.get("blended") or {}
    defs = definitions(config, history)
    items = [("Daily $ P&L", defs["daily_pnl"]), ("Net / Gross USD delta", defs["net_usd"] + " " + defs["gross_usd"]),
             ("Commodity net / gross USD", defs["commodity_net_usd"] + " " + defs["commodity_gross_usd"]),
             ("Parts and views", defs["parts_views"]),
             ("Blended vol", defs["vol_blended_ann_usd"]), ("Crisis-window fallback", defs["crisis_fallback"]),
             ("VaR", defs["var95_1d_usd"]), ("Worst day ex shocks", defs["worst_1d_ex_shocks_usd"]),
             ("Shock days", defs["shock_days"]),
             ("Worst day raw", defs["worst_1d_raw_usd"]), ("Scenarios", defs["scenarios"]),
             ("Commodity scenarios", defs["commodity_scenarios"])]
    shocks = "; ".join(f"{d.get('date', '')} {d.get('name', '')}".strip() for d in (config.get("shock_dates") or [])) or "none"
    params = (f"vol target {_usd(config.get('vol_target_usd'))}"
              + (" (placeholder)" if config.get("vol_target_placeholder") else "")
              + f"; stress cap {_usd(config.get('stress_cap_usd'))} "
              f"({_pct(config.get('stress_pct'), 0)}); trailing window {b.get('trail_window_bd')} bd, weights "
              f"{_weight(b.get('w_trail'))} / {_weight(b.get('w_stress'))}, crisis window {b.get('stress_start')} to "
              f"{b.get('stress_end')}, cutover {b.get('cutover')}; VaR window {config.get('var_window_bd')} bd at "
              f"{float(config.get('var_confidence') or 0) * 100:g}%; worst day from {config.get('worst_day_start')}; "
              f"shock dates: {shocks}. Read from {config.get('file')}"
              + (" (loaded)" if config.get("loaded") else f" (not loaded: {config.get('note') or 'defaults in use'})") + ".")
    files = []
    for key, f in (history.get("files") or {}).items():
        if key in RETIRED_HISTORY_FILES:
            continue
        state = (f"{f.get('first_date')} to {f.get('last_date')}, {f.get('rows')} rows" if f.get("loaded")
                 else f"not loaded ({f.get('reason') or 'no reason given'})")
        files.append(f"{key}: {f.get('file')} ({state})")
    hist = (f"{history.get('path') or 'no folder'}: " + ("; ".join(files) if files else (history.get("reason") or "no files")))
    items += [("Parameters", params), ("History", hist)]
    if ch:
        items.append(("Commodity history",
                      (f"{ch.get('path')}: settlements {ch.get('first_date')} to {ch.get('last_date')}, "
                       f"{ch.get('roots', 0)} roots, {ch.get('contracts', 0)} contracts"
                       + (f", USD conversion pairs {', '.join(ch.get('fx_pairs') or [])}" if ch.get("fx_pairs") else "")
                       + ". Read-only, a risk input only: nothing from it is a mark or enters P&L.")
                      if ch.get("available") else f"{ch.get('path') or 'no database'}: {ch.get('reason') or 'not available'}"))
    return html.Details(id=DEFINITIONS_ID, className="section section--secondary details", open=False, children=[
        html.Summary("Definitions"),
        html.Dl(className="legend", children=[html.Div([html.Dt(label), html.Dd(text)]) for label, text in items])])


# --------------------------------------------------------------------------- body and shell
def body(result: Dict[str, Any], margin: Optional[Dict[str, Any]] = None,
         checks: Optional[List[Dict[str, Any]]] = None) -> html.Div:
    """The whole tab body from one `book_risk` result and margin-limits' two results."""
    return html.Div(className="risk-body", children=[
        caption_block(result), book_cards(result), underlyer_section(result), scenario_section(result),
        commodity_scenario_section(result), margin_limits_section(margin, checks), definitions_block(result)])


def render(as_of: Optional[str], db_path) -> Any:
    """The body for `as_of` from the database at `db_path`: one `book_risk` call and one
    margin-and-limits pass on a read-only connection, closed straight after. A problem is
    a message where the body would be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local, as the Ladder does: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        result = book_risk(conn, as_of)
        margin, checks = margin_and_limits(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Risk could not be computed for {as_of} ({type(exc).__name__}: {exc}).", className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result, margin, checks)


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
    and on the safety interval (the market histories live outside the database)."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path())
