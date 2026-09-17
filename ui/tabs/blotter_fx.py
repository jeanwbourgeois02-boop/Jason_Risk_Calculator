"""Blotter "FX" sub-tab: a literal replica of the old xlsx workbook's "All FX trades"
sheet, per user authorisation 2026-09-17 -- this supersedes the earlier
`priced_value_book`/`value_book`-shaped FX table entirely (see `ui.tabs.blotter`'s
module docstring for the general sub-tab pattern and history).

Rows come from `engine.pnl.xlsx_fx_replica.fx_replica`, one row per FX_SPOT/FX_FWD/
FX_SWAP/FUTURE trade, deliberately reproducing the old sheet's known quirks (futures
P&L divided by mark instead of fill, the LTD-2 column's t-1-mark divisor bug, one
shared per-pair valuation date rather than each trade's own settle_date) -- these are
intentional display fidelity to the historical sheet, not bugs to fix or flag here; see
that module's own docstring for the CLAUDE.md "must not replicate" cross-reference.

Unlike Rates (`ui.tabs.rates`), this sub-tab DOES carry a P&L strip (added 2026-09-17,
user decision) -- but it deliberately does NOT reuse `fx_replica`'s own numbers for it.
The strip is built from `ui.tabs.blotter_pricing.priced_value_book`/`row_scoped_headline`
-- the SAME official per-trade valuation machinery every other populated sub-tab's strip
uses (`ui.tabs.blotter.render_headline_strip`) -- scoped to the FX+FUTURE trade universe
(`_scoped_trade_ids`, matching `fx_replica`'s own row scope: FX_SPOT/FX_FWD/FX_SWAP/
FUTURE). So the strip and the table below it show two DIFFERENT, intentionally
different, LTD figures for the same trades: the strip is the correct headline P&L per
CLAUDE.md's P&L conventions, the table is the historical-fidelity xlsx replica (which
still carries the "must not replicate" quirks by design). `STRIP_CAPTION` says this in
the UI so it doesn't read as a contradiction. There is still no filter bar or row-expand
detail panel here (module docstring below on `fx_replica_table`'s shape not matching
`priced_value_book`'s) -- the strip is built once per render (like the table), not
re-scoped by any interactive row selection.

Any of `fx_replica`'s mark/pnl columns can be `None` when a mark is genuinely missing.
As of 2026-09-17 (user decision), a cell that is missing for that reason is filled with
an illustrative SAMPLE value here -- in this UI module only, computed fresh from the
already-fetched `fx_replica` frame, never written back to the database or to
`engine.pnl.xlsx_fx_replica`/`engine.pnl.pnl` -- so the table's shape can be previewed
before Bloomberg marks exist on this PC. Sample cells are visually distinct (muted
italic, matching this app's existing "Unavailable: grey italic" convention in
`ui/assets/style.css`) and carry a literal " (sample)" suffix so nobody can mistake one
for a real Bloomberg-sourced number; a caption above the table explains this whenever at
least one sample value was used. A row with a real mark/P&L always keeps that real value
untouched -- sampling only ever fills a genuinely missing (`None`/NaN) cell. See
`_fill_sample_values` for the generation rule.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd
from dash import dash_table, html

from engine.pnl.pnl import workbook_fx_pnl
from engine.pnl.xlsx_fx_replica import fx_replica
from ui.tabs.blotter_pricing import priced_value_book, row_scoped_headline
from ui.tabs.formatting import format_cell

DATATABLE_ID = "blotter-fx-replica-datatable"

_DISPLAY_COLUMNS = [
    "trade_date", "instrument_id", "quantity_usd_notional", "tenor", "fill",
    "mark_t1", "mark_eod", "mark_t2", "pnl_t1", "pnl_eod", "pnl_t2",
]
_COLUMN_LABELS = {
    "trade_date": "Date", "instrument_id": "Instrument", "quantity_usd_notional": "Quantity",
    "tenor": "Value date", "fill": "Fill",
    "mark_t1": "T-1 mark", "mark_eod": "EOD mark", "mark_t2": "T-2 mark",
    "pnl_t1": "LTD-1 P&L", "pnl_eod": "LTD P&L", "pnl_t2": "LTD-2 P&L",
}
_RATE_COLS = {"fill", "mark_t1", "mark_eod", "mark_t2"}
_USD_COLS = {"quantity_usd_notional", "pnl_t1", "pnl_eod", "pnl_t2"}

# ------------------------------------------------------------------ P&L strip (2026-09-17)
# See module docstring: the strip's trade universe mirrors fx_replica's own row scope
# (FX_SPOT/FX_FWD/FX_SWAP/FUTURE) but its P&L comes from the official valuation pipeline,
# not the replica formula below.
FX_AND_FUTURE_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FUTURE")

STRIP_CAPTION = (
    "Strip uses the app's official per-trade valuation; table below replicates the "
    "legacy workbook's own calculation for comparison."
)


def _scoped_trade_ids(df_priced: pd.DataFrame) -> list:
    """`trade_id`s in a `priced_value_book` frame that belong to the FX+FUTURE universe
    this sub-tab covers. `value_book` (and so `priced_value_book`) only ever builds FX/
    FUTURE rows in the first place, so this filter is a no-op today, but it is explicit
    rather than assumed in case that scope ever widens (e.g. once IRS/FX_OPTION are
    added to `value_book`)."""
    if df_priced.empty:
        return []
    return df_priced[df_priced["product"].isin(FX_AND_FUTURE_PRODUCTS)]["trade_id"].tolist()


def _fx_strip(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The same headline-strip component every other populated sub-tab uses
    (`ui.tabs.blotter.render_headline_strip`), row-scoped to `_scoped_trade_ids`.
    Lazy-imports `ui.tabs.blotter` (not a module-level import) because `blotter.py`
    itself imports this module at load time -- a module-level import here would be
    circular."""
    from ui.tabs.blotter import fallback_caption, render_headline_strip

    df_priced, _n_fallback, _n_total = priced_value_book(conn, as_of)
    trade_ids = _scoped_trade_ids(df_priced)
    headline = row_scoped_headline(conn, as_of, trade_ids)
    scoped = df_priced[df_priced["trade_id"].isin(trade_ids)] if not df_priced.empty else df_priced
    n_fallback = int(scoped["priced_from_bnp"].sum()) if not scoped.empty else 0
    bnp_caption = fallback_caption(n_fallback, len(scoped))
    caption = STRIP_CAPTION if not bnp_caption else f"{STRIP_CAPTION} {bnp_caption}"
    return render_headline_strip(headline, caption)


