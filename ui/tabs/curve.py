"""Curve tab: "What am I long or short, in which month?" (Commodity conversion plan, Phase 1
step 3). Rendered from `engine.curve.curve_positions` and nothing else: every position, price,
notional and P&L on the tab is that dict's; nothing here re-prices or re-reads a mark (CLAUDE.md
"Tabs as views").

Layout, top to bottom (`body`):
  1. `caption_block`: the as-of, the engine's note (no commodity future, every one flat) and
     its `reasons` list.
  2. `grid_section`: one row per commodity (`by_commodity`) in the engine's order (sector,
     root), the Sector column first, the contract months (`months`) across, then Net lots,
     Gross lots, Net units, Unit, Net USD, Gross USD (the engine's own per-commodity figures,
     whatever the switch says). The unit switch (`UNIT_ID`: lots, physical units, USD) picks
     what a month cell shows: lots are `by_commodity[...]['months']` as the engine gives them;
     units and USD are the per-contract rows' `units` / `notional_usd` summed into their month
     cell, display arithmetic on engine figures only. A cell with a contract whose figure is
     None reads "n/a" with the contract's reason on hover; an empty cell is a month with no
     position. Every cell with a position lists its contracts on hover.
  3. `sector_section`: net and gross USD per sector (`by_sector`), the book's sum pinned under
     it (n/a with the reasons when any sector is n/a).
  4. `detail_section`: the engine's `rows`, one per contract: exchange, contract, expiry
     (marked "(est.)" when contract-master's dates are ESTIMATED), first notice, lots, units,
     price and its currency, USD per unit, local and USD notional, trades and the reason.
  5. `currency_section`: the P&L the non-USD futures hold in each currency and its USD value
     (`currency_exposure`).
  6. `flat_section`: the open contracts that net to zero, collapsed.

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
UNITS = ("lots", "units", "usd")
UNIT_LABELS = {"lots": "Lots", "units": "Physical units", "usd": "USD notional"}
DEFAULT_UNIT = "lots"
MONTH_PREFIX = "m_"          # a month column's id: 'm_2026-12'

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_EST_TIP = ("estimated by contract-master (the last weekday of the contract month, a 'no later than' date): "
            "Bloomberg's contract dates are not on file yet")


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
    return rk.amount(nully="") if unit == "usd" else rk.amount(2, nully="", trim=True)


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
def _contract_line(row: Dict[str, Any], unit: str) -> str:
    lots = _num(row.get("lots"))
    s = f"{row.get('contract_id', '')}: {lots:g} lot(s)" if lots is not None else f"{row.get('contract_id', '')}"
    if unit == "units" and _num(row.get("units")) is not None:
        s += f", {row['units']:,.2f} {row.get('unit') or ''}".rstrip()
    if unit == "usd" and _num(row.get("notional_usd")) is not None:
        s += f", USD {row['notional_usd']:,.0f}"
    return s


def month_cells(result: Dict[str, Any], root_id: str, unit: str) -> Tuple[Dict[str, Any], Dict[str, dict]]:
    """({column id: value}, {column id: tooltip}) of one commodity's month cells in `unit`.

    lots: the engine's `by_commodity[root]['months']` as they stand. units / usd: the
    commodity's contract rows' `units` / `notional_usd` summed per month; a month holding a
    contract whose figure is None is "n/a" with that contract's reason. A month with no
    contract is absent (blank)."""
    rows = [r for r in (result.get("rows") or []) if r.get("root_id") == root_id]
    by_month: Dict[str, List[dict]] = {}
    for r in rows:
        key = _row_month(r)
        if key is not None:
            by_month.setdefault(key, []).append(r)
    values: Dict[str, Any] = {}
    tips: Dict[str, dict] = {}
    engine_lots = (result.get("by_commodity") or {}).get(root_id, {}).get("months") or {}
    for key in sorted(set(by_month) | set(engine_lots)):
        col = month_column(key)
        mine = by_month.get(key, [])
        lines = [_contract_line(r, unit) for r in mine]
        if unit == "lots":
            v = _num(engine_lots.get(key))
            values[col] = NA if v is None else v
            gaps = [f"{r['contract_id']}: {r['reason']}" for r in mine if r.get("reason") and "quantity" in r["reason"]]
            if v is None:
                gaps = gaps or ["no lots for this month"]
        else:
            field = "units" if unit == "units" else "notional_usd"
            missing = [r for r in mine if _num(r.get(field)) is None]
            if missing or not mine:
                values[col] = NA
                gaps = [f"{r.get('contract_id', '')}: {r.get('reason') or 'no ' + ('physical units' if unit == 'units' else 'USD notional')}"
                        for r in missing] or ["no contract row for this month"]
            else:
                values[col] = float(sum(float(r[field]) for r in mine))
                gaps = []
        text = "; ".join(lines)
        if values[col] == NA:
            text = f"{NA}: " + "; ".join(gaps) + (f" | {text}" if text else "")
        elif gaps:
            text = f"{text} | " + "; ".join(gaps)
        if text:
            tips[col] = _tip(text)
    return values, tips


def grid_records(result: Dict[str, Any], unit: str) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips) of the grid, one per commodity in the engine's order."""
    records, tooltips = [], []
    rows = result.get("rows") or []
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
        units = _num(c.get("net_units"))
        if units is None:
            rec["net_units"] = NA
            why = "; ".join(f"{r['contract_id']}: {r.get('reason') or 'no physical units'}"
                            for r in mine if _num(r.get("units")) is None)
            tip["net_units"] = _tip(why or "no physical units")
        else:
            rec["net_units"] = units
        rec["unit"] = c.get("unit") or ""
        for col in ("net_usd", "gross_usd"):
            v = _num(c.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(c.get("reason") or "no USD notional")
        notes = []
        no_month = [r["contract_id"] for r in mine if _row_month(r) is None]
        if no_month:
            notes.append(f"no contract month for {', '.join(no_month)}: in the totals, in no month column")
        if c.get("missing"):
            notes.append(f"no USD notional for {', '.join(c['missing'])}")
        rec["note"] = "; ".join(notes)
        if c.get("reason"):
            tip["note"] = _tip(c["reason"])
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def grid_columns(months: List[str], unit: str) -> List[dict]:
    fmt = _fmt(unit)
    lots = rk.amount(2, nully="", trim=True)
    return ([rk.text("Sector", "sector"), rk.text("Commodity", "commodity"), rk.text("Exchange", "exchange"),
             rk.text("Ccy", "currency")]
            + [rk.numeric(month_label(k), month_column(k), fmt) for k in months]
            + [rk.numeric("Net lots", "net_lots", lots), rk.numeric("Gross lots", "gross_lots", lots),
               rk.numeric("Net units", "net_units", lots), rk.text("Unit", "unit"),
               rk.numeric("Net USD", "net_usd", rk.amount(nully="")), rk.numeric("Gross USD", "gross_usd", rk.amount(nully="")),
               rk.text("Note", "note")])


def grid_section(result: Dict[str, Any], unit: str) -> html.Div:
    unit = unit if unit in UNITS else DEFAULT_UNIT
    months = list(result.get("months") or [])
    records, tips = grid_records(result, unit)
    columns = grid_columns(months, unit)
    month_ids = [month_column(k) for k in months]
    signed = month_ids + ["net_lots", "net_units", "net_usd"]
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
        style_data_conditional=rk.sign_styles(signed) + _na_styles(signed + ["gross_usd"]),
    )
    in_words = {"lots": "net lots", "units": "net physical units (lots x contract size, in the Unit column's unit)",
                "usd": "net USD notional (lots x multiplier x price x spot, the engine's per-contract figure)"}[unit]
    caption = (f"One row per commodity, grouped by sector; each month cell is the {in_words} of that contract "
               "month, summed over its contracts (hover lists them). An empty month cell holds no position; n/a "
               "is a figure the engine could not compute, its reason on hover. Net and gross lots, units and USD "
               "at the end are the engine's per-commodity totals, whatever the switch shows.")
    return html.Div(className="section", children=[
        html.H4(f"Positions by contract month ({UNIT_LABELS[unit].lower()})"),
        html.P(caption, className="section-kicker"),
        table])


