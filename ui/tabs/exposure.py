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

2026-09-21 (user decisions, CLAUDE.md "Ladder"): the grid is TRANSPOSED -- one row per
currency, "Settled cash" then the dates across, a "Total (columns shown)" column and a
bottom "USD equivalent" row (`combined_frame` / `combined_table`) -- and the rate /
delta block that used to be its bottom rows is its own table ABOVE it, currencies
across as before (`summary_block_frame` / `summary_block_table`). NDF currencies are
dated on fixing dates and valued at Bloomberg's 1M NDF price, never spot
(engine.ladder.ndf): the row label says so, the rate cell shows 'KWN+1M 1,394.5', and a
missing 1M price is a blank with the engine's reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd
from dash import dash_table, dcc, html

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
            # The engine's own sentence, the one the app header uses too: a currency with
            # no official SPOT, and an NDF currency whose 1M NDF price is missing (never
            # valued at spot), are named apart, each with its reason (2026-09-21).
            from engine.ladder.ndf import missing_rate_reason
            reasons.append(missing_rate_reason(totals["missing"]))
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
COMBINED_TABLE_ID = "exposure-combined-table"            # the grid: currencies down, dates across
SUMMARY_BLOCK_TABLE_ID = "exposure-summary-block-table"  # rate / delta block above it, currencies across
RATE_REASONS_ID = "exposure-rate-reasons"
NDF_CAPTION_ID = "exposure-ndf-fixing-caption"
ROW_LABEL_COL = "row"
CURRENCY_COL = "currency"      # the grid row's plain currency code ('' on the USD equivalent row)
TOTAL_COL = "total"
SETTLED_ROW_LABEL = "Settled cash"
TOTAL_COLUMN_LABEL = "Total shown"
USD_EQUIVALENT_ROW_LABEL = "USD equivalent"
FX_RATE_ROW_LABEL = "FX rate (as quoted)"
SUMMARY_ROWS = [(FX_RATE_ROW_LABEL, "fx_rate"), ("Local delta", "local_delta"), ("USD delta", "usd_delta")]
_GRID_META_COLUMNS = (ROW_LABEL_COL, CURRENCY_COL, "kind")


def format_quoted_rate(value) -> str:
    """A rate the way Bloomberg quotes it: '1,394.5' for USDKRW, '0.66' for AUDUSD,
    '147.25' for USDJPY -- up to 5 decimals, trailing zeros dropped, thousands
    separated. Blank for NaN/None. Shown instead of the USD-per-local fraction
    (2026-09-18): '0.000714' for KRW cannot be checked by eye, '1,394.5' can, and a mark
    stored at the wrong scale (1.3945) is then visible at a glance."""
    if value is None or pd.isna(value):
        return ""
    text = f"{float(value):,.5f}".rstrip("0").rstrip(".")
    return text or "0"


def _ladder_row_order(index) -> list:
    """Settlement rows in display order: the settled-cash row (engine.ladder.
    exposure_adapter.SETTLED) first, then value dates ascending."""
    from engine.ladder.exposure_adapter import SETTLED
    dates = sorted(str(d) for d in index if str(d) != SETTLED)
    return ([SETTLED] if any(str(d) == SETTLED for d in index) else []) + dates


# ------------------------------------------------------------------ 4a. view filters (user's cash-ladder spec, 2026-09-18)
@dataclass(frozen=True)
class LadderView:
    """What the grid shows (the spec's "Cash ladder tab" controls). Filters change the
    GRID only: the headline card and the risk/scenario tables below always use the
    whole book. `currencies` None = every currency; `date_from`/`date_to` (ISO,
    inclusive) apply to the value-date columns -- the rolled "Settled cash" column is
    always the full settled history, never a date slice; `settled_one_by_one` shows each
    settled leg on its own value date instead of one rolled column; `show_usd` puts USD
    equivalents in the cells instead of local amounts (`ladder_usd_cells`)."""
    currencies: Optional[frozenset] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    settled_one_by_one: bool = False
    show_usd: bool = False


DEFAULT_VIEW = LadderView()


def grid_records(records: List[dict], view: Optional[LadderView] = None) -> List[dict]:
    """Records for the grid under `view`: the currency filter drops other currencies'
    records (grid only), and `settled_one_by_one` re-keys each settled record to its
    own `settled_on` date so the settled history is shown per date instead of rolled
    into one row. Nothing is dropped or re-dated on the engine side."""
    from engine.ladder.exposure_adapter import SETTLED
    view = view or DEFAULT_VIEW
    out = []
    for r in records:
        if view.currencies is not None and r["currency"] not in view.currencies:
            continue
        if view.settled_one_by_one and r["settlement_date"] == SETTLED and r.get("settled_on"):
            r = {**r, "settlement_date": r["settled_on"]}
        out.append(r)
    return out


def _rows_in_view(index, view: LadderView) -> list:
    """Keys of the engine ladder's date rows to show (they are the grid's COLUMNS since
    2026-09-21): the settled key always, value dates inside the range."""
    from engine.ladder.exposure_adapter import SETTLED
    rows = []
    for day in _ladder_row_order(index):
        if day != SETTLED:
            if view.date_from and str(day) < view.date_from:
                continue
            if view.date_to and str(day) > view.date_to:
                continue
        rows.append(day)
    return rows


def _shown_currencies(ladder: pd.DataFrame, ordered: List[str], rows: list) -> List[str]:
    """`ordered` minus the currencies that are zero on every shown date (the spec hides
    all-zero currencies in the view). Nothing hidden when no date is shown."""
    if not rows:
        return list(ordered)
    keep = [c for c in ordered if c in ladder.columns and bool((ladder.loc[rows, c] != 0).any())]
    return keep or list(ordered)
