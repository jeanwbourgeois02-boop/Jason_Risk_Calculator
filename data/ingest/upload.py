"""Trade blotter CSV/Excel upload, staged separately from the live database.

Blotter-only (user decision 2026-09-17): this is the app's one and only upload input.
File reading, column canonicalisation and every per-row tolerance live in
``data/ingest/blotter.py`` (``read_table`` / ``parse`` / ``load``); this module adds the
size guard, the pre-Confirm shape check, the stage-then-publish transaction and the
summary message.

Full replace, not merge (user decision 2026-09-17): "when a new excel is put in - that's
the only input for the trades - all of the old stuff gets deleted - sample data and
previous excels - so there are no duplicates or fake things." A successful
``import_blotter`` call deletes every existing row of ``trades`` and its trade-keyed
dependents (``trade_legs``, ``realised_pnl`` -- see ``FULL_REPLACE_CHILD_TABLES``) for
EVERY source, including legacy ``source='BNP'`` rows
and a previous upload's (or the launcher sample's) rows, before writing the new file's
own trades. The one exception (2026-09-18) is ``source='MANUAL'``: trades booked by hand
on the Blotter's Manual entry sub-tab (``data/ingest/manual.py``) exist precisely
because the export does not carry them, so a new export can never be evidence that
they are gone -- they survive every upload and are removed only through
``manual.delete_manual_trade``. This is the app's upload path only: ``blotter.load`` itself (the library
function this module calls) keeps its own idempotent-by-``trade_id`` upsert behaviour
unchanged, for callers that still want a merge (e.g. a script loading several files that
together make up one book). Instruments, marks, curves, curve_quotes and index_fixings
are untouched -- keyed by instrument/date, not by trade (so the underlying future the parser
writes, with no trade, for an option on a future it does not trade itself stays on file across
uploads, and the parser's INSERT OR IGNORE never overwrites it). Deletion only happens after the
new file has parsed successfully (inside the same transaction as publishing its rows),
so a parse failure leaves the existing book completely intact; see ``_stage_and_publish``.
The one exception to "marks are untouched" (2026-09-24): once the book is published,
Bloomberg's stored contract dates are put back onto the commodity futures
(``data/ingest/contract_dates.py``), which moves a future's expiry, its NOTIONAL legs and
the key of its FUTURE_PX marks, never a value.

Retired 2026-09-24 (commodity conversion Phase 2, user yes): the FX-swap package rule
(``swaps.py``) no longer runs after an upload, so two blotter forwards stay two outright
forwards; and the upload no longer turns an interest rate swap's priced history round
(``irs_direction.py``), since rates left the app. An FX swap is still booked by hand
(``manual.book_fx_swap``).

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
# must be cleared before trades itself (FK-safe child-then-parent order) on a full-replace
# upload. realised_pnl is not in schema.TABLES (the generic per-table merge loop below
# never touches it), so it is deleted explicitly rather than through that loop. Nothing
# outside data/ingest/schema.py keys a table off trade_id: engine/options' tables are
# keyed by instrument_id, not trade_id (checked 2026-09-17), so they are untouched by a
# trade replace.
FULL_REPLACE_CHILD_TABLES = ("trade_legs", "realised_pnl")
FULL_REPLACE_TABLES = FULL_REPLACE_CHILD_TABLES + ("trades",)
# Retired tables that still reference trades on a database made before 2026-09-24
# (swap_review: the retired FX-swap package rule's ambiguous candidates). Cleared with the
# book when the table is there, so its foreign key never blocks the delete; nothing is
# written to it any more, and a database without it is fine.
RETIRED_CHILD_TABLES = ("swap_review",)
# Rows a full replace removes: every trade NOT booked by hand (see the module docstring
# on ``source='MANUAL'``). Child tables are filtered through this subquery, so it must
# run before the ``trades`` delete itself.
_REPLACED_TRADES_SQL = "SELECT trade_id FROM trades WHERE source != 'MANUAL'"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _delete_replaced_book(conn: sqlite3.Connection) -> None:
    """Delete every non-MANUAL trade and its trade-keyed dependents, children first."""
    for table in RETIRED_CHILD_TABLES:
        if _table_exists(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE trade_id IN ({_REPLACED_TRADES_SQL})")
    for table in FULL_REPLACE_CHILD_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE trade_id IN ({_REPLACED_TRADES_SQL})")
    conn.execute("DELETE FROM trades WHERE source != 'MANUAL'")


def _replaced_counts(conn: sqlite3.Connection) -> dict:
    """Pre-delete row counts per FULL_REPLACE_TABLES name, MANUAL trades excluded."""
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE trade_id IN ({_REPLACED_TRADES_SQL})").fetchone()[0]
              for t in FULL_REPLACE_CHILD_TABLES}
    counts["trades"] = conn.execute("SELECT COUNT(*) FROM trades WHERE source != 'MANUAL'").fetchone()[0]
    return counts


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


def _stage_and_publish(db_path, load_fn, full_replace: bool = False):
    """Hold a writer lock on `db_path` while staging against a snapshot of it in an
    in-memory DB, call `load_fn(staged_conn)`, then publish staged's rows into the live
    DB in one transaction. `load_fn` raises `ValueError` (or lets
    a `sqlite3.Error` propagate) on any failure before the publish step runs, and before
    anything is written to `live` -- so a parse/load failure leaves `live` untouched.

    `full_replace=False` (default): every table is an upsert-merge (INSERT ... ON
    CONFLICT DO UPDATE), so existing rows not present in `staged` are left alone.

    `full_replace=True` (``import_blotter``'s "one input, no leftovers" rule): after
    `load_fn` succeeds, every row of `FULL_REPLACE_TABLES` (trades and everything keyed
    off trade_id) is deleted from `live` -- every source except MANUAL (module
    docstring), not just rows whose id also appears in `staged` -- before the merge loop
    runs, so the merge becomes a plain insert of exactly the new file's trades (plus
    the untouched MANUAL rows, which the merge re-upserts unchanged). Returns `(result, replaced)` where
    `replaced` is a dict of pre-delete row counts per `FULL_REPLACE_TABLES` name (used
    for the "replaced N trades" summary); `replaced` is `{}` when `full_replace=False`.
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
                replaced = {}
                if full_replace:
                    # staged is a snapshot of live at this instant, so counting on
                    # either connection gives the same pre-delete totals.
                    replaced = _replaced_counts(staged)
                    # Clear staged's full-replace tables BEFORE load_fn runs: staged was
                    # seeded from the old book, so without this, load_fn only adds the
                    # new file's rows alongside the old ones still sitting in staged, and
                    # the merge loop below would copy both back into live -- undoing the
                    # live-side delete a few lines down instead of replacing anything.
                    _delete_replaced_book(staged)
                result = load_fn(staged)
                if full_replace:
                    _delete_replaced_book(live)
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
                live.commit()
        except Exception:
            live.rollback()
            raise
    return result, replaced


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


