"""P&L tab: "Where did the P&L come from?" (UI redesign wave 2, user 2026-09-28).

A read-only attribution of the header's own figures. Every number is one of these, as given:
  - the trade valuations of the screens' shared filled reader,
    `ui.tabs.blotter_pricing.priced_value_book` (`value_book` with the fill of hard rule 2);
  - the header's period rule, `ui.tabs.header._priced_single` / `_priced_diff` over the
    reference close `engine.pnl.reference.resolve_reference` picks on the dates of
    `engine.pnl.ledger.period_reference_dates` (the step back of up to 5 business days, the
    "excl. N" / "filled N" / "ref <date>" markers), so the tab's total line is the header's
    figure to the cent;
  - the spread positions of `engine.spreads.book_spreads` through the same reader (the Book
    tab's memoised read, `ui.tabs.book._spreads`), with their own period figures;
  - the Trades tab's former P&L-by-asset-class table (`ui.tabs.blotter.asset_class_pnl_table`,
    imported), here "By product";
  - the header's LTD chart (`ui.tabs.header._build_chart`, memoised per day on the database's
    mtime), moved here from the header and built only while its collapsible is open.

Per-trade period figures (`period_rows`): for LTD a trade's own `pnl_usd`; for a period the
difference of its two valuations by exactly the header's rule (`_priced_diff`'s split,
`engine.pnl.reference.diff_split`): priced at both ends -> LTD(a) - LTD(ref); new since the
reference close -> its LTD; priced today but not on the reference close -> left out with that
reason; unpriced today -> n/a with `value_book`'s reason. The groups (by spread, commodity,
sector, trade) add those known figures up and say "excl. N" otherwise (the display rule of
CLAUDE.md "Header"); nothing is re-marked, converted or filled here.

Layout: the period selector (Daily, 5d, MTD, YTD, LTD; kept in the browser session), then
the body the callback fills: the period's total with the header's markers and, for LTD, the
realised (settled, frozen in `realised_pnl`) against the open P&L; the attribution tables (by
spread position with the outrights and unmatched legs after, by commodity, by sector, by
product, by trade); the collapsed LTD chart; one "Data issues (N)" drawer. Every table ranks
(`ui.tabs.ranking`); money on the summary tables is in k / m with the full figure on hover, the
trade table keeps full figures. The tab has no date picker: it follows the header's as-of and
re-renders in place on the data revision and on its safety interval. `layout(default_date)`
and `register_callbacks(app, get_db_path)` are the shell's interface.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from dash import Input, Output, dash_table, dcc, html

from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import header
from ui.tabs import ranking as rk
from ui.tabs.formatting import about, format_cell, issues_drawer, marker, short_money, tab_link
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "pnl-body"
REFRESH_ID = "pnl-refresh"
PERIOD_ID = "pnl-period"
CHART_DETAILS_ID = "pnl-ltd-details"
CHART_SUMMARY_ID = f"{CHART_DETAILS_ID}-summary"
CHART_CONTAINER_ID = "pnl-ltd-chart-container"
ISSUES_ID = "pnl-issues"
SPREADS_TABLE_ID = "pnl-by-spread-table"
COMMODITY_TABLE_ID = "pnl-by-commodity-table"
SECTOR_TABLE_ID = "pnl-by-sector-table"
TRADES_TABLE_ID = "pnl-by-trade-table"

NA = "n/a"
PERIODS = ("daily", "d5", "mtd", "ytd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "ltd": "LTD"}
PERIOD_TIPS = {"daily": "LTD today less LTD at the previous business day's close.",
               "d5": "LTD today less LTD five business days back.",
               "mtd": "LTD today less LTD at the last business day of the previous month.",
               "ytd": "LTD today less LTD at the last business day of the previous year.",
               "ltd": "Life to date: every trade's P&L at today's marks, settled trades frozen."}
DEFAULT_PERIOD = "daily"
TOTAL_LABEL = "Total"
_PRODUCT_LABELS = {"FX_SPOT": "FX spot", "FX_FWD": "FX forward", "FX_SWAP": "FX swap", "FUTURE": "Future",
                   "FX_OPTION": "FX option", "CMDTY_OPTION": "Option on future", "EQ_OPTION": "Listed option",
                   "LME_FWD": "LME forward"}
_STATUS_LABELS = {"OPEN": "Open", "SETTLED": "Settled", "CLOSED": "Closed out"}

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "nowrap"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_TOTAL_STYLE = [{"if": {"filter_query": f"{{label}} = '{TOTAL_LABEL}'"}, "fontWeight": "700",
                 "borderTop": "2px solid #1f2933"}]
_CARD = {"background": "var(--card)", "border": "1px solid var(--line)", "borderRadius": "8px",
         "padding": "10px 14px", "minWidth": "150px"}
_CARD_TITLE = {"fontSize": "10px", "textTransform": "uppercase", "letterSpacing": ".04em", "color": "var(--muted)"}
_CARD_VALUE = {"fontSize": "18px", "fontWeight": 700, "fontVariantNumeric": "tabular-nums"}

TAB_ABOUT = ("Where did the P&L come from? The header's figure for the chosen period, attributed by spread "
             "position, commodity, sector, product and trade, each group the sum of its trades' figures by the "
             "header's own rule (priced trades only, a period over the trades priced at both ends, the reference "
             "close stepped back when it has no value); realised against open for LTD; the LTD line since the "
             "first trade. Nothing is re-marked here.")
SPREADS_ABOUT = ("One row per spread position (spreads-engine: the same spread put on over several trade dates is "
                 "one), its legs on hover, then the outright contracts (futures in no spread) and the unmatched "
                 "legs of the spreads. The figure is the position's trades' P&L summed by spreads-engine against "
                 "the header's reference close; a position with a leg unpriced is n/a with the leg's reason, "
                 "never a partial sum. FX hedges, LME forwards and options on futures are not spreads: they are "
                 "in the tables below and on the Trades tab. Money in k / m, the full figure on hover.")
GROUP_ABOUT = ("The trades' period figures added up per group: priced trades only, and for a period only the "
               "trades priced at both ends (a trade new since the reference close counts in full, as trading "
               "P&L). A group that leaves trades out says so (excl. N, the trades and reasons on hover); a group "
               "with nothing priced is n/a. The Total is the header's figure. Money in k / m, the full figure on "
               "hover.")
TRADES_ABOUT = ("Every trade on the book at the as-of date with its figure for the chosen period, full figures: "
                "LTD is the trade's own P&L at the day's marks (frozen once settled); a period is its LTD less "
                "its LTD at the reference close, by the header's rule. A trade with no figure says why on hover.")
REALISED_ABOUT = ("Realised = the settled trades, frozen by the ledger (realised_pnl) and never marked again; "
                  "open = the trades still marked (a closed-out option group is valued at its closing fill until "
                  "expiry and counts as open here). Each is the known figures summed, excl. N otherwise; their "
                  "sum is the header's LTD.")


# --------------------------------------------------------------------------- small helpers
def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%a} {d.day} {d:%b %Y}"


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def full_usd(v: float) -> str:
    return f"{format_cell(v)} USD"


def _sum_known(values: Sequence[Optional[float]]) -> Tuple[Optional[float], int]:
    """(the known figures summed, how many were None); None when none is known."""
    known = [v for v in values if v is not None and v == v]
    return (float(sum(known)) if known else None), len(values) - len(known)


# --------------------------------------------------------------------------- the period
class PeriodView:
    """One period's figures: `entry` (the header's whole-book entry with its markers), `rows`
    (one per trade on the as-of book: trade_id, instrument_id, product, status, trade_date,
    value (float or None), reason, note), `ref_iso` (the period's own reference date, '' for
    LTD), `ref_used` (the close measured from) and `blocked` (the trades priced today but not
    on the reference close, left out with that reason)."""

    def __init__(self, key: str, entry: dict, rows: pd.DataFrame, ref_iso: str = "", ref_used: str = "",
                 blocked: Sequence[str] = ()):
        self.key, self.entry, self.rows, self.ref_iso, self.ref_used = key, entry, rows, ref_iso, ref_used
        self.blocked = list(blocked)

    @property
    def title(self) -> str:
        return PERIOD_TITLES[self.key]


def _rows_frame(df: pd.DataFrame, values: Dict[str, Optional[float]], reasons: Dict[str, str],
                notes: Dict[str, str]) -> pd.DataFrame:
    out = df[["trade_id", "instrument_id", "product", "status", "trade_date"]].copy()
    out["value"] = [values.get(t) for t in out["trade_id"]]
    out["reason"] = [reasons.get(t, "") for t in out["trade_id"]]
    out["note"] = [notes.get(t, "") for t in out["trade_id"]]
    return out


def period_rows(conn: sqlite3.Connection, as_of: str, key: str, df_today: pd.DataFrame) -> PeriodView:
    """The per-trade figures of `key` (`PERIODS`) on `as_of`, by the header's rule (module
    docstring). The whole-book `entry` is `header._priced_single` / `_priced_diff` itself,
    with the fill and step-back markers `header._build_figures` puts beside the header's
    cards, so the Total line here is the header's figure."""
    from engine.pnl.calendar import load_holidays
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.reference import annotate, fill_caption, filled_from, resolve_reference
    from ui.tabs.blotter_pricing import priced_value_book

    try:
        root_reason = header._root_reason(conn, as_of, header._needs_cached(conn, as_of))
    except Exception:  # noqa: BLE001 -- the plain fallback sentence, as the header does
        root_reason = header._root_reason(conn, as_of)
    if df_today.empty:
        return PeriodView(key, dict(header._EMPTY_PRICED), _rows_frame(df_today, {}, {}, {}))

    reasons_today = {t: str(r or "") for t, r in zip(df_today["trade_id"], df_today["reason"])}
    notes_today = {t: str(n or "") for t, n in zip(df_today["trade_id"], df_today.get("note", [""] * len(df_today)))}
    a_pnl = {t: float(v) for t, v, r in zip(df_today["trade_id"], df_today["pnl_usd"], df_today["reason"])
             if not r and v == v}

    if key == "ltd":
        entry = header._priced_single(df_today, root_reason)
        n_filled = header._filled_count(df_today)
        if n_filled and entry.get("available"):
            entry["markers"] = [(f"filled {n_filled}",
                                 header._joined(fill_caption(df_today, as_of), header._fill_notes(df_today)))]
        values = {t: a_pnl.get(t) for t in df_today["trade_id"]}
        notes = {t: n for t, n in notes_today.items() if filled_from(n)}
        return PeriodView(key, entry, _rows_frame(df_today, values, reasons_today, notes))

    holidays = load_holidays()
    ref_iso = period_reference_dates(as_of)[key]
    backfill = header.backfill_status(conn)
    title = PERIOD_TITLES[key]

    def _book(iso: str) -> pd.DataFrame:
        return priced_value_book(conn, iso)[0]

    choice = resolve_reference(df_today, ref_iso, _book, holidays, frames_filled=True)
    entry = header._priced_diff(df_today, choice.frame, root_reason, choice.ref_date_used,
                                header._reference_reason(conn, ref_iso, title, backfill))
    entry = annotate(entry, choice,
                     lambda s: header._reference_reason(conn, s.date, title, backfill)(s.n_blocked, s.n_open_then))
    if entry.get("available"):
        entry["markers"] = header._reference_markers(entry, choice)
    split = choice.split
    ref_used = choice.ref_date_used
    frame_b = choice.frame
    b_pnl = ({t: float(v) for t, v, r in zip(frame_b["trade_id"], frame_b["pnl_usd"], frame_b["reason"]) if not r and v == v}
             if not frame_b.empty else {})
    b_notes = ({t: str(n or "") for t, n in zip(frame_b["trade_id"], frame_b.get("note", [""] * len(frame_b)))}
               if not frame_b.empty else {})
    values: Dict[str, Optional[float]] = {}
    reasons: Dict[str, str] = {}
    notes: Dict[str, str] = {}
    unusable = not entry.get("available")
    for t in df_today["trade_id"]:
        if unusable:
            values[t] = None
            reasons[t] = str(entry.get("reason") or f"{title} has no usable reference close")
        elif t in split.contributing_b_ids:
            values[t] = a_pnl[t] - b_pnl[t]
            fill_note = b_notes.get(t, "")
            notes[t] = f"reference close {ref_used}: {fill_note}" if filled_from(fill_note) else ""
        elif t in split.contributing_a_ids:
            values[t] = a_pnl[t]
            notes[t] = f"new since the {ref_used} close: its whole LTD counts (trading P&L)"
        elif t in split.blocked_ids:
            values[t] = None
            reasons[t] = f"priced today but not on the {ref_used} close: left out of {title} rather than faked"
        else:
            values[t] = None
            reasons[t] = reasons_today.get(t) or f"no P&L on {as_of}"
        if filled_from(notes_today.get(t, "")) and values[t] is not None:
            notes[t] = header._joined(notes[t], notes_today[t])
    return PeriodView(key, entry, _rows_frame(df_today, values, reasons, notes), ref_iso, ref_used,
                      sorted(split.blocked_ids))


