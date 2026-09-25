"""Curve tab: "What am I long or short, in which month?" (Commodity conversion plan, Phase 1
step 3; options on futures, LME prompts and averaging contracts since Phase 5; cleaned up under
"Screens redesign plan" Phase A, 2026-09-25). Rendered from `engine.curve.curve_positions` and
nothing else: every position, price, notional, delta and P&L on the tab is that dict's; nothing
here re-prices or re-reads a mark (CLAUDE.md "Tabs as views").

Numbers first (Screens redesign plan, Decisions): a section's definitions sit on hover of its
title (`formatting.about`), never as a paragraph above the table; the engine's reasons are
gathered in one collapsed "Data issues (N)" drawer (`formatting.issues_drawer`); a note on a row
is a short marker with its sentence on hover; USD money on the grid and the sector table is in
k / m (`ranking.amount_short` fed through `ranking.whole_units`, the full figure on hover);
lots and units keep their figures, and the Contracts table keeps full figures.

Layout, top to bottom (`body`):
  1. `caption_block`: the as-of, the engine's note (no commodity future, every one flat) and
     the Data issues drawer of its `reasons`.
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
     note: the averaging days left, say). The Notes column holds short markers ("options",
     "avg", "no USD 1") with their sentences on hover.
     The month cells are a diverging heatmap (Screens redesign plan, Phase C; `heat_styles`):
     long green, short red, white at zero, the shade by the cell's quantile of |value| among
     the view's month cells (so one huge cell does not wash the rest out); an n/a cell is left
     unshaded. A calendar spread reads as a green / red pair.
  2b. `curve_section`: one commodity's curve with its positions under it. A selector
     (`SELECT_ID`, defaulting to the commodity with the largest gross USD) and a click on a
     grid row pick the commodity; one figure, two rows on a shared contract-month axis: the
     research app's settlement curve for the root (`engine.risk.commodity_history.
     research_curve`, `raw_settle`, Bloomberg's quoted scale, so it sits on our marks' scale)
     as a thin line labelled "research curve (<date>)", our official prices of the contracts
     held (the engine rows' `price`) as markers labelled "our official marks", and under them
     the engine's lots per month as bars (long green, short red), with its delta lots per
     month beside them when options or averaging contracts make the two differ. Every point is
     a figure from one of the two sources, nothing computed; the research curve is context,
     never a mark. With no research curve the marks and bars are drawn alone and one quiet line
     says why.
  3. `sector_section`: net and gross USD notional and net and gross USD delta per sector
     (`by_sector`), the book's sum pinned under it (n/a with the reasons when any sector is n/a).
  4. `detail_section`: the engine's `rows`, one per position, in the columns a trader reads
     (product when the book holds more than one, commodity, contract, month, expiry marked
     "(est.)" when contract-master's dates are ESTIMATED, lots, units, price, notional USD,
     delta lots, delta USD), so it fits 1680 px. The rest (sector, exchange, first notice, price
     source, USD per unit, local notional, delta factor, trades, reason) is on hover of the
     row's cells and in hidden columns the table's own "Toggle Columns" button shows
     (`DETAIL_MORE_COLUMNS`, the choice kept in the browser session).
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

import bisect
import calendar
import datetime as dt
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from dash import Input, Output, State, dash_table, dcc, html, no_update

from data.contracts import get_root
from engine.curve import curve_positions
from engine.risk.commodity_history import research_curve
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import about, issues_drawer, marker
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
CURVE_SECTION_ID = "curve-commodity"     # the one-commodity curve panel under the grid
SELECT_ID = "curve-select"               # its commodity selector (a grid row click sets it)
CHART_ID = "curve-chart"                 # the container the selector's callback refills
GRAPH_ID = "curve-chart-graph"
CHART_STORE_ID = "curve-chart-data"      # every commodity's chart figures from the engine, in the body
SELECTED_ID = "curve-selected"           # the commodity picked, kept across re-renders (session)

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
UNIT_WORDS = {"lots": "lots", "units": "physical units", "usd": "USD notional",
              "delta_lots": "delta lots", "delta_usd": "delta USD"}     # the same, inside a sentence
DEFAULT_UNIT = "lots"
MONTH_PREFIX = "m_"          # a month column's id: 'm_2026-12'
USD_VIEWS = ("usd", "delta_usd")                  # the views whose month cells are USD money (k / m)
GRID_USD_COLUMNS = ("net_usd", "gross_usd", "net_delta_usd", "gross_delta_usd")
# The Contracts table's columns behind its "Toggle Columns" button (hidden until asked for;
# every one of them is also on hover of the row's visible cells).
DETAIL_MORE_COLUMNS = ("sector", "exchange", "first_notice", "price_source", "usd_per_unit",
                       "notional_local", "delta_factor", "trades", "reason")
NOTE_SEP = " \u00b7 "        # between two markers in a Notes cell

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
    """'2026-09-15' -> 'Tue 15 Sep 2026 (2026-09-15)'."""
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%a} {d.day} {d:%b %Y} ({iso})"


def _full_usd(v: float) -> str:
    """The full figure behind a k / m cell: 'USD 1,650,590' / 'USD -51,018'."""
    return f"USD {v:,.0f}"


def _add_full_usd_tips(records: List[dict], tips: List[dict], columns) -> None:
    """Every numeric USD cell shown in k / m without a tooltip of its own gets its full figure
    on hover (a cell that already has one keeps it: n/a reasons, contract lists)."""
    for rec, tip in zip(records, tips):
        for col in columns:
            v = rec.get(col)
            if isinstance(v, float) and col not in tip:
                tip[col] = _tip(_full_usd(v))


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
    """The month cells' format: lots and units keep up to 2 decimals, USD money is k / m."""
    return rk.amount_short(nully="") if unit in USD_VIEWS else rk.amount(2, nully="", trim=True)


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


