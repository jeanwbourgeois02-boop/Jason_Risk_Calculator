"""Trade blotter CSV/Excel upload, staged separately from the live database.

Blotter-only (user decision 2026-09-17): this is the app's one and only upload input.
File reading, column canonicalisation and every per-row tolerance live in
``data/ingest/blotter.py`` (``read_table`` / ``parse`` / ``load``); this module adds the
size guard, the pre-Confirm shape check, the stage-then-publish transaction and the
summary message.

An upload is a merge by Trade Id (user decision 2026-09-28, "yeah that makes sense",
replacing the full replace of 2026-09-17): the broker's export is cumulative and corrects
fills in place, so

- a Trade Id already on file is REPLACED by the file's row: its ``trades`` row is
  overwritten, its ``trade_legs`` are deleted and re-inserted from the file (a leg count
  can change), and its ``realised_pnl`` row is dropped so the ledger freezes it again
  from the new fill; its bundle membership (``instrument_theme``, and a ``trades.theme``
  set by hand where the file carries none) is untouched;
- a Trade Id the file does not know is ADDED;
- a file row whose Status says cancelled / void / deleted / rejected / failed REMOVES that
  Trade Id from every trade-keyed table when it is on file (the parser lists them as
  ``ParseResult.cancelled_trade_ids``; read with ``getattr`` so this module works before
  and after that field lands);
- a trade on file that the file does not name stays as it is.

An old database's ``source = 'MANUAL'`` rows (the manual booking path left on 2026-09-28)
are still removed by every upload: the export is the one way a trade enters the app
(hard rule 1), and nothing can carry such a row forward. ``blotter.load`` itself keeps
its idempotent-by-``trade_id`` upsert, which is what the merge is built on; the
trade-keyed tables the removals and replacements touch are ``TRADE_KEYED_TABLES``.
Instruments, marks, curves, curve_quotes and index_fixings are untouched -- keyed by
instrument/date, not by trade (so the underlying future the parser writes, with no trade,
for an option on a future it does not trade itself stays on file across uploads, and the
parser's INSERT OR IGNORE never overwrites it). Every delete and write happens only after
the new file has parsed successfully, inside the one transaction that publishes its rows,
so a parse failure leaves the existing book completely intact; see ``_stage_and_publish``.
The one exception to "marks are untouched" (2026-09-24): once the book is published,
Bloomberg's stored contract dates are put back onto the commodity futures
(``data/ingest/contract_dates.py``), which moves a future's expiry, its NOTIONAL legs and
the key of its FUTURE_PX marks, never a value.

Retired 2026-09-24 (commodity conversion Phase 2, user yes): the FX-swap package rule
(``swaps.py``) no longer runs after an upload, so two blotter forwards stay two outright
forwards; and the upload no longer turns an interest rate swap's priced history round
(``irs_direction.py``), since rates left the app. The by-hand FX swap
(``manual.book_fx_swap``) left with the manual booking path on 2026-09-28.

Every row loads (hard rule 6, user decision 2026-09-29): a row the parser cannot identify is
written like any other trade, as product UNRECOGNISED (its 'UNRECOGNISED:<symbol>' instrument,
no leg, its reason in ``upload_issues``), and ``reresolve_unrecognised`` rewrites it in place as
the real product once the contract list knows it: at the end of every upload and at start-up.

``import_blotter_report`` returns the outcome as data (message, rejects, warnings,
notes) so the UI decides from counts, not from prose; ``import_blotter`` is its message.
"""
from __future__ import annotations

from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path
import base64
import json
import sqlite3

import pandas as pd

from data.ingest import blotter, schema

MAX_BYTES = 25 * 1024 * 1024
# The words the rejects sentence always carries. ui/uploads.py used to decide from this
# phrase whether the result box may dismiss itself (its REJECTS_PHRASE, pinned by a test
# on each side); `import_blotter_report` now gives it the counts instead, but the phrase
# stays so the sentence and every caller reading it keep working.
REJECTS_PHRASE = "could not be read"

# What a sheet must carry to be treated as a blotter, matched on the canonical names
# blotter.read_table produces. 'Fin Type' may be absent when 'Product' is present.
BLOTTER_REQUIRED = {"Symbol", "Trade Id"}
BLOTTER_KIND_COLUMNS = {"Fin Type", "Product"}

# Tables keyed by trade_id (data/ingest/schema.py -- grepped for every "REFERENCES
# trades" / "trade_id ... PRIMARY KEY"): trade_legs and realised_pnl reference trades and
# must be cleared before trades itself (FK-safe child-then-parent order) when a trade is
# removed. realised_pnl is not in schema.TABLES (the generic per-table merge loop below
# never touches it), so it is deleted explicitly rather than through that loop. Nothing
# outside data/ingest/schema.py keys a table off trade_id: engine/options' tables are
# keyed by instrument_id, not trade_id (checked 2026-09-17), so they are untouched by a
# trade replace. This set is what a removal (a cancelled Trade Id, an old MANUAL row)
# is deleted from, and the children are what a replaced Trade Id has rewritten.
TRADE_KEYED_CHILD_TABLES = ("trade_legs", "realised_pnl")
TRADE_KEYED_TABLES = TRADE_KEYED_CHILD_TABLES + ("trades",)
FULL_REPLACE_CHILD_TABLES = TRADE_KEYED_CHILD_TABLES   # the names before 2026-09-28's merge rule
FULL_REPLACE_TABLES = TRADE_KEYED_TABLES
# Retired tables that still reference trades on a database made before 2026-09-24
# (swap_review: the retired FX-swap package rule's ambiguous candidates). A removed trade
# is deleted from it when the table is there, so its foreign key never blocks the delete;
# nothing is written to it any more, and a database without it is fine.
RETIRED_CHILD_TABLES = ("swap_review",)
# Rows every upload still removes even though the file does not name them: an old
# database's manual entries (the manual booking path left on 2026-09-28; hard rule 1).
_MANUAL_TRADES_SQL = "SELECT trade_id FROM trades WHERE source = 'MANUAL'"

# Every row loads (hard rule 6, user decision 2026-09-29: "all rows need to load thats non
# negotiable"). A row the parser cannot identify is a trade on file all the same: product
# UNRECOGNISED, its instrument 'UNRECOGNISED:<symbol as written>' (asset_class UNRECOGNISED,
# multiplier 0, never marked), no leg, never asked of Bloomberg, blank P&L with its reason. The
# reason sits in `upload_issues` under kind NEED_FIX_KIND with the trade's id and symbol; a later
# upload that resolves the row replaces it by Trade Id, and `reresolve_unrecognised` rewrites it in
# place once the contract list knows it. Nothing is ever guessed.
UNRECOGNISED = "UNRECOGNISED"
UNRECOGNISED_PREFIX = "UNRECOGNISED:"
NEED_FIX_KIND = "UNRECOGNISED"          # upload_issues.kind of a row that is on file but needs a fix
_INSTRUMENT_COLUMNS = ("instrument_id", "asset_class", "base_ccy", "quote_ccy", "multiplier", "is_ndf",
                       "bbg_ticker", "expiry_date")
_OPTION_COLUMNS = ("instrument_id", "strike", "option_type", "barrier_level", "avg_start_date", "payoff")
_LEG_COLUMNS = ("trade_id", "leg_no", "leg_type", "ccy", "amount", "start_date", "settle_date", "rate",
                "settles_cash")


def _get(item, name, default=None):
    """`item.name` or `item[name]`, `default` when it has neither (a dataclass, an object or a dict)."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def unrecognised_instrument_id(symbol, description="") -> str:
    """'UNRECOGNISED:' + the Symbol cell as written, upper-cased and stripped
    ('UNRECOGNISED:XYZ6-USAA'); with no symbol, the Description instead."""
    text = " ".join(str(symbol or "").split()) or " ".join(str(description or "").split())
    return UNRECOGNISED_PREFIX + text.upper()


def _file_trades(result) -> list:
    """Every trade the file loaded, UNRECOGNISED ones included: `result.trades`, plus any
    UNRECOGNISED trade the parser lists only on `result.unrecognised` (one entry per trade id)."""
    trades = list(getattr(result, "trades", None) or ())
    seen = {t.trade_id for t in trades}
    for item in getattr(result, "unrecognised", None) or ():
        trade = _get(item, "trade")
        if trade is not None and trade.trade_id not in seen:
            trades.append(trade)
            seen.add(trade.trade_id)
    return trades


def unrecognised_rows(result) -> list[dict]:
    """The file's rows that loaded as UNRECOGNISED, one dict each, in row order:
    {trade_id, row_no, symbol, reason}. Read from `result.unrecognised` (items with trade_id,
    row_no, symbol, reason, as attributes or keys; optionally `trade` / `instrument`) and from
    every UNRECOGNISED trade in `result.trades`; a trade with no reason given takes the parser's
    reject of the same row, else a sentence naming its symbol (never a blank reason)."""
    trade_rows = getattr(result, "trade_rows", None) or {}
    rejects_by_row = {rj.row_no: rj.reason for rj in getattr(result, "rejects", None) or ()}
    out: dict = {}
    for item in getattr(result, "unrecognised", None) or ():
        if isinstance(item, tuple):
            item = dict(zip(("row_no", "symbol", "trade_id", "reason") if len(item) == 4
                            else ("row_no", "symbol", "reason"), item))
        trade = _get(item, "trade")
        tid = str(_get(item, "trade_id") or (trade.trade_id if trade is not None else "") or "")
        if not tid:
            continue
        out[tid] = {"trade_id": tid, "row_no": int(_get(item, "row_no") or trade_rows.get(tid, 0) or 0),
                    "symbol": str(_get(item, "symbol") or (getattr(trade, "broker_symbol", "") if trade else "") or ""),
                    "reason": str(_get(item, "reason") or "")}
    for t in _file_trades(result):
        if t.product != UNRECOGNISED:
            continue
        row = out.setdefault(t.trade_id, {"trade_id": t.trade_id, "row_no": int(trade_rows.get(t.trade_id, 0) or 0),
                                          "symbol": "", "reason": ""})
        # The Symbol cell as written (the trade's broker_symbol) wins: pnl-valuation matches on it.
        row["symbol"] = (str(t.broker_symbol or "").strip() or row["symbol"]
                         or t.instrument_id[len(UNRECOGNISED_PREFIX):])
    for row in out.values():
        if not row["reason"]:
            row["reason"] = rejects_by_row.get(row["row_no"]) or (
                f"contract not recognised: symbol {row['symbol'] or '(blank)'} is not in the contract list")
    return sorted(out.values(), key=lambda r: (r["row_no"], r["trade_id"]))


def _upsert_trade(conn: sqlite3.Connection, trade) -> None:
    """Upsert one `common.Trade` into `trades` by name, only the fields `trades` has as columns (a
    Trade field the database lacks, such as `fin_type` before its column exists, is left out)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    fields = {k: v for k, v in vars(trade).items() if k in have}
    cols = list(fields)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "trade_id")
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) "
                 f"ON CONFLICT(trade_id) DO UPDATE SET {updates}", list(fields.values()))


