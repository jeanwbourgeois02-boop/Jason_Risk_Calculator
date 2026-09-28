"""Timing & cash tab (the Expiries tab until 2026-09-28): "What happens when?" (Commodity
conversion plan, Phase 1 step 3; rebuilt under the Screens redesign plan, Phase A, 2026-09-25;
the timeline and the cash section added in the UI redesign wave 2, user 2026-09-28). The roll
calendar is rendered from `engine.expiry.expiry_schedule` and nothing else: every date,
business-day count, level and reason there is that dict's; nothing here computes a date, a
count or a level (CLAUDE.md "Tabs as views"). The cash section reads the FX & cash tab's own
sources as they are: the open cash legs and the settled cash of
`engine.ladder.exposure_adapter` (`records_from_db`, `settled_records_from_db`) and the
initial-margin estimate of `engine.limits.margin_estimate`.

Layout, top to bottom (`body`):
  0. `timeline_section` (wave 2): the next 60 business days on the app's calendar
     (`engine.pnl.calendar`, config/holidays.txt) on one horizontal axis, one row per event
     kind (first notice, last trade, option expiry, LME prompt, FX value date), one marker per
     event: the roll calendar's events coloured by their level (EXPIRED / RED / AMBER / GREEN),
     an estimated date as a hollow marker; the cash events (an LME ticket's USD leg on its
     prompt, an FX leg on its value date) in navy, with the lots or the amount at stake on
     hover. Events past the as-of or beyond the window are counted in a line under the chart,
     never dropped silently. A Plotly figure, nothing computed: every x is a date on file.
  1. `counts_strip`: one compact row of level chips (`counts`, worst first: EXPIRED and RED
     strong, AMBER warm, GREEN quiet), each with its threshold on hover, and the as-of.
  2. `schedule_section`: the "Roll calendar" title, its definitions on hover (`about`, with the
     thresholds from `thresholds`), and one single-line row per open position in the engine's
     order (worst first), or the engine's `note` when there is nothing to show. Columns, led by
     what a trader acts on: #, Level, Business days to alert, Alert date, Contract, Name,
     Exchange, Lots, Next event, Event date, Last trade, First notice, (Product, only when more
     than one product is present). Every column fits at 1680 px (`COLUMN_WIDTHS_PX`). What
     used to be wrapped columns is on hover of the row's cells: the reason on Contract, Level
     and the count; the alert basis on Alert date; the calendar and delivery on Exchange; the
     dates' source on each date.
  3. `settled_section`: the engine's `settled_expired`, the contracts the ledger has frozen in
     full, in a collapsed "Expired and settled (N)" section (contract, name, lots, last trade,
     frozen at, reason). They never alert: no level colour, never in the counts. Absent when
     the list is empty.
  4. `cash_section` (wave 2): settled cash to date by currency (`settled_records_from_db`, the
     FX & cash tab's Settled cash row: deliverable legs past their value date at face value, a
     settled future's or option's USD settlement from `realised_pnl`; one the ledger has not
     frozen is named, never valued), the dated cash events (`records_from_db` without the
     settled row: date, currency, amount, what it is), and one line for the initial-margin
     estimate (`engine.limits.margin_estimate`'s book figure, "estimate, not exchange SPAN").
     The full currency-by-date ladder stays on FX & cash (a tab link says so).
  5. `issues_section`: the tab's collapsed "Data issues (N)" drawer: counts the engine could
     not make, estimated dates, counts past a calendar file's coverage, delivery not on file,
     a settled ticket with no realised row, a cash leg the ladder left out. Absent when there
     is nothing to say.

Options on futures (CMDTY_OPTION) and LME prompts (LME_FWD) are rows like any future: an
option's type, strike and its underlying future's event are on hover of its Next event cell;
an LME row's tonnes are on hover of its Lots (a lot count the engine could not make reads
"n/a" with the reason); a date that does not apply to the product (an option's or a prompt's
first notice, a prompt's last trade) reads "—" with why on hover, never "missing".

An estimated date (`estimated`) is always shown as estimated: every date of that row carries
the short marker "(est.)", with the sentence on hover. A row whose count reaches past its
calendar file's coverage (`beyond_calendar_coverage`) has its count in the estimate style, the
sentence on hover of the count and of Exchange, and a line in the drawer. Every table ranks
(`ui.tabs.ranking`): numbers stored as numbers, a missing figure the string "n/a" (ranks last)
with the row's reason as its tooltip. "#" is the engine's own order, so a click on it restores
worst first. The records keep the fields that left the columns (alert basis, calendar,
delivery, dates source, reason) so a hover or a test can read them.

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
from ui.tabs.formatting import about, format_cell, issues_drawer, marker, short_money, tab_link
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "expiries-body"
REFRESH_ID = "expiries-refresh"
COUNTS_ID = "expiries-counts"
TABLE_ID = "expiries-table"
SETTLED_ID = "expiries-settled"
SETTLED_TABLE_ID = "expiries-settled-table"
ISSUES_ID = "expiries-issues"
TIMELINE_ID = "expiries-timeline"
TIMELINE_GRAPH_ID = "expiries-timeline-graph"
CASH_ID = "expiries-cash"
SETTLED_CASH_TABLE_ID = "expiries-settled-cash-table"
CASH_EVENTS_TABLE_ID = "expiries-cash-events-table"
TIMELINE_DAYS = 60             # business days shown, on the app's calendar

NA = "n/a"
EST = " (est.)"
BEYOND = " (beyond coverage)"
BUSINESS_DAYS_LABEL = "Business days to alert"
ESTIMATED_SOURCE = "Estimated, not Bloomberg's"
SOURCE_LABELS = {"BLOOMBERG": "Bloomberg", "ESTIMATED": ESTIMATED_SOURCE, "TICKET": "Ticket's prompt",
                 "": "unknown"}
ESTIMATE_TIP = "Estimated by contract-master, not Bloomberg's date: the real date may be earlier."
SOURCE_TIPS = {"BLOOMBERG": "Bloomberg's contract date.", "TICKET": "The ticket's own prompt date.",
               "ESTIMATED": ESTIMATE_TIP}
LEVEL_LABELS = {EXPIRED: "Expired", RED: "Red", AMBER: "Amber", GREEN: "Green"}
PRODUCT_LABELS = {"FUTURE": "Future", "CMDTY_OPTION": "Option", "LME_FWD": "LME prompt"}
NOT_APPLICABLE = "—"
NOT_APPLICABLE_TO = {"CMDTY_OPTION": "an option", "LME_FWD": "an LME prompt"}

# Strong for EXPIRED and RED, warm for AMBER, quiet for GREEN.
LEVEL_STYLES: Dict[str, dict] = {
    EXPIRED: {"backgroundColor": "#7f1d1d", "color": "#ffffff", "fontWeight": "700"},
    RED: {"backgroundColor": "#c62828", "color": "#ffffff", "fontWeight": "700"},
    AMBER: {"backgroundColor": "#fff3e0", "color": "#b26a00", "fontWeight": "700"},
    GREEN: {"backgroundColor": "#f1f5f1", "color": "#52705a"},
}
_CHIP_BORDER = {EXPIRED: "#7f1d1d", RED: "#c62828", AMBER: "#e0a040", GREEN: "#9bb5a1"}

# Every column's width, in px, so the whole row is in sight at 1680 px (the section's inner
# width there is about 1590 px): the widest content of each column, 12.5 px Inter, plus
# 16 px of padding. A longer name or contract is cut with an ellipsis, in full on hover.
COLUMN_WIDTHS_PX: Dict[str, int] = {
    "rank": 36, "level": 78, "business_days": 86, "alert_date": 134, "contract": 164, "name": 270,
    "exchange": 74, "lots": 60, "next_event": 106, "next_event_date": 134, "last_trade_date": 134,
    "first_notice_date": 134, "product": 92,
}
WIDTH_BUDGET_PX = 1590

_CELL = {"textAlign": "right", "fontVariantNumeric": "tabular-nums", "padding": "4px 8px",
         "whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
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
    return f"{d:%a} {d.day} {d:%b %Y}"


# --------------------------------------------------------------------------- definitions
def thresholds_sentence(thresholds: Dict[str, Any]) -> str:
    """The thresholds in force, as the engine reports them."""
    red, amber = thresholds.get(RED), thresholds.get(AMBER)
    return (f"RED: {red if red is not None else NA} business days or fewer to the alert date; "
            f"AMBER: {amber if amber is not None else NA} or fewer; GREEN: further out. "
            "EXPIRED: the event date is past and the position is still open. "
            "A count that could not be made is RED.")


def definitions(result: Dict[str, Any]) -> str:
    """The roll calendar's definitions, shown on hover of its title (never a paragraph)."""
    return " ".join([
        "One row per open commodity position (future, option on a future or LME prompt), worst first; "
        "the level, the dates and the count are expiry-monitor's.",
        "Business days are counted to each position's alert date on the contract's own exchange calendar, "
        "negative once past. The alert date is the next event (first notice for a physically delivered "
        "contract, else last trade); while a physical contract's dates are estimated it is held early, "
        "at the first business day of the month before the contract month.",
        f"A date marked{EST} is contract-master's estimate, not Bloomberg's: the real date may be earlier.",
        "An option on a future alerts on its own expiry; an LME prompt alerts from the day it becomes the "
        "cash date (the prompt less 2 LME business days), and its date is the ticket's own.",
        thresholds_sentence(result.get("thresholds") or {}),
        "Hover a row's contract, level or count for its reason, its alert date for the alert basis, "
        "its exchange for the calendar and delivery.",
    ])


