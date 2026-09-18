"""Rates tab: the Blotter "Rates" sub-tab's real view (2026-09-15, replacing the
placeholder that stood in while no IRS trades/marks existed -- see `ui.tabs.blotter`'s
module docstring for the placeholder history).

`engine/rates` (rates-pricer) now writes `PAR_RATE` / `PV_USD` / `DV01_USD` marks with
`source='QL_PRICER'`, which `data/ingest/schema.py`'s `OFFICIAL_MARK_SOURCE` makes
official for those three mark_types (`BBG_BDH` is reconciliation-only for them now,
mirroring `BNP_BVAL` for FX). This module therefore bypasses
`ui.tabs.blotter_pricing.priced_value_book` entirely -- that pipeline only builds
FX/FUTURE rows (see its own docstring) -- and reads IRS trades plus their official
marks straight from the DB, exactly one instrument per swap (CLAUDE.md "BNP file ->
tables", INTEREST_RATE_SWAP rows), so a plain `instrument_id` join is enough; no
per-trade settle_date disambiguation is needed.

Per CLAUDE.md "official marks": every priced column here reads `marks_official`, never
`marks` directly. The one exception is the recon-status column, which by definition
needs the reconciliation-only `BBG_BDH` PV -- that one read goes straight at `marks`
with an explicit `source = 'BBG_BDH'` filter, never through `marks_official` (which
would return QL_PRICER rows for the same mark_type and silently mask the comparison).

Direction: `trades.quantity > 0` = pay fixed (CLAUDE.md "Leg layouts": a payer has a
negative FIXED leg; `engine/rates/store.py` uses the same sign to build
`ql.Swap.Payer`/`Receiver`).

**Pay / Receive is set by hand, in the table (2026-09-18).** The blotter export carries
no pay/receive marker for a swap (Side is "Buy" on every row, notionals unsigned), so
every swap loads as pay fixed and the receivers come out with P&L and DV01 of the wrong
sign. `data/ingest/irs_direction.py` is the data layer (its docstring has the full
story); this module is only its front end and never writes SQL of its own:

  * Direction is a dropdown cell ("Pay fixed" / "Receive fixed"), the ONLY editable
    column. A change is applied with `irs_direction.set_direction` on a writable
    connection (`data.ingest.schema.connect`, as the Options and Manual entry saves do),
    the Bloomberg feed is woken so today's swap marks are rewritten, and both revision
    stores (`ui/revision.py`) are published so the header, the Blotter strips and this
    table redraw with no page reload.
  * "Set by" says where the direction came from: "You" (a stored override), "File" (an
    explicit short marker in the export) or "Not set: assumed pay fixed". Rows still
    waiting for the user's choice are tinted with a gold left edge.
  * While any swap is waiting, a notice above the table counts them and offers one
    button, "The rest are pay fixed: confirm", which stores PAY on every swap still
    flagged. Picking the value a swap already has is not an edit a DataTable reports, so
    this button is how a correct pay fixed is confirmed.
  * The callback always answers with rows rebuilt from the database, so a refused change
    is reverted in the cell and its reason is said in one line under the table. A
    direction is never invented: a swap whose stored quantity is unusable shows a blank.
  * Nothing is re-priced or recomputed here. On a flip `irs_direction` reverses the
    swap's own PV / DV01 / settled-cashflow marks in place on every date (a receiver is
    exactly minus a payer) and leaves the par rate alone, so the rebuilt rows show the
    opposite sign at once and the swap keeps its history; the Notional shows its new sign
    (a short in brackets) because it is `trades.quantity`.

**Refreshes itself in place (`ui/revision.py`).** A second callback listens to both
revision stores and replaces the table's rows and the notice, returning `no_update` for
the rows when nothing on screen changed, so new marks never disturb a dropdown the user
has open. The view therefore does not need the Blotter to rebuild the whole sub-tab on a
revision (`ui/tabs/blotter.py::_MARKS_REBUILD_SCOPES`); it tolerates one that does:
such a rebuild would wipe the line under the table the moment it was written, so
`_LAST_MESSAGE` keeps the last change's message for the rebuilt layout, the same device
as `ui/tabs/options.py::_LAST_SAVE`. The notice, its button and the status line
are ALWAYS in the layout (hidden / empty when there is nothing to say): Dash's renderer
silently drops a callback any of whose Outputs is missing from the page.

Recon status: no existing recon-status convention exists elsewhere in this app to
reuse (checked `ui/tabs/blotter.py`, `ui/tabs/reconciliation.py`), so this module
defines its own, simple, stated tolerance (`RECON_ABS_TOLERANCE_USD` /
`RECON_REL_TOLERANCE`, same "max(fixed, relative)" shape as CLAUDE.md's own workbook
reconciliation tolerances, not the same numbers -- those are calibrated to the
workbook's own known rounding, this one is not): OK within tolerance, WARN outside it,
MISSING when no BBG_BDH PV_USD mark exists for that trade's instrument/date, or when
our own official PV_USD mark is itself missing (nothing to compare against). Missing
marks are never defaulted to zero anywhere in this module, per CLAUDE.md.
"""
from __future__ import annotations

