"""Curve tab: "What am I long or short, in which month?" (Commodity conversion plan, Phase 1
step 3; options on futures, LME prompts and averaging contracts since Phase 5). Rendered from
`engine.curve.curve_positions` and nothing else: every position, price, notional, delta and P&L
on the tab is that dict's; nothing here re-prices or re-reads a mark (CLAUDE.md "Tabs as views").

Layout, top to bottom (`body`):
  1. `caption_block`: the as-of, the engine's note (no commodity future, every one flat) and
     its `reasons` list.
  2. `grid_section`: one row per commodity (`by_commodity`) in the engine's order (sector,
     root), the Sector column first, the contract months (`months`) across. The view switch
     (`UNIT_ID`) picks what a month cell shows and which totals end the row:
       - the outright views (lots, physical units, USD notional) are futures and LME prompts
         only, as the engine gives them (an option is not a lot of the future): lots are
         `by_commodity[...]['months']`; units and USD are the outright rows' `units` /
         `notional_usd` summed into their month cell. The row ends with Net / Gross lots, Net
         units, Unit, Net / Gross USD (the engine's per-commodity figures). A month holding only
         options is blank with the options named on hover.
       - the delta views (delta lots, delta USD) take every product, an option at its delta and
         an averaging contract at its reduced delta: delta lots are `by_commodity[...]
         ['delta_months']`; delta USD is the rows' `delta_usd` summed into their month cell. The
         row ends with Net delta lots, Net / Gross delta USD (the engine's).
     Summing the engine's per-contract figures into a month cell is display arithmetic on
     engine figures only. A cell with a contract whose figure is None reads "n/a" with the
     contract's reason on hover; an empty cell is a month with no position in that view. Every
     cell with a position lists its contracts on hover (and, in the delta views, each one's
     note: the averaging days left, say).
  3. `sector_section`: net and gross USD notional and net and gross USD delta per sector
     (`by_sector`), the book's sum pinned under it (n/a with the reasons when any sector is n/a).
  4. `detail_section`: the engine's `rows`, one per position: product (only when the book holds
     more than one), exchange, contract, expiry (marked "(est.)" when contract-master's dates are
     ESTIMATED; an LME prompt date never is), first notice, lots, units, price and its currency,
     USD per unit, local and USD notional (an option's reads "option: see delta"), delta factor,
     delta lots and delta USD (the row's note on hover), trades and the reason.
  5. `currency_section`: the P&L the non-USD futures and options hold in each currency and its
     USD value (`currency_exposure`).
  6. `flat_section`: the open positions that net to zero, collapsed.

Every table ranks (`ui.tabs.ranking`): numbers stored as numbers, a missing figure the string
"n/a" (ranks last) with its reason as the cell's tooltip, never zero and never blank without a
reason. The tab has no date picker: it follows the header's as-of store and re-renders in place
on the data revision and on its safety interval. `layout(default_date)` and
`register_callbacks(app, get_db_path)` are the shell's interface, the Risk tab's shape.
"""
from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from dash import Input, Output, dash_table, dcc, html

from engine.curve import curve_positions
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "curve-body"
REFRESH_ID = "curve-refresh"
UNIT_ID = "curve-unit"
GRID_ID = "curve-grid"
SECTOR_TABLE_ID = "curve-sector-table"
DETAIL_TABLE_ID = "curve-detail-table"
CURRENCY_TABLE_ID = "curve-currency-table"
FLAT_ID = "curve-flat"
FLAT_TABLE_ID = "curve-flat-table"

NA = "n/a"
ESTIMATED = "ESTIMATED"     # contract-master's dates_source for a date it estimated
PROMPT = "PROMPT"           # the engine's dates_source of an LME forward: the ticket's own prompt date
FUTURE, OPTION, LME = "FUTURE", "CMDTY_OPTION", "LME_FWD"
OUTRIGHT = (FUTURE, LME)    # the products the engine counts in lots, units and USD notional
PRODUCT_LABELS = {FUTURE: "Future", OPTION: "Option", LME: "LME prompt"}
OPTION_NOTIONAL = "option: see delta"   # an option's notional cell: it has none, its exposure is its delta
UNITS = ("lots", "units", "usd", "delta_lots", "delta_usd")
DELTA_UNITS = ("delta_lots", "delta_usd")
UNIT_LABELS = {"lots": "Lots", "units": "Physical units", "usd": "USD notional",
               "delta_lots": "Delta lots", "delta_usd": "Delta USD"}