# --------------------------------------------------------------------------- 1. level chips
def _chip_tip(level: str, n: int, thresholds: Dict[str, Any]) -> str:
    what = {EXPIRED: "event date past, still open",
            RED: f"{thresholds.get(RED, NA)} business days or fewer to the alert date",
            AMBER: f"{thresholds.get(AMBER, NA)} business days or fewer to the alert date",
            GREEN: "further out"}[level]
    return f"{LEVEL_LABELS[level]}: {n} open position{'s' if n != 1 else ''} ({what})."


def counts_strip(result: Dict[str, Any]) -> html.Div:
    """One compact row of chips, one per level (label, count), each with its threshold on
    hover, and the as-of."""
    counts = result.get("counts") or {}
    thresholds = result.get("thresholds") or {}
    chips = []
    for level in LEVELS:
        n = counts.get(level, 0)
        strong = level in (EXPIRED, RED) and n
        chips.append(html.Span(
            id=f"expiries-count-{level.lower()}", title=_chip_tip(level, n, thresholds),
            style={"display": "inline-flex", "alignItems": "center", "gap": "6px", "padding": "2px 10px 2px 3px",
                   "border": f"1px solid {_CHIP_BORDER[level]}", "borderRadius": "12px", "background": "#fff",
                   "fontSize": "12px", "cursor": "help"},
            children=[html.Span(level, style={**LEVEL_STYLES[level], "fontSize": "10px", "borderRadius": "10px",
                                              "padding": "1px 7px", "letterSpacing": ".04em"}),
                      html.Span(str(n), style={"fontWeight": "700", "fontVariantNumeric": "tabular-nums",
                                               **({"color": LEVEL_STYLES[level]["backgroundColor"]} if strong else {})})]))
    return html.Div(style={"display": "flex", "flexWrap": "wrap", "alignItems": "center", "gap": "8px",
                           "margin": "0 0 10px"}, children=[
        html.Div(id=COUNTS_ID, style={"display": "flex", "flexWrap": "wrap", "gap": "6px"}, children=chips),
        html.Span(f"As of {_date_words(result.get('as_of'))}", className="meta-line",
                  style={"margin": "0 0 0 8px"}, title=result.get("as_of") or "")])


