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

# The synthetic commodity, FX-hedge and FX-option book (commodity conversion Phase 2,
# 2026-09-24): 45 rows, 31 of them futures, two of which the parser rejects on purpose
# (ZCZ6: two exchanges list a ZC root; QQZ6: a root not in config/contracts.csv).
RAW_BLOTTER = Path(__file__).resolve().parents[1] / 'data/sample/blotter_sample.csv'
EXPECTED_TRADES, EXPECTED_LEGS = 43, 52  # 29 fut (1 leg) + 8 fwd (2) + 1 spot (2) + 5 opt (1)
SAMPLE_REJECTS = 2
SAMPLE_BREAKDOWN = '8 forwards, 1 spot, 29 futures, 5 options'


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
    assert f'{SAMPLE_REJECTS} row(s) could not be read' in message
    assert 'Replaced' not in message  # nothing pre-existed to replace


def test_summary_counts_the_trades_loaded_not_the_rows_seen_and_names_no_rate_swaps(tmp_path):
    """ui-shell, 2026-09-24: the summary said '31 futures' when 29 loaded (the parser's row
    counter includes the two rejected rows) and '0 rate swaps' after rates left the app."""
    from data.ingest import upload

    message = import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, tmp_path / 'risk.db')
    assert message.startswith(f'Imported {RAW_BLOTTER.name}: {EXPECTED_TRADES} trades -- {SAMPLE_BREAKDOWN}; '
                              f'{EXPECTED_LEGS} legs.')
    assert '31 futures' not in message and 'swap' not in message.lower()

    class T:
        def __init__(self, product):
            self.product = product

    assert upload.loaded_breakdown([]) == '0 forwards, 0 spot, 0 futures, 0 options'
    assert upload.loaded_breakdown([T('FX_SWAP'), T('FX_SWAP'), T('EQ_OPTION'), T('FX_OPTION'), T('IRS')]) == (
        '0 forwards, 0 spot, 2 FX swaps, 0 futures, 2 options, 1 IRS')  # nothing loaded goes uncounted


def test_an_upload_no_longer_packages_forwards_into_fx_swaps(tmp_path):
    """The package rule (swaps.py) left the app on 2026-09-24: two forwards on the same
    trade date, pair and account, opposite signs, equal USD, different value dates stay two
    outright forwards."""
    frame = sample_frame()
    fwd = frame[frame['Fin Type'] == 'FORWARD'].index[1]  # USDCNH: buy USD 1,000,000 value 2027-01-20
    twin = frame.loc[[fwd]].copy()
    twin['Trade Id'], twin['Side'], twin['Settle Date'] = '910000099', 'Sell', '18/11/2026'
    twin['Symbol'] = 'USDCNH111826-500099'
    twin['Description'] = 'TD 08/20/2026 VD 11/18/2026 SELL USD VS .BUY CNH @ 7.10800000'
    twin['Buy Currency'], twin['Sell Currency'] = frame.loc[fwd, 'Sell Currency'], frame.loc[fwd, 'Buy Currency']
    twin['BuyCurrency Amount'], twin['SellCurrency Amount'] = frame.loc[fwd, 'SellCurrency Amount'], frame.loc[fwd, 'BuyCurrency Amount']
    frame = pd.concat([frame, twin], ignore_index=True)
    db = tmp_path / 'risk.db'
    import_blotter(frame.to_csv(index=False).encode(), 'x.csv', db)
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT trade_id, product, package_id FROM trades WHERE trade_id IN (?, '910000099') "
                            "ORDER BY trade_id", (str(frame.loc[fwd, 'Trade Id']),)).fetchall()
        assert conn.execute("SELECT COUNT(*) FROM trades WHERE product = 'FX_SWAP'").fetchone()[0] == 0
    assert rows == [(tid, 'FX_FWD', tid) for tid, _, _ in rows] and len(rows) == 2


def test_upload_replaces_a_book_that_still_has_retired_swap_review_rows(tmp_path):
    """A database from before 2026-09-24 can hold swap_review rows pointing at the old book;
    their foreign key must not stop the replace. Works the same once the table is gone."""
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS swap_review (candidate_group TEXT NOT NULL, "
                     "trade_id TEXT NOT NULL REFERENCES trades, reason TEXT NOT NULL, PRIMARY KEY (candidate_group, trade_id))")
        conn.execute("INSERT INTO swap_review SELECT 'g', trade_id, 'old' FROM trades LIMIT 2")
    message = import_blotter(payload, RAW_BLOTTER.name, db)
    assert f'Replaced the previous book: {EXPECTED_TRADES} trade(s)' in message
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM swap_review').fetchone()[0] == 0
        conn.execute('DROP TABLE swap_review')
    import_blotter(payload, RAW_BLOTTER.name, db)
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)


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
    assert f'{SAMPLE_REJECTS} row(s) could not be read' in message


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
    assert f'{SAMPLE_REJECTS} row(s) could not be read' in message


def test_import_blotter_without_status_or_fund_columns_loads_everything(tmp_path):
    frame = sample_frame().drop(columns=['Status', 'Fund'])
    message = import_blotter(frame.to_csv(index=False).encode(), 'x.csv', tmp_path / 'risk.db')
    assert f'{EXPECTED_TRADES} trades' in message


def test_import_blotter_one_bad_row_does_not_block_the_rest(tmp_path):
    frame = sample_frame()
    fwd = frame[frame['Fin Type'] == 'FORWARD'].index[0]
    frame.loc[fwd, 'Buy Currency'] = 'EUR.C-EUAA'  # contradicts the USD/CNH description
    message = import_blotter(frame.to_csv(index=False).encode(), 'x.csv', tmp_path / 'risk.db')
    assert trade_count(tmp_path / 'risk.db') == (EXPECTED_TRADES - 1, EXPECTED_LEGS - 2)
    assert f'{SAMPLE_REJECTS + 1} row(s) could not be read' in message
    assert 'disagree' in message