# "Settlement type" (NDF / Deliverable) summary row and its NDF super-header removed
# 2026-09-15 per the user's decision. Since 2026-09-21 an NDF currency's grid row is
# labelled by engine.ladder.ndf.currency_label ('KRW (NDF)').


def grid_shape(result, records: List[dict], sort: str = SORT_USD, view: Optional[LadderView] = None):
    """`(dates, currencies)` the view shows, shared by the summary block, the grid, the
    heatmap and the Ladder CSV so they can never disagree: `dates` = the engine ladder's
    keys in display order (settled first, then value dates ascending, inside From / To);
    `currencies` ordered by |USD delta| descending (or A-Z), all-zero ones hidden."""
    view = view or DEFAULT_VIEW
    summary = summary_frame(result, records, sort=sort, scope=SCOPE_ALL)
    shown = _rows_in_view(result.ladder.index, view)
    return shown, _shown_currencies(result.ladder, list(summary["currency"]), shown)


def _grid_numbers(result, shown: list, ccys: List[str],
                  forward_rates: Optional[Mapping[tuple, dict]], show_usd: bool):
    """The grid as numbers, one computation for the screen and the CSV: `body` (index =
    currencies, columns = the shown dates + TOTAL_COL; local amounts, or USD equivalents
    when `show_usd`) and `usd_row` (same columns: each date's USD equivalent at its own
    outright over every currency of the grid, NaN where any non-zero amount has no mark;
    its TOTAL_COL is the sum of the dates shown, NaN if any of them is)."""
    from engine.ladder.exposure import ladder_usd_cells
    usd_cells = ladder_usd_cells(result, forward_rates)
    cells = (usd_cells if show_usd else result.ladder).reindex(columns=ccys, fill_value=0.0)
    body = cells.loc[shown].T if shown else pd.DataFrame(index=ccys)
    usd_eq = usd_cells.sum(axis=1, skipna=False) if not usd_cells.empty else pd.Series(dtype=float)
    usd_row = usd_eq.reindex(shown)
    if shown:
        body[TOTAL_COL] = cells.loc[shown].sum(axis=0, skipna=False)
        usd_row[TOTAL_COL] = usd_row.sum(skipna=False)
    return body, usd_row


def _rate_status(result):
    """`(status, message)` dicts per currency from the engine's status frame."""
    if result.status.empty:
        return {}, {}
    return (dict(zip(result.status["currency"], result.status["status"])),
            dict(zip(result.status["currency"], result.status["message"])))


def summary_block_frame(result, records: List[dict], sort: str = SORT_USD,
                        fallback_ccys: Optional[set] = None,
                        rates: Optional[Dict[str, dict]] = None,
                        view: Optional[LadderView] = None):
    """The rate / delta block shown ABOVE the grid (user decision 2026-09-21: "the rows
    at the bottom showing delta, fx rate etc - they stay as they are and get put at the
    top of the table"): rows FX rate (as quoted), Local delta, USD delta, Rate source;
    one column per currency in the grid's own row order (`grid_shape`), and TOTAL_COL
    carrying the net USD delta total on the USD delta row, where it was before. Always
    the whole grid's deltas, never a date slice. Returns `(frame, currencies)`; every
    value comes from build_exposure / portfolio_totals.

    The FX rate row shows the rate as Bloomberg quotes it under the name the engine
    gives it: `rates[ccy]['label']` for an NDF currency priced at its 1M NDF mark
    ('KWN+1M 1,394.5', engine.ladder.ndf.apply_ndf_1m_rates), else the pair ('USDJPY
    147.25'), else the engine's USD-per-local rate. A currency with no rate -- an NDF
    currency whose 1M price is missing has none, spot is never used for it -- is BLANK
    there and on the USD delta row, and the 'Rate source' row carries the engine's own
    reason ('MISSING: ...'; 'SUSPECT: ...' for a mark the plausibility guard refused)
    instead of 'Bloomberg'. `rate_reasons_caption` repeats those reasons in full.

    `fallback_ccys` (user decision 2026-09-15, item D; the BNP_BVAL source it originally
    labelled was removed outright 2026-09-17, "no bnp fall back" -- the live app never
    populates this set any more, see `headline_numbers`'s docstring): currencies priced
    from a non-official fallback rate, were one ever supplied, named in 'Rate source'."""
    from engine.ladder.exposure import portfolio_totals
    fallback_ccys = fallback_ccys or set()
    rates = rates or {}
    summary = summary_frame(result, records, sort=sort, scope=SCOPE_ALL)
    _shown, ccys = grid_shape(result, records, sort, view)
    fx = result.summary.set_index("currency")["fx_rate"]
    status_of, status_msg = _rate_status(result)
    totals = portfolio_totals(result)
    by_ccy = summary.set_index("currency")
    rows = []
    for label, key in SUMMARY_ROWS + [("Rate source", "rate_source")]:
        row = {ROW_LABEL_COL: label, "kind": key}
        for c in ccys:
            entry = rates.get(c)
            if key == "fx_rate":
                if c == "USD":
                    row[c] = "1"
                elif entry is not None and entry.get("rate") is not None:
                    name = entry.get("label") or entry.get("pair") or ""
                    row[c] = (f"{name} " if name else "") + format_quoted_rate(entry["rate"])
                else:
                    v = fx.get(c, float("nan"))
                    row[c] = "" if pd.isna(v) else f"{v:.6g}"
            elif key == "rate_source":
                if status_of.get(c) == "SUSPECT_RATE":
                    row[c] = "SUSPECT: " + status_msg.get(c, "")
                elif status_of.get(c) == "MISSING_RATE":
                    row[c] = "MISSING: " + status_msg.get(c, "")
                elif c in fallback_ccys:
                    row[c] = "BNP file"
                else:
                    row[c] = "Bloomberg 1M NDF" if (entry or {}).get("mark_type") == "NDF_1M" else "Bloomberg"
            else:
                row[c] = format_amount(by_ccy.loc[c, key])
        row[TOTAL_COL] = format_amount(totals["net_usd"]) if key == "usd_delta" else ""
        rows.append(row)
    return pd.DataFrame(rows, columns=[ROW_LABEL_COL, "kind"] + ccys + [TOTAL_COL]), ccys