def _write_unrecognised(conn: sqlite3.Connection, result) -> None:
    """Make sure every UNRECOGNISED trade of the file is written on `conn` as the shared definition
    says, whether or not `blotter.load` wrote it: its instrument row (named columns; a row the parser
    wrote is kept as it is), its trade row (upsert by name), and no leg. Idempotent."""
    trades = [t for t in _file_trades(result) if t.product == UNRECOGNISED]
    if not trades:
        return
    instruments = dict(getattr(result, "instruments", None) or {})
    for item in getattr(result, "unrecognised", None) or ():
        inst = _get(item, "instrument") if not isinstance(item, tuple) else None
        if inst is not None:
            instruments.setdefault(inst.instrument_id, inst)
    names = ",".join(_INSTRUMENT_COLUMNS)
    with conn:
        for t in trades:
            inst = instruments.get(t.instrument_id)
            values = ([getattr(inst, c) for c in _INSTRUMENT_COLUMNS] if inst is not None else
                      [t.instrument_id, UNRECOGNISED, "", "", 0.0, 0, "", "9999-12-31"])
            conn.execute(f"INSERT OR IGNORE INTO instruments ({names}) VALUES ({','.join('?' * len(values))})", values)
            _upsert_trade(conn, t)
            conn.execute("DELETE FROM trade_legs WHERE trade_id = ?", (t.trade_id,))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _delete_trade_rows(conn: sqlite3.Connection, trade_ids, tables=TRADE_KEYED_TABLES) -> None:
    """Delete `trade_ids` from `tables` (children first, as the tuple is ordered) and, when
    `tables` includes ``trades``, from the retired child tables that still exist. Nothing
    happens for an empty list. Works inside an open transaction on `conn`."""
    ids = list(dict.fromkeys(trade_ids))
    if not ids:
        return
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _gone (trade_id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _gone")
    conn.executemany("INSERT OR IGNORE INTO _gone VALUES (?)", [(t,) for t in ids])
    if "trades" in tables:
        for table in RETIRED_CHILD_TABLES:
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE trade_id IN (SELECT trade_id FROM _gone)")
    for table in tables:
        conn.execute(f"DELETE FROM {table} WHERE trade_id IN (SELECT trade_id FROM _gone)")
    conn.execute("DROP TABLE _gone")


def preview_frame(payload: bytes, filename: str) -> pd.DataFrame:
    """Read just far enough to validate shape before Confirm; writes nothing."""
    return blotter.read_table(payload, filename)


def validate_blotter_shape(frame: pd.DataFrame) -> None:
    """Raise a clean ValueError if `frame` doesn't look like a trade blotter."""
    columns = set(blotter.canonicalize_columns(frame.copy()).columns)
    missing = sorted(BLOTTER_REQUIRED - columns)
    if not columns & BLOTTER_KIND_COLUMNS:
        missing.append("Fin Type (or Product)")
    if missing:
        raise ValueError("This file is not a trade blotter. Missing columns: " + ", ".join(missing))


def decode(contents):
    if not contents or len(contents) > MAX_BYTES * 4 // 3 + 1024:
        raise ValueError("Choose a file smaller than 25 MB.")
    payload = base64.b64decode(contents.split(",", 1)[1], validate=True)
    if len(payload) > MAX_BYTES:
        raise ValueError("Choose a file smaller than 25 MB.")
    return payload


def _stage_and_publish(db_path, load_fn):
    """Hold a writer lock on `db_path` while staging against a snapshot of it in an
    in-memory DB, call `load_fn(staged_conn)`, then publish staged's rows into the live
    DB in one transaction. `load_fn` raises `ValueError` (or lets a `sqlite3.Error`
    propagate) on any failure before the publish step runs, and before anything is
    written to `live` -- so a parse/load failure leaves `live` untouched.

    The publish is a merge by Trade Id (module docstring). Every table in `schema.TABLES`
    is an upsert (INSERT ... ON CONFLICT DO UPDATE) of staged's rows, so a row the file
    does not name is left alone; on top of that, before the upsert runs,

    - a Trade Id the file loaded that was already on file (REPLACED) has its `trade_legs`
      and `realised_pnl` rows deleted from `live`, so the upsert re-inserts exactly the
      file's legs (a leg count can shrink) and the ledger freezes the trade again;
      a `trades.theme` set by hand is kept where the file's row carries none; every other
      `trades` column is the file's (`broker_symbol` / `broker_price`, the Symbol and
      Price cells as written, 2026-09-29, included: `blotter.load` inserts every `Trade`
      field by name and this upsert copies every column by name, so a column added to
      `trades` and `common.Trade` needs no change here);
    - a Trade Id the file cancels (`ParseResult.cancelled_trade_ids`, read with getattr)
      that is on file and not also loaded, and every `source = 'MANUAL'` trade the file
      does not name, is deleted from every trade-keyed table (REMOVED), on both
      connections, so the upsert never copies it back.

    Returns `(result, change)`, `change` the merge's accounting: `added`, `replaced`,
    `removed` (cancelled), `removed_manual`, `on_file_before`, `on_file_after`, and the
    ids behind each count (`added_ids`, `replaced_ids`, `removed_ids`, `removed_manual_ids`).
    """
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(schema.connect(db_path)) as live:
        live.execute("BEGIN IMMEDIATE")
        try:
            with closing(sqlite3.connect(":memory:")) as staged:
                # A whole-database backup: staged carries contract_static too, so the parse
                # sees Bloomberg's stored contract dates (and apply_contract_dates re-applies them after publish).
                with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as reader:
                    reader.backup(staged)
                # staged is a snapshot of live at this instant: the book before the file.
                before = {r[0] for r in staged.execute("SELECT trade_id FROM trades")}
                manual = {r[0] for r in staged.execute(_MANUAL_TRADES_SQL)}
                result = load_fn(staged)
                incoming = list(dict.fromkeys(t.trade_id for t in _file_trades(result)))
                incoming_set = set(incoming)
                cancelled = [str(t) for t in (getattr(result, "cancelled_trade_ids", None) or ())]
                removed_ids = [t for t in dict.fromkeys(cancelled) if t in before and t not in incoming_set]
                removed_manual_ids = sorted(manual - incoming_set)
                replaced_ids = [t for t in incoming if t in before]
                added_ids = [t for t in incoming if t not in before]
                gone = removed_ids + removed_manual_ids
                # A hand-set theme on a replaced trade (the file's row carries ''): kept.
                replaced_set = set(replaced_ids)
                kept_themes = [(theme, tid) for tid, theme in live.execute(
                    "SELECT trade_id, theme FROM trades WHERE theme != ''") if tid in replaced_set]
                _delete_trade_rows(staged, gone)
                _delete_trade_rows(live, gone)
                _delete_trade_rows(live, replaced_ids, TRADE_KEYED_CHILD_TABLES)
                for table in schema.TABLES:
                    # Quoted throughout: curve_quotes.index is a reserved word.
                    columns = [r[1] for r in staged.execute(f"PRAGMA table_info({table})")]
                    quoted = [f'"{c}"' for c in columns]
                    names = ",".join(quoted)
                    updates = ",".join(f"{q}=excluded.{q}" for q in quoted)
                    placeholders = ",".join("?" for _ in columns)
                    live.executemany(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders}) ON CONFLICT DO UPDATE SET {updates}",
                        staged.execute(f"SELECT {names} FROM {table}"))
                live.executemany("UPDATE trades SET theme = ? WHERE trade_id = ? AND theme = ''", kept_themes)
                on_file_after = live.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
                live.commit()
        except Exception:
            live.rollback()
            raise
    change = {"added": len(added_ids), "replaced": len(replaced_ids), "removed": len(removed_ids),
              "removed_manual": len(removed_manual_ids), "on_file_before": len(before),
              "on_file_after": int(on_file_after), "added_ids": added_ids, "replaced_ids": replaced_ids,
              "removed_ids": removed_ids, "removed_manual_ids": removed_manual_ids}
    return result, change


# The upload summary's breakdown, counted over the trades the file LOADED (ParseResult.trades),
# never over the parser's per-kind row counters (n_future etc.), which also count rows that
# were rejected: "31 futures" when 29 loaded (ui-shell, 2026-09-24). Plain labels, never a
# product code (Phase 5, 2026-09-24: "3 LME_FWD" became "3 LME forwards"): Jason's commodity
# book first, then the FX hedges. (label, products, always shown even at zero)
_LOADED_KINDS = (("futures", ("FUTURE",), True),
                 ("options on futures", ("CMDTY_OPTION",), True),
                 ("LME forwards", ("LME_FWD",), True),
                 ("listed options", ("EQ_OPTION",), False),
                 ("FX forwards", ("FX_FWD",), True),
                 ("FX spot", ("FX_SPOT",), True),
                 ("FX swaps", ("FX_SWAP",), False),
                 ("FX options", ("FX_OPTION",), True),
                 # every row loads (2026-09-29): a row the parser could not identify is on file too
                 ("needing a fix (contract not recognised)", (UNRECOGNISED,), False))


def loaded_counts(trades) -> dict:
    """The loaded trades by product code: {'FUTURE': 29, 'CMDTY_OPTION': 4, ...}."""
    counts: dict = {}
    for t in trades:
        counts[t.product] = counts.get(t.product, 0) + 1
    return counts


def loaded_breakdown(trades) -> str:
    """'29 futures, 4 options on futures, 3 LME forwards, 8 FX forwards, 1 FX spot, 5 FX options':
    the loaded trades by kind. Listed options (EQ_OPTION) and FX swaps are named only when there
    are some, every other kind always; a product this list does not know is named as it is
    stored, so no loaded trade goes uncounted."""
    counts = loaded_counts(trades)
    parts, known = [], set()
    for label, products, always in _LOADED_KINDS:
        known.update(products)
        n = sum(counts.get(p, 0) for p in products)
        if n or always:
            parts.append(f"{n} {label}")
    parts += [f"{n} {product}" for product, n in sorted(counts.items()) if product not in known]
    return ", ".join(parts)


def merge_sentence(n_in_file: int, change: dict) -> str:
    """The merge by Trade Id in plain words: '89 trades in the file: 12 added, 77 already on
    file (replaced by the file's rows), 0 removed as cancelled; 89 trades on file.' An old
    database's manual entries removed are named in a second clause; nothing else is. The trades
    on file that need a fix (product UNRECOGNISED, `change['need_fix_on_file']`, every row loads,
    2026-09-29) are counted among the trades on file: '...; 91 trades on file, 2 of them need a fix.'"""
    text = (f"{n_in_file} trades in the file: {change.get('added', 0)} added, "
            f"{change.get('replaced', 0)} already on file (replaced by the file's rows), "
            f"{change.get('removed', 0)} removed as cancelled")
    if change.get("removed_manual"):
        text += f", {change['removed_manual']} manual entr{'y' if change['removed_manual'] == 1 else 'ies'} removed"
    fix = int(change.get("need_fix_on_file", 0) or 0)
    tail = f", {fix} of them need{'s' if fix == 1 else ''} a fix" if fix else ""
    return f"{text}; {change.get('on_file_after', 0)} trades on file{tail}."


def need_fix_sentence(rows) -> str:
    """'2 rows need a fix (on file as trades, P&L blank until the contract is mapped): row 33 ZCZ6:
    <reason>; row 34 QQZ6-USAA: <reason>.' from `unrecognised_rows`; '' when there are none."""
    rows = list(rows or ())
    if not rows:
        return ""
    head = "; ".join(f"row {r['row_no']} {r['symbol']}: {r['reason']}" for r in rows[:5])
    more = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    n = len(rows)
    return (f"{n} row{'s' if n != 1 else ''} need{'s' if n == 1 else ''} a fix (on file as trades, P&L blank "
            f"until the contract is mapped): {head}{more}.")


def _library_update(db_path) -> tuple:
    """Bring the Bloomberg library (data/bloomberg/library.py: what the trades on file need
    from Bloomberg for their P&L) up to date with the book just published, and say what
    changed. An upload asks nothing of Bloomberg (user decision 2026-09-21); the next
    "Pull Bloomberg now" asks for exactly what is listed there. Returns (sentence, tickers);
    tickers is 0 when the sync could not run here."""
    try:
        from data.bloomberg import library
        with closing(schema.connect(Path(db_path).resolve())) as conn:
            changed = library.sync(conn)
            tickers = library.summary(conn)["tickers"]
    except Exception as exc:  # noqa: BLE001 -- the book is already published; a reader syncs the library itself
        return f"Bloomberg library not updated here ({exc}); it updates itself on the next pull.", 0
    return (f"Bloomberg library: {tickers} ticker(s) needed today, {changed['added']} item(s) added, "
            f"{changed['removed']} removed. Nothing was pulled; press Pull Bloomberg now to price the book."), tickers


def _library_sentence(db_path) -> str:
    return _library_update(db_path)[0]


def _contract_dates_sentence(db_path) -> str:
    """Put Bloomberg's stored contract dates back onto the commodity futures just published
    (``contract_dates.apply_contract_dates``): the parser books a contract month at its
    estimated last trade date, so without this a re-upload would undo the dates the last
    pull stored. Asks Bloomberg nothing. '' when nothing changed; never fails an upload
    (the book is already published, and the next pull applies the dates again)."""
    try:
        from data.ingest import contract_dates
        with closing(schema.connect(Path(db_path).resolve())) as conn:
            return contract_dates.summary_sentence(contract_dates.apply_contract_dates(conn))
    except Exception as exc:  # noqa: BLE001 -- the book is already published
        return f"Contract dates not applied here ({exc}); the next Bloomberg pull applies them."


def import_blotter(payload, filename, db_path):
    """Load a blotter file into `db_path`, merging it into the book by Trade Id (module
    docstring, user decision 2026-09-28): a Trade Id already on file is replaced by the
    file's row, a new one is added, a cancelled one is removed, and a trade the file does
    not name stays; an old database's MANUAL rows are removed. Every row loads (hard rule 6,
    2026-09-29): a row the parser cannot identify is on file as product UNRECOGNISED and named in
    an "N rows need a fix" sentence; a row an older parser still rejects is listed in a sentence
    that carries the words "could not be read" (REJECTS_PHRASE). Only a file with no
    recognisable blotter header at all is refused -- and refusing it never touches the
    existing book (every write happens only after this file has parsed).

    Returns the one-paragraph message, exactly `import_blotter_report(...)["message"]`.
    A caller that needs to know whether anything was skipped or doubtful should call
    `import_blotter_report` and read its counts rather than this prose."""
    return import_blotter_report(payload, filename, db_path)["message"]


