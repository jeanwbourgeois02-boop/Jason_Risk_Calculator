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

_BODY_FONT = ("Inter, 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, "
              "Roboto, Helvetica, Arial, sans-serif")
# One shared table look for all three Ladder tables (coordinator addition 2026-09-15,
# item 2): same font, header, row height and borders as the currency ladder grid
# (`combined_table`), which was the reference look. `_MONO`'s old monospace font is
# retired; kept as an alias only so any stray reference elsewhere still resolves.
_MONO = {"fontFamily": _BODY_FONT, "fontSize": "12.5px", "padding": "6px 10px",
         "textAlign": "right", "whiteSpace": "nowrap", "height": "32px"}
_HEAD = {"fontWeight": "700", "backgroundColor": "#0f1f3d", "color": "#ffffff",
         "borderBottom": "1px solid #0f1f3d", "fontFamily": _BODY_FONT, "padding": "6px 10px"}
_TABLE_STYLE = {"overflowX": "auto", "minWidth": "100%"}
_NEG = "#c0392b"  # matches ui/assets/style.css --neg
_POS = "#1a7f4b"  # matches ui/assets/style.css --pos


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


HEADLINE_ID = "exposure-headline"


def _gross_net_card(title: str, gross_text: str, net_value: Optional[float] = None,
                    note: Optional[str] = None, unavailable: bool = False,
                    direction: bool = False) -> html.Div:
    """One headline card (user decision 2026-09-15, item 1): small-caps title, GROSS
    bold, then directly under it NET equally bold with a small-caps "net" label,
    coloured green/red by sign (gross itself stays neutral). `note` replaces the net
    line entirely -- used for the Unavailable reason and for "no open futures" -- so a
    card never shows both a note and a net figure at once."""
    children = [html.Span(title, className="card-label"),
                html.Span(gross_text, className="card-value" + (" card-value--muted" if unavailable else ""))]
    if note is not None:
        children.append(html.Span(note, className="card-note"))
    elif net_value is not None:
        sign_class = "pos" if net_value > 0 else ("neg" if net_value < 0 else "")
        parts = [
            html.Span("net ", className="card-net-label"),
            html.Span(format_amount(net_value), className=f"card-net-value {sign_class}".strip()),
        ]
        if direction and net_value != 0:
            # Same words as the header's Net USD delta card (user decision 2026-09-15):
            # `net_value` is already the USD position here, + = long USD.
            parts.append(html.Span("long USD" if net_value > 0 else "short USD", className="card-net-direction"))
        children.append(html.Span(parts, className="card-net"))
    return html.Div(children, className="card")


