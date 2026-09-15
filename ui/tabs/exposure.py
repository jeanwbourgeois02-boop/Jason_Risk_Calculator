"""Exposure dashboard section of the Cash ladder tab (docs/BUILD_PLAN.md section 5,
"Ladder" row). Pure delta table, no P&L: renders engine.ladder.exposure output
(build_exposure, portfolio_totals, ladder_usd_equivalent) and engine.pnl.stress
(move_1pct, load_scenarios, run_scenarios). Formulas live in engine/; this module only
formats and orders:
    1. snapshot cards (Net USD, Gross USD, open trades)
    2. compact metadata line
    3. currency summary (top / all)
    4. expandable settlement ladder
    5. stress block (1% move + named scenarios)
    6. legend

Rates are passed in by the caller from data.bloomberg.live.rates_from_marks (latest
official SPOT marks written by the 2-minute Bloomberg feed). A currency without a mark
is reported MISSING_RATE and shown blank, never given a made-up value. `mock_rates`
below exists ONLY for the unit tests (AUD screenshot fixture); the app never calls it.

2026-09-15 (Task C split, docs/BUILD_PLAN.md): the workbook mark-to-market panel, the
P&L ledger block and the Bloomberg diagnostics/feed panel are REMOVED from this module
(they move to the Reconciliation and Market data tabs respectively -- see
`engine.ladder.exposure.portfolio_totals`, which no longer returns an `exposure_pnl`
key, and `engine.ladder.exposure.SUMMARY_COLUMNS`, which no longer carries
`usd_delta_entry` / `exposure_pnl`). This is a pure exposure/delta view; P&L belongs to
`engine.pnl` and is rendered on the Blotter/Header tabs, not here.

Book display: records carry `book_source` (BNP 'NM Strategy', e.g. HAHY7). BOOK_DISPLAY
is the explicit, optional display mapping; empty by default so HAHY7 shows as HAHY7.

Display conventions (UI only, stored values untouched): whole units, thousands
separators, negatives in parentheses and red, positives restrained green, exact zero
as an em dash, dates as '08 Sep 2026' with ISO retained in the data.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional

import pandas as pd
from dash import dash_table, html

from ui.tabs.formatting import format_cell

SIGN_CONVENTION = "broker_reference"
BOOK_DISPLAY: Dict[str, str] = {}

MOCK_SOURCE = "MOCK (deterministic development rates, not Bloomberg)"
MOCK_TIMESTAMP = "2026-08-17T15:00:00-04:00 (fixed)"
_MOCK = {  # quoted convention: (rate, inverted). USD-per-local = 1/rate when inverted.
    "AUD": (0.65, False), "CAD": (0.73, False), "CHF": (1.2, False), "EUR": (1.1, False),
    "GBP": (1.3, False), "NZD": (0.6, False), "XAU": (3300.0, False),
    "HKD": (7.8, True), "JPY": (150.0, True), "MXN": (18.0, True), "NOK": (10.5, True),
    "SEK": (10.0, True), "SGD": (1.3, True), "TRY": (40.0, True), "ZAR": (18.0, True),
    "BRL": (5.2, True), "TWD": (30.0, True), "KRW": (1380.0, True), "IDR": (16000.0, True),
}

SNAPSHOT_ID = "exposure-snapshot"
META_ID = "exposure-meta"
WORKBOOK_STATUS_ID = "exposure-workbook-status"
LADDER_TABLE_ID = "exposure-ladder-table"
LADDER_DETAILS_ID = "exposure-ladder-details"
SUMMARY_TABLE_ID = "exposure-summary-table"
HEADER_ID = "exposure-header"
WARNINGS_ID = "exposure-warnings"
MARKET_DATA_ID = "exposure-market-data"
LEGEND_ID = "exposure-legend"

SORT_USD = "usd"
SORT_ALPHA = "alpha"
SCOPE_TOP = "top"
SCOPE_ALL = "all"
TOP_N = 6

EM_DASH = "—"
NDF_BADGE = "NDF"
NDF_EXPLANATION = ("non-deliverable forward (settles_cash=0): the local amount is an exposure notional, "
                   "not a cash flow that settles physically. Kept in the ladder and summary.")
USD_EQUIVALENT_COL = "usd_equivalent"
DATE_LABEL_COL = "settlement_date_label"

_MONO = {"fontFamily": "Consolas, 'Courier New', monospace", "fontSize": "12.5px", "padding": "4px 10px",
         "textAlign": "right", "whiteSpace": "nowrap"}
_HEAD = {"fontWeight": "600", "backgroundColor": "#f0f2f5", "color": "#1f2933", "borderBottom": "1px solid #d9dee3"}
_NEG = "#b42318"
_POS = "#1a7f4b"


def mock_rates(currencies: Iterable[str]) -> Dict[str, dict]:
    """Deterministic mock rates for the given currencies; unknown currencies are omitted
    so build_exposure flags them MISSING_RATE."""
    out = {}
    for ccy in currencies:
        if ccy in _MOCK:
            rate, inverted = _MOCK[ccy]
            out[ccy] = {"rate": rate, "inverted": inverted, "source": MOCK_SOURCE,
                        "timestamp": MOCK_TIMESTAMP, "stale": False}
    return out


def format_amount(value) -> str:
    """UI-only: exact zero -> em dash; otherwise the shared parentheses format."""
    try:
        if value is not None and not pd.isna(value) and round(float(value)) == 0:
            return EM_DASH
    except (TypeError, ValueError):
        pass
    return format_cell(value)


def format_date(iso: str) -> str:
    """'2026-09-08' -> '08 Sep 2026'; anything unparseable is returned unchanged."""
    try:
        return date.fromisoformat(str(iso)).strftime("%d %b %Y")
    except ValueError:
        return str(iso)


def rate_status_text(result) -> str:
    """One-line rate status for the toolbar indicator and metadata."""
    bad = int((result.status["status"] != "OK").sum()) if not result.status.empty else 0
    return "Rates: OK" if not bad else f"Rates: {bad} missing/stale"


def _sign_styles(columns: Iterable[str]) -> list:
    styles = []
    for c in columns:
        styles.append({"if": {"column_id": c, "filter_query": f"{{{c}}} contains '('"}, "color": _NEG})
        styles.append({"if": {"column_id": c, "filter_query": f"{{{c}}} contains ',' && !({{{c}}} contains '(')"},
                       "color": _POS})
    return styles


# ------------------------------------------------------------------ 1. four snapshot cards
def _card(label: str, value: str, tag: str, note: str = "") -> html.Div:
    tag_class = {"Exposure": "tag--exposure", "Workbook MTM": "tag--workbook", "Mock": "tag--mock",
                 "Unavailable": "tag--unavailable"}.get(tag, "")
    children = [html.Span(label, className="card-label"),
                html.Span(value, className="card-value" + (" card-value--muted" if tag == "Unavailable" else "")),
                html.Span(tag, className=f"tag {tag_class}")]
    if note:
        children.append(html.Span(note, className="card-note"))
    return html.Div(children, className="card")


def risk_snapshot(result, records: List[dict]) -> html.Div:
    from engine.ladder.exposure import portfolio_totals
    totals = portfolio_totals(result)
    if totals["missing"]:
        currencies = {r["currency"] for r in records}
        note = ("no Bloomberg rates" if currencies and set(totals["missing"]) >= currencies
                else "no rate: " + ", ".join(totals["missing"]))
        net = gross = "Unavailable"
        tag = "Unavailable"
        notes = (note, note)
    else:
        net, gross = (format_amount(totals[k]) for k in ("net_usd", "gross_usd"))
        tag = "Exposure"
        notes = ("sum of currency USD deltas", "sum of |USD delta|")
    return html.Div(id=SNAPSHOT_ID, className="cards cards--three", children=[
        _card("Net USD exposure", net, tag, notes[0]),
        _card("Gross USD exposure", gross, tag, notes[1]),
        _card("Open FX trades", str(len(records)), "Exposure", f"{len({r['currency'] for r in records})} currencies"),
    ])


# ------------------------------------------------------------------ 2. metadata, mock line, workbook status
def metadata_line(result, records: List[dict], unresolved: list, as_of_date: str,
                  rates: Optional[Dict[str, dict]] = None) -> html.Div:
    books = sorted({r["book"] for r in records})
    sources = sorted({r["book_source"] for r in records})
    book_text = ", ".join(books) or "none"
    if books != sources:
        book_text += f" (source {', '.join(sources)})"
    ndf_trades = sum(1 for r in records if r.get("settles_cash") == 0)
    rates = rates or {}
    rate_sources = ", ".join(sorted({str(v["source"]) for v in rates.values()})) or "none loaded"
    latest = max((str(v["timestamp"]) for v in rates.values()), default="n/a")
    items = [f"As-of {as_of_date}", f"Book {book_text}", f"NDF trades {ndf_trades}",
             f"Rates {rate_sources}", f"Last rate {latest}"]
    if unresolved:
        items.append(f"Unresolved trades {len(unresolved)}")
    spans = [html.Span(item, className="meta-item") for item in items]
    spans[1] = html.Span(items[1], id=HEADER_ID, className="meta-item")  # book, addressable for tests
    return html.Div(id=META_ID, className="meta-line", children=spans)


# market_data_panel / workbook_status removed 2026-09-15 (docs/BUILD_PLAN.md Task C
# split): Bloomberg feed status and diagnostics moved to ui/tabs/market_data.py
# (owned by C3); the workbook mark-to-market panel moved to ui/tabs/reconciliation.py
# (owned by C4). This module is a pure exposure/delta view.


# ------------------------------------------------------------------ 3. currency summary
SUMMARY_COLUMNS = ["currency", "local_delta", "usd_delta", "settlement"]


def summary_frame(result, records: List[dict], sort: str = SORT_USD, scope: str = SCOPE_ALL) -> pd.DataFrame:
    """Primary summary columns, NDF flag per currency (settles_cash=0 on any record),
    sorted by |USD delta| desc (default) or alphabetically; scope 'top' keeps TOP_N."""
    ndf: Dict[str, bool] = {}
    for r in records:
        ndf[r["currency"]] = ndf.get(r["currency"], False) or r["settles_cash"] == 0
    s = result.summary.copy()
    s["settlement"] = s["currency"].map(lambda c: NDF_BADGE if ndf.get(c) else "Deliverable")
    if s.empty:
        return s.reindex(columns=SUMMARY_COLUMNS)
    s = s.assign(_abs=s["usd_delta"].abs().fillna(-1)).sort_values(["_abs", "currency"], ascending=[False, True])
    if scope == SCOPE_TOP:
        s = s.head(TOP_N)
    if sort == SORT_ALPHA:
        s = s.sort_values("currency")
    return s.drop(columns="_abs").reindex(columns=SUMMARY_COLUMNS).reset_index(drop=True)


def summary_table(frame: pd.DataFrame) -> dash_table.DataTable:
    labels = {"currency": "Currency", "settlement": "Settlement type", "local_delta": "Local delta",
              "usd_delta": "USD delta"}
    f = frame.reindex(columns=SUMMARY_COLUMNS).copy()
    amounts = ["local_delta", "usd_delta"]
    for col in amounts:
        f[col] = f[col].map(format_amount)
    return dash_table.DataTable(
        id=SUMMARY_TABLE_ID,
        columns=[{"name": labels[c], "id": c} for c in f.columns],
        data=f.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={**_MONO, "minWidth": "130px"},
        style_cell_conditional=[
            {"if": {"column_id": "currency"}, "textAlign": "left", "fontWeight": "600", "minWidth": "90px"},
            {"if": {"column_id": "settlement"}, "textAlign": "center", "minWidth": "110px"},
        ],
        style_header=_HEAD,
        style_data_conditional=_sign_styles(amounts) + [
            {"if": {"column_id": "settlement", "filter_query": f"{{settlement}} = '{NDF_BADGE}'"},
             "backgroundColor": "#eef2ff", "color": "#3538cd", "fontWeight": "600"},
        ],
    )


# ------------------------------------------------------------------ 4. settlement ladder
def ladder_table(result) -> dash_table.DataTable:
    """Settlement dates as rows (ISO kept in `settlement_date`, display label added),
    currencies as columns, USD equivalent on the right from the engine. Sticky header
    and sticky first column; horizontal scroll for many currencies."""
    from engine.ladder.exposure import ladder_usd_equivalent
    frame = result.ladder.reset_index()
    ccys = [c for c in frame.columns if c != "settlement_date"]
    usd = ladder_usd_equivalent(result)
    frame[USD_EQUIVALENT_COL] = frame["settlement_date"].map(usd)
    frame.insert(0, DATE_LABEL_COL, frame["settlement_date"].map(format_date))
    for col in ccys + [USD_EQUIVALENT_COL]:
        frame[col] = frame[col].map(format_amount)
    columns = ([{"name": "Settlement date", "id": DATE_LABEL_COL}]
               + [{"name": c, "id": c} for c in ccys]
               + [{"name": "USD equivalent", "id": USD_EQUIVALENT_COL}])
    return dash_table.DataTable(
        id=LADDER_TABLE_ID,
        columns=columns,
        data=frame.to_dict("records"),  # includes ISO settlement_date, not displayed
        fixed_rows={"headers": True},
        fixed_columns={"headers": True, "data": 1},
        style_table={"overflowX": "auto", "minWidth": "100%"},
        style_cell={**_MONO, "minWidth": "120px", "width": "120px", "maxWidth": "160px"},
        style_cell_conditional=[
            {"if": {"column_id": DATE_LABEL_COL}, "textAlign": "left", "fontWeight": "600",
             "minWidth": "130px", "width": "130px"},
            {"if": {"column_id": USD_EQUIVALENT_COL}, "fontWeight": "600", "borderLeft": "2px solid #d9dee3"},
        ],
        style_header=_HEAD,
        style_data_conditional=_sign_styles(ccys + [USD_EQUIVALENT_COL]),
    )


# ------------------------------------------------------------------ 4b. combined workbook-style ladder
COMBINED_TABLE_ID = "exposure-combined-table"
ROW_LABEL_COL = "row"
SUMMARY_ROWS = [("FX rate (USD per local)", "fx_rate"), ("Local delta", "local_delta"), ("USD delta", "usd_delta"),
                ("Settlement type", "settlement")]


def combined_frame(result, records: List[dict], sort: str = SORT_USD) -> pd.DataFrame:
    """Screenshot layout: settlement-date rows (signed local amounts) followed by the
    per-currency summary rows, all in the same currency columns. Currency columns
    ordered by |USD delta| desc (default) or A-Z. A `USD equivalent` column carries the
    engine's per-date USD equivalent and, on the summary rows, the portfolio totals.
    Every value comes from build_exposure / portfolio_totals / ladder_usd_equivalent."""
    from engine.ladder.exposure import ladder_usd_equivalent, portfolio_totals
    summary = summary_frame(result, records, sort=sort, scope=SCOPE_ALL)
    ccys = list(summary["currency"])
    fx = result.summary.set_index("currency")["fx_rate"]
    rows = []
    usd_eq = ladder_usd_equivalent(result)
    for day in result.ladder.index:
        row = {ROW_LABEL_COL: format_date(day), "settlement_date": day, "kind": "date"}
        for c in ccys:
            row[c] = format_amount(result.ladder.loc[day, c]) if c in result.ladder.columns else EM_DASH
        row[USD_EQUIVALENT_COL] = format_amount(usd_eq.get(day, float("nan")))
        rows.append(row)
    totals = portfolio_totals(result)
    by_ccy = summary.set_index("currency")
    for label, key in SUMMARY_ROWS:
        row = {ROW_LABEL_COL: label, "settlement_date": "", "kind": key}
        for c in ccys:
            if key == "fx_rate":
                v = fx.get(c, float("nan"))
                row[c] = "" if pd.isna(v) else f"{v:.6f}"
            elif key == "settlement":
                row[c] = by_ccy.loc[c, "settlement"]
            else:
                row[c] = format_amount(by_ccy.loc[c, key])
        row[USD_EQUIVALENT_COL] = format_amount(totals["net_usd"]) if key == "usd_delta" else ""
        rows.append(row)
    return pd.DataFrame(rows), ccys


def combined_table(result, records: List[dict], sort: str = SORT_USD) -> dash_table.DataTable:
    frame, ccys = combined_frame(result, records, sort)
    columns = ([{"name": ["", "Settlement date"], "id": ROW_LABEL_COL}]
               + [{"name": [by_ccy_label(frame, c), c], "id": c} for c in ccys]
               + [{"name": ["", "USD equivalent"], "id": USD_EQUIVALENT_COL}])
    first_summary = len(result.ladder.index)
    return dash_table.DataTable(
        id=COMBINED_TABLE_ID,
        columns=columns,
        data=frame.to_dict("records"),
        merge_duplicate_headers=True,
        fixed_rows={"headers": True},
        fixed_columns={"headers": True, "data": 1},
        style_table={"overflowX": "auto", "minWidth": "100%"},
        style_cell={**_MONO, "minWidth": "125px", "width": "125px", "maxWidth": "170px"},
        style_cell_conditional=[
            {"if": {"column_id": ROW_LABEL_COL}, "textAlign": "left", "fontWeight": "600", "minWidth": "170px", "width": "170px"},
            {"if": {"column_id": USD_EQUIVALENT_COL}, "fontWeight": "600", "borderLeft": "2px solid #d9dee3"},
        ],
        style_header=_HEAD,
        style_data_conditional=_sign_styles(ccys + [USD_EQUIVALENT_COL]) + [
            {"if": {"row_index": first_summary}, "borderTop": "2px solid #1f2933"},
            {"if": {"filter_query": "{kind} != 'date'"}, "backgroundColor": "#f7f8fa", "fontWeight": "600"},
            {"if": {"filter_query": "{kind} = 'fx_rate'"}, "color": "#616e7c", "fontWeight": "400"},
            {"if": {"filter_query": "{kind} = 'exposure_pnl'"}, "backgroundColor": "#eef4fb"},
            {"if": {"filter_query": "{kind} = 'settlement'"}, "color": "#3538cd", "fontWeight": "600", "fontSize": "11px"},
        ],
    )


def by_ccy_label(frame: pd.DataFrame, ccy: str) -> str:
    """Header group: 'NDF' above non-deliverable currencies, blank otherwise."""
    st = frame.loc[frame["kind"] == "settlement", ccy]
    return NDF_BADGE if not st.empty and st.iloc[0] == NDF_BADGE else ""


# ------------------------------------------------------------------ 5. legend
def legend() -> html.Dl:
    items = [
        ("Local delta", "sum of signed local-currency amounts across all settlement dates."),
        ("USD delta", "local delta x USD-per-local rate. This is a delta table, not P&L "
                      "-- see the Blotter tab for LTD/Daily/MTD/YTD."),
        (NDF_BADGE, NDF_EXPLANATION),
        ("Bloomberg rates", "latest official SPOT mark per currency, pulled from the Bloomberg terminal on this "
                            "computer every 2 minutes; blank when no mark exists. Nothing is substituted."),
        (EM_DASH, "zero amount (display only)."),
    ]
    return html.Dl(id=LEGEND_ID, className="legend",
                   children=[html.Div([html.Dt(k), html.Dd(v)]) for k, v in items])


# ------------------------------------------------------------------ 5b. stress block
STRESS_ID = "exposure-stress"
STRESS_TABLE_ID = "exposure-stress-table"
FUTURES_DELTA_ID = "exposure-futures-delta"


def futures_delta_line(futures_usd_delta: Optional[float], reason: str = "") -> html.Div:
    """One line for the futures (e.g. ES) USD delta feeding the stress block's
    non-FX line. `futures_usd_delta=None` renders Unavailable with `reason` shown in
    place -- no engine query for open-futures USD delta exists yet
    (docs/BUILD_PLAN.md section 4 names 'a futures USD delta' as a stress input but does
    not specify the source query); this stays Unavailable, never zero, until
    cash-ladder/pnl-engine adds one."""
    if futures_usd_delta is None:
        text = "Unavailable" + (f" ({reason})" if reason else "")
    else:
        text = format_amount(futures_usd_delta)
    return html.Div(id=FUTURES_DELTA_ID, className="meta-line",
                    children=[html.Span("Futures USD delta", className="meta-item"),
                              html.Span(text, className="meta-item")])


def stress_block(delta_by_ccy: Dict[str, float], futures_usd_delta: Optional[float] = None) -> html.Div:
    """1% move per currency plus named scenarios from config/stress.yaml, both from
    `engine.pnl.stress`. `futures_usd_delta=None` (no engine source yet) is passed
    through as 0.0 to `run_scenarios`/`move_1pct` for the FX-only figures, but is shown
    Unavailable on its own line rather than silently included as zero."""
    from engine.pnl.stress import load_scenarios, move_1pct, run_scenarios
    moves = move_1pct(delta_by_ccy)
    scenarios = load_scenarios()
    results = run_scenarios(delta_by_ccy, scenarios, futures_usd_delta=futures_usd_delta or 0.0)
    move_rows = [{"currency": ccy, "move_1pct_usd": format_amount(v)} for ccy, v in sorted(moves.items())]
    move_table = dash_table.DataTable(
        columns=[{"name": "Currency", "id": "currency"}, {"name": "1% move (USD)", "id": "move_1pct_usd"}],
        data=move_rows, style_cell={**_MONO, "minWidth": "110px"}, style_header=_HEAD,
    ) if move_rows else html.P("No FX delta to stress.", style={"color": "#616e7c"})
    scenario_rows = [{"scenario": name, "fx_total": format_amount(r["fx_total"]),
                      "futures_pnl": format_amount(r["futures_pnl"]), "total": format_amount(r["total"])}
                     for name, r in sorted(results.items())]
    scenario_table = dash_table.DataTable(
        id=STRESS_TABLE_ID,
        columns=[{"name": "Scenario", "id": "scenario"}, {"name": "FX P&L (USD)", "id": "fx_total"},
                 {"name": "Futures P&L (USD)", "id": "futures_pnl"}, {"name": "Total (USD)", "id": "total"}],
        data=scenario_rows, style_cell={**_MONO, "minWidth": "120px"}, style_header=_HEAD,
    ) if scenario_rows else html.P("No named scenarios in config/stress.yaml.", style={"color": "#616e7c"})
    return html.Div(id=STRESS_ID, className="section section--secondary", children=[
        html.H4("Stress"),
        futures_delta_line(futures_usd_delta, reason="no engine query for open-futures USD delta yet"),
        html.H5("1% move per currency"), move_table,
        html.H5("Named scenarios"), scenario_table,
    ])


# ------------------------------------------------------------------ section
def exposure_section(records: List[dict], unresolved: list, as_of_date: str,
                     rates: Dict[str, dict] | None = None,
                     sort: str = SORT_USD, scope: str = SCOPE_ALL,
                     futures_usd_delta: Optional[float] = None) -> html.Div:
    """Cards, metadata, combined cash ladder, alternative views, stress block, legend.
    `rates` is whatever the marks table holds (see data.bloomberg.live.rates_from_marks);
    None/empty means every currency is MISSING. Bloomberg feed status and the workbook
    mark-to-market panel are NOT rendered here any more -- see module docstring."""
    from engine.ladder.exposure import build_exposure
    rates = rates or {}
    result = build_exposure(records, rates)
    empty = result.ladder.empty
    main = (combined_table(result, records, sort) if not empty
            else html.P("No open FX trades for this as-of date.", style={"color": "#616e7c"}))
    order_note = "currencies by |USD delta|" if sort != SORT_ALPHA else "currencies A-Z"
    delta_by_ccy = dict(zip(result.summary["currency"], result.summary["usd_delta"])) if not result.summary.empty else {}
    return html.Div(className="section", children=[
        risk_snapshot(result, records),
        metadata_line(result, records, unresolved, as_of_date, rates),
        html.H4(f"Cash ladder by settlement date ({order_note})"),
        main,
        html.Details(id=LADDER_DETAILS_ID, className="details", open=False, children=[
            html.Summary("Other views"),
            html.H4("Currency summary (list)"),
            summary_table(summary_frame(result, records, sort, SCOPE_ALL)),
            html.H4("Settlement ladder (dates only)"),
            ladder_table(result) if not empty else html.Div(),
        ]),
        stress_block(delta_by_ccy, futures_usd_delta),
        html.Details(className="details details--compact", children=[html.Summary("What the rows mean"), legend()]),
    ])