def _summary_block_datatable(frame: pd.DataFrame, ccys: List[str]) -> dash_table.DataTable:
    columns = ([{"name": "Per currency", "id": ROW_LABEL_COL}]
               + [{"name": c, "id": c} for c in ccys]
               + [{"name": "Total", "id": TOTAL_COL}])
    return dash_table.DataTable(
        id=SUMMARY_BLOCK_TABLE_ID,
        columns=columns,
        data=frame.to_dict("records"),
        fixed_rows={},
        style_table=_TABLE_STYLE,
        style_cell={**_MONO, "minWidth": "125px", "width": "125px", "maxWidth": "170px"},
        style_cell_conditional=[
            {"if": {"column_id": ROW_LABEL_COL}, "textAlign": "left", "fontWeight": "600", "minWidth": "170px", "width": "170px"},
            {"if": {"column_id": TOTAL_COL}, "fontWeight": "600", "borderLeft": "2px solid #d9dee3"},
        ],
        style_header=_HEAD,
        style_data_conditional=_sign_styles(ccys + [TOTAL_COL]) + [
            # Visual hierarchy of the block (user decision 2026-09-15, unchanged by the
            # 2026-09-21 move above the grid): FX rate bold (the bridge between local and
            # USD); local delta medium; USD delta the single heaviest row with a navy tint
            # (the answer the block exists to give); rate source small and muted -- it is
            # provenance, so it is grey, except a cell that says why a rate is missing or
            # was refused, which is red.
            {"if": {"filter_query": "{kind} = 'fx_rate'"}, "color": "#1b2333", "fontWeight": "700"},
            {"if": {"filter_query": "{kind} = 'local_delta'"}, "fontWeight": "500"},
            {"if": {"filter_query": "{kind} = 'usd_delta'"}, "fontWeight": "700", "backgroundColor": "#e8edf7",
             "borderTop": "1px solid #c8d0e0", "borderBottom": "1px solid #c8d0e0"},
            {"if": {"filter_query": "{kind} = 'rate_source'"}, "color": "#6b7280", "fontWeight": "400", "fontSize": "11px"},
        ] + [
            {"if": {"column_id": c, "filter_query": f"{{kind}} = 'rate_source' && {{{c}}} contains '{word}'"},
             "color": "#b42318", "fontWeight": "600"}
            for c in ccys for word in ("SUSPECT", "MISSING")
        ],
    )


def summary_block_table(result, records: List[dict], sort: str = SORT_USD,
                        fallback_ccys: Optional[set] = None,
                        rates: Optional[Dict[str, dict]] = None,
                        view: Optional[LadderView] = None) -> dash_table.DataTable:
    frame, ccys = summary_block_frame(result, records, sort, fallback_ccys, rates=rates, view=view)
    return _summary_block_datatable(frame, ccys)


def rate_reasons_caption(result, ccys: Optional[List[str]] = None):
    """One line under the rate / delta block naming every shown currency whose rate is
    missing or was refused, with the engine's own plain-language reason in full (a table
    cell cuts a sentence short). An NDF currency with no 1M NDF price on file is named
    here with the pull button that fetches it; it is never valued at spot (CLAUDE.md
    hard rule 2). None when every shown currency has a rate."""
    status_of, status_msg = _rate_status(result)
    wanted = list(ccys) if ccys is not None else list(status_of)
    parts = [f"{c}: {status_msg.get(c, '')}" for c in wanted
             if status_of.get(c) in ("MISSING_RATE", "SUSPECT_RATE")]
    if not parts:
        return None
    return html.P("Shown blank, never a substitute rate. " + " | ".join(parts) + ".",
                  id=RATE_REASONS_ID, className="section-kicker")


