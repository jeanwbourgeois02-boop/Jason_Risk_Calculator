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

**P&L by currency (2026-09-21, user: "pnl breakdown by currency - as fixed tables - also
with a dynamic p&l sum per currency - all the tables above the general table").** Two
compact tables sit between the strip and the trade table, side by side on a wide window:

- "P&L by currency" (`fixed_currency_panel`): the whole FX sub-tab book, whatever the trade
  table is filtered to. One row per currency (`currency_label`: the pair's non-USD
  currency; a cross with no USD side under its own pair name, never split) and a Total.
  Every figure is `row_scoped_headline` over that currency's trade ids -- the strip's own
  function, so the Total row IS the strip, and whatever that function decides about a
  period's reference close applies to every row alike. Nothing is summed here.
- "P&L by currency, rows shown" (`shown_currency_panel`): the same currencies over the rows
  the trade table shows after its native column filters (all pages). The only callback of
  this module (`register_callbacks`) redraws it from the table's `derived_virtual_data`.
  The sums are made from the real numbers each row carries in hidden columns
  (`_HIDDEN_COLUMNS`), never from the formatted cells: a "(sample)" cell or a blank carries
  `None` there, counts as unpriced and is named in the "excludes N" note. Grouping and
  summing only, under the header's display rule (`shown_currency_rows`).

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
import threading
from typing import Callable, Dict, List, Optional

import pandas as pd
from dash import Input, Output, dash_table, html

from engine.pnl.fx_blotter import fx_blotter_rows
from ui.tabs.blotter_pricing import _render_cache_key, priced_value_book, row_scoped_headline
from ui.tabs import ranking as rk
from ui.tabs.formatting import format_cell

DATATABLE_ID = "blotter-fx-datatable"
# The two per-currency tables above the trade table (module docstring). The second id is
# the container the module's one callback redraws.
CURRENCY_TABLES_ID = "blotter-fx-by-currency"
SHOWN_CURRENCY_ID = "blotter-fx-by-currency-shown"

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

# Carried in every row, never shown (`hidden_columns`, so they survive the native filter
# and come back in `derived_virtual_data`): what "P&L by currency, rows shown" sums. The
# visible P&L cells are formatted strings and may be illustrative "(sample)" values; these
# hold the REAL number or `None` -- a sample or a blank is `None`, so it can never enter a
# sum. `on_book_t1` / `on_book_t2`: 1 when the trade had been dealt by that earlier close
# (`trade_date <= that date`, `value_book`'s own rule), which tells a trade with no value
# there (left out of a difference) from one dealt since (counts in full, as in the strip).
_NUM_COLUMN_OF = {"pnl_eod": "pnl_eod_num", "pnl_t1": "pnl_t1_num", "pnl_t2": "pnl_t2_num"}
_HIDDEN_TEXT_COLUMNS = ["trade_id", "currency"]
_HIDDEN_NUMERIC_COLUMNS = [*_NUM_COLUMN_OF.values(), "on_book_t1", "on_book_t2"]
_HIDDEN_COLUMNS = [*_HIDDEN_TEXT_COLUMNS, *_HIDDEN_NUMERIC_COLUMNS]


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


# ------------------------------------------------------------------ P&L by currency (2026-09-21)
# Two tables above the trade table (module docstring). A row of either is
# `{"currency": str, "trades": int, "figures": {key: entry}}`, an entry being the strip's
# own `{value, available, reason, excluded_summary, excluded_detail, ...}` -- whatever else
# `row_scoped_headline` puts in one is carried along and ignored.
TOTAL_LABEL = "Total"
FIXED_TABLE_ID = "blotter-fx-ccy-fixed"     # "P&L by currency" (the whole FX book)
SHOWN_TABLE_ID = "blotter-fx-ccy-shown"     # "P&L by currency, rows shown"
# (key in `figures`, column heading). The fixed table reads `row_scoped_headline`'s keys
# ("ltd1_daily" is its Previous day); the rows-shown table builds the same keys itself.
FIXED_PERIODS = (("ltd", "LTD"), ("daily", "Daily"), ("ltd1_daily", "Previous day"),
                 ("d5", "5d"), ("mtd", "MTD"), ("ytd", "YTD"))
SHOWN_PERIODS = (("ltd", "LTD"), ("ltd1", "LTD-1"), ("ltd2", "LTD-2"),
                 ("daily", "Daily"), ("ltd1_daily", "Previous day"))
FIXED_TITLE = "P&L by currency"
SHOWN_TITLE = "P&L by currency, rows shown"
FIXED_CAPTION = "Whole FX book, whatever the table below is filtered to. The Total row is the strip above."
SHOWN_CAPTION = ("Rows shown in the table below (every page), after its column filters. "
                 "Sample values are never counted.")


def currency_label(instrument_id: str, base_ccy: str = "", quote_ccy: str = "") -> str:
    """The row a pair's P&L is listed under: its non-USD currency (USDJPY -> JPY, AUDUSD ->
    AUD, XAUUSD -> XAU). A cross with no USD side stays under its own pair name (EURSEK):
    its P&L is one number and is never split between two currencies. `base_ccy` /
    `quote_ccy` are the instrument's own (`instruments`); without them a six-letter id is
    read as the pair it names, and anything else is listed as it is."""
    base, quote = (base_ccy or "").upper(), (quote_ccy or "").upper()
    if not (base and quote) and len(instrument_id) == 6 and instrument_id.isalpha():
        base, quote = instrument_id[:3].upper(), instrument_id[3:].upper()
    if base == "USD" and quote and quote != "USD":
        return quote
    if quote == "USD" and base and base != "USD":
        return base
    return instrument_id


def currency_labels(conn: sqlite3.Connection, instrument_ids) -> Dict[str, str]:
    """instrument_id -> `currency_label`, from the currencies `instruments` holds."""
    on_file = {row[0]: (row[1], row[2]) for row in
               conn.execute("SELECT instrument_id, base_ccy, quote_ccy FROM instruments")}
    return {i: currency_label(str(i), *on_file.get(i, ("", ""))) for i in instrument_ids}


def _sorted_currency_rows(rows: List[dict]) -> List[dict]:
    """|LTD| descending, a currency whose LTD is unavailable last, then by name."""
    def key(row):
        ltd = row["figures"].get("ltd", {})
        value = ltd.get("value")
        if ltd.get("available") and value is not None and value == value:
            return (0, -abs(float(value)), row["currency"])
        return (1, 0.0, row["currency"])
    return sorted(rows, key=key)


# The per-currency figures of the last few (database state, as-of) renders. Each currency
# is one `row_scoped_headline` call, about 20 ms on a full book and 0.4 s for twenty of
# them -- more than the rest of this sub-tab -- while coming back to the sub-tab with
# nothing changed is the common case. Keyed like `priced_value_book` (file path + mtime,
# pinned for the render); an in-memory database is never cached. A hit is used only when
# its `signature` still holds: the same trades per currency AND the same whole-book Total,
# which is worked out afresh on every render because it has to equal the strip. So a change
# in anything the figures depend on besides the file shows up in the Total and drops the hit.
_BY_CURRENCY_CACHE: Dict[tuple, dict] = {}
_BY_CURRENCY_CACHE_SIZE = 8
_BY_CURRENCY_LOCK = threading.Lock()


def _figures_signature(ids_by_currency: Dict[str, list], total_figures: dict) -> tuple:
    def entry(key):
        e = total_figures.get(key, {})
        value = e.get("value")
        return (key, bool(e.get("available")), None if value is None or value != value else round(float(value), 2),
                e.get("ref_date"), e.get("ref_date_used"), e.get("excluded_summary"))
    return (tuple(sorted((c, len(ids)) for c, ids in ids_by_currency.items())),
            tuple(entry(key) for key, _label in FIXED_PERIODS))


def by_currency_rows(conn: sqlite3.Connection, as_of: str):
    """`(rows, total)` for the fixed table: one row per currency of the FX sub-tab book
    (`_scoped_trade_ids`' scope), sorted by `_sorted_currency_rows`, and the Total row.
    Every figure is `row_scoped_headline` over that row's trade ids, the Total's over all
    of them -- the very call the strip makes, so the two cannot disagree."""
    df_priced, _n_fallback, _n_total = priced_value_book(conn, as_of)
    fx = df_priced[df_priced["product"].isin(FX_PRODUCTS)] if not df_priced.empty else df_priced
    ids_by_currency: Dict[str, list] = {}
    if not fx.empty:
        labels = currency_labels(conn, fx["instrument_id"].unique().tolist())
        for trade_id, instrument_id in zip(fx["trade_id"].tolist(), fx["instrument_id"].tolist()):
            ids_by_currency.setdefault(labels[instrument_id], []).append(trade_id)
    all_ids = _scoped_trade_ids(df_priced)
    total = {"currency": TOTAL_LABEL, "trades": len(all_ids),
             "figures": row_scoped_headline(conn, as_of, all_ids)}

    signature = _figures_signature(ids_by_currency, total["figures"])
    db_key = _render_cache_key(conn)
    cache_key = (db_key, as_of)
    if db_key is not None:
        with _BY_CURRENCY_LOCK:
            hit = _BY_CURRENCY_CACHE.get(cache_key)
        if hit is not None and hit["signature"] == signature:
            return hit["rows"], total

    rows = _sorted_currency_rows([
        {"currency": currency, "trades": len(ids), "figures": row_scoped_headline(conn, as_of, ids)}
        for currency, ids in ids_by_currency.items()])
    if db_key is not None:
        with _BY_CURRENCY_LOCK:
            _BY_CURRENCY_CACHE.pop(cache_key, None)
            _BY_CURRENCY_CACHE[cache_key] = {"signature": signature, "rows": rows}
            while len(_BY_CURRENCY_CACHE) > _BY_CURRENCY_CACHE_SIZE:
                _BY_CURRENCY_CACHE.pop(next(iter(_BY_CURRENCY_CACHE)))
    return rows, total


def _real_number(value) -> Optional[float]:
    """A number carried in a hidden column, else None. Never reads a display string: a
    formatted or "(sample)" cell is text and is refused here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def _entry(value: float, summary: str = "", detail: str = "") -> dict:
    return {"value": value, "available": True, "reason": "",
            "excluded_summary": summary, "excluded_detail": detail}


def _no_entry(reason: str) -> dict:
    return {"value": float("nan"), "available": False, "reason": reason,
            "excluded_summary": "", "excluded_detail": ""}


def _none_real(total: int, label: str) -> str:
    who = "the row shown has no" if total == 1 else f"none of the {total} rows shown has a"
    return f"{who} real {label} figure"


def _shown_level(values: List[Optional[float]], label: str) -> dict:
    """The sum of one P&L column over the rows on the book that day, priced rows only --
    `blotter_pricing._priced_single_from_df`'s rule on the rows shown. `values` holds one
    item per such row: its real number, or None when the row has none."""
    total = len(values)
    if total == 0:
        return _entry(0.0)
    priced = [v for v in values if v is not None]
    if not priced:
        return _no_entry(f"{_none_real(total, label)}: no official mark on file "
                         f"(an illustrative sample value is never counted)")
    n = total - len(priced)
    if not n:
        return _entry(sum(priced))
    return _entry(sum(priced), f"excludes {n} of {total} rows unpriced",
                  f"{n} with no real {label} figure (no official mark, or an illustrative sample value)")


def _shown_difference(rows: List[tuple], label_a: str, label_b: str) -> dict:
    """`label_a` minus `label_b` over the rows shown, by `blotter_pricing.
    _priced_diff_scoped`'s rule. `rows` holds `(a, b, on_book_b)` per row on the book at
    the later close: a row counts only when it is priced at BOTH ends; one dealt since the
    earlier close (`on_book_b` false) counts in full, a new trade and not a pricing gap;
    one priced at one end only is left out, never credited with a one-sided jump; and when
    those left out that way outnumber the rows priced at both ends there is no figure."""
    total = len(rows)
    if total == 0:
        return _entry(0.0)
    a_priced = [r for r in rows if r[0] is not None]
    both = [r for r in a_priced if r[1] is not None]
    blocked = [r for r in a_priced if r[1] is None and r[2]]
    new = [r for r in a_priced if r[1] is None and not r[2]]
    if blocked and len(blocked) > len(both):
        return _no_entry(f"needs the {label_b} figures: {len(blocked)} of {len(blocked) + len(both)} rows shown "
                         f"that were on the book then have no real value there")
    if not both and not new:
        return _no_entry(_none_real(total, label_a))
    value = sum(r[0] for r in both) + sum(r[0] for r in new) - sum(r[1] for r in both)
    n_unpriced = total - len(a_priced)
    n_excluded = n_unpriced + len(blocked)
    if not n_excluded:
        return _entry(value)
    parts = ([f"{n_unpriced} with no real {label_a} figure"] if n_unpriced else []) + \
            ([f"{len(blocked)} priced on {label_a} but with no real {label_b} figure"] if blocked else [])
    return _entry(value, f"excludes {n_excluded} of {total} rows unpriced", "; ".join(parts))


def _shown_figures(rows: List[dict]) -> dict:
    eod = [_real_number(r.get("pnl_eod_num")) for r in rows]
    t1 = [_real_number(r.get("pnl_t1_num")) for r in rows]
    t2 = [_real_number(r.get("pnl_t2_num")) for r in rows]
    # Unknown (a row without the flag) reads as "dealt by then": the choice that leaves a
    # row out of a difference rather than crediting it with a one-sided jump.
    on_t1 = [t1[i] is not None or r.get("on_book_t1", 1) not in (0, "0", False) for i, r in enumerate(rows)]
    on_t2 = [t2[i] is not None or r.get("on_book_t2", 1) not in (0, "0", False) for i, r in enumerate(rows)]
    n = range(len(rows))
    return {
        "ltd": _shown_level(eod, "LTD"),
        "ltd1": _shown_level([t1[i] for i in n if on_t1[i]], "LTD-1"),
        "ltd2": _shown_level([t2[i] for i in n if on_t2[i]], "LTD-2"),
        "daily": _shown_difference([(eod[i], t1[i], on_t1[i]) for i in n], "LTD", "LTD-1"),
        "ltd1_daily": _shown_difference([(t1[i], t2[i], on_t2[i]) for i in n if on_t1[i]], "LTD-1", "LTD-2"),
    }


def shown_currency_rows(rows: Optional[List[dict]]):
    """`(rows, total)` for "P&L by currency, rows shown", from the trade table's
    `derived_virtual_data` (or its `data`, for the first render): per currency and in
    total, the rows shown, LTD, LTD-1, LTD-2, Daily (LTD - LTD-1) and Previous day
    (LTD-1 - LTD-2). Sums of the rows' hidden real numbers only (`_HIDDEN_COLUMNS`), under
    the display rule the strip uses; no database read and no P&L worked out."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    by_currency: Dict[str, list] = {}
    for r in rows:
        currency = r.get("currency") or currency_label(str(r.get("instrument_id") or ""))
        by_currency.setdefault(currency or "?", []).append(r)
    out = _sorted_currency_rows([{"currency": currency, "trades": len(group), "figures": _shown_figures(group)}
                                 for currency, group in by_currency.items()])
    return out, {"currency": TOTAL_LABEL, "trades": len(rows), "figures": _shown_figures(rows)}


def _figure_notes(entry: dict) -> List[str]:
    """What a figure's tooltip says beyond its value: the "excludes N of M" note when it is
    a sum of the priced trades only, and the pricing path's own caption when a period is
    measured from an earlier close than its usual one (`engine.pnl.reference.annotate`,
    2026-09-21); nothing for a full sum."""
    notes = []
    summary = entry.get("excluded_summary") or ""
    if summary:
        detail = entry.get("excluded_detail") or ""
        notes.append(f"{summary}. {detail}" if detail else summary)
    ref_note = entry.get("ref_note") or ""
    if ref_note:
        notes.append(ref_note)
    return notes


def _currency_record(row: dict, periods: tuple) -> tuple:
    """`(record, tooltip)` for one currency, or the Total: the trade count and each period's
    number (the table formats it, ui.tabs.ranking), None (printed "n/a") with its reason as
    the tooltip when unavailable; a figure that is a sum of the priced trades only is
    flagged in `<key>__partial` (a dotted underline) with the note as its tooltip."""
    rec = {"currency": row["currency"], "trades": int(row["trades"])}
    tip = {}
    for key, _label in periods:
        entry = row["figures"].get(key, {})
        value = entry.get("value")
        if not entry.get("available") or value is None or value != value:
            rec[key], rec[f"{key}__partial"] = None, 0
            tip[key] = {"value": entry.get("reason") or "unavailable", "type": "text"}
            continue
        rec[key] = float(value)
        notes = _figure_notes(entry)
        rec[f"{key}__partial"] = 1 if notes else 0
        if notes:
            tip[key] = {"value": "\n".join(notes), "type": "text"}
    return rec, tip


def _currency_notes(rows: List[dict], total: dict, periods: tuple) -> Optional[html.P]:
    """The visible note under a table: how many trades the Total LTD leaves out, and what a
    dotted figure and an "n/a" are. None when every figure is a full sum."""
    entries = [row["figures"].get(key, {}) for row in [*rows, total] for key, _label in periods]
    parts = []
    ltd = total["figures"].get("ltd", {})
    if ltd.get("available") and ltd.get("excluded_summary"):
        parts.append(f"Total LTD {ltd['excluded_summary']}.")
    if any(e.get("available") and (e.get("excluded_summary") or e.get("ref_note")) for e in entries):
        parts.append("A dotted figure carries a note (trades left out unpriced, or an earlier close used): hover it.")
    if any(not e.get("available") for e in entries):
        parts.append("n/a: hover it for the reason.")
    return html.P(" ".join(parts), className="fx-ccy-note") if parts else None


def currency_table(rows: List[dict], total: dict, periods: tuple, trades_label: str = "Trades",
                   table_id: Optional[str] = None) -> list:
    """`[table, note]` (the note only when there is something to say): one row per currency,
    ranked on a header click (ui.tabs.ranking), and the Total pinned under them as the
    table's footer, so it stays put whatever the order."""
    body = [_currency_record(r, periods) for r in rows]
    total_rec, total_tip = _currency_record(total, periods)
    keys = [key for key, _label in periods]
    table = dash_table.DataTable(
        **({"id": table_id} if table_id else {}),
        columns=[rk.text("Currency", "currency"), rk.numeric(trades_label, "trades", rk.count())]
                + [rk.numeric(label, key, rk.amount(nully="n/a")) for key, label in periods],
        data=[r for r, _ in body], tooltip_data=[t for _, t in body],
        **rk.sortable(table_id),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "padding": "4px 8px"},
        style_cell_conditional=[{"if": {"column_id": "currency"}, "textAlign": "left", "fontWeight": "600"}],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(keys, bold=True, nil={"color": "var(--muted)", "fontStyle": "italic"})
                               + [{"if": {"column_id": key, "filter_query": f"{{{key}__partial}} = 1"},
                                   "textDecoration": "underline dotted"} for key in keys],
    )
    total_style = [{"if": {"filter_query": "{currency} = '" + TOTAL_LABEL + "'"}, "fontWeight": "700",
                    "borderTop": "2px solid var(--muted)"}]
    note = _currency_notes(rows, total, periods)
    ranked = rk.with_footer(table, [total_rec], footer_style=total_style, footer_tooltips=[total_tip])
    return [html.Div(ranked, className="fx-ccy-scroll"), *([note] if note is not None else [])]


