"""Timing & cash tab (the Expiries tab until 2026-09-28): "What happens when, and when is it
cash?" (Commodity conversion plan, Phase 1 step 3; the timeline and the cash section added in
the UI redesign wave 2, user 2026-09-28; the roll table and the cash tidied in the screens tidy,
wave 2, the same day). The roll calendar is rendered from `engine.expiry.expiry_schedule` and
nothing else: every date, business-day count, level and reason there is that dict's; nothing here
computes a date, a count or a level (CLAUDE.md "Tabs as views"). The cash section reads the cash
ladder's own sources as they are: the open cash legs and the settled cash of
`engine.ladder.exposure_adapter` (`records_from_db`, `settled_records_from_db`) and the
initial-margin estimate of `engine.limits.margin_estimate`.

Layout, top to bottom (`body`):
  0. `timeline_section`: the next 60 business days on the app's calendar (`engine.pnl.calendar`)
     on one horizontal axis, one row per event kind, one marker per event coloured by its level,
     an estimated date hollow, the cash events in navy; events past the as-of or beyond the
     window are counted in a line under the chart, never dropped silently.
  1. `schedule_section`: the "Roll calendar" title line with the level counts in words
     ("0 expired · 0 red · 0 amber · 24 green · 7 on estimated dates"), then one single-line row
     per open position in the engine's order (worst first), seven columns: Level (a chip; grey
     with the engine's level on hover while the date is estimated), In ("3 bd", "today", "2 bd
     ago"), Position (plain name), Event (first notice / last trade / option expiry / LME
     prompt), Date (grey with a leading "≈" while estimated), Lots, Exchange. Everything else
     (alert date and basis, contract id, first notice, product, calendar, delivery, the dates'
     source, the reason) is on hover of the row's cells and in the Data issues drawer.
  2. `settled_section`: the engine's `settled_expired`, the contracts the ledger has frozen in
     full, in a collapsed "Expired and settled (N)" section. They never alert.
  3. `cash_section`: settled cash to date, one line per currency (the cash ladder's Settled cash
     row: deliverable legs past their value date at face value, a settled future's or option's
     USD settlement from `realised_pnl`, never recomputed; a settled ticket the ledger has not
     frozen is named, not valued); the cash to come netted by date and currency (the legs on that
     date in that currency summed, display, the trades on hover): Date, Currency, Amount, What;
     and one line for the initial-margin estimate ("not estimated (margin rates not set)" when the
     estimate covers nothing, else the figure with its "excl." marker).
  4. The tab's one collapsed "Data issues (N)" drawer.

Display rules: `ui.tabs.formatting` (2026-09-28): a missing figure is an em dash with its reason,
never "n/a"; an estimated date grey with a leading "≈"; colour on a Level cell only when the
engine level is RED / AMBER / EXPIRED and the date is real. The tab has no date picker: it follows
the header's as-of store and re-renders in place on the data revision and on its safety interval.
`layout(default_date)` and `register_callbacks(app, get_db_path)` are the shell's interface.
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
from ui.tabs.formatting import (
    ESTIMATED, MINUS, MISSING, about, contract_name, date_cell, estimated_hover, format_cell, issues_drawer, lme_name,
    marker, missing_cell,
    short_money, sign_class,
)
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

NA = MISSING
EST = " (est.)"
ESTIMATED_SOURCE = "Estimated, not Bloomberg's"
SOURCE_LABELS = {"BLOOMBERG": "Bloomberg", "ESTIMATED": ESTIMATED_SOURCE, "TICKET": "Ticket's prompt", "": "unknown"}
ESTIMATE_TIP = "Estimated by contract-master, not Bloomberg's date: the real date may be earlier."
SOURCE_TIPS = {"BLOOMBERG": "Bloomberg's contract date.", "TICKET": "The ticket's own prompt date.",
               "ESTIMATED": ESTIMATE_TIP}
LEVEL_LABELS = {EXPIRED: "Expired", RED: "Red", AMBER: "Amber", GREEN: "Green"}
LEVEL_WORDS = {EXPIRED: "expired", RED: "red", AMBER: "amber", GREEN: "green"}
PRODUCT_LABELS = {"FUTURE": "Future", "CMDTY_OPTION": "Option", "LME_FWD": "LME prompt"}
EVENT_WORDS = {"first notice": "first notice", "last trade": "last trade", "option expiry": "option expiry",
               "LME prompt": "LME prompt"}

# Strong for EXPIRED and RED, warm for AMBER, quiet for GREEN (the settled table's palette too).
LEVEL_STYLES: Dict[str, dict] = {
    EXPIRED: {"backgroundColor": "#7f1d1d", "color": "#ffffff", "fontWeight": "700"},
    RED: {"backgroundColor": "#c62828", "color": "#ffffff", "fontWeight": "700"},
    AMBER: {"backgroundColor": "#fff3e0", "color": "#b26a00", "fontWeight": "700"},
    GREEN: {"backgroundColor": "#f1f5f1", "color": "#52705a"},
}

_CELL = {"textAlign": "right", "fontVariantNumeric": "tabular-nums", "padding": "4px 8px",
         "whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
_NA_STYLE = {"color": "#9ca3af"}
_EST_STYLE = {"color": "#6b7280", "fontStyle": "italic"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the root id stands for its name
        return {}


# --------------------------------------------------------------------------- definitions
def thresholds_sentence(thresholds: Dict[str, Any]) -> str:
    """The thresholds in force, as the engine reports them."""
    red, amber = thresholds.get(RED), thresholds.get(AMBER)
    return (f"RED: {red if red is not None else 'no threshold'} business days or fewer to the alert date; "
            f"AMBER: {amber if amber is not None else 'no threshold'} or fewer; GREEN: further out. "
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
        f"A date marked{EST} or shown grey with a leading ≈ is contract-master's estimate, not Bloomberg's: the real "
        "date may be earlier; its level chip is grey too, the engine's level on hover.",
        "An option on a future alerts on its own expiry; an LME prompt alerts from the day it becomes the "
        "cash date (the prompt less 2 LME business days), and its date is the ticket's own.",
        thresholds_sentence(result.get("thresholds") or {}),
        "Hover a row for its alert date and basis, contract id, first notice, product, calendar, delivery and reason.",
    ])


# --------------------------------------------------------------------------- 1. the counts
def _chip_tip(level: str, n: int, thresholds: Dict[str, Any]) -> str:
    what = {EXPIRED: "event date past, still open",
            RED: f"{thresholds.get(RED, 'no threshold')} business days or fewer to the alert date",
            AMBER: f"{thresholds.get(AMBER, 'no threshold')} business days or fewer to the alert date",
            GREEN: "further out"}[level]
    return f"{LEVEL_LABELS[level]}: {n} open position{'s' if n != 1 else ''} ({what})."


def counts_words(result: Dict[str, Any]) -> List[Any]:
    """'0 expired · 0 red · 0 amber · 24 green · 7 on estimated dates', each count with its
    threshold on hover, for the section's title line."""
    counts = result.get("counts") or {}
    thresholds = result.get("thresholds") or {}
    rows = result.get("rows") or []
    parts: List[Any] = []
    for i, level in enumerate(LEVELS):
        n = int(counts.get(level, 0) or 0)
        if i:
            parts.append(" · ")
        strong = level in (EXPIRED, RED) and n
        parts.append(html.Span(f"{n} {LEVEL_WORDS[level]}", title=_chip_tip(level, n, thresholds),
                               style={"fontWeight": 700, "color": LEVEL_STYLES[level]["backgroundColor"]} if strong else None))
    estimated = sum(1 for r in rows if r.get("estimated"))
    if estimated:
        parts += [" · ", html.Span(f"{estimated} on estimated dates", className="cell-estimated",
                                   title="Positions whose dates are contract-master's estimate, not Bloomberg's: their "
                                         "level is shown grey, the engine's level on hover.")]
    return parts