def combined_frame(result, records: List[dict], sort: str = SORT_USD,
                   forward_rates: Optional[Mapping[tuple, dict]] = None,
                   view: Optional[LadderView] = None):
    """The grid (user decision 2026-09-21, "flip the other way, currency vertical, dates
    horizontal"): one ROW per currency, ordered by |USD delta| descending (or A-Z);
    COLUMNS = "Settled cash" first (engine.ladder.exposure_adapter.SETTLED, "expired
    tickets must settle not disappear"), then the value dates ascending (column id = the
    ISO date), then TOTAL_COL "Total (columns shown)"; and a bottom "USD equivalent" row
    (kind 'usd_equivalent') with each date's USD equivalent and their total. Returns
    `(frame, currencies)`; frame columns = ROW_LABEL_COL, CURRENCY_COL, 'kind', then the
    value columns in display order. Every number comes from build_exposure /
    ladder_usd_cells (`_grid_numbers`); nothing is computed here.

    The row label is engine.ladder.ndf.currency_label ('KRW (NDF)' for an
    NDF currency, whose records are dated on fixing dates); CURRENCY_COL keeps the plain
    code, so nothing that filters or looks up by currency depends on the label.

    `forward_rates` (engine.ladder.usd_marks.forward_usd_rates, spec 2026-09-18) marks
    each cell's USD equivalent at its OWN value date's outright (settled cash at spot, an
    NDF currency at its 1M NDF price on every date), so the USD equivalent row's total is
    the book's FX value at outrights, undiscounted; a date where any non-zero amount has
    no mark is blank, never a partial sum. `view` (LadderView) selects the currencies and
    dates shown and, with `show_usd`, puts those USD equivalents in the cells."""
    from engine.ladder.ndf import currency_label
    view = view or DEFAULT_VIEW
    shown, ccys = grid_shape(result, records, sort, view)
    body, usd_row = _grid_numbers(result, shown, ccys, forward_rates, view.show_usd)
    value_cols = list(body.columns)
    rows = []
    for c in ccys:
        row = {ROW_LABEL_COL: currency_label(c), CURRENCY_COL: c, "kind": "currency"}
        row.update({col: format_amount(body.at[c, col]) for col in value_cols})
        rows.append(row)
    if shown:
        row = {ROW_LABEL_COL: USD_EQUIVALENT_ROW_LABEL, CURRENCY_COL: "", "kind": USD_EQUIVALENT_COL}
        row.update({col: format_amount(usd_row[col]) for col in value_cols})
        rows.append(row)
    return pd.DataFrame(rows, columns=list(_GRID_META_COLUMNS) + value_cols), ccys


def grid_value_columns(frame: pd.DataFrame) -> List[str]:
    """The grid frame's value column ids in display order: the settled key, the ISO
    dates, TOTAL_COL."""
    return [c for c in frame.columns if c not in _GRID_META_COLUMNS]


def _grid_column_name(col: str, with_year: bool = True) -> str:
    """A grid column's header. Kept short: a header's width is paid on every date column
    (user, 2026-09-21: "make the columns thinner - theres so much wasted space so you have
    to scroll a lot"). '24 Sep' when every date shown is in one year (`with_year` False),
    '24 Sep 26' otherwise."""
    from engine.ladder.exposure_adapter import SETTLED
    if col == SETTLED:
        return SETTLED_ROW_LABEL
    if col == TOTAL_COL:
        return TOTAL_COLUMN_LABEL
    text = format_date(col)                       # '24 Sep 2026', or `col` itself when not a date
    if text == col:
        return text
    return f"{text[:6]} {text[-2:]}" if with_year else text[:6]


# The rate / delta figures sit IN the grid, as its first columns (user, 2026-09-21: the
# separate block above the grid was "super clunky with the other stuff above and
# scrolling odd"): each currency's rate, local delta and USD delta read across on its own
# row, in one table with one scrollbar. The bottom USD row carries the net USD delta.
SUMMARY_GRID_COLUMNS = [("fx_rate", "FX rate"), ("local_delta", "Local delta"), ("usd_delta", "USD delta")]


def grid_records_with_summary(frame: pd.DataFrame, summary: Optional[pd.DataFrame]) -> List[dict]:
    """The grid's records with `summary_block_frame`'s three figures added to each
    currency row under the SUMMARY_GRID_COLUMNS ids; the bottom USD row takes the block's
    own Total (the net USD delta under 'usd_delta', blank elsewhere). Display only: the
    same formatted strings the block shows, nothing recomputed."""
    records = frame.to_dict("records")
    if summary is None or summary.empty:
        return records
    by_kind = summary.set_index("kind")
    for rec in records:
        col = rec.get(CURRENCY_COL) if rec.get("kind") == "currency" else TOTAL_COL
        for key, _label in SUMMARY_GRID_COLUMNS:
            rec[key] = by_kind.at[key, col] if (key in by_kind.index and col in by_kind.columns) else ""
    return records


def _grid_datatable(frame: pd.DataFrame, view: Optional[LadderView] = None,
                    summary: Optional[pd.DataFrame] = None) -> dash_table.DataTable:
    from engine.ladder.exposure_adapter import SETTLED
    view = view or DEFAULT_VIEW
    value_cols = grid_value_columns(frame)
    summary_cols = [] if summary is None or summary.empty else SUMMARY_GRID_COLUMNS
    several_years = len({c[:4] for c in value_cols if c not in (SETTLED, TOTAL_COL)}) > 1
    columns = ([{"name": "Currency (USD eq.)" if view.show_usd else "Currency", "id": ROW_LABEL_COL}]
               + [{"name": label, "id": key} for key, label in summary_cols]
               + [{"name": _grid_column_name(c, several_years), "id": c} for c in value_cols])
    # The currency label stays in sight when the dates scroll sideways. Done with
    # `position: sticky` on that one column, NOT Dash's `fixed_columns`: that option
    # splits the table into separate fixed and scrolling tables whose widths and row
    # heights can drift apart, and it could not be checked in a browser for this change;
    # a sticky cell that a browser ignores just scrolls like any other column. The cell
    # needs its own opaque background or the dates show through it.
    sticky = {"position": "sticky", "left": 0, "zIndex": 2, "backgroundColor": "#ffffff",
              "boxShadow": "1px 0 0 #d9dee3"}
    return dash_table.DataTable(
        id=COMBINED_TABLE_ID,
        columns=columns,
        data=grid_records_with_summary(frame, summary),  # includes CURRENCY_COL (plain code) and kind, not displayed
        style_table=_TABLE_STYLE,
        # Thin columns (user, 2026-09-21): no fixed width, so each column is as wide as its
        # own longest number and no wider; tighter padding and rows than the other tables.
        style_cell={**_MONO, "fontSize": "12px", "padding": "3px 8px", "height": "26px", "minWidth": "48px"},
        style_cell_conditional=[
            {"if": {"column_id": ROW_LABEL_COL}, "textAlign": "left", "fontWeight": "600", **sticky},
            # the rate / delta columns: the rate bold (the bridge between local and USD),
            # USD delta the heaviest, tinted and ruled off from the cash columns beside it
            {"if": {"column_id": "fx_rate"}, "fontWeight": "700", "color": "#1b2333"},
            {"if": {"column_id": "usd_delta"}, "fontWeight": "700", "backgroundColor": "#e8edf7",
             "borderRight": "2px solid #1f2933"},
        ],
        style_header={**_HEAD, "fontSize": "12px", "padding": "4px 8px"},
        style_header_conditional=[
            {"if": {"column_id": ROW_LABEL_COL}, "textAlign": "left", "position": "sticky", "left": 0, "zIndex": 3},
        ],
        style_data_conditional=_sign_styles(value_cols + [k for k, _ in summary_cols if k != "fx_rate"]) + [
            # USD equivalent (the old right-hand column, now the bottom row): ruled off
            # from the currency rows above it.
            {"if": {"filter_query": f"{{kind}} = '{USD_EQUIVALENT_COL}'"}, "fontWeight": "700",
             "backgroundColor": "#f7f8fa", "borderTop": "2px solid #1f2933"},
            # Settled cash (2026-09-18): the balance the value dates add to -- first
            # column, bold, tinted, ruled off from the dated flows beside it.
            {"if": {"column_id": SETTLED}, "fontWeight": "700", "backgroundColor": "#eef7ee",
             "borderRight": "2px solid #1f2933"},
            # Total of the columns shown (spec 2026-09-18): the view's own sums.
            {"if": {"column_id": TOTAL_COL}, "fontWeight": "700", "backgroundColor": "#f1f3f7",
             "borderLeft": "1px solid #c8d0e0"},
        ],
    )