# --------------------------------------------------------------------------- 2. the table
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


def _number(v) -> str:
    """A figure as the engine gave it, for a hover: thousands separated, no trailing zeros."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v) if v not in (None, "") else NA
    return f"{f:,.0f}" if f.is_integer() else f"{f:,.4f}".rstrip("0")


def _tonnes_tip(row: Dict[str, Any]) -> str:
    """The tonnes of an LME row, as the engine gives them."""
    tonnes = row.get("tonnes")
    return f"{_number(tonnes)} tonnes" if tonnes is not None else "tonnes not on file"


def _option_tip(row: Dict[str, Any]) -> str:
    """An option row's type, strike, style and its underlying future's event."""
    kind = (row.get("option_type") or "").title() or "Option"
    style = (row.get("style") or "").title()
    head = f"{kind}, strike {_number(row.get('strike'))}" + (f", {style}" if style else "")
    und = row.get("underlying_id") or "underlying not on file"
    event = row.get("underlying_event")
    if event:
        when = _dated(row.get("underlying_event_date"), bool(row.get("underlying_estimated")))
        tail = f"underlying {und}: {event} {when}"
    else:
        tail = f"underlying {und}"
    return f"{head}; {tail}."


def _not_applicable(row: Dict[str, Any], what: str) -> Optional[str]:
    """The hover of a date that does not apply to this row's product; None when it applies
    (first notice: not to an option or a prompt; last trade: not to a prompt)."""
    product = row.get("product") or ""
    who = NOT_APPLICABLE_TO.get(product)
    if who is None or (what == "last trade" and product != "LME_FWD"):
        return None
    extra = ": the prompt date is the event" if product == "LME_FWD" else ""
    return f"{what.capitalize()} is not applicable to {who}{extra}."


def _coverage_sentence(row: Dict[str, Any]) -> str:
    cal = row.get("calendar") or "its"
    return (f"The count reaches past the {cal} calendar file's coverage, where only weekends are known "
            "closed, so it may be too high.")


def _date_tip(row: Dict[str, Any]) -> str:
    """Where a row's dates come from: the (est.) marker's sentence for an estimate."""
    if row.get("estimated"):
        return ESTIMATE_TIP
    return SOURCE_TIPS.get(row.get("dates_source") or "", "Source of the date not on file.")


def schedule_record(row: Dict[str, Any], position: int) -> Tuple[dict, dict]:
    """(record, tooltips) of one table row, the engine's figures as they are: a date or a
    count the engine left empty is "n/a" with the row's reason as its tooltip, or "—"
    with why on hover when it does not apply to the product. The fields that are not
    columns (alert basis, calendar, delivery, dates source, reason) stay in the record and
    are read on hover."""
    reason = row.get("reason") or ""
    est = bool(row.get("estimated"))
    beyond = bool(row.get("beyond_calendar_coverage"))
    tip: Dict[str, dict] = {}

    def hover(col: str, text: str) -> None:
        if text:
            tip[col] = {"value": text, "type": "text"}

    rec: Dict[str, Any] = {
        "rank": position,
        "level": row.get("level") or NA,
        "product": product_label(row.get("product")),
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
        "calendar": (row.get("calendar") or NA) + (BEYOND if beyond else ""),
        "delivery": _delivery(row),
        "dates_source": SOURCE_LABELS.get(row.get("dates_source") or "", row.get("dates_source") or ""),
        "reason": reason,
    }
    if rec["business_days"] is None:
        rec["business_days"] = NA

    # the reason: on the contract, the level and the count
    for col in ("contract", "level"):
        hover(col, reason)
    hover("business_days", " ".join(t for t in (reason, _coverage_sentence(row) if beyond else "") if t))
    # the alert basis on the alert date; each date's source on the date (the est. sentence)
    if rec["alert_date"] == NA:
        hover("alert_date", reason)
    else:
        basis = row.get("alert_basis") or ""
        hover("alert_date", " ".join(t for t in (f"Alert basis: {basis}." if basis else "",
                                                  ESTIMATE_TIP if est else "") if t))
    for col in ("next_event_date", "last_trade_date"):
        hover(col, reason if rec[col] == NA else _date_tip(row))
    if rec["first_notice_date"] == NA:
        hover("first_notice_date", reason)
    elif rec["first_notice_date"] == "none on file":
        hover("first_notice_date", f"No first notice date on file ({rec['dates_source']}).")
    else:
        hover("first_notice_date", _date_tip(row))
    for col, what in (("first_notice_date", "first notice"), ("last_trade_date", "last trade")):
        na_text = _not_applicable(row, what)
        if na_text and not row.get(col):
            rec[col] = NOT_APPLICABLE
            hover(col, na_text)
    # the calendar and delivery on the exchange; the full name on the name (it may be cut)
    hover("exchange", f"Calendar: {rec['calendar']}. Delivery: {rec['delivery']}. Dates: {rec['dates_source']}."
                      + (f" {_coverage_sentence(row)}" if beyond else ""))
    hover("name", " ".join(t for t in (rec["name"], f"({row['sector']})" if row.get("sector") else "") if t))
    if rec["lots"] is None:
        rec["lots"] = NA
        hover("lots", reason)
    if row.get("product") == "LME_FWD" or "tonnes" in row:
        hover("lots", _tonnes_tip(row) + (f". {reason}" if rec["lots"] == NA and reason else ""))
    if row.get("product") == "CMDTY_OPTION":
        hover("next_event", _option_tip(row))
    return rec, tip


def product_label(product: Optional[str]) -> str:
    """The product as the tab names it: "Future", "Option", "LME prompt"; anything else as given."""
    return PRODUCT_LABELS.get(product or "", product or NA)


def show_product(rows: List[Dict[str, Any]]) -> bool:
    """The Product column is shown only when the rows hold more than one product."""
    return len({r.get("product") or "" for r in rows}) > 1


def _columns(with_product: bool = False) -> List[dict]:
    """The visible columns, led by what a trader acts on; Product last, only when mixed."""
    lots = rk.amount(2, nully="", trim=True)
    product = [rk.text("Product", "product")] if with_product else []
    return [rk.numeric("#", "rank", rk.count()), rk.text("Level", "level"),
            rk.numeric(BUSINESS_DAYS_LABEL, "business_days", rk.count(nully=NA)),
            rk.text("Alert date", "alert_date"), rk.text("Contract", "contract"),
            rk.text("Name", "name"), rk.text("Exchange", "exchange"), rk.numeric("Lots", "lots", lots),
            rk.text("Next event", "next_event"), rk.text("Event date", "next_event_date"),
            rk.text("Last trade", "last_trade_date"), rk.text("First notice", "first_notice_date"),
            *product]


def _width_rules(columns: List[dict]) -> List[dict]:
    rules = []
    for col in columns:
        px = f"{COLUMN_WIDTHS_PX[col['id']]}px"
        rules.append({"if": {"column_id": col["id"]}, "width": px, "minWidth": px, "maxWidth": px})
    return rules


_HEADER_TIPS = {
    "rank": "The engine's order, worst first: level, then fewest business days. Click to restore it.",
    "level": "EXPIRED, RED, AMBER or GREEN, by the business days to the alert date; the reason on hover of a cell.",
    "business_days": ("Business days from the as-of date to the alert date on the contract's own exchange "
                      "calendar (not to the event when the alert is held early); negative once past."),
    "alert_date": "The date the level is counted to: the next event, or earlier while the dates are estimated "
                  "(the alert basis on hover of a cell).",
    "exchange": "The calendar the count uses, the delivery method and the dates' source are on hover of a cell.",
    "next_event_date": f"A date marked{EST} is contract-master's estimate, not Bloomberg's.",
}


def _level_styles() -> List[dict]:
    return [{"if": {"column_id": "level", "filter_query": f"{{level}} = '{lvl}'"}, **style}
            for lvl, style in LEVEL_STYLES.items()]


def schedule_section(result: Dict[str, Any]) -> html.Div:
    rows = result.get("rows") or []
    heading = about("Roll calendar", definitions(result))
    if not rows:
        return html.Div(className="section", children=[
            heading, html.P(result.get("note") or "No open position to show.", className="section-kicker")])
    recs = [schedule_record(r, i) for i, r in enumerate(rows, start=1)]
    columns = _columns(show_product(rows))
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
        style_cell=_CELL,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in left]
                               + _width_rules(columns),
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=_level_styles()
                               + rk.sign_styles(["lots"])
                               + [{"if": {"column_id": c, "filter_query": f'{{{c}}} contains "(est.)"'}, **_EST_STYLE}
                                  for c in ("next_event_date", "last_trade_date", "alert_date")]
                               + [{"if": {"column_id": "business_days",
                                          "filter_query": '{calendar} contains "beyond coverage"'}, **_EST_STYLE}]
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in ("business_days", "next_event_date", "alert_date", "last_trade_date",
                                            "first_notice_date", "lots")]
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NOT_APPLICABLE}'"},
                                   "color": "var(--muted)"} for c in ("first_notice_date", "last_trade_date")],
        page_action="none",
    )
    return html.Div(className="section", children=[heading, table])


# --------------------------------------------------------------------------- 3. expired and settled
def settled_record(entry: Dict[str, Any]) -> Tuple[dict, dict]:
    """(record, tooltips) of one settled contract, the engine's entry as it is."""
    reason = entry.get("reason") or ""
    lots = rk.value(entry.get("lots"))
    tip: Dict[str, dict] = {"contract": {"value": reason, "type": "text"}} if reason else {}
    if entry.get("product") == "LME_FWD" or "tonnes" in entry:
        tip["lots"] = {"value": _tonnes_tip(entry), "type": "text"}
        tip["last_trade_date"] = {"value": "The LME prompt date.", "type": "text"}
    elif entry.get("estimated"):
        tip["last_trade_date"] = {"value": ESTIMATE_TIP, "type": "text"}
    if reason:
        tip["reason"] = {"value": reason, "type": "text"}
    rec = {
        "contract": entry.get("contract_id") or "",
        "name": entry.get("name") or "",
        "lots": NA if lots is None else lots,
        "last_trade_date": _dated(entry.get("last_trade_date"), bool(entry.get("estimated"))),
        "frozen_at": entry.get("frozen_at") or NA,
        "reason": reason,
    }
    return rec, tip


