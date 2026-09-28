"""Tests for data/ingest/upload.py -- blotter-only upload. File-shape tolerance
(encodings, delimiters, header casing, preamble rows, Excel, re-upload) is exercised
here end to end through import_blotter; per-row parsing lives in test_blotter.py."""
from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from data.ingest import blotter
from data.ingest.common import ParseWarning
from data.ingest.upload import import_blotter, preview_frame, record_upload_issues, validate_blotter_shape

# The synthetic commodity, FX-hedge and FX-option book (commodity conversion Phase 2,
# 2026-09-24; Phase 5 added 4 options on futures and 3 LME forwards): 52 rows, 31 of them
# futures, two of which the parser rejects on purpose (ZCZ6: two exchanges list a ZC root;
# QQZ6: a root not in config/contracts.csv).
RAW_BLOTTER = Path(__file__).resolve().parents[1] / 'data/sample/blotter_sample.csv'
EXPECTED_TRADES, EXPECTED_LEGS = 50, 62  # 29 fut (1 leg) + 4 opt on fut (1) + 3 LME (2) + 8 fwd (2) + 1 spot (2) + 5 FX opt (1)
SAMPLE_REJECTS = 2
SAMPLE_BREAKDOWN = '29 futures, 4 options on futures, 3 LME forwards, 8 FX forwards, 1 FX spot, 5 FX options'


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
    assert f'{EXPECTED_TRADES} added, 0 already on file' in message  # nothing pre-existed to replace


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

    assert upload.loaded_breakdown([]) == ('0 futures, 0 options on futures, 0 LME forwards, 0 FX forwards, '
                                           '0 FX spot, 0 FX options')
    assert upload.loaded_breakdown([T('FX_SWAP'), T('FX_SWAP'), T('EQ_OPTION'), T('FX_OPTION'), T('IRS')]) == (
        '0 futures, 0 options on futures, 0 LME forwards, 1 listed options, 0 FX forwards, 0 FX spot, '
        '2 FX swaps, 1 FX options, 1 IRS')  # nothing loaded goes uncounted


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
    their foreign key must not stop the upload. Since 2026-09-28's merge by Trade Id a replaced
    trade stays on file, so its leftover rows stay too (schema.purge_retired_sources drops the
    table at start-up); a removed trade's rows go with it. Works the same once the table is gone."""
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS swap_review (candidate_group TEXT NOT NULL, "
                     "trade_id TEXT NOT NULL REFERENCES trades, reason TEXT NOT NULL, PRIMARY KEY (candidate_group, trade_id))")
        conn.execute("INSERT INTO swap_review SELECT 'g', trade_id, 'old' FROM trades LIMIT 2")
    message = import_blotter(payload, RAW_BLOTTER.name, db)
    assert f'0 added, {EXPECTED_TRADES} already on file' in message
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM swap_review').fetchone()[0] == 2
        conn.execute('DROP TABLE swap_review')
    import_blotter(payload, RAW_BLOTTER.name, db)
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)


def test_import_blotter_reupload_of_same_file_replaces_and_keeps_same_count(tmp_path):
    # Merge by Trade Id (user decision 2026-09-28, replacing the full replace of 2026-09-17):
    # a re-upload replaces each Trade Id already on file with the file's row and re-inserts
    # its legs -- same file twice still ends at the same counts, and the message says so.
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    message = import_blotter(payload, RAW_BLOTTER.name, db)
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)
    assert (f'{EXPECTED_TRADES} trades in the file: 0 added, {EXPECTED_TRADES} already on file (replaced by the '
            f"file's rows), 0 removed as cancelled; {EXPECTED_TRADES} trades on file.") in message


def test_import_blotter_newer_export_replaces_amended_trade(tmp_path):
    db = tmp_path / 'risk.db'
    frame = sample_frame()
    import_blotter(frame.to_csv(index=False).encode(), 'day1.csv', db)
    fut = frame[frame['Fin Type'] == 'FUTURE'].index[0]
    frame.loc[fut, 'Price'] = '1234.5'
    message = import_blotter(frame.to_csv(index=False).encode(), 'day2.csv', db)
    assert f'0 added, {EXPECTED_TRADES} already on file' in message
    with sqlite3.connect(db) as conn:
        price, = conn.execute('SELECT price FROM trades WHERE trade_id = ?', (frame.loc[fut, 'Trade Id'],)).fetchone()
    assert price == 1234.5
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)


