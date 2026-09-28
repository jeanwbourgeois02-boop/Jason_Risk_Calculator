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

``import_blotter_report`` returns the outcome as data (message, rejects, warnings,
notes) so the UI decides from counts, not from prose; ``import_blotter`` is its message.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import base64
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
      a `trades.theme` set by hand is kept where the file's row carries none;
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
                incoming = list(dict.fromkeys(t.trade_id for t in getattr(result, "trades", ())))
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
                 ("FX options", ("FX_OPTION",), True))


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
    database's manual entries removed are named in a second clause; nothing else is."""
    text = (f"{n_in_file} trades in the file: {change.get('added', 0)} added, "
            f"{change.get('replaced', 0)} already on file (replaced by the file's rows), "
            f"{change.get('removed', 0)} removed as cancelled")
    if change.get("removed_manual"):
        text += f", {change['removed_manual']} manual entr{'y' if change['removed_manual'] == 1 else 'ies'} removed"
    return f"{text}; {change.get('on_file_after', 0)} trades on file."


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
    not name stays; an old database's MANUAL rows are removed. Rows that cannot be parsed
    are skipped and listed in the returned message, in a sentence that always carries the
    words "could not be read" (REJECTS_PHRASE); everything else loads. Only a file with no
    recognisable blotter header at all is refused -- and refusing it never touches the
    existing book (every write happens only after this file has parsed).

    Returns the one-paragraph message, exactly `import_blotter_report(...)["message"]`.
    A caller that needs to know whether anything was skipped or doubtful should call
    `import_blotter_report` and read its counts rather than this prose."""
    return import_blotter_report(payload, filename, db_path)["message"]


UPLOAD_ISSUES_DDL = ("CREATE TABLE IF NOT EXISTS upload_issues (row_no INTEGER NOT NULL, symbol TEXT NOT NULL, "
                     "kind TEXT NOT NULL, reason TEXT NOT NULL, filename TEXT NOT NULL, uploaded_at TEXT NOT NULL)")


def record_upload_issues(db_path, filename, result) -> int:
    """Every row of the uploaded file that did NOT become a trade, with why (user, 2026-09-21:
    "a zar option and spx option that just isnt in the table"): the parser's rejects and the
    rows of a type the app does not load. Since 2026-09-28 also the parser's file-level
    warnings (``ParseWarning`` with ``row_no == 0``: a sentence about the file as a whole,
    such as two strategy labels that look like one strategy), as kind WARNING with symbol
    '', so the Book tab's "Last load" and the Data tab show them as rows and not only inside
    the summary; a row-level warning (``row_no >= 2``) stays in the summary sentence only.
    Replaced by each upload; shown on the Market data tab. Never fails an import: a database
    error here is swallowed."""
    import datetime as _dt
    rows = [(rj.row_no, rj.symbol or "", "REJECTED", rj.reason) for rj in getattr(result, "rejects", [])]
    rows += [(n, sym or "", "NOT LOADED", why) for n, sym, why in getattr(result, "skipped_other_rows", [])]
    rows += [(0, "", "WARNING", w.message) for w in getattr(result, "warnings", [])
             if getattr(w, "row_no", None) == 0]
    stamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(str(db_path), timeout=60)
        try:
            with conn:
                conn.execute(UPLOAD_ISSUES_DDL)
                conn.execute("DELETE FROM upload_issues")
                conn.executemany("INSERT INTO upload_issues VALUES (?,?,?,?,?,?)",
                                 [(n, sym, kind, why, str(filename), stamp) for n, sym, kind, why in rows])
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
                     "removed INTEGER NOT NULL DEFAULT 0, on_file_after INTEGER NOT NULL DEFAULT 0)")
UPLOAD_REPORT_COLUMNS = ("filename", "uploaded_at", "futures", "options_on_futures", "lme_forwards",
                         "fx_forwards", "fx_spot", "fx_options", "excluded_rows", "excluded_text",
                         "underlying_futures_written", "library_tickers", "summary",
                         # the merge by Trade Id (2026-09-28): trades added, replaced by the file's
                         # rows, removed as cancelled, and the whole book's count after the upload
                         "added", "replaced", "removed", "on_file_after")
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
                         change: dict | None = None) -> bool:
    """Persist the load summary of the upload just published (`upload_report`, one row replacing
    the previous one). The kind counts are `loaded_breakdown`'s (the trades that LOADED, never
    the parser's row counters); `excluded_rows` is the book filter's total (status, fund, trader,
    desk) with `filter_summary()` as its wording; `change` is `_stage_and_publish`'s merge
    accounting (`added`, `replaced`, `removed`, `on_file_after`; zeros when not given). Never
    fails an import: a database error is swallowed and False returned."""
    import datetime as _dt
    counts = loaded_counts(result.trades)
    change = change or {}
    row = {"filename": str(filename),
           "uploaded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           **{col: counts.get(product, 0) for col, product in _REPORT_KIND_COLUMNS},
           "excluded_rows": int(getattr(result, "n_skipped_status_or_fund", 0) or 0),
           "excluded_text": result.filter_summary(),
           "underlying_futures_written": int(n_underlying),
           "library_tickers": int(library_tickers),
           "summary": summary,
           **{col: int(change.get(col, 0) or 0) for col in ("added", "replaced", "removed", "on_file_after")}}
    names = ",".join(UPLOAD_REPORT_COLUMNS)
    try:
        conn = sqlite3.connect(str(db_path), timeout=60)
        try:
            with conn:
                _migrate_report_table(conn)
                conn.execute("DELETE FROM upload_report")
                conn.execute(f"INSERT INTO upload_report ({names}) VALUES ({','.join('?' for _ in UPLOAD_REPORT_COLUMNS)})",
                             [row[c] for c in UPLOAD_REPORT_COLUMNS])
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

    INFORMATION is in `notes` and `message` but never raises `warnings`: an option with
    no strike in the file, which the Blotter's missing-terms banner shows persistently, so
    an import that only has that is a clean one.

    The first sentence counts the trades the file loaded, by kind (`loaded_breakdown`), so
    a rejected row is never in it; the rejects have their own sentence."""
    frame = blotter.read_table(payload, filename)
    validate_blotter_shape(frame)

    def _load(staged):
        try:
            return blotter.load(frame, staged, strict=False, filename=filename)
        except sqlite3.Error as e:
            raise ValueError(f"Nothing imported. Database error: {e}") from e

    result, change = _stage_and_publish(db_path, _load)
    record_upload_issues(db_path, filename, result)
    n_trades, n_legs = len(result.trades), len(result.legs)
    parts = [f"Imported {filename}: {n_trades} trades -- {loaded_breakdown(result.trades)}; {n_legs} legs. "
             f"{result.n_currency} cash rows seen ({result.n_spot} of them spot fills).",
             merge_sentence(n_trades, change)]
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
    if result.rejects:
        head = "; ".join(f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in result.rejects[:5])
        more = f" (+{len(result.rejects) - 5} more)" if len(result.rejects) > 5 else ""
        parts.append(f"{len(result.rejects)} row(s) {REJECTS_PHRASE} and were skipped: {head}{more}.")
    # Before the library sync, so the library lists each future's price at Bloomberg's date.
    dates_sentence = _contract_dates_sentence(db_path)
    if dates_sentence:
        parts.append(dates_sentence)
    library_sentence, library_tickers = _library_update(db_path)
    parts.append(library_sentence)
    notes = result.notes()
    message = " ".join(parts + notes)
    record_upload_report(db_path, filename, result, len(underlying), library_tickers, message, change)
    return {"message": message, "rejects": len(result.rejects),
            "warnings": len(result.warnings), "notes": notes,
            "added": change["added"], "replaced": change["replaced"], "removed": change["removed"],
            "removed_manual": change["removed_manual"], "on_file_after": change["on_file_after"]}