# ------------------------------------------------------------------ sample/placeholder marks
# 2026-09-17 user decision: fill genuinely-missing mark/P&L cells with a clearly-flagged
# illustrative value so the table's shape can be previewed with no Bloomberg marks on
# file. UI-display fallback only -- never touches the database or engine/pnl/.

SAMPLE_SUFFIX = " (sample)"
SAMPLE_CAPTION = (
    "Italicised values marked \"(sample)\" are illustrative placeholders for "
    "marks/P&L with no Bloomberg data yet -- computed fresh for display only, never "
    "saved, and replaced automatically the moment real marks are loaded."
)

# Deterministic, distinct-per-column offsets off the trade's own fill -- not random, so
# re-rendering the same (unpriced) row always shows the same sample figure, and the
# three mark columns are visually distinguishable from one another. Applied as
# fill * (1 + offset) for every instrument (FX pair or futures contract) alike: a plain
# fill * offset (no "+1") would produce a value with no relation to the real price's
# magnitude, which defeats the point of previewing a representative table -- the literal
# " (sample)" suffix and muted-italic styling are what prevent this from being mistaken
# for a real quote, not the number's shape.
_MARK_SAMPLE_OFFSETS = {"mark_t1": -0.0125, "mark_eod": 0.0175, "mark_t2": -0.0225}
_SAMPLE_MARK_COLS = ("mark_t1", "mark_eod", "mark_t2")
_SAMPLE_PNL_COLS = ("pnl_t1", "pnl_eod", "pnl_t2")


def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def _sample_mark(fill, offset: float) -> Optional[float]:
    if _is_missing(fill):
        return None
    return float(fill) * (1.0 + offset)


def _fill_sample_values(df: pd.DataFrame):
    """Return `(df_with_samples, sample_mask)`. `sample_mask[col]` is a list, one entry
    per row of `df` (same positional order), `True` where that cell was a genuinely
    missing mark/P&L and got an illustrative sample value instead. A cell that already
    has a real value is never touched, so `sample_mask` also tells the caller exactly
    which cells to flag visually. Sample P&L reuses `engine.pnl.pnl.workbook_fx_pnl` --
    the SAME formula (and the same must-not-replicate quirks: futures/`fill`-divisor
    branch, the LTD-2 column's t-1-mark divisor) `engine.pnl.xlsx_fx_replica` uses for
    the real numbers -- so the sample P&L's relationship to its sample mark is
    representative, not an unrelated random figure. Real marks are preferred over
    sample ones wherever a P&L formula needs more than one mark (e.g. LTD-2 uses a real
    T-1 mark, if present, as its denominator instead of a sample one)."""
    out = df.copy().reset_index(drop=True)
    mask = {col: [False] * len(out) for col in (*_SAMPLE_MARK_COLS, *_SAMPLE_PNL_COLS)}
    if out.empty:
        return out, mask

    for i in range(len(out)):
        row = out.iloc[i]
        effective = {}
        for col in _SAMPLE_MARK_COLS:
            value = row[col]
            if _is_missing(value):
                sample = _sample_mark(row["fill"], _MARK_SAMPLE_OFFSETS[col])
                if sample is not None:
                    out.at[i, col] = sample
                    mask[col][i] = True
                effective[col] = sample
            else:
                effective[col] = value

        if _is_missing(row["pnl_eod"]) and mask["mark_eod"][i]:
            out.at[i, "pnl_eod"] = workbook_fx_pnl(
                row["instrument_id"], row["quantity_usd_notional"], row["fill"], effective["mark_eod"])
            mask["pnl_eod"][i] = True

        if _is_missing(row["pnl_t1"]) and mask["mark_t1"][i]:
            out.at[i, "pnl_t1"] = workbook_fx_pnl(
                row["instrument_id"], row["quantity_usd_notional"], row["fill"], effective["mark_t1"])
            mask["pnl_t1"][i] = True

        needs_t2_sample = mask["mark_t2"][i] or mask["mark_t1"][i]
        if (_is_missing(row["pnl_t2"]) and needs_t2_sample
                and effective["mark_t2"] is not None and effective["mark_t1"] is not None):
            out.at[i, "pnl_t2"] = workbook_fx_pnl(
                row["instrument_id"], row["quantity_usd_notional"], row["fill"],
                effective["mark_t2"], effective["mark_t1"])
            mask["pnl_t2"][i] = True

    return out, mask