# --------------------------------------------------------------------------- 2. the table
def _number(v) -> str:
    """A figure as the engine gave it, for a hover: thousands separated, no trailing zeros."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v) if v not in (None, "") else "not on file"
    return f"{f:,.0f}" if f.is_integer() else f"{f:,.4f}".rstrip("0")


def _tonnes_tip(row: Dict[str, Any]) -> str:
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
        when = f"{row.get('underlying_event_date') or 'date unknown'}{EST if row.get('underlying_estimated') else ''}"
        return f"{head}; underlying {und}: {event} {when}."
    return f"{head}; underlying {und}."


def _coverage_sentence(row: Dict[str, Any]) -> str:
    cal = row.get("calendar") or "its"
    return (f"The count reaches past the {cal} calendar file's coverage, where only weekends are known "
            "closed, so it may be too high.")


def _delivery(row: Dict[str, Any]) -> str:
    delivery = row.get("delivery") or ""
    assumed = row.get("delivery_assumed") or ""
    if assumed and assumed != delivery:
        return f"{delivery or 'not on file'} (assumed {assumed})"
    return delivery or "not on file"


def plain_position(row: Dict[str, Any], roots: Dict[str, Any]) -> str:
    """'WTI Dec26', 'WTI Dec26 75c', 'LME copper 10 Dec'."""
    root_id = str(row.get("root_id") or "")
    root = roots.get(root_id)
    cid = str(row.get("contract_id") or "")
    if str(row.get("product") or "") == "LME_FWD":
        prompt = row.get("next_event_date") or (cid.split(" ")[-1] if " " in cid else None)
        return lme_name(root, root_id, prompt)
    return contract_name(cid, root, root_id)


def row_hover(row: Dict[str, Any]) -> str:
    """Everything that left the columns, as one hover: contract id, product, alert date and basis,
    last trade, first notice, dates' source, calendar, delivery, the reason."""
    est = bool(row.get("estimated"))
    source = SOURCE_LABELS.get(row.get("dates_source") or "", row.get("dates_source") or "")
    parts = [f"{row.get('contract_id') or ''} ({row.get('name') or ''}), {PRODUCT_LABELS.get(row.get('product') or '', row.get('product') or 'position')}"]
    if row.get("alert_date"):
        parts.append(f"alert date {row['alert_date']}{EST if est else ''}"
                     + (f" ({row['alert_basis']})" if row.get("alert_basis") else ""))
    if row.get("last_trade_date"):
        parts.append(f"last trade {row['last_trade_date']}{EST if est else ''}")
    if row.get("first_notice_date"):
        parts.append(f"first notice {row['first_notice_date']}")
    elif row.get("product") == "FUTURE":
        parts.append("no first notice date on file" if row.get("dates_source") else "first notice unknown until Bloomberg's dates are on file")
    parts.append(f"dates: {source}")
    parts.append(f"calendar {row.get('calendar') or 'unknown'}, delivery {_delivery(row)}")
    if row.get("beyond_calendar_coverage"):
        parts.append(_coverage_sentence(row))
    if row.get("product") == "CMDTY_OPTION":
        parts.append(_option_tip(row))
    if row.get("product") == "LME_FWD" or "tonnes" in row:
        parts.append(_tonnes_tip(row))
    if row.get("reason"):
        parts.append(str(row["reason"]))
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def level_chip(level: str, estimated: bool, hover: str = "", business_days: Optional[int] = None,
               alert_date: Optional[str] = None):
    """The Level cell: a coloured chip for a real date; while the date is estimated a grey chip
    reading 'est.', the engine's level and the count to the alert on hover (display only, the
    level is unchanged), never a red or amber chip."""
    lvl = str(level or "").upper()
    if not lvl:
        return missing_cell(hover or "no level")
    if estimated:
        return html.Span("est.", className="level-chip level-chip--estimated",
                         title=estimated_hover(business_days, alert_date, lvl, hover))
    return html.Span(lvl, className=f"level-chip level-chip--{lvl.lower()}", title=hover or lvl)