def settled_section(result: Dict[str, Any]) -> Optional[html.Details]:
    """The contracts the ledger has frozen in full, collapsed below the roll calendar; None
    when there are none. No level and no colour: they never alert and are not counted."""
    entries = result.get("settled_expired") or []
    if not entries:
        return None
    recs = [settled_record(e) for e in entries]
    any_prompt = any(e.get("product") == "LME_FWD" or "tonnes" in e for e in entries)
    columns = [rk.text("Contract", "contract"), rk.text("Name", "name"),
               rk.numeric("Lots", "lots", rk.amount(2, nully="", trim=True)),
               rk.text("Last trade / prompt" if any_prompt else "Last trade", "last_trade_date"),
               rk.text("Frozen at", "frozen_at"), rk.text("Reason", "reason")]
    left = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=SETTLED_TABLE_ID,
        columns=columns,
        data=[r for r, _ in recs],
        tooltip_data=[t for _, t in recs],
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(SETTLED_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_CELL,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in left]
                               + [{"if": {"column_id": "name"}, "maxWidth": "270px"},
                                  {"if": {"column_id": "reason"}, "maxWidth": "620px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=[{"if": {"column_id": "last_trade_date", "filter_query": '{last_trade_date} contains "(est.)"'},
                                 **_EST_STYLE}]
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in ("last_trade_date", "frozen_at", "lots")],
        page_action="none",
    )
    note = ("Contracts past their last trade whose every trade the ledger has frozen: they have left the book, "
            "raise no alert and are not in the counts. Listed here so none disappears unseen.")
    return html.Details(id=SETTLED_ID, className="section", open=False, children=[
        html.Summary(f"Expired and settled ({len(entries)})", title=note), table])