import numbers
import os
import sqlite3
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import pandas as pd
from dash import Input, Output, State, dash_table, html

from data.ingest import irs_direction
from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID
from ui.tabs.formatting import format_cell

DATATABLE_ID = "rates-datatable"
NOTICE_ID = "rates-direction-notice"
NOTICE_TEXT_ID = "rates-direction-notice-text"
CONFIRM_REST_ID = "rates-direction-confirm-rest"
STATUS_ID = "rates-direction-status"
# The Blotter tab's own date picker, as a literal: importing `ui.tabs.blotter` here would
# be circular (it imports this module). Same device as `ui/tabs/options.py`.
DEFAULT_DATE_PICKER_ID = "blotter-date"

RECON_ABS_TOLERANCE_USD = 1000.0
RECON_REL_TOLERANCE = 0.005

_MARK_TYPES = ("PAR_RATE", "PV_USD", "DV01_USD", "CASHFLOW_USD")

# pnl_usd (2026-09-17) = PV_USD + CASHFLOW_USD, the same figure engine/pnl/valuation
# puts on the swap's value_book row (CLAUDE.md "P&L conventions", IRS), so this table,
# the Rates strip above it and the Total book's Rates row all agree.
# set_by (2026-09-18) sits next to the direction it explains; every other column keeps
# its place.
_DISPLAY_COLUMNS = [
    "trade_id", "instrument_id", "ccy", "direction", "set_by", "notional",
    "par_rate", "pv_usd", "dv01_usd", "cashflow_usd", "pnl_usd", "recon_status",
]
_COLUMN_LABELS = {
    "trade_id": "Trade id", "instrument_id": "Swap", "ccy": "Ccy",
    "direction": "Direction", "set_by": "Set by", "notional": "Notional",
    "par_rate": "Par rate", "pv_usd": "PV (USD)", "dv01_usd": "DV01 (USD)",
    "cashflow_usd": "Settled cashflows (USD)", "pnl_usd": "P&L (USD)",
    "recon_status": "Recon (QL_PRICER vs BBG SWPM)",
}

# The direction cell holds `irs_direction`'s own code, so what the dropdown hands back is
# exactly what `set_direction` takes; the words are the dropdown's labels.
DIRECTION_LABELS = {"PAY": "Pay fixed", "RECEIVE": "Receive fixed"}
EDITABLE_COLUMNS = ("direction",)

# "Set by", keyed by `irs_direction.direction_report`'s `source`.
SET_BY_NOT_SET = "Not set: assumed pay fixed"
SET_BY_LABELS = {"USER": "You", "FILE": "File", "DEFAULT": SET_BY_NOT_SET}
# A swap whose stored quantity is unusable has no direction to assume anything about.
SET_BY_UNKNOWN = "Not set"
# Matches both "Not set..." wordings above and nothing else: the flagged-row styling keys
# on the displayed column, so it needs no hidden bookkeeping column.
_FLAGGED_QUERY = '{set_by} contains "Not set"'

CONFIRM_REST_LABEL = "The rest are pay fixed: confirm"

