"""Spreads tab: "Which spreads am I in, and what are they making?" (Commodity conversion plan,
Phase 3). Rendered from `engine.spreads.book_spreads` and nothing else: every grouping, P&L,
reference close and leftover on the tab is that dict's; nothing here regroups a leg, re-prices a
trade or re-reads a mark (CLAUDE.md "Tabs as views").

The engine is called with the screens' shared filled reader
(`ui.tabs.blotter_pricing.priced_value_book`) as its `value_fn`, so a trade with no price on a
date takes the value of its last earlier close exactly as the header does ("The fill").

Layout, top to bottom (`body`; Screens redesign plan, Phase A: a section's definitions sit on
hover of its title, never as a paragraph above the table, and the reasons are gathered in one
collapsed "Data issues (N)" drawer):
  1. `caption_block`: one line, the as-of and the counts, then the Data issues drawer
     (`issues_items`: the engine's book-level `reasons` and each spread's or outright's n/a
     reasons).
  2. `spreads_section` (open spreads): one row per open spread, largest |LTD| first (n/a last;
     "#" restores that order after a ranking): name, kind, size with its unit in one cell
     ('10 lots', '5,000 bbl'), the legs summed up ('CLZ26 +10 / CLF27 -10'), LTD, Daily, 5d,
     MTD, YTD in USD, the leftover outright per root in lots and its USD notional; the USD
     columns in k / m (`ranking.amount_short` over `ranking.whole_units`), the full figure on
     hover. A Total line is pinned under it: each column sums only the known figures and, when
     any spread is n/a there, says "excludes N" (the header's display rule). Under the table,
     one collapsed "Legs" block per spread in the same order: contract, month, weight, lots,
     open lots, currency, local P&L, USD P&L (LTD), status, trades, reason (full figures).
  3. `closed_section`: the closed spreads, the same table and legs, collapsed below the open ones.
  4. `outrights_section`: the futures trades in no spread, single-line rows with their period
     P&L (trade rows: full figures), why they are outright and whether they are listed for
     review; a Total line as above.
  5. `review_section`: a collapsed "For review (N)" drawer, one line per group the engine would
     not decide with its full reason on hover, and the one line that a bundle on the Blotter's
     Bundles sub-tab groups them by hand.

A figure the engine gives as None reads "n/a" (it ranks last) with the engine's reason on hover,
never 0 and never blank without a reason. Every table ranks (`ui.tabs.ranking`); a Total line
never takes part in a ranking (`ranking.with_footer`). The only arithmetic here is the display
sums of the Total lines and of a spread's leftover USD over its roots, engine figures added up,
and the display rounding of the k / m columns.

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
from ui.tabs.formatting import about, issues_drawer, marker, short_money
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
REVIEW_TABLE_ID = "spreads-review-table"     # the review drawer's list of groups (a table until 2026-09-25)
ISSUES_ID = "spreads-issues"

NA = "n/a"
MINUS = "−"
PERIODS = ("ltd", "daily", "d5", "mtd", "ytd")          # the engine's period keys
PERIOD_TITLES = {"ltd": "LTD", "daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}
TOTAL_LABEL = "Total"
KIND_LABELS = {"calendar": "Calendar", "bundle": "Bundle (by hand)", "pinned": "Pinned (by hand)"}
REVIEW_LABELS = {"ambiguous": "Two ways to group", "ratio_off": "Ratio off", "accounts": "Two accounts"}
BUNDLE_HINT = ("To group any of them, put their trades in one bundle on the Blotter's Bundles sub-tab: "
               "a bundle is always taken as one spread.")

TITLE_ABOUT = ("The book's spreads as spreads-engine finds them, at the header's as-of date. P&L in USD is "
               "the legs' own valuation added up (each leg converted at its day's spot), over the header's "
               "periods and reference closes; n/a is a figure spreads-engine could not give, its reason on "
               "hover. A Total line adds up the known figures only and says what it leaves out.")
OPEN_ABOUT = ("One row per open spread, largest |LTD| first. A calendar or template spread is found by "
              "spreads-engine in the trades of one account and one trade date; a bundle or a pin is yours. "
              "Size is in the spread's own unit. USD figures in k / m, the full figure on hover. "
              "The leftover is what the open legs hold beyond a whole spread.")
LEGS_ABOUT = ("Each spread's contracts with their lots and the P&L spreads-engine added up, in the table's "
              "order: click a spread to open it.")
CLOSED_ABOUT = ("Spreads whose every leg has settled or been closed: their P&L is frozen, listed so none "
                "disappears unseen.")
OUTRIGHTS_ABOUT = "The futures trades in no spread, each with its period P&L and why spreads-engine left it outright."
REVIEW_ABOUT = ("Groups spreads-engine would not decide, never guessed: two ways to group the same trades, "
                "a ratio outside 5 %, or legs on two accounts. Their trades stay under Outrights. "
                "Hover a line for the full reason.")

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
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


def plain(v: float) -> str:
    """5,000 / 96.45 / -2.5 (a true minus sign): thousands separators, at most 2 decimals, trimmed."""
    text = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    return (MINUS + text) if v < 0 and text != "0" else text


def full_usd(v: float) -> str:
    """The full figure behind a k / m cell: 'USD -6,928' (a true minus sign)."""
    return f"USD {v:,.0f}".replace("-", MINUS)


def size_text(size: float, unit: str) -> str:
    """'10 lots', '5,000 bbl', '96.45 oz': the engine's size with its unit in one cell."""
    return f"{plain(size)} {unit}".strip()


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
def period_cells(item: Dict[str, Any], rec: Dict[str, Any], tip: Dict[str, dict], full: bool = False) -> None:
    """The five period cells of a spread or an outright, as the engine gives them: a None is
    "n/a" with its reason; a figure carries the close it is measured from (and the engine's
    step-back or fill note) on hover, and with `full` (a k / m column) the full figure first."""
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
        words = [full_usd(v)] if full else []
        if p != "ltd":
            words.append(f"measured from the {refs.get(p) or NA} close")
        if note:
            words.append(note)
        if words:
            tip[p] = _tip("; ".join(words))


