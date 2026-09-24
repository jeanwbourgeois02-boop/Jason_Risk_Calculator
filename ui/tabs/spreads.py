"""Spreads tab: "Which spreads am I in, and what are they making?" (Commodity conversion plan,
Phase 3). Rendered from `engine.spreads.book_spreads` and nothing else: every grouping, P&L,
reference close and leftover on the tab is that dict's; nothing here regroups a leg, re-prices a
trade or re-reads a mark (CLAUDE.md "Tabs as views").

The engine is called with the screens' shared filled reader
(`ui.tabs.blotter_pricing.priced_value_book`) as its `value_fn`, so a trade with no price on a
date takes the value of its last earlier close exactly as the header does ("The fill").

Layout, top to bottom (`body`):
  1. `caption_block`: the as-of, the counts, what n/a and the Total line mean, and the engine's
     book-level `reasons` (a template that could not be read, an override that could not apply).
  2. `spreads_section` (open spreads): one row per open spread, largest |LTD| first (n/a last;
     "#" restores that order after a ranking): name, kind, size and its unit, the legs summed up
     ('CLZ26 +10 / CLF27 -10'), LTD, Daily, 5d, MTD, YTD in USD, the leftover outright per root
     in lots and its USD notional. A Total line is pinned under it: each column sums only the
     known figures and, when any spread is n/a there, says "excludes N" (the header's display
     rule). Under the table, one collapsed "Legs" block per spread in the same order: contract,
     month, weight, lots, open lots, currency, local P&L, USD P&L (LTD), status, trades, reason.
  3. `closed_section`: the closed spreads, the same table and legs, collapsed below the open ones.
  4. `outrights_section`: the futures trades in no spread, with their period P&L, why they are
     outright and whether they are listed for review; a Total line as above.
  5. `review_section`: the groups the engine would not decide, with their reason, and the one
     line that a bundle on the Blotter's Bundles sub-tab groups them by hand.

A figure the engine gives as None reads "n/a" (it ranks last) with the engine's reason on hover,
never 0 and never blank without a reason. Every table ranks (`ui.tabs.ranking`); a Total line
never takes part in a ranking (`ranking.with_footer`). The only arithmetic here is the display
sums of the Total lines and of a spread's leftover USD over its roots, engine figures added up.

The tab has no date picker: it follows the header's as-of store and re-renders in place on the
data revision and on its safety interval. `layout(default_date)` (alias `build_layout`) and
`register_callbacks(app, get_db_path)` are the shell's interface, the Curve and Expiries tabs'
shape.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dash import Input, Output, dash_table, dcc, html

from engine.spreads import book_spreads
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "spreads-body"
REFRESH_ID = "spreads-refresh"
TABLE_ID = "spreads-table"
LEGS_ID = "spreads-legs"
CLOSED_ID = "spreads-closed"
CLOSED_TABLE_ID = "spreads-closed-table"
CLOSED_LEGS_ID = "spreads-closed-legs"
OUTRIGHTS_ID = "spreads-outrights"
OUTRIGHTS_TABLE_ID = "spreads-outrights-table"
REVIEW_ID = "spreads-review"
REVIEW_TABLE_ID = "spreads-review-table"

NA = "n/a"
MINUS = "−"
PERIODS = ("ltd", "daily", "d5", "mtd", "ytd")          # the engine's period keys
PERIOD_TITLES = {"ltd": "LTD", "daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}
TOTAL_LABEL = "Total"
KIND_LABELS = {"calendar": "Calendar", "bundle": "Bundle (by hand)", "pinned": "Pinned (by hand)"}
REVIEW_LABELS = {"ambiguous": "Two ways to group", "ratio_off": "Ratio off", "accounts": "Two accounts"}
BUNDLE_HINT = ("To group any of them, put their trades in one bundle on the Blotter's Bundles sub-tab: "
               "a bundle is always taken as one spread.")

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_TOTAL_STYLE = {"fontWeight": "700", "borderTop": "2px solid #1f2933"}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


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


def signed(v: Optional[float]) -> str:
    """+10 / -10 (a true minus sign) / 0, as few decimals as the number needs."""
    if v is None:
        return NA
    if v == 0:
        return "0"
    return f"{v:+,g}".replace("-", MINUS)


def contract_label(instrument_id: Optional[str]) -> str:
    """'CLZ26 Comdty' -> 'CLZ26': the instrument id without its Bloomberg yellow key."""
    s = str(instrument_id or "")
    for key in (" Comdty", " Curncy", " Index"):
        if s.endswith(key):
            return s[: -len(key)]
    return s


def root_label(root_id: Optional[str]) -> str:
    """'NYMEX:CL' -> 'CL'."""
    return str(root_id or "").split(":")[-1]


def kind_label(spread: Dict[str, Any]) -> str:
    """Calendar, Bundle (by hand), Pinned (by hand), or a template's family and id."""
    kind = spread.get("kind") or ""
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    family = spread.get("family") or ""
    template = spread.get("template") or kind
    return f"{family.capitalize()}: {template}" if family else (template or NA)