def headline_numbers(result, futures: Optional[dict] = None, fallback_ccys: Optional[set] = None,
                     forward_proxy_ccys: Optional[set] = None) -> html.Div:
    """One merged headline card (user decision 2026-09-15, "Henry doesn't care about the
    split, put it all together"): was three cards -- Delta combined / Delta non-forward
    (futures) / Delta forward (FX) -- now just "Delta", currencies and futures already
    summed together: gross = sum of |USD delta| over currencies + |futures|, net
    (the USD position, CLAUDE.md sign: + = long USD) directly underneath with its
    LONG USD / SHORT USD direction, same convention as the app header's Net USD delta
    card. The FX-only and futures-only splits still exist -- per-currency in the risk
    table below, futures alone in its own "Delta non-forward" total row there -- this
    top card just no longer repeats them.
    Unavailable with the engine's own reason only when a rate or a futures mark is
    genuinely missing. `fallback_ccys`/`forward_proxy_ccys` are always empty in the live
    app since 2026-09-17 ("no bnp fall back" -- `ui.tabs.cash_ladder`'s BNP_BVAL SPOT/
    forward-outright fallback was removed outright, not just left unused, so a caller
    there never populates these sets any more); the parameters and the '*'/caption
    machinery below are kept only as a generic, currently-unused labelling mechanism
    (still exercised directly by tests/test_ui_ladder.py) for a future non-official
    fallback source, should one ever exist again. A note under the card names how many
    currencies are on fallback, once, so callers don't need to derive it from the risk
    table's '*' marks again."""
    from engine.ladder.exposure import portfolio_totals
    futures = futures or DEFAULT_FUTURES
    fallback_ccys = fallback_ccys or set()
    forward_proxy_ccys = forward_proxy_ccys or set()
    totals = portfolio_totals(result)
    fut_value = futures.get("value", float("nan"))
    no_open_futures = not (futures.get("by_instrument") or futures.get("missing"))
    if no_open_futures and pd.isna(fut_value):
        fut_value = 0.0
    rate_ok = not totals["missing"]
    fut_ok = not pd.isna(fut_value)

    if rate_ok and fut_ok:
        gross = totals["gross_usd"] + abs(fut_value)
        net = -(totals["net_usd"] + fut_value)  # USD position, same sign as the header
        combined_card = _gross_net_card("Delta (FX + futures)", format_amount(gross), net_value=net, direction=True)
    else:
        reasons = []
        if not rate_ok:
            reasons.append("no rate: " + ", ".join(totals["missing"]))
        if not fut_ok:
            reasons.append(futures.get("reason") or "futures delta unavailable")
        combined_card = _gross_net_card("Delta (FX + futures)", "Unavailable", note="; ".join(reasons), unavailable=True)

    cards = html.Div(id=HEADLINE_ID, className="cards cards--one", children=[combined_card])
    if not fallback_ccys and not forward_proxy_ccys:
        return cards
    parts = []
    if fallback_ccys:
        parts.append(f"{len(fallback_ccys)} currencies on BNP file rate, not Bloomberg: "
                     + ", ".join(sorted(fallback_ccys)))
    if forward_proxy_ccys:
        parts.append(f"{len(forward_proxy_ccys)} currencies on BNP forward proxy: "
                     + ", ".join(sorted(forward_proxy_ccys)))
    caption = html.P("; ".join(parts) + " (marked * in the risk table below)",
                     className="section-kicker")
    return html.Div([cards, caption])


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
        fixed_rows={},
        fixed_columns={},
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
SUMMARY_ROWS = [("FX rate (USD per local)", "fx_rate"), ("Local delta", "local_delta"), ("USD delta", "usd_delta")]
# "Settlement type" (NDF / Deliverable) summary row and its NDF super-header removed
# 2026-09-15 per the user's decision -- NDF currencies stay in the grid unmarked.


def combined_frame(result, records: List[dict], sort: str = SORT_USD,
                   fallback_ccys: Optional[set] = None) -> pd.DataFrame:
    """Screenshot layout: settlement-date rows (signed local amounts) followed by the
    per-currency summary rows, all in the same currency columns. Currency columns
    ordered by |USD delta| desc (default) or A-Z. A `USD equivalent` column carries the
    engine's per-date USD equivalent and, on the summary rows, the portfolio totals.
    Every value comes from build_exposure / portfolio_totals / ladder_usd_equivalent.

    `fallback_ccys` (user decision 2026-09-15, item D; the BNP_BVAL source it originally
    labelled was removed outright 2026-09-17, "no bnp fall back" -- the live app never
    populates this set any more, see `headline_numbers`'s docstring): currencies priced
    from a non-official fallback rate, were one ever supplied. Adds a 'Rate source'
    summary row so a fallback would be visible in place, never silent, if this is ever
    wired to a source again."""
    fallback_ccys = fallback_ccys or set()
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
    for label, key in SUMMARY_ROWS + [("Rate source", "rate_source")]:
        row = {ROW_LABEL_COL: label, "settlement_date": "", "kind": key}
        for c in ccys:
            if key == "fx_rate":
                v = fx.get(c, float("nan"))
                row[c] = "" if pd.isna(v) else f"{v:.6f}"
            elif key == "rate_source":
                row[c] = "BNP file" if c in fallback_ccys else "Bloomberg"
            else:
                row[c] = format_amount(by_ccy.loc[c, key])
        row[USD_EQUIVALENT_COL] = format_amount(totals["net_usd"]) if key == "usd_delta" else ""
        rows.append(row)
    return pd.DataFrame(rows), ccys


