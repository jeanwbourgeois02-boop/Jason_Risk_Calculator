"""Trade blotter CSV/Excel upload, staged separately from the live database.

Blotter-only (user decision 2026-09-17): this is the app's one and only upload input.
File reading, column canonicalisation and every per-row tolerance live in
``data/ingest/blotter.py`` (``read_table`` / ``parse`` / ``load``); this module adds the
size guard, the pre-Confirm shape check, the stage-then-publish transaction and the
summary message. A re-upload of the same or a newer export is idempotent: trades with an
id already in the database are replaced, everything else is added.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import base64
import sqlite3

import pandas as pd

from data.ingest import blotter, schema, swaps

MAX_BYTES = 25 * 1024 * 1024

# What a sheet must carry to be treated as a blotter, matched on the canonical names
# blotter.read_table produces. 'Fin Type' may be absent when 'Product' is present.
BLOTTER_REQUIRED = {"Symbol", "Trade Id"}
BLOTTER_KIND_COLUMNS = {"Fin Type", "Product"}


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
    in-memory DB, call `load_fn(staged_conn)`, then upsert every table's rows into the
    live DB in one transaction and run swap packaging. `load_fn` raises `ValueError` on
    any failure before the publish step runs."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(schema.connect(db_path)) as live:
        live.execute("BEGIN IMMEDIATE")
        try:
            with closing(sqlite3.connect(":memory:")) as staged:
                with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as reader:
                    reader.backup(staged)
                result = load_fn(staged)
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
                swaps.package_swaps(live)
        except Exception:
            live.rollback()
            raise
    return result


def import_blotter(payload, filename, db_path):
    """Load a blotter file into `db_path`. Rows that cannot be parsed are skipped and
    listed in the returned message; everything else loads. Only a file with no
    recognisable blotter header at all is refused."""
    frame = blotter.read_table(payload, filename)
    validate_blotter_shape(frame)

    def _load(staged):
        try:
            return blotter.load(frame, staged, strict=False, filename=filename)
        except sqlite3.Error as e:
            raise ValueError(f"Nothing imported. Database error: {e}") from e

    result = _stage_and_publish(db_path, _load)
    n_trades, n_legs = len(result.trades), len(result.legs)
    n_new = n_trades - result.n_updated
    parts = [f"Imported {filename}: {n_trades} trades ({n_new} new, {result.n_updated} updated) -- "
             f"{result.n_forward} forwards, {result.n_future} futures, {result.n_option} options, "
             f"{result.n_irs} rate swaps; {n_legs} legs. {result.n_currency} cash rows seen."]
    excluded = []
    if result.n_skipped_status_or_fund:
        excluded.append(f"{result.n_skipped_status_or_fund} rows from other funds or cancelled/pending status")
    if result.n_skipped_other:
        excluded.append(f"{result.n_skipped_other} rows of unsupported type")
    if result.n_superseded:
        excluded.append(f"{result.n_superseded} earlier versions of repeated trade ids")
    if excluded:
        parts.append("Excluded: " + ", ".join(excluded) + ".")
    if result.rejects:
        head = "; ".join(f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in result.rejects[:5])
        more = f" (+{len(result.rejects) - 5} more)" if len(result.rejects) > 5 else ""
        parts.append(f"{len(result.rejects)} row(s) could not be read and were skipped: {head}{more}.")
    return " ".join(parts)
