"""Explicit inputs for the portfolio workbook's shared-date rate calculations."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import math
from pathlib import Path

import openpyxl

from data.ingest.schema import connect

SOURCE = 'WORKBOOK_REFERENCE'


def workday(day, offset):
    day = date.fromisoformat(str(day)[:10])
    direction = 1 if offset >= 0 else -1
    for _ in range(abs(offset)):
        day += timedelta(days=direction)
        while day.weekday() >= 5:
            day += timedelta(days=direction)
    return day.isoformat()


def rate_grid(conn, as_of):
    date.fromisoformat(as_of)
    maturity = workday(as_of, 5)
    instruments = conn.execute(
        "SELECT DISTINCT i.instrument_id FROM instruments i JOIN trades t USING(instrument_id) "
        "WHERE i.asset_class='FX' AND t.trade_date<=? ORDER BY i.instrument_id", (as_of,)).fetchall()
    out = []
    for (instrument,) in instruments:
        row = {'instrument_id': instrument}
        spot = conn.execute("SELECT value FROM marks WHERE instrument_id=? AND as_of_date=? "
                            "AND settle_date=? AND mark_type='SPOT' AND source=?",
                            (instrument, as_of, as_of, SOURCE)).fetchone()
        row['spot'] = spot[0] if spot else None
        for field, day in [('current', as_of), ('previous', workday(as_of, -1)), ('previous2', workday(as_of, -2))]:
            value = conn.execute("SELECT value FROM marks WHERE instrument_id=? AND as_of_date=? "
                                 "AND settle_date=? AND mark_type='FWD_OUTRIGHT' AND source=?",
                                 (instrument, day, maturity, SOURCE)).fetchone()
            row[field] = value[0] if value else None
        out.append(row)
    return out


def save_rates(db_path, as_of, rows):
    date.fromisoformat(as_of)
    maturity = workday(as_of, 5)
    prepared = []
    seen = set()
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(connect(Path(db_path).resolve())) as conn:
        known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE asset_class='FX'")}
        for row in rows:
            instrument = row['instrument_id']
            if instrument not in known or instrument in seen:
                raise ValueError(f'Unknown or repeated pair: {instrument}')
            seen.add(instrument)
            for field, day in [('current', as_of), ('previous', workday(as_of, -1)), ('previous2', workday(as_of, -2)), ('spot', as_of)]:
                raw = row.get(field)
                value = None if raw is None or raw == '' else float(raw)
                if value is not None and (not math.isfinite(value) or value <= 0):
                    raise ValueError(f'{instrument} {field}: enter a positive rate or leave blank.')
                prepared.append((day, instrument, day if field == 'spot' else maturity,
                                 'SPOT' if field == 'spot' else 'FWD_OUTRIGHT', value))
        with conn:
            for day, instrument, maturity, mark_type, value in prepared:
                key = (day, instrument, maturity, mark_type, SOURCE)
                conn.execute('DELETE FROM marks WHERE as_of_date=? AND instrument_id=? AND settle_date=? AND mark_type=? AND source=?', key)
                if value is not None:
                    conn.execute('INSERT INTO marks VALUES (?,?,?,?,?,?,?)',
                                 (day, instrument, maturity, mark_type, value, SOURCE, timestamp))
    return sum(v is not None for _, _, _, _, v in prepared)


def read_cached_rates(payload):
    """Return workbook-date inputs without evaluating Bloomberg or inventing caches."""
    book = openpyxl.load_workbook(BytesIO(payload), read_only=True, data_only=True)
    try:
        sheet = book['All FX trades']
        current_day = sheet['M1'].value
        if not isinstance(current_day, (date, datetime)):
            raise ValueError('Workbook M1 has no saved calculation date.')
        as_of = current_day.strftime('%Y-%m-%d')
        for cell, offset in [('N1', -1), ('O1', -2)]:
            cached_day = sheet[cell].value
            if not isinstance(cached_day, (date, datetime)) or cached_day.strftime('%Y-%m-%d') != workday(as_of, offset):
                raise ValueError(f'Saved workbook {cell} date does not match its WORKDAY formula.')
        maturity = sheet['P1'].value
        if not isinstance(maturity, (date, datetime)) or maturity.strftime('%Y-%m-%d') != workday(as_of, 5):
            raise ValueError('Saved workbook maturity does not match WORKDAY(M1,5).')
        rows, missing = [], 0
        for cells in sheet.iter_rows(min_row=11, max_row=30, min_col=12, max_col=16, values_only=True):
            instrument, _, previous, current, previous2 = cells
            if not isinstance(instrument, str) or len(instrument) != 6 or not instrument.isalpha():
                continue
            row = {'instrument_id': instrument}
            for field, value in [('current', current), ('previous', previous), ('previous2', previous2)]:
                valid = isinstance(value, (float, int)) and math.isfinite(value) and value > 0
                row[field] = value if valid else None
                missing += not valid
            rows.append(row)
        return as_of, rows, missing
    finally:
        book.close()