def combined_table(result, records: List[dict], sort: str = SORT_USD,
                   fallback_ccys: Optional[set] = None) -> dash_table.DataTable:
    frame, ccys = combined_frame(result, records, sort, fallback_ccys)
    columns = ([{"name": "Settlement date", "id": ROW_LABEL_COL}]
               + [{"name": c, "id": c} for c in ccys]
               + [{"name": "USD equivalent", "id": USD_EQUIVALENT_COL}])
    first_summary = len(result.ladder.index)
    return dash_table.DataTable(
        id=COMBINED_TABLE_ID,
        columns=columns,
        data=frame.to_dict("records"),
        merge_duplicate_headers=True,
        fixed_rows={},
        fixed_columns={},
        style_table=_TABLE_STYLE,
        style_cell={**_MONO, "minWidth": "125px", "width": "125px", "maxWidth": "170px"},
        style_cell_conditional=[
            {"if": {"column_id": ROW_LABEL_COL}, "textAlign": "left", "fontWeight": "600", "minWidth": "170px", "width": "170px"},
            {"if": {"column_id": USD_EQUIVALENT_COL}, "fontWeight": "600", "borderLeft": "2px solid #d9dee3"},
        ],
        style_header=_HEAD,
        style_data_conditional=_sign_styles(ccys + [USD_EQUIVALENT_COL]) + [
            # Visual hierarchy of the summary block (user decision 2026-09-15): date rows
            # regular; FX rate bold (the bridge between local and USD); local delta
            # medium; USD delta the single heaviest row with a navy tint (the answer the
            # table exists to give); rate source small and muted -- it is provenance,
            # not an error, so it is never red.
            {"if": {"row_index": first_summary}, "borderTop": "2px solid #1f2933"},
            {"if": {"filter_query": "{kind} != 'date'"}, "backgroundColor": "#f7f8fa", "fontWeight": "400"},
            {"if": {"filter_query": "{kind} = 'fx_rate'"}, "color": "#1b2333", "fontWeight": "700"},
            {"if": {"filter_query": "{kind} = 'local_delta'"}, "fontWeight": "500"},
            {"if": {"filter_query": "{kind} = 'usd_delta'"}, "fontWeight": "700", "backgroundColor": "#e8edf7",
             "borderTop": "1px solid #c8d0e0", "borderBottom": "1px solid #c8d0e0"},
            {"if": {"filter_query": "{kind} = 'rate_source'"}, "color": "#6b7280", "fontWeight": "400", "fontSize": "11px"},
            {"if": {"filter_query": "{kind} = 'exposure_pnl'"}, "backgroundColor": "#eef4fb"},
            {"if": {"filter_query": "{kind} = 'settlement'"}, "color": "#3538cd", "fontWeight": "600", "fontSize": "11px"},
        ],
    )


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


# ------------------------------------------------------------------ 5b. combined risk table (currencies + futures + stress)
RISK_TABLE_ID = "exposure-risk-table"
RISK_LABEL_COL = "name"
RISK_BASE_COLUMNS = [RISK_LABEL_COL, "usd_delta", "move_1pct"]

DEFAULT_FUTURES: dict = {"value": float("nan"), "by_instrument": {}, "missing": [], "reason": "no futures data supplied"}

# 2026-09-15 (docs/BUILD_PLAN.md section 4/5, user decision "Reorder the Ladder tab"):
# the old stand-alone `stress_block` / `futures_delta_line` are retired -- named
# scenarios now live as columns of this one combined table (currency rows + one row per
# open future), so a number never appears in two places. `futures` is the full dict
# returned by `engine.ladder.futures_delta.futures_usd_delta` (not just its `value`),
# so a per-instrument row can show Unavailable with the engine's own `reason` string
# instead of a bare futures total.