def legs_summary(legs: Sequence[Dict[str, Any]]) -> str:
    """'CLZ26 +10 / CLF27 -10', the engine's legs in its order."""
    return " / ".join(f"{contract_label(leg.get('instrument_id'))} {signed(_num(leg.get('lots')))}" for leg in legs)


def ltd_order(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Largest |LTD| first, an n/a LTD last, the engine's order within a tie."""
    def key(pair):
        n, item = pair
        v = _num((item.get("pnl_usd") or {}).get("ltd"))
        return (v is None, -abs(v) if v is not None else 0.0, n)
    return [item for _n, item in sorted(enumerate(items), key=key)]


# --------------------------------------------------------------------------- period cells
def period_cells(item: Dict[str, Any], rec: Dict[str, Any], tip: Dict[str, dict]) -> None:
    """The five period cells of a spread or an outright, as the engine gives them: a None is
    "n/a" with its reason; a figure carries the close it is measured from (and the engine's
    step-back or fill note) on hover."""
    values = item.get("pnl_usd") or {}
    reasons = item.get("pnl_reasons") or {}
    notes = item.get("pnl_notes") or {}
    refs = item.get("ref_dates") or {}
    for p in PERIODS:
        v = _num(values.get(p))
        note = notes.get(p) or ""
        if v is None:
            rec[p] = NA
            why = reasons.get(p) or "spreads-engine gave no figure and no reason"
            tip[p] = _tip(f"{why}. {note}".strip() if note else why)
            continue
        rec[p] = v
        words = [] if p == "ltd" else [f"measured from the {refs.get(p) or NA} close"]
        if note:
            words.append(note)
        if words:
            tip[p] = _tip("; ".join(words))


def _period_columns() -> List[dict]:
    usd = rk.amount(nully="")
    return [rk.numeric(f"{PERIOD_TITLES[p]} USD", p, usd) for p in PERIODS]


def total_record(items: Sequence[Dict[str, Any]], records: Sequence[Dict[str, Any]], columns: Sequence[str],
                 label_col: str, note_col: str, noun: str) -> Tuple[dict, dict]:
    """(footer record, its tooltips): each column in `columns` is the sum of the records' known
    figures; one that leaves any out says so on hover and in the note column ("excludes N"); one
    with no known figure at all is n/a. The header's display rule."""
    rec: Dict[str, Any] = {label_col: TOTAL_LABEL}
    tip: Dict[str, dict] = {}
    notes: List[str] = []
    names = [str(i.get("name") or i.get("trade_id") or "") for i in items]
    total = len(records)
    for col in columns:
        known = [(n, r[col]) for n, r in enumerate(records) if isinstance(r.get(col), float)]
        missing = [names[n] for n, r in enumerate(records) if not isinstance(r.get(col), float)]
        title = PERIOD_TITLES.get(col, "Leftover USD" if col == "leftover_usd" else col)
        if not known:
            rec[col] = NA
            tip[col] = _tip(f"no {noun} has a {title} figure" if total else f"no {noun}")
            continue
        rec[col] = float(sum(v for _n, v in known))
        if missing:
            notes.append(f"{title} {len(missing)}")
            tip[col] = _tip(f"excludes {len(missing)} of {total} {noun}s with no figure (n/a): "
                            + ", ".join(missing))
    rec[note_col] = f"excludes n/a: {', '.join(notes)}" if notes else f"{total} {noun}{'s' if total != 1 else ''}"
    return rec, tip


# --------------------------------------------------------------------------- 1. caption
def caption_block(result: Dict[str, Any]) -> html.Div:
    spreads = result.get("spreads") or []
    n_open = sum(1 for s in spreads if s.get("status") != "closed")
    lines = [
        f"As of {_date_words(result.get('as_of'))}.",
        f"{n_open} open spread{'s' if n_open != 1 else ''}, {len(spreads) - n_open} closed, "
        f"{len(result.get('outrights') or [])} outright trade(s), {len(result.get('review') or [])} group(s) for review.",
        "P&L in USD is the legs' own valuation added up (each leg converted at its day's spot), over the "
        "header's periods and reference closes; n/a is a figure spreads-engine could not give, its reason on hover. "
        "A Total line adds up the known figures only and says what it leaves out.",
    ]
    children: List[Any] = [html.Div(className="meta-line", children=[html.Span(line) for line in lines])]
    reasons = [r for r in (result.get("reasons") or []) if r]
    if reasons:
        children.append(html.Details(className="details details--compact", open=len(reasons) <= 3, children=[
            html.Summary(f"Gaps ({len(reasons)}): what the grouping could not use"),
            html.Ul([html.Li(r) for r in reasons], style={"margin": "2px 0 0 16px", "padding": 0})]))
    return html.Div(children, className="spreads-caption")


# --------------------------------------------------------------------------- 2. spreads
def leftover_cells(spread: Dict[str, Any]) -> Tuple[str, Any, str]:
    """(lots text per root, USD notional or "n/a", hover). The USD is the roots' engine figures
    added up; any root without one makes it n/a with the engine's reason."""
    entries = spread.get("leftover") or []
    basis = spread.get("leftover_basis") or ""
    if not entries:
        return "none", NA, "no futures leg in this spread, so no leftover outright is sized"
    lots = [(e, _num(e.get("lots"))) for e in entries]
    if all(v == 0 for _e, v in lots):
        text = "none (clean)"
    else:
        text = "; ".join(f"{root_label(e.get('root_id'))} {signed(v)}" for e, v in lots if v != 0)
    usd = [_num(e.get("usd_notional")) for e in entries]
    parts = [f"{e.get('root_id') or ''}: {signed(v)} lot(s), "
             + (f"USD {u:,.0f}" if u is not None else f"{NA} ({e.get('reason') or 'no USD notional'})")
             for (e, v), u in zip(lots, usd)]
    hover = "; ".join(parts) + (f". Leftover {basis}." if basis else "")
    if any(u is None for u in usd):
        return text, NA, hover
    return text, float(sum(usd)), hover


def spread_record(spread: Dict[str, Any], position: int) -> Tuple[dict, dict]:
    """(record, tooltips) of one spread row, the engine's figures as they are."""
    tip: Dict[str, dict] = {}
    legs = spread.get("legs") or []
    rec: Dict[str, Any] = {
        "rank": position,
        "name": spread.get("name") or spread.get("spread_id") or "",
        "kind": kind_label(spread),
        "size_unit": spread.get("size_unit") or "",
        "legs": legs_summary(legs),
    }
    size = _num(spread.get("size"))
    if size is None:
        rec["size"] = NA
        tip["size"] = _tip("no calendar or template fits all of its futures legs, so it has no size"
                           if spread.get("kind") in ("bundle", "pinned") else "spreads-engine gave no size")
    else:
        rec["size"] = size
    about = [f"id {spread.get('spread_id') or ''}",
             f"trades {', '.join(spread.get('trade_ids') or [])}",
             f"account(s) {', '.join(spread.get('accounts') or [])}",
             f"traded {', '.join(spread.get('trade_dates') or [])}"]
    if spread.get("unit"):
        about.append(f"quoted in {spread['unit']}")
    dev = _num(spread.get("deviation"))
    if dev is not None:
        about.append(f"lots {dev:.1%} off the exact ratio")
    if spread.get("also_matches"):
        about.append(f"also matches {', '.join(spread['also_matches'])}")
    tip["name"] = _tip("; ".join(about))
    tip["legs"] = _tip("; ".join(f"{leg.get('instrument_id') or ''}: {signed(_num(leg.get('lots')))} lot(s), "
                                 f"{signed(_num(leg.get('open_lots')))} open" for leg in legs) or "no legs")
    period_cells(spread, rec, tip)
    text, usd, hover = leftover_cells(spread)
    rec["leftover"], rec["leftover_usd"] = text, usd
    tip["leftover"] = tip["leftover_usd"] = _tip(hover)
    return rec, tip


def spread_columns() -> List[dict]:
    return ([rk.numeric("#", "rank", rk.count()), rk.text("Spread", "name"), rk.text("Kind", "kind"),
             rk.numeric("Size", "size", rk.amount(2, nully="", trim=True)), rk.text("Size unit", "size_unit"),
             rk.text("Legs (lots)", "legs")]
            + _period_columns()
            + [rk.text("Leftover outright (lots)", "leftover"),
               rk.numeric("Leftover USD", "leftover_usd", rk.amount(nully=""))])


_SUMMED = PERIODS + ("leftover_usd",)

_SPREAD_HEADER_TIPS = {
    "rank": "Largest |LTD| first, n/a last. Click to restore that order.",
    "size": "The spread's size: a calendar in lots of the near month; a template in its quantity unit "
            "(+ = long the spread as the template writes it).",
    "legs": "Each leg's net lots, in the engine's leg order.",
    "leftover": "What the open legs hold beyond the largest whole spread they make, per commodity.",
    "leftover_usd": "The leftover's USD notional: lots x multiplier x the day's price x spot, as spreads-engine "
                    "gives it, added over the commodities.",
    **{p: f"{PERIOD_TITLES[p]} P&L in USD, the legs' valuations added up by spreads-engine" for p in PERIODS},
}


def _table_styles(numeric_cols: Sequence[str], text_cols: Sequence[str], wide: Sequence[str] = ()) -> dict:
    return dict(
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in text_cols]
                               + [{"if": {"column_id": c}, "whiteSpace": "normal", "minWidth": "220px",
                                   "maxWidth": "480px"} for c in wide],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(numeric_cols)
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in numeric_cols],
        tooltip_delay=0, tooltip_duration=None,
    )