def in_words(business_days: Optional[int]) -> str:
    if business_days is None:
        return NA
    n = int(business_days)
    if n == 0:
        return "today"
    return f"{n} bd" if n > 0 else f"{abs(n)} bd ago"


def lots_words(lots) -> str:
    v = rk.value(lots)
    if v is None:
        return NA
    body = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    return (MINUS + body) if v < 0 else body


def schedule_tr(row: Dict[str, Any], roots: Dict[str, Any]) -> html.Tr:
    """One single-line row of the roll table, the engine's figures as they are."""
    est = bool(row.get("estimated"))
    reason = str(row.get("reason") or "")
    hover = row_hover(row)
    bd = row.get("business_days")
    in_cls = "cell-estimated" if est and bd is not None else (
        "cell-red" if row.get("level") in (RED, EXPIRED) and not est else "cell-amber" if row.get("level") == AMBER and not est else "")
    est_hover = estimated_hover(bd, row.get("alert_date"), str(row.get("level") or ""), reason) if est else ""
    in_cell = (missing_cell(reason or "no count could be made") if bd is None
               else html.Span(ESTIMATED if est else in_words(bd), className=in_cls or None,
                              title=est_hover if est else
                              ("to the alert date, on the " + str(row.get("calendar") or "exchange") + " calendar"
                               + (f"; {_coverage_sentence(row)}" if row.get("beyond_calendar_coverage") else ""))))
    event = EVENT_WORDS.get(str(row.get("next_event") or ""), str(row.get("next_event") or ""))
    lots = rk.value(row.get("lots"))
    cells = [
        html.Td(level_chip(str(row.get("level") or ""), est, reason, bd, row.get("alert_date")), className="l"),
        html.Td(in_cell),
        html.Td(plain_position(row, roots), className="l book-name", title=hover),
        html.Td(event or missing_cell(reason or "no event"), className="l",
                title=_option_tip(row) if row.get("product") == "CMDTY_OPTION" else None),
        html.Td(date_cell(row.get("next_event_date"), None, est, "", reason if not row.get("next_event_date")
                          else SOURCE_TIPS.get(row.get("dates_source") or "", "Source of the date not on file.")), className="l"),
        html.Td(lots_words(lots) if lots is not None else missing_cell(reason or "no lot count"),
                className=sign_class(lots) or None,
                title=_tonnes_tip(row) if (row.get("product") == "LME_FWD" or "tonnes" in row) else None),
        html.Td(str(row.get("exchange") or ""), className="l",
                title=f"Calendar: {row.get('calendar') or 'unknown'}. Delivery: {_delivery(row)}. Dates: "
                      f"{SOURCE_LABELS.get(row.get('dates_source') or '', row.get('dates_source') or '')}."),
    ]
    return html.Tr(cells, title=hover, className="roll-row")