def combined_risk_frame(result, futures: Optional[dict] = None,
                        scenarios: Optional[Dict[str, Dict[str, float]]] = None,
                        futures_pct_by_scenario: Optional[Dict[str, float]] = None,
                        fallback_ccys: Optional[set] = None) -> pd.DataFrame:
    """One row per currency (from `result.summary`) plus one row per open future (from
    `futures['by_instrument']`, and one Unavailable row per name in `futures['missing']`).
    Columns: name, usd_delta, move_1pct (=usd_delta x 0.01), then one column per scenario
    name. A currency not named in a scenario's move dict, or a future when
    `futures_pct_by_scenario` has no entry for that scenario (config/stress.yaml defines
    none today -- see module docstring), gets `None` (rendered blank, distinct from an
    explicit zero) for that scenario cell. `_unavailable` carries the reason string when
    non-empty; `usd_delta`/`move_1pct`/every scenario cell are then meaningless and the
    renderer replaces them with a single Unavailable label.

    A commodity currency (XAU etc., `engine.ladder.exposure.COMMODITY_CCYS`) still gets
    its own row here -- oz `usd_delta` shown exactly like any other currency -- but is
    tagged `kind="commodity"` instead of `"currency"` (2026-09-17, "the XAU does not
    work well"): CLAUDE.md reports gold separately from FX Net/Gross, and
    `portfolio_totals` (which computes this frame's own totals row) already excludes it
    from the sums; the distinct `kind` is only so the renderer can style/caption it as
    "shown but not counted" rather than have it look like an ordinary FX row."""
    from engine.ladder.exposure import COMMODITY_CCYS
    futures = futures or DEFAULT_FUTURES
    scenarios = scenarios or {}
    futures_pct_by_scenario = futures_pct_by_scenario or {}
    fallback_ccys = fallback_ccys or set()
    # Column order = config/stress.yaml's own order (user decision 2026-09-15, item 3),
    # not alphabetical: `scenarios` is an ordinary dict from `load_scenarios`, which
    # preserves the YAML's insertion order.
    scenario_names = list(scenarios)
    status_msg = (dict(zip(result.status["currency"], result.status["message"]))
                  if not result.status.empty else {})
    rows = []
    for _, r in result.summary.iterrows():
        ccy, usd = r["currency"], r["usd_delta"]
        label = ccy + " *" if ccy in fallback_ccys else ccy
        kind = "commodity" if ccy in COMMODITY_CCYS else "currency"
        row = {RISK_LABEL_COL: label, "kind": kind}
        if pd.isna(usd):
            row["usd_delta"], row["move_1pct"] = None, None
            row["_unavailable"] = status_msg.get(ccy, "no rate")
            for name in scenario_names:
                row[name] = None
        else:
            row["usd_delta"], row["move_1pct"] = usd, usd * 0.01
            row["_unavailable"] = ""
            for name in scenario_names:
                pct = scenarios[name].get(ccy)
                row[name] = usd * pct if pct is not None else None
        rows.append(row)

    reason = futures.get("reason", "")
    for instrument_id, usd in (futures.get("by_instrument") or {}).items():
        row = {RISK_LABEL_COL: instrument_id, "kind": "future", "usd_delta": usd,
              "move_1pct": usd * 0.01, "_unavailable": ""}
        for name in scenario_names:
            pct = futures_pct_by_scenario.get(name)
            row[name] = usd * pct if pct is not None else None
        rows.append(row)
    for instrument_id in futures.get("missing") or []:
        row = {RISK_LABEL_COL: instrument_id, "kind": "future", "usd_delta": None,
              "move_1pct": None, "_unavailable": reason}
        for name in scenario_names:
            row[name] = None
        rows.append(row)

    columns = RISK_BASE_COLUMNS[:1] + ["kind"] + RISK_BASE_COLUMNS[1:] + scenario_names + ["_unavailable"]
    return pd.DataFrame(rows, columns=columns)


def _unavailable_label(reason: str) -> str:
    return f"Unavailable ({reason})" if reason else "Unavailable"


