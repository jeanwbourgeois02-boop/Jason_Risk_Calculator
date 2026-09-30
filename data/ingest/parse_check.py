"""The parsing check: how an uploaded blotter file would be read, row by row, and what an
upload of it would do to the book on file, with nothing saved (user, 2026-09-30: the Data tab's
"parsing diagnostic").

``check_file(payload, filename, db_path)`` reads the file with the upload's own reader
(``upload.preview_frame`` / ``validate_blotter_shape``), parses it with ``blotter.parse`` under
the upload's own book filter (``config/book.yaml``), against a read-only connection to the
database (so Bloomberg's stored contract dates apply exactly as on upload), and compares the
Trade Ids with the trades on file by the upload's merge rule (``upload._stage_and_publish``).
It never writes: the database is opened ``mode=ro``, a missing database is an empty book and is
never created, and ``blotter.load`` / ``write_parsed`` are never called. It never raises: a file
that cannot be read comes back ``ok`` False with a plain ``error``.

Plain words in ``summary``, ``file_notes``, ``product`` and ``message``; ``instrument_id`` and
``bbg_ticker`` are raw fields for a screen to show on hover.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Dict, List

import pandas as pd

from data.ingest import blotter
from data.ingest.common import PERPETUAL, UNRECOGNISED

log = logging.getLogger(__name__)

# Row statuses, in the words the screen shows.
OK = "OK"
WARNING = "WARNING"
NOT_RECOGNISED = "NOT RECOGNISED"
EXCLUDED = "EXCLUDED"
CANCELS = "CANCELS"

# product code -> (singular, plural), in the order the summary lists them
PRODUCT_NAMES = {
    "FUTURE": ("Future", "futures"),
    "CMDTY_OPTION": ("Option on a future", "options on futures"),
    "LME_FWD": ("LME forward", "LME forwards"),
    "EQ_OPTION": ("Listed option", "listed options"),
    "FX_FWD": ("FX forward", "FX forwards"),
    "FX_SPOT": ("FX spot", "FX spot"),
    "FX_SWAP": ("FX swap", "FX swaps"),
    "FX_OPTION": ("FX option", "FX options"),
    UNRECOGNISED: ("Not recognised", "not recognised"),
}

# Technical names in the parser's reasons, said in plain words on screen.
_PLAIN = (("config/contracts.csv", "the contract list"), ("config/book.yaml", "the book settings"))


def _plain(text: str) -> str:
    """The parser's sentence with file paths in plain words and a capital first letter."""
    s = " ".join(str(text or "").split())
    for raw, plain in _PLAIN:
        s = s.replace(raw, plain)
    return s[:1].upper() + s[1:] if s else ""


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _product_name(code: str) -> str:
    return PRODUCT_NAMES.get(code, (code.replace("_", " ").capitalize(), ""))[0]


def _failed(filename: str, error: str) -> dict:
    return {"ok": False, "error": error, "filename": filename, "summary": error,
            "counts": {"rows": 0, "loaded": 0, "by_product": {}, "not_recognised": 0, "warnings": 0,
                       "excluded": 0, "superseded": 0, "cancelled": 0, "prices_converted": 0},
            "merge": {"new": 0, "replace": 0, "remove": 0, "remove_manual": 0, "on_file_before": 0,
                      "on_file_after": 0},
            "file_notes": [], "rows": []}


def _book_on_file(db_path) -> tuple:
    """(read-only connection or None, trade ids on file, MANUAL trade ids on file). A missing
    database, or one with no trades table, is an empty book; nothing is ever created."""
    if db_path is None:
        return None, set(), set()
    path = Path(db_path).resolve()
    if not path.exists():
        return None, set(), set()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10.0, check_same_thread=False)
    try:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trades'").fetchone()
        if not has:
            return conn, set(), set()
        before = {str(r[0]) for r in conn.execute("SELECT trade_id FROM trades")}
        manual = {str(r[0]) for r in conn.execute("SELECT trade_id FROM trades WHERE source = 'MANUAL'")}
        return conn, before, manual
    except Exception:
        conn.close()
        raise


def _price_factor(trade, instrument) -> float:
    """The broker_price_scale the parser applied to this trade's Price cell: the root's scale when
    the stored fill is the cell times it, else 1 (no scale, or a fill rebuilt from another column)."""
    if trade.product not in ("FUTURE", "CMDTY_OPTION") or not trade.broker_price or instrument is None:
        return 1.0
    cell = blotter._num(trade.broker_price)
    if math.isnan(cell):
        return 1.0
    try:
        from data.contracts import get_root
        scale = float(get_root(instrument.base_ccy).broker_price_scale)
    except Exception:
        return 1.0
    if scale != 1.0 and abs(cell * scale - trade.price) <= 1e-9 * max(1.0, abs(trade.price)):
        return scale
    return 1.0