UPLOAD_ISSUES_DDL = ("CREATE TABLE IF NOT EXISTS upload_issues (row_no INTEGER NOT NULL, symbol TEXT NOT NULL, "
                     "kind TEXT NOT NULL, reason TEXT NOT NULL, filename TEXT NOT NULL, uploaded_at TEXT NOT NULL, "
                     "trade_id TEXT NOT NULL DEFAULT '')")
UPLOAD_ISSUES_COLUMNS = ("row_no", "symbol", "kind", "reason", "filename", "uploaded_at", "trade_id")


def _migrate_issues_table(conn: sqlite3.Connection) -> None:
    """Create `upload_issues`, and add `trade_id` (2026-09-29) to an older database's table
    (`schema._migrate_columns` does not reach this lane's own tables)."""
    conn.execute(UPLOAD_ISSUES_DDL)
    present = {r[1] for r in conn.execute("PRAGMA table_info(upload_issues)")}
    if "trade_id" not in present:
        conn.execute("ALTER TABLE upload_issues ADD COLUMN trade_id TEXT NOT NULL DEFAULT ''")


def _insert_issues(conn: sqlite3.Connection, rows) -> None:
    """`rows`: tuples in `UPLOAD_ISSUES_COLUMNS` order. Named columns, inside the caller's transaction."""
    conn.executemany(f"INSERT INTO upload_issues ({','.join(UPLOAD_ISSUES_COLUMNS)}) "
                     f"VALUES ({','.join('?' for _ in UPLOAD_ISSUES_COLUMNS)})", list(rows))


def record_upload_issues(db_path, filename, result) -> int:
    """The last upload's rows that need the user, with why, replacing the previous upload's
    (user, 2026-09-21: "a zar option and spx option that just isnt in the table"):

    - kind UNRECOGNISED (NEED_FIX_KIND, every row loads, 2026-09-29): a row that is on file as a
      trade of product UNRECOGNISED, its `trade_id` and the Symbol cell as written, and the reason
      in plain words (pnl-valuation shows it on the trade's row);
    - kind WARNING: the parser's warnings, a contradiction between two populated fields that loaded
      on the primary field included, with the row's `trade_id` (a file-level warning: row 0,
      symbol '' and trade_id '');
    - kind REJECTED / NOT LOADED: a row the parser still did not make a trade (none under hard
      rule 6 once the parser is on it; kept so an older parser's output is never lost).

    An UNRECOGNISED row of an EARLIER upload whose trade is still UNRECOGNISED on file and that
    this file does not name is kept (pnl-valuation's reason for it; `reresolve_unrecognised` keeps
    that set in step with the trades on file). Never fails an import: a database error here is
    swallowed and 0 returned; else the number of rows written for this file."""
    import datetime as _dt
    need_fix = unrecognised_rows(result)
    fix_rows = {r["row_no"] for r in need_fix if r["row_no"]}
    trade_rows = getattr(result, "trade_rows", None) or {}
    trade_by_row: dict = {}
    for tid, n in trade_rows.items():
        trade_by_row.setdefault(n, tid)
    rows = [(r["row_no"], r["symbol"], NEED_FIX_KIND, r["reason"], r["trade_id"]) for r in need_fix]
    rows += [(rj.row_no, rj.symbol or "", "REJECTED", rj.reason, "") for rj in getattr(result, "rejects", None) or ()
             if rj.row_no not in fix_rows]
    rows += [(n, sym or "", "NOT LOADED", why, "") for n, sym, why in getattr(result, "skipped_other_rows", None) or ()]
    for w in getattr(result, "warnings", None) or ():
        n = int(getattr(w, "row_no", 0) or 0)
        tid = str(getattr(w, "trade_id", "") or (trade_by_row.get(n, "") if n else ""))
        rows.append((n, (getattr(w, "symbol", "") or "") if n else "", "WARNING", w.message, tid))
    stamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    file_ids = {t.trade_id for t in _file_trades(result)}
    try:
        conn = sqlite3.connect(str(db_path), timeout=60)
        try:
            with conn:
                _migrate_issues_table(conn)
                carried = [r for r in conn.execute(
                    f"SELECT {','.join(UPLOAD_ISSUES_COLUMNS)} FROM upload_issues WHERE kind = ? AND trade_id != ''",
                    (NEED_FIX_KIND,)) if r[-1] not in file_ids]
                still = {r[0] for r in conn.execute("SELECT trade_id FROM trades WHERE product = ?", (UNRECOGNISED,))}
                conn.execute("DELETE FROM upload_issues")
                _insert_issues(conn, [r for r in carried if r[-1] in still])
                _insert_issues(conn, [(n, sym, kind, why, str(filename), stamp, tid) for n, sym, kind, why, tid in rows])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0
    return len(rows)


# The last upload's load summary as data (UI redesign, 2026-09-28): one row, replaced by each
# upload, written alongside upload_issues once the book is published and the summary is known.
# Column order is the INSERT's and `last_upload_report`'s.
UPLOAD_REPORT_DDL = ("CREATE TABLE IF NOT EXISTS upload_report ("
                     "filename TEXT NOT NULL, uploaded_at TEXT NOT NULL, "
                     "futures INTEGER NOT NULL DEFAULT 0, options_on_futures INTEGER NOT NULL DEFAULT 0, "
                     "lme_forwards INTEGER NOT NULL DEFAULT 0, fx_forwards INTEGER NOT NULL DEFAULT 0, "
                     "fx_spot INTEGER NOT NULL DEFAULT 0, fx_options INTEGER NOT NULL DEFAULT 0, "
                     "excluded_rows INTEGER NOT NULL DEFAULT 0, excluded_text TEXT NOT NULL DEFAULT '', "
                     "underlying_futures_written INTEGER NOT NULL DEFAULT 0, "
                     "library_tickers INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '', "
                     "added INTEGER NOT NULL DEFAULT 0, replaced INTEGER NOT NULL DEFAULT 0, "
                     "removed INTEGER NOT NULL DEFAULT 0, on_file_after INTEGER NOT NULL DEFAULT 0, "
                     "need_fix INTEGER NOT NULL DEFAULT 0)")
UPLOAD_REPORT_COLUMNS = ("filename", "uploaded_at", "futures", "options_on_futures", "lme_forwards",
                         "fx_forwards", "fx_spot", "fx_options", "excluded_rows", "excluded_text",
                         "underlying_futures_written", "library_tickers", "summary",
                         # the merge by Trade Id (2026-09-28): trades added, replaced by the file's
                         # rows, removed as cancelled, and the whole book's count after the upload
                         "added", "replaced", "removed", "on_file_after",
                         # every row loads (2026-09-29): the file's rows on file as UNRECOGNISED
                         # after the upload's re-resolution (the "N rows need a fix" count)
                         "need_fix")
# The default of every column an older `upload_report` may lack (a database made before the
# column was added): `record_upload_report` adds the column, `last_upload_report` fills it.
_REPORT_DEFAULTS = {"excluded_text": "", "summary": ""}
_REPORT_KIND_COLUMNS = (("futures", "FUTURE"), ("options_on_futures", "CMDTY_OPTION"), ("lme_forwards", "LME_FWD"),
                        ("fx_forwards", "FX_FWD"), ("fx_spot", "FX_SPOT"), ("fx_options", "FX_OPTION"))


def _report_columns_on_file(conn: sqlite3.Connection) -> list:
    return [r[1] for r in conn.execute("PRAGMA table_info(upload_report)")]


def _migrate_report_table(conn: sqlite3.Connection) -> None:
    """Create `upload_report`, then add any column of `UPLOAD_REPORT_COLUMNS` an older
    database's table lacks, with its default, so the INSERT below always fits."""
    conn.execute(UPLOAD_REPORT_DDL)
    present = set(_report_columns_on_file(conn))
    for col in UPLOAD_REPORT_COLUMNS:
        if col not in present:
            default = "''" if col in _REPORT_DEFAULTS else "0"
            kind = "TEXT" if col in _REPORT_DEFAULTS else "INTEGER"
            conn.execute(f"ALTER TABLE upload_report ADD COLUMN {col} {kind} NOT NULL DEFAULT {default}")


def record_upload_report(db_path, filename, result, n_underlying: int, library_tickers: int, summary: str,
                         change: dict | None = None, file_rows: list | None = None) -> bool:
    """Persist the load summary of the upload just published (`upload_report`, one row replacing
    the previous one). The kind counts are `loaded_breakdown`'s (the trades that LOADED, never
    the parser's row counters); `excluded_rows` is the book filter's total (status, fund, trader,
    desk) with `filter_summary()` as its wording; `change` is `_stage_and_publish`'s merge
    accounting (`added`, `replaced`, `removed`, `on_file_after`; zeros when not given). The same
    row, with the merge's trade ids, is appended to `upload_history` in the same transaction, and
    `file_rows` (``file_rows_outcome``; None leaves the table as it is) replaces `upload_not_loaded`.
    Never fails an import: a database error is swallowed and False returned."""
    import datetime as _dt
    counts = loaded_counts(_file_trades(result))
    change = change or {}
    row = {"filename": str(filename),
           "uploaded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           **{col: counts.get(product, 0) for col, product in _REPORT_KIND_COLUMNS},
           "excluded_rows": int(getattr(result, "n_skipped_status_or_fund", 0) or 0),
           "excluded_text": result.filter_summary(),
           "underlying_futures_written": int(n_underlying),
           "library_tickers": int(library_tickers),
           "summary": summary,
           **{col: int(change.get(col, 0) or 0) for col in ("added", "replaced", "removed", "on_file_after",
                                                             "need_fix")}}
    names = ",".join(UPLOAD_REPORT_COLUMNS)
    try:
        conn = sqlite3.connect(str(db_path), timeout=60)
        try:
            with conn:
                _migrate_report_table(conn)
                conn.execute("DELETE FROM upload_report")
                conn.execute(f"INSERT INTO upload_report ({names}) VALUES ({','.join('?' for _ in UPLOAD_REPORT_COLUMNS)})",
                             [row[c] for c in UPLOAD_REPORT_COLUMNS])
                # The same upload appended to the history, in the same transaction (2026-09-29).
                _append_history(conn, row, change)
                if file_rows is not None:       # the rows that loaded no trade, and why (2026-09-30)
                    _record_not_loaded(conn, file_rows, str(filename), row["uploaded_at"])
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return True