def combined_risk_table(result, futures: Optional[dict] = None,
                        scenarios: Optional[Dict[str, Dict[str, float]]] = None,
                        futures_pct_by_scenario: Optional[Dict[str, float]] = None,
                        fallback_ccys: Optional[set] = None) -> html.Div:
    """The combined risk table plus its Net USD / Gross USD totals (docs/BUILD_PLAN.md
    "Reorder the Ladder tab", item 1). Net excludes futures; Gross adds |futures value|.
    Either total is Unavailable (never a fabricated number) if any currency lacks a rate
    or the futures total is NaN. Currencies priced from a non-official fallback rate
    (`fallback_ccys`, item D -- always empty from the live app since 2026-09-17, "no
    bnp fall back"; see `exposure_section`'s docstring) would be marked with a trailing
    '*' in the name column; see the caption this function's caller adds and the 'Rate
    source' row in the currency ladder grid below it."""
    from engine.ladder.exposure import portfolio_totals
    futures = futures or DEFAULT_FUTURES
    scenarios = scenarios if scenarios is not None else {}
    frame = combined_risk_frame(result, futures, scenarios, futures_pct_by_scenario, fallback_ccys)
    scenario_names = list(scenarios)

    display_rows = []
    for _, row in frame.iterrows():
        out = {RISK_LABEL_COL: row[RISK_LABEL_COL], "kind": row["kind"]}
        if row["_unavailable"]:
            label = _unavailable_label(row["_unavailable"])
            out["usd_delta"] = label
            out["move_1pct"] = label
            for name in scenario_names:
                out[name] = label
        else:
            out["usd_delta"] = format_amount(row["usd_delta"])
            out["move_1pct"] = format_amount(row["move_1pct"])
            for name in scenario_names:
                v = row[name]
                # A currency the scenario does not move is a zero, shown as the same em
                # dash as any other zero, so a blank never reads as "not computed"
                # (user decision 2026-09-15).
                out[name] = format_amount(v) if v is not None else EM_DASH
        display_rows.append(out)

    totals = portfolio_totals(result)
    fut_value = futures.get("value", float("nan"))
    if totals["missing"]:
        net_text = _unavailable_label("no rate: " + ", ".join(totals["missing"]))
    else:
        # USD position, same sign as the header's "Net USD delta" (CLAUDE.md: + = long USD):
        # the engine's net_usd is the net non-USD delta, so it is negated here, exactly as
        # ui/tabs/header.py and headline_numbers above do. One figure, one sign, everywhere.
        net_text = format_amount(-totals["net_usd"])
    if totals["missing"] or pd.isna(fut_value):
        reasons = []
        if totals["missing"]:
            reasons.append("no rate: " + ", ".join(totals["missing"]))
        if pd.isna(fut_value):
            reasons.append(futures.get("reason") or "futures delta unavailable")
        gross_text = _unavailable_label("; ".join(reasons))
    else:
        gross_text = format_amount(totals["gross_usd"] + abs(fut_value))
    display_rows.append({RISK_LABEL_COL: "Net USD delta, FX only (+ = long USD)", "usd_delta": net_text,
                         "move_1pct": "", "kind": "total", **{name: "" for name in scenario_names}})
    display_rows.append({RISK_LABEL_COL: "Gross delta (incl. |futures|)", "usd_delta": gross_text,
                         "move_1pct": "", "kind": "total", **{name: "" for name in scenario_names}})

    commodities = totals.get("commodities") or []
    commodity_caption = None
    if commodities:
        parts = []
        for c in commodities:
            oz = format_amount(c["local_delta"])
            usd = (_unavailable_label(c["status"]) if pd.isna(c["usd_delta"])
                  else format_amount(c["usd_delta"]))
            parts.append(f"{c['currency']} {oz} oz, {usd} USD notional at spot")
        commodity_caption = html.P(
            "Gold/metals (excluded from FX Net/Gross USD above, shown on their own "
            "row in the table): " + "; ".join(parts),
            className="section-kicker",
        )

    # Scenario headers wrap on two lines when long (user decision 2026-09-15, item 3);
    # a plain string name lets Dash wrap it itself once whiteSpace is 'normal' below --
    # no manual line-break needed since dash_table headers already wrap on word
    # boundaries when the header cell allows it.
    columns = ([{"name": "Name", "id": RISK_LABEL_COL}, {"name": "USD delta", "id": "usd_delta"},
               {"name": "1% P&L (USD)", "id": "move_1pct"}]
              + [{"name": name, "id": name} for name in scenario_names])
    table = dash_table.DataTable(
        id=RISK_TABLE_ID,
        columns=columns,
        data=display_rows,
        fixed_rows={},
        style_table=_TABLE_STYLE,
        style_cell={**_MONO, "minWidth": "125px", "width": "125px", "maxWidth": "170px"},
        style_cell_conditional=[
            {"if": {"column_id": RISK_LABEL_COL}, "textAlign": "left", "fontWeight": "600",
             "minWidth": "170px", "width": "170px"},
        ],
        style_header={**_HEAD, "whiteSpace": "normal", "height": "auto", "lineHeight": "14px",
                     "textAlign": "center", "verticalAlign": "bottom"},
        style_header_conditional=[
            {"if": {"column_id": RISK_LABEL_COL}, "textAlign": "left"},
        ],
        style_data_conditional=_sign_styles(["usd_delta", "move_1pct"] + scenario_names) + [
            {"if": {"filter_query": "{" + RISK_LABEL_COL + "} contains 'Net USD' || {" + RISK_LABEL_COL + "} contains 'Gross USD'"},
             "fontWeight": "700", "borderTop": "2px solid #1f2933"},
            # Gold/metals (XAU etc.): shown on their own row like any currency, but
            # excluded from the Net/Gross totals above (2026-09-17, "the XAU does not
            # work well" -- CLAUDE.md reports gold separately from FX Net/Gross USD).
            # Distinct, muted styling so it reads as "informational, not counted".
            {"if": {"filter_query": "{kind} = 'commodity'"}, "backgroundColor": "#fbf3e0", "fontStyle": "italic"},
        ],
    )
    children = [html.H4("Risk and scenarios"), table]
    if commodity_caption is not None:
        children.append(commodity_caption)
    return html.Div(className="section", children=children)


