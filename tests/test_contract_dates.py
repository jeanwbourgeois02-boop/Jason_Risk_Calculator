"""Tests for data/ingest/contract_dates.py: Bloomberg's stored last trade date replaces a
commodity future's estimated expiry on the instrument, its NOTIONAL legs and the key of its
FUTURE_PX marks (values never change), and the upload applies it."""
from contextlib import closing
from pathlib import Path
import sqlite3

from data.contracts import ensure_static_table, store_static_dates
from data.ingest import contract_dates, schema
from data.ingest.contract_dates import apply_contract_dates
from data.ingest.upload import import_blotter_report

RAW_BLOTTER = Path(__file__).resolve().parents[1] / 'data/sample/blotter_sample.csv'
CL = 'CLZ26 Comdty'
ESTIMATE, BBG = '2026-12-31', '2026-11-19'


def _book_future(conn, instrument_id=CL, root='NYMEX:CL', expiry=ESTIMATE, trade_id='MANUAL-1'):
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?, 'FUTURE', ?, 'USD', 1000, 0, ?, ?)",
                 (instrument_id, root, instrument_id, expiry))
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description) VALUES "
                 "(?, 'MANUAL', ?, 'FUTURE', ?, '2026-09-01', 2, 70.0, 'ACC', '', '', '', '')",
                 (trade_id, instrument_id, trade_id))
    conn.execute("INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
                 "settles_cash) VALUES (?, 1, 'NOTIONAL', 'USD', 140000, '2026-09-01', ?, 70.0, 0)",
                 (trade_id, expiry))
    conn.commit()


def _mark(conn, as_of, settle, value, source='BBG_BDH', snapped='2026-09-10T17:00:00-04:00', iid=CL):
    conn.execute("INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
                 "VALUES (?, ?, ?, 'FUTURE_PX', ?, ?, ?)", (as_of, iid, settle, value, source, snapped))
    conn.commit()


def _store_bbg(conn, contract_id=CL, ltd=BBG):
    ensure_static_table(conn)
    store_static_dates(conn, [{'contract_id': contract_id, 'last_trade_date': ltd,
                               'first_notice_date': '2026-11-20', 'source': 'BBG_BDP'}])


def _marks(conn, iid=CL):
    return conn.execute("SELECT as_of_date, settle_date, value, source, snapped_at FROM marks "
                        "WHERE instrument_id = ? ORDER BY as_of_date, settle_date", (iid,)).fetchall()


def test_estimated_expiry_is_replaced_by_bloombergs_with_legs_and_marks_rekeyed():
    with closing(schema.connect()) as conn:
        _book_future(conn)
        _mark(conn, '2026-09-10', ESTIMATE, 70.5)
        _mark(conn, '2026-09-11', ESTIMATE, 71.25, snapped='2026-09-11T17:00:00-04:00')
        _store_bbg(conn)
        conn.execute("UPDATE bbg_library_state SET dirty = 0")
        conn.commit()

        out = apply_contract_dates(conn)

        assert out == {'checked': 1, 'missing_dates': [], 'updated': [{
            'instrument_id': CL, 'expiry_before': ESTIMATE, 'expiry_after': BBG,
            'legs': 1, 'marks_rekeyed': 2, 'marks_dropped': 0}]}
        assert conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = ?", (CL,)).fetchone()[0] == BBG
        assert conn.execute("SELECT settle_date FROM trade_legs WHERE trade_id = 'MANUAL-1'").fetchone()[0] == BBG
        # the key moved; value, source and snap time did not
        assert _marks(conn) == [('2026-09-10', BBG, 70.5, 'BBG_BDH', '2026-09-10T17:00:00-04:00'),
                                ('2026-09-11', BBG, 71.25, 'BBG_BDH', '2026-09-11T17:00:00-04:00')]
        # the trade_legs update fired the Bloomberg library's dirty trigger
        assert conn.execute("SELECT dirty FROM bbg_library_state").fetchone()[0] == 1
        # a second run finds nothing to do
        assert apply_contract_dates(conn)['updated'] == []


def test_a_mark_already_under_the_new_key_wins_and_the_moved_duplicate_is_dropped():
    with closing(schema.connect()) as conn:
        _book_future(conn)
        _mark(conn, '2026-09-10', ESTIMATE, 70.5)
        _mark(conn, '2026-09-10', BBG, 70.9, snapped='2026-09-10T17:00:01-04:00')
        _mark(conn, '2026-09-11', ESTIMATE, 71.25)
        _store_bbg(conn)

        out = apply_contract_dates(conn)

        assert out['updated'][0]['marks_dropped'] == 1
        assert out['updated'][0]['marks_rekeyed'] == 1
        assert [(a, s, v) for a, s, v, _src, _snap in _marks(conn)] == [
            ('2026-09-10', BBG, 70.9), ('2026-09-11', BBG, 71.25)]