# --------------------------------------------------------------------------- 4. data issues
def issues(result: Dict[str, Any]) -> List[Any]:
    """The tab's reasons, for the one "Data issues" drawer: a count the engine could not make
    (the row's reason), the positions on estimated dates (one line), a count past its
    calendar file's coverage, a delivery method not on file. Nothing is recounted: each line
    is a row's own field."""
    rows = result.get("rows") or []
    out: List[Any] = []
    for r in rows:
        if r.get("business_days") is None:
            out.append((r.get("contract_id") or "", r.get("reason") or "No count could be made."))
    estimated = [r.get("contract_id") or "" for r in rows
                 if r.get("estimated") and r.get("business_days") is not None]
    if estimated:
        out.append(("Estimated dates",
                    f"{len(estimated)} of {len(rows)} open position{'s' if len(rows) != 1 else ''} "
                    "are dated by contract-master's estimate, not Bloomberg's (the real date may be earlier; a "
                    "physical contract's alert is held early): " + ", ".join(estimated) + "."))
    for r in rows:
        if r.get("beyond_calendar_coverage"):
            out.append((r.get("contract_id") or "", _coverage_sentence(r)))
    for r in rows:
        if not r.get("delivery") and r.get("delivery_assumed") and r.get("business_days") is not None:
            out.append((r.get("contract_id") or "",
                        f"Delivery method not on file: treated as {r['delivery_assumed']}."))
    return out


def issues_section(result: Dict[str, Any]):
    """The collapsed "Data issues (N)" drawer, or None when there is nothing to say."""
    return issues_drawer(issues(result), id=ISSUES_ID)


# --------------------------------------------------------------------------- 0. the timeline
EVENT_KINDS = ("first notice", "last trade", "option expiry", "LME prompt", "FX value date")
EVENT_ROW_LABELS = {"first notice": "First notice", "last trade": "Last trade", "option expiry": "Option expiry",
                    "LME prompt": "LME prompt (cash)", "FX value date": "FX value date"}
_LEVEL_COLOURS = {EXPIRED: "#7f1d1d", RED: "#c62828", AMBER: "#e0a040", GREEN: "#52705a"}
_CASH_COLOUR = "#0f1f3d"
TIMELINE_ABOUT = (
    f"The next {TIMELINE_DAYS} business days on the app's calendar (config/holidays.txt), one row per kind of "
    "event. The roll calendar's events (first notice, last trade, option expiry, an LME prompt's cash date) "
    "are coloured by their level (EXPIRED dark red, RED, AMBER, GREEN); a hollow marker is a date "
    "contract-master estimated, not Bloomberg's. The cash events (an LME ticket's USD leg on its prompt, an "
    "FX leg on its value date) are navy, from the FX & cash tab's own legs. Hover a marker for the contract, "
    "the lots or the amount. Events past the as-of or beyond the window are counted under the chart. Nothing "
    "is computed: every date is one on file.")


def business_days_ahead(as_of: str, n: int = TIMELINE_DAYS) -> List[dt.date]:
    """`as_of` and the next `n` business days on the app's calendar (`engine.pnl.calendar`)."""
    from engine.pnl.calendar import _is_business_day, load_holidays
    holidays = load_holidays()
    d = dt.date.fromisoformat(as_of)
    days = [d]
    while len(days) < n + 1:
        d += dt.timedelta(days=1)
        if _is_business_day(d, holidays):
            days.append(d)
    return days


def schedule_events(result: Dict[str, Any]) -> List[dict]:
    """The roll calendar's rows as timeline events: kind (the engine's `next_event`), date,
    level, estimated, label, hover. A row with no event date is left out here and is in the
    Data issues drawer (its count could not be made either)."""
    events = []
    for r in result.get("rows") or []:
        date = r.get("next_event_date")
        if not date:
            continue
        lots = r.get("lots")
        stake = (f"{_number(r.get('tonnes'))} t" if r.get("product") == "LME_FWD" and r.get("tonnes") is not None
                 else f"{_number(lots)} lots" if lots is not None else "lots n/a")
        alert = r.get("alert_date")
        held = f"; alert {alert} ({r.get('alert_basis')})" if alert and alert != date else ""
        events.append({"kind": r.get("next_event") or "last trade", "date": date, "level": r.get("level") or GREEN,
                       "estimated": bool(r.get("estimated")), "label": r.get("contract_id") or "",
                       "hover": f"{r.get('contract_id') or ''} ({r.get('name') or ''}): {r.get('next_event')} {date}"
                                f"{EST if r.get('estimated') else ''}, {stake}, {r.get('level')}{held}"})
    return events