def _period_columns(short: bool = False) -> List[dict]:
    """The five USD period columns: k / m on the spreads tables (`short`, fed through
    `ranking.whole_units`), full figures on the trade rows of the outrights."""
    usd = rk.amount_short(nully="") if short else rk.amount(nully="")
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
def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _na_reasons(item: Dict[str, Any]) -> List[str]:
    """The engine's distinct reasons for the periods it gave as None, in period order."""
    values = item.get("pnl_usd") or {}
    reasons = item.get("pnl_reasons") or {}
    by_reason: Dict[str, List[str]] = {}
    for p in PERIODS:
        if _num(values.get(p)) is None:
            why = reasons.get(p) or "spreads-engine gave no figure and no reason"
            by_reason.setdefault(why, []).append(PERIOD_TITLES[p])
    return [f"{', '.join(titles)} n/a: {why}" for why, titles in by_reason.items()]


def issues_items(result: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The Data issues drawer's lines: the engine's book-level `reasons` (a template that could
    not be read, an override that could not apply), then every spread and outright with an n/a
    period, its reasons as the engine gives them."""
    items: List[Tuple[str, str]] = [("Grouping", r) for r in (result.get("reasons") or []) if r]
    for s in ltd_order(result.get("spreads") or []):
        lines = _na_reasons(s)
        if lines:
            items.append((str(s.get("name") or s.get("spread_id") or ""), "; ".join(lines)))
    for o in result.get("outrights") or []:
        lines = _na_reasons(o)
        if lines:
            items.append((f"Outright {o.get('trade_id') or ''} ({contract_label(o.get('instrument_id'))})",
                          "; ".join(lines)))
    return items


def caption_block(result: Dict[str, Any]) -> html.Div:
    """One line (the as-of and the counts) and the Data issues drawer; the definitions are on
    the title's hover (`TITLE_ABOUT`)."""
    spreads = result.get("spreads") or []
    n_open = sum(1 for s in spreads if s.get("status") != "closed")
    counts = (f"{_plural(n_open, 'open spread')}, {len(spreads) - n_open} closed, "
              f"{_plural(len(result.get('outrights') or []), 'outright')}, "
              f"{_plural(len(result.get('review') or []), 'group')} for review")
    children: List[Any] = [html.Div(className="meta-line", children=[
        html.Span(f"As of {_date_words(result.get('as_of'))}"), html.Span(counts)])]
    drawer = issues_drawer(issues_items(result), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
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
        "legs": legs_summary(legs),
    }
    size = _num(spread.get("size"))
    if size is None:
        rec["size"] = NA
        tip["size"] = _tip("no calendar or template fits all of its futures legs, so it has no size"
                           if spread.get("kind") in ("bundle", "pinned") else "spreads-engine gave no size")
    else:
        rec["size"] = size_text(size, spread.get("size_unit") or "")
    facts = [f"id {spread.get('spread_id') or ''}",
             f"trades {', '.join(spread.get('trade_ids') or [])}",
             f"account(s) {', '.join(spread.get('accounts') or [])}",
             f"traded {', '.join(spread.get('trade_dates') or [])}"]
    if spread.get("unit"):
        facts.append(f"quoted in {spread['unit']}")
    dev = _num(spread.get("deviation"))
    if dev is not None:
        facts.append(f"lots {dev:.1%} off the exact ratio")
    if spread.get("also_matches"):
        facts.append(f"also matches {', '.join(spread['also_matches'])}")
    tip["name"] = _tip("; ".join(facts))
    tip["legs"] = _tip("; ".join(f"{leg.get('instrument_id') or ''}: {signed(_num(leg.get('lots')))} lot(s), "
                                 f"{signed(_num(leg.get('open_lots')))} open" for leg in legs) or "no legs")
    period_cells(spread, rec, tip, full=True)
    text, usd, hover = leftover_cells(spread)
    rec["leftover"], rec["leftover_usd"] = text, usd
    tip["leftover"] = _tip(hover)
    tip["leftover_usd"] = _tip(f"{full_usd(usd)}. {hover}" if isinstance(usd, float) else hover)
    return rec, tip


def spread_columns() -> List[dict]:
    return ([rk.numeric("#", "rank", rk.count()), rk.text("Spread", "name"), rk.text("Kind", "kind"),
             rk.text("Size", "size"), rk.text("Legs (lots)", "legs")]
            + _period_columns(short=True)
            + [rk.text("Leftover outright (lots)", "leftover"),
               rk.numeric("Leftover USD", "leftover_usd", rk.amount_short(nully=""))])


_SUMMED = PERIODS + ("leftover_usd",)

_SPREAD_HEADER_TIPS = {
    "rank": "Largest |LTD| first, n/a last. Click to restore that order.",
    "size": "The spread's size with its unit: a calendar in lots of the near month; a template in its "
            "quantity unit (+ = long the spread as the template writes it).",
    "legs": "Each leg's net lots, in the engine's leg order.",
    "leftover": "What the open legs hold beyond the largest whole spread they make, per commodity.",
    "leftover_usd": "The leftover's USD notional: lots x multiplier x the day's price x spot, as spreads-engine "
                    "gives it, added over the commodities.",
    **{p: f"{PERIOD_TITLES[p]} P&L in USD, the legs' valuations added up by spreads-engine" for p in PERIODS},
}


def _table_styles(numeric_cols: Sequence[str], text_cols: Sequence[str], wide: Sequence[str] = (),
                  na_cols: Sequence[str] = (), one_line: Sequence[str] = (), compact: bool = False) -> dict:
    """Shared table styles. `wide` columns wrap; `one_line` columns are cut with an ellipsis
    (their full text on hover); `compact` tightens the rows; `na_cols` are text columns whose
    "n/a" is greyed like the numeric ones'."""
    cell = dict(_MONO, padding="2px 8px") if compact else _MONO
    return dict(
        style_table={"overflowX": "auto"},
        style_cell=cell,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in text_cols]
                               + [{"if": {"column_id": c}, "whiteSpace": "normal", "minWidth": "220px",
                                   "maxWidth": "480px"} for c in wide]
                               + [{"if": {"column_id": c}, **_ONE_LINE, "maxWidth": "320px"} for c in one_line],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(numeric_cols)
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in (*numeric_cols, *na_cols)],
        tooltip_delay=0, tooltip_duration=None,
    )