def spreads_table(spreads: Sequence[Dict[str, Any]], table_id: str) -> html.Div:
    """The ranked spreads table with its Total line pinned under it."""
    recs = [spread_record(s, i) for i, s in enumerate(spreads, start=1)]
    records = [r for r, _ in recs]
    columns = spread_columns()
    numeric = [c["id"] for c in columns if c["type"] == "numeric" and c["id"] != "rank"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=table_id, columns=columns, data=records, tooltip_data=[t for _, t in recs],
        tooltip_header=_SPREAD_HEADER_TIPS,
        **rk.sortable(table_id),
        **_table_styles(numeric, text),
        page_action="none",
    )
    footer, footer_tip = total_record(spreads, records, _SUMMED, "name", "legs", "spread")
    return rk.with_footer(table, [footer], footer_style=[{"if": {"filter_query": f"{{name}} = '{TOTAL_LABEL}'"},
                                                          **_TOTAL_STYLE}],
                          footer_tooltips=[footer_tip])


# ---- the legs under each spread
def leg_record(leg: Dict[str, Any]) -> Tuple[dict, dict]:
    reason = leg.get("reason") or ""
    tip: Dict[str, dict] = {}
    product = leg.get("product") or ""
    month = leg.get("contract_month") or ""
    rec: Dict[str, Any] = {
        "contract": contract_label(leg.get("instrument_id")),
        "month": month or (NA if product == "FUTURE" else f"not a future ({product})" if product else NA),
        "product": product,
        "weight": _num(leg.get("weight")) if _num(leg.get("weight")) is not None else "",
        "lots": _num(leg.get("lots")), "open_lots": _num(leg.get("open_lots")),
        "currency": leg.get("currency") or "", "status": leg.get("status") or "",
        "trades": ", ".join(str(t) for t in (leg.get("trade_ids") or [])), "reason": reason,
    }
    if rec["month"] == NA:
        tip["month"] = _tip("contract-master gave no contract month for this contract")
    if rec["weight"] == "":
        rec["weight"] = NA
        tip["weight"] = _tip("no calendar or template fits these legs, so no weight")
    for col in ("lots", "open_lots"):
        if rec[col] is None:
            rec[col] = NA
            tip[col] = _tip(reason or "not a number on file")
    for col, what in (("pnl_local", "local P&L"), ("pnl_usd", "USD P&L")):
        v = _num(leg.get(col))
        rec[col] = NA if v is None else v
        if v is None:
            tip[col] = _tip(reason or f"spreads-engine gave no {what} and no reason")
    if reason:
        tip["contract"] = _tip(reason)
    return rec, tip


