"""Blotter "FX" sub-tab: the legacy xlsx workbook's "All FX trades" sheet column layout
(trade / tenor / fill / t-1-EOD-t-2 mark and P&L columns), priced under CLAUDE.md's
market-standard "P&L conventions" -- not the workbook arithmetic.

History (2026-09-17, one day): this sub-tab was first built as a literal replica of the
old sheet (`engine.pnl.xlsx_fx_replica`, since deleted), deliberately reproducing its
"must not replicate" bugs; the user withdrew that authorisation the same day and asked
for the sheet's column *shape* only, priced correctly. Rows now come from
`engine.pnl.fx_blotter.fx_blotter_rows`, which prices the FX_SPOT/FX_FWD/FX_SWAP/FUTURE
trade book three times (as_of, t-1bd, t-2bd) via `engine.pnl.valuation.value_book` --
each FX leg at its own settle_date's FWD_OUTRIGHT, quote P&L converted at spot, futures
contracts x multiplier x (mark - fill), settled trades frozen -- and lays the results out
as one row per trade. `fx_blotter_rows` is called with `value_fn=priced_value_book` (via
a thin wrapper dropping its `(n_fallback, n_total)` tuple, kept for other callers'
back-compat only -- see that module's docstring), the SAME pricing path `_fx_strip`
below uses for the strip, so the table and the strip agree exactly on any priced trade.
A missing cell (no official mark, no spot, an unrealised settled trade) stays
`None`/blank here too: never zeroed, never defaulted, and never retried against a
second source (BNP_BVAL fallback pricing removed 2026-09-17, user decision "no bnp
fall back" -- see `ui.tabs.blotter_pricing`'s module docstring).

This sub-tab carries a P&L strip (`_fx_strip`, added 2026-09-17), built statically inside
`build_layout` on every render (like the table) rather than through the generic
`_register_strip_callback` loop in `ui.tabs.blotter` -- there is no filter bar or
row-expand detail panel here, since this module's row shape
(trade_id/instrument_id/quantity_usd_notional/tenor/fill/mark_*/pnl_*) does not match
that scaffolding's assumed `value_book` shape (trade_id/mark/pnl_usd/...).

Any mark/P&L cell can be `None` when a mark is genuinely missing (e.g. no Bloomberg data
loaded on this PC yet). As of 2026-09-17 (user decision), a cell that is missing for that
reason is filled with an illustrative SAMPLE value here -- in this UI module only,
computed fresh from the already-fetched frame, never written back to the database or to
`engine.pnl.fx_blotter`/`engine.pnl.valuation` -- so the table's shape can be previewed
before Bloomberg marks exist. Sample cells are visually distinct (muted italic, matching
this app's existing "Unavailable: grey italic" convention in `ui/assets/style.css`) and
carry a literal " (sample)" suffix so nobody can mistake one for a real Bloomberg-sourced
number; a caption above the table explains this whenever at least one sample value was
used. A row with a real mark/P&L always keeps that real value untouched -- sampling only
ever fills a genuinely missing (`None`/NaN) cell. See `_fill_sample_values` for the
generation rule; its sample P&L is a plain display-only approximation of the standard
convention (mark used in place of spot, since no spot is fetched for this preview), not a
call into any engine module.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd
from dash import dash_table, html

from engine.pnl.fx_blotter import fx_blotter_rows
from ui.tabs.blotter_pricing import priced_value_book, row_scoped_headline
from ui.tabs.formatting import format_cell

DATATABLE_ID = "blotter-fx-datatable"

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


def _priced_value_fn(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """`fx_blotter_rows`'s `value_fn`: `priced_value_book` without its `(n_fallback,
    n_total)` tuple -- the same pricing path `_fx_strip` uses for the strip below, so
    the two always agree."""
    return priced_value_book(conn, as_of)[0]


# ------------------------------------------------------------------ P&L strip (2026-09-17)
# The strip's trade universe is the FX sub-tab's own row scope: the three FX products
# only. Futures have their own sub-tab; counting them here too made this strip's LTD
# disagree with the Total book's "FX" row (2026-09-17 audit).
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")


def _scoped_trade_ids(df_priced: pd.DataFrame) -> list:
    """`trade_id`s in a `priced_value_book` frame that belong to the FX+FUTURE universe
    this sub-tab covers. `value_book` (and so `priced_value_book`) only ever builds FX/
    FUTURE rows in the first place, so this filter is a no-op today, but it is explicit
    rather than assumed in case that scope ever widens (e.g. once IRS/FX_OPTION are
    added to `value_book`)."""
    if df_priced.empty:
        return []
    return df_priced[df_priced["product"].isin(FX_PRODUCTS)]["trade_id"].tolist()


def _fx_strip(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The same headline-strip component every other populated sub-tab uses
    (`ui.tabs.blotter.render_headline_strip`), row-scoped to `_scoped_trade_ids`, priced
    by the exact same `priced_value_book` pipeline the table below uses -- so the strip's
    LTD and the table's summed `pnl_eod` column agree for any set of priced trades.
    Lazy-imports `ui.tabs.blotter` (not a module-level import) because `blotter.py`
    itself imports this module at load time -- a module-level import here would be
    circular."""
    from ui.tabs.blotter import render_headline_strip

    df_priced, _n_fallback, _n_total = priced_value_book(conn, as_of)
    trade_ids = _scoped_trade_ids(df_priced)
    headline = row_scoped_headline(conn, as_of, trade_ids)
    return render_headline_strip(headline)


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
_PNL_TO_MARK_COL = {"pnl_t1": "mark_t1", "pnl_eod": "mark_eod", "pnl_t2": "mark_t2"}


def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def _sample_mark(fill, offset: float) -> Optional[float]:
    if _is_missing(fill):
        return None
    return float(fill) * (1.0 + offset)


def _sample_pnl_divisor(instrument_id: str) -> Optional[str]:
    """Which of `fill` (`'f'`) or `mark` (`'m'`) the illustrative sample P&L divides by,
    or `None` when no sensible display-only approximation exists for this instrument.
    This frame carries no `product`/`base_ccy`/`quote_ccy` column (see `OUTPUT_COLUMNS`),
    so the split is a heuristic on `instrument_id`'s shape alone, display-only:

    - starts with "USD" (a USDXXX pair): local P&L is `Q_usd x (m - f)` in quote ccy;
      approximating the spot conversion by the mark itself gives `Q x (m - f) / m`.
    - a 6-letter pair ending in "USD" (an XXXUSD pair): `quantity_usd_notional` here is
      the USD amount, and `Q / f` approximates the base amount, so `Q x (m - f) / f`.
    - anything else that isn't a plausible 6-letter currency pair (a futures contract,
      e.g. "ESU6 Index"): `quantity_usd_notional` there is `contracts * multiplier *
      fill`, so `Q / f` recovers `contracts * multiplier` and `Q x (m - f) / f` is exactly
      `contracts * multiplier * (m - f)` -- the real futures P&L formula, not merely an
      approximation.
    - a 6-letter pair with neither side USD (a cross, e.g. EURSEK): no USD leg exists to
      approximate a conversion from, so no sample is generated -- returns `None`.
    """
    if instrument_id.startswith("USD"):
        return "m"
    looks_like_fx_pair = len(instrument_id) == 6 and instrument_id.isalpha() and instrument_id.isupper()
    if looks_like_fx_pair and not instrument_id.endswith("USD"):
        return None  # cross: neither side USD, no conversion to approximate
    return "f"


def _sample_pnl(instrument_id: str, notional_usd, fill, mark) -> Optional[float]:
    """Display-only illustrative approximation of the standard P&L convention -- the mark
    stands in for spot (no spot is fetched for this preview), so this is never the real
    number and is always shown with `SAMPLE_SUFFIX`. Real rows always come from
    `engine.pnl.fx_blotter.fx_blotter_rows` -> `value_book`, never from here."""
    if _is_missing(notional_usd) or _is_missing(fill) or _is_missing(mark):
        return None
    divisor = _sample_pnl_divisor(instrument_id)
    if divisor is None:
        return None
    denom = float(mark) if divisor == "m" else float(fill)
    if denom == 0:
        return None
    return float(notional_usd) * (float(mark) - float(fill)) / denom


def _fill_sample_values(df: pd.DataFrame):
    """Return `(df_with_samples, sample_mask)`. `sample_mask[col]` is a list, one entry
    per row of `df` (same positional order), `True` where that cell was a genuinely
    missing mark/P&L and got an illustrative sample value instead. A cell that already
    has a real value is never touched, so `sample_mask` also tells the caller exactly
    which cells to flag visually. Sample P&L (`_sample_pnl`) is a plain display-only
    approximation of the market-standard convention -- independent per column (`pnl_t2`
    uses `mark_t2` directly, no dependency on `mark_t1`), unlike the retired xlsx-replica
    sampling this replaces."""
    out = df.copy().reset_index(drop=True)
    mask = {col: [False] * len(out) for col in (*_SAMPLE_MARK_COLS, *_SAMPLE_PNL_COLS)}
    if out.empty:
        return out, mask

    for i in range(len(out)):
        row = out.iloc[i]
        instrument_id = row["instrument_id"]
        notional = row["quantity_usd_notional"]
        fill = row["fill"]
        effective = {}
        for col in _SAMPLE_MARK_COLS:
            value = row[col]
            if _is_missing(value):
                sample = _sample_mark(fill, _MARK_SAMPLE_OFFSETS[col])
                if sample is not None:
                    out.at[i, col] = sample
                    mask[col][i] = True
                effective[col] = sample
            else:
                effective[col] = value

        for pnl_col, mark_col in _PNL_TO_MARK_COL.items():
            if _is_missing(row[pnl_col]) and mask[mark_col][i]:
                sample_pnl = _sample_pnl(instrument_id, notional, fill, effective[mark_col])
                if sample_pnl is not None:
                    out.at[i, pnl_col] = sample_pnl
                    mask[pnl_col][i] = True

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
    """Formatted `data` records for `fx_blotter_table`, split out so it can be
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


def fx_blotter_table(df: pd.DataFrame, table_id: str = DATATABLE_ID,
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
    """The whole FX sub-tab body: a P&L strip (`_fx_strip`), `fx_blotter_rows` rows with
    missing marks/P&L sample-filled (`_fill_sample_values`), plus a short note when
    there are no trades (table still renders, empty, with the full column set -- "rows
    must always render" rule elsewhere in this app)."""
    df = fx_blotter_rows(conn, as_of, value_fn=_priced_value_fn, products=FX_PRODUCTS)
    children = [_fx_strip(conn, as_of)]
    sample_mask = None
    if df.empty:
        children.append(html.P("No FX trades on file for this as-of date.",
                                className="section-kicker", style={"fontStyle": "italic"}))
    else:
        df, sample_mask = _fill_sample_values(df)
        if any(any(flags) for flags in sample_mask.values()):
            children.append(html.P(SAMPLE_CAPTION, className="section-kicker",
                                    style={"fontStyle": "italic"}))
        else:
            sample_mask = None
    children.append(fx_blotter_table(df, sample_mask=sample_mask))
    return html.Div(children)
