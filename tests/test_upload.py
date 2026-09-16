from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from data.ingest.upload import BLOTTER_REQUIRED, BNP_REQUIRED, detect_format, import_blotter, import_report, suggested_date

RAW = Path(__file__).resolve().parents[1] / 'data/raw/HA_PNL_20260818.csv'
RAW_BLOTTER = Path(__file__).resolve().parents[1] / 'data/raw/new_sample_trades.csv'
needs_raw_blotter = pytest.mark.skipif(not RAW_BLOTTER.exists(), reason=f'raw file absent: {RAW_BLOTTER}')


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def test_detect_format_bnp():
    # A superset of BNP_REQUIRED (extra unrelated columns present) still detects as bnp.
    assert detect_format(BNP_REQUIRED | {'Unnamed: 136', 'Column 2'}) == 'bnp'


def test_detect_format_blotter():
    assert detect_format(BLOTTER_REQUIRED | {'TradeDate', 'Notional'}) == 'blotter'


def test_detect_format_unrecognized():
    assert detect_format({'Foo', 'Bar', 'Baz'}) is None


def test_daily_upload_repeat_and_history(tmp_path):
    db = tmp_path / 'risk.db'
    payload = RAW.read_bytes()
    message = import_report(payload, RAW.name, '2026-08-17', db)
    assert '232 new trades' in message
    before = snapshot(db)
    assert '0 new trades' in import_report(payload, RAW.name, '2026-08-17', db)
    assert snapshot(db) == before
    import_report(payload, 'HA_PNL_20260819.csv', '2026-08-18', db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('select count(distinct as_of_date) from positions').fetchone()[0] == 2
        assert conn.execute('select count(*) from trades').fetchone()[0] == 232


@pytest.mark.parametrize('failure', ['reject', 'conflict', 'mark_failure'])
def test_failed_import_leaves_all_tables_unchanged(tmp_path, monkeypatch, failure):
    db = tmp_path / 'risk.db'
    import_report(RAW.read_bytes(), RAW.name, '2026-08-17', db)
    before = snapshot(db)
    frame = pd.read_csv(RAW)
    if failure == 'reject':
        frame.loc[frame['Financial Type'] == 'FORWARD', 'Symbol Description'] = 'bad description'
    elif failure == 'conflict':
        idx = frame.index[frame['Financial Type'] == 'CURRENCY'][0]
        frame.loc[idx, 'Quantity'] += 1000
    else:
        monkeypatch.setattr('data.ingest.upload._load_bnp_marks_idempotent',
                            lambda *a: (0, 0, 0, ['Invalid mark'], []))
    with pytest.raises(ValueError):
        import_report(frame.to_csv(index=False).encode(), RAW.name, '2026-08-17', db)
    assert snapshot(db) == before


def test_excel_report_matches_csv(tmp_path):
    buf = BytesIO()
    pd.read_csv(RAW).to_excel(buf, sheet_name='Positions', index=False)
    csv_db, excel_db = tmp_path / 'csv.db', tmp_path / 'excel.db'
    import_report(RAW.read_bytes(), RAW.name, '2026-08-17', csv_db)
    import_report(buf.getvalue(), 'report.xlsx', '2026-08-17', excel_db, 'Positions')
    # Excel numeric serialization may differ at insignificant binary precision.
    with sqlite3.connect(csv_db) as a, sqlite3.connect(excel_db) as b:
        for table in ['trades', 'trade_legs', 'positions', 'marks']:
            pd.testing.assert_frame_equal(pd.read_sql(f'SELECT * FROM {table}', a),
                                          pd.read_sql(f'SELECT * FROM {table}', b))


def test_reference_workbook_cannot_be_imported_as_bnp(tmp_path):
    workbook = RAW.with_name('HA-portfolio vJean.xlsx')
    with pytest.raises(ValueError, match='not a BNP position report'):
        import_report(workbook.read_bytes(), workbook.name, '2026-08-17', tmp_path / 'risk.db', 'All FX trades')
    assert not (tmp_path / 'risk.db').exists()


def test_filename_dates():
    assert suggested_date('HA_PNL_20260818.xlsx') == '2026-08-17'
    assert suggested_date('report.xlsx') is None
    assert suggested_date('HA_PNL_20269999.csv') is None
    # browser re-download suffixes keep the date; a ninth digit does not
    assert suggested_date('HA_PNL_20260915[22].csv') == '2026-09-14'
    assert suggested_date('HA_PNL_20260915 (1).csv') == '2026-09-14'
    assert suggested_date('HA_PNL_202609151.csv') is None


RAW_0915 = RAW.with_name('HA_PNL_20260915[22].csv')


@pytest.mark.skipif(not RAW_0915.exists(), reason='2026-09-15 BNP file not present in data/raw')
def test_second_daily_file_with_closed_lines_imports_after_first(tmp_path):
    """The 2026-09-15 file has 49 zero-quantity lines (NDFs fixed 09-14, settled HKD)
    and 5-dp Price rounding on multi-million legs; it must load on top of 08-18."""
    db = tmp_path / 'risk.db'
    import_report(RAW.read_bytes(), RAW.name, '2026-08-17', db)
    message = import_report(RAW_0915.read_bytes(), RAW_0915.name, '2026-09-14', db)
    assert '49 closed FORWARD lines' in message and '15 other unsupported rows' in message
    with sqlite3.connect(db) as conn:
        dates = [r[0] for r in conn.execute('SELECT DISTINCT as_of_date FROM positions ORDER BY 1')]
        closed = conn.execute(
            "SELECT p.instrument_id, p.settle_date, p.pnl_dtd_usd FROM positions p JOIN instruments i USING (instrument_id) "
            "WHERE p.as_of_date='2026-09-14' AND p.quantity=0 AND i.asset_class='FX' ORDER BY 1").fetchall()
        n_trades = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
        zero_qty_trades = conn.execute('SELECT COUNT(*) FROM trades WHERE quantity=0').fetchone()[0]
    assert dates == ['2026-08-17', '2026-09-14']
    # one netted zero-quantity row per fixed NDF value date (plus the settled HKD line), P&L kept
    assert [(i, d) for i, d, _ in closed] == [('USDBRL', '2026-09-16'), ('USDHKD', '2026-09-08'),
                                              ('USDIDR', '2026-09-16'), ('USDKRW', '2026-09-16'),
                                              ('USDTWD', '2026-09-16')]
    assert sum(pnl for _, _, pnl in closed) == pytest.approx(39.539287 - 5.122639 - 4.843978 + 18.316588, abs=0.01)
    assert n_trades > 229 and zero_qty_trades == 0


# --------------------------------------------------------------------------- import_blotter
@needs_raw_blotter
def test_import_blotter_loads_real_file(tmp_path):
    db = tmp_path / 'risk.db'
    message = import_blotter(RAW_BLOTTER.read_bytes(), RAW_BLOTTER.name, db)
    assert '772 trades' in message
    assert '743 forwards' in message
    assert '11 futures' in message
    assert '8 options' in message
    assert '10 rate swaps' in message
    assert '85 currency rows seen' in message
    assert 'no position snapshot' in message
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0] == 772
        assert conn.execute('SELECT COUNT(*) FROM trade_legs').fetchone()[0] == 1525
        assert conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 0


@needs_raw_blotter
def test_import_blotter_duplicate_raises_clean_valueerror(tmp_path):
    db = tmp_path / 'risk.db'
    payload = RAW_BLOTTER.read_bytes()
    import_blotter(payload, RAW_BLOTTER.name, db)
    before = list(sqlite3.connect(db).iterdump())
    with pytest.raises(ValueError, match='Duplicate key'):
        import_blotter(payload, RAW_BLOTTER.name, db)
    assert list(sqlite3.connect(db).iterdump()) == before