# --------------------------------------------------------------------------- import_blotter_report
# The outcome as data (2026-09-18): ui/uploads.py auto-dismisses a clean import after 8 s
# and used to decide "clean" by reading the message for a phrase. The report gives it
# counts instead: `rejects` (rows skipped), `warnings` (the file's content was doubtful
# and a cell had to be rebuilt, ignored or distrusted) and `notes`; things the app shows
# persistently elsewhere (an option with no strike) are information and never raise
# `warnings`.

def test_report_for_the_sample_counts_its_two_rejects_and_no_warnings_only_information(tmp_path):
    from data.ingest.upload import import_blotter_report

    report = import_blotter_report(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, tmp_path / 'risk.db')
    assert set(report) == {'message', 'rejects', 'warnings', 'notes'}
    assert (report['rejects'], report['warnings']) == (SAMPLE_REJECTS, 0)
    assert isinstance(report['message'], str) and isinstance(report['notes'], list)
    assert f'{SAMPLE_REJECTS} row(s) could not be read' in report['message']
    # information: the one option with no strike -- present, short, and not a warning
    assert report['notes'] == ['1 option(s) have no strike in the file: USDJPY111926P-500043.']
    assert all(note in report['message'] for note in report['notes'])
    assert report['message'].startswith(f'Imported {RAW_BLOTTER.name}: {EXPECTED_TRADES} trades')
    assert len(report['message']) < 900                       # a paragraph, not a line per row


def test_import_blotter_is_the_reports_message_and_still_a_plain_string(tmp_path):
    import inspect

    from data.ingest import upload
    from data.ingest.upload import import_blotter_report

    payload = RAW_BLOTTER.read_bytes()
    message = import_blotter(payload, RAW_BLOTTER.name, tmp_path / 'a.db')
    report = import_blotter_report(payload, RAW_BLOTTER.name, tmp_path / 'b.db')
    assert type(message) is str and message == report['message']
    assert list(inspect.signature(import_blotter).parameters) == ['payload', 'filename', 'db_path']
    assert list(inspect.signature(import_blotter_report).parameters) == ['payload', 'filename', 'db_path']
    # the phrase ui/uploads.py pins against this function's own source stays in it
    assert upload.REJECTS_PHRASE == 'could not be read'
    assert upload.REJECTS_PHRASE in inspect.getsource(import_blotter)


def test_report_counts_a_price_that_arrived_as_a_date_and_was_rebuilt_as_a_warning_naming_the_row(tmp_path):
    from data.ingest.upload import import_blotter_report

    frame = sample_frame()
    fut = frame[frame['Fin Type'] == 'FUTURE'].index[0]
    fill = blotter._num(frame.loc[fut, 'Price'])
    frame.loc[fut, 'Price'] = '24-Jul'                        # what Excel makes of 7.24
    db = tmp_path / 'risk.db'
    report = import_blotter_report(frame.to_csv(index=False).encode(), 'x.csv', db)
    assert report['rejects'] == SAMPLE_REJECTS and report['warnings'] >= 1
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)      # rebuilt, so nothing was lost
    (warning_note,) = [n for n in report['notes'] if "'24-Jul'" in n]
    assert f'row {fut + 2} ' in warning_note and 'Price' in warning_note and 'is not a number' in warning_note
    assert warning_note in report['message']
    with sqlite3.connect(db) as conn:
        price, kind = conn.execute('SELECT price, typeof(price) FROM trades WHERE trade_id = ?',
                                   (frame.loc[fut, 'Trade Id'],)).fetchone()
    assert kind == 'real' and price == pytest.approx(fill, abs=0.005)


def test_report_counts_a_rejected_row_and_keeps_the_phrase_in_the_message(tmp_path):
    from data.ingest.upload import import_blotter_report

    frame = sample_frame()
    opt = frame[frame['Fin Type'] == 'OPTION'].index[0]
    frame.loc[opt, ['Price', 'NetInvoice']] = ['24-Jul', '']  # nothing left to rebuild the premium from
    report = import_blotter_report(frame.to_csv(index=False).encode(), 'x.csv', tmp_path / 'risk.db')
    assert report['rejects'] == SAMPLE_REJECTS + 1 and report['warnings'] == 0
    assert f'{SAMPLE_REJECTS + 1} row(s) could not be read and were skipped' in report['message']
    assert f'row {opt + 2} ' in report['message'] and "Price '24-Jul' is not a number" in report['message']
    assert trade_count(tmp_path / 'risk.db') == (EXPECTED_TRADES - 1, EXPECTED_LEGS - 1)


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


# --------------------------------------------------------------------------- book filter sentence

def test_summary_breaks_the_excluded_rows_down_by_the_book_filter(tmp_path):
    frame = blotter.read_table(RAW_BLOTTER)
    frame = frame.astype({'Status': object, 'Fund': object})
    frame.loc[frame.index[0], 'Status'] = 'Cancelled'
    frame.loc[frame.index[1], 'Fund'] = 'OTHERFUND'
    payload = frame.to_csv(index=False).encode('utf-8')

    from data.ingest.upload import import_blotter_report
    message = import_blotter_report(payload, 'filtered.csv', tmp_path / 'risk.db')['message']

    assert 'Book filter (' in message
    assert 'Rows excluded: 1 status, 1 fund.' in message
    assert 'other funds or cancelled' not in message
