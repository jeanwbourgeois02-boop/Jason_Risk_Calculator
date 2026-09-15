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

from data.ingest import bnp, schema, swaps
from data.load import _load_bnp_marks_idempotent

MAX_BYTES = 25 * 1024 * 1024
REQUIRED = set('Fund|Financial Type|Symbol|Symbol Description|Currency|Quantity|Local Cost|Price|Fx|Market Value Local|Market Value Base|DTD Total P&L|DTD Trading P&L|MTD Total P&L|YTD Total P&L|Start Date Dirty MV|Previous Month End Market Value Base|Position|Trade Factor|Account|CounterParty|NM Strategy|Trader Name|Cost'.split('|'))


def suggested_date(filename):
    match = re.search(r'HA_PNL_(\d{8})\.(csv|xlsx|xlsm|xls)$', filename or '', re.I)
    if not match:
        return None
    try:
        return bnp._previous_weekday(date.fromisoformat(match[1])).isoformat()
    except ValueError:
        return None


def read_report(payload: bytes, filename: str, sheet=None):
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
    missing = REQUIRED - set(frame.columns)
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


def import_report(payload, filename, as_of, db_path, sheet=None):
    """Reject the whole upload on any validation error or conflicting existing row.

    Hold a writer lock while staging against a read-only snapshot. Existing loaders
    commit only to the memory copy. Publish validated rows in one live transaction.
    """
    as_of = date.fromisoformat(as_of).isoformat()
    frame = read_report(payload, filename, sheet)
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp, closing(schema.connect(db_path)) as live:
        csv_path = Path(tmp) / ('HA_PNL_' + as_of.replace('-', '') + '.csv')
        frame.to_csv(csv_path, index=False)
        live.execute('BEGIN IMMEDIATE')
        try:
            with closing(sqlite3.connect(':memory:')) as staged:
                with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as reader:
                    reader.backup(staged)
                result = bnp.load(csv_path, staged, as_of_date=as_of, strict=True, on_duplicate='skip')
                loaded, skipped, conflicts, rejects, details = _load_bnp_marks_idempotent(csv_path, staged, as_of)
                errors = result.conflict_details + rejects + details
                if errors:
                    raise ValueError('Nothing imported. ' + ' | '.join(errors[:10]))
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
                # Swap packaging runs at the end of every BNP upload (CLAUDE.md package_id
                # rule); it only touches trades still at product='FX_FWD', so it is safe to
                # run against the whole live table, not just the rows just imported.
                swaps.package_swaps(live)
        except Exception:
            live.rollback()
            raise
    counts = {
        'trades': len(result.trades) - result.skipped['trades'],
        'positions': len(result.positions) - result.skipped['positions'],
        'marks': loaded,
    }
    return (f"Imported {as_of}: {counts['trades']} new trades, {counts['positions']} new positions, "
            f"{counts['marks']} new BNP marks. "
            f"{sum(result.skipped.values()) + skipped} identical records already present. "
            f"Excluded: {result.n_skipped_irs} malformed IRS rows, {result.n_skipped_other} other unsupported rows, "
            f"{result.n_skipped_fund} rows from other funds. Futures are positions only.")