def legs_table(legs: Sequence[Dict[str, Any]]) -> dash_table.DataTable:
    recs = [leg_record(leg) for leg in legs]
    lots = rk.amount(2, nully="", trim=True)
    columns = [rk.text("Contract", "contract"), rk.text("Month", "month"), rk.text("Product", "product"),
               rk.numeric("Weight", "weight", rk.rate(4, trim=True)), rk.numeric("Lots", "lots", lots),
               rk.numeric("Open lots", "open_lots", lots), rk.text("Ccy", "currency"),
               rk.numeric("P&L (local)", "pnl_local", rk.amount(nully="")),
               rk.numeric("LTD USD", "pnl_usd", rk.amount(nully="")),
               rk.text("Status", "status"), rk.text("Trades", "trades"), rk.text("Reason", "reason")]
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    return dash_table.DataTable(
        columns=columns, data=[r for r, _ in recs], tooltip_data=[t for _, t in recs],
        **rk.sortable(), **_table_styles(numeric, text, wide=("reason",)), page_action="none")


def legs_block(spreads: Sequence[Dict[str, Any]], block_id: str) -> html.Div:
    """One collapsed block per spread, in the table's order, holding its legs."""
    blocks = []
    for n, s in enumerate(spreads, start=1):
        legs = s.get("legs") or []
        ltd = _num((s.get("pnl_usd") or {}).get("ltd"))
        figure = f"LTD USD {ltd:,.0f}" if ltd is not None else f"LTD {NA}"
        blocks.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"#{n} {s.get('name') or s.get('spread_id') or ''}: {legs_summary(legs)} "
                         f"({len(legs)} leg{'s' if len(legs) != 1 else ''}, {figure})"),
            legs_table(legs)]))
    return html.Div(id=block_id, className="spreads-legs", children=[
        html.P("Legs of each spread (click to open): its contracts with their lots and the P&L "
               "spreads-engine added up.", className="section-kicker"), *blocks])