DEFAULT_UNIT = "lots"
MONTH_PREFIX = "m_"          # a month column's id: 'm_2026-12'

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_EST_TIP = ("estimated by contract-master (the last weekday of the contract month, a 'no later than' date): "
            "Bloomberg's contract dates are not on file yet")
_PROMPT_TIP = "the LME forward's own prompt date, from the ticket: never estimated"
_OPTION_NOTIONAL_TIP = ("an option has no notional: its exposure is its delta in futures-equivalent lots "
                        "(the Delta columns, and the delta views of the grid)")


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%A} {d.day} {d:%B %Y} ({iso})"


def _sector_label(sector: Optional[str]) -> str:
    return sector.capitalize() if sector else "Unclassified"


def _product(row: Dict[str, Any]) -> str:
    """A row's product; a row without one (the Phase 1 shape) is a future."""
    return row.get("product") or FUTURE


def product_label(product: Optional[str]) -> str:
    return PRODUCT_LABELS.get(product or FUTURE, product or "")


def month_column(key: str) -> str:
    """'2026-12' -> 'm_2026-12', the grid column id of that contract month."""
    return f"{MONTH_PREFIX}{key}"


def month_label(key: str) -> str:
    """'2026-12' -> 'Dec 26'."""
    try:
        year, month = (int(p) for p in key.split("-"))
        return f"{calendar.month_abbr[month]} {year % 100:02d}"
    except (ValueError, IndexError):
        return key


def _row_month(row: Dict[str, Any]) -> Optional[str]:
    """The engine's month key of a contract row, or None when contract-master gave it none."""
    if row.get("year") is None or row.get("month") is None:
        return None
    return f"{int(row['year']):04d}-{int(row['month']):02d}"


def _na_styles(columns) -> List[dict]:
    return [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE} for c in columns]


def _fmt(unit: str) -> dict:
    """The month cells' format: lots and units keep up to 2 decimals, USD is whole dollars."""
    return rk.amount(nully="") if unit in ("usd", "delta_usd") else rk.amount(2, nully="", trim=True)


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


# --------------------------------------------------------------------------- 1. caption
def caption_lines(result: Dict[str, Any]) -> List[str]:
    lines = [f"As of {_date_words(result.get('as_of'))}."]
    if not result.get("available"):
        lines.append("Curve positions unavailable.")
    if result.get("note"):
        lines.append(f"{result['note'][0].upper()}{result['note'][1:]}.")
    return lines


def caption_block(result: Dict[str, Any]) -> html.Div:
    children: List[Any] = [html.Div(className="meta-line", children=[html.Span(line) for line in caption_lines(result)])]
    reasons = [r for r in (result.get("reasons") or []) if r]
    if reasons:
        children.append(html.Details(className="details details--compact", open=len(reasons) <= 3, children=[
            html.Summary(f"Gaps ({len(reasons)}): figures the engine could not compute"),
            html.Ul([html.Li(r) for r in reasons], style={"margin": "2px 0 0 16px", "padding": 0})]))
    return html.Div(children, className="curve-caption")


# --------------------------------------------------------------------------- 2. the grid
_CELL_FIELD = {"units": "units", "usd": "notional_usd", "delta_lots": "delta_lots", "delta_usd": "delta_usd"}
_CELL_WORDS = {"lots": "lots", "units": "physical units", "usd": "USD notional",
               "delta_lots": "delta", "delta_usd": "USD delta"}


def _contract_line(row: Dict[str, Any], unit: str) -> str:
    cid = row.get("contract_id", "")
    tag = " (option)" if _product(row) == OPTION else " (LME prompt)" if _product(row) == LME else ""
    if unit in DELTA_UNITS:
        dl = _num(row.get("delta_lots"))
        if dl is None:
            return f"{cid}{tag}: delta {NA}"
        s = f"{cid}{tag}: {dl:g} delta lot(s)"
        lots, factor = _num(row.get("lots")), _num(row.get("delta_factor"))
        if lots is not None and factor is not None and factor != 1.0:
            s += f" = {lots:g} lot(s) x {factor:.4g}"
        if unit == "delta_usd" and _num(row.get("delta_usd")) is not None:
            s += f", USD {row['delta_usd']:,.0f}"
        if row.get("note") and _product(row) != OPTION:
            s += f" ({row['note']})"
        return s
    lots = _num(row.get("lots"))
    s = f"{cid}{tag}: {lots:g} lot(s)" if lots is not None else f"{cid}{tag}"
    if unit == "units" and _num(row.get("units")) is not None:
        s += f", {row['units']:,.2f} {row.get('unit') or ''}".rstrip()
    if unit == "usd" and _num(row.get("notional_usd")) is not None:
        s += f", USD {row['notional_usd']:,.0f}"
    return s


