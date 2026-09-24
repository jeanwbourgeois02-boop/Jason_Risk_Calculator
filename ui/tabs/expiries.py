"""Expiries tab: "What is about to expire or go to delivery?" (Commodity conversion plan,
Phase 1 step 3). Rendered from `engine.expiry.expiry_schedule` and nothing else: every date,
business-day count, level and reason on the tab is that dict's; nothing here computes a date,
a count or a level (CLAUDE.md "Tabs as views").

Layout, top to bottom (`body`):
  1. `caption_block`: the as-of and what the counts mean (business days to the alert date, on
     the contract's own exchange calendar; "(est.)" marks contract-master's estimates).
  2. `counts_strip`: one card per level (`counts`, worst first: EXPIRED and RED strong, AMBER
     warm, GREEN quiet) and the thresholds sentence from `thresholds`.
  3. `schedule_section`: one row per open position in the engine's order (worst first), or
     the engine's `note` when there is nothing to show. Columns: #, Level, Contract, Name,
     Exchange, Lots, Next event, Event date, Last trade, First notice, Alert date, Alert basis,
     Business days to alert date, Calendar, Delivery, Dates source, Reason.

An estimated date (`estimated`) is always shown as estimated: every date of that row carries
"(est.)" and Dates source reads "Estimated, not Bloomberg's". A row whose count reaches past
its calendar file's coverage (`beyond_calendar_coverage`) reads "(beyond coverage)" in its
Calendar cell, with the reason on hover of its count. Every table ranks (`ui.tabs.ranking`):
numbers stored as numbers, a missing figure the string "n/a" (ranks last) with the row's
reason as its tooltip. "#" is the engine's own order, so a click on it restores worst first.

The tab has no date picker: it follows the header's as-of store and re-renders in place on
the data revision and on its safety interval. `layout(default_date)` and
`register_callbacks(app, get_db_path)` are the shell's interface, the Risk tab's shape.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from dash import Input, Output, dash_table, dcc, html

from engine.expiry import expiry_schedule
from engine.expiry.levels import AMBER, EXPIRED, GREEN, LEVELS, RED
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "expiries-body"
REFRESH_ID = "expiries-refresh"
COUNTS_ID = "expiries-counts"
TABLE_ID = "expiries-table"

NA = "n/a"
EST = " (est.)"
BEYOND = " (beyond coverage)"
BUSINESS_DAYS_LABEL = "Business days to alert date"
ESTIMATED_SOURCE = "Estimated, not Bloomberg's"
SOURCE_LABELS = {"BLOOMBERG": "Bloomberg", "ESTIMATED": ESTIMATED_SOURCE, "": "unknown"}
LEVEL_LABELS = {EXPIRED: "Expired", RED: "Red", AMBER: "Amber", GREEN: "Green"}

# Strong for EXPIRED and RED, warm for AMBER, quiet for GREEN.
LEVEL_STYLES: Dict[str, dict] = {
    EXPIRED: {"backgroundColor": "#7f1d1d", "color": "#ffffff", "fontWeight": "700"},
    RED: {"backgroundColor": "#c62828", "color": "#ffffff", "fontWeight": "700"},
    AMBER: {"backgroundColor": "#fff3e0", "color": "#b26a00", "fontWeight": "700"},
    GREEN: {"backgroundColor": "#f1f5f1", "color": "#52705a"},
}

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_EST_STYLE = {"color": "#b26a00", "fontStyle": "italic"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%A} {d.day} {d:%B %Y} ({iso})"


# --------------------------------------------------------------------------- 1. caption
def caption_block(result: Dict[str, Any]) -> html.Div:
    lines = [
        f"As of {_date_words(result.get('as_of'))}.",
        "Business days are counted to each position's alert date on the contract's own exchange calendar, "
        "negative once past. The alert date is the next event (first notice for a physically delivered "
        "contract, else last trade); while a physical contract's dates are estimated it is held early, "
        "at the first business day of the month before the contract month (its Alert basis says so).",
        f"A date marked{EST} is contract-master's estimate, not Bloomberg's: the real date may be earlier.",
    ]
    return html.Div(className="meta-line", children=[html.Span(line) for line in lines])


# --------------------------------------------------------------------------- 2. counts strip
def thresholds_sentence(thresholds: Dict[str, Any]) -> str:
    """The thresholds in force, as the engine reports them."""
    red, amber = thresholds.get(RED), thresholds.get(AMBER)
    return (f"RED: {red if red is not None else NA} business days or fewer to the alert date; "
            f"AMBER: {amber if amber is not None else NA} or fewer; GREEN: further out. "
            "EXPIRED: the event date is past and the position is still open. "
            "A count that could not be made is RED.")


def counts_strip(result: Dict[str, Any]) -> html.Div:
    counts = result.get("counts") or {}
    cards = []
    for level in LEVELS:
        n = counts.get(level, 0)
        style = LEVEL_STYLES[level]
        cards.append(html.Div(
            className="card", id=f"expiries-count-{level.lower()}",
            style={"borderLeft": f"6px solid {style['backgroundColor'] if level != GREEN else '#9bb5a1'}"},
            title=f"{LEVEL_LABELS[level]}: {n} open position{'s' if n != 1 else ''}",
            children=[html.Span(level, className="tag", style={**style, "fontSize": "11px"}),
                      html.Span(str(n), className="card-value",
                                style={"color": style["backgroundColor"]} if level in (EXPIRED, RED) and n else {}),
                      html.Span(f"position{'s' if n != 1 else ''}", className="card-note")]))
    return html.Div(children=[
        html.Div(id=COUNTS_ID, className="cards cards--four", children=cards),
        html.P(thresholds_sentence(result.get("thresholds") or {}), className="section-kicker")])


# --------------------------------------------------------------------------- 3. the table
def _dated(iso: Optional[str], estimated: bool) -> str:
    if not iso:
        return NA
    return f"{iso}{EST if estimated else ''}"


def _delivery(row: Dict[str, Any]) -> str:
    delivery = row.get("delivery") or ""
    assumed = row.get("delivery_assumed") or ""
    if assumed and assumed != delivery:
        return f"{delivery or 'not on file'} (assumed {assumed})"
    return delivery or NA


def schedule_record(row: Dict[str, Any], position: int) -> Tuple[dict, dict]:
    """(record, tooltips) of one table row, the engine's figures as they are: a date or a
    count the engine left empty is "n/a" with the row's reason as its tooltip."""
    reason = row.get("reason") or ""
    est = bool(row.get("estimated"))
    tip: Dict[str, dict] = {}

    def why(col: str) -> None:
        if reason:
            tip[col] = {"value": reason, "type": "text"}

    rec: Dict[str, Any] = {
        "rank": position,
        "level": row.get("level") or NA,
        "contract": row.get("contract_id") or "",
        "name": row.get("name") or "",
        "exchange": row.get("exchange") or "",
        "lots": rk.value(row.get("lots")),
        "next_event": row.get("next_event") or NA,
        "next_event_date": _dated(row.get("next_event_date"), est),
        "last_trade_date": _dated(row.get("last_trade_date"), est),
        "first_notice_date": (row.get("first_notice_date") or ("none on file" if row.get("dates_source") else NA)),
        "alert_date": _dated(row.get("alert_date"), est),
        "alert_basis": row.get("alert_basis") or NA,
        "business_days": rk.value(row.get("business_days")),
        "calendar": (row.get("calendar") or NA) + (BEYOND if row.get("beyond_calendar_coverage") else ""),
        "delivery": _delivery(row),
        "dates_source": SOURCE_LABELS.get(row.get("dates_source") or "", row.get("dates_source") or ""),
        "reason": reason,
    }
    if rec["business_days"] is None:
        rec["business_days"] = NA
    for col in ("contract", "level", "business_days", "calendar", "next_event_date", "alert_date", "alert_basis"):
        why(col)
    if rec["lots"] is None:
        rec["lots"] = NA
    return rec, tip