def _currency_panel(title: str, caption: str, body: list, body_id: Optional[str] = None) -> html.Div:
    body_div = html.Div(body, id=body_id) if body_id else html.Div(body)
    return html.Div(className="section fx-ccy-panel", children=[
        html.H4(title, className="fx-ccy-title"),
        html.P(caption, className="section-kicker"),
        body_div,
    ])


def fixed_currency_panel(conn: sqlite3.Connection, as_of: str) -> html.Div:
    rows, total = by_currency_rows(conn, as_of)
    return _currency_panel(FIXED_TITLE, FIXED_CAPTION, currency_table(rows, total, FIXED_PERIODS, table_id=FIXED_TABLE_ID))


def shown_currency_children(rows: Optional[List[dict]]) -> list:
    """What `SHOWN_CURRENCY_ID` holds: built once with the table's own `data` when the
    sub-tab is rendered, then by the callback from `derived_virtual_data`."""
    shown, total = shown_currency_rows(rows)
    return currency_table(shown, total, SHOWN_PERIODS, trades_label="Trades shown", table_id=SHOWN_TABLE_ID)


def shown_currency_panel(rows: Optional[List[dict]]) -> html.Div:
    return _currency_panel(SHOWN_TITLE, SHOWN_CAPTION, shown_currency_children(rows), body_id=SHOWN_CURRENCY_ID)


