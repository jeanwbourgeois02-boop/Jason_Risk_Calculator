"""Tests for data/ingest/upload.py -- blotter-only upload (BNP support removed
2026-09-17, per direct user instruction; data/ingest/bnp.py itself is untouched and
still used by data/load.py's CLI, just no longer reachable through this module)."""
from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from data.ingest.upload import (
    BLOTTER_REQUIRED,
    _canonicalize_columns,
    import_blotter,
    validate_blotter_shape,
)

RAW_BLOTTER = Path(__file__).resolve().parents[1] / 'data/raw/new_sample_trades.csv'
needs_raw_blotter = pytest.mark.skipif(not RAW_BLOTTER.exists(), reason=f'raw file absent: {RAW_BLOTTER}')


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


# --------------------------------------------------------------------------- shape validation

def test_validate_blotter_shape_accepts_superset_of_required_columns():
    frame = pd.DataFrame(columns=list(BLOTTER_REQUIRED) + ['TradeDate', 'Notional', 'Something Extra'])
    validate_blotter_shape(frame)  # must not raise


def test_validate_blotter_shape_rejects_missing_columns():
    frame = pd.DataFrame(columns=['Foo', 'Bar', 'Baz'])
    with pytest.raises(ValueError, match='not a trade blotter'):
        validate_blotter_shape(frame)


def test_validate_blotter_shape_is_case_and_whitespace_insensitive():
    # 'STATUS', ' fin type ', 'Trade ID' etc. must all still count as present.
    columns = [c.upper() if i % 2 else f' {c} ' for i, c in enumerate(BLOTTER_REQUIRED)]
    frame = pd.DataFrame(columns=columns)
    validate_blotter_shape(frame)  # must not raise


# --------------------------------------------------------------------------- canonicalization

def test_canonicalize_columns_recases_known_columns():
    frame = pd.DataFrame(columns=['STATUS', 'fin type', 'Trade id', 'Symbol'])
    out = _canonicalize_columns(frame)
    assert list(out.columns) == ['Status', 'Fin Type', 'Trade Id', 'Symbol']


def test_canonicalize_columns_leaves_unknown_columns_untouched():
    frame = pd.DataFrame(columns=['Status', 'Some Unrecognized Column'])
    out = _canonicalize_columns(frame)
    assert list(out.columns) == ['Status', 'Some Unrecognized Column']


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


@needs_raw_blotter
def test_import_blotter_tolerates_bom_and_mixed_case_headers(tmp_path):
    """A UTF-8 BOM before the header, plus a mixed-case 'Fin Type'/'Trade Id', must
    not change the outcome versus the plain reference file."""
    text = RAW_BLOTTER.read_text(encoding='utf-8')
    text = text.replace('Fin Type', 'FIN TYPE', 1).replace('Trade Id', 'trade id', 1)
    payload = ('﻿' + text).encode('utf-8')
    db = tmp_path / 'risk.db'
    message = import_blotter(payload, 'bom_mixed_case.csv', db)
    assert '772 trades' in message
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0] == 772


def test_import_blotter_one_bad_row_does_not_block_the_rest(tmp_path):
    """strict=False (2026-09-17, 'as flexible as possible'): a single malformed row
    must not prevent every other good row in the same file from loading."""
    good_row = (
        'Completed,Firm,APAC,TD 08/20/2026 VD 09/16/2026 SELL JPY VS .BUY USD @ 158.26755000,'
        'Buy,FORWARD,934530555,5,,,USDJPY091626-197584766,USDJPY-XXAA,"1,137,580.00",158.26755,,,,'
        'SCBANK,,,HA,PARIUK,,NMCL,NMMF,HAHY7,HAHY7:XXNMMFNMCL0001,20/8/2026,,,Unchecked,JPY.C-JPAA,,'
        '"180,042,000.00",,,0,Explicit,CUSTOM,16/9/2026,,,,,,,,"180,042,000.00",0,,ASIA,1010,0,'
        'NMCL911783813,NEW,,,,20/8/2026 19:54,20/8/2026 20:59,sys_il_tramp,PRIMEBKR,BNPP-IPBFX-NMMF,'
        'FUTURE,,,812,420817455,197584766,FX Forward,,,,,,,HFS,,,Matched in FX,,,,,,,,,,,,,,,,,,,'
        'sys_il_tramp,USDJPY-XXAA,DOL.C-USAA,JPY.C-JPAA,"1,137,580.00","180,042,000.00",,,,,,,'
    )
    bad_row = good_row.replace(
        'TD 08/20/2026 VD 09/16/2026 SELL JPY VS .BUY USD @ 158.26755000', 'not a real description'
    ).replace('934530555', '934530556')  # different Trade Id so it isn't a duplicate key
    header = RAW_BLOTTER.read_text(encoding='utf-8').splitlines()[0]
    payload = ('\n'.join([header, good_row, bad_row]) + '\n').encode('utf-8')

    db = tmp_path / 'risk.db'
    message = import_blotter(payload, 'one_bad_row.csv', db)

    assert '1 trades' in message
    assert 'could not be parsed and were skipped' in message
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0] == 1