def issue_items(result: Dict[str, Any]) -> List[Any]:
    """The engine's `reasons`, once each, in its order, for the Data issues drawer: a reason
    that opens with a contract on the tab ('CUZ26 Comdty: no SPOT ...') is a (contract,
    sentence) pair, anything else the sentence as it stands."""
    contracts = {r.get("contract_id") for r in (result.get("rows") or []) if r.get("contract_id")}
    items: List[Any] = []
    seen = set()
    for reason in result.get("reasons") or []:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        head, sep, rest = reason.partition(": ")
        items.append((head, rest) if sep and head in contracts else reason)
    return items


def caption_block(result: Dict[str, Any]) -> html.Div:
    children: List[Any] = [html.Div(className="meta-line", children=[html.Span(line) for line in caption_lines(result)])]
    drawer = issues_drawer(issue_items(result))
    if drawer is not None:
        children.append(drawer)
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
        # "id" is the DataTable's row id: a click on the row names its commodity whatever the sort
        rec: Dict[str, Any] = {"id": root_id, "sector": _sector_label(c.get("sector")),
                               "commodity": c.get("name") or root_id, "root_id": root_id, "exchange": c.get("exchange") or "", "currency": c.get("currency") or ""}
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
        markers = note_markers(c, mine, delta_view)
        rec["note"] = NOTE_SEP.join(short for short, _ in markers)
        sentences = [sentence for _, sentence in markers]
        why = c.get("delta_reason") if delta_view else c.get("reason")
        if why and why not in sentences:
            sentences.append(why)
        if sentences:
            tip["note"] = _tip("; ".join(sentences))
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def note_markers(c: Dict[str, Any], mine: List[dict], delta_view: bool) -> List[Tuple[str, str]]:
    """The Notes cell of one commodity as (marker, sentence) pairs: the marker is what the cell
    shows, the sentences are its hover. Outright views: a contract with no USD notional, and
    options (in the delta views only). Delta views: a contract with no delta, averaging
    contracts at their reduced delta. Both: a contract with no contract month."""
    out: List[Tuple[str, str]] = []
    no_month = [r["contract_id"] for r in mine if _row_month(r) is None]
    if no_month:
        out.append((f"no month {len(no_month)}",
                    f"no contract month for {', '.join(no_month)}: in the totals, in no month column"))
    if delta_view:
        if c.get("delta_missing"):
            out.append((f"no delta {len(c['delta_missing'])}", f"no delta for {', '.join(c['delta_missing'])}"))
        averaging = [r["contract_id"] for r in mine if _product(r) == FUTURE and r.get("note")]
        if averaging:
            out.append(("avg", f"averaging, reduced delta: {', '.join(averaging)}"))
    else:
        if c.get("missing"):
            out.append((f"no USD {len(c['missing'])}", f"no USD notional for {', '.join(c['missing'])}"))
        products = c.get("products") or sorted({_product(r) for r in mine})
        if OPTION in products:
            out.append(("options", "options: in the delta views only (an option is not a lot of the future; "
                                   "its exposure is its delta)"))
    return out


def grid_columns(months: List[str], unit: str) -> List[dict]:
    fmt = _fmt(unit)
    lots = rk.amount(2, nully="", trim=True)
    usd = rk.amount_short(nully="")
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
    return head + cells + tail + [rk.text("Notes", "note")]


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
    caption += (" USD in k / m, the full figure on hover. Notes are short markers, their sentences on hover."
                " The month cells are shaded green for long and red for short, deeper for a larger position "
                "(by its rank among the month cells of this view), white at zero; n/a is not shaded. Click a "
                "row to chart that commodity's curve below.")
    return caption


