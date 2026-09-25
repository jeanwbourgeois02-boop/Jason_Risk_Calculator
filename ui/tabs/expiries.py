"""Expiries tab: "What is about to expire or go to delivery?" (Commodity conversion plan,
Phase 1 step 3; rebuilt under the Screens redesign plan, Phase A, 2026-09-25). Rendered from
`engine.expiry.expiry_schedule` and nothing else: every date, business-day count, level and
reason on the tab is that dict's; nothing here computes a date, a count or a level (CLAUDE.md
"Tabs as views").

Layout, top to bottom (`body`):
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
  4. `issues_section`: the tab's collapsed "Data issues (N)" drawer: counts the engine could
     not make, estimated dates, counts past a calendar file's coverage, delivery not on file.
     Absent when there is nothing to say.

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
from ui.tabs.formatting import about, issues_drawer
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "expiries-body"
REFRESH_ID = "expiries-refresh"
COUNTS_ID = "expiries-counts"
TABLE_ID = "expiries-table"
SETTLED_ID = "expiries-settled"
SETTLED_TABLE_ID = "expiries-settled-table"
ISSUES_ID = "expiries-issues"

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


# --------------------------------------------------------------------------- body and shell
def body(result: Dict[str, Any]) -> html.Div:
    """The whole tab body from one `expiry_schedule` result."""
    parts = [counts_strip(result), schedule_section(result)]
    for extra in (settled_section(result), issues_section(result)):
        if extra is not None:
            parts.append(extra)
    return html.Div(className="expiries-body", children=parts)


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


_TAB_ABOUT = ("What is about to expire or go to delivery: every open future, option on a future and LME prompt, "
              "its next first notice, last trade, option expiry or prompt, the business days to its alert date "
              "and its level, as expiry-monitor gives them. A date marked (est.) is an estimate, not Bloomberg's.")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="expiries-tab", children=[
        html.Div(className="ladder-title-row", children=[
            about("Expiries", _TAB_ABOUT, level="h3", className="ladder-title-row-heading"),
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