def spreads_table(spreads: Sequence[Dict[str, Any]], table_id: str) -> html.Div:
    """The ranked spreads table with its Total line pinned under it; the USD columns in k / m
    (rounded to whole units for display only), the full figure on hover."""
    recs = [spread_record(s, i) for i, s in enumerate(spreads, start=1)]
    records = [r for r, _ in recs]
    columns = spread_columns()
    numeric = [c["id"] for c in columns if c["type"] == "numeric" and c["id"] != "rank"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=table_id, columns=columns, data=rk.whole_units(records, _SUMMED), tooltip_data=[t for _, t in recs],
        tooltip_header=_SPREAD_HEADER_TIPS,
        **rk.sortable(table_id),
        **_table_styles(numeric, text, na_cols=("size",)),
        page_action="none",
    )
    footer, footer_tip = total_record(spreads, records, _SUMMED, "name", "legs", "spread")
    for col in _SUMMED:
        if isinstance(footer.get(col), float):
            rest = (footer_tip.get(col) or {}).get("value")
            footer_tip[col] = _tip(f"{full_usd(footer[col])}; {rest}" if rest else full_usd(footer[col]))
    footer = rk.whole_units([footer], _SUMMED)[0]
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
        figure = f"LTD USD {short_money(ltd)}" if ltd is not None else f"LTD {NA}"
        blocks.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"#{n} {s.get('name') or s.get('spread_id') or ''}: {legs_summary(legs)} "
                         f"({len(legs)} leg{'s' if len(legs) != 1 else ''}, {figure})"),
            legs_table(legs)]))
    return html.Div(id=block_id, className="spreads-legs", children=[
        about("Legs", LEGS_ABOUT, level="div", className="section-kicker"), *blocks])


