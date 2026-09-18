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


# --------------------------------------------------------------------------- import_blotter_report
# The outcome as data (2026-09-18): ui/uploads.py auto-dismisses a clean import after 8 s
# and used to decide "clean" by reading the message for a phrase. The report gives it
# counts instead: `rejects` (rows skipped), `warnings` (the file's content was doubtful
# and a cell had to be rebuilt, ignored or distrusted) and `notes`; things the app shows
# persistently elsewhere (swaps defaulted to pay fixed, overrides kept, options with no
# strike) are information and never raise `warnings`.

def test_report_for_the_clean_sample_has_no_rejects_and_no_warnings_only_information(tmp_path):
    from data.ingest.upload import import_blotter_report

    report = import_blotter_report(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, tmp_path / 'risk.db')
    assert set(report) == {'message', 'rejects', 'warnings', 'notes'}
    assert (report['rejects'], report['warnings']) == (0, 0)
    assert isinstance(report['message'], str) and isinstance(report['notes'], list)
    assert 'could not be read' not in report['message']
    # information: the ten swaps read as pay fixed by default (names cut at "and n more")
    # and the three options with no strike -- present, short, and not warnings
    swaps_note, strike_note = report['notes']
    assert swaps_note.startswith('10 rate swap(s) carry no pay/receive marker') and 'and 5 more' in swaps_note
    assert strike_note.startswith('3 option(s) have no strike in the file: EURSEK112526C-197906813')
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
    assert report['rejects'] == 0 and report['warnings'] >= 1
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
    assert report['rejects'] == 1 and report['warnings'] == 0
    assert '1 row(s) could not be read and were skipped' in report['message']
    assert f'row {opt + 2} ' in report['message'] and "Price '24-Jul' is not a number" in report['message']
    assert trade_count(tmp_path / 'risk.db') == (EXPECTED_TRADES - 1, EXPECTED_LEGS - 1)


def test_report_says_how_many_direction_overrides_were_kept_as_information(tmp_path):
    from data.ingest import irs_direction, schema
    from data.ingest.upload import import_blotter_report

    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    conn = schema.connect(db)
    for trade_id in ('918421481', '920118423', '932385416'):
        irs_direction.set_direction(conn, trade_id, 'RECEIVE')
    conn.close()
    report = import_blotter_report(payload, RAW_BLOTTER.name, db)
    assert (report['rejects'], report['warnings']) == (0, 0)
    assert report['notes'][0].startswith('3 rate swap directions set by you kept')
    assert any(n.startswith('7 rate swap(s) carry no pay/receive marker') for n in report['notes'])
    with sqlite3.connect(db) as conn:
        receivers = {r[0] for r in conn.execute("SELECT trade_id FROM trades WHERE product='IRS' AND quantity < 0")}
    assert receivers == {'918421481', '920118423', '932385416'}


# --------------------------------------------------------------------------- swap history across an upload
SWAP_TRADE, SWAP_INSTRUMENT = '918421481', 'IRSOIS-USD-22860996'


def _seed_swap_history(db):
    from data.ingest import schema

    conn = schema.connect(db)
    rows = [(d, SWAP_INSTRUMENT, '2027-02-11', mt, v, src, 't')
            for d in ('2026-09-16', '2026-09-17')
            for mt, v, src in (('PV_USD', 100.0, 'QL_PRICER'), ('DV01_USD', 10.0, 'QL_PRICER'),
                               ('CASHFLOW_USD', 1.0, 'QL_PRICER'), ('PAR_RATE', 0.04, 'QL_PRICER'),
                               ('PV_USD', 99.0, 'BBG_BDH'))]
    conn.executemany('INSERT INTO marks VALUES (?,?,?,?,?,?,?)', rows)
    conn.commit()
    conn.close()


def _swap_history(db):
    with sqlite3.connect(db) as conn:
        return sorted(conn.execute('SELECT as_of_date, mark_type, source, value FROM marks WHERE instrument_id = ?',
                                   (SWAP_INSTRUMENT,)).fetchall())


def test_upload_that_turns_a_swap_round_reverses_its_priced_history_once_and_deletes_nothing_it_can_keep(tmp_path):
    """Full-replace path: the staging copy's book is emptied before the load, so only
    `_stage_and_publish` can see the flip -- and it must reverse exactly once."""
    db = tmp_path / 'risk.db'
    frame = sample_frame()
    import_blotter(frame.to_csv(index=False).encode(), 'day1.csv', db)
    _seed_swap_history(db)
    before = _swap_history(db)

    import_blotter(frame.to_csv(index=False).encode(), 'day1-again.csv', db)      # same direction
    assert _swap_history(db) == before

    swap = frame[frame['Trade Id'] == SWAP_TRADE].index[0]
    frame.loc[swap, 'Notional'] = '(625,000,000)'                                  # the export now marks it short
    import_blotter(frame.to_csv(index=False).encode(), 'day2.csv', db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT quantity FROM trades WHERE trade_id = ?', (SWAP_TRADE,)).fetchone()[0] == -625e6
    expected = sorted((d, mt, s, v if mt == 'PAR_RATE' else -v) for d, mt, s, v in before if s == 'QL_PRICER')
    assert _swap_history(db) == expected                       # both dates reversed, PAR_RATE kept, BBG_BDH gone

    import_blotter(frame.to_csv(index=False).encode(), 'day2-again.csv', db)      # still short: not reversed again
    assert _swap_history(db) == expected


def test_upload_never_reverses_the_history_of_a_swap_the_user_has_already_set(tmp_path):
    from data.ingest import irs_direction, schema

    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    _seed_swap_history(db)
    conn = schema.connect(db)
    irs_direction.set_direction(conn, SWAP_TRADE, 'RECEIVE')   # the one real flip: history reversed here
    conn.close()
    after_set = _swap_history(db)
    assert ('2026-09-17', 'PV_USD', 'QL_PRICER', -100.0) in after_set
    import_blotter(payload, RAW_BLOTTER.name, db)              # the file still says nothing; the override holds
    import_blotter(payload, RAW_BLOTTER.name, db)
    assert _swap_history(db) == after_set


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