_HEADER_TIPS = {
    "Level": "EXPIRED, RED, AMBER or GREEN by the business days to the alert date; grey while the date is estimated, "
             "the engine's level on hover; the reason on hover of the chip.",
    "In": "Business days from the as-of date to the alert date on the contract's own exchange calendar (not to the "
          "event when the alert is held early); 'ago' once past.",
    "Position": "The contract, option or LME prompt in plain words; the contract id, alert date and basis, first "
                "notice, calendar, delivery and reason on hover.",
    "Event": "The next event: first notice, last trade, option expiry or LME prompt.",
    "Date": "The event's date; grey with a leading ≈ when it is contract-master's estimate, not Bloomberg's.",
    "Lots": "The open lots (an LME prompt's tonnes on hover).",
    "Exchange": "The exchange; its calendar, the delivery method and the dates' source on hover.",
}


def schedule_section(result: Dict[str, Any], roots: Optional[Dict[str, Any]] = None) -> html.Div:
    rows = result.get("rows") or []
    roots = roots if roots is not None else _roots()
    title = html.Div(className="section-title-line", children=[
        about("Roll calendar", definitions(result), level="h4"),
        html.Span(counts_words(result), id=COUNTS_ID, className="book-counts")])
    if not rows:
        return html.Div(className="section", children=[
            title, html.P(result.get("note") or "No open position to show.", className="section-kicker")])
    head = html.Thead(html.Tr([html.Th(name, className="l" if name in ("Level", "Position", "Event", "Date", "Exchange") else None,
                                       title=_HEADER_TIPS[name]) for name in ("Level", "In", "Position", "Event", "Date", "Lots", "Exchange")]))
    table = html.Table([head, html.Tbody([schedule_tr(r, roots) for r in rows])], id=TABLE_ID, className="book-table roll-table")
    return html.Div(className="section", children=[title, html.Div(className="book-card", children=[table])])


# --------------------------------------------------------------------------- 3. expired and settled
def _dated(iso: Optional[str], estimated: bool) -> str:
    if not iso:
        return NA
    return f"{iso}{EST if estimated else ''}"


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
    "FX leg on its value date) are navy, from the cash ladder's own legs (engine.ladder). Hover a marker for the contract, "
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
                 else f"{_number(lots)} lots" if lots is not None else "lots not counted")
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