def spreads_section(result: Dict[str, Any]) -> html.Div:
    spreads = ltd_order([s for s in (result.get("spreads") or []) if s.get("status") != "closed"])
    heading = html.H4("Open spreads")
    if not spreads:
        return html.Div(className="section", children=[heading, message_box("No open spread in the book.")])
    kicker = ("One row per open spread, largest |LTD| first. A calendar or template spread is found by "
              "spreads-engine in the trades of one account and one trade date; a bundle or a pin is yours. "
              "The leftover is what the open legs hold beyond a whole spread.")
    return html.Div(className="section", children=[
        heading, html.P(kicker, className="section-kicker"),
        spreads_table(spreads, TABLE_ID), legs_block(spreads, LEGS_ID)])


# --------------------------------------------------------------------------- 3. closed spreads
def closed_section(result: Dict[str, Any]) -> Optional[html.Details]:
    """The closed spreads, collapsed below the open ones; None when there are none."""
    closed = ltd_order([s for s in (result.get("spreads") or []) if s.get("status") == "closed"])
    if not closed:
        return None
    return html.Details(id=CLOSED_ID, className="section section--secondary details", open=False, children=[
        html.Summary(f"Closed spreads ({len(closed)})"),
        html.P("Spreads whose every leg has settled or been closed: their P&L is frozen, listed so none "
               "disappears unseen.", className="section-kicker"),
        spreads_table(closed, CLOSED_TABLE_ID), legs_block(closed, CLOSED_LEGS_ID)])