def _unit(trade, instrument) -> str:
    if trade.product in ("FUTURE", "CMDTY_OPTION", "EQ_OPTION"):
        return "lots"
    if trade.product == "LME_FWD":
        return "t"
    if trade.product == UNRECOGNISED:
        return "as in the file"
    return instrument.base_ccy if instrument is not None else ""


def _expiry(trade, instrument, legs_by_trade: Dict[str, list]) -> str:
    """A future's or option's last trade date, an LME ticket's prompt, an FX leg's value date."""
    legs = legs_by_trade.get(trade.trade_id) or []
    if legs:
        return max(leg.settle_date for leg in legs)
    if instrument is not None and instrument.expiry_date and instrument.expiry_date != PERPETUAL:
        return instrument.expiry_date
    return ""


def _blank_row(row_no: int, cells: pd.Series) -> dict:
    """A row of the file before the parser's outcome is known: the cells as written."""
    side = blotter._side(cells.get("Side")) or ""
    return {"row_no": row_no, "trade_id": blotter._s(cells.get("Trade Id")),
            "symbol": blotter._s(cells.get("Symbol")),
            "fin_type": blotter._s(cells.get("Fin Type")) or blotter._s(cells.get("Product")),
            "status": "", "product": "", "instrument_id": "", "bbg_ticker": "", "expiry": "",
            "trade_date": "", "side": side, "quantity": None, "unit": "",
            "price_in_file": blotter._s(cells.get("Price")), "price_used": None, "price_factor": 1.0,
            "strategy": "", "pb_root": blotter._s(cells.get("PBRoot")), "trade_type": "",
            "on_file": "", "message": ""}


_ROW_RE = re.compile(r"^row (\d+) ")
_FILTER_COLUMNS = {"fund": "Fund", "trader": "Trader", "desk": "Desk"}
_TRADE_TYPE_WORDS = {"CROSS_EXCHANGE": "Cross-exchange", "CROSS_PRODUCT": "Cross-product",
                     "TERM_STRUCTURE": "Calendar"}


def check_file(payload: bytes, filename: str, db_path) -> dict:
    """How `payload` (a blotter file's bytes, named `filename`) would load against the database at
    `db_path`, with nothing written. Never raises.

    Returns ``{ok, error, filename, summary, counts, merge, file_notes, rows}``:

    - ``counts``: rows (the file's data rows), loaded (trades an upload would write, not recognised
      included), by_product {plain name: n}, not_recognised, warnings (the parser's, as the upload
      counts them), excluded (status pending / draft / error, the book filter, an earlier version of
      a repeated Trade Id), superseded (of excluded: earlier versions), cancelled (rows whose status
      removes a trade), prices_converted (fills taken from the broker's units to Bloomberg's).
    - ``merge``: new, replace, remove (cancelled and on file), remove_manual (an old database's
      manual entries the file does not name), on_file_before, on_file_after: the upload's merge rule.
    - ``file_notes``: plain sentences about the file as a whole.
    - ``rows``: one dict per data row, in file order (see the module docstring and ``_blank_row``);
      status OK | WARNING | NOT RECOGNISED | EXCLUDED | CANCELS; on_file new | replaces | removes | ''.
    """
    filename = str(filename or "blotter")
    try:
        return _check(payload, filename, db_path)
    except Exception as e:     # a diagnostic never takes the screen down
        log.debug("parse check of %s failed", filename, exc_info=True)
        return _failed(filename, f"The file could not be checked: {_plain(str(e)) or type(e).__name__}.")