# --------------------------------------------------------------------------- 1. the total and realised / open
def _entry_card(title: str, entry: dict, hover: str = "") -> html.Div:
    """One figure card: the value in k / m with the full figure on hover, its markers beside
    it, n/a with the reason when unavailable."""
    if not entry.get("available"):
        value = html.Span(NA, style={**_CARD_VALUE, **_NA_STYLE, "cursor": "help"},
                          title=str(entry.get("reason") or "no figure"))
        markers: List[Any] = []
    else:
        v = float(entry["value"])
        colour = "var(--pos)" if v > 0 else "var(--neg)" if v < 0 else "var(--text)"
        value = html.Span(short_money(v, "$"), style={**_CARD_VALUE, "color": colour}, title=full_usd(v))
        markers = [marker(short, sentence) for short, sentence, *_rest in header._entry_markers(entry)]
    return html.Div(style=_CARD, title=hover or None, children=[
        html.Div(title, style=_CARD_TITLE), html.Div([value, *[m for m in markers if m is not None]],
                                                     style={"display": "flex", "gap": "8px", "alignItems": "baseline"})])


def realised_entries(df_today: pd.DataFrame) -> Dict[str, dict]:
    """{'settled', 'open'}: each the known LTD figures of those trades summed (the header's
    `_priced_single` over the status subset), so the two add to the header's LTD."""
    out = {}
    for name, statuses in (("settled", ("SETTLED",)), ("open", ("OPEN", "CLOSED"))):
        subset = df_today[df_today["status"].isin(statuses)] if not df_today.empty else df_today
        entry = header._priced_single(subset, "no trade of this kind has a figure")
        entry["count"] = int(len(subset))
        out[name] = entry
    return out