def month_cells(result: Dict[str, Any], root_id: str, unit: str) -> Tuple[Dict[str, Any], Dict[str, dict]]:
    """({column id: value}, {column id: tooltip}) of one commodity's month cells in `unit`.

    lots / delta_lots: the engine's `by_commodity[root]['months']` / `['delta_months']` as they
    stand. units / usd / delta_usd: the commodity's rows' `units` / `notional_usd` /
    `delta_usd` summed per month; a month holding a row whose figure is None is "n/a" with
    that row's reason. The outright views take futures and LME prompts only; a month holding
    only options there is blank with the options named on hover. A month with no row is
    absent (blank)."""
    rows = [r for r in (result.get("rows") or []) if r.get("root_id") == root_id]
    delta_view = unit in DELTA_UNITS
    shown = rows if delta_view else [r for r in rows if _product(r) in OUTRIGHT]
    options = [] if delta_view else [r for r in rows if _product(r) == OPTION]
    by_month: Dict[str, List[dict]] = {}
    for r in shown:
        key = _row_month(r)
        if key is not None:
            by_month.setdefault(key, []).append(r)
    opt_month: Dict[str, List[dict]] = {}
    for r in options:
        key = _row_month(r)
        if key is not None:
            opt_month.setdefault(key, []).append(r)
    c = (result.get("by_commodity") or {}).get(root_id, {})
    engine_map = (c.get("months") if unit == "lots" else c.get("delta_months") if unit == "delta_lots" else None) or {}
    values: Dict[str, Any] = {}
    tips: Dict[str, dict] = {}
    for key in sorted(set(by_month) | set(engine_map) | set(opt_month)):
        col = month_column(key)
        mine = by_month.get(key, [])
        opts = opt_month.get(key, [])
        lines = [_contract_line(r, unit) for r in mine]
        gaps: List[str] = []
        if unit in ("lots", "delta_lots") and key in engine_map:
            v = _num(engine_map.get(key))
            values[col] = NA if v is None else v
            if unit == "lots":
                gaps = [f"{r['contract_id']}: {r['reason']}" for r in mine if r.get("reason") and "quantity" in r["reason"]]
                if v is None:
                    gaps = gaps or ["no lots for this month"]
            elif v is None:
                gaps = [f"{r.get('contract_id', '')}: {r.get('reason') or 'no delta'}"
                        for r in mine if _num(r.get("delta_lots")) is None] or [c.get("delta_reason") or "no delta for this month"]
        elif mine:
            field = _CELL_FIELD.get(unit, "lots")
            missing = [r for r in mine if _num(r.get(field)) is None]
            if missing:
                values[col] = NA
                gaps = [f"{r.get('contract_id', '')}: {r.get('reason') or 'no ' + _CELL_WORDS[unit]}" for r in missing]
            else:
                values[col] = float(sum(float(r[field]) for r in mine))
        text = "; ".join(lines)
        if values.get(col) == NA:
            text = f"{NA}: " + "; ".join(gaps) + (f" | {text}" if text else "")
        elif gaps:
            text = f"{text} | " + "; ".join(gaps)
        if opts:
            ids = ", ".join(r.get("contract_id", "") for r in opts)
            opt_text = (f"options: {ids}; an option is not a lot of the future, see the delta views")
            text = f"{text} | also {opt_text}" if text else f"only {opt_text}"
        if text:
            tips[col] = _tip(text)
    return values, tips


