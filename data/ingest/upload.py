"""Trade blotter CSV/Excel upload, staged separately from the live database.

Blotter-only (user decision 2026-09-17): this is the app's one and only upload input
now. BNP support (``import_report``/``read_report``/``BNP_REQUIRED``/``detect_format``/
``sniff_format``/``suggested_date``) is removed from this module entirely -- it is not
just unreachable, it no longer exists here. ``data/ingest/bnp.py`` itself is untouched
and still used as a library by ``data/load.py``'s CLI and ``data/bloomberg/bnp_marks.py``
(neither goes through this module), and by ``data/ingest/blotter.py`` for shared
dataclasses/regexes -- none of that is affected by removing the upload path.

Flexibility (user decision 2026-09-17, "make it as flexible as possible"):
  - ``encoding='utf-8-sig'`` tolerates a leading UTF-8 byte-order-mark (very common in
    an Excel "CSV UTF-8" export) without corrupting the first column name.
  - Required-column matching is case-/whitespace-insensitive (``_normalized_columns``),
    so 'STATUS' or 'fin type' still matches -- a trivial header-casing difference must
    never be why a real file gets rejected.
  - ``_canonicalize_columns`` renames every column that matches one of the reference
    blotter header names case-insensitively to that reference's exact casing, so
    ``data/ingest/blotter.py``'s own (case-sensitive) ``row.get('Status')``-style
    lookups still work regardless of how the source file capitalised its header row.
    An unrecognised column is left exactly as-is and simply ignored downstream (this
    format's parser looks up columns by name, never by position, so extra/reordered/
    unknown columns are harmless).
  - A single-sheet Excel workbook needs no sheet argument; only an ambiguous multi-sheet
    workbook asks the caller to pick one.
"""
from __future__ import annotations

from contextlib import closing
from io import BytesIO
from pathlib import Path
import base64
import sqlite3
from tempfile import TemporaryDirectory

import pandas as pd

from data.ingest import blotter, schema, swaps

MAX_BYTES = 25 * 1024 * 1024

# Minimal, distinctive column signature for the blotter format, matched
# case-/whitespace-insensitively via _normalized_columns.
BLOTTER_REQUIRED = {'status', 'fund', 'fin type', 'trade id', 'symbol'}

# Reference header, exactly as data/raw/new_sample_trades.csv's own header row (the
# canonical casing data/ingest/blotter.py's row.get(...) calls expect). Used only to
# re-case an incoming file's columns that match case-insensitively; never to reject a
# file for having extra, missing or reordered columns.
_REFERENCE_HEADER = (
    "Status,Firm,Client Domicile,Description,Side,Fin Type,Trade Id,Version,RollSide,"
    "Swap ID,Symbol,Underlying Symbol,Quantity,Price,Yield,Total Fees,Accrued Fees,"
    "Counterparty,Execution Venue,Counterparty Desc,Trader,Clearing Cpty,"
    "Trade Request ID,Desk,Fund,PBRoot,Position Block,TradeDate,NotificationID,"
    "Email Notification Status,Is Swap,Currency,Valuation Currency,NetInvoice,Notes,"
    "CCP/Confirm ID,Commission,Commission Type,Settle Type,Settle Date,PaymentDate,"
    "Swap Type,Dividend,Spread,FixedRate,Notional,IsSellOfBook,Invoice,Invoice Comm,"
    "QtyFactor,Region,OTC Type,Oasys Ref Id,External Ref Id,Tran Type,"
    "CDS Classification,Effective Date,Termination,CreateDate,LastModified,ModifiedBy,"
    "Account Type,ExtAccount,Sub Account,PSET Code,Is Excess Return,Counterparty Id,"
    "Alt Src,Instrument Id,Product,CUSIP,SEDOL,LoanxID,ISIN,BB_YK_IDENTIFIER,FOID,"
    "BusinessLine,TradeGroup,TrailerTradeId,Error Message,PositionType Id,"
    "Repo Interest Index,Order Id,Cut Time,Cut Location,Repo Term Date,Close Date,"
    "Repo Financing Interest,Repo Interest Rate,Premium,Adj. Expiry Date,Factor,"
    "Original Face,Current Face,Gross Amnt/Principal,Accrued Interest,"
    "External Execution Id,Settlement Status,Created By,Currency Pair,Buy Currency,"
    "Sell Currency,BuyCurrency Amount,SellCurrency Amount,PayLegPmtFreq,"
    "RecvLegPmtFreq,PayLegDCF,RecvLeg DCF,FxOption Type,PM Name,CPI Factor"
).split(",")
_CANONICAL_BY_CASEFOLD = {c.casefold(): c for c in _REFERENCE_HEADER}


def _normalized_columns(columns) -> set:
    return {str(c).strip().casefold() for c in columns}


def _canonicalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename any column matching a reference header name case-insensitively to that
    reference's exact casing; leave anything else untouched. Never drops a column."""
    rename = {}
    for col in frame.columns:
        canonical = _CANONICAL_BY_CASEFOLD.get(str(col).strip().casefold())
        if canonical is not None and canonical != col:
            rename[col] = canonical
    return frame.rename(columns=rename) if rename else frame