def loaded_breakdown(trades) -> str:
    """'29 futures, 4 options on futures, 3 LME forwards, 8 FX forwards, 1 FX spot, 5 FX options':
    the loaded trades by kind. Listed options (EQ_OPTION) and FX swaps are named only when there
    are some, every other kind always; a product this list does not know is named as it is
    stored, so no loaded trade goes uncounted."""
    counts: dict = {}
    for t in trades:
        counts[t.product] = counts.get(t.product, 0) + 1
    parts, known = [], set()
    for label, products, always in _LOADED_KINDS:
        known.update(products)
        n = sum(counts.get(p, 0) for p in products)
        if n or always:
            parts.append(f"{n} {label}")
    parts += [f"{n} {product}" for product, n in sorted(counts.items()) if product not in known]
    return ", ".join(parts)


def _library_sentence(db_path) -> str:
    """Bring the Bloomberg library (data/bloomberg/library.py: what the trades on file need
    from Bloomberg for their P&L) up to date with the book just published, and say what
    changed. An upload asks nothing of Bloomberg (user decision 2026-09-21); the next
    "Pull Bloomberg now" asks for exactly what is listed there."""
    try:
        from data.bloomberg import library
        with closing(schema.connect(Path(db_path).resolve())) as conn:
            changed = library.sync(conn)
            tickers = library.summary(conn)["tickers"]
    except Exception as exc:  # noqa: BLE001 -- the book is already published; a reader syncs the library itself
        return f"Bloomberg library not updated here ({exc}); it updates itself on the next pull."
    return (f"Bloomberg library: {tickers} ticker(s) needed today, {changed['added']} item(s) added, "
            f"{changed['removed']} removed. Nothing was pulled; press Pull Bloomberg now to price the book.")


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
    """Load a blotter file into `db_path`, replacing the entire existing trade book (see
    module docstring: every existing `trades` row and trade-keyed dependent, any source,
    is deleted first). Rows that cannot be parsed are skipped and listed in the returned
    message, in a sentence that always carries the words "could not be read"
    (REJECTS_PHRASE); everything else loads. Only a file with no recognisable blotter
    header at all is refused -- and refusing it never touches the existing book (the
    delete only happens after this file has parsed).

    Returns the one-paragraph message, exactly `import_blotter_report(...)["message"]`.
    A caller that needs to know whether anything was skipped or doubtful should call
    `import_blotter_report` and read its counts rather than this prose."""
    return import_blotter_report(payload, filename, db_path)["message"]


