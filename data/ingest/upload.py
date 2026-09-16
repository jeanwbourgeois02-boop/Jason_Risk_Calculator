"""Validated BNP CSV/Excel uploads, staged separately from the live database."""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import date
from io import BytesIO
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

import pandas as pd

from data.ingest import bnp, blotter, schema, swaps
from data.load import _load_bnp_marks_idempotent

MAX_BYTES = 25 * 1024 * 1024
BNP_REQUIRED = set('Fund|Financial Type|Symbol|Symbol Description|Currency|Quantity|Local Cost|Price|Fx|Market Value Local|Market Value Base|DTD Total P&L|DTD Trading P&L|MTD Total P&L|YTD Total P&L|Start Date Dirty MV|Previous Month End Market Value Base|Position|Trade Factor|Account|CounterParty|NM Strategy|Trader Name|Cost'.split('|'))
REQUIRED = BNP_REQUIRED  # back-compat alias; nothing outside this module imports the old name (grepped 2026-09-16)

# Minimal, distinctive column signature for the blotter format (data/ingest/blotter.py).
# 'Fin Type' and 'Trade Id' in particular do not appear in the BNP file (which has
# 'Financial Type' and no 'Trade Id' column), so BNP and BLOTTER frames can never both
# match.
BLOTTER_REQUIRED = {'Status', 'Fund', 'Fin Type', 'Trade Id', 'Symbol'}


def detect_format(columns):
    """Sniff which importer a dropped file belongs to, from its column names alone
    (never the filename -- BNP files are not reliably named, and blotter exports
    have no fixed name at all).

    ``columns`` is any iterable of column-name strings (e.g. ``frame.columns``).
    Returns ``'bnp'`` if BNP_REQUIRED is a subset of ``columns``, ``'blotter'`` if
    BLOTTER_REQUIRED is a subset, or ``None`` if neither matches (unrecognized or
    ambiguous file -- caller should ask the user or reject the upload). The two
    signatures are disjoint (see BLOTTER_REQUIRED's comment above), so a single
    file can never match both.
    """
    columns = set(columns)
    if BNP_REQUIRED <= columns:
        return 'bnp'
    if BLOTTER_REQUIRED <= columns:
        return 'blotter'
    return None


def sniff_format(payload: bytes, filename: str, sheet=None):
    """Parse just far enough to tell BNP from blotter from an unconfirmed upload, for
    the UI to decide which controls (date picker or not) to show before Confirm is
    pressed. Returns ``(format, frame)`` where ``format`` is ``'bnp'``, ``'blotter'``
    or ``None`` (unrecognized). Confirm-time loading still goes through
    ``import_report``/``import_blotter``, which re-validate from scratch -- this is a
    preview only, never itself a source of truth for what gets written."""
    frame = _parse_frame(payload, filename, sheet)
    return detect_format(frame.columns), frame


def suggested_date(filename):
    """Snapshot date from an HA_PNL_YYYYMMDD file name (that date minus one weekday).
    A browser-download suffix after the date ('HA_PNL_20260915[22].csv',
    'HA_PNL_20260915 (1).csv') is accepted; a ninth digit is not."""
    match = re.search(r'HA_PNL_(\d{8})(?!\d).*\.(csv|xlsx|xlsm|xls)$', filename or '', re.I)
    if not match:
        return None
    try:
        return bnp._previous_weekday(date.fromisoformat(match[1])).isoformat()
    except ValueError:
        return None


def _parse_frame(payload: bytes, filename: str, sheet=None) -> pd.DataFrame:
    """Format-agnostic CSV/Excel -> DataFrame, shared by the BNP and blotter paths."""
    suffix = Path(filename).suffix.lower()
    if suffix == '.csv':
        frame = pd.read_csv(BytesIO(payload))
    elif suffix in ('.xlsx', '.xlsm', '.xls'):
        with pd.ExcelFile(BytesIO(payload)) as book:
            if not sheet:
                raise ValueError('Select an Excel worksheet before importing.')
            frame = pd.read_excel(book, sheet_name=sheet)
    else:
        raise ValueError('Choose a CSV, XLSX, XLSM or XLS file.')
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def read_report(payload: bytes, filename: str, sheet=None):
    frame = _parse_frame(payload, filename, sheet)
    missing = BNP_REQUIRED - set(frame.columns)
    if missing:
        raise ValueError('This sheet is not a BNP position report. Missing columns: ' + ', '.join(sorted(missing)))
    if frame.empty or not (frame['Fund'] == bnp.FUND).any():
        raise ValueError('The report contains no NMMF rows.')
    return frame


def decode(contents):
    if not contents or len(contents) > MAX_BYTES * 4 // 3 + 1024:
        raise ValueError('Choose a file smaller than 25 MB.')
    payload = base64.b64decode(contents.split(',', 1)[1], validate=True)
    if len(payload) > MAX_BYTES:
        raise ValueError('Choose a file smaller than 25 MB.')
    return payload