# --------------------------------------------------------------------------- 3. sectors
def sector_records(result: Dict[str, Any]) -> Tuple[List[dict], List[dict], dict, dict]:
    """(records, tooltips, book footer record, its tooltips). The book line sums the sectors'
    engine figures, n/a with the reasons when any sector is n/a."""
    records, tips = [], []
    names = {k: (v.get("name") or k) for k, v in (result.get("by_commodity") or {}).items()}
    sums: Dict[str, Optional[float]] = {"net_usd": 0.0, "gross_usd": 0.0}
    gaps: List[str] = []
    for sector, s in (result.get("by_sector") or {}).items():
        rec: Dict[str, Any] = {"sector": _sector_label(sector),
                               "commodities": ", ".join(names.get(c, c) for c in (s.get("commodities") or []))}
        tip: Dict[str, dict] = {}
        for col in ("net_usd", "gross_usd"):
            v = _num(s.get(col))
            if v is None:
                rec[col] = NA
                tip[col] = _tip(s.get("reason") or "no USD notional")
                sums[col] = None
            else:
                rec[col] = v
                if sums[col] is not None:
                    sums[col] += v
        if s.get("reason"):
            gaps.append(f"{_sector_label(sector)}: {s['reason']}")
        records.append(rec)
        tips.append(tip)
    footer: Dict[str, Any] = {"sector": "Book", "commodities": f"{len(names)} commodit{'y' if len(names) == 1 else 'ies'}"}
    footer_tip: Dict[str, dict] = {}
    for col in ("net_usd", "gross_usd"):
        if sums[col] is None:
            footer[col] = NA
            footer_tip[col] = _tip("; ".join(gaps) or "a sector has no USD notional")
        else:
            footer[col] = sums[col]
    return records, tips, footer, footer_tip