UPLOAD_ISSUES_DDL = ("CREATE TABLE IF NOT EXISTS upload_issues (row_no INTEGER NOT NULL, symbol TEXT NOT NULL, "
                     "kind TEXT NOT NULL, reason TEXT NOT NULL, filename TEXT NOT NULL, uploaded_at TEXT NOT NULL)")


def record_upload_issues(db_path, filename, result) -> int:
    """Every row of the uploaded file that did NOT become a trade, with why (user, 2026-09-21:
    "a zar option and spx option that just isnt in the table"): the parser's rejects and the
    rows of a type the app does not load. Replaced by each upload; shown on the Market data
    tab. Never fails an import: a database error here is swallowed."""
    import datetime as _dt
    rows = [(rj.row_no, rj.symbol or "", "REJECTED", rj.reason) for rj in getattr(result, "rejects", [])]
    rows += [(n, sym or "", "NOT LOADED", why) for n, sym, why in getattr(result, "skipped_other_rows", [])]
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

    result, replaced = _stage_and_publish(db_path, _load, full_replace=True)
    record_upload_issues(db_path, filename, result)
    n_trades, n_legs = len(result.trades), len(result.legs)
    parts = [f"Imported {filename}: {n_trades} trades -- {loaded_breakdown(result.trades)}; {n_legs} legs. "
             f"{result.n_currency} cash rows seen ({result.n_spot} of them spot fills)."]
    underlying = sorted(getattr(result, "underlying_only", None) or ())
    if underlying:
        # An option on a future whose underlying the file does not trade: the future is written as
        # an instrument with no trade, so its price can be kept for the option's Greeks.
        parts.append(f"{len(underlying)} underlying future(s) of the options on futures written with no trade "
                     f"of their own: {', '.join(underlying)}.")
    if replaced.get("trades"):
        parts.append(f"Replaced the previous book: {replaced['trades']} trade(s) and "
                     f"{replaced['trade_legs']} leg(s) removed.")
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
    parts.append(_library_sentence(db_path))
    notes = result.notes()
    return {"message": " ".join(parts + notes), "rejects": len(result.rejects),
            "warnings": len(result.warnings), "notes": notes}