# ------------------------------------------------------------------ 5c. open futures block
FUTURES_TABLE_ID = "exposure-futures-table"
FUTURES_COLUMNS = ["instrument", "contracts", "multiplier", "settlement_price", "usd_delta"]

# Gap noted for cash-ladder: engine.ladder.futures_delta.futures_usd_delta only returns
# usd_delta per instrument (by_instrument), not contracts/multiplier/settlement price.
# `details` lets a future caller supply those for display; until then they render "n/a".


def futures_table_frame(futures: Optional[dict] = None, details: Optional[Dict[str, dict]] = None) -> pd.DataFrame:
    futures = futures or DEFAULT_FUTURES
    details = details or {}
    reason = futures.get("reason", "")
    rows = []
    for instrument_id, usd in (futures.get("by_instrument") or {}).items():
        d = details.get(instrument_id, {})
        rows.append({"instrument": instrument_id, "contracts": d.get("contracts", "n/a"),
                     "multiplier": d.get("multiplier", "n/a"), "settlement_price": d.get("price", "n/a"),
                     "usd_delta": usd, "_unavailable": ""})
    for instrument_id in futures.get("missing") or []:
        d = details.get(instrument_id, {})
        rows.append({"instrument": instrument_id, "contracts": d.get("contracts", "n/a"),
                     "multiplier": d.get("multiplier", "n/a"), "settlement_price": "n/a",
                     "usd_delta": None, "_unavailable": reason})
    return pd.DataFrame(rows, columns=FUTURES_COLUMNS + ["_unavailable"])


def futures_table(futures: Optional[dict] = None, details: Optional[Dict[str, dict]] = None):
    frame = futures_table_frame(futures, details)
    if frame.empty:
        return html.P("No open futures for this as-of date.", className="section-kicker")
    records = frame.to_dict("records")
    for row in records:
        row["usd_delta"] = (_unavailable_label(row["_unavailable"]) if row["_unavailable"]
                            else format_amount(row["usd_delta"]))
        row.pop("_unavailable")
    labels = {"instrument": "Instrument", "contracts": "Contracts", "multiplier": "Multiplier",
              "settlement_price": "Settlement price", "usd_delta": "USD delta"}
    return dash_table.DataTable(
        id=FUTURES_TABLE_ID,
        columns=[{"name": labels[c], "id": c} for c in FUTURES_COLUMNS],
        data=records,
        fixed_rows={},
        style_table=_TABLE_STYLE,
        style_cell={**_MONO, "minWidth": "125px", "width": "125px", "maxWidth": "170px"},
        style_cell_conditional=[
            {"if": {"column_id": "instrument"}, "textAlign": "left", "fontWeight": "600",
             "minWidth": "170px", "width": "170px"},
        ],
        style_header=_HEAD,
    )


# ------------------------------------------------------------------ 5d. per-pair Position (dollar convention)
PAIR_TABLE_ID = "exposure-pair-table"
PAIR_LABEL_COL = "pair"
PAIR_DISPLAY_COLUMNS = ["spot", "notional_base", "notional_usd", "move_1pct_usd"]

# 2026-09-17 user decision ("for aud, eur and gbp - convention adjusted for dollar
# convention - it needs to be done"): renders engine.ladder.ladder.per_pair_delta, one
# row per open FX pair, with BOTH notional sign conventions shown side by side and
# explicitly labelled -- see that function's module-level docstring in engine/ladder/
# ladder.py for the exact formulas and the reasoning for each. Data comes from the
# caller (ui/tabs/cash_ladder.py, which has the DB connection); this module only formats.