def last_upload_report(conn: sqlite3.Connection) -> dict | None:
    """The last upload's summary as a dict keyed by `UPLOAD_REPORT_COLUMNS`, or None when no
    upload has been recorded (no table, or an empty one). A column the database's table does
    not have yet (a row written before 2026-09-28's `added` / `replaced` / `removed` /
    `on_file_after`) is returned at its default, so the reader never needs the migration,
    which `record_upload_report` runs on the next upload."""
    if not _table_exists(conn, "upload_report"):
        return None
    present = [c for c in UPLOAD_REPORT_COLUMNS if c in set(_report_columns_on_file(conn))]
    row = conn.execute(f"SELECT {','.join(present)} FROM upload_report ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    out = {col: _REPORT_DEFAULTS.get(col, 0) for col in UPLOAD_REPORT_COLUMNS}
    out.update(zip(present, row))
    return out


# Every successful upload, one row each (user, 2026-09-29: the Blotter tab is the audit trail
# of the uploaded file, so a missing trade can be traced to the file it came from). Written
# with `upload_report`, in the same transaction, after a successful publish; never back-filled
# from `upload_report` (an old database starts its history at its next upload). The counts are
# `upload_report`'s; the *_ids columns are the merge's trade ids as JSON arrays. `removed_ids`
# holds every trade the upload removed: the cancelled ones (counted in `removed`) and an old
# database's manual entries (counted in `removed_manual`).
UPLOAD_HISTORY_DDL = ("CREATE TABLE IF NOT EXISTS upload_history ("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                      "filename TEXT NOT NULL DEFAULT '', uploaded_at TEXT NOT NULL DEFAULT '', "
                      "futures INTEGER NOT NULL DEFAULT 0, options_on_futures INTEGER NOT NULL DEFAULT 0, "
                      "lme_forwards INTEGER NOT NULL DEFAULT 0, fx_forwards INTEGER NOT NULL DEFAULT 0, "
                      "fx_spot INTEGER NOT NULL DEFAULT 0, fx_options INTEGER NOT NULL DEFAULT 0, "
                      "excluded_rows INTEGER NOT NULL DEFAULT 0, excluded_text TEXT NOT NULL DEFAULT '', "
                      "underlying_futures_written INTEGER NOT NULL DEFAULT 0, "
                      "library_tickers INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '', "
                      "added INTEGER NOT NULL DEFAULT 0, replaced INTEGER NOT NULL DEFAULT 0, "
                      "removed INTEGER NOT NULL DEFAULT 0, removed_manual INTEGER NOT NULL DEFAULT 0, "
                      "on_file_after INTEGER NOT NULL DEFAULT 0, "
                      "added_ids TEXT NOT NULL DEFAULT '[]', replaced_ids TEXT NOT NULL DEFAULT '[]', "
                      "removed_ids TEXT NOT NULL DEFAULT '[]', need_fix INTEGER NOT NULL DEFAULT 0)")
_HISTORY_ID_COLUMNS = ("added_ids", "replaced_ids", "removed_ids")
UPLOAD_HISTORY_COLUMNS = UPLOAD_REPORT_COLUMNS + ("removed_manual",) + _HISTORY_ID_COLUMNS
# trade_upload_trail's action per id column, in the order an upload applies them.
_TRAIL_ACTIONS = (("added_ids", "added"), ("replaced_ids", "replaced"), ("removed_ids", "removed"))


def _append_history(conn: sqlite3.Connection, row: dict, change: dict) -> None:
    """Append one `upload_history` row: `row` is the `upload_report` row just written, `change`
    the merge accounting with its id lists. Runs inside the caller's transaction. A column an
    older database's table lacks (`need_fix`, 2026-09-29) is added first."""
    conn.execute(UPLOAD_HISTORY_DDL)
    present = {r[1] for r in conn.execute("PRAGMA table_info(upload_history)")}
    for col in UPLOAD_HISTORY_COLUMNS:
        if col not in present:
            kind, default = (("TEXT", "'[]'") if col in _HISTORY_ID_COLUMNS else
                             ("TEXT", "''") if col in _REPORT_DEFAULTS else ("INTEGER", "0"))
            conn.execute(f"ALTER TABLE upload_history ADD COLUMN {col} {kind} NOT NULL DEFAULT {default}")
    ids = {"added_ids": list(change.get("added_ids") or ()),
           "replaced_ids": list(change.get("replaced_ids") or ()),
           "removed_ids": list(change.get("removed_ids") or ()) + list(change.get("removed_manual_ids") or ())}
    values = {**row, "removed_manual": int(change.get("removed_manual", 0) or 0),
              **{col: json.dumps([str(t) for t in v]) for col, v in ids.items()}}
    conn.execute(f"INSERT INTO upload_history ({','.join(UPLOAD_HISTORY_COLUMNS)}) "
                 f"VALUES ({','.join('?' for _ in UPLOAD_HISTORY_COLUMNS)})",
                 [values[c] for c in UPLOAD_HISTORY_COLUMNS])


def _history_ids(text) -> list:
    try:
        found = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return [str(t) for t in found] if isinstance(found, list) else []


def upload_history(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Every recorded upload, newest first: one dict per `upload_history` row, keyed `id` then
    `UPLOAD_HISTORY_COLUMNS`, with `added_ids` / `replaced_ids` / `removed_ids` as lists of
    trade ids. `limit` keeps the newest N. [] on a database with no history (no upload since
    2026-09-29). Read-only: never creates the table."""
    if not _table_exists(conn, "upload_history"):
        return []
    cols = ("id",) + UPLOAD_HISTORY_COLUMNS
    sql = f"SELECT {','.join(cols)} FROM upload_history ORDER BY id DESC"
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(int(limit), 0),)
    out = []
    for values in conn.execute(sql, params):
        entry = dict(zip(cols, values))
        for col in _HISTORY_ID_COLUMNS:
            entry[col] = _history_ids(entry[col])
        out.append(entry)
    return out


def trade_upload_trail(conn: sqlite3.Connection, trade_id) -> list[dict]:
    """The uploads that added, replaced or removed `trade_id`, newest first: one dict per
    (upload, action), {id, filename, uploaded_at, action}, action 'added' | 'replaced' |
    'removed'. [] when no recorded upload touched it (or no history exists). Read-only."""
    if not _table_exists(conn, "upload_history"):
        return []
    tid = str(trade_id)
    needle = json.dumps(tid)          # the id as it sits inside the JSON arrays, quotes included
    rows = conn.execute("SELECT id, filename, uploaded_at, added_ids, replaced_ids, removed_ids FROM upload_history "
                        "WHERE instr(added_ids, ?) > 0 OR instr(replaced_ids, ?) > 0 OR instr(removed_ids, ?) > 0 "
                        "ORDER BY id DESC", (needle, needle, needle)).fetchall()
    out = []
    for upload_id, filename, uploaded_at, *id_texts in rows:
        for (col, action), text in zip(_TRAIL_ACTIONS, id_texts):
            if tid in _history_ids(text):
                out.append({"id": upload_id, "filename": filename, "uploaded_at": uploaded_at, "action": action})
    return out


def last_upload_issues(conn: sqlite3.Connection) -> list[dict]:
    """The rows `record_upload_issues` stored: [{row_no, symbol, kind, reason, filename,
    uploaded_at, trade_id}] in row order. kind is UNRECOGNISED (NEED_FIX_KIND: on file as a
    trade that needs a fix, `trade_id` set; an earlier upload's such trade still on file keeps
    its row, with that upload's file name), WARNING (a doubtful cell or a contradiction that
    loaded on the primary field, with its `trade_id`; row 0 / symbol '' / trade_id '' for a
    line about the file as a whole), or REJECTED / NOT LOADED (an older parser's rows that did
    not become trades). `trade_id` is '' on a table made before 2026-09-29. [] when no upload
    has been recorded. Read-only."""
    if not _table_exists(conn, "upload_issues"):
        return []
    has_tid = "trade_id" in {r[1] for r in conn.execute("PRAGMA table_info(upload_issues)")}
    found = conn.execute("SELECT row_no, symbol, kind, reason, filename, uploaded_at, "
                         f"{'trade_id' if has_tid else chr(39) * 2} FROM upload_issues ORDER BY row_no, rowid").fetchall()
    return [{"row_no": n, "symbol": sym, "kind": kind, "reason": why, "filename": name, "uploaded_at": at,
             "trade_id": tid} for n, sym, kind, why, name, at, tid in found]


# ---- The last file's rows that did not become a trade (user, 2026-09-30: "what i need is check of what
# is and isnt pulled from the blotter and why"). One row per file row that loaded no trade, replaced by
# every successful upload, written in `record_upload_report`'s transaction: status EXCLUDED (pending /
# draft / error status, the book filter, an earlier version of a repeated Trade Id) or CANCELS (a
# cancelled / void / deleted / rejected / failed row; `removed` 1 when it removed a trade on file). Kept
# apart from `upload_issues`, whose every kind but WARNING / UNRECOGNISED the screens count as a reject.
UPLOAD_NOT_LOADED_DDL = ("CREATE TABLE IF NOT EXISTS upload_not_loaded (row_no INTEGER NOT NULL, "
                         "trade_id TEXT NOT NULL DEFAULT '', symbol TEXT NOT NULL DEFAULT '', "
                         "status TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', "
                         "filename TEXT NOT NULL DEFAULT '', uploaded_at TEXT NOT NULL DEFAULT '')")
UPLOAD_NOT_LOADED_COLUMNS = ("row_no", "trade_id", "symbol", "status", "removed", "reason", "filename", "uploaded_at")


def file_rows_outcome(frame: pd.DataFrame, filename, result, change: dict) -> list[dict]:
    """The file's rows as the upload read them, one dict per data row in file order: the parsing
    check's row (``parse_check`` row dicts: row_no, trade_id, symbol, status OK | WARNING | NOT
    RECOGNISED | EXCLUDED | CANCELS, on_file new | replaces | removes | '', message ...), built from
    the load's own ParseResult (no second parse) and the merge's accounting. [] when it cannot be
    built (never fails an upload)."""
    try:
        from data.ingest import parse_check
        df = blotter.canonicalize_columns(frame.copy()).reset_index(drop=True)
        before = set(change.get("replaced_ids") or ()) | set(change.get("removed_ids") or ())
        manual = set(change.get("removed_manual_ids") or ())
        return parse_check._report(str(filename), df, result, before, manual)["rows"]
    except Exception:  # noqa: BLE001 -- a report of the rows never takes the upload down
        return []


def _record_not_loaded(conn: sqlite3.Connection, rows, filename: str, stamp: str) -> None:
    """Replace `upload_not_loaded` with the EXCLUDED / CANCELS rows of `rows` (``file_rows_outcome``).
    Inside the caller's transaction."""
    conn.execute(UPLOAD_NOT_LOADED_DDL)
    conn.execute("DELETE FROM upload_not_loaded")
    out = [(int(r.get("row_no") or 0), str(r.get("trade_id") or ""), str(r.get("symbol") or ""), r["status"],
            1 if r.get("on_file") == "removes" else 0, str(r.get("message") or ""), str(filename), stamp)
           for r in rows if r.get("status") in ("EXCLUDED", "CANCELS")]
    conn.executemany(f"INSERT INTO upload_not_loaded ({','.join(UPLOAD_NOT_LOADED_COLUMNS)}) "
                     f"VALUES ({','.join('?' for _ in UPLOAD_NOT_LOADED_COLUMNS)})", out)


def last_upload_not_loaded(conn: sqlite3.Connection) -> list[dict]:
    """The last upload's file rows that loaded no trade, in row order: [{row_no, trade_id, symbol,
    status, removed, reason, filename, uploaded_at}], status 'EXCLUDED' (not a trade: pending /
    draft / error status, the book filter, an earlier version of a repeated Trade Id) or 'CANCELS'
    (the row cancels its Trade Id; `removed` True when that trade was on file and was removed).
    [] before the first upload that recorded them (2026-09-30). Read-only."""
    if not _table_exists(conn, "upload_not_loaded"):
        return []
    found = conn.execute(f"SELECT {','.join(UPLOAD_NOT_LOADED_COLUMNS)} FROM upload_not_loaded "
                         "ORDER BY row_no, rowid").fetchall()
    out = []
    for values in found:
        entry = dict(zip(UPLOAD_NOT_LOADED_COLUMNS, values))
        entry["removed"] = bool(entry["removed"])
        out.append(entry)
    return out


# ---- Possible duplicates (user, 2026-09-30: "obviously no duplicates when updating with new blotter").
# The merge by Trade Id never loads a Trade Id twice; what it cannot see is the same fill arriving under
# a new Trade Id in a later export. This only FLAGS (hard rule 6: every row loads): nothing is dropped
# or merged. Two trades on file are the same fill when they share the trade date, the contract (the
# instrument, and the last leg's date, so two LME prompts or two FX value dates never match), the side,
# the size and the fill (the stored price: the file's Price cell times the root's broker scale, so the
# same cell always gives the same fill, and a row loaded before `broker_price` was recorded still
# matches). Read at call time from `trades`, `trade_legs` and `upload_history`, so a cancel or a
# re-upload clears a group at once.
DUP_DIFFERENT_UPLOADS = "different_uploads"   # no one upload carried them all: likely a re-booked fill
DUP_SAME_FILE = "same_file"                   # one file carried them all: may be two real fills
DUP_UNKNOWN = "unknown"                       # a member loaded before the upload history began
_HK = timezone(timedelta(hours=8))   # Hong Kong keeps no DST
_COUNT_WORDS = {2: "twice", 3: "three times", 4: "four times"}
_NUMBER_WORDS = {2: "Two", 3: "Three", 4: "Four"}
_TRADE_RECORDS_SQL = ("SELECT t.trade_id, t.trade_date, t.instrument_id, t.product, t.quantity, t.price, "
                      "{symbol}, {price_cell}, t.pb_root, COALESCE(MAX(l.settle_date), '') "
                      "FROM trades t LEFT JOIN trade_legs l ON l.trade_id = t.trade_id GROUP BY t.trade_id")
_RECORD_KEYS = ("trade_id", "trade_date", "instrument_id", "product", "quantity", "price", "broker_symbol",
                "broker_price", "pb_root", "settle")


def trade_records(conn: sqlite3.Connection) -> list[dict]:
    """Every trade on file as the duplicate check reads it: [{trade_id, trade_date, instrument_id,
    product, quantity, price, broker_symbol, broker_price, pb_root, settle}] (`settle` the last leg's
    date, '' with no leg). [] with no trades table. Read-only."""
    if not _table_exists(conn, "trades"):
        return []
    have = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    sql = _TRADE_RECORDS_SQL.format(symbol="t.broker_symbol" if "broker_symbol" in have else "''",
                                    price_cell="t.broker_price" if "broker_price" in have else "''")
    if "pb_root" not in have:
        sql = sql.replace("t.pb_root", "''")
    if not _table_exists(conn, "trade_legs"):
        sql = sql.replace("COALESCE(MAX(l.settle_date), '')", "''").replace(
            " LEFT JOIN trade_legs l ON l.trade_id = t.trade_id", "")
    return [dict(zip(_RECORD_KEYS, values)) for values in conn.execute(sql)]


def uploads_by_trade(conn: sqlite3.Connection) -> dict:
    """{trade_id: [{id, filename, uploaded_at}]}: every recorded upload whose file carried the trade
    (added or replaced it), oldest first. {} with no upload history. Read-only."""
    out: dict = {}
    for entry in reversed(upload_history(conn)):
        carried = {"id": entry["id"], "filename": entry["filename"], "uploaded_at": entry["uploaded_at"]}
        for tid in list(dict.fromkeys(entry["added_ids"] + entry["replaced_ids"])):
            out.setdefault(tid, []).append(carried)
    return out


def _upload_day(uploaded_at, with_time: bool = False) -> str:
    """'29 Sep' (the Hong Kong date of an upload stamped in UTC), '29 Sep 18:02 HK' with the time;
    '' when the stamp does not read."""
    import datetime as _dt
    try:
        stamp = _dt.datetime.fromisoformat(str(uploaded_at))
    except (TypeError, ValueError):
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    day = stamp.astimezone(_HK)
    return f"{day.day} {day:%b}" + (f" {day:%H:%M} HK" if with_time else "")


def _upload_label(upload, with_time: bool = False) -> str:
    """'uploaded 29 Sep' ('uploaded 29 Sep 18:02 HK' with the time), 'in this file' (the dry run's own
    file, id None), '' when unknown."""
    if upload is None:
        return ""
    if upload.get("id") is None:
        return "in this file"
    day = _upload_day(upload.get("uploaded_at"), with_time)
    return f"uploaded {day}" if day else f"uploaded in {upload.get('filename') or 'an earlier file'}"


def _join_words(items: list) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _number_key(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:.10g}"


def duplicate_groups(records, uploads: dict) -> list[dict]:
    """The possible duplicates among `records` (``trade_records`` dicts), `uploads` the files that
    carried each trade (``uploads_by_trade``; an entry with id None is the dry run's own file).
    Pure: see ``possible_duplicates`` for the returned shape."""
    groups: dict = {}
    for r in records:
        q = float(r.get("quantity") or 0.0)
        if q == 0:
            continue
        key = (str(r.get("trade_date") or ""), str(r.get("instrument_id") or ""), str(r.get("settle") or ""),
               1 if q > 0 else -1, _number_key(abs(q)), _number_key(r.get("price")))
        groups.setdefault(key, []).append(r)
    out = []
    for key, members in groups.items():
        if len({m["trade_id"] for m in members}) < 2:
            continue
        members = sorted(members, key=lambda m: str(m["trade_id"]))
        carried = [uploads.get(m["trade_id"]) or [] for m in members]
        if any(not c for c in carried):
            kind = DUP_UNKNOWN
            common = []
        else:
            shared = set.intersection(*({u.get("id") for u in c} for c in carried))
            kind = DUP_SAME_FILE if shared else DUP_DIFFERENT_UPLOADS
            common = [u for u in carried[0] if u.get("id") in shared]
        ids = [str(m["trade_id"]) for m in members]
        n = len(ids)
        if kind == DUP_DIFFERENT_UPLOADS:
            labels = [_upload_label(c[0]) for c in carried]
            if len(set(labels)) < n:       # two uploads on one day: the time tells them apart
                labels = [_upload_label(c[0], with_time=True) for c in carried]
            named = [f"{tid} ({label})" if label else tid for tid, label in zip(ids, labels)]
            sentence = (f"Same fill on file {_COUNT_WORDS.get(n, f'{n} times')} under different Trade Ids: "
                        f"{_join_words(named)}. If the broker re-booked it, the old one should be cancelled in the file.")
        elif kind == DUP_SAME_FILE:
            where = _upload_label(common[-1])
            where = "in this file" if where == "in this file" else f"in the file {where}" if where else "in one file"
            sentence = (f"{_NUMBER_WORDS.get(n, str(n))} identical fills under different Trade Ids {where}: "
                        f"{_join_words(ids)}. They may be {'two' if n == 2 else n} real fills; if one is a "
                        "re-booking, it should be cancelled in the file.")
        else:
            sentence = (f"Same fill on file {_COUNT_WORDS.get(n, f'{n} times')} under different Trade Ids: "
                        f"{_join_words(ids)} (at least one loaded before the upload history began). They may be "
                        f"{'two' if n == 2 else n} real fills; if the broker re-booked it, the old one should be "
                        "cancelled in the file.")
        first = members[0]
        q = float(first["quantity"])
        out.append({
            "kind": kind, "trade_ids": ids, "sentence": sentence,
            "trade_date": key[0], "instrument_id": key[1], "settle_date": key[2],
            "side": "Buy" if q > 0 else "Sell", "quantity": abs(q), "price": float(first["price"]),
            "symbol": next((str(m.get("broker_symbol") or "") for m in members if m.get("broker_symbol")), ""),
            "price_in_file": next((str(m.get("broker_price") or "") for m in members if m.get("broker_price")), ""),
            "product": str(first.get("product") or ""),
            "trades": [{"trade_id": tid, "pb_root": str(m.get("pb_root") or ""),
                        "broker_symbol": str(m.get("broker_symbol") or ""),
                        "broker_price": str(m.get("broker_price") or ""),
                        "first_upload": dict(c[0]) if c else None, "last_upload": dict(c[-1]) if c else None,
                        "arrived": _upload_label(c[0]) if c else ""}
                       for tid, m, c in zip(ids, members, carried)]})
    order = {DUP_DIFFERENT_UPLOADS: 0, DUP_UNKNOWN: 1, DUP_SAME_FILE: 2}
    return sorted(out, key=lambda g: (order[g["kind"]], g["trade_date"], g["instrument_id"], g["trade_ids"]))


def possible_duplicates(conn: sqlite3.Connection) -> list[dict]:
    """The trades on file that look like one fill booked under two or more Trade Ids (never dropped or
    merged: a flag only). One dict per group, the likely re-bookings first:

      kind           'different_uploads' (no one upload carried them all: likely a re-booked fill, the
                     old one should be cancelled in the file) | 'same_file' (one file carried them all:
                     may be two real fills) | 'unknown' (a member loaded before the upload history)
      trade_ids      list[str], sorted
      sentence       the plain sentence for the screen
      trade_date, instrument_id, settle_date (the last leg's date, '' with no leg), side 'Buy' | 'Sell',
      quantity (unsigned, lots / tonnes / base amount), price (the stored fill), symbol and
      price_in_file (the file's cells as written, '' when not recorded), product
      trades         [{trade_id, pb_root, broker_symbol, broker_price, first_upload, last_upload,
                     arrived}] with first_upload / last_upload {id, filename, uploaded_at} of the
                     uploads that carried it (None before the history) and arrived 'uploaded 29 Sep'

    [] with no trades. Read at call time (a few hundred trades: milliseconds). Read-only."""
    return duplicate_groups(trade_records(conn), uploads_by_trade(conn))


# Plain labels of `upload_report`'s kind columns, in the order the load summary names them.
_REPORT_KIND_LABELS = (("futures", "Futures"), ("options_on_futures", "Options on futures"),
                       ("lme_forwards", "LME forwards"), ("fx_forwards", "FX forwards"), ("fx_spot", "FX spot"),
                       ("fx_options", "FX options"))


def last_upload_outcome(conn: sqlite3.Connection) -> dict | None:
    """What the last file brought in and what it did not, and why, in one read (user, 2026-09-30).
    None before the first upload. Keys:

      report          `last_upload_report(conn)` (filename, uploaded_at, the merge counts, summary ...)
      loaded_by_kind  {plain label: n} of the file's trades that loaded, nonzero only ('Futures',
                      'Options on futures', 'LME forwards', 'FX forwards', 'FX spot', 'FX options',
                      'Other', 'Not recognised': on file, need a fix)
      not_loaded      the rows that loaded no trade, with why: `last_upload_not_loaded` rows of status
                      EXCLUDED, then `last_upload_issues` rows of kind REJECTED / NOT LOADED (an older
                      parser's), each {row_no, trade_id, symbol, status, reason}
      cancelled       the cancelling rows: {row_no, trade_id, symbol, removed (bool), reason}
      need_fix        `last_upload_issues` rows of kind UNRECOGNISED (on file, P&L blank until mapped)
      warnings        `last_upload_issues` rows of kind WARNING (loaded on the primary field)
      possible_duplicates  `possible_duplicates(conn)` (the whole book, at call time)
      counts          {loaded, not_loaded, cancelled, removed, need_fix, warnings, possible_duplicates}

    Rows before 2026-09-30's first upload carry no per-row not-loaded list (`not_loaded` then holds
    only the older kinds; `report['excluded_rows']` / `excluded_text` still count them). Read-only."""
    report = last_upload_report(conn)
    if report is None:
        return None
    issues = last_upload_issues(conn)
    rows = last_upload_not_loaded(conn)
    need_fix = [i for i in issues if i["kind"] == NEED_FIX_KIND]
    warnings = [i for i in issues if i["kind"] == "WARNING"]
    not_loaded = [{k: r[k] for k in ("row_no", "trade_id", "symbol", "status", "reason")}
                  for r in rows if r["status"] == "EXCLUDED"]
    not_loaded += [{"row_no": i["row_no"], "trade_id": i["trade_id"], "symbol": i["symbol"], "status": i["kind"],
                    "reason": i["reason"]} for i in issues if i["kind"] in ("REJECTED", "NOT LOADED")]
    not_loaded.sort(key=lambda r: (r["row_no"], r["trade_id"]))
    cancelled = [{k: r[k] for k in ("row_no", "trade_id", "symbol", "removed", "reason")}
                 for r in rows if r["status"] == "CANCELS"]
    by_kind = {label: int(report.get(col) or 0) for col, label in _REPORT_KIND_LABELS}
    loaded = int(report.get("added") or 0) + int(report.get("replaced") or 0)
    need_fix_n = int(report.get("need_fix") or 0)
    other = loaded - sum(by_kind.values()) - need_fix_n
    if other > 0:
        by_kind["Other"] = other
    by_kind["Not recognised"] = need_fix_n
    by_kind = {k: v for k, v in by_kind.items() if v}
    dups = possible_duplicates(conn)
    return {"report": report, "loaded_by_kind": by_kind, "not_loaded": not_loaded, "cancelled": cancelled,
            "need_fix": need_fix, "warnings": warnings, "possible_duplicates": dups,
            "counts": {"loaded": loaded, "not_loaded": len(not_loaded), "cancelled": len(cancelled),
                       "removed": int(report.get("removed") or 0), "need_fix": len(need_fix),
                       "warnings": len(warnings), "possible_duplicates": len(dups)}}


def unrecognised_reasons(conn: sqlite3.Connection) -> dict:
    """{trade_id: reason} for every trade on file that needs a fix (product UNRECOGNISED), read
    from `upload_issues` rows of kind UNRECOGNISED; a trade with no such row (none after an upload
    or `reresolve_unrecognised`) is absent. Read-only; {} with no table."""
    if not _table_exists(conn, "upload_issues"):
        return {}
    if "trade_id" not in {r[1] for r in conn.execute("PRAGMA table_info(upload_issues)")}:
        return {}
    out: dict = {}
    for tid, why in conn.execute("SELECT i.trade_id, i.reason FROM upload_issues i JOIN trades t USING (trade_id) "
                                 "WHERE i.kind = ? AND t.product = ? ORDER BY i.row_no, i.rowid",
                                 (NEED_FIX_KIND, UNRECOGNISED)):
        out.setdefault(tid, why)
    return out


# --------------------------------------------------------------------------- re-resolution
def _resolved_parts(parsed) -> dict:
    """What `blotter.resolve_stored` returned for one trade, as lists: trades, legs, instruments,
    instrument_options, underlying_only. Accepts a ParseResult-like object, a dict carrying any of
    those keys (or `trade` / `instrument` singly), or a bare Trade."""
    def many(name, single=None):
        found = _get(parsed, name)
        if found is None and single is not None:
            one = _get(parsed, single)
            found = [] if one is None else [one]
        if isinstance(found, dict):
            found = list(found.values())
        return list(found or ())
    if parsed is not None and hasattr(parsed, "product") and hasattr(parsed, "trade_id"):
        return {"trades": [parsed], "legs": [], "instruments": [], "instrument_options": [], "underlying_only": set()}
    return {"trades": many("trades", "trade"), "legs": many("legs"), "instruments": many("instruments", "instrument"),
            "instrument_options": many("instrument_options", "instrument_option"),
            "underlying_only": set(_get(parsed, "underlying_only") or ())}


def _call_resolve_stored(fn, trade: dict, conn: sqlite3.Connection):
    """(parsed or None, reason) from the parser's `resolve_stored`, whatever of the two shapes it
    returns ((parsed, reason) or parsed alone); an exception is (None, its message)."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
        out = fn(trade, conn=conn) if "conn" in params else fn(trade)
    except Exception as exc:  # noqa: BLE001 -- one trade's failure is its reason, never the whole pass's
        return None, f"could not be re-read ({exc})"
    if isinstance(out, tuple) and len(out) == 2:
        return out[0], str(out[1] or "")
    return out, ""


def _replace_resolved(conn: sqlite3.Connection, trade_id: str, old_instrument: str, parts: dict) -> dict:
    """Rewrite one UNRECOGNISED trade in place as the product it now resolves to, exactly as an
    upload writes a trade: its instrument (INSERT OR REPLACE, named columns; an option's underlying
    future INSERT OR IGNORE), its option terms (a typed term never overwritten by a blank), its
    trade row (upsert by name; a hand-set theme kept), its legs (deleted, then the parser's), its
    realised_pnl row dropped. The UNRECOGNISED instrument is deleted when nothing else refers to
    it, and the Bloomberg library is marked dirty (the triggers do so too). One transaction."""
    trade = next(t for t in parts["trades"] if t.trade_id == trade_id)
    legs = [leg for leg in parts["legs"] if leg.trade_id == trade_id]
    inames = ",".join(_INSTRUMENT_COLUMNS)
    iholes = ",".join("?" for _ in _INSTRUMENT_COLUMNS)
    with conn:
        theme = conn.execute("SELECT theme FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
        for inst in parts["instruments"]:
            verb = "INSERT OR IGNORE" if inst.instrument_id in parts["underlying_only"] else "INSERT OR REPLACE"
            conn.execute(f"{verb} INTO instruments ({inames}) VALUES ({iholes})",
                         [getattr(inst, c) for c in _INSTRUMENT_COLUMNS])
        conn.executemany(
            f"INSERT INTO instrument_options ({','.join(_OPTION_COLUMNS)}) VALUES ({','.join('?' for _ in _OPTION_COLUMNS)}) "
            "ON CONFLICT(instrument_id) DO UPDATE SET "
            "strike = CASE WHEN excluded.strike != 0 THEN excluded.strike ELSE strike END, "
            "option_type = CASE WHEN excluded.option_type != '' THEN excluded.option_type ELSE option_type END, "
            "payoff = CASE WHEN excluded.payoff != 'VANILLA' THEN excluded.payoff ELSE payoff END",
            [[getattr(o, c) for c in _OPTION_COLUMNS] for o in parts["instrument_options"]])
        for table in TRADE_KEYED_CHILD_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE trade_id = ?", (trade_id,))
        _upsert_trade(conn, trade)
        if theme and theme[0]:
            conn.execute("UPDATE trades SET theme = ? WHERE trade_id = ? AND theme = ''", (theme[0], trade_id))
        conn.executemany(f"INSERT INTO trade_legs ({','.join(_LEG_COLUMNS)}) VALUES ({','.join('?' for _ in _LEG_COLUMNS)})",
                         [[getattr(leg, c) for c in _LEG_COLUMNS] for leg in legs])
        dropped = False
        if old_instrument and old_instrument != trade.instrument_id:
            refs = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE instrument_id = ?", (old_instrument,)).fetchone()[0]
                       for table in ("trades", "instrument_theme", "marks") if _table_exists(conn, table))
            if not refs:
                conn.execute("DELETE FROM instrument_options WHERE instrument_id = ?", (old_instrument,))
                conn.execute("DELETE FROM instruments WHERE instrument_id = ? AND asset_class = ?",
                             (old_instrument, UNRECOGNISED))
                dropped = True
        if _table_exists(conn, "bbg_library_state"):
            conn.execute("UPDATE bbg_library_state SET dirty = 1")
    return {"trade_id": trade_id, "symbol": trade.broker_symbol, "instrument_id": trade.instrument_id,
            "product": trade.product, "legs": len(legs), "unrecognised_instrument_dropped": dropped}


def _refresh_need_fix_issues(conn: sqlite3.Connection, still: list, resolved_ids) -> None:
    """Keep `upload_issues`' UNRECOGNISED rows in step with the trades on file: a resolved trade's
    row goes; a still-unrecognised trade with no row (loaded by an upload made before the table
    had trade_id, or whose row was lost) gets one, dated by the last upload that added or replaced
    it (`trade_upload_trail`), with the parser's reason of today. Rows already there keep their
    reason (the upload's own words)."""
    with conn:
        _migrate_issues_table(conn)
        conn.executemany("DELETE FROM upload_issues WHERE kind = ? AND trade_id = ?",
                         [(NEED_FIX_KIND, tid) for tid in resolved_ids])
        have = {r[0] for r in conn.execute("SELECT trade_id FROM upload_issues WHERE kind = ?", (NEED_FIX_KIND,))}
        rows = []
        for s in still:
            if s["trade_id"] in have:
                continue
            trail = [t for t in trade_upload_trail(conn, s["trade_id"]) if t["action"] != "removed"]
            name, at = (trail[0]["filename"], trail[0]["uploaded_at"]) if trail else ("", "")
            rows.append((0, s["symbol"], NEED_FIX_KIND, s["reason"], name, at, s["trade_id"]))
        _insert_issues(conn, rows)


def reresolve_unrecognised(conn: sqlite3.Connection) -> dict:
    """Try every trade on file of product UNRECOGNISED again through the parser
    (`data.ingest.blotter.resolve_stored`), so a fix to config/contracts.csv prices it with no
    re-upload (hard rule 6, 2026-09-29). Runs at the end of every upload (after the merge, before
    the contract dates and the Bloomberg library sync) and at app start-up. Asks Bloomberg nothing.

    `resolve_stored` is handed each trade as a dict: every `trades` column, plus `currency` (the
    UNRECOGNISED instrument's quote_ccy, i.e. the file's Currency cell) and `symbol` (=
    broker_symbol); `conn` too when it takes one. It returns the parsed trade to write (a
    ParseResult-like object with trades / legs / instruments / instrument_options /
    underlying_only, or a dict of those, or a Trade) or None, with the reason: `(parsed, reason)`.
    A trade that now resolves (same trade_id, a product other than UNRECOGNISED) is rewritten in
    place (`_replace_resolved`); nothing is ever guessed, and a trade the parser still cannot
    identify stays exactly as it is.

    Returns {'resolved': [{trade_id, symbol, instrument_id, product, legs,
    unrecognised_instrument_dropped}], 'still_unrecognised': [{trade_id, symbol, reason}],
    'contract_dates': apply_contract_dates' result when something resolved (else None),
    'sentence': one plain sentence ('' when there was nothing to try), 'error': '' or the database
    error that stopped the pass (never raised: a start-up must not fail on it)}."""
    out = {"resolved": [], "still_unrecognised": [], "contract_dates": None, "sentence": "", "error": ""}
    try:
        rows = conn.execute("SELECT t.*, COALESCE(i.quote_ccy, '') AS _currency FROM trades t "
                            "LEFT JOIN instruments i USING (instrument_id) WHERE t.product = ? ORDER BY t.trade_id",
                            (UNRECOGNISED,))
        names = [d[0] for d in rows.description]
        stored = [dict(zip(names, r)) for r in rows.fetchall()]
    except sqlite3.Error as exc:
        out["error"] = str(exc)
        return out
    if not stored:
        return out
    fn = getattr(blotter, "resolve_stored", None)
    for row in stored:
        tid, old_instrument = str(row["trade_id"]), str(row["instrument_id"] or "")
        trade = {k: v for k, v in row.items() if k != "_currency"}
        trade.update(currency=row["_currency"], symbol=row.get("broker_symbol") or "")
        symbol = str(trade["symbol"] or old_instrument[len(UNRECOGNISED_PREFIX):])
        if fn is None:
            out["still_unrecognised"].append({"trade_id": tid, "symbol": symbol,
                                              "reason": "the parser cannot re-read a stored row yet (no resolve_stored)"})
            continue
        parsed, reason = _call_resolve_stored(fn, trade, conn)
        parts = _resolved_parts(parsed) if parsed is not None else None
        match = [t for t in (parts or {}).get("trades", ()) if t.trade_id == tid and t.product != UNRECOGNISED]
        if not match:
            out["still_unrecognised"].append({"trade_id": tid, "symbol": symbol,
                                              "reason": reason or "contract not recognised: the parser still cannot "
                                                                  f"identify {symbol or 'this row'}"})
            continue
        try:
            out["resolved"].append(_replace_resolved(conn, tid, old_instrument, parts))
        except sqlite3.Error as exc:
            out["still_unrecognised"].append({"trade_id": tid, "symbol": symbol,
                                              "reason": f"resolved but could not be rewritten ({exc})"})
    try:
        _refresh_need_fix_issues(conn, out["still_unrecognised"], [r["trade_id"] for r in out["resolved"]])
        if out["resolved"]:
            from data.ingest import contract_dates
            out["contract_dates"] = contract_dates.apply_contract_dates(conn)
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    n_ok, n_left = len(out["resolved"]), len(out["still_unrecognised"])
    if n_ok:
        shown = ", ".join(f"{r['symbol'] or r['trade_id']} -> {r['instrument_id']}" for r in out["resolved"][:5])
        more = f" (+{n_ok - 5} more)" if n_ok > 5 else ""
        out["sentence"] = (f"{n_ok} trade(s) that needed a fix now match a contract and were rewritten in place "
                           f"({shown}{more}); {n_left} still need a fix.")
    return out


# --------------------------------------------------------------------------- rebuild on a contract fix
# The products whose instrument hangs off a contract root (instruments.base_ccy = the root id): a
# commodity future, an option on one (its underlying future is written with it), an LME ticket.
ROOT_PRODUCTS = ("FUTURE", "CMDTY_OPTION", "LME_FWD")
# The Fin Type a recognised trade is re-read as when its stored fin_type is blank (loaded before the
# column existed): the parser's own kind words (an LME ticket arrives as a FUTURE row naming a metal).
_KIND_OF_PRODUCT = {"FUTURE": "Future", "CMDTY_OPTION": "Option", "LME_FWD": "Future"}
_FIELD_WORDS = {"instrument_id": "contract id", "bbg_ticker": "Bloomberg ticker", "multiplier": "multiplier",
                "quote_ccy": "currency", "expiry_date": "expiry", "asset_class": "kind", "base_ccy": "root"}


def _same(a, b) -> bool:
    """Equal, numbers within float noise (a price rebuilt from its Price cell round-trips a divide)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        import math
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _root_key(root_id) -> str:
    return "".join(str(root_id or "").split()).upper()


def _stored_rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _instrument_row(conn: sqlite3.Connection, instrument_id: str) -> dict | None:
    rows = _stored_rows(conn, f"SELECT {','.join(_INSTRUMENT_COLUMNS)} FROM instruments WHERE instrument_id = ?",
                        (instrument_id,))
    return rows[0] if rows else None


def _stored_trade_for_rebuild(row: dict, instrument: dict, legs: list[dict], currency: str, before) -> dict:
    """The stored trade as `blotter.resolve_stored` reads it, for a trade that DID resolve at upload:
    every trades column, `symbol` = broker_symbol, the Currency cell (`currency`), the Fin Type from
    the product when none is stored, the Price cell as written (or, when it is not on file, the fill
    divided back by the root's broker price scale, so the parser's scale gives the stored fill again:
    trades.price is always in Bloomberg's units), and for an LME ticket its lots (tonnes over the lot
    size; `before`'s when given, since the fix may have changed it) and its prompt (the metal leg's
    date)."""
    from data.contracts import load_roots
    trade = dict(row)
    trade.update(currency=currency, symbol=row.get("broker_symbol") or "")
    if not str(row.get("fin_type") or "").strip():
        trade["fin_type"] = _KIND_OF_PRODUCT.get(row["product"], "")
    root_id = instrument.get("base_ccy") or ""
    root = load_roots().get(root_id)
    if row["product"] == "LME_FWD":
        old = (before or {}).get(root_id) or root
        size = float(getattr(old, "contract_size", 0) or 0)
        if size:
            trade["quantity"] = float(row["quantity"] or 0) / size
        metal = [leg for leg in legs if leg["settles_cash"] == 0] or legs
        if metal:
            trade["settle_date"] = metal[0]["settle_date"]
    elif not str(row.get("broker_price") or "").strip():
        scale = float(getattr(root, "broker_price_scale", 1.0) or 1.0)
        price = row.get("price")
        if isinstance(price, (int, float)) and price != 0:
            trade["broker_price"] = repr(float(price) / scale)
    return trade


def _rebuild_one(conn: sqlite3.Connection, row: dict, instrument: dict, legs: list[dict], before):
    """(parts, reason): the trade re-read from its stored broker fields against the contract list as
    it is now, or (None, why). Tried first with no Currency cell (the broker symbol alone, as Jason's
    export gives it), then with the currency it was booked in (a code two exchanges share). It must
    come back as the same trade, the same product, on the same root: a fix changes a root's fields,
    never which root a trade belongs to."""
    fn = getattr(blotter, "resolve_stored", None)
    if fn is None:
        return None, "the parser cannot re-read a stored row (no resolve_stored)"
    tid, root_id = str(row["trade_id"]), instrument.get("base_ccy") or ""
    reason = ""
    for currency in dict.fromkeys(("", str(instrument.get("quote_ccy") or ""))):
        parsed, why = _call_resolve_stored(fn, _stored_trade_for_rebuild(row, instrument, legs, currency, before), conn)
        reason = why or reason
        parts = _resolved_parts(parsed) if parsed is not None else None
        match = [t for t in (parts or {}).get("trades", ()) if t.trade_id == tid]
        if not match:
            continue
        trade = match[0]
        if trade.product != row["product"]:
            return None, f"it now reads as {trade.product} rather than {row['product']}; left as it was"
        insts = {i.instrument_id: i for i in parts["instruments"]}
        new_root = getattr(insts.get(trade.instrument_id), "base_ccy", root_id)
        if new_root != root_id:
            return None, f"its symbol {row.get('broker_symbol') or tid} now matches {new_root}, not {root_id}; left as it was"
        # Only the booking moves: the file's own words stay as stored (a Fin Type or Price cell
        # filled in above for the re-read is never written back).
        import dataclasses
        keep = {name: row[name] for name in ("fin_type", "broker_price", "broker_symbol", "theme")
                if name in row and hasattr(trade, name)}
        kept = dataclasses.replace(trade, **keep) if keep else trade
        parts["trades"] = [kept if t is trade else t for t in parts["trades"]]
        return parts, ""
    return None, reason or f"its symbol {row.get('broker_symbol') or tid} no longer matches a contract"


def _booking_changed(conn: sqlite3.Connection, row: dict, legs: list[dict], parts: dict) -> bool:
    """True when the re-read booking differs from what is on file: any instrument row it writes, the
    trade's columns (its hand-set theme aside), its legs, an option's terms."""
    tid = str(row["trade_id"])
    trade = next(t for t in parts["trades"] if t.trade_id == tid)
    for inst in parts["instruments"]:
        stored = _instrument_row(conn, inst.instrument_id)
        if stored is None or any(not _same(stored[c], getattr(inst, c)) for c in _INSTRUMENT_COLUMNS):
            return True
    for name, value in vars(trade).items():
        if name in row and name != "theme" and not _same(row[name], value):
            return True
    new_legs = sorted(([getattr(leg, c) for c in _LEG_COLUMNS] for leg in parts["legs"] if leg.trade_id == tid),
                      key=lambda v: v[1])
    old_legs = [[leg[c] for c in _LEG_COLUMNS] for leg in legs]
    if len(new_legs) != len(old_legs) or any(not _same(a, b) for n, o in zip(new_legs, old_legs) for a, b in zip(n, o)):
        return True
    for opt in parts["instrument_options"]:
        got = conn.execute("SELECT 1 FROM instrument_options WHERE instrument_id = ?", (opt.instrument_id,)).fetchone()
        if got is None:
            return True
    return False


def _write_rebuilt(conn: sqlite3.Connection, tid: str, old_instrument: str, parts: dict) -> None:
    """Write one rebuilt trade inside the caller's transaction: every instrument the re-read gives
    (its own and an option's underlying future) upserted by name, so a stored row takes the fixed
    Bloomberg ticker, multiplier and currency; option terms merged (a typed term never lost); the
    trade upserted with its theme kept; its legs replaced; its realised_pnl row dropped so the
    ledger freezes it again from the new booking. A bundle membership follows a moved contract id.
    Marks are never touched."""
    trade = next(t for t in parts["trades"] if t.trade_id == tid)
    legs = [leg for leg in parts["legs"] if leg.trade_id == tid]
    names = ",".join(_INSTRUMENT_COLUMNS)
    sets = ",".join(f"{c}=excluded.{c}" for c in _INSTRUMENT_COLUMNS if c != "instrument_id")
    for inst in parts["instruments"]:
        conn.execute(f"INSERT INTO instruments ({names}) VALUES ({','.join('?' for _ in _INSTRUMENT_COLUMNS)}) "
                     f"ON CONFLICT(instrument_id) DO UPDATE SET {sets}", [getattr(inst, c) for c in _INSTRUMENT_COLUMNS])
    conn.executemany(
        f"INSERT INTO instrument_options ({','.join(_OPTION_COLUMNS)}) VALUES ({','.join('?' for _ in _OPTION_COLUMNS)}) "
        "ON CONFLICT(instrument_id) DO UPDATE SET "
        "strike = CASE WHEN excluded.strike != 0 THEN excluded.strike ELSE strike END, "
        "option_type = CASE WHEN excluded.option_type != '' THEN excluded.option_type ELSE option_type END, "
        "payoff = CASE WHEN excluded.payoff != 'VANILLA' THEN excluded.payoff ELSE payoff END",
        [[getattr(o, c) for c in _OPTION_COLUMNS] for o in parts["instrument_options"]])
    theme = conn.execute("SELECT theme FROM trades WHERE trade_id = ?", (tid,)).fetchone()
    for table in TRADE_KEYED_CHILD_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE trade_id = ?", (tid,))
    _upsert_trade(conn, trade)
    if theme and theme[0]:
        conn.execute("UPDATE trades SET theme = ? WHERE trade_id = ?", (theme[0], tid))
    conn.executemany(f"INSERT INTO trade_legs ({','.join(_LEG_COLUMNS)}) VALUES ({','.join('?' for _ in _LEG_COLUMNS)})",
                     [[getattr(leg, c) for c in _LEG_COLUMNS] for leg in legs])
    if old_instrument != trade.instrument_id and _table_exists(conn, "instrument_theme"):
        conn.execute("INSERT OR IGNORE INTO instrument_theme (instrument_id, theme) "
                     "SELECT ?, theme FROM instrument_theme WHERE instrument_id = ?", (trade.instrument_id, old_instrument))


def _unreferenced(conn: sqlite3.Connection, instrument_id: str) -> bool:
    return not any(conn.execute(f"SELECT 1 FROM {table} WHERE instrument_id = ? LIMIT 1", (instrument_id,)).fetchone()
                   for table in ("trades", "marks") if _table_exists(conn, table))


def _rebuild_sentence(names: list[str], rebuilt: list, unchanged: list, failed: list, changes: list,
                      moved_ids: bool) -> str:
    """One plain sentence for the Data tab."""
    on = ", ".join(names) or "the roots named"
    if not (rebuilt or unchanged or failed):
        return f"No trade on file is on {on}; nothing to rebuild."
    parts = []
    if rebuilt:
        by_field: dict = {}
        for c in changes:
            by_field.setdefault(c["field"], []).append(c)
        said = []
        for field, rows in by_field.items():
            first = rows[0]
            before = first["before"] if first["before"] not in ("", None) else "none"
            more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
            said.append(f"{_FIELD_WORDS.get(field, field)} {before} → {first['after']}{more}")
        what = "; ".join(said) if said else "legs and fills re-read"
        parts.append(f"{len(rebuilt)} trade(s) on {on} rebuilt: {what}")
        if unchanged:
            parts.append(f"{len(unchanged)} already matched")
    else:
        parts.append(f"Nothing in the book changed: the {len(unchanged)} trade(s) on {on} already match the contract list"
                     if unchanged else f"No trade on {on} was rebuilt")
    if failed:
        head = "; ".join(f"{f['trade_id']}: {f['why']}" for f in failed[:3])
        extra = f" (+{len(failed) - 3} more)" if len(failed) > 3 else ""
        parts.append(f"{len(failed)} could not be rebuilt and stay as they were ({head}{extra})")
    sentence = "; ".join(parts) + "."
    if moved_ids:
        sentence += (" Prices on file stay under the old contract ids; press Pull Bloomberg now to price the new ones.")
    elif rebuilt:
        sentence += " Nothing was pulled; the next Bloomberg pull asks for the new tickers."
    return sentence


def reresolve_roots(conn: sqlite3.Connection, root_ids, before=None) -> dict:
    """Rebuild every trade on the contract roots `root_ids` ('COMEX:HG', or a list of them) from its
    stored broker fields against the contract list as it is NOW, so a fix applied to
    config/contracts.csv from the Data tab (`data.contracts.apply_fixes`: Bloomberg root, yellow key,
    currency, contract size, units, price scale, verified) reaches the book with no re-upload. The
    user, 2026-09-30: "i should be able to do this in the app". Call it straight after the fix is
    written. Asks Bloomberg nothing (hard rule 8).

    In scope: every FUTURE, CMDTY_OPTION (and its underlying future's row) and LME_FWD whose
    instrument's root (base_ccy) is named. contract-master's cache is cleared first, so the rewritten
    file is read. Each trade goes through `blotter.resolve_stored` exactly as a re-upload of its row
    would (the Symbol and Price cells as written, the Fin Type, PBRoot, date, side and size), and
    must come back as the same trade, product and root.

    - A trade whose booking is the same (instrument row, trade columns, legs) is `unchanged` and
      not written: a bbg_verified fix alone changes nothing on file.
    - A changed trade is `rebuilt`: its instrument(s) take the new Bloomberg ticker, multiplier,
      currency (a Bloomberg root or yellow-key fix moves it to a new canonical contract id, 'HGZ26
      Comdty' -> 'HGXZ26 Comdty', with its bundle membership), its fill and NOTIONAL legs are
      rewritten, its realised_pnl row is dropped for the ledger to freeze again. All rebuilt trades
      are written in ONE transaction; an old instrument row that nothing refers to any more (no
      trade, no mark) is then removed, unless an option of the pass failed (its old underlying
      future's row is kept for it).
    - A trade that no longer resolves, or resolves to another product or root, stays exactly as it
      was and is listed under `failed` with the reason: never dropped, never turned UNRECOGNISED
      here (hard rule 6). Nothing raises for one trade; a database error on the write rolls the
      whole pass back and every trade to be rebuilt is listed as failed with it.

    Then Bloomberg's stored contract dates are put back (`contract_dates.apply_contract_dates`,
    which re-keys an expiry's marks, never a value), and `reresolve_unrecognised` runs, since the
    fixed file may now name a contract a row that needed a fix was missing. Marks are otherwise
    never touched. The trades / trade_legs triggers set the Bloomberg library dirty (and it is set
    here too); the screens' pricing memos key on the database file's (mtime_ns, size), so the write
    itself makes every screen re-read.

    `before` (optional): {root_id: ContractRoot} as `data.contracts.load_roots()` gave it BEFORE the
    fix was written. Only an LME ticket needs it, and only when the fix changed the lot size: its
    lots are not on file (quantity is tonnes), so they are recovered as tonnes / the old lot size.
    Without it the current lot size is used, which leaves the tonnes as they are.

    Returns {'rebuilt': [trade_id], 'unchanged': [trade_id], 'failed': [{'trade_id', 'why'}],
    'changed_instruments': [{'instrument_id', 'field', 'before', 'after'}] (field in the
    instruments columns, 'instrument_id' for a trade moved to a new contract id: before = the old
    id), 'removed_instruments': [instrument_id], 'contract_dates': apply_contract_dates' result or
    None, 'reresolved_unrecognised': [trade_id], 'sentence': one plain sentence, 'error': '' or the
    database error}."""
    out = {"rebuilt": [], "unchanged": [], "failed": [], "changed_instruments": [], "removed_instruments": [],
           "contract_dates": None, "reresolved_unrecognised": [], "sentence": "", "error": ""}
    roots_asked = [_root_key(r) for r in ([root_ids] if isinstance(root_ids, str) else list(root_ids or ()))]
    roots_asked = [r for r in dict.fromkeys(roots_asked) if r]
    try:
        from data.contracts import load_roots
        from data.contracts.universe import _load
        _load.cache_clear()
        known = load_roots()
    except Exception as exc:  # noqa: BLE001 -- a contract file that will not load: nothing is rebuilt
        out["error"] = f"the contract list could not be read ({exc})"
        out["sentence"] = f"Nothing rebuilt: the contract list could not be read ({exc})."
        return out
    names = [getattr(known.get(r), "name", "") or r for r in roots_asked]
    if not roots_asked:
        out["sentence"] = "No contract named; nothing to rebuild."
        return out
    try:
        holes = ",".join("?" for _ in roots_asked)
        rows = _stored_rows(conn, f"SELECT t.* FROM trades t JOIN instruments i USING (instrument_id) "
                                  f"WHERE i.base_ccy IN ({holes}) AND t.product IN ({','.join('?' for _ in ROOT_PRODUCTS)}) "
                                  "ORDER BY t.trade_id", (*roots_asked, *ROOT_PRODUCTS))
    except sqlite3.Error as exc:
        out["error"] = str(exc)
        out["sentence"] = f"Nothing rebuilt: the trades could not be read ({exc})."
        return out

    to_write = []          # (trade_id, old instrument row, parts)
    for row in rows:
        tid = str(row["trade_id"])
        try:
            instrument = _instrument_row(conn, row["instrument_id"]) or {"instrument_id": row["instrument_id"]}
            legs = _stored_rows(conn, f"SELECT {','.join(_LEG_COLUMNS)} FROM trade_legs WHERE trade_id = ? "
                                      "ORDER BY leg_no", (tid,))
            parts, why = _rebuild_one(conn, row, instrument, legs, before)
            if parts is None:
                out["failed"].append({"trade_id": tid, "why": why})
            elif _booking_changed(conn, row, legs, parts):
                to_write.append((tid, instrument, parts))
            else:
                out["unchanged"].append(tid)
        except Exception as exc:  # noqa: BLE001 -- one trade's failure is its reason, never the whole pass's
            out["failed"].append({"trade_id": tid, "why": f"could not be re-read ({exc})"})

    snapshots: dict = {}
    for _tid, instrument, parts in to_write:
        snapshots.setdefault(instrument["instrument_id"], instrument)
        for inst in parts["instruments"]:
            if inst.instrument_id not in snapshots:
                snapshots[inst.instrument_id] = _instrument_row(conn, inst.instrument_id)
    try:
        with conn:
            for tid, instrument, parts in to_write:
                _write_rebuilt(conn, tid, instrument["instrument_id"], parts)
            if to_write and _table_exists(conn, "bbg_library_state"):
                conn.execute("UPDATE bbg_library_state SET dirty = 1")
        out["rebuilt"] = [tid for tid, _i, _p in to_write]
    except sqlite3.Error as exc:
        out["error"] = str(exc)
        out["failed"] += [{"trade_id": tid, "why": f"could not be written ({exc}); left as it was"}
                          for tid, _i, _p in to_write]
        to_write = []

    try:
        if to_write:
            from data.ingest import contract_dates
            out["contract_dates"] = contract_dates.apply_contract_dates(conn)
        # Instruments the rebuilt trades left behind: removed when nothing refers to them any more.
        new_ids = {inst.instrument_id for _t, _i, parts in to_write for inst in parts["instruments"]}
        # A failed option may still need its old underlying future's row (it is referenced by no
        # trade), so nothing is removed then.
        failed_ids = {f["trade_id"] for f in out["failed"]}
        failed_option = any(str(r["trade_id"]) in failed_ids and r["product"] == "CMDTY_OPTION" for r in rows)
        if to_write and not failed_option:
            gone = [iid for iid in snapshots if iid not in new_ids and snapshots[iid] is not None
                    and _unreferenced(conn, iid)]
            with conn:
                for iid in gone:
                    conn.execute("DELETE FROM instrument_options WHERE instrument_id = ?", (iid,))
                    if _table_exists(conn, "instrument_theme"):
                        conn.execute("DELETE FROM instrument_theme WHERE instrument_id = ?", (iid,))
                    conn.execute("DELETE FROM instruments WHERE instrument_id = ?", (iid,))
            out["removed_instruments"] = gone
    except sqlite3.Error as exc:
        out["error"] = out["error"] or str(exc)

    # What changed per instrument: a moved trade against its old contract, every other against itself.
    moved_ids = False
    seen = set()
    for tid, instrument, parts in to_write:
        trade = next(t for t in parts["trades"] if t.trade_id == tid)
        for inst in parts["instruments"]:
            iid = inst.instrument_id
            old = snapshots.get(iid)
            if old is None and iid == trade.instrument_id:
                old = instrument
                if (iid, "instrument_id") not in seen:
                    seen.add((iid, "instrument_id"))
                    moved_ids = True
                    out["changed_instruments"].append({"instrument_id": iid, "field": "instrument_id",
                                                       "before": instrument["instrument_id"], "after": iid})
            now = _instrument_row(conn, iid)
            if old is None or now is None:
                continue
            for field in _INSTRUMENT_COLUMNS[1:]:
                if field in old and not _same(old[field], now[field]) and (iid, field) not in seen:
                    seen.add((iid, field))
                    out["changed_instruments"].append({"instrument_id": iid, "field": field,
                                                       "before": old[field], "after": now[field]})

    unrec = reresolve_unrecognised(conn)
    out["reresolved_unrecognised"] = [r["trade_id"] for r in unrec["resolved"]]
    out["error"] = out["error"] or unrec["error"]
    out["sentence"] = _rebuild_sentence(names, out["rebuilt"], out["unchanged"], out["failed"],
                                        out["changed_instruments"], moved_ids)
    if unrec["sentence"]:
        out["sentence"] += " " + unrec["sentence"]
    return out


def import_blotter_report(payload, filename, db_path) -> dict:
    """`import_blotter`, with the outcome as data. Keys, exactly:

      message   str        the summary paragraph: what was imported and replaced, what
                           was excluded, the rejected rows (the REJECTS_PHRASE sentence),
                           then every note below
      rejects   int        rows skipped because they could not be read
      warnings  int        things the user should read: a cell that was not a number and
                           was rebuilt from other columns, a NetInvoice that disagrees
                           with Quantity x Price beyond tolerance, a non-numeric strike
                           cell that was ignored -- anywhere the file's content was
                           doubtful and the parser had to rebuild, ignore or distrust a cell
      notes     list[str]  the individual note sentences (information first, then the
                           warnings sentence, which names the rows)
      added, replaced, removed, removed_manual, on_file_after
                int        the merge by Trade Id (2026-09-28): trades the file added, trades
                           already on file replaced by the file's rows, trades removed as
                           cancelled, an old database's manual entries removed, and the
                           book's trade count after the upload
      need_fix  int        the file's rows on file as UNRECOGNISED after the re-resolution
                           (every row loads, 2026-09-29): "N rows need a fix"; never in `rejects`
      need_fix_ids list    their trade ids
      need_fix_on_file int the whole book's UNRECOGNISED trades after the upload
      reresolved list      trade ids `reresolve_unrecognised` rewrote as a real product
      possible_duplicates int  groups of trades on file that look like one fill under two or more
                           Trade Ids after the upload (`possible_duplicates`; flagged, never dropped)

    INFORMATION is in `notes` and `message` but never raises `warnings`: an option with
    no strike in the file, which the Blotter's missing-terms banner shows persistently, so
    an import that only has that is a clean one.

    The first sentence counts the trades the file loaded, by kind (`loaded_breakdown`), so
    a rejected row is never in it; the rejects have their own sentence."""
    frame = blotter.read_table(payload, filename)
    validate_blotter_shape(frame)

    def _load(staged):
        try:
            res = blotter.load(frame, staged, strict=False, filename=filename)
            _write_unrecognised(staged, res)      # every row loads (2026-09-29); a no-op when load wrote them
            return res
        except sqlite3.Error as e:
            raise ValueError(f"Nothing imported. Database error: {e}") from e

    result, change = _stage_and_publish(db_path, _load)
    record_upload_issues(db_path, filename, result)
    # After the merge, before the contract dates and the library sync: a trade on file that needed
    # a fix and that the contract list now knows is rewritten in place (every row loads, 2026-09-29).
    with closing(schema.connect(Path(db_path).resolve())) as conn:
        reresolved = reresolve_unrecognised(conn)
        change["need_fix_on_file"] = conn.execute("SELECT COUNT(*) FROM trades WHERE product = ?",
                                                  (UNRECOGNISED,)).fetchone()[0]
    resolved_now = {r["trade_id"] for r in reresolved["resolved"]}
    need_fix = [r for r in unrecognised_rows(result) if r["trade_id"] not in resolved_now]
    change["need_fix"] = len(need_fix)
    file_trades = _file_trades(result)
    n_trades, n_legs = len(file_trades), len(result.legs)
    parts = [f"Imported {filename}: {n_trades} trades -- {loaded_breakdown(file_trades)}; {n_legs} legs. "
             f"{result.n_currency} cash rows seen ({result.n_spot} of them spot fills).",
             merge_sentence(n_trades, change)]
    fix_sentence = need_fix_sentence(need_fix)
    if fix_sentence:
        parts.append(fix_sentence)
    if reresolved["sentence"]:
        parts.append(reresolved["sentence"])
    underlying = sorted(getattr(result, "underlying_only", None) or ())
    if underlying:
        # An option on a future whose underlying the file does not trade: the future is written as
        # an instrument with no trade, so its price can be kept for the option's Greeks.
        parts.append(f"{len(underlying)} underlying future(s) of the options on futures written with no trade "
                     f"of their own: {', '.join(underlying)}.")
    excluded = []
    if result.n_skipped_other:
        excluded.append(f"{result.n_skipped_other} rows of unsupported type")
    if result.n_superseded:
        excluded.append(f"{result.n_superseded} earlier versions of repeated trade ids")
    if excluded:
        parts.append("Excluded: " + ", ".join(excluded) + ".")
    # The book filter (config/book.yaml: funds, traders, desks) and what it excluded, by
    # reason; n_skipped_status_or_fund is only their total, so it no longer gets a sentence.
    parts.append(result.filter_summary())
    # A reject the parser also loaded as UNRECOGNISED is a row that needs a fix, said above.
    fix_rows = {r["row_no"] for r in unrecognised_rows(result) if r["row_no"]}
    rejects = [rj for rj in result.rejects if rj.row_no not in fix_rows]
    if rejects:
        head = "; ".join(f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in rejects[:5])
        more = f" (+{len(rejects) - 5} more)" if len(rejects) > 5 else ""
        parts.append(f"{len(rejects)} row(s) {REJECTS_PHRASE} and were skipped: {head}{more}.")
    # Before the library sync, so the library lists each future's price at Bloomberg's date.
    dates_sentence = _contract_dates_sentence(db_path)
    if dates_sentence:
        parts.append(dates_sentence)
    library_sentence, library_tickers = _library_update(db_path)
    parts.append(library_sentence)
    # Possible duplicates: the same fill under two Trade Ids (flagged only, never dropped; 2026-09-30).
    try:
        with closing(schema.connect(Path(db_path).resolve())) as conn:
            dups = possible_duplicates(conn)
    except sqlite3.Error:
        dups = []
    if dups:
        parts.append(f"{len(dups)} possible duplicate fill{'s' if len(dups) != 1 else ''} on file (same date, "
                     "contract, side, size and price under different Trade Ids): nothing was dropped, "
                     "see the Data tab's Trades card.")
    notes = result.notes()
    message = " ".join(parts + notes)
    record_upload_report(db_path, filename, result, len(underlying), library_tickers, message, change,
                         file_rows_outcome(frame, filename, result, change))
    return {"message": message, "rejects": len(rejects),
            "warnings": len(result.warnings), "notes": notes,
            "added": change["added"], "replaced": change["replaced"], "removed": change["removed"],
            "removed_manual": change["removed_manual"], "on_file_after": change["on_file_after"],
            "need_fix": change["need_fix"], "need_fix_ids": [r["trade_id"] for r in need_fix],
            "need_fix_on_file": change["need_fix_on_file"],
            "reresolved": [r["trade_id"] for r in reresolved["resolved"]],
            "possible_duplicates": len(dups)}
