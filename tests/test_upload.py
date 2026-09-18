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
EXPECTED_TRADES, EXPECTED_LEGS = 857, 1695  # 743 fwd + 85 spot + 11 fut + 8 opt + 10 IRS (spot rows since 2026-09-18)


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
    assert f'{EXPECTED_TRADES} trades' in message
    assert 'could not be read' not in message
    assert 'Replaced' not in message  # nothing pre-existed to replace


def test_import_blotter_reupload_of_same_file_replaces_and_keeps_same_count(tmp_path):
    # Full replace (user decision 2026-09-17): a re-upload deletes the whole previous
    # book first, then writes the new file's trades -- same file twice still ends at the
    # same counts, but via delete-then-insert, not merge, and the message says so.
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    message = import_blotter(payload, RAW_BLOTTER.name, db)
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)
    assert f'Replaced the previous book: {EXPECTED_TRADES} trade(s) and {EXPECTED_LEGS} leg(s) removed.' in message


def test_import_blotter_newer_export_replaces_amended_trade(tmp_path):
    db = tmp_path / 'risk.db'
    frame = sample_frame()
    import_blotter(frame.to_csv(index=False).encode(), 'day1.csv', db)
    fut = frame[frame['Fin Type'] == 'FUTURE'].index[0]
    frame.loc[fut, 'Price'] = '1234.5'
    message = import_blotter(frame.to_csv(index=False).encode(), 'day2.csv', db)
    assert f'Replaced the previous book: {EXPECTED_TRADES} trade(s)' in message
    with sqlite3.connect(db) as conn:
        price, = conn.execute('SELECT price FROM trades WHERE trade_id = ?', (frame.loc[fut, 'Trade Id'],)).fetchone()
    assert price == 1234.5
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)


def test_import_blotter_full_replace_removes_old_trades_and_dependents_no_orphans(tmp_path):
    """Coordinator instruction 2026-09-17: a new blotter is the only input -- sample
    data, a previous excel's trades, and legacy BNP-sourced rows are all deleted, along
    with every trade-keyed dependent (realised_pnl, swap_review), never left orphaned."""
    db = tmp_path / 'risk.db'
    import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)

    with sqlite3.connect(db) as conn:
        old_trade_id, old_instrument_id = conn.execute(
            "SELECT trade_id, instrument_id FROM trades LIMIT 1").fetchone()
        # A legacy BNP-sourced trade + leg (pre-2026-09-17 books could carry these).
        conn.execute(
            "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, "
            "quantity, price, account, counterparty, strategy, trader, description) "
            "VALUES ('BNP-1','BNP',?,'FX_FWD','BNP-1','2026-01-01',1,1,'','','','','')", (old_instrument_id,))
        conn.execute("INSERT INTO trade_legs VALUES ('BNP-1',1,'FX_NEAR','USD',1,"
                     "'2026-01-01','2026-01-01',1,1)")
        # A realised_pnl row keyed to a trade that the next upload will remove.
        conn.execute(
            "INSERT INTO realised_pnl VALUES (?, ?, 'FX_FWD', 'USD', '2026-01-01', 0, 0, 'SPOT', "
            "1, '2026-01-01', 'BBG_BFXFORWARD', 0, '2026-01-01T00:00:00', '')",
            (old_trade_id, old_instrument_id))
        conn.commit()

    frame = sample_frame()
    frame['Trade Id'] = frame['Trade Id'].astype(str) + '9'  # a disjoint id space
    message = import_blotter(frame.to_csv(index=False).encode(), 'day2.csv', db)

    with sqlite3.connect(db) as conn:
        trade_ids = {r[0] for r in conn.execute('SELECT trade_id FROM trades')}
        leg_trade_ids = {r[0] for r in conn.execute('SELECT DISTINCT trade_id FROM trade_legs')}
        realised_count = conn.execute('SELECT COUNT(*) FROM realised_pnl').fetchone()[0]
    assert old_trade_id not in trade_ids
    assert 'BNP-1' not in trade_ids
    assert leg_trade_ids == trade_ids  # no orphaned legs, and every remaining trade has legs
    assert realised_count == 0  # the old trade's realised_pnl row is gone, not orphaned
    assert all(tid.endswith('9') for tid in trade_ids)
    assert f'Replaced the previous book: {EXPECTED_TRADES + 1} trade(s)' in message


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


# --------------------------------------------------------------------------- schema migration
# Regression for a bug reported live (2026-09-17): a DB created before the
# `instrument_options.payoff` column landed in the DDL is never migrated by
# `CREATE TABLE IF NOT EXISTS`, so `ui/tabs/options.py`'s `o.payoff` query 500s. Fixed by
# making `data/ingest/schema.py::_migrate_columns` generic (diffs every DDL table's own
# column list against `PRAGMA table_info`) instead of a hand-maintained list that can go
# stale the next time a column is added to the DDL.

OLD_INSTRUMENT_OPTIONS_DDL = """
CREATE TABLE instrument_options (
  instrument_id   TEXT PRIMARY KEY,
  strike          REAL NOT NULL DEFAULT 0,
  option_type     TEXT NOT NULL DEFAULT '',
  barrier_level   REAL NOT NULL DEFAULT 0,
  avg_start_date  TEXT NOT NULL DEFAULT '9999-12-31'
)
"""


def test_create_schema_migrates_old_four_column_instrument_options_table():
    from data.ingest import schema

    conn = sqlite3.connect(':memory:')
    conn.execute(OLD_INSTRUMENT_OPTIONS_DDL)
    conn.execute("INSERT INTO instrument_options VALUES ('USDJPY111926P-1', 152.0, 'PUT', 0, '9999-12-31')")
    conn.commit()

    schema.create_schema(conn)

    columns = [r[1] for r in conn.execute('PRAGMA table_info(instrument_options)')]
    assert 'payoff' in columns
    payoff, = conn.execute(
        "SELECT payoff FROM instrument_options WHERE instrument_id = 'USDJPY111926P-1'").fetchone()
    assert payoff == 'VANILLA'  # DDL's own DEFAULT, backfilled onto the pre-existing row


def test_options_leg_rows_query_runs_against_a_migrated_old_instrument_options_table():
    from data.ingest import schema
    from ui.tabs.options import _leg_rows

    conn = sqlite3.connect(':memory:')
    conn.execute(OLD_INSTRUMENT_OPTIONS_DDL)
    conn.commit()
    schema.create_schema(conn)  # migrates instrument_options onto the pre-existing table

    conn.execute("INSERT INTO instruments VALUES "
                 "('USDJPY111926P-1','FX_OPTION','USD','JPY',1,0,'USDJPY111926P-1','2026-11-19')")
    conn.execute("INSERT INTO instrument_options VALUES ('USDJPY111926P-1',152.0,'PUT',0,'9999-12-31','VANILLA')")
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) VALUES "
        "('T1','XLSX','USDJPY111926P-1','FX_OPTION','T1','2026-08-19',1000000,0.0142,'','','','','')")
    conn.execute("INSERT INTO trade_legs VALUES ('T1',1,'NOTIONAL','USD',1000000,'2026-08-19','2026-11-19',0.0142,0)")
    conn.commit()

    rows = _leg_rows(conn, '2026-08-19')  # must not raise sqlite3.OperationalError: no such column: o.payoff
    assert len(rows) == 1
    assert rows[0]['trade_id'] == 'T1'