def combined_table(result, records: List[dict], sort: str = SORT_USD,
                   forward_rates: Optional[Mapping[tuple, dict]] = None,
                   view: Optional[LadderView] = None) -> dash_table.DataTable:
    frame, _ccys = combined_frame(result, records, sort, forward_rates=forward_rates, view=view)
    return _grid_datatable(frame, view)


def ndf_fixing_caption(result, unresolved: Optional[list] = None):
    """engine.ladder.ndf.FIXING_CAPTION, the one line under the grid that says NDF rows
    are dated on fixing dates -- shown when the grid holds an NDF currency, or when an
    NDF ticket that has already fixed is named below it. None otherwise."""
    from engine.ladder.exposure_adapter import NDF_FIXED_REASON_PREFIX
    from engine.ladder.ndf import FIXING_CAPTION, is_ndf_currency
    in_grid = any(is_ndf_currency(c) for c in result.ladder.columns)
    fixed = any(str(getattr(u, "reason", "")).startswith(NDF_FIXED_REASON_PREFIX) for u in (unresolved or []))
    if not (in_grid or fixed):
        return None
    return html.P(FIXING_CAPTION, id=NDF_CAPTION_ID, className="section-kicker")


# ------------------------------------------------------------------ 4c. settled tickets caption
SETTLED_CAPTION_ID = "exposure-settled-caption"


_VALUE_DATE_IN_REASON = re.compile(r"value date (\d{4}-\d{2}-\d{2})")


def _named_tickets(items: list, with_value_date: bool = False) -> str:
    """'USDKRW (n1), ...' for the first six tickets, then 'and N more'."""
    names = []
    for u in items[:6]:
        hit = _VALUE_DATE_IN_REASON.search(str(getattr(u, "reason", ""))) if with_value_date else None
        names.append(f"{u.symbol} ({u.trade_id}" + (f", value date {format_date(hit.group(1))}" if hit else "") + ")")
    return ", ".join(names) + (f" and {len(items) - 6} more" if len(items) > 6 else "")


def settled_unknown_caption(unresolved: list):
    """Under the ladder grid: the non-deliverable tickets (NDF forwards, futures,
    options) that are off the grid while their USD settlement is not in Settled cash yet
    (engine.ladder.exposure_adapter lists them in `unresolved` with a reason starting
    'settled'). Two kinds of waiting, worded apart, one line each:
      - an NDF that has FIXED and is waiting for its value date (reason starting
        NDF_FIXED_REASON_PREFIX): nothing is missing and no Bloomberg pull changes it;
        the ledger realises it the day after the value date;
      - a ticket whose value date has passed and which the ledger could not realise:
        it needs an official mark on or before that date, which a pull fetches.
    Nothing is rendered when there are none. Never a number: a settlement the ledger has
    not realised stays out of the column and is named here instead."""
    from engine.ladder.exposure_adapter import NDF_FIXED_REASON_PREFIX
    items = [u for u in (unresolved or []) if str(getattr(u, "reason", "")).startswith("settled")]
    if not items:
        return None
    fixed = [u for u in items if str(u.reason).startswith(NDF_FIXED_REASON_PREFIX)]
    unmarked = [u for u in items if not str(u.reason).startswith(NDF_FIXED_REASON_PREFIX)]
    lines = []
    if unmarked:
        lines.append(html.P(
            f"{len(unmarked)} settled non-deliverable ticket{'s' if len(unmarked) != 1 else ''} not yet in "
            f"Settled cash (USD settlement unknown until realised at an official mark on or before the "
            f"value date -- press \"Pull Bloomberg now\" to fetch it): {_named_tickets(unmarked)}.",
            className="section-kicker"))
    if fixed:
        lines.append(html.P(
            f"{len(fixed)} NDF ticket{'s have' if len(fixed) != 1 else ' has'} fixed and "
            f"{'are' if len(fixed) != 1 else 'is'} waiting for the value date: off the grid since the "
            f"fixing, and in Settled cash the day after the value date, when the USD settlement is "
            f"realised (nothing is missing, no Bloomberg pull is needed): "
            f"{_named_tickets(fixed, with_value_date=True)}.",
            className="section-kicker"))
    return html.Div(lines, id=SETTLED_CAPTION_ID)