def _columns() -> List[dict]:
    lots = rk.amount(2, nully="", trim=True)
    return [rk.numeric("#", "rank", rk.count()), rk.text("Level", "level"), rk.text("Contract", "contract"),
            rk.text("Name", "name"), rk.text("Exchange", "exchange"), rk.numeric("Lots", "lots", lots),
            rk.text("Next event", "next_event"), rk.text("Event date", "next_event_date"),
            rk.text("Last trade", "last_trade_date"), rk.text("First notice", "first_notice_date"),
            rk.text("Alert date", "alert_date"), rk.text("Alert basis", "alert_basis"),
            rk.numeric(BUSINESS_DAYS_LABEL, "business_days", rk.count(nully=NA)),
            rk.text("Calendar", "calendar"), rk.text("Delivery", "delivery"),
            rk.text("Dates source", "dates_source"), rk.text("Reason", "reason")]


_HEADER_TIPS = {
    "rank": "The engine's order, worst first: level, then fewest business days. Click to restore it.",
    "business_days": ("Business days from the as-of date to the alert date on the contract's own exchange "
                      "calendar (not to the event when the alert is held early); negative once past."),
    "alert_date": "The date the level is counted to: the next event, or earlier while the dates are estimated.",
    "calendar": "The exchange calendar the count uses; (beyond coverage) = the count reaches past the calendar "
                "file, where only weekends are known closed, so it may be too high.",
    "delivery": "The contract file's delivery method; an unknown one is treated as physical and says so.",
    "dates_source": "Bloomberg's contract dates, or contract-master's estimate (last weekday of the contract month).",
}


