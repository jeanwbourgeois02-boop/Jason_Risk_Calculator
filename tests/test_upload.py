"""Tests for data/ingest/upload.py -- blotter-only upload. File-shape tolerance
(encodings, delimiters, header casing, preamble rows, Excel, re-upload) is exercised
here end to end through import_blotter; per-row parsing lives in test_blotter.py."""
from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from data.ingest import blotter
from data.ingest.upload import import_blotter, preview_frame, validate_blotter_shape

RAW_BLOTTER = Path(__file__).resolve().parents[1] / 'data/sample/blotter_sample.csv'
EXPECTED_TRADES, EXPECTED_LEGS = 772, 1525


def trade_count(db):
    with sqlite3.connect(db) as conn:
        return (conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM trade_legs').fetchone()[0])


def sample_frame():
    return blotter.read_table(RAW_BLOTTER)


# --------------------------------------------------------------------------- shape validation

def test_validate_blotter_shape_accepts_superset_of_required_columns():
    validate_blotter_shape(pd.DataFrame(columns=['Symbol', 'Trade Id', 'Fin Type', 'Something Extra']))


def test_validate_blotter_shape_accepts_product_in_place_of_fin_type():
    validate_blotter_shape(pd.DataFrame(columns=['symbol', 'TRADE ID', 'Product']))


def test_validate_blotter_shape_rejects_missing_columns():
    with pytest.raises(ValueError, match='not a trade blotter'):
        validate_blotter_shape(pd.DataFrame(columns=['Foo', 'Bar', 'Baz']))


def test_preview_frame_refuses_file_with_no_blotter_header():
    with pytest.raises(ValueError, match='not a trade blotter'):
        preview_frame(b'just,some,numbers\n1,2,3\n', 'x.csv')


# --------------------------------------------------------------------------- import

def test_import_blotter_loads_real_file(tmp_path):
    db = tmp_path / 'risk.db'
    message = import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)
    assert f'{EXPECTED_TRADES} trades ({EXPECTED_TRADES} new, 0 updated)' in message
    assert 'could not be read' not in message


def test_import_blotter_reupload_is_idempotent_and_reports_updated(tmp_path):
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    message = import_blotter(payload, RAW_BLOTTER.name, db)
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)
    assert f'(0 new, {EXPECTED_TRADES} updated)' in message


def test_import_blotter_newer_export_replaces_amended_trade(tmp_path):
    db = tmp_path / 'risk.db'
    frame = sample_frame()
    import_blotter(frame.to_csv(index=False).encode(), 'day1.csv', db)
    fut = frame[frame['Fin Type'] == 'FUTURE'].index[0]
    frame.loc[fut, 'Price'] = '1234.5'
    message = import_blotter(frame.to_csv(index=False).encode(), 'day2.csv', db)
    assert f'(0 new, {EXPECTED_TRADES} updated)' in message
    with sqlite3.connect(db) as conn:
        price, = conn.execute('SELECT price FROM trades WHERE trade_id = ?', (frame.loc[fut, 'Trade Id'],)).fetchone()
    assert price == 1234.5
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)


def test_import_blotter_tolerates_bom_semicolons_cp1252_and_mixed_case_headers(tmp_path):
    frame = sample_frame()
    text = frame.to_csv(index=False, sep=';')
    header, body = text.split('\n', 1)
    payload = b'\xef\xbb\xbf' + (header.upper() + '\n' + body).encode('cp1252')
    message = import_blotter(payload, 'export.csv', tmp_path / 'risk.db')
    assert f'{EXPECTED_TRADES} trades' in message
    assert 'could not be read' not in message


def test_import_blotter_reads_excel_with_cover_sheet_preamble_and_real_dates(tmp_path):
    frame = sample_frame()
    for col in ['TradeDate', 'Settle Date', 'Effective Date', 'Termination']:
        frame[col] = pd.to_datetime(frame[col], dayfirst=True, errors='coerce')
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        pd.DataFrame([['Cover page'], ['nothing here']]).to_excel(writer, sheet_name='Cover', index=False, header=False)
        pd.DataFrame([['Blotter as of 17/09/2026']]).to_excel(writer, sheet_name='Trades', index=False, header=False)
        frame.to_excel(writer, sheet_name='Trades', index=False, startrow=3)
    message = import_blotter(buf.getvalue(), 'export.xlsx', tmp_path / 'risk.db')
    assert f'{EXPECTED_TRADES} trades' in message
    assert 'could not be read' not in message


def test_import_blotter_without_status_or_fund_columns_loads_everything(tmp_path):
    frame = sample_frame().drop(columns=['Status', 'Fund'])
    message = import_blotter(frame.to_csv(index=False).encode(), 'x.csv', tmp_path / 'risk.db')
    assert f'{EXPECTED_TRADES} trades' in message


def test_import_blotter_one_bad_row_does_not_block_the_rest(tmp_path):
    frame = sample_frame()
    fwd = frame[frame['Fin Type'] == 'FORWARD'].index[0]
    frame.loc[fwd, 'Buy Currency'] = 'EUR.C-EUAA'  # contradicts the USD/JPY description
    message = import_blotter(frame.to_csv(index=False).encode(), 'x.csv', tmp_path / 'risk.db')
    assert trade_count(tmp_path / 'risk.db') == (EXPECTED_TRADES - 1, EXPECTED_LEGS - 2)
    assert '1 row(s) could not be read' in message
    assert 'disagree' in message