def grid_records(result: Dict[str, Any], unit: str) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips) of the grid, one per commodity in the engine's order. Every record
    carries both the outright and the delta end figures; `grid_columns` shows the view's."""
    records, tooltips = [], []
    rows = result.get("rows") or []
    delta_view = unit in DELTA_UNITS
    for root_id, c in (result.get("by_commodity") or {}).items():
        rec: Dict[str, Any] = {"sector": _sector_label(c.get("sector")), "commodity": c.get("name") or root_id,
                               "root_id": root_id, "exchange": c.get("exchange") or "", "currency": c.get("currency") or ""}
        tip: Dict[str, dict] = {}
        cells, cell_tips = month_cells(result, root_id, unit)
        rec.update(cells)
        tip.update(cell_tips)
        rec["net_lots"] = _num(c.get("net_lots"))
        rec["gross_lots"] = _num(c.get("gross_lots"))
        mine = [r for r in rows if r.get("root_id") == root_id]
        outright = [r for r in mine if _product(r) in OUTRIGHT]
        units = _num(c.get("net_units"))
        if units is None:
            rec["net_units"] = NA
            why = "; ".join(f"{r['contract_id']}: {r.get('reason') or 'no physical units'}"
                            for r in outright if _num(r.get("units")) is None)
            tip["net_units"] = _tip(why or "no physical units")
        else:
            rec["net_units"] = units
        rec["unit"] = c.get("unit") or ""
        for col in ("net_usd", "gross_usd"):
            v = _num(c.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(c.get("reason") or "no USD notional")
        for col in ("net_delta_lots", "net_delta_usd", "gross_delta_usd"):
            v = _num(c.get(col))
            rec[col] = NA if v is None else v
            if v is None and delta_view:
                tip[col] = _tip(c.get("delta_reason") or "no delta")
        products = c.get("products") or sorted({_product(r) for r in mine})
        notes = []
        no_month = [r["contract_id"] for r in mine if _row_month(r) is None]
        if no_month:
            notes.append(f"no contract month for {', '.join(no_month)}: in the totals, in no month column")
        if delta_view:
            if c.get("delta_missing"):
                notes.append(f"no delta for {', '.join(c['delta_missing'])}")
            averaging = [r["contract_id"] for r in mine if _product(r) == FUTURE and r.get("note")]
            if averaging:
                notes.append(f"averaging, reduced delta: {', '.join(averaging)}")
            if c.get("delta_reason"):
                tip["note"] = _tip(c["delta_reason"])
        else:
            if c.get("missing"):
                notes.append(f"no USD notional for {', '.join(c['missing'])}")
            if OPTION in products:
                notes.append("options: in the delta views only")
            if c.get("reason"):
                tip["note"] = _tip(c["reason"])
        rec["note"] = "; ".join(notes)
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def grid_columns(months: List[str], unit: str) -> List[dict]:
    fmt = _fmt(unit)
    lots = rk.amount(2, nully="", trim=True)
    usd = rk.amount(nully="")
    head = [rk.text("Sector", "sector"), rk.text("Commodity", "commodity"), rk.text("Exchange", "exchange"),
            rk.text("Ccy", "currency")]
    cells = [rk.numeric(month_label(k), month_column(k), fmt) for k in months]
    if unit in DELTA_UNITS:
        tail = [rk.numeric("Net delta lots", "net_delta_lots", lots),
                rk.numeric("Net delta USD", "net_delta_usd", usd), rk.numeric("Gross delta USD", "gross_delta_usd", usd)]
    else:
        tail = [rk.numeric("Net lots", "net_lots", lots), rk.numeric("Gross lots", "gross_lots", lots),
                rk.numeric("Net units", "net_units", lots), rk.text("Unit", "unit"),
                rk.numeric("Net USD", "net_usd", usd), rk.numeric("Gross USD", "gross_usd", usd)]
    return head + cells + tail + [rk.text("Note", "note")]


_IN_WORDS = {
    "lots": "net lots",
    "units": "net physical units (lots x contract size, in the Unit column's unit)",
    "usd": "net USD notional (lots x multiplier x price x spot, the engine's per-contract figure)",
    "delta_lots": "net delta in futures-equivalent lots (lots x delta factor: 1 for a future or LME prompt, the "
                  "share of pricing days left for an averaging contract, the option's delta for an option)",
    "delta_usd": "net USD delta (delta lots x multiplier x the future's price x spot, the engine's per-contract figure)",
}


def grid_caption(result: Dict[str, Any], unit: str) -> str:
    caption = (f"One row per commodity, grouped by sector; each month cell is the {_IN_WORDS[unit]} of that "
               "contract month, summed over its contracts (hover lists them). An empty month cell holds no "
               "position in this view; n/a is a figure the engine could not compute, its reason on hover.")
    if unit in DELTA_UNITS:
        caption += (" Every product is here, an option at its delta and an averaging contract at its reduced "
                    "delta (the days left on hover). Net delta lots and net and gross delta USD at the end are "
                    "the engine's per-commodity totals.")
    else:
        caption += (" Lots, units and USD notional are futures and LME prompts only, as the engine gives them: "
                    "an option is not a lot of the future, and shows in the delta views. Net and gross lots, "
                    "units and USD at the end are the engine's per-commodity totals.")
    return caption


def grid_section(result: Dict[str, Any], unit: str) -> html.Div:
    unit = unit if unit in UNITS else DEFAULT_UNIT
    months = list(result.get("months") or [])
    records, tips = grid_records(result, unit)
    columns = grid_columns(months, unit)
    month_ids = [month_column(k) for k in months]
    if unit in DELTA_UNITS:
        signed, other = month_ids + ["net_delta_lots", "net_delta_usd"], ["gross_delta_usd"]
    else:
        signed, other = month_ids + ["net_lots", "net_units", "net_usd"], ["gross_usd"]
    table = dash_table.DataTable(
        id=GRID_ID,
        columns=columns,
        data=records,
        tooltip_data=tips,
        tooltip_header={month_column(k): f"contract month {k}, in {UNIT_LABELS[unit].lower()}" for k in months},
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(GRID_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("sector", "commodity", "exchange", "currency", "unit", "note")]
                               + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "200px", "maxWidth": "380px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(signed) + _na_styles(signed + other),
    )
    return html.Div(className="section", children=[
        html.H4(f"Positions by contract month ({UNIT_LABELS[unit].lower()})"),
        html.P(grid_caption(result, unit), className="section-kicker"),
        table])


# --------------------------------------------------------------------------- 3. sectors
_SECTOR_COLS = (("net_usd", "reason", "no USD notional"), ("gross_usd", "reason", "no USD notional"),
                ("net_delta_usd", "delta_reason", "no USD delta"), ("gross_delta_usd", "delta_reason", "no USD delta"))


def sector_records(result: Dict[str, Any]) -> Tuple[List[dict], List[dict], dict, dict]:
    """(records, tooltips, book footer record, its tooltips). The book line sums the sectors'
    engine figures (USD notional and USD delta), n/a with the reasons when any sector is n/a."""
    records, tips = [], []
    names = {k: (v.get("name") or k) for k, v in (result.get("by_commodity") or {}).items()}
    sums: Dict[str, Optional[float]] = {col: 0.0 for col, _, _ in _SECTOR_COLS}
    gaps: Dict[str, List[str]] = {"reason": [], "delta_reason": []}
    for sector, s in (result.get("by_sector") or {}).items():
        rec: Dict[str, Any] = {"sector": _sector_label(sector),
                               "commodities": ", ".join(names.get(c, c) for c in (s.get("commodities") or []))}
        tip: Dict[str, dict] = {}
        for col, why_key, why_default in _SECTOR_COLS:
            v = _num(s.get(col))
            if v is None:
                rec[col] = NA
                tip[col] = _tip(s.get(why_key) or why_default)
                sums[col] = None
            else:
                rec[col] = v
                if sums[col] is not None:
                    sums[col] += v
        for why_key in gaps:
            if s.get(why_key):
                gaps[why_key].append(f"{_sector_label(sector)}: {s[why_key]}")
        records.append(rec)
        tips.append(tip)
    footer: Dict[str, Any] = {"sector": "Book", "commodities": f"{len(names)} commodit{'y' if len(names) == 1 else 'ies'}"}
    footer_tip: Dict[str, dict] = {}
    for col, why_key, why_default in _SECTOR_COLS:
        if sums[col] is None:
            footer[col] = NA
            footer_tip[col] = _tip("; ".join(gaps[why_key]) or f"a sector has {why_default}")
        else:
            footer[col] = sums[col]
    return records, tips, footer, footer_tip


def sector_section(result: Dict[str, Any]) -> html.Div:
    records, tips, footer, footer_tip = sector_records(result)
    usd = rk.amount(nully="")
    numeric = [col for col, _, _ in _SECTOR_COLS]
    table = dash_table.DataTable(
        id=SECTOR_TABLE_ID,
        columns=[rk.text("Sector", "sector"), rk.numeric("Net USD", "net_usd", usd),
                 rk.numeric("Gross USD", "gross_usd", usd), rk.numeric("Net delta USD", "net_delta_usd", usd),
                 rk.numeric("Gross delta USD", "gross_delta_usd", usd), rk.text("Commodities", "commodities")],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(SECTOR_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("sector", "commodities")],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(["net_usd", "net_delta_usd"], bold=True) + _na_styles(numeric),
    )
    footer_style = [{"if": {"filter_query": "{sector} = 'Book'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    return html.Div(className="section", children=[
        html.H4("Net outright by sector (USD)"),
        html.P("Net = the sector's contracts' USD notionals summed with their signs (a calendar spread nets to its "
               "leftover outright); gross = their absolute values summed; both over futures and LME prompts. The "
               "delta columns are the same over every product, an option at its delta and an averaging contract "
               "at its reduced delta. The Book line adds the sectors up; any sector n/a makes it n/a.",
               className="section-kicker"),
        rk.with_footer(table, [footer], footer_style=footer_style, footer_tooltips=[footer_tip])])


# --------------------------------------------------------------------------- 4. the contracts
def _first_notice_tip(r: Dict[str, Any], est: bool) -> str:
    product = _product(r)
    if product == LME:
        return "an LME forward has no first notice: it settles on its prompt date"
    if product == OPTION:
        und = r.get("underlying_id")
        return ("an option has no first notice" + (f"; its underlying future {und} carries its own" if und else ""))
    if est or not r.get("dates_source"):
        return "no first notice date until Bloomberg's contract dates are on file (contract-master never estimates it)"
    return "Bloomberg gives no first notice date for this contract"


def detail_records(result: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    records, tooltips = [], []
    for r in result.get("rows") or []:
        reason = r.get("reason") or ""
        note = r.get("note") or ""
        product = _product(r)
        tip: Dict[str, dict] = {}
        est = r.get("dates_source") == ESTIMATED
        rec: Dict[str, Any] = {
            "product": product_label(product),
            "sector": _sector_label(r.get("sector")), "commodity": r.get("name") or r.get("root_id", ""),
            "exchange": r.get("exchange") or "", "contract_id": r.get("contract_id") or "",
            "month": _row_month(r) or NA,
            "expiry": f"{r.get('expiry') or NA}{' (est.)' if est else ''}",
            "lots": _num(r.get("lots")), "unit": r.get("unit") or "",
            "currency": r.get("currency") or "", "price_source": r.get("price_source") or "",
            "trades": ", ".join(str(t) for t in (r.get("trade_ids") or [])), "reason": reason,
        }
        if rec["month"] == NA:
            tip["month"] = _tip(reason or "contract-master gave no contract month")
        elif product == OPTION and r.get("underlying_id"):
            tip["month"] = _tip(f"the underlying future's contract month ({r['underlying_id']})")
        if est:
            tip["expiry"] = _tip(_EST_TIP)
        elif r.get("dates_source") == PROMPT:
            tip["expiry"] = _tip(_PROMPT_TIP)
        if r.get("first_notice"):
            rec["first_notice"] = r["first_notice"]
        else:
            rec["first_notice"] = NA
            tip["first_notice"] = _tip(_first_notice_tip(r, est))
        for col, why_default in (("units", "no physical units"), ("price", "no price"),
                                 ("usd_per_unit", "no USD per unit"), ("notional_local", "no local notional"),
                                 ("notional_usd", "no USD notional")):
            if product == OPTION and col in ("notional_local", "notional_usd"):
                rec[col] = OPTION_NOTIONAL
                tip[col] = _tip(_OPTION_NOTIONAL_TIP)
                continue
            v = _num(r.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(reason or why_default)
        if product == OPTION and _num(r.get("price")) is not None:
            tip["price"] = _tip(f"the underlying future's price ({r.get('underlying_id') or 'underlying'}), "
                                "the price the option's delta is taken at")
        if r.get("usd_source"):
            tip.setdefault("usd_per_unit", _tip(r["usd_source"]))
        for col, why_default in (("delta_factor", "no delta factor"), ("delta_lots", "no delta"),
                                 ("delta_usd", "no USD delta")):
            v = _num(r.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(" | ".join(t for t in (reason or why_default, note) if t))
            elif note:
                tip[col] = _tip(note)
        if reason:
            tip["reason"] = _tip(reason)
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def show_product_column(result: Dict[str, Any]) -> bool:
    """The Product column shows only when the book holds more than one product."""
    return len(result.get("products_present") or []) > 1


def detail_section(result: Dict[str, Any]) -> html.Div:
    records, tips = detail_records(result)
    lots = rk.amount(2, nully="", trim=True)
    columns = ([rk.text("Product", "product")] if show_product_column(result) else []) + [
        rk.text("Sector", "sector"), rk.text("Commodity", "commodity"), rk.text("Exchange", "exchange"),
        rk.text("Contract", "contract_id"), rk.text("Month", "month"), rk.text("Expiry", "expiry"),
        rk.text("First notice", "first_notice"), rk.numeric("Lots", "lots", lots),
        rk.numeric("Units", "units", lots), rk.text("Unit", "unit"),
        rk.numeric("Price", "price", rk.rate(6, trim=True)), rk.text("Ccy", "currency"),
        rk.text("Price source", "price_source"), rk.numeric("USD per unit", "usd_per_unit", rk.rate(6, trim=True)),
        rk.numeric("Notional (local)", "notional_local", rk.amount(nully="")),
        rk.numeric("Notional USD", "notional_usd", rk.amount(nully="")),
        rk.numeric("Delta factor", "delta_factor", rk.rate(4, trim=True)),
        rk.numeric("Delta lots", "delta_lots", rk.amount(4, nully="", trim=True)),
        rk.numeric("Delta USD", "delta_usd", rk.amount(nully="")),
        rk.text("Trades", "trades"), rk.text("Reason", "reason")]
    numeric_cols = [c["id"] for c in columns if c["type"] == "numeric"]
    sort_props = rk.sortable(DETAIL_TABLE_ID)
    sort_props["sort_as_null"] = list(sort_props["sort_as_null"]) + [OPTION_NOTIONAL]
    option_notional_style = [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{OPTION_NOTIONAL}'"}, **_NA_STYLE}
                             for c in ("notional_local", "notional_usd")]
    table = dash_table.DataTable(
        id=DETAIL_TABLE_ID, columns=columns, data=records, tooltip_data=tips,
        tooltip_delay=0, tooltip_duration=None,
        **sort_props,
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c["id"]}, "textAlign": "left"} for c in columns if c["type"] == "text"]
                               + [{"if": {"column_id": "reason"}, "whiteSpace": "normal", "minWidth": "220px", "maxWidth": "420px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(["lots", "units", "notional_local", "notional_usd", "delta_lots", "delta_usd"])
                               + _na_styles(numeric_cols + ["month", "first_notice"]) + option_notional_style
                               + [{"if": {"column_id": "expiry", "filter_query": '{expiry} contains "(est.)"'}, **_NA_STYLE}],
    )
    return html.Div(className="section", children=[
        html.H4("Contracts"),
        html.P("One row per open position with a non-zero net, in the engine's order (sector, commodity, expiry). "
               "Notional = lots x multiplier x the day's official price, in the contract's currency, and x the "
               "day's spot in USD; an option has none (its exposure is its delta) and sits under its underlying "
               "future's month, priced at that future. Delta lots = lots x delta factor (1 for a future or LME "
               "prompt, the share of pricing days left for an averaging contract, the option's delta for an "
               "option), what it is on hover. An expiry marked (est.) is contract-master's estimate until "
               "Bloomberg's dates are on file; an LME prompt date is the ticket's own.", className="section-kicker"),
        table])


# --------------------------------------------------------------------------- 5. currency exposure
def currency_records(result: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    records, tooltips = [], []
    for ccy, e in (result.get("currency_exposure") or {}).items():
        rec: Dict[str, Any] = {"currency": ccy, "contracts": ", ".join(e.get("contracts") or []),
                               "note": e.get("reason") or ""}
        tip: Dict[str, dict] = {}
        for col in ("pnl_local", "pnl_usd"):
            v = _num(e.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(e.get("reason") or "no P&L for this currency")
        if e.get("reason"):
            tip["note"] = _tip(e["reason"])
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def currency_section(result: Dict[str, Any]) -> html.Div:
    records, tips = currency_records(result)
    kicker = ("The P&L the open non-USD futures and options on them have built up in their own currency (a "
              "margined position holds no notional cash, so its currency exposure is its P&L), and that P&L in "
              "USD at the day's spot, both from the valuation engine.")
    if not records:
        return html.Div(className="section", children=[
            html.H4("Currency exposure of non-USD futures"),
            html.P(kicker, className="section-kicker"),
            message_box("No open non-USD commodity future or option: no currency exposure.")])
    table = dash_table.DataTable(
        id=CURRENCY_TABLE_ID,
        columns=[rk.text("Currency", "currency"), rk.numeric("P&L (local)", "pnl_local", rk.amount(nully="")),
                 rk.numeric("P&L USD", "pnl_usd", rk.amount(nully="")), rk.text("Contracts", "contracts"),
                 rk.text("Note", "note")],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(CURRENCY_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("currency", "contracts", "note")]
                               + [{"if": {"column_id": "note"}, "whiteSpace": "normal", "minWidth": "200px", "maxWidth": "420px"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["pnl_local", "pnl_usd"]) + _na_styles(["pnl_local", "pnl_usd"]),
    )
    return html.Div(className="section", children=[
        html.H4("Currency exposure of non-USD futures"), html.P(kicker, className="section-kicker"), table])


# --------------------------------------------------------------------------- 6. flat contracts
def flat_section(result: Dict[str, Any]) -> html.Details:
    flat = result.get("flat_contracts") or []
    names = {k: (v.get("name") or k) for k, v in (result.get("by_commodity") or {}).items()}
    if not flat:
        inner: Any = message_box("No open contract nets to zero.")
    else:
        with_product = any(_product(f) != FUTURE for f in flat)
        inner = dash_table.DataTable(
            id=FLAT_TABLE_ID,
            columns=([rk.text("Product", "product")] if with_product else [])
                    + [rk.text("Commodity", "commodity"), rk.text("Contract", "contract_id"),
                       rk.text("Expiry", "expiry"), rk.text("Trades", "trades")],
            data=[{"product": product_label(_product(f)),
                   "commodity": names.get(f.get("root_id"), f.get("root_id") or ""),
                   "contract_id": f.get("contract_id") or "", "expiry": f.get("expiry") or "",
                   "trades": ", ".join(str(t) for t in (f.get("trade_ids") or []))} for f in flat],
            **rk.sortable(FLAT_TABLE_ID),
            style_table={"overflowX": "auto"},
            style_cell={**_MONO, "textAlign": "left"},
            style_header={"fontWeight": "bold"},
        )
    return html.Details(id=FLAT_ID, className="section section--secondary details", open=False, children=[
        html.Summary(f"Flat contracts ({len(flat)}): open, netting to zero lots"), inner])


# --------------------------------------------------------------------------- body and shell
def body(result: Dict[str, Any], unit: str = DEFAULT_UNIT) -> html.Div:
    """The whole tab body from one `curve_positions` result, month cells in `unit`."""
    children: List[Any] = [caption_block(result)]
    if result.get("by_commodity"):
        children += [grid_section(result, unit), sector_section(result), detail_section(result)]
    else:
        children.append(message_box(
            f"No commodity position to show: {result.get('note') or 'see the gaps above'}."))
    children += [currency_section(result), flat_section(result)]
    return html.Div(className="curve-body", children=children)


def render(as_of: Optional[str], db_path, unit: str = DEFAULT_UNIT) -> Any:
    """The body for `as_of` from the database at `db_path`: one `curve_positions` call on a
    read-only connection, closed straight after. A problem is a message where the body would
    be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local, as the Risk tab does: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        result = curve_positions(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Curve positions could not be computed for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result, unit)


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row with the view switch and the body container the callback
    fills. No date picker: the tab follows the header's as-of store."""
    return html.Div(className="curve-tab", children=[
        html.Div(className="ladder-title-row", children=[
            html.H3("Curve", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(f"Follows the header's as-of date{f' ({default_date})' if default_date else ''}",
                        className="section-title")])]),
        html.Div(className="meta-line", children=[
            html.Span("Month cells in: "),
            dcc.RadioItems(id=UNIT_ID, options=[{"label": UNIT_LABELS[u], "value": u} for u in UNITS],
                           value=DEFAULT_UNIT, inline=True, persistence=True, persistence_type="session",
                           inputStyle={"marginRight": "4px", "marginLeft": "10px"})]),
        html.Div(id=BODY_ID, children=[message_box("Loading the curve positions...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """One callback: the body re-renders on the header's as-of, on every data revision, on
    the safety interval and on the view switch."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        Input(UNIT_ID, "value"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, unit=DEFAULT_UNIT):
        return render(as_of, get_db_path(), unit or DEFAULT_UNIT)