# db -> (when, message, is_error) of the last direction change, for the layout the
# Blotter rebuilds right after it (module docstring).
_LAST_MESSAGE: Dict[str, Tuple[float, str, bool]] = {}
_MESSAGE_SECONDS = 30.0


def direction_code(quantity) -> str:
    """'PAY' for `trades.quantity > 0`, 'RECEIVE' for `< 0` (CLAUDE.md / engine/rates
    sign convention); '' when the quantity is zero or not a number -- a direction is
    never invented."""
    if isinstance(quantity, bool) or not isinstance(quantity, numbers.Real) or quantity != quantity:
        return ""
    if quantity > 0:
        return "PAY"
    return "RECEIVE" if quantity < 0 else ""


def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def _norm_path(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _db_key(conn: sqlite3.Connection) -> Optional[str]:
    """Normalised path of the connection's main database file; None for ':memory:'."""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list"):
            if name == "main":
                return _norm_path(path) if path else None
    except sqlite3.Error:
        return None
    return None


def recon_status(official_pv_usd, bbg_pv_usd) -> str:
    """OK / WARN / MISSING -- see module docstring for the tolerance and why MISSING
    covers either side being absent."""
    if _is_missing(official_pv_usd) or _is_missing(bbg_pv_usd):
        return "MISSING"
    tolerance = max(RECON_ABS_TOLERANCE_USD, abs(float(official_pv_usd)) * RECON_REL_TOLERANCE)
    return "OK" if abs(float(official_pv_usd) - float(bbg_pv_usd)) <= tolerance else "WARN"


def irs_rows(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """One row per IRS trade on file (regardless of whether it has been priced yet --
    an un-priced trade still shows its trade id/ccy/notional/direction with the mark
    columns blank, matching the "rows must always render" rule elsewhere in this app),
    with the official PAR_RATE/PV_USD/DV01_USD marks for `as_of` and a recon_status
    against the reconciliation-only BBG_BDH PV.

    `direction` is `irs_direction`'s code ('PAY' | 'RECEIVE' | '' when the stored
    quantity is unusable); `direction_source` / `needs_user_choice` / `set_by` come from
    `irs_direction.direction_report`, which is read-only and needs no override table."""
    empty_cols = ["trade_id", "instrument_id", "ccy", "quantity", "notional", "direction",
                  "direction_source", "needs_user_choice", "set_by",
                  "par_rate", "pv_usd", "dv01_usd", "cashflow_usd", "pnl_usd", "bbg_pv_usd", "recon_status"]
    trades = pd.read_sql_query(
        # trades_official (2026-09-16): excludes source='BNP' so an IRS swap loaded
        # from both the once-daily BNP snapshot and the real-time blotter under two
        # different trade_ids (docs/open-questions.md item 55) is not listed, and its
        # PV/DV01 not double-counted, twice.
        "SELECT t.trade_id, t.instrument_id, i.base_ccy AS ccy, t.quantity "
        "FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id "
        "WHERE t.product = 'IRS' ORDER BY t.trade_id",
        conn,
    )
    if trades.empty:
        return pd.DataFrame(columns=empty_cols)

    official = pd.read_sql_query(
        "SELECT instrument_id, mark_type, value FROM marks_official "
        "WHERE as_of_date = ? AND mark_type IN ('PAR_RATE','PV_USD','DV01_USD','CASHFLOW_USD')",
        conn, params=(as_of,),
    )
    official_pivot = (official.pivot_table(index="instrument_id", columns="mark_type",
                                            values="value", aggfunc="first")
                       if not official.empty else pd.DataFrame())

    # Reconciliation PV: Bloomberg SWPM via BBG_BDH when a pull has written one, else a
    # MANUAL PV_USD typed in from the terminal on the Market data tab (2026-09-17: blpapi
    # has no per-trade SWPM valuation, so manual entry is the practical recon path).
    bbg = pd.read_sql_query(
        "SELECT instrument_id, value FROM marks "
        "WHERE as_of_date = ? AND mark_type = 'PV_USD' AND source IN ('BBG_BDH','MANUAL') "
        "ORDER BY CASE source WHEN 'BBG_BDH' THEN 0 ELSE 1 END DESC",
        conn, params=(as_of,),
    )
    bbg_map = dict(zip(bbg["instrument_id"], bbg["value"]))

    df = trades.copy()
    # Signed, as the book shows it (user, 2026-09-18): a short (receive fixed) is a
    # negative notional, rendered in brackets by `_fmt_notional`.
    df["notional"] = df["quantity"]
    report = {str(r["trade_id"]): r for r in irs_direction.direction_report(conn)}
    directions, sources, flags, set_by = [], [], [], []
    for trade_id, quantity in zip(df["trade_id"], df["quantity"]):
        entry = report.get(str(trade_id))
        direction = entry["direction"] if entry else direction_code(quantity)
        source = entry["source"] if entry else ""
        directions.append(direction)
        sources.append(source)
        flags.append(bool(entry["needs_user_choice"]) if entry else False)
        # "assumed pay fixed" is only true of a swap that IS on file as pay fixed.
        set_by.append(SET_BY_UNKNOWN if source == "DEFAULT" and direction != "PAY"
                      else SET_BY_LABELS.get(source, ""))
    df["direction"] = directions
    df["direction_source"] = sources
    df["needs_user_choice"] = flags
    df["set_by"] = set_by
    for mark_type in _MARK_TYPES:
        col = mark_type.lower()
        if mark_type in official_pivot.columns:
            df[col] = df["instrument_id"].map(official_pivot[mark_type])
        else:
            df[col] = float("nan")
    df["pnl_usd"] = [pv + cf if not (_is_missing(pv) or _is_missing(cf)) else float("nan")
                     for pv, cf in zip(df["pv_usd"], df["cashflow_usd"])]
    df["bbg_pv_usd"] = df["instrument_id"].map(bbg_map)
    df["recon_status"] = [recon_status(o, b) for o, b in zip(df["pv_usd"], df["bbg_pv_usd"])]
    return df


def _fmt_rate(value) -> str:
    if _is_missing(value):
        return "n/a"
    return f"{float(value) * 100:.4f}%"


def _fmt_notional(value) -> str:
    """Whole units, a short in brackets (`format_cell`'s own convention)."""
    return "" if _is_missing(value) else format_cell(value)


def _fmt_usd(value) -> str:
    return "n/a" if _is_missing(value) else format_cell(value)


def format_rows(df: pd.DataFrame) -> tuple:
    """`(data_records, style_data_conditional)` for `rates_table`, split out so it can
    be unit-tested without Dash, matching `ui.tabs.blotter._format_rows`'s convention."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    for col in cols:
        if col == "notional":
            formatted[col] = formatted[col].map(_fmt_notional)
        elif col == "par_rate":
            formatted[col] = formatted[col].map(_fmt_rate)
        elif col in ("pv_usd", "dv01_usd", "cashflow_usd", "pnl_usd"):
            formatted[col] = formatted[col].map(_fmt_usd)
    data_records = formatted.to_dict("records")
    return data_records, table_styles()


def table_styles() -> List[dict]:
    """`style_data_conditional`; independent of the data, so the direction callback never
    has to resend it. Order matters (later rules win): the row tint first, then the
    cell-level rules on top of it."""
    return [
        # A swap still waiting for the user's pay/receive: tinted row, gold left edge,
        # and its "Set by" in the warning colour. Existing CSS variables only. The edge and
        # the outline below are a box-shadow / an outline, not borders: style.css forces
        # `border-color: var(--line) !important` on every table cell, so a gold BORDER
        # renders grey (seen in a real browser, 2026-09-18).
        {"if": {"filter_query": _FLAGGED_QUERY}, "backgroundColor": "var(--warn-bg)"},
        {"if": {"filter_query": _FLAGGED_QUERY, "column_id": "trade_id"},
         "boxShadow": "inset 4px 0 0 var(--gold)"},
        {"if": {"filter_query": _FLAGGED_QUERY, "column_id": "set_by"},
         "color": "var(--warn)", "fontWeight": "700"},
        # The one editable cell reads as an input.
        {"if": {"column_id": "direction"},
         "outline": "1px dashed var(--gold)", "outlineOffset": "-3px", "cursor": "pointer"},
        {"if": {"filter_query": "{recon_status} = 'WARN'", "column_id": "recon_status"},
         "color": "var(--neg)", "fontWeight": "700"},
        {"if": {"filter_query": "{recon_status} = 'OK'", "column_id": "recon_status"},
         "color": "var(--pos)", "fontWeight": "700"},
        {"if": {"filter_query": "{recon_status} = 'MISSING'", "column_id": "recon_status"},
         "color": "var(--muted)", "fontStyle": "italic"},
    ]


def table_columns(cols: Optional[List[str]] = None) -> List[dict]:
    """Column definitions. Direction is a dropdown cell and the only editable column
    (the table itself is `editable=False`; a column's own `editable` wins)."""
    columns = []
    for col in (cols if cols is not None else _DISPLAY_COLUMNS):
        spec = {"name": _COLUMN_LABELS.get(col, col.replace("_", " ").title()), "id": col}
        if col in EDITABLE_COLUMNS:
            spec["editable"] = True
            spec["presentation"] = "dropdown"
        columns.append(spec)
    return columns


def direction_dropdown() -> dict:
    """DataTable `dropdown`: the two directions, never clearable (a swap always faces
    one way; an unknown one is shown blank but cannot be SET to blank)."""
    return {"direction": {"clearable": False,
                          "options": [{"label": label, "value": code}
                                      for code, label in DIRECTION_LABELS.items()]}}


def rates_table(df: pd.DataFrame, table_id: str = DATATABLE_ID) -> dash_table.DataTable:
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns] if not df.empty else list(_DISPLAY_COLUMNS)
    data_records, style_data_conditional = format_rows(df)
    return dash_table.DataTable(
        id=table_id,
        columns=table_columns(cols),
        data=data_records,
        editable=False,  # per column: Direction only (`table_columns`)
        dropdown=direction_dropdown(),
        # A cell dropdown's menu is clipped by the table's scroll box unless it is forced
        # to display and given room under the last row (same fix as the Options table).
        css=[{"selector": ".Select-menu-outer", "rule": "display: block !important"}],
        style_table={"overflowX": "auto", "paddingBottom": "100px"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_cell_conditional=[
            {"if": {"column_id": "direction"}, "textAlign": "left", "minWidth": "150px"},
            {"if": {"column_id": "set_by"}, "textAlign": "left"},
        ],
        style_header={"fontWeight": "bold"},
        style_data_conditional=style_data_conditional,
        page_size=25,
        page_action="native",
    )


# --------------------------------------------------------------------------- pay / receive
def notice_text(flagged: int, total: int) -> str:
    """The sentence above the table while swaps still wait for a pay/receive; '' when
    none does."""
    if flagged <= 0:
        return ""
    swaps = "swap" if total == 1 else "swaps"
    verb = "has" if flagged == 1 else "have"
    assumed = "is assumed" if flagged == 1 else "are assumed"
    target = "it if you receive on it; its" if flagged == 1 else "the ones you receive on; their"
    return (f"{flagged} of {total} {swaps} {verb} no pay/receive in the file and {assumed} PAY FIXED. "
            f"Set Receive fixed on {target} P&L and DV01 change sign.")


_NOTICE_STYLE = {"display": "flex", "alignItems": "center", "flexWrap": "wrap", "gap": "12px",
                 "background": "var(--warn-bg)", "color": "var(--warn)",
                 "borderLeft": "4px solid var(--gold)", "borderRadius": "4px",
                 "padding": "8px 12px", "margin": "0 0 8px", "fontSize": "13px", "fontWeight": "600"}


def notice_style(flagged: int) -> dict:
    return dict(_NOTICE_STYLE) if flagged > 0 else {"display": "none"}


def _flag_counts(df: pd.DataFrame) -> Tuple[int, int]:
    """(swaps still waiting for the user's choice, swaps on file)."""
    if df.empty or "needs_user_choice" not in df.columns:
        return 0, len(df)
    return int(df["needs_user_choice"].astype(bool).sum()), len(df)


def direction_notice(flagged: int, total: int) -> html.Div:
    """The notice and its one button. Always rendered -- hidden when nothing is flagged --
    so the ids the direction callback writes to exist whenever the table does."""
    return html.Div(id=NOTICE_ID, role="status", style=notice_style(flagged), children=[
        html.Span(id=NOTICE_TEXT_ID, children=notice_text(flagged, total)),
        html.Button(CONFIRM_REST_LABEL, id=CONFIRM_REST_ID, n_clicks=0, className="btn"),
    ])


class DirectionResult(NamedTuple):
    ok: bool        # False when any requested change was refused
    message: str    # one line for under the table ('' = nothing to say)
    written: int    # swaps whose direction was stored (a confirmation counts)
    flipped: int    # of those, swaps that actually turned round


def detect_direction_edits(rows: Optional[List[dict]], previous: Optional[List[dict]]) -> List[Tuple[str, object]]:
    """[(trade_id, new value)] for every Direction cell that differs between the table's
    `data` and its `data_previous`, rows matched by trade id. Only what the user changed
    in THIS edit -- never a comparison with the database, so a table that has gone stale
    cannot flip a swap the user did not touch."""
    before = {str(r.get("trade_id")): r for r in (previous or []) if isinstance(r, dict)}
    edits = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("trade_id") in (None, ""):
            continue
        old = before.get(str(row["trade_id"]))
        if old is not None and (row.get("direction") or "") != (old.get("direction") or ""):
            edits.append((str(row["trade_id"]), row.get("direction")))
    return edits


def _describe(direction) -> str:
    return DIRECTION_LABELS.get(str(direction or "").strip().upper(), str(direction))


def apply_directions(db_path, changes: List[Tuple[str, object]],
                     nudge: Optional[Callable[[], None]] = None) -> DirectionResult:
    """Store each `(trade_id, direction)` with `irs_direction.set_direction` -- the one
    write path of this module -- on a writable connection. Never raises for bad input: a
    refused change (unknown trade, not a swap, not PAY / RECEIVE) is named in the message
    and the others still go through. `nudge` wakes the Bloomberg feed when a swap actually
    turned round, so today's marks are rewritten for the new direction."""
    if not changes:
        return DirectionResult(True, "", 0, 0)
    from data.ingest.schema import connect
    done, failures, flipped = [], [], 0
    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        return DirectionResult(False, f"Not changed: database not available ({exc}).", 0, 0)
    try:
        for trade_id, direction in changes:
            try:
                before = irs_direction.irs_signs(conn).get(str(trade_id))
                irs_direction.set_direction(conn, trade_id, direction)
            except ValueError as exc:
                failures.append(f"Not changed: {exc}.")
                continue
            except sqlite3.Error as exc:
                failures.append(f"Not changed: database error on {trade_id} ({exc}).")
                continue
            done.append((str(trade_id), direction))
            if irs_direction.irs_signs(conn).get(str(trade_id)) != before:
                flipped += 1
    finally:
        conn.close()
    if flipped and nudge is not None:
        nudge()
    parts = list(failures)
    if len(done) == 1:
        parts.append(f"{done[0][0]} set to {_describe(done[0][1])}.")
    elif done:
        parts.append(f"{len(done)} swaps set: " + ", ".join(f"{t} {_describe(d)}" for t, d in done) + ".")
    if flipped:
        parts.append("Its P&L and DV01 change sign." if flipped == 1 else "Their P&L and DV01 change sign.")
    return DirectionResult(not failures, " ".join(parts), len(done), flipped)


def confirm_rest_as_pay(db_path, nudge: Optional[Callable[[], None]] = None) -> DirectionResult:
    """The notice's button: store PAY on every swap still waiting for the user's choice
    (`irs_direction.needs_user_choice`). They are on file as pay fixed already, so nothing
    priced changes; what changes is that the choice is now the user's, and sticks."""
    from ui.app import connect_readonly
    try:
        conn = connect_readonly(db_path)
    except sqlite3.Error as exc:
        return DirectionResult(False, f"Not confirmed: database not available ({exc}).", 0, 0)
    try:
        waiting = irs_direction.needs_user_choice(conn)
    except sqlite3.Error as exc:
        return DirectionResult(False, f"Not confirmed: database error ({exc}).", 0, 0)
    finally:
        conn.close()
    if not waiting:
        return DirectionResult(True, "Nothing left to confirm: every swap has its pay/receive.", 0, 0)
    result = apply_directions(db_path, [(trade_id, "PAY") for trade_id in waiting], nudge)
    if not result.ok:
        return result
    swaps = "swap" if result.written == 1 else "swaps"
    return DirectionResult(True, f"{result.written} {swaps} confirmed as pay fixed.", result.written, result.flipped)


class DirectionEvent(NamedTuple):
    records: Optional[List[dict]]   # rows rebuilt from the database (None: could not be read)
    flagged: int
    total: int
    result: DirectionResult


def handle_direction_event(db_path, as_of: Optional[str], rows: Optional[List[dict]],
                           previous: Optional[List[dict]], confirm_rest: bool,
                           nudge: Optional[Callable[[], None]] = None) -> DirectionEvent:
    """What the direction callback does, free of Dash so it can be tested directly: apply
    the edited Direction cells (or the confirm-the-rest click), then rebuild the rows and
    the notice's counts from the DATABASE -- which is what reverts a refused cell."""
    if confirm_rest:
        result = confirm_rest_as_pay(db_path, nudge)
    else:
        result = apply_directions(db_path, detect_direction_edits(rows, previous), nudge)
    records, flagged, total = None, 0, 0
    try:
        records, flagged, total = read_view(db_path, as_of)
    except sqlite3.Error as exc:
        result = DirectionResult(False, (result.message + " " if result.message else "")
                                 + f"The table could not be refreshed ({exc}).", result.written, result.flipped)
    if result.message:
        _LAST_MESSAGE[_norm_path(db_path)] = (time.time(), result.message, not result.ok)
    return DirectionEvent(records, flagged, total, result)


def read_view(db_path, as_of: Optional[str]) -> Tuple[List[dict], int, int]:
    """`(table records, swaps still flagged, swaps on file)` straight from the database,
    on a read-only connection: what both callbacks answer with."""
    from ui.app import connect_readonly
    conn = connect_readonly(db_path)
    try:
        df = irs_rows(conn, as_of or "")
        records, _styles = format_rows(df)
        flagged, total = _flag_counts(df)
    finally:
        conn.close()
    return records, flagged, total


def _status_line(message: str, is_error: bool):
    if not message:
        return None
    return html.Span(message, className="source-result--error" if is_error else "source-result--info")


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole Rates sub-tab body: a short note when there are no IRS trades at all
    (table still renders, empty, with the full column set -- "rows must always render"),
    then the pay/receive notice (hidden when no swap is waiting), the blotter, and the
    one-line status under it (the last direction change's message when this layout is the
    rebuild that change caused, `_LAST_MESSAGE`)."""
    df = irs_rows(conn, as_of)
    flagged, total = _flag_counts(df)
    children = []
    if df.empty:
        children.append(html.P("No IRS trades on file for this as-of date.",
                                className="section-kicker", style={"fontStyle": "italic"}))
    children.append(direction_notice(flagged, total))
    children.append(rates_table(df))
    when, message, is_error = _LAST_MESSAGE.get(_db_key(conn) or "", (0.0, "", False))
    flash = _status_line(message, is_error) if message and time.time() - when <= _MESSAGE_SECONDS else None
    children.append(html.Div(id=STATUS_ID, className="status-line", role="status", children=flash))
    return html.Div(children)


def register_callbacks(app, get_db_path: Callable[[], object],
                        date_picker_id: str = DEFAULT_DATE_PICKER_ID) -> None:
    """Two callbacks: the write (a Direction cell edited, or the notice's button clicked)
    and the in-place refresh on a data / book revision. Same convention as
    `ui.tabs.options.register_callbacks` (called from `ui.tabs.blotter.register_callbacks`).
    Every Output / State is either rendered by `build_layout` -- always, together -- or
    always in the page (the revision stores, the Blotter's date picker)."""
    from dash import ctx, no_update
    from dash.exceptions import MissingCallbackContextException

    def _nudge_feed():
        # Same call `ui.tabs.options`, `ui.tabs.manual_entry` and `ui.uploads` make after a
        # write: one extra Bloomberg cycle now. No feed on this machine: a no-op.
        feed = getattr(app, "bloomberg_feed", None)
        if feed is not None:
            feed.trigger_now()

    def _triggered_ids() -> set:
        try:
            return {t["prop_id"].split(".")[0] for t in (ctx.triggered or []) if t.get("prop_id")}
        except MissingCallbackContextException:  # called directly (a test), not by Dash
            return set()

    @app.callback(
        Output(DATATABLE_ID, "data"),
        Output(NOTICE_ID, "style"),
        Output(NOTICE_TEXT_ID, "children"),
        Output(STATUS_ID, "children"),
        Output(DATA_REVISION_ID, "data", allow_duplicate=True),
        Output(BOOK_REVISION_ID, "data", allow_duplicate=True),
        Input(DATATABLE_ID, "data_timestamp"),
        Input(CONFIRM_REST_ID, "n_clicks"),
        State(DATATABLE_ID, "data"),
        State(DATATABLE_ID, "data_previous"),
        State(date_picker_id, "date"),
        prevent_initial_call=True,
    )
    def _on_direction(_edited_at, n_clicks, rows, previous, as_of_date=None):
        """`prevent_initial_call` does not cover a sub-tab rendered later by the Blotter
        (the revision stores, Outputs here, are already on the page), so a call that
        carries neither a click nor an edited cell is answered with no change at all."""
        nothing = (no_update,) * 6
        triggered = _triggered_ids() - {""}
        confirm_rest = bool(n_clicks) and (not triggered or CONFIRM_REST_ID in triggered)
        edited = not confirm_rest and (not triggered or DATATABLE_ID in triggered)
        if not confirm_rest and not (edited and detect_direction_edits(rows, previous)):
            return nothing
        from ui import revision
        db_path = get_db_path()
        event = handle_direction_event(db_path, as_of_date, rows, previous, confirm_rest, _nudge_feed)
        status = _status_line(event.result.message, not event.result.ok)
        if event.result.written:
            # Published here, at once, so nothing waits for the poll (ui/revision.py).
            data_rev = revision.file_signature(db_path) or no_update
            book_rev = revision.book_signature(db_path) or no_update
        else:
            data_rev = book_rev = no_update
        if event.records is None:  # the database could not be read back: say so, touch nothing else
            return no_update, no_update, no_update, status, data_rev, book_rev
        return (event.records, notice_style(event.flagged), notice_text(event.flagged, event.total),
                status, data_rev, book_rev)

    @app.callback(
        Output(DATATABLE_ID, "data", allow_duplicate=True),
        Output(NOTICE_ID, "style", allow_duplicate=True),
        Output(NOTICE_TEXT_ID, "children", allow_duplicate=True),
        Input(DATA_REVISION_ID, "data"),
        Input(BOOK_REVISION_ID, "data"),
        State(DATATABLE_ID, "data"),
        State(date_picker_id, "date"),
        prevent_initial_call=True,
    )
    def _refresh(_data_rev, _book_rev, rows, as_of_date=None):
        """New marks, a re-upload or a direction set elsewhere: refresh the rows and the
        notice IN PLACE (`ui/revision.py`), so this view does not depend on the Blotter
        rebuilding the whole sub-tab. When nothing on screen changed the table is left
        alone, so a Bloomberg write never disturbs a dropdown the user has open."""
        if not as_of_date:
            return no_update, no_update, no_update
        try:
            records, flagged, total = read_view(get_db_path(), as_of_date)
        except sqlite3.Error:  # locked / mid-write: the next revision tries again
            return no_update, no_update, no_update
        return ((no_update if records == rows else records),
                notice_style(flagged), notice_text(flagged, total))