def _check(payload, filename: str, db_path) -> dict:
    from data.ingest import upload

    if not payload:
        return _failed(filename, "The file is empty.")
    if len(payload) > upload.MAX_BYTES:
        return _failed(filename, "Choose a file smaller than 25 MB.")
    try:
        frame = upload.preview_frame(bytes(payload), filename)
        upload.validate_blotter_shape(frame)
    except ValueError as e:
        return _failed(filename, _plain(str(e)))
    except Exception as e:
        return _failed(filename, f"The file could not be read: {_plain(str(e)) or type(e).__name__}.")

    df = blotter.canonicalize_columns(frame).reset_index(drop=True)   # the frame parse() walks
    try:
        conn, before, manual = _book_on_file(db_path)
    except sqlite3.Error as e:
        return _failed(filename, f"The book on file could not be read: {e}.")
    with closing(conn) if conn is not None else _NullContext():
        try:
            res = blotter.parse(df, filename, conn=conn)
        except ValueError as e:
            return _failed(filename, _plain(str(e)))
    return _report(filename, df, res, before, manual)


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _report(filename: str, df: pd.DataFrame, res, before: set, manual: set) -> dict:
    rows: Dict[int, dict] = {int(i) + 2: _blank_row(int(i) + 2, cells) for i, cells in df.iterrows()}

    # Earlier versions of a repeated Trade Id: dropped before the rows are parsed.
    day_first = blotter.detect_day_first(df)
    kept, _n, _how = blotter._dedupe_versions(df, day_first)
    winner_by_id = {blotter._s(kept.at[i, "Trade Id"]): int(i) + 2 for i in kept.index} if "Trade Id" in kept else {}
    superseded = 0
    for i in df.index.difference(kept.index):
        r = rows[int(i) + 2]
        winner = winner_by_id.get(r["trade_id"])
        how = {"Version": "the kept Version", "LastModified": "modified last"}.get(res.superseded_by,
                                                                                  "last in the file")
        r["status"] = EXCLUDED
        r["message"] = (f"Replaced by row {winner} ({how} of the same Trade Id): not loaded" if winner
                        else "An earlier version of a repeated Trade Id: not loaded")
        superseded += 1

    # Status rows: cancelled / void / deleted / rejected / failed remove a trade, the rest only exclude.
    loaded_ids = {t.trade_id for t in res.trades}
    removed: List[str] = []
    n_cancelled = n_status_excluded = 0
    for row_no, trade_id, _symbol, status in res.excluded_status_rows:
        r = rows.get(row_no)
        if r is None:
            continue
        if blotter._status_removes(status):
            n_cancelled += 1
            r["status"] = CANCELS
            if trade_id and trade_id in before and trade_id not in loaded_ids:
                r["on_file"] = "removes"
                removed.append(trade_id)
                r["message"] = f"Status {status!r}: removes this trade from the book on file"
            elif trade_id:
                r["message"] = f"Status {status!r}: this Trade Id is not on file, nothing to remove"
            else:
                r["message"] = f"Status {status!r} and no Trade Id: nothing to remove"
        else:
            n_status_excluded += 1
            r["status"] = EXCLUDED
            r["message"] = f"Status {status!r}: not a trade yet, not loaded"

    # The book filter (config/book.yaml): not recorded per row by the parser, so asked again here.
    book = res.book_filter or blotter.BookFilter()
    n_filtered = 0
    for i in kept.index:
        r = rows[int(i) + 2]
        if r["status"]:
            continue
        by = book.excluded_by(kept.loc[i])
        if by is not None:
            column = _FILTER_COLUMNS[by]
            r["status"] = EXCLUDED
            r["message"] = f"{column} {blotter._s(kept.at[i, column])!r} is not one of the book's {by}s: not loaded"
            n_filtered += 1

    # The trades, joined to their row, instrument and legs.
    legs_by_trade: Dict[str, list] = {}
    for leg in res.legs:
        legs_by_trade.setdefault(leg.trade_id, []).append(leg)
    reasons = {u.trade_id: _plain(u.reason) for u in res.unrecognised}
    warnings_by_row: Dict[int, List[str]] = {}
    for w in res.warnings:
        if w.row_no:
            warnings_by_row.setdefault(w.row_no, []).append(_plain(w.message))
    by_product: Dict[str, int] = {}
    new_ids, replace_ids = [], []
    for t in res.trades:
        row_no = res.trade_rows.get(t.trade_id, 0)
        r = rows.get(row_no)
        if r is None:           # never expected: a trade always comes from a row of the frame
            continue
        inst = res.instruments.get(t.instrument_id)
        name = _product_name(t.product)
        by_product[name] = by_product.get(name, 0) + 1
        on_file = "replaces" if t.trade_id in before else "new"
        (replace_ids if on_file == "replaces" else new_ids).append(t.trade_id)
        messages = ([reasons[t.trade_id]] if t.trade_id in reasons else []) + warnings_by_row.get(row_no, [])
        r.update({
            "trade_id": t.trade_id,
            "status": NOT_RECOGNISED if t.product == UNRECOGNISED else (WARNING if warnings_by_row.get(row_no) else OK),
            "product": name, "instrument_id": t.instrument_id,
            "bbg_ticker": inst.bbg_ticker if inst is not None else "",
            "expiry": _expiry(t, inst, legs_by_trade), "trade_date": t.trade_date,
            "side": "Buy" if t.quantity > 0 else "Sell" if t.quantity < 0 else r["side"],
            "quantity": t.quantity, "unit": _unit(t, inst),
            "price_in_file": t.broker_price or r["price_in_file"], "price_used": t.price,
            "price_factor": _price_factor(t, inst),
            "strategy": t.strategy, "pb_root": t.pb_root,
            "trade_type": _TRADE_TYPE_WORDS.get(t.trade_type, ""),
            "on_file": on_file, "message": "; ".join(messages)})
    for row_no, messages in warnings_by_row.items():    # a warning on a row that wrote no trade
        r = rows.get(row_no)
        if r is not None and r["status"] in (EXCLUDED, CANCELS, ""):
            r["message"] = "; ".join([m for m in [r["message"]] + messages if m])
    for r in rows.values():
        if not r["status"]:     # never expected: every kept row loads (hard rule 6)
            r["status"] = EXCLUDED
            r["message"] = "Not loaded: the parser wrote no trade for this row"

    loaded = len(res.trades)
    not_recognised = sum(1 for t in res.trades if t.product == UNRECOGNISED)
    prices_converted = sum(n for _name, _scale, n in res.price_scaled.values())
    remove_manual = sorted(manual - loaded_ids)
    removed = list(dict.fromkeys(removed))
    on_file_after = len(before) + len(new_ids) - len(removed) - len(remove_manual)
    excluded = superseded + n_status_excluded + n_filtered + sum(
        1 for r in rows.values() if r["message"] == "Not loaded: the parser wrote no trade for this row")
    counts = {"rows": len(df), "loaded": loaded, "by_product": by_product, "not_recognised": not_recognised,
              "warnings": len(res.warnings), "excluded": excluded, "superseded": superseded,
              "cancelled": n_cancelled, "prices_converted": prices_converted}
    merge = {"new": len(new_ids), "replace": len(replace_ids), "remove": len(removed),
             "remove_manual": len(remove_manual), "on_file_before": len(before), "on_file_after": on_file_after}

    file_notes = [_plain(w.message) for w in res.warnings if not w.row_no]
    file_notes.append("Dates read as day / month / year." if res.day_first else "Dates read as month / day / year.")
    # The parser's information notes name instruments by id; said here by row instead.
    if res.options_missing_strike:
        n = len(res.options_missing_strike)
        file_notes.append(f"{_plural(n, 'option has', 'options have')} no strike in the file: type "
                          f"{'it' if n == 1 else 'them'} on the Blotter tab's Options sub-tab.")
    if res.lme_prompt_notes:
        lme_rows = sorted({int(m.group(1)) for m in (_ROW_RE.match(t) for t in res.lme_prompt_notes) if m})
        n = len(res.lme_prompt_notes)
        where = (f" (row{'s' if len(lme_rows) > 1 else ''} {', '.join(str(x) for x in lme_rows)})"
                 if lme_rows else "")
        file_notes.append(f"{_plural(n, 'LME ticket carries', 'LME tickets carry')} no prompt date in the file"
                          f"{where}: the prompt was taken from the month or the 3-month date the ticket names.")
    file_notes += [_plain(n) for n in res.information_notes()
                   if "have no strike" not in n and "carry no prompt date" not in n]
    if res.underlying_only:
        n = len(res.underlying_only)
        file_notes.append(f"{_plural(n, 'underlying future', 'underlying futures')} of the options would be "
                          "written with no trade of its own, for the options' Greeks.")
    if remove_manual:
        file_notes.append(f"{_plural(len(remove_manual), 'manual entry', 'manual entries')} on file would be "
                          "removed (manual booking left the app).")

    return {"ok": True, "error": "", "filename": filename,
            "summary": _summary(counts, merge), "counts": counts, "merge": merge,
            "file_notes": file_notes, "rows": [rows[k] for k in sorted(rows)]}