# --------------------------------------------------------------------------- 4. outrights
def outright_record(o: Dict[str, Any]) -> Tuple[dict, dict]:
    tip: Dict[str, dict] = {}
    review_ids = o.get("review_ids") or []
    rec: Dict[str, Any] = {
        "trade_id": o.get("trade_id") or "", "contract": contract_label(o.get("instrument_id")),
        "month": o.get("contract_month") or NA, "account": o.get("account") or "",
        "trade_date": o.get("trade_date") or "", "lots": _num(o.get("lots")),
        "currency": o.get("currency") or "", "status": o.get("status") or "",
        "why": o.get("why_outright") or "no spread fits",
        "review": f"yes ({len(review_ids)})" if review_ids else "",
    }
    if rec["month"] == NA:
        tip["month"] = _tip("contract-master gave no contract month for this contract")
    if rec["lots"] is None:
        rec["lots"] = NA
        tip["lots"] = _tip("trades.quantity is not a number on file")
    local = _num(o.get("pnl_local"))
    rec["pnl_local"] = NA if local is None else local
    if local is None:
        tip["pnl_local"] = _tip((o.get("pnl_reasons") or {}).get("ltd") or "spreads-engine gave no local P&L")
    if review_ids:
        tip["review"] = _tip("listed under For review: " + "; ".join(review_ids))
    period_cells(o, rec, tip)
    return rec, tip


def outrights_section(result: Dict[str, Any]) -> html.Div:
    outrights = result.get("outrights") or []
    heading = html.H4(f"Outrights ({len(outrights)})")
    kicker = "The futures trades in no spread, each with its period P&L and why spreads-engine left it outright."
    if not outrights:
        return html.Div(id=OUTRIGHTS_ID, className="section", children=[
            heading, html.P(kicker, className="section-kicker"), message_box("No outright futures trade.")])
    recs = [outright_record(o) for o in outrights]
    records = [r for r, _ in recs]
    lots = rk.amount(2, nully="", trim=True)
    columns = ([rk.text("Trade", "trade_id"), rk.text("Contract", "contract"), rk.text("Month", "month"),
                rk.text("Account", "account"), rk.text("Trade date", "trade_date"), rk.numeric("Lots", "lots", lots),
                rk.text("Ccy", "currency"), rk.text("Status", "status"),
                rk.numeric("P&L (local)", "pnl_local", rk.amount(nully=""))]
               + _period_columns()
               + [rk.text("Why outright", "why"), rk.text("For review", "review")])
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=OUTRIGHTS_TABLE_ID, columns=columns, data=records, tooltip_data=[t for _, t in recs],
        **rk.sortable(OUTRIGHTS_TABLE_ID), **_table_styles(numeric, text, wide=("why",)), page_action="none")
    footer, footer_tip = total_record(outrights, records, PERIODS, "trade_id", "why", "trade")
    return html.Div(id=OUTRIGHTS_ID, className="section", children=[
        heading, html.P(kicker, className="section-kicker"),
        rk.with_footer(table, [footer], footer_style=[{"if": {"filter_query": f"{{trade_id}} = '{TOTAL_LABEL}'"},
                                                       **_TOTAL_STYLE}],
                       footer_tooltips=[footer_tip], skip_widths=("why",))])