def pair_position_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Format `engine.ladder.ladder.per_pair_delta` output for display. `spot` keeps
    6 dp (a thousands-rounded USD-style format would destroy a sub-1.0 quote like
    AUDUSD 0.66); the two notional columns and the 1%-move use the shared `format_amount`
    like every other USD figure on this tab. A cross pair is suffixed "(cross)", a
    commodity pair (XAUUSD) "(metal)", so the two special cases documented in
    per_pair_delta's docstring are visible in the table itself, not just in a caption."""
    if df.empty:
        return pd.DataFrame(columns=[PAIR_LABEL_COL] + PAIR_DISPLAY_COLUMNS)

    def _label(r) -> str:
        if r["commodity"]:
            return r["instrument_id"] + " (metal)"
        if r["cross"]:
            return r["instrument_id"] + " (cross)"
        return r["instrument_id"]

    out = pd.DataFrame({
        PAIR_LABEL_COL: df.apply(_label, axis=1),
        "spot": df["spot"].map(lambda v: "" if pd.isna(v) else f"{v:.6f}"),
        "notional_base": df["notional_base"].map(format_amount),
        "notional_usd": df["notional_usd"].map(format_amount),
        "move_1pct_usd": df["move_1pct_usd"].map(format_amount),
    })
    return out


def pair_position_table(df: Optional[pd.DataFrame]) -> html.Div:
    """"Position" table (CLAUDE.md: "the sheet's Position", per-pair delta grouped by
    instrument_id), FX_SPOT/FX_FWD/FX_SWAP only (see per_pair_delta's scope note).
    `df` is `engine.ladder.ladder.per_pair_delta`'s own output, or None/empty when the
    caller has no DB access yet (e.g. an import error) -- rendered as a plain message,
    never a missing table."""
    if df is None or df.empty:
        return html.Div(className="section", children=[
            html.H4("Position (per pair, dollar convention)"),
            html.P("No open FX forward/spot/swap pairs for this as-of date.", className="section-kicker"),
        ])
    frame = pair_position_frame(df)
    columns = ([{"name": "Pair", "id": PAIR_LABEL_COL}, {"name": "Spot (market quote)", "id": "spot"},
               {"name": "Notional (base ccy)", "id": "notional_base"},
               {"name": "USD notional (USD sign: + long USD)", "id": "notional_usd"},
               {"name": "1% P&L (USD)", "id": "move_1pct_usd"}])
    table = dash_table.DataTable(
        id=PAIR_TABLE_ID,
        columns=columns,
        data=frame.to_dict("records"),
        fixed_rows={},
        style_table=_TABLE_STYLE,
        style_cell={**_MONO, "minWidth": "150px", "width": "150px", "maxWidth": "220px"},
        style_cell_conditional=[
            {"if": {"column_id": PAIR_LABEL_COL}, "textAlign": "left", "fontWeight": "600",
             "minWidth": "150px", "width": "150px"},
        ],
        style_header={**_HEAD, "whiteSpace": "normal", "height": "auto", "lineHeight": "14px",
                     "textAlign": "center", "verticalAlign": "bottom"},
        style_header_conditional=[{"if": {"column_id": PAIR_LABEL_COL}, "textAlign": "left"}],
        style_data_conditional=_sign_styles(["notional_base", "notional_usd", "move_1pct_usd"]),
    )
    note = html.P(
        "“Notional (base ccy)” is signed by the base currency's own direction "
        "(the xlsx / CLAUDE.md “Display notional” convention: + = bought the "
        "base currency). “USD notional” is signed by the USD direction itself "
        "(+ = long USD, the “dollar convention”): identical to the base-ccy "
        "column for USDJPY-style pairs (base_ccy = USD), sign-flipped for AUDUSD/EURUSD/"
        "GBPUSD/XAUUSD-style pairs (quote_ccy = USD) -- buying the base currency there "
        "means selling USD. “1% P&L” always follows the base-ccy sign, so long "
        "AUDUSD gains when AUDUSD rises. A cross pair (cross, no USD leg, e.g. EURSEK) "
        "prices both notional columns off the base currency's own USD spot alone -- the "
        "quote currency's own exposure (e.g. SEK) stays fully visible, independently "
        "converted, in the currency table above; it is never merged into one EURSEK "
        "line here or there.",
        className="section-kicker",
    )
    return html.Div(className="section", children=[html.H4("Position (per pair, dollar convention)"), table, note])