def cash_events(records: List[dict]) -> List[dict]:
    """The open cash legs (`records_from_db` without the settled row) as timeline events: an
    LME ticket's USD leg on its prompt, an FX leg on its value date, with the amount."""
    events = []
    for rec in records:
        date = str(rec.get("settlement_date") or "")
        if not date or date == "settled" or not rec.get("settles_cash", 1):
            continue
        product = str(rec.get("product_type") or "")
        kind = "LME prompt" if product == "LME_FWD" else "FX value date"
        amount = float(rec.get("local_amount") or 0.0)
        ccy = str(rec.get("currency") or "")
        events.append({"kind": kind, "date": date, "level": None, "estimated": False,
                       "label": f"{rec.get('trade_id')} {ccy}", "currency": ccy, "amount": amount,
                       "trade_id": str(rec.get("trade_id") or ""), "product": product,
                       "pair": str(rec.get("currency_pair") or ""),
                       "hover": f"{rec.get('trade_id')} {rec.get('currency_pair') or ''}: {kind} {date}, "
                                f"{'receive' if amount >= 0 else 'pay'} {ccy} {format_cell(abs(amount))}"})
    return events


def timeline_figure(as_of: str, events: List[dict], days: List[dt.date]):
    """One figure: the event kinds as rows, the window's business days across, one marker per
    event, the level as colour, an estimate hollow, cash in navy; events outside the window
    are not drawn (`timeline_notes` counts them)."""
    import plotly.graph_objects as go
    start, end = days[0], days[-1]
    fig = go.Figure()
    rows_present = [k for k in EVENT_KINDS if any(e["kind"] == k for e in events)] or list(EVENT_KINDS[:1])
    for kind in rows_present:
        mine = [e for e in events if e["kind"] == kind and start <= dt.date.fromisoformat(e["date"]) <= end]
        if not mine:
            continue
        fig.add_trace(go.Scatter(
            x=[e["date"] for e in mine], y=[EVENT_ROW_LABELS[kind]] * len(mine), mode="markers", name=EVENT_ROW_LABELS[kind],
            marker={"size": 13, "line": {"width": 2, "color": [_LEVEL_COLOURS.get(e["level"], _CASH_COLOUR) for e in mine]},
                    "color": ["rgba(255,255,255,1)" if e["estimated"] else _LEVEL_COLOURS.get(e["level"], _CASH_COLOUR)
                              for e in mine],
                    "symbol": ["diamond" if e["level"] is None else "circle" for e in mine]},
            text=[e["hover"] for e in mine], hovertemplate="%{text}<extra></extra>", showlegend=False))
    fig.add_vline(x=as_of, line={"color": "#c9a227", "width": 1.5, "dash": "dot"})
    fig.update_layout(
        height=60 + 34 * max(len(rows_present), 2), margin={"l": 8, "r": 16, "t": 8, "b": 28},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font={"size": 11.5, "color": "#1b2333"},
        hovermode="closest")
    fig.update_xaxes(type="date", range=[(start - dt.timedelta(days=1)).isoformat(), (end + dt.timedelta(days=1)).isoformat()],
                     tickformat="%d %b", showgrid=True, gridcolor="#eef0f4", fixedrange=True,
                     tickfont={"size": 10.5, "color": "#6b7280"})
    fig.update_yaxes(type="category", categoryorder="array",
                     categoryarray=[EVENT_ROW_LABELS[k] for k in reversed(rows_present)], fixedrange=True,
                     showgrid=True, gridcolor="#eef0f4", tickfont={"size": 11})
    return fig


def timeline_notes(as_of: str, events: List[dict], days: List[dt.date]) -> List[str]:
    """What the chart does not show: events past the as-of (with their level) and beyond the window."""
    start, end = days[0], days[-1]
    past = [e for e in events if dt.date.fromisoformat(e["date"]) < start]
    beyond = [e for e in events if dt.date.fromisoformat(e["date"]) > end]
    notes = []
    if past:
        notes.append(f"{len(past)} event(s) before {as_of}, not drawn: "
                     + "; ".join(f"{e['label']} {e['kind']} {e['date']}" + (f" ({e['level']})" if e.get("level") else "")
                                 for e in past[:8]) + (f"; and {len(past) - 8} more" if len(past) > 8 else "") + ".")
    if beyond:
        notes.append(f"{len(beyond)} event(s) beyond {end.isoformat()} ({TIMELINE_DAYS} business days), not drawn: "
                     + "; ".join(f"{e['label']} {e['kind']} {e['date']}" for e in beyond[:8])
                     + (f"; and {len(beyond) - 8} more" if len(beyond) > 8 else "") + ".")
    if not events:
        notes.append("No expiry, prompt or value date on file for the open positions.")
    return notes


def timeline_section(as_of: str, result: Dict[str, Any], cash_records: List[dict]) -> html.Div:
    days = business_days_ahead(as_of)
    events = schedule_events(result) + cash_events(cash_records)
    return html.Div(id=TIMELINE_ID, className="section", children=[
        about(f"Next {TIMELINE_DAYS} business days", TIMELINE_ABOUT),
        dcc.Graph(id=TIMELINE_GRAPH_ID, figure=timeline_figure(as_of, events, days), config={"displayModeBar": False}),
        *[html.P(line, className="section-kicker") for line in timeline_notes(as_of, events, days)]])