def register_callbacks(app, get_db_path: Optional[Callable[[], object]] = None) -> None:
    """The sub-tab's one callback: "P&L by currency, rows shown" follows the trade table's
    native filter. It reads nothing but the rows the browser sends (`get_db_path` is taken
    only so every sub-tab module registers the same way). Both ids live inside the FX
    sub-tab's content, which `ui.tabs.blotter` renders by callback."""

    @app.callback(
        Output(SHOWN_CURRENCY_ID, "children"),
        Input(DATATABLE_ID, "derived_virtual_data"),
        prevent_initial_call=True,
    )
    def _update_shown(rows):
        from dash import no_update
        if rows is None:  # the table has not worked its rows out yet: keep the first render
            return no_update
        try:
            return shown_currency_children(rows)
        except Exception as exc:  # noqa: BLE001 -- an HTTP 500 would leave stale figures on screen, silently
            import logging
            logging.getLogger(__name__).exception("Blotter FX rows-shown currency table failed")
            return [html.P(f"{SHOWN_TITLE} could not be worked out ({exc}).", className="section-kicker",
                           style={"color": "var(--neg)"})]


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
    """`data` records for `fx_blotter_table`, split out so it can be unit-tested without
    Dash, matching `ui.tabs.rates.format_rows`'s convention: rates and USD amounts as
    numbers, which the table formats (ui.tabs.ranking). A missing mark or P&L is None
    (blank), never 0 or "n/a", UNLESS `sample_mask` flags that cell as sample-filled
    (module docstring), in which case it carries the formatted text with `SAMPLE_SUFFIX`
    ("1.108750 (sample)"), shown as it is and ranked last."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy().astype(object) if not df.empty else pd.DataFrame(columns=cols)
    if not formatted.empty:
        for col in cols:
            if col in _RATE_COLS or col in _USD_COLS:
                formatted[col] = pd.Series([rk.value(v) for v in df[col].tolist()], dtype=object, index=formatted.index)
    if sample_mask and not formatted.empty:
        formatted = formatted.reset_index(drop=True)
        for col, flags in sample_mask.items():
            if col not in formatted.columns:
                continue
            for i, is_sample in enumerate(flags):
                if is_sample and i < len(formatted):
                    v = formatted.at[i, col]
                    if v is not None and not isinstance(v, str):
                        formatted.at[i, col] = (_fmt_rate(v) if col in _RATE_COLS else format_cell(v)) + SAMPLE_SUFFIX
    records = formatted.to_dict("records")
    for rec, hidden in zip(records, _hidden_values(df, sample_mask)):
        rec.update(hidden)
    return records


def _hidden_values(df: pd.DataFrame, sample_mask: Optional[dict] = None) -> List[dict]:
    """One dict of `_HIDDEN_COLUMNS` per row of `df`, in its order. A P&L number is carried
    only when the cell is REAL: a missing cell, or one `sample_mask` flags as an
    illustrative sample, is `None`. `currency` / `on_book_*` come from `df` when
    `_table_children` put them there; a frame without them (a unit-test fixture) falls back
    on the instrument id's shape and on "dealt by then", the choice that never credits a
    one-sided jump to a difference."""
    if df.empty:
        return []
    n = len(df)

    def column(name: str, default) -> list:  # plain lists: a per-row `.iloc` costs 0.1 s on a full book
        return df[name].tolist() if name in df.columns else [default] * n

    instrument_ids, trade_ids, currencies = column("instrument_id", ""), column("trade_id", ""), column("currency", None)
    out = [{"trade_id": "" if _is_missing(trade_ids[i]) else str(trade_ids[i]),
            "currency": currency_label(str(instrument_ids[i])) if _is_missing(currencies[i]) else currencies[i]}
           for i in range(n)]
    for col, num_col in _NUM_COLUMN_OF.items():
        values = column(col, None)
        flags = (sample_mask or {}).get(col) or []
        for i in range(n):
            is_sample = i < len(flags) and bool(flags[i])
            out[i][num_col] = None if (is_sample or _is_missing(values[i])) else float(values[i])
    for flag in ("on_book_t1", "on_book_t2"):
        values = column(flag, 1)
        for i in range(n):
            out[i][flag] = 1 if _is_missing(values[i]) else int(values[i])
    return out


def table_columns(visible: List[str]) -> List[dict]:
    """The visible columns typed (ui.tabs.ranking): rates and USD amounts numeric with a
    display format, so they rank as numbers and the native filter compares `> 0`; dates
    and text as text, so `>= 2026-09` works on the two ISO date columns and text matches
    on "contains", case-insensitively. A cell carrying an illustrative "(sample)" value
    is a string in its numeric column: shown as it is, ranked last. Then the hidden
    bookkeeping columns (`_HIDDEN_COLUMNS`)."""
    columns = []
    for c in visible:
        name = _COLUMN_LABELS.get(c, c.replace("_", " ").title())
        if c in _RATE_COLS:
            columns.append(rk.numeric(name, c, rk.rate(6)))
        elif c in _USD_COLS:
            columns.append(rk.numeric(name, c, rk.amount()))
        else:
            columns.append(rk.text(name, c))
    columns += [{"name": c, "id": c, "type": "text"} for c in _HIDDEN_TEXT_COLUMNS]
    columns += [{"name": c, "id": c, "type": "numeric"} for c in _HIDDEN_NUMERIC_COLUMNS]
    return columns


def fx_blotter_table(df: pd.DataFrame, table_id: str = DATATABLE_ID,
                      sample_mask: Optional[dict] = None) -> dash_table.DataTable:
    """The trade table. Native column filtering (2026-09-21): it is what "P&L by currency,
    rows shown" follows. Native sort too (ui.tabs.ranking, 2026-09-22); the trade-date /
    pair order is the order it opens in. The sub-tab is rebuilt whole on a data revision,
    so the typed filter and the chosen order are kept in the browser session
    (`persistence`), as the Options table does."""
    from ui.tabs.options import FILTER_ROW_CSS  # the legible filter row, verified in a browser there

    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns] if not df.empty else list(_DISPLAY_COLUMNS)
    data_records = format_rows(df, sample_mask)
    style_data_conditional = _sample_style_conditional(sample_mask) if sample_mask else []
    return dash_table.DataTable(
        id=table_id,
        columns=table_columns(cols),
        hidden_columns=list(_HIDDEN_COLUMNS),
        data=data_records,
        filter_action="native",
        filter_options={"case": "insensitive", "placeholder_text": "filter"},
        **rk.sortable(table_id, persisted=("filter_query",)),
        css=[{"selector": ".show-hide", "rule": "display: none"},  # no "Toggle Columns" for bookkeeping fields
             *FILTER_ROW_CSS],
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_header={"fontWeight": "bold"},
        style_filter={"fontStyle": "italic"},
        style_data_conditional=style_data_conditional,
        page_size=25,
        page_action="native",
    )


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole FX sub-tab body: a P&L strip (`_fx_strip`), `fx_blotter_rows` rows with
    missing marks/P&L sample-filled (`_fill_sample_values`), plus a short note when
    there are no trades (table still renders, empty, with the full column set -- "rows
    must always render" rule elsewhere in this app).

    The strip and the table fail separately (2026-09-18): each is built under
    `ui.tabs.blotter._safe_section`, so whatever breaks one -- on the Bloomberg PC it was
    one stored value that was not a number -- leaves the other on the page, and the card
    in its place names the table.column and the row of any such value instead of a bare
    "could not convert string to float". On success both are passed through untouched, so
    the children stay a flat list: strip, the two per-currency tables (one block, each
    table under its own `_safe_section` too), optional caption, trade table."""
    from ui.tabs.blotter import _error_card, _safe_section

    children = [_safe_section("P&L strip", lambda: _fx_strip(conn, as_of), conn)]
    try:
        table_children = _table_children(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- same deliberate breadth as `_safe_section`
        import logging
        logging.getLogger(__name__).exception("Blotter FX trade table failed to render for as_of=%s", as_of)
        table_children = [_error_card("FX trade table", exc, conn)]
    # "Rows shown" starts from the trade table's own records -- the very dicts the browser
    # sends back as `derived_virtual_data` -- so it is right before any callback has run.
    table = next((c for c in table_children if isinstance(c, dash_table.DataTable)), None)
    table_rows = list(table.data or []) if table is not None else []
    children.append(html.Div(id=CURRENCY_TABLES_ID, className="fx-ccy-tables", children=[
        _safe_section(FIXED_TITLE, lambda: fixed_currency_panel(conn, as_of), conn),
        _safe_section(SHOWN_TITLE, lambda: shown_currency_panel(table_rows), conn),
    ]))
    children.extend(table_children)
    return html.Div(children)


def _with_currency_fields(conn: sqlite3.Connection, df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """`df` plus what the hidden columns need (`_hidden_values`): each row's `currency` and
    whether the trade had been dealt by the T-1 / T-2 close (`trade_date <=` that date,
    `value_book`'s own rule for what is on the book), the same two dates the strip and
    `fx_blotter_rows` use."""
    from engine.pnl.ledger import period_reference_dates

    refs = period_reference_dates(as_of)
    out = df.copy()
    out["currency"] = out["instrument_id"].map(currency_labels(conn, out["instrument_id"].unique().tolist()))
    trade_dates = out["trade_date"].astype(str)
    out["on_book_t1"] = (trade_dates <= refs["daily"]).astype(int)
    out["on_book_t2"] = (trade_dates <= refs["previous_day"]).astype(int)
    return out


def _table_children(conn: sqlite3.Connection, as_of: str) -> list:
    """The optional "no trades" / sample caption and the trade table, in display order."""
    df = fx_blotter_rows(conn, as_of, value_fn=_priced_value_fn, products=FX_PRODUCTS)
    children = []
    sample_mask = None
    if df.empty:
        children.append(html.P("No FX trades on file for this as-of date.",
                                className="section-kicker", style={"fontStyle": "italic"}))
    else:
        df = _with_currency_fields(conn, df, as_of)
        df, sample_mask = _fill_sample_values(df)
        if any(any(flags) for flags in sample_mask.values()):
            children.append(html.P(SAMPLE_CAPTION, className="section-kicker",
                                    style={"fontStyle": "italic"}))
        else:
            sample_mask = None
    children.append(fx_blotter_table(df, sample_mask=sample_mask))
    return children