def summary_section(view: PeriodView, df_today: pd.DataFrame, as_of: str) -> html.Div:
    """The period's total (the header's figure) and, for LTD, realised against open."""
    ref = (f"measured from the {view.ref_used} close" + (f" (stepped back from {view.ref_iso})"
                                                         if view.ref_used != view.ref_iso else "")
           if view.ref_iso else f"every trade at the {as_of} marks")
    cards = [_entry_card(f"{view.title} P&L", view.entry, hover=f"{PERIOD_TIPS[view.key]} {ref[0].upper()}{ref[1:]}.")]
    if view.key == "ltd":
        parts = realised_entries(df_today)
        cards.append(_entry_card(f"Realised (settled, {parts['settled']['count']})", parts["settled"], REALISED_ABOUT))
        cards.append(_entry_card(f"Open ({parts['open']['count']})", parts["open"], REALISED_ABOUT))
    return html.Div(className="section", children=[
        html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "12px", "alignItems": "stretch"}, children=cards),
        html.Div(className="meta-line", style={"marginTop": "8px"}, children=[
            html.Span(f"As of {_date_words(as_of)}; {ref}.")])])


# --------------------------------------------------------------------------- 2. by spread position
def _period_entry(figures: dict, excluded: dict, reasons: dict, notes: dict, key: str) -> Tuple[Optional[float], str, str]:
    """(value, the marker's short text, hover) of one spread's period from spreads-engine's dicts."""
    v = _num((figures or {}).get(key))
    n_left = int((excluded or {}).get(key) or 0)
    why = str((reasons or {}).get(key) or "")
    note = str((notes or {}).get(key) or "")
    if v is None:
        return None, NA, why or "spreads-engine gave no figure for this period"
    short = f"excl. {n_left}" if n_left else ""
    return v, short, header._joined(f"{full_usd(v)}", f"excludes {n_left} entries: {why}" if n_left else "", note)