def test_import_blotter_merges_by_trade_id_and_keeps_trades_the_file_does_not_name(tmp_path):
    """User decision 2026-09-28 ("yeah that makes sense"): an upload is a merge by Trade Id.
    A trade the file does not name stays on file with its realised_pnl row; a Trade Id the
    file replaces has its realised_pnl row dropped (the ledger freezes it again from the new
    fill) and its legs re-inserted, never orphaned or doubled."""
    db = tmp_path / 'risk.db'
    import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)

    with sqlite3.connect(db) as conn:
        old_trade_id, old_instrument_id = conn.execute(
            "SELECT trade_id, instrument_id FROM trades LIMIT 1").fetchone()
        # A trade the next file does not name, and its frozen row.
        conn.execute(
            "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, "
            "quantity, price, account, counterparty, strategy, trader, description) "
            "VALUES ('KEEP-1','XLSX',?,'FX_FWD','KEEP-1','2026-01-01',1,1,'','','','','')", (old_instrument_id,))
        conn.execute("INSERT INTO trade_legs VALUES ('KEEP-1',1,'FX_NEAR','USD',1,"
                     "'2026-01-01','2026-01-01',1,1)")
        conn.execute(
            "INSERT INTO realised_pnl VALUES ('KEEP-1', ?, 'FX_FWD', 'USD', '2026-01-01', 0, 0, 'SPOT', "
            "1, '2026-01-01', 'BBG_BFXFORWARD', 0, '2026-01-01T00:00:00', '')", (old_instrument_id,))
        # A realised_pnl row keyed to a trade the next upload replaces.
        conn.execute(
            "INSERT INTO realised_pnl VALUES (?, ?, 'FX_FWD', 'USD', '2026-01-01', 0, 0, 'SPOT', "
            "1, '2026-01-01', 'BBG_BFXFORWARD', 0, '2026-01-01T00:00:00', '')",
            (old_trade_id, old_instrument_id))
        conn.commit()

    message = import_blotter(RAW_BLOTTER.read_bytes(), 'day2.csv', db)

    with sqlite3.connect(db) as conn:
        trade_ids = {r[0] for r in conn.execute('SELECT trade_id FROM trades')}
        leg_trade_ids = {r[0] for r in conn.execute('SELECT DISTINCT trade_id FROM trade_legs')}
        realised = {r[0] for r in conn.execute('SELECT trade_id FROM realised_pnl')}
    assert old_trade_id in trade_ids and 'KEEP-1' in trade_ids
    assert leg_trade_ids == trade_ids  # no orphaned legs, and every trade has legs
    assert realised == {'KEEP-1'}  # the replaced trade's row is dropped, the kept trade's stays
    assert trade_count(db) == (EXPECTED_TRADES + 1, EXPECTED_LEGS + 1)
    assert f'0 added, {EXPECTED_TRADES} already on file' in message and f'{EXPECTED_TRADES + 1} trades on file' in message


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
    assert set(report) == {'message', 'rejects', 'warnings', 'notes',
                           'added', 'replaced', 'removed', 'removed_manual', 'on_file_after'}
    assert (report['rejects'], report['warnings']) == (SAMPLE_REJECTS, 0)
    assert isinstance(report['message'], str) and isinstance(report['notes'], list)
    assert f'{SAMPLE_REJECTS} row(s) could not be read' in report['message']
    # information: the one option with no strike -- present, short, and not a warning
    assert report['notes'][0] == '1 option(s) have no strike in the file: USDJPY111926P-500043.'
    # information too: the LME ticket with no prompt date in the file (Phase 5)
    assert len(report['notes']) >= 2 and report['notes'][1].startswith('1 LME ticket(s) carry no prompt date')
    # information too (parser, 2026-09-28): fills converted from the broker's price units; nothing else
    assert all("converted from the broker's price units" in note for note in report['notes'][2:])
    assert all(note in report['message'] for note in report['notes'])
    assert report['message'].startswith(f'Imported {RAW_BLOTTER.name}: {EXPECTED_TRADES} trades')
    # a paragraph, not a line per row (52 rows named one by one would run past 3000); 1800 since
    # 2026-09-28's merge sentence and the parser's price-units note (the sample's is ~1380)
    assert len(report['message']) < 1800


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
    # config/book.yaml's fund list is empty since 2026-09-28 (an empty list takes every value),
    # so only the cancelled row is excluded; the OTHERFUND row loads.
    assert 'Rows excluded: 1 status.' in message
    assert 'other funds or cancelled' not in message


# --------------------------------------------------------------------------- Phase 5: options on futures, LME forwards

