from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from data.ingest.upload import import_report, suggested_date

RAW = Path(__file__).resolve().parents[1] / 'data/raw/HA_PNL_20260818.csv'


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def test_daily_upload_repeat_and_history(tmp_path):
    db = tmp_path / 'risk.db'
    payload = RAW.read_bytes()
    message = import_report(payload, RAW.name, '2026-08-17', db)
    assert '229 new trades' in message
    before = snapshot(db)
    assert '0 new trades' in import_report(payload, RAW.name, '2026-08-17', db)
    assert snapshot(db) == before
    import_report(payload, 'HA_PNL_20260819.csv', '2026-08-18', db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('select count(distinct as_of_date) from positions').fetchone()[0] == 2
        assert conn.execute('select count(*) from trades').fetchone()[0] == 229


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