# ------------------------------------------------------------------ section
def exposure_section(records: List[dict], unresolved: list, as_of_date: str,
                     rates: Dict[str, dict] | None = None,
                     sort: str = SORT_USD, scope: str = SCOPE_ALL,
                     futures: Optional[dict] = None,
                     futures_details: Optional[Dict[str, dict]] = None,
                     fallback_ccys: Optional[set] = None,
                     forward_proxy_ccys: Optional[set] = None,
                     exposure_records: Optional[List[dict]] = None,
                     pair_positions: Optional[pd.DataFrame] = None) -> html.Div:
    """Ladder tab body per the user's 2026-09-15 "Reorder the Ladder tab" decision
    (items 1-2, superseding the same-day C-split layout below): three headline cards,
    then three tables in this order -- (a) the currency ladder grid with its summary
    rows ("Cash ladder: spot, forwards, swaps and option deltas" -- option delta records
    come from engine.ladder.exposure_adapter.option_records_from_db, 2026-09-17; cash
    balances have had no source since the BNP upload was removed), (b) the open-futures
    block ("Open futures"), (c) the combined risk table ("Risk and scenarios"). Nothing
    else is rendered on this tab (snapshot cards, metadata line, legend, alternative
    views and the settlement-only ladder are retired, not moved).

    `rates` is whatever the marks table holds -- `data.bloomberg.live.rates_from_marks`
    (official SPOT, `marks_official`) only; None/empty means every currency is MISSING.
    2026-09-17 ("no bnp fall back" -- user decision): the caller (`ui/tabs/
    cash_ladder.py`) no longer merges in any BNP-sourced fallback rate at all, so
    `fallback_ccys`/`forward_proxy_ccys` are always empty in practice now -- the
    parameters and the '*'-in-the-risk-table / "Rate source" row machinery below are
    kept as a generic, currently-dormant labelling mechanism (still directly exercised
    by tests/test_ui_ladder.py) rather than removed, in case a non-official fallback
    source is ever wired back in. `futures` is the dict from
    `engine.ladder.futures_delta.futures_usd_delta` (or DEFAULT_FUTURES).

    `exposure_records` (optional): a second record set built with `settle_date > as_of`
    (engine.ladder.exposure_adapter.exposure_records_from_db), used only for the
    delta/exposure math (headline Net/Gross card and the risk-and-scenarios table).
    `records` (`settle_date >= as_of`) still drives the grid display (`combined_table`)
    unchanged -- a leg settling exactly on `as_of` is cash that moves today (still shown
    in the grid) but carries no delta by close (excluded from Net/Gross and the risk
    table). Defaults to `records` when not supplied, for backward compatibility.

    `pair_positions` (optional, 2026-09-17 "dollar convention" decision): the DataFrame
    from `engine.ladder.ladder.per_pair_delta(conn, as_of_date)`, supplied by the caller
    (which owns the DB connection -- this module never touches the DB itself). Renders a
    fourth table, "Position (per pair, dollar convention)", after the risk table. None
    (the default) renders that table's own "no open pairs" message rather than omitting
    the section, so its presence in the layout never depends on whether the caller
    remembered to pass it."""
    from engine.ladder.exposure import build_exposure
    from engine.pnl.stress import load_scenarios, futures_pct_by_scenario
    rates = rates or {}
    futures = futures or DEFAULT_FUTURES
    fallback_ccys = fallback_ccys or set()
    forward_proxy_ccys = forward_proxy_ccys or set()
    all_fallback = fallback_ccys | forward_proxy_ccys
    result = build_exposure(records, rates)
    exposure_result = (build_exposure(exposure_records, rates)
                       if exposure_records is not None else result)
    empty = result.ladder.empty
    scenarios = load_scenarios()
    main = (combined_table(result, records, sort, all_fallback) if not empty
            else html.P("No open FX trades for this as-of date.", className="section-kicker"))
    return html.Div(className="section", children=[
        headline_numbers(exposure_result, futures, fallback_ccys, forward_proxy_ccys),
        html.H4("Cash ladder: spot, forwards, swaps and option deltas"),
        main,
        html.H4("Open futures"),
        futures_table(futures, futures_details),
        combined_risk_table(exposure_result, futures, scenarios, futures_pct_by_scenario(scenarios), all_fallback),
        pair_position_table(pair_positions),
    ])