HEAT_POS = (26, 127, 75)       # --pos of ui/assets/style.css
HEAT_NEG = (192, 57, 43)       # --neg
HEAT_MIN, HEAT_MAX = 0.10, 0.60  # the shade's opacity at the smallest and the largest |value|
HEAT_INK = "#1b2333"           # --text: a shaded cell's figure stays dark, the shade carries the sign
ACTIVE_STYLE = {"if": {"state": "active"}, "backgroundColor": "rgba(15, 31, 61, 0.06)",
                "border": "1px solid #0f1f3d"}


def heat_level(v: float, magnitudes: List[float]) -> float:
    """The quantile of |v| among `magnitudes` (sorted): the share of cells whose |value| is at
    most |v|, in (0, 1]. By rank, not by size, so one huge cell does not wash the rest out."""
    if not magnitudes:
        return 1.0
    return bisect.bisect_right(magnitudes, abs(v)) / len(magnitudes)


def heat_colour(v: float, magnitudes: List[float]) -> Optional[str]:
    """The cell's background: green for long, red for short, the opacity by `heat_level`; None
    (white) at zero."""
    if not v:
        return None
    alpha = HEAT_MIN + (HEAT_MAX - HEAT_MIN) * heat_level(v, magnitudes)
    r, g, b = HEAT_POS if v > 0 else HEAT_NEG
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def heat_styles(records: List[dict], columns: List[str]) -> List[dict]:
    """`style_data_conditional` rules shading the month cells as a diverging heatmap over the
    view shown: one rule per distinct (column, value), matched on the value itself so the shade
    follows the cell through any sort. Only numbers are shaded: an empty cell, a zero and an
    "n/a" (a string) are left as they are. Display only: the figures are the records'."""
    cells = [(c, r[c]) for r in records for c in columns if isinstance(r.get(c), float) and r[c] != 0]
    magnitudes = sorted(abs(v) for _, v in cells)
    out: List[dict] = []
    seen = set()
    for col, v in cells:
        if (col, v) in seen:
            continue
        seen.add((col, v))
        out.append({"if": {"column_id": col, "filter_query": f"{{{col}}} = {v!r}"},
                    "backgroundColor": heat_colour(v, magnitudes), "color": HEAT_INK})
    return out