# ------------------------------------------------------------------ 4d. USD basis, heatmap, local vs USD, downloads (spec 2026-09-18)
USD_BASIS_CAPTION_ID = "exposure-usd-basis-caption"
HEATMAP_ID = "exposure-ladder-heatmap"
LOCAL_VS_USD_ID = "exposure-local-vs-usd"
LOCAL_VS_USD_DETAILS_ID = "exposure-local-vs-usd-details"
# Diverging fill for the heatmap: red = pay, blue = receive, neutral grey at zero (two
# hues plus a neutral midpoint, symmetric around 0 -- never a hue at the midpoint).
HEAT_PAY, HEAT_MID, HEAT_RECEIVE = "#c0392b", "#f0efec", "#1f5fa8"


def usd_basis_caption(forward_rates: Optional[Mapping[tuple, dict]], view: Optional[LadderView] = None) -> html.P:
    """One line saying what the USD equivalents on the grid are marked at. With forward
    marks: each value date at its own official outright (spot on or before the spot
    date, broken dates interpolated between Bloomberg's tenors, flat beyond the last,
    undiscounted), naming any currency with no forward curve on file, which is shown at
    spot rather than hidden, and the NDF currencies valued on BASIS_NDF_1M (Bloomberg's
    1M NDF price on every date, settled cash included, never spot; user decision
    2026-09-21). Without: spot for every date."""
    from engine.ladder.usd_marks import BASIS_NDF_1M, BASIS_NO_CURVE
    view = view or DEFAULT_VIEW
    cells = "Cells are USD equivalents. " if view.show_usd else "Cells are local amounts. "
    if not forward_rates:
        return html.P(cells + "USD equivalent: at spot for every date (no forward marks on file for this day).",
                      id=USD_BASIS_CAPTION_ID, className="section-kicker")
    no_curve = sorted({ccy for (ccy, _day), e in forward_rates.items() if e.get("basis") == BASIS_NO_CURVE})
    ndf_1m = sorted({ccy for (ccy, _day), e in forward_rates.items() if e.get("basis") == BASIS_NDF_1M})
    text = (cells + "USD equivalent (bottom row): each value date at its own official forward outright "
            "(spot on or before the spot date, broken dates interpolated between Bloomberg's tenors, flat "
            "beyond the last tenor; undiscounted), settled cash at spot. Its total is the book's "
            "FX value at outrights; the headline P&L converts at spot and is not this number.")
    if ndf_1m:
        text += (" NDF currencies are valued at Bloomberg's 1M NDF price on every date, settled cash "
                 "included, never at spot: " + ", ".join(ndf_1m) + ".")
    if no_curve:
        text += " No forward curve on file for " + ", ".join(no_curve) + ": shown at spot."
    return html.P(text, id=USD_BASIS_CAPTION_ID, className="section-kicker")


def ladder_heatmap(result, ccys: List[str], forward_rates: Optional[Mapping[tuple, dict]] = None,
                   view: Optional[LadderView] = None):
    """The spec's heatmap: rows = the grid's rows, columns = its currencies, cell colour
    = USD equivalent (diverging, symmetric around zero, red pay / blue receive), hover =
    the local amount and its USD equivalent at that row's mark. Colour is USD so a JPY
    cell and an AUD cell are comparable; the numbers stay in the hover and the table.
    None when there is nothing to draw."""
    import plotly.graph_objects as go
    from engine.ladder.exposure import ladder_usd_cells
    from engine.ladder.exposure_adapter import SETTLED
    view = view or DEFAULT_VIEW
    if result.ladder.empty:
        return None
    shown = _rows_in_view(result.ladder.index, view)
    ccys = [c for c in ccys if c in result.ladder.columns]
    if not shown or not ccys:
        return None
    usd = ladder_usd_cells(result, forward_rates)
    z, local_text, usd_text = [], [], []
    for day in shown:
        z.append([None if pd.isna(usd.loc[day, c]) else float(usd.loc[day, c]) for c in ccys])
        local_text.append([format_amount(result.ladder.loc[day, c]) for c in ccys])
        usd_text.append([format_amount(usd.loc[day, c]) for c in ccys])
    labels = [SETTLED_ROW_LABEL if d == SETTLED else format_date(d) for d in shown]
    peak = max((abs(v) for row in z for v in row if v is not None), default=0.0) or 1.0
    fig = go.Figure(go.Heatmap(
        z=z, x=ccys, y=labels, text=local_text, customdata=usd_text,
        hovertemplate="%{y} · %{x}<br>Local %{text}<br>USD equivalent %{customdata}<extra></extra>",
        colorscale=[[0.0, HEAT_PAY], [0.5, HEAT_MID], [1.0, HEAT_RECEIVE]],
        zmin=-peak, zmax=peak, zmid=0.0, xgap=2, ygap=2, hoverongaps=False,
        colorbar=dict(title=dict(text="USD equivalent", side="right"), thickness=10, len=0.9,
                      tickformat=",.3s", outlinewidth=0),
    ))
    fig.update_layout(
        height=max(140, 36 + 26 * len(shown)), margin=dict(l=8, r=8, t=8, b=8),
        xaxis=dict(side="top", type="category", showgrid=False, fixedrange=True),
        yaxis=dict(autorange="reversed", type="category", showgrid=False, fixedrange=True),
        font=dict(family=_BODY_FONT, size=11, color="#1b2333"),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
    )
    return dcc.Graph(id=HEATMAP_ID, figure=fig, config={"displayModeBar": False})