def test_no_stored_dates_leaves_everything_alone_and_lists_the_instrument():
    with closing(schema.connect()) as conn:
        _book_future(conn)
        _book_future(conn, instrument_id='GCZ26 Comdty', root='COMEX:GC', trade_id='MANUAL-2')
        _book_future(conn, instrument_id='ESU6 Index', root='ES', trade_id='MANUAL-3')  # not a contract root
        _mark(conn, '2026-09-10', ESTIMATE, 70.5)
        _store_bbg(conn, contract_id='GCZ26 Comdty', ltd=ESTIMATE)  # stored and already equal

        out = apply_contract_dates(conn)

        assert out == {'checked': 2, 'updated': [], 'missing_dates': [CL]}
        assert conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = ?", (CL,)).fetchone()[0] == ESTIMATE
        assert conn.execute("SELECT settle_date FROM trade_legs WHERE trade_id = 'MANUAL-1'").fetchone()[0] == ESTIMATE
        assert _marks(conn)[0][1] == ESTIMATE


def test_no_contract_static_table_returns_an_empty_result():
    with closing(schema.connect()) as conn:
        _book_future(conn)
        assert apply_contract_dates(conn) == {'checked': 0, 'updated': [], 'missing_dates': []}
        assert conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = ?", (CL,)).fetchone()[0] == ESTIMATE


def test_summary_sentence_is_empty_when_nothing_changed():
    assert contract_dates.summary_sentence({'checked': 3, 'updated': [], 'missing_dates': ['X']}) == ''


def test_upload_applies_bloombergs_dates_and_says_so(tmp_path):
    db = tmp_path / 'risk.db'
    with closing(schema.connect(db)) as conn:
        _book_future(conn)  # a MANUAL future: it survives the upload
        _mark(conn, '2026-09-10', ESTIMATE, 70.5)
        _store_bbg(conn)

    report = import_blotter_report(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)

    assert "Contract dates: 1 future(s) moved to Bloomberg's last trade date" in report['message']
    assert f"{CL} {ESTIMATE} -> {BBG}" in report['message']
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT settle_date FROM trade_legs WHERE trade_id = 'MANUAL-1'").fetchone()[0] == BBG
        assert conn.execute("SELECT settle_date FROM marks WHERE instrument_id = ?", (CL,)).fetchone()[0] == BBG


def test_upload_calls_apply_contract_dates_and_is_silent_when_nothing_changed(tmp_path, monkeypatch):
    calls = []

    def fake(conn):
        calls.append(conn)
        return {'checked': 0, 'updated': [], 'missing_dates': []}

    monkeypatch.setattr(contract_dates, 'apply_contract_dates', fake)
    report = import_blotter_report(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, tmp_path / 'risk.db')
    assert len(calls) == 1
    assert 'Contract dates' not in report['message']


def test_a_failure_applying_the_dates_never_fails_the_upload(tmp_path, monkeypatch):
    def boom(conn):
        raise RuntimeError('contracts.csv unreadable')

    monkeypatch.setattr(contract_dates, 'apply_contract_dates', boom)
    report = import_blotter_report(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, tmp_path / 'risk.db')
    assert 'Contract dates not applied here (contracts.csv unreadable)' in report['message']
    with closing(sqlite3.connect(tmp_path / 'risk.db')) as conn:
        assert conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0] > 0


COMMODITY_BLOTTER = Path(__file__).resolve().parents[1] / 'data/sample/commodity_blotter_sample.csv'


def test_the_staged_parse_sees_the_stored_dates_so_the_upload_writes_bloombergs_date_at_once(tmp_path):
    """The upload parses against a whole-database backup of the live file, so contract_static
    is there and the parser writes Bloomberg's date itself: nothing is left to move after."""
    db = tmp_path / 'risk.db'
    with closing(schema.connect(db)) as conn:
        _store_bbg(conn)

    report = import_blotter_report(COMMODITY_BLOTTER.read_bytes(), COMMODITY_BLOTTER.name, db)

    assert 'Contract dates' not in report['message']
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = ?", (CL,)).fetchone()[0] == BBG
        legs = conn.execute("SELECT DISTINCT l.settle_date FROM trade_legs l JOIN trades t USING (trade_id) "
                            "WHERE t.instrument_id = ? AND l.leg_type = 'NOTIONAL'", (CL,)).fetchall()
        assert legs == [(BBG,)]