def grid_section(result: Dict[str, Any], unit: str) -> html.Div:
    unit = unit if unit in UNITS else DEFAULT_UNIT
    months = list(result.get("months") or [])
    records, tips = grid_records(result, unit)
    columns = grid_columns(months, unit)
    month_ids = [month_column(k) for k in months]
    money = list(GRID_USD_COLUMNS) + (month_ids if unit in USD_VIEWS else [])
    _add_full_usd_tips(records, tips, money)
    records = rk.whole_units(records, money)       # k / m display: whole units, never "400m" (milli)
    if unit in DELTA_UNITS:
        signed, other = month_ids + ["net_delta_lots", "net_delta_usd"], ["gross_delta_usd"]
    else:
        signed, other = month_ids + ["net_lots", "net_units", "net_usd"], ["gross_usd"]
    table = dash_table.DataTable(
        id=GRID_ID,
        columns=columns,
        data=records,
        tooltip_data=tips,
        tooltip_header={month_column(k): f"contract month {k}, in {UNIT_WORDS[unit]}" for k in months},
        tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(GRID_ID),
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("sector", "commodity", "exchange", "currency", "unit", "note")]
                               + [{"if": {"column_id": "note"}, "color": "var(--muted)"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        # later rules win: the heat shade over the sign colour and over the active-cell tint
        style_data_conditional=([ACTIVE_STYLE] + rk.sign_styles(signed) + _na_styles(signed + other)
                                + heat_styles(records, month_ids)),
    )
    return html.Div(className="section", children=[
        about(f"Positions by contract month ({UNIT_WORDS[unit]})", grid_caption(result, unit)),
        table])


# --------------------------------------------------------------------------- 2b. one commodity's curve
RESEARCH_TRACE = "research curve"        # + " (<settlement date>)"
MARKS_TRACE = "our official marks"
LOTS_TRACE = "position (lots)"
DELTA_TRACE = "delta lots"
_POS_COLOUR, _NEG_COLOUR = "#1a7f4b", "#c0392b"   # --pos / --neg
_NAVY, _GOLD, _MUTED = "#0f1f3d", "#c9a227", "#6b7280"
_NOTE_STYLE = {"color": "var(--muted)", "fontSize": "12px", "margin": "2px 0"}   # a quiet line under the chart
CURVE_ABOUT = (
    "One commodity at a time: pick it here or click its row in the grid (the default is the commodity with the "
    "largest gross USD). The line is the research app's settlement curve for the root on its latest settlement "
    "on or before the as-of, as Bloomberg quotes it (raw_settle), so it sits on the same scale as our marks; it "
    "is research context, never a mark and never in P&L or delta. The markers are our official prices of the "
    "contracts we hold, the engine's figures (an option's is its underlying future's). The bars under them are "
    "our net lots per contract month (futures and LME prompts), long green, short red; where options or "
    "averaging contracts are held the delta lots per month stand beside them, outlined. Nothing is computed "
    "here: every point is a figure from one of the two sources.")


def default_root(result: Dict[str, Any]) -> Optional[str]:
    """The commodity the panel opens on: the largest gross USD notional (the engine's), a
    commodity with none after those that have one (then by gross delta USD), else the first."""
    by = result.get("by_commodity") or {}
    if not by:
        return None

    def rank(item):
        i, (_root, c) = item
        gross, gross_delta = _num(c.get("gross_usd")), _num(c.get("gross_delta_usd"))
        return (gross is not None, gross or 0.0, gross_delta or 0.0, -i)

    return max(enumerate(by.items()), key=rank)[1][0]


def _root_terms(root_id: str) -> Dict[str, Any]:
    """contract-master's quote unit and price scale of a root, {} when it has none."""
    try:
        root = get_root(root_id)
    except (KeyError, OSError, ValueError):
        return {}
    return {"quote_unit": root.quote_unit or "", "price_scale": float(root.price_scale)}


def chart_payload(result: Dict[str, Any], root_id: str) -> Dict[str, Any]:
    """What the chart of one commodity draws from the engine, as given: its contracts' official
    prices (a future's or LME prompt's own; an option's is its underlying future's, drawn once,
    and only when that future is not held itself), its lots per month (`months`) and, when they
    differ, its delta lots per month (`delta_months`). A contract with no price or no month is
    not drawn and is named with its reason."""
    c = (result.get("by_commodity") or {}).get(root_id, {})
    rows = [r for r in (result.get("rows") or []) if r.get("root_id") == root_id]
    marks: List[dict] = []
    missing: List[str] = []
    seen = set()
    for r in [r for r in rows if _product(r) != OPTION] + [r for r in rows if _product(r) == OPTION]:
        option = _product(r) == OPTION
        cid = (r.get("underlying_id") if option else r.get("contract_id")) or r.get("contract_id") or ""
        if cid in seen:
            continue
        seen.add(cid)
        month, price = _row_month(r), _num(r.get("price"))
        if month is None or price is None:
            why = r.get("reason") or ("no contract month" if month is None else "no official price")
            missing.append(f"{cid}: {why}")
            continue
        marks.append({"contract_id": cid, "month": month, "price": price, "source": r.get("price_source") or "",
                      "product": _product(r), "held": not option})
    lots = {k: _num(v) for k, v in sorted((c.get("months") or {}).items())}
    delta = {k: _num(v) for k, v in sorted((c.get("delta_months") or {}).items())}
    return {"root_id": root_id, "name": c.get("name") or root_id, "exchange": c.get("exchange") or "",
            "currency": c.get("currency") or "", "unit": c.get("unit") or "", **_root_terms(root_id),
            "marks": marks, "missing": missing, "lots": lots, "delta": delta,
            "show_delta": bool(delta) and delta != lots, "delta_reason": c.get("delta_reason") or ""}


def chart_store(result: Dict[str, Any]) -> Dict[str, Any]:
    """The body's store: every commodity's chart payload (engine figures only, cheap) and the
    as-of; the research curve is read only for the commodity shown."""
    return {"as_of": result.get("as_of"),
            "roots": {root: chart_payload(result, root) for root in (result.get("by_commodity") or {})}}


def _month_x(key: str) -> str:
    """'2026-12' -> '2026-12-01', a contract month on the chart's date axis."""
    return f"{key}-01"


def price_axis_title(payload: Dict[str, Any], research: Dict[str, Any]) -> str:
    """The price axis's unit: Bloomberg's quoted price, named through the quote unit and price
    scale (contract-master's, else the research app's)."""
    unit = payload.get("quote_unit") or research.get("unit") or payload.get("currency") or ""
    scale = payload.get("price_scale") if payload.get("price_scale") is not None else research.get("price_scale")
    if scale is None or float(scale) == 1.0:
        return f"{unit}, as quoted" if unit else "price as Bloomberg quotes it"
    return f"quoted price (x {float(scale):g} = {unit})"


def research_points(research: Dict[str, Any]) -> List[dict]:
    """The research rows that carry a month and a raw settlement, in the research app's order."""
    return [r for r in (research.get("rows") or [])
            if r.get("month") and isinstance(r.get("raw_settle"), (int, float))]


def curve_figure(payload: Dict[str, Any], research: Dict[str, Any]):
    """One figure, two rows on a shared contract-month axis: the research curve (line, `raw_settle`)
    and our official marks (markers) above; our lots per month (bars, long green, short red) and,
    when they differ, the delta lots per month (outlined bars) below. Every y is a source's figure."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.66, 0.34], vertical_spacing=0.06)
    points = research_points(research)
    if points:
        unit = research.get("unit") or ""
        fig.add_trace(go.Scatter(
            x=[_month_x(r["month"]) for r in points], y=[r["raw_settle"] for r in points],
            mode="lines+markers", name=f"{RESEARCH_TRACE} ({research.get('date') or 'no date'})",
            line={"color": _MUTED, "width": 1.2}, marker={"size": 3, "color": _MUTED},
            customdata=[[r.get("contract_id") or "", r.get("expiry") or NA, r.get("settle")] for r in points],
            hovertemplate=("%{customdata[0]} (research)<br>%{x|%b %Y}: %{y:,.6g}"
                           + (f"<br>= %{{customdata[2]:,.6g}} {unit}" if unit else "")
                           + "<br>last trade %{customdata[1]}<extra></extra>")), row=1, col=1)
    marks = payload.get("marks") or []
    if marks:
        fig.add_trace(go.Scatter(
            x=[_month_x(m["month"]) for m in marks], y=[m["price"] for m in marks],
            mode="markers", name=MARKS_TRACE,
            marker={"size": 10, "symbol": "diamond", "color": _GOLD, "line": {"color": _NAVY, "width": 1.5}},
            customdata=[[m["contract_id"], m.get("source") or "", "" if m.get("held") else " (underlying of options held)"]
                        for m in marks],
            hovertemplate="%{customdata[0]}%{customdata[2]}<br>%{x|%b %Y}: %{y:,.6g}<br>%{customdata[1]}<extra></extra>"),
            row=1, col=1)
    lots = [(k, v) for k, v in (payload.get("lots") or {}).items() if v is not None]
    if lots:
        fig.add_trace(go.Bar(
            x=[_month_x(k) for k, _ in lots], y=[v for _, v in lots], name=LOTS_TRACE,
            marker_color=[_POS_COLOUR if v >= 0 else _NEG_COLOUR for _, v in lots],
            hovertemplate="%{x|%b %Y}: %{y:,.4g} lot(s)<extra></extra>"), row=2, col=1)
    if payload.get("show_delta"):
        delta = [(k, v) for k, v in (payload.get("delta") or {}).items() if v is not None]
        if delta:
            fig.add_trace(go.Bar(
                x=[_month_x(k) for k, _ in delta], y=[v for _, v in delta], name=DELTA_TRACE,
                marker={"color": "rgba(0,0,0,0)",
                        "line": {"color": [_POS_COLOUR if v >= 0 else _NEG_COLOUR for _, v in delta], "width": 1.5}},
                hovertemplate="%{x|%b %Y}: %{y:,.4g} delta lot(s)<extra></extra>"), row=2, col=1)
    fig.update_layout(
        height=440, margin={"l": 8, "r": 16, "t": 28, "b": 24}, barmode="group", bargap=0.35,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"size": 11.5, "color": HEAT_INK}, hovermode="closest",
        legend={"orientation": "h", "x": 0, "y": 1.08, "font": {"size": 11}},
    )
    fig.update_xaxes(type="date", tickformat="%b %y", showgrid=False, fixedrange=True,
                     tickfont={"size": 10.5, "color": _MUTED})
    fig.update_yaxes(title_text=price_axis_title(payload, research), row=1, col=1, automargin=True, fixedrange=True,
                     gridcolor="#eef0f4", tickformat=",.6~g", tickfont={"size": 10.5, "color": _MUTED})
    fig.update_yaxes(title_text="lots", row=2, col=1, automargin=True, fixedrange=True, zeroline=True,
                     zerolinecolor="#9aa3b2", gridcolor="#eef0f4", tickfont={"size": 10.5, "color": _MUTED})
    return fig


def curve_notes(payload: Dict[str, Any], research: Dict[str, Any]) -> List[str]:
    """The quiet lines under the chart: why there is no research curve, what the research app left
    out, a price scale the two sources disagree on, the contracts not drawn and the months with no
    lots. Each is a sentence from its source."""
    notes: List[str] = []
    if not research_points(research):
        notes.append(f"No research curve: {research.get('reason') or 'the research app has no settlements for it'}. "
                     "Our official marks and positions only.")
    elif research.get("note"):
        notes.append(f"Research curve: {research['note']}.")
    ours, theirs = payload.get("price_scale"), research.get("price_scale")
    if research_points(research) and ours is not None and theirs is not None and float(ours) != float(theirs):
        notes.append(f"The research app's price scale ({float(theirs):g}) differs from contract-master's "
                     f"({float(ours):g}): its curve may not sit on our marks' scale.")
    if payload.get("missing"):
        notes.append(f"Not drawn: {'; '.join(payload['missing'])}.")
    no_lots = [k for k, v in (payload.get("lots") or {}).items() if v is None]
    if no_lots:
        notes.append(f"No lots for {', '.join(month_label(k) for k in no_lots)}: see the grid's n/a cells.")
    if payload.get("show_delta"):
        no_delta = [k for k, v in (payload.get("delta") or {}).items() if v is None]
        if no_delta:
            notes.append(f"No delta lots for {', '.join(month_label(k) for k in no_delta)}: "
                         f"{payload.get('delta_reason') or 'a contract has no delta'}.")
    return notes


def curve_children(payload: Optional[Dict[str, Any]], as_of: Optional[str]) -> List[Any]:
    """The chart of one commodity and its quiet lines. The research curve is read here, for this
    commodity only (the history behind it is cached); its failure is a line, never a broken panel."""
    if not payload:
        return [message_box("No commodity to chart.")]
    try:
        research = research_curve(payload["root_id"], as_of)
    except Exception as exc:  # noqa: BLE001 -- research_curve never raises; a reason on screen if it did
        research = {"rows": [], "reason": f"the research curve could not be read ({type(exc).__name__}: {exc})"}
    research = research or {}
    # the research source (database, root, settlement date) on hover of the chart
    children: List[Any] = [html.Div(title=research.get("source") or "", children=[
        dcc.Graph(id=GRAPH_ID, figure=curve_figure(payload, research), config={"displayModeBar": False})])]
    children += [html.P(line, className="curve-note", style=_NOTE_STYLE) for line in curve_notes(payload, research)]
    return children


def curve_section(result: Dict[str, Any], selected: Optional[str] = None) -> html.Div:
    """The panel under the grid: the selector (the kept pick when it is still in the book, else
    `default_root`), the store of every commodity's engine figures, and the chart."""
    by = result.get("by_commodity") or {}
    chosen = selected if selected in by else default_root(result)
    store = chart_store(result)
    options = [{"label": f"{c.get('name') or root}" + (f" ({c['exchange']})" if c.get("exchange") else ""),
                "value": root} for root, c in by.items()]
    return html.Div(id=CURVE_SECTION_ID, className="section", children=[
        html.Div(style={"display": "flex", "alignItems": "center", "gap": "12px", "flexWrap": "wrap"}, children=[
            about("Curve and positions", CURVE_ABOUT),
            dcc.Dropdown(id=SELECT_ID, options=options, value=chosen, clearable=False, searchable=True,
                         style={"minWidth": "340px", "fontSize": "12.5px"})]),
        dcc.Store(id=CHART_STORE_ID, data=store),
        html.Div(id=CHART_ID, children=curve_children(store["roots"].get(chosen), store.get("as_of")))])


def root_from_cell(active_cell: Optional[dict]) -> Optional[str]:
    """The commodity of a clicked grid cell: its row id (the record's "id", the root)."""
    return (active_cell or {}).get("row_id") or None


def show_curve(root_id: Optional[str], store: Optional[dict]) -> Tuple[Any, Any]:
    """(chart children, the pick to keep) for the selector's `root_id`."""
    if not root_id:
        return no_update, no_update
    store = store or {}
    return curve_children((store.get("roots") or {}).get(root_id), store.get("as_of")), root_id


# --------------------------------------------------------------------------- 3. sectors
SECTOR_ABOUT = ("Net = the sector's contracts' USD notionals summed with their signs (a calendar spread nets to its "
                "leftover outright); gross = their absolute values summed; both over futures and LME prompts. The "
                "delta columns are the same over every product, an option at its delta and an averaging contract "
                "at its reduced delta. The Book line adds the sectors up; any sector n/a makes it n/a. USD in "
                "k / m; the full figure on hover.")
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
    usd = rk.amount_short(nully="")
    numeric = [col for col, _, _ in _SECTOR_COLS]
    _add_full_usd_tips(records, tips, numeric)
    _add_full_usd_tips([footer], [footer_tip], numeric)
    records, footer = rk.whole_units(records, numeric), rk.whole_units([footer], numeric)[0]
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
        about("Net outright by sector (USD)", SECTOR_ABOUT),
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
        _hover_the_hidden(r, rec, tip)
        records.append(rec)
        tooltips.append(tip)
    return records, tooltips


def _extend(tip: Dict[str, dict], col: str, *lines: str) -> None:
    """Add `lines` to the hover of `col`, after what is already there."""
    parts = ([tip[col]["value"]] if col in tip else []) + [t for t in lines if t]
    if parts:
        tip[col] = _tip(" | ".join(parts))


def _hover_the_hidden(r: Dict[str, Any], rec: Dict[str, Any], tip: Dict[str, dict]) -> None:
    """The figures of the columns behind "Toggle Columns" (`DETAIL_MORE_COLUMNS`), on hover of
    the visible cell they explain, so nothing leaves the screen with them: sector, exchange
    and the full name on the commodity; the trades and the reason on the contract; the first
    notice on the expiry; the price source and USD per unit on the price; the local notional on
    the notional USD; the delta factor on the delta lots. Display only: engine figures, as given."""
    _extend(tip, "commodity", NOTE_SEP.join(t for t in (rec["sector"], rec["exchange"], rec["commodity"]) if t))
    _extend(tip, "contract_id", f"trades: {rec['trades']}" if rec["trades"] else "", rec["reason"])
    fn = rec["first_notice"]
    fn_line = f"first notice: {fn}" if fn != NA else f"first notice: {NA} ({tip['first_notice']['value']})"
    _extend(tip, "expiry", fn_line)
    ccy = rec["currency"]
    usd_per = rec["usd_per_unit"]
    usd_line = (f"USD per {ccy or 'unit'}: {usd_per:.6g}" + (f" ({r['usd_source']})" if r.get("usd_source") else "")
                if isinstance(usd_per, float) else f"USD per unit: {NA}")
    _extend(tip, "price", f"source: {rec['price_source']}" if rec["price_source"] else "",
            usd_line if ccy and ccy != "USD" else "")
    local = rec["notional_local"]
    if isinstance(local, float) and ccy and ccy != "USD":
        _extend(tip, "notional_usd", f"local notional: {ccy} {local:,.0f}", usd_line)
    factor = rec["delta_factor"]
    if isinstance(factor, float) and factor != 1.0 and isinstance(rec["lots"], float):
        _extend(tip, "delta_lots", f"{rec['lots']:g} lot(s) x delta factor {factor:.4g}")


def show_product_column(result: Dict[str, Any]) -> bool:
    """The Product column shows only when the book holds more than one product."""
    return len(result.get("products_present") or []) > 1


DETAIL_ABOUT = (
    "One row per open position with a non-zero net, in the engine's order (sector, commodity, expiry). "
    "Notional = lots x multiplier x the day's official price, in the contract's currency, and x the "
    "day's spot in USD; an option has none (its exposure is its delta) and sits under its underlying "
    "future's month, priced at that future. Delta lots = lots x delta factor (1 for a future or LME "
    "prompt, the share of pricing days left for an averaging contract, the option's delta for an "
    "option), what it is on hover. An expiry marked (est.) is contract-master's estimate until "
    "Bloomberg's dates are on file; an LME prompt date is the ticket's own. Sector, exchange, first "
    "notice, price source, USD per unit, local notional, delta factor, trades and reason are on hover of "
    "the row's cells, and in the columns the table's Toggle Columns button shows.")


def detail_columns(result: Dict[str, Any]) -> List[dict]:
    """The Contracts table's columns: the ones a trader reads first, in sight at 1680 px, then
    `DETAIL_MORE_COLUMNS` (hideable, hidden until the table's Toggle Columns button shows them)."""
    lots = rk.amount(2, nully="", trim=True)
    more = {"hideable": True}
    return ([rk.text("Product", "product")] if show_product_column(result) else []) + [
        rk.text("Commodity", "commodity"), rk.text("Contract", "contract_id"), rk.text("Month", "month"),
        rk.text("Expiry", "expiry"), rk.numeric("Lots", "lots", lots),
        rk.numeric("Units", "units", lots), rk.text("Unit", "unit"),
        rk.numeric("Price", "price", rk.rate(6, trim=True)), rk.text("Ccy", "currency"),
        rk.numeric("Notional USD", "notional_usd", rk.amount(nully="")),
        rk.numeric("Delta lots", "delta_lots", rk.amount(4, nully="", trim=True)),
        rk.numeric("Delta USD", "delta_usd", rk.amount(nully="")),
        rk.text("Sector", "sector", **more), rk.text("Exchange", "exchange", **more),
        rk.text("First notice", "first_notice", **more),
        rk.text("Price source", "price_source", **more),
        rk.numeric("USD per unit", "usd_per_unit", rk.rate(6, trim=True), **more),
        rk.numeric("Notional (local)", "notional_local", rk.amount(nully=""), **more),
        rk.numeric("Delta factor", "delta_factor", rk.rate(4, trim=True), **more),
        rk.text("Trades", "trades", **more), rk.text("Reason", "reason", **more)]


def detail_section(result: Dict[str, Any]) -> html.Div:
    records, tips = detail_records(result)
    columns = detail_columns(result)
    numeric_cols = [c["id"] for c in columns if c["type"] == "numeric"]
    sort_props = rk.sortable(DETAIL_TABLE_ID, persisted=("hidden_columns",))
    sort_props["sort_as_null"] = list(sort_props["sort_as_null"]) + [OPTION_NOTIONAL]
    option_notional_style = [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{OPTION_NOTIONAL}'"}, **_NA_STYLE}
                             for c in ("notional_local", "notional_usd")]
    table = dash_table.DataTable(
        id=DETAIL_TABLE_ID, columns=columns, data=records, tooltip_data=tips,
        hidden_columns=list(DETAIL_MORE_COLUMNS),
        tooltip_delay=0, tooltip_duration=None,
        **sort_props,
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c["id"]}, "textAlign": "left"} for c in columns if c["type"] == "text"]
                               + [{"if": {"column_id": "commodity"}, "maxWidth": "30ch", "overflow": "hidden",
                                   "textOverflow": "ellipsis"},
                                  {"if": {"column_id": "reason"}, "whiteSpace": "normal", "minWidth": "220px", "maxWidth": "420px"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(["lots", "units", "notional_local", "notional_usd", "delta_lots", "delta_usd"])
                               + _na_styles(numeric_cols + ["month", "first_notice"]) + option_notional_style
                               + [{"if": {"column_id": "expiry", "filter_query": '{expiry} contains "(est.)"'}, **_NA_STYLE}],
    )
    return html.Div(className="section", children=[
        about("Contracts", DETAIL_ABOUT),
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
            about("Currency exposure of non-USD futures", kicker),
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
        about("Currency exposure of non-USD futures", kicker), table])


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
def body(result: Dict[str, Any], unit: str = DEFAULT_UNIT, selected: Optional[str] = None) -> html.Div:
    """The whole tab body from one `curve_positions` result, month cells in `unit`, the curve
    panel on `selected` (the kept pick) or the default commodity."""
    children: List[Any] = [caption_block(result)]
    if result.get("by_commodity"):
        children += [grid_section(result, unit), curve_section(result, selected), sector_section(result),
                     detail_section(result)]
    else:
        children.append(message_box(
            f"No commodity position to show: {result.get('note') or 'see the gaps above'}."))
    children += [currency_section(result), flat_section(result)]
    return html.Div(className="curve-body", children=children)


def render(as_of: Optional[str], db_path, unit: str = DEFAULT_UNIT, selected: Optional[str] = None) -> Any:
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
    return body(result, unit, selected)


AS_OF_HOVER = ("The tab follows the header's as-of date (the Blotter's and the FX & cash tab's date pickers "
               "set it); it has no date picker of its own. The date the figures are for is under the view switch.")


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row with the view switch and the body container the callback
    fills. No date picker: the tab follows the header's as-of store."""
    return html.Div(className="curve-tab", children=[
        html.Div(className="ladder-title-row", children=[
            html.H3("Curve", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                marker(f"header's as-of{f' {default_date}' if default_date else ''}", AS_OF_HOVER)])]),
        html.Div(className="meta-line", children=[
            html.Span("Month cells in: "),
            dcc.RadioItems(id=UNIT_ID, options=[{"label": UNIT_LABELS[u], "value": u} for u in UNITS],
                           value=DEFAULT_UNIT, inline=True, persistence=True, persistence_type="session",
                           inputStyle={"marginRight": "4px", "marginLeft": "10px"})]),
        html.Div(id=BODY_ID, children=[message_box("Loading the curve positions...")]),
        dcc.Store(id=SELECTED_ID, storage_type="session"),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Three callbacks: the body re-renders on the header's as-of, on every data revision, on
    the safety interval and on the view switch (the curve panel on the commodity kept in
    `SELECTED_ID`); a click on a grid row sets the curve selector to its commodity; the
    selector redraws the chart and keeps its pick."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        Input(UNIT_ID, "value"),
        State(SELECTED_ID, "data"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, unit=DEFAULT_UNIT, selected=None):
        return render(as_of, get_db_path(), unit or DEFAULT_UNIT, selected)

    @app.callback(
        Output(SELECT_ID, "value"),
        Input(GRID_ID, "active_cell"),
        prevent_initial_call=True,
    )
    def _pick_row(active_cell):
        return root_from_cell(active_cell) or no_update

    @app.callback(
        Output(CHART_ID, "children"),
        Output(SELECTED_ID, "data"),
        Input(SELECT_ID, "value"),
        State(CHART_STORE_ID, "data"),
        prevent_initial_call=True,
    )
    def _show_curve(root_id, store):
        return show_curve(root_id, store)