LOCAL_VS_USD_LABELS = {"currency": "Currency", "settlement_date": "Value date", "net_local": "Net local",
                       "net_local_vs_usd": "Net local vs USD", "net_local_cross": "Net local (crosses)",
                       "net_usd": "Net USD", "implied_rate_local_per_usd": "Implied local per USD"}


def local_vs_usd_details(records: List[dict]):
    """Collapsed "Local vs USD by value date" table (engine.ladder.exposure.
    local_vs_usd): per non-USD currency and value date, the local flows dealt against
    USD, the cross flows, the USD flows and the implied local-per-USD rate the USD legs
    give -- so a cross leg never distorts the implied rate. None when empty."""
    from engine.ladder.exposure import LOCAL_VS_USD_COLUMNS, local_vs_usd
    from engine.ladder.exposure_adapter import SETTLED
    df = local_vs_usd(records)
    if df.empty:
        return None
    data = []
    for _, r in df.iterrows():
        day = r["settlement_date"]
        data.append({
            "currency": r["currency"],
            "settlement_date": SETTLED_ROW_LABEL if day == SETTLED else format_date(day),
            "net_local": format_amount(r["net_local"]), "net_local_vs_usd": format_amount(r["net_local_vs_usd"]),
            "net_local_cross": format_amount(r["net_local_cross"]), "net_usd": format_amount(r["net_usd"]),
            "implied_rate_local_per_usd": ("" if pd.isna(r["implied_rate_local_per_usd"])
                                           else f"{r['implied_rate_local_per_usd']:,.6g}"),
        })
    table = dash_table.DataTable(
        id=LOCAL_VS_USD_ID,
        columns=[{"name": LOCAL_VS_USD_LABELS[c], "id": c} for c in LOCAL_VS_USD_COLUMNS],
        data=data, fixed_rows={}, style_table=_TABLE_STYLE,
        style_cell={**_MONO, "minWidth": "125px", "width": "125px", "maxWidth": "170px"},
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left", "fontWeight": "600"}
                                for c in ("currency", "settlement_date")],
        style_header=_HEAD,
        style_data_conditional=_sign_styles(["net_local", "net_local_vs_usd", "net_local_cross", "net_usd"]),
    )
    return html.Details(id=LOCAL_VS_USD_DETAILS_ID, className="details", children=[
        html.Summary("Local vs USD by value date"),
        html.P("Per currency and value date: every local flow, the part dealt against USD, the part from "
               "crosses, the USD flows dealt against that currency, and the rate they imply "
               "(|net local vs USD / net USD|). Cross legs never enter the implied rate.",
               className="section-kicker"),
        table,
    ])


def ladder_export_frame(result, forward_rates: Optional[Mapping[tuple, dict]] = None,
                        view: Optional[LadderView] = None, ccys: Optional[List[str]] = None) -> pd.DataFrame:
    """The displayed grid as numbers (spec download `cash_ladder.csv`), in the displayed
    orientation (2026-09-21): one row per shown currency, labelled as on screen ('KRW
    (NDF)'), then a 'USD equivalent' row; columns `currency`, 'Settled
    cash', the ISO value dates, 'Total'. Local amounts, or USD equivalents when
    `view.show_usd`. Same numbers as the screen (`_grid_numbers`): a date or a total with
    an unmarked amount is empty in the file, never a partial sum."""
    from engine.ladder.exposure_adapter import SETTLED
    from engine.ladder.ndf import currency_label
    view = view or DEFAULT_VIEW
    if result.ladder.empty:
        return pd.DataFrame(columns=[CURRENCY_COL, "Total"])
    shown = _rows_in_view(result.ladder.index, view)
    ccys = [c for c in (ccys or list(result.ladder.columns)) if c in result.ladder.columns]
    body, usd_row = _grid_numbers(result, shown, ccys, forward_rates, view.show_usd)
    names = {SETTLED: SETTLED_ROW_LABEL, TOTAL_COL: "Total"}
    rows = [{CURRENCY_COL: currency_label(c), **{names.get(col, col): float(body.at[c, col]) for col in body.columns}}
            for c in ccys]
    if shown:
        rows.append({CURRENCY_COL: USD_EQUIVALENT_ROW_LABEL,
                     **{names.get(col, col): float(usd_row[col]) for col in body.columns}})
    return pd.DataFrame(rows, columns=[CURRENCY_COL] + [names.get(col, col) for col in body.columns])


LEGS_EXPORT_COLUMNS = ["trade_id", "product_type", "currency_pair", "currency", "settlement_date", "settled_on",
                       "local_amount", "entry_rate", "usd_per_unit", "mark_basis", "usd_eq", "book", "account"]


