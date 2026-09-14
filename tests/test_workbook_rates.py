from pathlib import Path
import sqlite3

import pytest

from data.ingest.schema import connect
from data.ingest.workbook_rates import rate_grid, save_rates, read_cached_rates, workday


def seed(path):
    conn = connect(path)
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('t','BNP','USDJPY','FX_FWD','t','2026-08-17',100,150,'a','b','s','t','d')")
    conn.commit()
    conn.close()


def test_rates_keep_common_date_and_spot_separate(tmp_path):
    path = tmp_path / 'risk.db'
    seed(path)
    assert save_rates(path, '2026-08-17', [{'instrument_id':'USDJPY','current':151,'previous':150,'previous2':149,'spot':148}]) == 4
    with sqlite3.connect(path) as conn:
        assert rate_grid(conn, '2026-08-17') == [{'instrument_id':'USDJPY','current':151,'previous':150,'previous2':149,'spot':148}]
        rows = conn.execute('SELECT as_of_date,settle_date,mark_type,value FROM marks ORDER BY as_of_date,mark_type').fetchall()
    assert rows == [('2026-08-13','2026-08-24','FWD_OUTRIGHT',149), ('2026-08-14','2026-08-24','FWD_OUTRIGHT',150),
                    ('2026-08-17','2026-08-24','FWD_OUTRIGHT',151), ('2026-08-17','2026-08-17','SPOT',148)]


def test_invalid_edit_rolls_back_and_blank_removes_rate(tmp_path):
    path = tmp_path / 'risk.db'
    seed(path)
    save_rates(path, '2026-08-17', [{'instrument_id':'USDJPY','current':151}])
    with pytest.raises(ValueError):
        save_rates(path, '2026-08-17', [{'instrument_id':'USDJPY','current':160,'previous':-1}])
    with sqlite3.connect(path) as conn:
        assert rate_grid(conn, '2026-08-17')[0]['current'] == 151
    save_rates(path, '2026-08-17', [{'instrument_id':'USDJPY','current':''}])
    with sqlite3.connect(path) as conn:
        assert rate_grid(conn, '2026-08-17')[0]['current'] is None


def test_real_workbook_reports_absent_fx_caches():
    path = Path(__file__).resolve().parents[1] / 'data/raw/HA-portfolio vJean.xlsx'
    as_of, rows, missing = read_cached_rates(path.read_bytes())
    assert as_of == '2026-09-11'
    assert rows and missing == len(rows)*3
    assert all(r['current'] is None for r in rows)
    assert workday(as_of,5) == '2026-09-18'