def _sample_style_conditional(sample_mask: dict) -> list:
    """One `style_data_conditional` rule per column that has any sample cell, keyed on
    the `SAMPLE_SUFFIX` the formatted cell carries -- muted italic, matching
    `.card-value--muted`/`.cell--unavailable` in `ui/assets/style.css`. Per-column
    rather than per-cell (`row_index`) on purpose: a full book is ~750 rows x 6 columns,
    and one rule per cell made the DataTable carry thousands of rules."""
    return [
        {
            "if": {"column_id": col, "filter_query": f'{{{col}}} contains "{SAMPLE_SUFFIX.strip()}"'},
            "color": "var(--muted)", "fontStyle": "italic",
        }
        for col, flags in sample_mask.items() if any(flags)
    ]


def _fmt_rate(value) -> str:
    if _is_missing(value):
        return ""
    return f"{float(value):,.6f}"


def format_rows(df: pd.DataFrame, sample_mask: Optional[dict] = None) -> list:
    """Formatted `data` records for `fx_replica_table`, split out so it can be
    unit-tested without Dash, matching `ui.tabs.rates.format_rows`'s convention.
    Missing marks/P&L render blank, never "0.00" or "n/a", UNLESS `sample_mask` flags
    that cell as sample-filled (module docstring), in which case the formatted value
    gets `SAMPLE_SUFFIX` appended so it reads e.g. "1.108750 (sample)"."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    for col in cols:
        if col in _RATE_COLS:
            formatted[col] = formatted[col].map(_fmt_rate)
        elif col in _USD_COLS:
            formatted[col] = formatted[col].map(format_cell)
    if sample_mask and not formatted.empty:
        formatted = formatted.reset_index(drop=True)
        for col, flags in sample_mask.items():
            if col not in formatted.columns:
                continue
            for i, is_sample in enumerate(flags):
                if is_sample and i < len(formatted) and formatted.at[i, col]:
                    formatted.at[i, col] = formatted.at[i, col] + SAMPLE_SUFFIX
    return formatted.to_dict("records")


def fx_replica_table(df: pd.DataFrame, table_id: str = DATATABLE_ID,
                      sample_mask: Optional[dict] = None) -> dash_table.DataTable:
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns] if not df.empty else list(_DISPLAY_COLUMNS)
    data_records = format_rows(df, sample_mask)
    style_data_conditional = _sample_style_conditional(sample_mask) if sample_mask else []
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": _COLUMN_LABELS.get(c, c.replace("_", " ").title()), "id": c}
                 for c in cols],
        data=data_records,
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_header={"fontWeight": "bold"},
        style_data_conditional=style_data_conditional,
        page_size=25,
        page_action="native",
    )


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole FX sub-tab body: a P&L strip (`_fx_strip`), `fx_replica` rows with
    missing marks/P&L sample-filled (`_fill_sample_values`), plus a short note when
    there are no trades (table still renders, empty, with the full column set -- "rows
    must always render" rule elsewhere in this app)."""
    df = fx_replica(conn, as_of)
    children = [_fx_strip(conn, as_of)]
    sample_mask = None
    if df.empty:
        children.append(html.P("No FX/futures trades on file for this as-of date.",
                                className="section-kicker", style={"fontStyle": "italic"}))
    else:
        df, sample_mask = _fill_sample_values(df)
        if any(any(flags) for flags in sample_mask.values()):
            children.append(html.P(SAMPLE_CAPTION, className="section-kicker",
                                    style={"fontStyle": "italic"}))
        else:
            sample_mask = None
    children.append(fx_replica_table(df, sample_mask=sample_mask))
    return html.Div(children)