def _level_styles() -> List[dict]:
    return [{"if": {"column_id": "level", "filter_query": f"{{level}} = '{lvl}'"}, **style}
            for lvl, style in LEVEL_STYLES.items()]


def schedule_section(result: Dict[str, Any]) -> html.Div:
    rows = result.get("rows") or []
    heading = html.H4("Roll calendar")
    if not rows:
        return html.Div(className="section", children=[
            heading, html.P(result.get("note") or "No open position to show.", className="section-kicker")])
    recs = [schedule_record(r, i) for i, r in enumerate(rows, start=1)]
    columns = _columns()
    left = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=TABLE_ID,
        columns=columns,
        data=[r for r, _ in recs],
        tooltip_data=[t for _, t in recs],
        tooltip_header=_HEADER_TIPS,
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in left]
                               + [{"if": {"column_id": "reason"}, "whiteSpace": "normal",
                                   "minWidth": "320px", "maxWidth": "560px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=_level_styles()
                               + rk.sign_styles(["lots"])
                               + [{"if": {"column_id": c, "filter_query": f'{{{c}}} contains "(est.)"'}, **_EST_STYLE}
                                  for c in ("next_event_date", "last_trade_date", "alert_date")]
                               + [{"if": {"column_id": "dates_source", "filter_query": f"{{dates_source}} = \"{ESTIMATED_SOURCE}\""},
                                   **_EST_STYLE},
                                  {"if": {"column_id": "calendar", "filter_query": '{calendar} contains "beyond coverage"'},
                                   **_EST_STYLE}]
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in ("business_days", "next_event_date", "alert_date", "last_trade_date", "lots")],
        page_action="none",
    )
    caption = ("One row per open commodity futures position, worst first. The level, the dates and the count are "
               "expiry-monitor's; the reason on each row says why, and is on hover of its contract, level and count.")
    return html.Div(className="section", children=[heading, html.P(caption, className="section-kicker"), table])


# --------------------------------------------------------------------------- body and shell
def body(result: Dict[str, Any]) -> html.Div:
    """The whole tab body from one `expiry_schedule` result."""
    return html.Div(className="expiries-body", children=[
        caption_block(result), counts_strip(result), schedule_section(result)])


def render(as_of: Optional[str], db_path) -> Any:
    """The body for `as_of` from the database at `db_path`: one `expiry_schedule` call on a
    read-only connection, closed straight after. A problem is a message where the body would
    be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        result = expiry_schedule(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The roll calendar could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result)


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="expiries-tab", children=[
        html.Div(className="ladder-title-row", children=[
            html.H3("Expiries", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(f"Follows the header's as-of date{f' ({default_date})' if default_date else ''}",
                        className="section-title")])]),
        html.Div(id=BODY_ID, children=[message_box("Loading the roll calendar...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """One callback: the body re-renders on the header's as-of, on every data revision and
    on the safety interval."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path())