def _parse_frame(payload: bytes, filename: str, sheet=None) -> pd.DataFrame:
    """CSV/Excel -> DataFrame with columns stripped and canonically re-cased."""
    suffix = Path(filename).suffix.lower()
    if suffix == '.csv':
        frame = pd.read_csv(BytesIO(payload), encoding='utf-8-sig')
    elif suffix in ('.xlsx', '.xlsm', '.xls'):
        with pd.ExcelFile(BytesIO(payload)) as book:
            if not sheet:
                # A single-sheet workbook needs no picker at all; only ask when there's
                # a real choice to make.
                if len(book.sheet_names) == 1:
                    sheet = book.sheet_names[0]
                else:
                    raise ValueError(
                        'This workbook has more than one sheet; choose which one to import.')
            frame = pd.read_excel(book, sheet_name=sheet)
    else:
        raise ValueError('Choose a CSV, XLSX, XLSM or XLS file.')
    frame.columns = [str(c).strip() for c in frame.columns]
    return _canonicalize_columns(frame)


def preview_frame(payload: bytes, filename: str, sheet=None) -> pd.DataFrame:
    """Public entry point for a pre-Confirm preview (the UI's file-picked callback):
    parse just far enough to validate shape and show the file name, without writing
    anything. Confirm-time loading (`import_blotter`) re-parses from scratch rather
    than reusing this result, so this is never itself a source of truth for what gets
    written."""
    return _parse_frame(payload, filename, sheet)


def validate_blotter_shape(frame: pd.DataFrame) -> None:
    """Raise a clean ValueError if `frame` doesn't look like a trade blotter (checked
    case-/whitespace-insensitively). Called for an early preview before Confirm, and
    again inside import_blotter as cheap defence in depth."""
    missing = BLOTTER_REQUIRED - _normalized_columns(frame.columns)
    if missing:
        raise ValueError('This file is not a trade blotter. Missing columns: ' + ', '.join(sorted(missing)))


def decode(contents):
    if not contents or len(contents) > MAX_BYTES * 4 // 3 + 1024:
        raise ValueError('Choose a file smaller than 25 MB.')
    payload = base64.b64decode(contents.split(',', 1)[1], validate=True)
    if len(payload) > MAX_BYTES:
        raise ValueError('Choose a file smaller than 25 MB.')
    return payload


def _stage_and_publish(db_path, load_fn):
    """Hold a writer lock on `db_path` while staging against a read-only snapshot in an
    in-memory DB, call `load_fn(staged_conn)` against it, then copy every table's rows
    into the live DB in one transaction (`INSERT ... ON CONFLICT DO UPDATE`, so a
    re-upload is idempotent) and run swap packaging. `load_fn` owns all loader-specific
    behaviour -- raising `ValueError` on any failure (a bad file shape, a duplicate key
    on re-upload) -- before this function's generic publish step runs. Returns whatever
    `load_fn` returns, unexamined."""
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
                # Swap packaging runs at the end of every upload (CLAUDE.md package_id
                # rule); it only touches trades still at product='FX_FWD', so it is
                # safe to run against the whole live table, not just the rows just
                # imported.
                swaps.package_swaps(live)
        except Exception:
            live.rollback()
            raise
    return result


def import_blotter(payload, filename, db_path, sheet=None):
    """Reject the whole upload on any validation error or a duplicate key on re-upload.
    Staging/publish safety pattern lives in `_stage_and_publish`.

    No `as_of`: blotter rows carry their own `TradeDate` / settle dates, so there is no
    single snapshot date to ask for, and this format writes no `positions` rows
    (CURRENCY rows here are settlement-level cash movements, not an EOD balance -- see
    blotter.py's module docstring).
    """
    frame = _parse_frame(payload, filename, sheet)
    validate_blotter_shape(frame)
    with TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / Path(filename).with_suffix('.csv').name
        frame.to_csv(csv_path, index=False)

        def _load(staged):
            try:
                # strict=False (2026-09-17, "as flexible as possible"): one malformed
                # row anywhere in the file must never block every other good row --
                # rows that fail to parse are still never inserted (reject-not-coerce
                # is preserved per-row), but the rest of a large file loads regardless.
                # The rejected rows are still fully visible in the returned message,
                # never silently dropped.
                return blotter.load(csv_path, staged, strict=False)
            except sqlite3.IntegrityError as e:
                # blotter.load has no idempotent re-upload mode yet (plain INSERT,
                # documented in its own docstring): a duplicate trade_id/leg key raises
                # here rather than in blotter.load itself. Turn it into a clean
                # ValueError shape rather than a raw sqlite traceback.
                raise ValueError(f'Nothing imported. Duplicate key on re-upload: {e}') from e

        result = _stage_and_publish(db_path, _load)
    n_trades, n_legs = len(result.trades), len(result.legs)
    rejected = ""
    if result.rejects:
        head = "; ".join(f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in result.rejects[:5])
        more = f" (+{len(result.rejects) - 5} more)" if len(result.rejects) > 5 else ""
        rejected = f" {len(result.rejects)} row(s) could not be parsed and were skipped: {head}{more}."
    return (f"Imported {filename}: {n_trades} trades ({result.n_forward} forwards, {result.n_future} futures, "
            f"{result.n_option} options, {n_trades - result.n_forward - result.n_future - result.n_option} rate swaps), "
            f"{n_legs} legs. {result.n_currency} currency rows seen (no position snapshot -- this file has no "
            f"EOD balance grain). Excluded: {result.n_skipped_status_or_fund} rows from other funds/status, "
            f"{result.n_skipped_irs} malformed IRS rows, {result.n_skipped_other} other unsupported rows.{rejected}")