def spreads_section(result: Dict[str, Any]) -> html.Div:
    spreads = ltd_order([s for s in (result.get("spreads") or []) if s.get("status") != "closed"])
    heading = about(f"Open spreads ({len(spreads)})", OPEN_ABOUT)
    if not spreads:
        return html.Div(className="section", children=[heading, message_box("No open spread in the book.")])
    return html.Div(className="section", children=[
        heading, spreads_table(spreads, TABLE_ID), legs_block(spreads, LEGS_ID)])


# --------------------------------------------------------------------------- 3. closed spreads
def closed_section(result: Dict[str, Any]) -> Optional[html.Details]:
    """The closed spreads, collapsed below the open ones; None when there are none."""
    closed = ltd_order([s for s in (result.get("spreads") or []) if s.get("status") == "closed"])
    if not closed:
        return None
    return html.Details(id=CLOSED_ID, className="section section--secondary details", open=False, children=[
        html.Summary(f"Closed spreads ({len(closed)})", title=CLOSED_ABOUT, className="about-title"),
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
    tip["why"] = _tip(rec["why"])
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
    """The outright futures trades, one single-line row each (trade rows: full figures), a
    Total line pinned under them."""
    outrights = result.get("outrights") or []
    heading = about(f"Outrights ({len(outrights)})", OUTRIGHTS_ABOUT)
    if not outrights:
        return html.Div(id=OUTRIGHTS_ID, className="section", children=[
            heading, message_box("No outright futures trade.")])
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
        **rk.sortable(OUTRIGHTS_TABLE_ID), **_table_styles(numeric, text, one_line=("why",), compact=True),
        page_action="none")
    footer, footer_tip = total_record(outrights, records, PERIODS, "trade_id", "why", "trade")
    return html.Div(id=OUTRIGHTS_ID, className="section", children=[
        heading,
        rk.with_footer(table, [footer], footer_style=[{"if": {"filter_query": f"{{trade_id}} = '{TOTAL_LABEL}'"},
                                                       **_TOTAL_STYLE}],
                       footer_tooltips=[footer_tip], skip_widths=("why",))])


# --------------------------------------------------------------------------- 5. review
def review_record(r: Dict[str, Any]) -> Tuple[dict, dict]:
    """(fields, hover) of one group for review, the engine's as they are."""
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


def review_deviation(r: Dict[str, Any]) -> Optional[float]:
    """The largest deviation spreads-engine gives among a group's candidates, or None."""
    devs = [d for d in (_num(c.get("deviation")) for c in (r.get("candidates") or [])) if d is not None]
    return max(devs) if devs else None


def review_line(r: Dict[str, Any]) -> html.Li:
    """One group for review on one line: kind, candidate, contracts, trades, account(s), trade
    date(s) and how far off the ratio is; the engine's full reason and candidates on hover."""
    rec, tip = review_record(r)
    words = " · ".join(w for w in (rec["candidates"] or "no candidate", rec["contracts"],
                                   f"trades {rec['trades']}" if rec["trades"] else "",
                                   rec["accounts"], rec["trade_dates"]) if w)
    dev = review_deviation(r)
    hover = rec["reason"] or "spreads-engine gave no reason"
    if tip["candidates"]["value"] != "no candidate listed":
        hover += f". Candidates: {tip['candidates']['value']}"
    return html.Li(title=hover, style={**_ONE_LINE, "cursor": "help"}, children=[
        html.Span(rec["kind"], className="issue-label"), " ", words,
        marker(f"off {dev:.1%}" if dev is not None and dev > 0 else "", hover)])


def review_section(result: Dict[str, Any]) -> Optional[html.Details]:
    """The groups for review as a collapsed drawer, "For review (N)", one line per group with
    its reason on hover, and the bundle hint; None when there is nothing for review (the
    caption's count says 0)."""
    review = result.get("review") or []
    if not review:
        return None
    return html.Details(id=REVIEW_ID, className="issues-drawer", open=False, children=[
        html.Summary(f"For review ({len(review)})", title=REVIEW_ABOUT),
        html.Ul(id=REVIEW_TABLE_ID, className="issues-list", children=[review_line(r) for r in review]),
        html.Div(BUNDLE_HINT, className="section-kicker", style={"margin": "4px 0 0"})])


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
    children.append(outrights_section(result))
    review = review_section(result)
    if review is not None:
        children.append(review)
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
    """The static shell: a title row (its definitions on hover) and the body container the
    callback fills. No date picker: the tab follows the header's as-of store, and the body's
    first line names the date."""
    follows = f"Follows the header's as-of date{f' ({default_date} when the page loaded)' if default_date else ''}."
    return html.Div(className="spreads-tab", children=[
        html.Div(className="ladder-title-row", children=[
            about("Spreads", f"{TITLE_ABOUT} {follows}", level="h3", className="ladder-title-row-heading")]),
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