def legs_export_frame(records: List[dict], rates: Optional[Dict[str, dict]] = None,
                      forward_rates: Optional[Mapping[tuple, dict]] = None) -> pd.DataFrame:
    """Every leg with its mark and USD equivalent (spec download `legs.csv`): the mark is
    the (currency, value date) forward entry when supplied, else spot, else NaN."""
    from engine.ladder.exposure import usd_per_local
    rates = rates or {}
    forward_rates = forward_rates or {}
    rows = []
    for r in records:
        ccy, day = r["currency"], r["settlement_date"]
        entry = forward_rates.get((ccy, day))
        if entry is not None:
            mark, basis = float(entry["rate"]), str(entry.get("basis", ""))
        elif ccy == "USD":
            mark, basis = 1.0, "identity"
        elif ccy in rates:
            mark, basis = usd_per_local(rates[ccy]), "spot"
        else:
            mark, basis = float("nan"), "no rate"
        rows.append({
            "trade_id": r.get("trade_id"), "product_type": r.get("product_type"),
            "currency_pair": r.get("currency_pair"), "currency": ccy, "settlement_date": day,
            "settled_on": r.get("settled_on", ""), "local_amount": float(r["local_amount"]),
            "entry_rate": r.get("entry_rate"), "usd_per_unit": mark, "mark_basis": basis,
            "usd_eq": float(r["local_amount"]) * mark, "book": r.get("book"), "account": r.get("account"),
        })
    return pd.DataFrame(rows, columns=LEGS_EXPORT_COLUMNS)


# ------------------------------------------------------------------ 5. legend
def legend() -> html.Dl:
    from engine.ladder.ndf import NDF_1M_TICKERS, ticker_label
    ndf_tickers = ", ".join(f"{ccy} {ticker_label(t)}" for ccy, t in sorted(NDF_1M_TICKERS.items()))
    items = [
        ("Local delta", "sum of signed local-currency amounts across all settlement dates."),
        ("USD delta", "local delta x USD-per-local rate. This is a delta table, not P&L "
                      "-- see the Blotter tab for LTD/Daily/MTD/YTD."),
        (NDF_BADGE, NDF_EXPLANATION),
        ("Bloomberg rates", "latest official SPOT mark per currency; an NDF currency uses Bloomberg's 1M NDF "
                            f"price instead, never spot ({ndf_tickers}). Pulled from the Bloomberg terminal on this "
                            "computer when you press \"Pull Bloomberg now\" (never by itself); blank when no mark "
                            "exists. Nothing is substituted."),
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
        "GBPUSD-style pairs (quote_ccy = USD) -- buying the base currency there "
        "means selling USD. A metal (XAUUSD, marked “metal”) is not flipped: gold is a "
        "metal position, not a dollar position, so both columns show + for long gold. "
        "“1% P&L” always follows the base-ccy sign, so long "
        "AUDUSD gains when AUDUSD rises. Settled tickets are not in this table: once a "
        "forward has settled its cash sits in the Settled cash row of the currency "
        "table above. A cross pair (cross, no USD leg, e.g. EURSEK) "
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
                     pair_positions: Optional[pd.DataFrame] = None,
                     forward_rates: Optional[Mapping[tuple, dict]] = None,
                     view: Optional[LadderView] = None) -> html.Div:
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
    remembered to pass it.

    `forward_rates` / `view` (2026-09-18, user's cash-ladder spec): per-(currency, value
    date) USD marks from engine.ladder.usd_marks.forward_usd_rates, and the grid's
    filters/toggles (LadderView). Both affect the grid, its heatmap, its total column and
    the downloads only; the headline card and the risk table are always the whole book
    at spot.

    2026-09-21 (user decisions): the grid is transposed (`combined_frame`: one row per
    currency, dates across, a bottom USD equivalent row) and the rate / delta figures
    (`summary_block_frame`) are its first columns, FX rate / Local delta / USD delta on
    each currency's own row (first built as a table of its own above the grid, which the
    user found clunky and odd to scroll; `grid_records_with_summary`), and NDF currencies are
    dated on fixing dates and valued at the 1M NDF price: `rates` is then
    `engine.ladder.ndf.apply_ndf_1m_rates(conn, rates_from_marks(conn))`, in which an NDF
    currency with no 1M price has no entry at all, so it is blank here with the engine's
    reason (`rate_reasons_caption`), never at spot."""
    from engine.ladder.exposure import build_exposure
    from engine.pnl.stress import load_scenarios, futures_pct_by_scenario
    rates = rates or {}
    view = view or DEFAULT_VIEW
    futures = futures or DEFAULT_FUTURES
    fallback_ccys = fallback_ccys or set()
    forward_proxy_ccys = forward_proxy_ccys or set()
    all_fallback = fallback_ccys | forward_proxy_ccys
    grid = grid_records(records, view)
    result = build_exposure(grid, rates)
    exposure_result = build_exposure(exposure_records if exposure_records is not None else records, rates)
    empty = result.ladder.empty
    scenarios = load_scenarios()
    if empty:
        main = html.P("No open FX trades for this as-of date.", className="section-kicker")
        reasons = heat = details = None
    else:
        # One table (user, 2026-09-21, second note): the rate / delta block is folded into
        # the grid as its first columns instead of standing above it as a table of its own.
        summary, ccys = summary_block_frame(result, grid, sort, all_fallback, rates=rates, view=view)
        reasons = rate_reasons_caption(result, ccys)
        frame, _ccys = combined_frame(result, grid, sort, forward_rates=forward_rates, view=view)
        main = _grid_datatable(frame, view, summary=summary)
        heat = ladder_heatmap(result, ccys, forward_rates, view)
        details = local_vs_usd_details(grid)
    return html.Div(className="section", children=[
        headline_numbers(exposure_result, futures, fallback_ccys, forward_proxy_ccys),
        html.H4("Cash ladder: settled cash, spot, forwards, swaps and option deltas"),
        reasons,
        usd_basis_caption(forward_rates, view),
        main,
        ndf_fixing_caption(result, unresolved),
        settled_unknown_caption(unresolved),
        heat,
        details,
        html.H4("Open futures"),
        futures_table(futures, futures_details),
        combined_risk_table(exposure_result, futures, scenarios, futures_pct_by_scenario(scenarios), all_fallback),
        pair_position_table(pair_positions),
    ])