# --------------------------------------------------------------------------- 5. cash
CASH_ABOUT = (
    "Settled cash to date by currency: the cash ladder's own Settled cash row (engine.ladder.exposure_adapter): "
    "deliverable FX legs past their value date at face value in their own currency, an LME ticket's USD leg "
    "after its prompt, and a settled future's or option's USD settlement as the ledger froze it in "
    "realised_pnl, never recomputed; a settled ticket the ledger has not frozen is named, not valued. This is "
    "cash from the tickets on file, not a bank balance. Then the cash still to come, netted by date and currency "
    "(the legs on that date in that currency added up, the trades on hover), and the initial-margin estimate. A "
    "futures book is margined daily, so its cash is its Daily P&L; the FX delta by currency is on the Exposure tab.")
MARGIN_ABOUT = ("The book's estimated initial margin (engine/limits, config/limits.yaml's placeholder rates with "
                "spread credits): an estimate, not exchange SPAN, never the exchange's or the clearer's figure.")


def settled_cash_lines(records: List[dict]) -> List[dict]:
    """The settled cash summed per currency (known figures added up, the tickets on hover), USD
    first then by |amount|."""
    by_ccy: Dict[str, List[dict]] = {}
    for rec in records:
        by_ccy.setdefault(str(rec.get("currency") or ""), []).append(rec)
    out = []
    for ccy, mine in by_ccy.items():
        total = float(sum(float(r.get("local_amount") or 0.0) for r in mine))
        lines = [f"{r.get('trade_id')} {r.get('currency_pair') or ''} settled {r.get('settled_on') or ''}: "
                 f"{format_cell(float(r.get('local_amount') or 0.0))}" for r in mine]
        out.append({"currency": ccy, "amount": total, "tickets": len(mine),
                    "hover": f"{ccy} {format_cell(total)}; " + "; ".join(lines[:12]) + (f"; and {len(lines) - 12} more" if len(lines) > 12 else "")})
    out.sort(key=lambda r: (r["currency"] != "USD", -abs(r["amount"])))
    return out


def cash_to_come_lines(events: List[dict]) -> List[dict]:
    """The cash still to come netted by date and currency: the legs on that date in that currency
    added up (display), what they are, the trades on hover; soonest first."""
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for e in events:
        groups.setdefault((e["date"], e["currency"]), []).append(e)
    out = []
    for (date, ccy), mine in sorted(groups.items()):
        amount = float(sum(e["amount"] for e in mine))
        kinds = list(dict.fromkeys(e["kind"] for e in mine))
        out.append({"date": date, "currency": ccy, "amount": amount, "what": " + ".join(kinds), "trades": len(mine),
                    "hover": "; ".join(e["hover"] for e in mine[:12]) + (f"; and {len(mine) - 12} more" if len(mine) > 12 else "")})
    return out


def _amount_td(amount: float, hover: str) -> html.Td:
    return html.Td(format_cell(amount), className=sign_class(amount) or None, title=hover)


def margin_line(margin: Optional[Dict[str, Any]], error: str = "") -> html.Div:
    """One line: the book's estimated initial margin in k / m with its basis on hover; "not
    estimated (margin rates not set)" when the estimate covers nothing."""
    if error:
        return html.Div(className="meta-line", children=[html.Span(f"Initial margin: not estimated ({error})", title=MARGIN_ABOUT)])
    m = margin or {}
    book = m.get("book") or {}
    value = book.get("margin_usd")
    positions = int(book.get("positions") or 0)
    excluded = int(book.get("excluded_count") or 0)
    reasons = list(m.get("reasons") or [])
    nothing_in = (not m.get("available")) or value is None or (positions and excluded >= positions) or not positions
    if nothing_in:
        why = "; ".join(reasons) or book.get("reason") or book.get("caption") or "no position in the estimate"
        short = "margin rates not set" if ("rate set" in why or "not set" in why or not reasons) else why
        return html.Div(className="meta-line", children=[
            html.Span(f"Initial margin: not estimated ({short})", title=f"{why}. {MARGIN_ABOUT}")])
    hover = (f"{format_cell(value)} USD = gross charge {format_cell(book.get('gross_charge_usd') or 0)} less spread "
             f"credits {format_cell(book.get('spread_credit_usd') or 0)}; {book.get('basis') or MARGIN_ABOUT}")
    parts: List[Any] = [html.Span("Initial margin (estimate, not exchange SPAN): ", title=MARGIN_ABOUT),
                        html.Span(short_money(value, "$"), title=hover, style={"fontWeight": 700})]
    if excluded:
        parts.append(marker(f"excl. {excluded}", f"{book.get('caption') or f'excludes {excluded} positions'}: "
                                                 + ("; ".join(reasons) or book.get("reason") or "no rate set")))
    return html.Div(className="meta-line", children=parts)