def _summary(counts: dict, merge: dict) -> str:
    """'89 rows: 89 would load (75 futures, 14 LME forwards), 12 prices converted from the broker's
    units, 0 not recognised, 1 warning; against the book on file: 12 new, 77 replace, 0 removed as
    cancelled.'"""
    kinds = []
    for single, many in PRODUCT_NAMES.values():
        n = counts["by_product"].get(single, 0)
        if n and single != PRODUCT_NAMES[UNRECOGNISED][0]:
            one = single if single[:2].isupper() else single[:1].lower() + single[1:]
            kinds.append(f"{n} {one if n == 1 else many}")
    head = f"{_plural(counts['rows'], 'row', 'rows')}: {counts['loaded']} would load"
    if kinds:
        head += f" ({', '.join(kinds)})"
    parts = [head,
             f"{_plural(counts['prices_converted'], 'price', 'prices')} converted from the broker's units",
             f"{counts['not_recognised']} not recognised",
             _plural(counts["warnings"], "warning", "warnings")]
    if counts["excluded"]:
        parts.append(f"{counts['excluded']} not loaded")
    if counts["cancelled"]:
        parts.append(f"{counts['cancelled']} cancelled")
    tail = (f"against the book on file: {merge['new']} new, {merge['replace']} replace, "
            f"{merge['remove']} removed as cancelled")
    if merge["remove_manual"]:
        tail += f", {merge['remove_manual']} manual removed"
    return ", ".join(parts) + "; " + tail + "."


__all__ = ["check_file", "PRODUCT_NAMES", "OK", "WARNING", "NOT_RECOGNISED", "EXCLUDED", "CANCELS"]