# --------------------------------------------------------------------------- 4. cash
CASH_ABOUT = (
    "Settled cash to date by currency: the FX & cash tab's own Settled cash row (engine.ladder.exposure_adapter): "
    "deliverable FX legs past their value date at face value in their own currency, an LME ticket's USD leg "
    "after its prompt, and a settled future's or option's USD settlement as the ledger froze it in "
    "realised_pnl, never recomputed; a settled ticket the ledger has not frozen is named, not valued. This is "
    "cash from the tickets on file, not a bank balance. Then the dated cash events still to come, and the "
    "initial-margin estimate. The full currency-by-date ladder is on FX & cash.")
MARGIN_ABOUT = ("The book's estimated initial margin (engine/limits, config/limits.yaml's placeholder rates with "
                "spread credits): an estimate, not exchange SPAN, never the exchange's or the clearer's figure.")


def settled_cash_records(records: List[dict], unresolved: List[Any]) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips): the settled cash summed per currency (known figures added up, the
    tickets on hover), USD first then by |amount|; a ticket the ledger has not frozen is not in
    any figure and is named on the drawer (`cash_issue_items`)."""
    by_ccy: Dict[str, List[dict]] = {}
    for rec in records:
        by_ccy.setdefault(str(rec.get("currency") or ""), []).append(rec)
    out, tips = [], []
    for ccy, mine in by_ccy.items():
        total = float(sum(float(r.get("local_amount") or 0.0) for r in mine))
        lines = [f"{r.get('trade_id')} {r.get('currency_pair') or ''} settled {r.get('settled_on') or ''}: "
                 f"{format_cell(float(r.get('local_amount') or 0.0))}" for r in mine]
        out.append({"currency": ccy, "amount": total, "tickets": len(mine)})
        tips.append({"amount": {"value": f"{format_cell(total)} {ccy}; " + "; ".join(lines[:12])
                                         + (f"; and {len(lines) - 12} more" if len(lines) > 12 else ""), "type": "text"}})
    order = sorted(range(len(out)), key=lambda i: (out[i]["currency"] != "USD", -abs(out[i]["amount"])))
    return [out[i] for i in order], [tips[i] for i in order]


def cash_event_records(events: List[dict]) -> Tuple[List[dict], List[dict]]:
    """The dated cash events (`cash_events`) as table rows, soonest first."""
    rows = sorted(events, key=lambda e: (e["date"], e["currency"]))
    records = [{"date": e["date"], "currency": e["currency"], "amount": e["amount"], "what": e["kind"],
                "trade_id": e["trade_id"], "pair": e["pair"]} for e in rows]
    tips = [{"amount": {"value": e["hover"], "type": "text"}} for e in rows]
    return records, tips


def margin_line(margin: Optional[Dict[str, Any]], error: str = "") -> html.Div:
    """One line: the book's estimated initial margin in k / m with its basis on hover, or n/a
    with the reason."""
    if error:
        return html.Div(className="meta-line", children=[html.Span("Initial margin (estimate): "), marker(NA, error)])
    book = (margin or {}).get("book") or {}
    value = book.get("margin_usd")
    if not (margin or {}).get("available") or value is None:
        why = "; ".join((margin or {}).get("reasons") or []) or book.get("reason") or "no margin figure"
        return html.Div(className="meta-line", children=[html.Span("Initial margin (estimate): "), marker(NA, why)])
    hover = (f"{format_cell(value)} USD = gross charge {format_cell(book.get('gross_charge_usd') or 0)} less spread "
             f"credits {format_cell(book.get('spread_credit_usd') or 0)}; {book.get('basis') or MARGIN_ABOUT}")
    parts = [html.Span("Initial margin (estimate, not exchange SPAN): ", title=MARGIN_ABOUT),
             html.Span(short_money(value, "$"), title=hover, style={"fontWeight": 700})]
    if book.get("caption"):
        parts.append(marker(f"excl. {book.get('excluded_count') or ''}".strip(), f"{book['caption']}: {book.get('reason') or ''}"))
    return html.Div(className="meta-line", children=parts)


def cash_section(settled: List[dict], settled_unresolved: List[Any], cash_records: List[dict],
                 margin: Optional[Dict[str, Any]], margin_error: str = "") -> html.Div:
    heading = about("Cash", CASH_ABOUT)
    children: List[Any] = [heading]
    records, tips = settled_cash_records(settled, settled_unresolved)
    if records:
        children.append(html.H5("Settled cash to date", className="about-title", title=CASH_ABOUT))
        children.append(dash_table.DataTable(
            id=SETTLED_CASH_TABLE_ID,
            columns=[rk.text("Currency", "currency"), rk.numeric("Amount", "amount", rk.amount(nully=NA)),
                     rk.numeric("Tickets", "tickets", rk.count())],
            data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
            **rk.sortable(SETTLED_CASH_TABLE_ID),
            style_table={"overflowX": "auto", "maxWidth": "520px"}, style_cell=_CELL,
            style_cell_conditional=[{"if": {"column_id": "currency"}, "textAlign": "left"}],
            style_header={"fontWeight": "bold"},
            style_data_conditional=rk.sign_styles(["amount"], bold=True)))
    else:
        children.append(html.P("No settled cash yet from the tickets on file.", className="section-kicker"))
    if settled_unresolved:
        children.append(html.P(f"{len(settled_unresolved)} settled ticket(s) not in the figures: "
                               + "; ".join(f"{u.trade_id} {u.symbol}: {u.reason}" for u in settled_unresolved[:4])
                               + (f"; and {len(settled_unresolved) - 4} more" if len(settled_unresolved) > 4 else ""),
                               className="section-kicker"))
    events = cash_events(cash_records)
    ev_records, ev_tips = cash_event_records(events)
    if ev_records:
        children.append(html.H5("Cash to come", className="about-title",
                                title="Each open cash leg on its own date: an LME ticket's USD leg on its prompt, an FX "
                                      "leg on its value date (+ = receive). Soonest first."))
        children.append(dash_table.DataTable(
            id=CASH_EVENTS_TABLE_ID,
            columns=[rk.text("Date", "date"), rk.text("Currency", "currency"),
                     rk.numeric("Amount", "amount", rk.amount(nully=NA)), rk.text("What", "what"),
                     rk.text("Trade", "trade_id"), rk.text("Pair / metal", "pair")],
            data=ev_records, tooltip_data=ev_tips, tooltip_delay=0, tooltip_duration=None,
            **rk.sortable(CASH_EVENTS_TABLE_ID),
            page_action="native", page_size=15,
            style_table={"overflowX": "auto", "maxWidth": "900px"}, style_cell=_CELL,
            style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                    for c in ("date", "currency", "what", "trade_id", "pair")],
            style_header={"fontWeight": "bold"},
            style_data_conditional=rk.sign_styles(["amount"])))
    else:
        children.append(html.P("No cash leg still to settle on the tickets on file.", className="section-kicker"))
    children.append(margin_line(margin, margin_error))
    children.append(html.Div(className="meta-line", children=[
        html.Span("The full currency-by-date ladder, the USD equivalents and the FX stress are on "),
        tab_link("FX & cash", "ladder", "expiries-cash-ladder"), html.Span(".")]))
    return html.Div(id=CASH_ID, className="section", children=children)


def cash_issue_items(settled_unresolved: List[Any], cash_unresolved: List[Any]) -> List[Any]:
    """The drawer's cash lines: a settled ticket with no realised row, a leg the ladder left out."""
    out: List[Any] = []
    for u in settled_unresolved:
        out.append((getattr(u, "trade_id", ""), f"{getattr(u, 'symbol', '')}: {getattr(u, 'reason', '')}"))
    for u in cash_unresolved:
        reason = str(getattr(u, "reason", ""))
        if "non-FX product" in reason:
            continue   # a future: it has no cash leg, and it is on the roll calendar above
        out.append((getattr(u, "trade_id", ""), f"{getattr(u, 'symbol', '')}: {reason}"))
    return out