def spread_records(spreads: Optional[dict], key: str, roots: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    """The spread positions (open, then closed), then the outright contracts, then the unmatched
    legs, each with the period figure spreads-engine gives it; a leg's contract is named by
    `ui.tabs.book.contract_label`."""
    from ui.tabs.book import contract_label
    from ui.tabs.spreads import position_size_text
    records, tips = [], []
    if not spreads:
        return records, tips
    positions = list(spreads.get("positions") or [])
    positions.sort(key=lambda p: (0 if p.get("status") == "open" else 1, str(p.get("name") or "")))
    for p in positions:
        legs = p.get("legs") or []
        v, short, hover = _period_entry(p.get("pnl_usd"), p.get("pnl_excluded"), p.get("pnl_reasons"),
                                        p.get("pnl_notes"), key)
        size, size_hover = position_size_text(p)
        left = [e for e in (p.get("leftover") or []) if _num(e.get("lots"))]
        unmatched = "; ".join(f"{str(e.get('root_id') or '').split(':')[-1]} {_num(e.get('lots')):+g} lot(s)" for e in left)
        rec = {"kind": "spread", "label": str(p.get("name") or p.get("position_id") or ""),
               "what": "Spread" if p.get("status") == "open" else "Spread (closed)",
               "size": size, "legs": ", ".join(contract_label(leg.get("instrument_id")) for leg in legs),
               "value": v, "note": short if v is not None else "",
               "unmatched": unmatched}
        tip = {"value": _tip(hover), "size": _tip(size_hover),
               "legs": _tip("; ".join(f"{contract_label(leg.get('instrument_id'))}: {_num(leg.get('lots')):+g} lot(s)"
                                      + (f" ({leg['reason']})" if leg.get("reason") else "") for leg in legs) or "no legs"),
               "label": _tip(f"trades {', '.join(p.get('trade_ids') or [])}; entries {', '.join(p.get('spread_ids') or [])}")}
        if short and v is not None:
            tip["note"] = _tip(hover)
        if unmatched:
            tip["unmatched"] = _tip("the open lots the spread's ratio does not match: an outright position of its own")
        records.append(rec)
        tips.append(tip)
    by_contract: Dict[str, List[dict]] = {}
    for o in spreads.get("outrights") or []:
        by_contract.setdefault(str(o.get("instrument_id") or ""), []).append(o)
    for inst, trades in by_contract.items():
        known = [_num((t.get("pnl_usd") or {}).get(key)) for t in trades]
        value, n_left = _sum_known(known)
        why = "; ".join(f"{t.get('trade_id')}: {(t.get('pnl_reasons') or {}).get(key) or 'no figure'}"
                        for t, v in zip(trades, known) if v is None)
        root_id = str(trades[0].get("root_id") or "")
        name = str(getattr(roots.get(root_id), "name", "") or root_id)
        lots = [_num(t.get("lots")) for t in trades if str(t.get("status") or "open") == "open"]
        open_lots = sum(v for v in lots if v is not None)
        rec = {"kind": "outright", "label": f"{contract_label(inst)} outright", "what": "Outright",
               "size": f"{open_lots:+g} lots" if lots else "settled", "legs": name, "value": value,
               "note": (f"excl. {n_left}" if n_left and value is not None else ""), "unmatched": ""}
        tip = {"label": _tip(f"{name}: {_plural(len(trades), 'trade')} in no spread ("
                             + "; ".join(dict.fromkeys(str(t.get("why_outright") or "no spread fits") for t in trades)) + ")"),
               "value": _tip(full_usd(value) + (f"; excludes {n_left}: {why}" if n_left else "") if value is not None
                             else why or "no figure")}
        if rec["note"]:
            tip["note"] = _tip(f"excludes {n_left} of {len(trades)} trades: {why}")
        records.append(rec)
        tips.append(tip)
    return records, tips


def spreads_section(spreads: Optional[dict], view: PeriodView, roots: Dict[str, Any], error: str = "") -> html.Div:
    heading = about(f"By spread position ({view.title})", SPREADS_ABOUT)
    if error:
        return html.Div(className="section", children=[heading, marker("n/a", error)])
    records, tips = spread_records(spreads, view.key, roots)
    if not records:
        return html.Div(className="section", children=[
            heading, html.P("No futures spread or outright on the book at this date.", className="section-kicker")])
    value, n_left = _sum_known([r["value"] for r in records])
    footer = {"kind": "total", "label": TOTAL_LABEL, "what": "", "size": "", "legs": f"{len(records)} lines",
              "value": value, "note": f"excl. {n_left}" if n_left and value is not None else "", "unmatched": ""}
    footer_tip = {"value": _tip(full_usd(value) + " (the spread positions and outrights with a figure summed)"
                                if value is not None else "no line has a figure"),
                  "note": _tip(f"{n_left} line(s) with no figure left out") if n_left else _tip("")}
    for rec, tip in zip(records, tips):
        if isinstance(rec.get("value"), float) and "value" not in tip:
            tip["value"] = _tip(full_usd(rec["value"]))
    records = rk.whole_units(records, ("value",))
    footer = rk.whole_units([footer], ("value",))[0]
    table = dash_table.DataTable(
        id=SPREADS_TABLE_ID,
        columns=[rk.text("Position", "label"), rk.text("Kind", "what"), rk.text("Size", "size"),
                 rk.text("Legs", "legs"), rk.numeric(f"{view.title} P&L", "value", rk.amount_short(nully=NA)),
                 rk.text("", "note"), rk.text("Unmatched legs", "unmatched")],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(SPREADS_TABLE_ID),
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("label", "what", "size", "legs", "note", "unmatched")]
                               + [{"if": {"column_id": "note"}, "color": "var(--muted)"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(["value"], bold=True, nil=_NA_STYLE)
                               + [{"if": {"filter_query": "{kind} = 'outright'"}, "color": "var(--muted)"}],
    )
    return html.Div(className="section", children=[
        heading, rk.with_footer(table, [footer], footer_style=_TOTAL_STYLE, footer_tooltips=[footer_tip])])


# --------------------------------------------------------------------------- 3. by commodity / sector
def _labelled(conn: sqlite3.Connection, rows: pd.DataFrame) -> pd.DataFrame:
    """The per-trade rows with the Trades tab's descriptive columns (commodity, sector,
    exchange), looked up by `ui.tabs.blotter.add_instrument_fields`, never computed."""
    from ui.tabs.blotter import add_instrument_fields
    if rows.empty:
        for col in ("commodity", "sector", "exchange"):
            rows[col] = pd.Series(dtype=object)
        return rows
    return add_instrument_fields(conn, rows)


def group_records(rows: pd.DataFrame, by: Sequence[str], entry: dict) -> Tuple[List[dict], List[dict], dict, dict]:
    """(records, tooltips, footer, footer tooltip): the trades' period figures summed per
    group, in |value| order; the footer is the header's `entry`."""
    records, tips = [], []
    if rows.empty:
        return records, tips, {}, {}
    for keys, g in rows.groupby(list(by), sort=False, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        values = [(_num(v)) for v in g["value"]]
        value, n_left = _sum_known(values)
        left = [(t, r) for t, r, v in zip(g["trade_id"], g["reason"], values) if v is None]
        rec = {"label": " / ".join(str(k or "") for k in keys), "trades": int(len(g)), "value": value,
               "note": f"excl. {n_left}" if n_left and value is not None else ""}
        why = "; ".join(f"{t}: {r or 'no figure'}" for t, r in left[:6]) + (f"; and {len(left) - 6} more" if len(left) > 6 else "")
        tip = {"value": _tip(full_usd(value) + (f"; excludes {n_left} of {len(g)}: {why}" if n_left else "")
                             if value is not None else f"no trade of this group has a figure: {why}")}
        if rec["note"]:
            tip["note"] = _tip(f"excludes {n_left} of {len(g)} trades: {why}")
        records.append(rec)
        tips.append(tip)
    records_tips = sorted(zip(records, tips), key=lambda rt: -abs(rt[0]["value"]) if rt[0]["value"] is not None else 1.0)
    records, tips = [r for r, _ in records_tips], [t for _, t in records_tips]
    footer = {"label": TOTAL_LABEL, "trades": int(len(rows)),
              "value": float(entry["value"]) if entry.get("available") else None,
              "note": " ".join(short for short, _s, *_r in header._entry_markers(entry)) if entry.get("available") else ""}
    footer_tip = {"value": _tip(full_usd(footer["value"]) + " (the header's figure)" if footer["value"] is not None
                                else str(entry.get("reason") or "no figure")),
                  "note": _tip("; ".join(sentence for _s, sentence, *_r in header._entry_markers(entry)))}
    return records, tips, footer, footer_tip


def group_section(title: str, table_id: str, rows: pd.DataFrame, by: Sequence[str], view: PeriodView,
                  label: str) -> html.Div:
    heading = about(f"{title} ({view.title})", GROUP_ABOUT)
    records, tips, footer, footer_tip = group_records(rows, by, view.entry)
    if not records:
        return html.Div(className="section", children=[heading, html.P("No trades on the book at this date.",
                                                                       className="section-kicker")])
    records = rk.whole_units(records, ("value",))
    footer = rk.whole_units([footer], ("value",))[0]
    table = dash_table.DataTable(
        id=table_id,
        columns=[rk.text(label, "label"), rk.numeric("Trades", "trades", rk.count()),
                 rk.numeric(f"{view.title} P&L", "value", rk.amount_short(nully=NA)), rk.text("", "note")],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(table_id),
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("label", "note")]
                               + [{"if": {"column_id": "note"}, "color": "var(--muted)"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["value"], bold=True, nil=_NA_STYLE),
    )
    return html.Div(className="section", children=[
        heading, rk.with_footer(table, [footer], footer_style=_TOTAL_STYLE, footer_tooltips=[footer_tip])])


# --------------------------------------------------------------------------- 4. by product (the Trades tab's table)
def product_section(conn: sqlite3.Connection, as_of: str, df_today: pd.DataFrame) -> html.Div:
    """The P&L by asset class as the Trades tab showed it (`ui.tabs.blotter.asset_class_pnl_table`,
    every period across), here under "By product"."""
    from ui.tabs.blotter import asset_class_pnl_table
    if df_today.empty:
        return html.Div(className="section", children=[about("By product", None),
                                                       html.P("No trades on the book at this date.", className="section-kicker")])
    section = asset_class_pnl_table(conn, as_of, df_today)
    for child in section.children or []:   # the Trades tab's title, renamed for this screen
        if isinstance(child, html.H4) and "about-title" in str(getattr(child, "className", "")):
            if isinstance(child.children, list) and child.children:
                child.children[0] = "By product (every period)"
            else:
                child.children = "By product (every period)"
    return section


# --------------------------------------------------------------------------- 5. by trade
def trade_records(rows: pd.DataFrame) -> Tuple[List[dict], List[dict]]:
    records, tips = [], []
    for r in rows.itertuples(index=False):
        v = _num(r.value)
        rec = {"trade_id": str(r.trade_id), "instrument_id": str(r.instrument_id),
               "commodity": str(getattr(r, "commodity", "") or ""),
               "product": _PRODUCT_LABELS.get(str(r.product), str(r.product)),
               "status": _STATUS_LABELS.get(str(r.status), str(r.status)), "trade_date": str(r.trade_date),
               "value": v, "note": ("filled" if "no price on" in str(r.note or "") else "new" if "new since" in str(r.note or "") else "")}
        tip = {}
        if v is None:
            tip["value"] = _tip(str(r.reason or "no figure"))
        elif r.note:
            tip["value"] = _tip(str(r.note))
            tip["note"] = _tip(str(r.note))
        records.append(rec)
        tips.append(tip)
    return records, tips


def trades_section(rows: pd.DataFrame, view: PeriodView) -> html.Div:
    heading = about(f"By trade ({view.title})", TRADES_ABOUT)
    records, tips = trade_records(rows)
    if not records:
        return html.Div(className="section", children=[heading, html.P("No trades on the book at this date.",
                                                                       className="section-kicker")])
    table = dash_table.DataTable(
        id=TRADES_TABLE_ID,
        columns=[rk.text("Trade", "trade_id"), rk.text("Instrument", "instrument_id"), rk.text("Commodity / pair", "commodity"),
                 rk.text("Product", "product"), rk.text("Status", "status"), rk.text("Traded", "trade_date"),
                 rk.numeric(f"{view.title} P&L (USD)", "value", rk.amount(nully=NA)), rk.text("", "note")],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(TRADES_TABLE_ID),
        page_action="native", page_size=25,
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("trade_id", "instrument_id", "commodity", "product", "status", "trade_date", "note")]
                               + [{"if": {"column_id": "note"}, "color": "var(--muted)"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["value"], nil=_NA_STYLE),
    )
    return html.Div(className="section", children=[heading, table])


# --------------------------------------------------------------------------- 6. the LTD chart
def chart_section() -> html.Details:
    """The collapsed LTD chart; its body is filled by the chart callback only while open."""
    return html.Details(id=CHART_DETAILS_ID, className="section details", open=False, children=[
        html.Summary("LTD chart", id=CHART_SUMMARY_ID,
                     title="The LTD line over every business day since the first trade: each day the sum of its "
                           "priced trades, the excluded count and the fill on hover, a gap where nothing priced."),
        html.Div(id=CHART_CONTAINER_ID)])


_SUMMARY_OPEN_MIRROR_JS = (
    "function(n_clicks) {\n"
    "    if (!n_clicks) { return window.dash_clientside.no_update; }\n"
    f"    var details = document.getElementById({CHART_DETAILS_ID!r});\n"
    "    if (!details) { return window.dash_clientside.no_update; }\n"
    "    return Boolean(details.open);\n"
    "}"
)


# --------------------------------------------------------------------------- 7. data issues
def issue_items(view: PeriodView, spreads: Optional[dict], spreads_error: str) -> List[Any]:
    items: List[Any] = []
    rows = view.rows
    if not rows.empty:
        for r in rows.itertuples(index=False):
            if _num(r.value) is None and r.reason:
                items.append((str(r.trade_id), f"no {view.title} figure: {r.reason}"))
        for r in rows.itertuples(index=False):
            if _num(r.value) is not None and "no price on" in str(r.note or ""):
                items.append((str(r.trade_id), str(r.note)))
    if not view.entry.get("available"):
        items.append((view.title, str(view.entry.get("reason") or "no figure")))
    if spreads_error:
        items.append(("Spreads", spreads_error))
    elif spreads:
        review = spreads.get("review") or []
        if review:
            items.append(("Spreads", f"{_plural(len(review), 'group')} spreads-engine could not group, listed as "
                                     "outrights here (the Spreads tab names them)."))
        for reason in spreads.get("reasons") or []:
            items.append(("Spreads", str(reason)))
    return items


# --------------------------------------------------------------------------- body and shell
def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the root id stands for its name
        return {}


def _safe(build: Callable[[], Any], label: str) -> Any:
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank section
        logging.getLogger(__name__).exception("P&L section %r failed", label)
        return html.Div(className="section", children=[
            about(label, None), marker("n/a", f"{label} could not be built ({type(exc).__name__}: {exc})")])


def body(conn: sqlite3.Connection, as_of: str, period: str) -> html.Div:
    from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
    period = period if period in PERIODS else DEFAULT_PERIOD
    with pricing_snapshot(conn, "P&L tab"):
        df_today, _n_filled, _n_total = priced_value_book(conn, as_of)
        view = period_rows(conn, as_of, period, df_today)
        spreads, spreads_error = None, ""
        try:
            from ui.tabs.book import _spreads
            spreads = _spreads(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- the reason in the section and the drawer
            spreads_error = f"spreads-engine could not group the book ({type(exc).__name__}: {exc})"
        labelled = _labelled(conn, view.rows.copy())
        roots = _roots()
        children: List[Any] = [
            _safe(lambda: summary_section(view, df_today, as_of), "Total"),
            _safe(lambda: spreads_section(spreads, view, roots, spreads_error), "By spread position"),
            _safe(lambda: group_section("By commodity", COMMODITY_TABLE_ID, labelled, ("commodity",), view,
                                        "Commodity / pair"), "By commodity"),
            _safe(lambda: group_section("By sector", SECTOR_TABLE_ID, labelled, ("sector",), view, "Sector"), "By sector"),
            _safe(lambda: product_section(conn, as_of, df_today), "By product"),
            _safe(lambda: trades_section(labelled, view), "By trade"),
            chart_section(),
        ]
        drawer = issues_drawer(issue_items(view, spreads, spreads_error), id=ISSUES_ID)
        if drawer is not None:
            children.insert(1, drawer)
    return html.Div(className="pnl-body", children=children)


def render(as_of: Optional[str], db_path, period: str = DEFAULT_PERIOD) -> Any:
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        return body(conn, as_of, period)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        logging.getLogger(__name__).exception("P&L tab failed for as_of=%s", as_of)
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The P&L attribution could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: the title row, the period selector and the body container the
    callback fills. No date picker: the tab follows the header's as-of store."""
    return html.Div(className="pnl-tab", children=[
        html.Div(className="ladder-title-row", children=[
            about("P&L", TAB_ABOUT, level="h3", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                marker(f"header's as-of{f' {default_date}' if default_date else ''}",
                       "The tab follows the header's as-of date; it has no date picker of its own."),
                tab_link("→ Trades", "blotter", "pnl-title-trades", title="Open the Trades tab: every trade row in full")])]),
        html.Div(className="meta-line", children=[
            html.Span("Period: "),
            dcc.RadioItems(id=PERIOD_ID,
                           options=[{"label": html.Span(PERIOD_TITLES[p], title=PERIOD_TIPS[p]), "value": p} for p in PERIODS],
                           value=DEFAULT_PERIOD, inline=True, persistence=True, persistence_type="session",
                           inputStyle={"marginRight": "4px", "marginLeft": "10px"})]),
        html.Div(id=BODY_ID, children=[message_box("Loading the P&L attribution...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body re-renders on the header's as-of, the period, every data revision and the
    safety interval. The LTD chart (moved from the header, 2026-09-28) keeps the header's two
    callbacks on this tab's ids: a clientside mirror of the collapsible's DOM `open` state
    (Dash never syncs a native <details> toggle to `open`), and the chart itself, built only
    while the collapsible is open (`header._build_chart`, memoised per day on the database)."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(PERIOD_ID, "value"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, period=DEFAULT_PERIOD, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path(), period or DEFAULT_PERIOD)

    app.clientside_callback(
        _SUMMARY_OPEN_MIRROR_JS,
        Output(CHART_DETAILS_ID, "open"),
        Input(CHART_SUMMARY_ID, "n_clicks"),
    )

    @app.callback(
        Output(CHART_CONTAINER_ID, "children"),
        Input(CHART_DETAILS_ID, "open"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
    )
    def _update_chart(is_open: bool, as_of: Optional[str], _data_rev=None):
        if not is_open or not as_of:
            from dash import no_update
            return no_update
        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return html.P(f"Database not available ({exc}).")
        try:
            return header._build_chart(conn, as_of, db_path=db_path)
        except Exception as exc:  # noqa: BLE001 -- the reason on screen, never an empty panel
            logging.getLogger(__name__).exception("P&L LTD chart failed for as_of=%s", as_of)
            return html.P(header._failure_reason("LTD chart could not be built", exc, conn),
                          className="section-kicker")
        finally:
            conn.close()