def sector_section(result: Dict[str, Any]) -> html.Div:
    records, tips, footer, footer_tip = sector_records(result)
    usd = rk.amount(nully="")
    table = dash_table.DataTable(
        id=SECTOR_TABLE_ID,
        columns=[rk.text("Sector", "sector"), rk.numeric("Net USD", "net_usd", usd),
                 rk.numeric("Gross USD", "gross_usd", usd), rk.text("Commodities", "commodities")],
        data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(SECTOR_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("sector", "commodities")],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(["net_usd"], bold=True) + _na_styles(["net_usd", "gross_usd"]),
    )
    footer_style = [{"if": {"filter_query": "{sector} = 'Book'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    return html.Div(className="section", children=[
        html.H4("Net outright by sector (USD)"),
        html.P("Net = the sector's contracts' USD notionals summed with their signs (a calendar spread nets to its "
               "leftover outright); gross = their absolute values summed. The Book line adds the sectors up; any "
               "sector n/a makes it n/a.", className="section-kicker"),
        rk.with_footer(table, [footer], footer_style=footer_style, footer_tooltips=[footer_tip])])


# --------------------------------------------------------------------------- 4. the contracts
def detail_records(result: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    records, tooltips = [], []
    for r in result.get("rows") or []:
        reason = r.get("reason") or ""
        tip: Dict[str, dict] = {}
        est = r.get("dates_source") == ESTIMATED
        rec: Dict[str, Any] = {
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
        if est:
            tip["expiry"] = _tip(_EST_TIP)
        if r.get("first_notice"):
            rec["first_notice"] = r["first_notice"]
        else:
            rec["first_notice"] = NA
            tip["first_notice"] = _tip(
                "no first notice date until Bloomberg's contract dates are on file (contract-master never estimates it)"
                if est or not r.get("dates_source") else "Bloomberg gives no first notice date for this contract")
        for col, why_default in (("units", "no physical units"), ("price", "no price"),
                                 ("usd_per_unit", "no USD per unit"), ("notional_local", "no local notional"),
                                 ("notional_usd", "no USD notional")):
            v = _num(r.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(reason or why_default)
        if r.get("usd_source"):
            tip.setdefault("usd_per_unit", _tip(r["usd_source"]))
        if reason:
            tip["reason"] = _tip(reason)
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def detail_section(result: Dict[str, Any]) -> html.Div:
    records, tips = detail_records(result)
    lots = rk.amount(2, nully="", trim=True)
    columns = [rk.text("Sector", "sector"), rk.text("Commodity", "commodity"), rk.text("Exchange", "exchange"),
               rk.text("Contract", "contract_id"), rk.text("Month", "month"), rk.text("Expiry", "expiry"),
               rk.text("First notice", "first_notice"), rk.numeric("Lots", "lots", lots),
               rk.numeric("Units", "units", lots), rk.text("Unit", "unit"),
               rk.numeric("Price", "price", rk.rate(6, trim=True)), rk.text("Ccy", "currency"),
               rk.text("Price source", "price_source"), rk.numeric("USD per unit", "usd_per_unit", rk.rate(6, trim=True)),
               rk.numeric("Notional (local)", "notional_local", rk.amount(nully="")),
               rk.numeric("Notional USD", "notional_usd", rk.amount(nully="")),
               rk.text("Trades", "trades"), rk.text("Reason", "reason")]
    numeric_cols = [c["id"] for c in columns if c["type"] == "numeric"]
    table = dash_table.DataTable(
        id=DETAIL_TABLE_ID, columns=columns, data=records, tooltip_data=tips,
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(DETAIL_TABLE_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c["id"]}, "textAlign": "left"} for c in columns if c["type"] == "text"]
                               + [{"if": {"column_id": "reason"}, "whiteSpace": "normal", "minWidth": "220px", "maxWidth": "420px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(["lots", "units", "notional_local", "notional_usd"])
                               + _na_styles(numeric_cols + ["month", "first_notice"])
                               + [{"if": {"column_id": "expiry", "filter_query": '{expiry} contains "(est.)"'}, **_NA_STYLE}],
    )
    return html.Div(className="section", children=[
        html.H4("Contracts"),
        html.P("One row per open contract with a non-zero net, in the engine's order (sector, commodity, expiry). "
               "Notional = lots x multiplier x the day's official price, in the contract's currency, and x the "
               "day's spot in USD. An expiry marked (est.) is contract-master's estimate until Bloomberg's dates "
               "are on file.", className="section-kicker"),
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
    kicker = ("The P&L the open non-USD futures have built up in their own currency (a margined future holds "
              "no notional cash, so its currency exposure is its P&L), and that P&L in USD at the day's spot, "
              "both from the valuation engine.")
    if not records:
        return html.Div(className="section", children=[
            html.H4("Currency exposure of non-USD futures"),
            html.P(kicker, className="section-kicker"),
            message_box("No open non-USD commodity future: no currency exposure.")])
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
        inner = dash_table.DataTable(
            id=FLAT_TABLE_ID,
            columns=[rk.text("Commodity", "commodity"), rk.text("Contract", "contract_id"),
                     rk.text("Expiry", "expiry"), rk.text("Trades", "trades")],
            data=[{"commodity": names.get(f.get("root_id"), f.get("root_id") or ""),
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
    """The static shell: a title row with the unit switch and the body container the callback
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
    the safety interval and on the unit switch."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        Input(UNIT_ID, "value"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, unit=DEFAULT_UNIT):
        return render(as_of, get_db_path(), unit or DEFAULT_UNIT)