# --------------------------------------------------------------------------- body and shell
def _safe(build: Callable[[], Any], label: str) -> Any:
    """A section, or its failure as one line with the reason, so one section never blanks the tab."""
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="section", children=[
            about(label, None), marker(NA, f"{label} could not be built ({type(exc).__name__}: {exc})")])


def body(result: Dict[str, Any], cash: Optional[Dict[str, Any]] = None) -> html.Div:
    """The whole tab body from one `expiry_schedule` result and the cash reads (`cash_inputs`;
    None renders the roll calendar alone, the old shape)."""
    cash = cash or {}
    as_of = result.get("as_of") or ""
    parts: List[Any] = []
    if cash:
        parts.append(_safe(lambda: timeline_section(as_of, result, cash.get("records") or []), "Timeline"))
    parts += [counts_strip(result), schedule_section(result)]
    settled = settled_section(result)
    if settled is not None:
        parts.append(settled)
    if cash:
        parts.append(_safe(lambda: cash_section(cash.get("settled") or [], cash.get("settled_unresolved") or [],
                                                cash.get("records") or [], cash.get("margin"), cash.get("margin_error", "")),
                           "Cash"))
    items = issues(result) + cash_issue_items(cash.get("settled_unresolved") or [], cash.get("unresolved") or [])
    drawer = issues_drawer(items, id=ISSUES_ID)
    if drawer is not None:
        parts.append(drawer)
    return html.Div(className="expiries-body", children=parts)


def cash_inputs(conn: sqlite3.Connection, as_of: str) -> Dict[str, Any]:
    """The cash reads for `as_of`, each as its owner gives it: the open cash legs and their
    unresolved list (`records_from_db`, no settled row), the settled cash records and the
    tickets not frozen yet (`settled_records_from_db`), and the margin estimate (or why not)."""
    from engine.ladder.exposure_adapter import records_from_db, settled_records_from_db
    out: Dict[str, Any] = {"records": [], "unresolved": [], "settled": [], "settled_unresolved": [],
                           "margin": None, "margin_error": ""}
    out["records"], out["unresolved"] = records_from_db(conn, as_of, include_settled=False)
    out["settled"], out["settled_unresolved"] = settled_records_from_db(conn, as_of)
    try:
        from engine.limits import margin_estimate
        out["margin"] = margin_estimate(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on the margin line
        out["margin_error"] = f"the margin estimate could not be read ({type(exc).__name__}: {exc})"
    return out


def render(as_of: Optional[str], db_path) -> Any:
    """The body for `as_of` from the database at `db_path`: one `expiry_schedule` call and the
    cash reads on a read-only connection, closed straight after. A problem is a message where
    the body would be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        result = expiry_schedule(conn, as_of)
        try:
            cash = cash_inputs(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- the roll calendar still renders, the cash says why
            cash = {"margin_error": f"the cash legs could not be read ({type(exc).__name__}: {exc})"}
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The roll calendar could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result, cash)


_TAB_ABOUT = ("What happens when: the next 60 business days of expiries, prompts and value dates on one line, "
              "the roll calendar (every open future, option on a future and LME prompt, its next first notice, "
              "last trade, option expiry or prompt, the business days to its alert date and its level, as "
              "expiry-monitor gives them; a date marked (est.) is an estimate, not Bloomberg's), and the cash: "
              "settled to date, still to come, and the initial-margin estimate.")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="expiries-tab", children=[
        html.Div(className="ladder-title-row", children=[
            about("Timing & cash", _TAB_ABOUT, level="h3", className="ladder-title-row-heading"),
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