def test_summary_names_options_on_futures_lme_forwards_and_the_underlying_futures_in_plain_words(tmp_path):
    """Phase 5 (2026-09-24): the sample's 4 options on futures and 3 LME forwards reach the
    summary under plain labels, never a product code ('3 LME_FWD'), and the two underlying
    futures written with no trade of their own are named."""
    from data.ingest.upload import import_blotter_report

    db = tmp_path / 'risk.db'
    report = import_blotter_report(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)
    message = report['message']
    assert '4 options on futures' in message and '3 LME forwards' in message and '5 FX options' in message
    assert 'LME_FWD' not in message and 'CMDTY_OPTION' not in message
    assert ('2 underlying future(s) of the options on futures written with no trade of their own: '
            'CUZ26 Comdty, GCQ26 Comdty.') in message
    # the LME prompt note is information: in the message, never a warning
    assert report['warnings'] == 0
    assert any(n.startswith('1 LME ticket(s) carry no prompt date') and n in message for n in report['notes'])
    with sqlite3.connect(db) as conn:
        products = dict(conn.execute('SELECT product, COUNT(*) FROM trades GROUP BY product').fetchall())
        assert products['CMDTY_OPTION'] == 4 and products['LME_FWD'] == 3
        assert conn.execute("SELECT instrument_id, asset_class FROM instruments WHERE instrument_id IN "
                            "('GCQ26 Comdty', 'CUZ26 Comdty') ORDER BY 1").fetchall() == [
            ('CUZ26 Comdty', 'FUTURE'), ('GCQ26 Comdty', 'FUTURE')]


def test_underlying_only_futures_survive_a_re_upload_and_are_never_overwritten(tmp_path):
    """Instruments are never deleted by an upload, and the parser writes an option's
    underlying future with INSERT OR IGNORE: a row on file (here with Bloomberg's date
    already applied) keeps what it has across a re-upload, and no trade hangs off it."""
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE instruments SET expiry_date = '2026-07-29' WHERE instrument_id = 'GCQ26 Comdty'")
    message = import_blotter(payload, RAW_BLOTTER.name, db)
    assert f'0 added, {EXPECTED_TRADES} already on file' in message
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT asset_class, expiry_date FROM instruments WHERE instrument_id = 'GCQ26 Comdty'"
                            ).fetchone() == ('FUTURE', '2026-07-29')
        assert conn.execute("SELECT COUNT(*) FROM trades WHERE instrument_id IN ('GCQ26 Comdty', 'CUZ26 Comdty')"
                            ).fetchone()[0] == 0


def test_import_blotter_replaces_a_manual_trade_too(tmp_path):
    """User, 2026-09-28: the blotter upload is the only way a trade enters the app, so even under
    the merge by Trade Id a ``source = 'MANUAL'`` leftover of the retired manual-entry screen is
    removed by every upload (until that day a MANUAL trade survived an upload)."""
    db = tmp_path / 'risk.db'
    import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)
    with sqlite3.connect(db) as conn:
        instrument_id = conn.execute("SELECT instrument_id FROM trades LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, "
            "quantity, price, account, counterparty, strategy, trader, description) "
            "VALUES ('MANUAL-1','MANUAL',?,'FX_FWD','MANUAL-1','2026-01-01',1,1,'','','','','')", (instrument_id,))
        conn.execute("INSERT INTO trade_legs VALUES ('MANUAL-1',1,'FX_NEAR','USD',1,"
                     "'2026-01-01','2026-01-01',1,1)")
        conn.commit()
    assert trade_count(db) == (EXPECTED_TRADES + 1, EXPECTED_LEGS + 1)

    import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)

    with sqlite3.connect(db) as conn:
        trade_ids = {r[0] for r in conn.execute('SELECT trade_id FROM trades')}
        sources = {r[0] for r in conn.execute('SELECT DISTINCT source FROM trades')}
        legs = conn.execute("SELECT COUNT(*) FROM trade_legs WHERE trade_id = 'MANUAL-1'").fetchone()[0]
    assert 'MANUAL-1' not in trade_ids and legs == 0
    assert sources == {'XLSX'}
    assert trade_count(db) == (EXPECTED_TRADES, EXPECTED_LEGS)


def test_a_file_level_parse_warning_is_persisted_as_a_warning_row_in_upload_issues(tmp_path):
    """ingest-parser, 2026-09-28: a ``ParseWarning`` with ``row_no == 0`` is a sentence about the
    file as a whole (two strategy labels that look like one strategy). It becomes one
    ``upload_issues`` row of kind WARNING with symbol '' and the sentence as its reason, so the
    Book tab's "Last load" and the Data tab list it; a row-level warning stays in the summary
    only, and the trades on file do not change."""
    db = tmp_path / 'risk.db'
    import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)
    before = trade_count(db)
    sentence = ("Strategy labels that look like one strategy: 'JSHY10_ZNA1' (25 rows) and "
                "'JSHY10.3_ZNA1' (2 rows) are grouped as ZNA1; if they are two positions, tell us.")

    class _Result:
        rejects = []
        skipped_other_rows = []
        warnings = [ParseWarning(0, '', sentence),
                    ParseWarning(7, 'CLZ6-USAA', 'Price arrived as a date; rebuilt from NetInvoice.')]

    assert record_upload_issues(db, 'export.csv', _Result()) == 1

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT row_no, symbol, kind, reason, filename FROM upload_issues").fetchall()
    assert rows == [(0, '', 'WARNING', sentence, 'export.csv')]
    assert trade_count(db) == before