# --------------------------------------------------------------------------- 5. review
def review_record(r: Dict[str, Any]) -> Tuple[dict, dict]:
    cands = r.get("candidates") or []
    rec = {
        "kind": REVIEW_LABELS.get(r.get("kind") or "", r.get("kind") or NA),
        "trades": ", ".join(r.get("trade_ids") or []),
        "contracts": ", ".join(contract_label(i) for i in (r.get("instruments") or [])),
        "accounts": ", ".join(r.get("accounts") or []),
        "trade_dates": ", ".join(r.get("trade_dates") or []),
        "candidates": "; ".join(c.get("name") or c.get("kind") or "" for c in cands),
        "reason": r.get("reason") or "",
    }
    tip = {"kind": _tip(r.get("reason") or ""), "candidates": _tip(
        "; ".join(f"{c.get('name') or c.get('kind')}: {', '.join(c.get('trade_ids') or [])}" for c in cands)
        or "no candidate listed")}
    return rec, tip


def review_section(result: Dict[str, Any]) -> html.Div:
    review = result.get("review") or []
    heading = html.H4(f"For review ({len(review)})")
    kicker = ("Groups spreads-engine would not decide, never guessed: two ways to group the same trades, "
              "a ratio outside 5 %, or legs on two accounts. Their trades stay under Outrights. " + BUNDLE_HINT)
    if not review:
        return html.Div(id=REVIEW_ID, className="section", children=[
            heading, html.P(kicker, className="section-kicker"), message_box("Nothing for review.")])
    recs = [review_record(r) for r in review]
    columns = [rk.text("Kind", "kind"), rk.text("Trades", "trades"), rk.text("Contracts", "contracts"),
               rk.text("Accounts", "accounts"), rk.text("Trade dates", "trade_dates"),
               rk.text("Candidates", "candidates"), rk.text("Reason", "reason")]
    table = dash_table.DataTable(
        id=REVIEW_TABLE_ID, columns=columns, data=[r for r, _ in recs], tooltip_data=[t for _, t in recs],
        **rk.sortable(REVIEW_TABLE_ID), **_table_styles([], [c["id"] for c in columns], wide=("reason",)),
        page_action="none")
    return html.Div(id=REVIEW_ID, className="section", children=[
        heading, html.P(kicker, className="section-kicker"), table])


# --------------------------------------------------------------------------- body and shell
def is_empty(result: Dict[str, Any]) -> bool:
    return not (result.get("spreads") or result.get("outrights") or result.get("review"))


def body(result: Dict[str, Any]) -> html.Div:
    """The whole tab body from one `book_spreads` result."""
    children: List[Any] = [caption_block(result)]
    if is_empty(result):
        children.append(message_box(
            f"No commodity futures in the book on {result.get('as_of') or 'this date'}: "
            "no spread, outright or group for review to show."))
        return html.Div(className="spreads-body", children=children)
    children.append(spreads_section(result))
    closed = closed_section(result)
    if closed is not None:
        children.append(closed)
    children += [outrights_section(result), review_section(result)]
    return html.Div(className="spreads-body", children=children)


def spreads_for(conn: sqlite3.Connection, as_of: str) -> Dict[str, Any]:
    """`book_spreads` through the screens' shared filled reader, one pricing snapshot."""
    from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
    with pricing_snapshot(conn, "Spreads tab"):
        return book_spreads(conn, as_of, value_fn=priced_value_book)


def render(as_of: Optional[str], db_path) -> Any:
    """The body for `as_of` from the database at `db_path`: one `book_spreads` call on a
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
        result = spreads_for(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The spreads could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result)


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="spreads-tab", children=[
        html.Div(className="ladder-title-row", children=[
            html.H3("Spreads", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(f"Follows the header's as-of date{f' ({default_date})' if default_date else ''}",
                        className="section-title")])]),
        html.Div(id=BODY_ID, children=[message_box("Loading the spreads...")]),
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