def _stage_and_publish(db_path, load_fn):
    """Shared staging/publish safety pattern for both `import_report` and
    `import_blotter` (factored out 2026-09-16 -- the two were near-identical copies of
    this same generic-upsert-plus-swap-packaging block, which meant every blotter
    upload paid an upsert pass over every table, not just the ones blotter.load()
    writes, and any future fix to the merge logic had to be made twice).

    Hold a writer lock on `db_path` while staging against a read-only snapshot in an
    in-memory DB, call `load_fn(staged_conn)` against it, then copy every table's rows
    into the live DB in one transaction (`INSERT ... ON CONFLICT DO UPDATE`, so a
    re-upload is idempotent) and run swap packaging. `load_fn` owns all
    loader-specific behaviour -- calling the right parser, and raising `ValueError` on
    any loader-specific failure (a bad file shape, conflicting existing rows, a
    duplicate key on re-upload) -- before this function's generic publish step runs.
    Returns whatever `load_fn` returns, unexamined, so each caller can shape its own
    return value (a plain loader result, or a tuple with extra counts).
    """
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(schema.connect(db_path)) as live:
        live.execute('BEGIN IMMEDIATE')
        try:
            with closing(sqlite3.connect(':memory:')) as staged:
                with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as reader:
                    reader.backup(staged)
                result = load_fn(staged)
                for table in schema.TABLES:
                    # Column names are double-quoted throughout: curve_quotes.index is a
                    # reserved word in bare SQL and would otherwise break this generic copy.
                    columns = [r[1] for r in staged.execute(f'PRAGMA table_info({table})')]
                    quoted = [f'"{c}"' for c in columns]
                    names = ','.join(quoted)
                    updates = ','.join(f'{q}=excluded.{q}' for q in quoted)
                    placeholders = ','.join('?' for _ in columns)
                    live.executemany(
                        f'INSERT INTO {table} ({names}) VALUES ({placeholders}) ON CONFLICT DO UPDATE SET {updates}',
                        staged.execute(f'SELECT {names} FROM {table}'))
                live.commit()
                # Swap packaging runs at the end of every upload regardless of source
                # (CLAUDE.md package_id rule); it only touches trades still at
                # product='FX_FWD', so it is safe to run against the whole live table,
                # not just the rows just imported.
                swaps.package_swaps(live)
        except Exception:
            live.rollback()
            raise
    return result


def import_report(payload, filename, as_of, db_path, sheet=None):
    """Reject the whole upload on any validation error or conflicting existing row.
    Staging/publish safety pattern lives in `_stage_and_publish`."""
    as_of = date.fromisoformat(as_of).isoformat()
    frame = read_report(payload, filename, sheet)
    with TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / ('HA_PNL_' + as_of.replace('-', '') + '.csv')
        frame.to_csv(csv_path, index=False)

        def _load(staged):
            result = bnp.load(csv_path, staged, as_of_date=as_of, strict=True, on_duplicate='skip')
            loaded, skipped, conflicts, rejects, details = _load_bnp_marks_idempotent(csv_path, staged, as_of)
            errors = result.conflict_details + rejects + details
            if errors:
                raise ValueError('Nothing imported. ' + ' | '.join(errors[:10]))
            return result, loaded, skipped

        result, loaded, skipped = _stage_and_publish(db_path, _load)
    counts = {
        'trades': len(result.trades) - result.skipped['trades'],
        'positions': len(result.positions) - result.skipped['positions'],
        'marks': loaded,
    }
    closed = (f"{result.n_forward_closed} closed FORWARD lines (quantity 0: NDF fixed or settled) kept as "
              f"positions with their P&L, no trades. " if result.n_forward_closed else "")
    return (f"Imported {as_of}: {counts['trades']} new trades, {counts['positions']} new positions, "
            f"{counts['marks']} new BNP marks. {closed}"
            f"{sum(result.skipped.values()) + skipped} identical records already present. "
            f"Excluded: {result.n_skipped_irs} malformed IRS rows, {result.n_skipped_other} other unsupported rows, "
            f"{result.n_skipped_fund} rows from other funds. Futures are positions only.")


def import_blotter(payload, filename, db_path, sheet=None):
    """Same staging/publish safety pattern as ``import_report`` (factored out into
    ``_stage_and_publish``), for the transaction-level blotter format
    (``data/ingest/blotter.py``). No ``as_of``: blotter rows carry their own
    ``TradeDate`` / settle dates, so there is no single snapshot date to ask for, and this
    format writes no ``positions`` rows (CURRENCY rows here are settlement-level cash
    movements, not an EOD balance -- see blotter.py's module docstring).
    """
    frame = _parse_frame(payload, filename, sheet)
    missing = BLOTTER_REQUIRED - set(frame.columns)
    if missing:
        raise ValueError('This file is not a trade blotter. Missing columns: ' + ', '.join(sorted(missing)))
    with TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / Path(filename).with_suffix('.csv').name
        frame.to_csv(csv_path, index=False)

        def _load(staged):
            try:
                return blotter.load(csv_path, staged, strict=True)
            except sqlite3.IntegrityError as e:
                # blotter.load has no idempotent re-upload mode yet (plain INSERT,
                # documented in its own docstring): a duplicate trade_id/leg key raises
                # here rather than in blotter.load itself. Turn it into the same clean
                # ValueError shape import_report already uses for loader failures.
                raise ValueError(f'Nothing imported. Duplicate key on re-upload: {e}') from e

        result = _stage_and_publish(db_path, _load)
    n_trades, n_legs = len(result.trades), len(result.legs)
    return (f"Imported {filename}: {n_trades} trades ({result.n_forward} forwards, {result.n_future} futures, "
            f"{result.n_option} options, {n_trades - result.n_forward - result.n_future - result.n_option} rate swaps), "
            f"{n_legs} legs. {result.n_currency} currency rows seen (no position snapshot -- this file has no "
            f"EOD balance grain). Excluded: {result.n_skipped_status_or_fund} rows from other funds/status, "
            f"{result.n_skipped_irs} malformed IRS rows, {result.n_skipped_other} other unsupported rows.")