def cash_section(settled: List[dict], settled_unresolved: List[Any], cash_records: List[dict],
                 margin: Optional[Dict[str, Any]], margin_error: str = "") -> html.Div:
    children: List[Any] = [about("Cash", CASH_ABOUT, level="h4")]
    lines = settled_cash_lines(settled)
    cards: List[Any] = []
    settled_card: List[Any] = [html.Div("Settled cash to date", className="book-h", title=CASH_ABOUT)]
    if lines:
        settled_card.append(html.Table(id=SETTLED_CASH_TABLE_ID, className="book-table", children=[
            html.Thead(html.Tr([html.Th("Currency", className="l"), html.Th("Amount"), html.Th("Tickets")])),
            html.Tbody([html.Tr([html.Td(ln["currency"], className="l"), _amount_td(ln["amount"], ln["hover"]),
                                 html.Td(str(ln["tickets"]))]) for ln in lines])]))
    else:
        settled_card.append(html.P("No settled cash yet from the tickets on file.", className="section-kicker"))
    if settled_unresolved:
        settled_card.append(html.P(f"{len(settled_unresolved)} settled ticket(s) not in the figures: "
                                   + "; ".join(f"{u.trade_id} {u.symbol}: {u.reason}" for u in settled_unresolved[:4])
                                   + (f"; and {len(settled_unresolved) - 4} more" if len(settled_unresolved) > 4 else ""),
                                   className="section-kicker"))
    cards.append(html.Div(className="book-card card-pad", children=settled_card))
    to_come = cash_to_come_lines(cash_events(cash_records))
    come_card: List[Any] = [html.Div("Cash to come", className="book-h",
                                     title="The cash legs still to settle, netted by date and currency (the legs added up, "
                                           "the trades on hover): an LME ticket's USD leg on its prompt, an FX leg on its "
                                           "value date (+ = receive). Soonest first.")]
    if to_come:
        come_card.append(html.Table(id=CASH_EVENTS_TABLE_ID, className="book-table", children=[
            html.Thead(html.Tr([html.Th("Date", className="l"), html.Th("Currency", className="l"), html.Th("Amount"),
                                html.Th("What", className="l")])),
            html.Tbody([html.Tr([html.Td(ln["date"], className="l"), html.Td(ln["currency"], className="l"),
                                 _amount_td(ln["amount"], ln["hover"]),
                                 html.Td(ln["what"] + (f" · {ln['trades']} legs" if ln["trades"] > 1 else ""), className="l grid-note",
                                         title=ln["hover"])]) for ln in to_come])]))
    else:
        come_card.append(html.P("No cash leg still to settle on the tickets on file.", className="section-kicker"))
    cards.append(html.Div(className="book-card card-pad", children=come_card))
    children.append(html.Div(className="cards-row", children=cards))
    children.append(margin_line(margin, margin_error))
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
            about(label, None), missing_cell(f"{label} could not be built ({type(exc).__name__}: {exc})")])


def body(result: Dict[str, Any], cash: Optional[Dict[str, Any]] = None) -> html.Div:
    """The whole tab body from one `expiry_schedule` result and the cash reads (`cash_inputs`;
    None renders the roll calendar alone)."""
    cash = cash or {}
    as_of = result.get("as_of") or ""
    roots = _roots()
    parts: List[Any] = []
    if cash:
        parts.append(_safe(lambda: timeline_section(as_of, result, cash.get("records") or []), "Timeline"))
    parts.append(_safe(lambda: schedule_section(result, roots), "Roll calendar"))
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


_TAB_ABOUT = ("What happens when, and when is it cash: the next 60 business days of expiries, prompts and value dates "
              "on one line, the roll calendar (every open future, option on a future and LME prompt, its next event, "
              "the business days to its alert date and its level, as expiry-monitor gives them; an estimated date grey "
              "with a leading ≈), and the cash: settled to date, still to come, and the initial-margin estimate.")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="expiries-tab", children=[
        html.Div(className="book-title-row", children=[
            about("Timing & cash", _TAB_ABOUT, level="h3"),
            html.Span("what happens when, and when is it cash", className="book-counts")]),
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
