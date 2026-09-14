from datetime import date, datetime

import pytest

from data.bloomberg.fwd_curve import outright_for_date, points_from_rows


ROWS = [  # shape Bloomberg returns for FWD_CURVE / OUTRIGHTS (column names vary by terminal)
    {"Tenor": "1W", "Settlement Date": datetime(2026, 9, 21), "Bid": 0.7130, "Ask": 0.7134},
    {"Tenor": "1M", "Settlement Date": datetime(2026, 10, 16), "Bid": 0.7128, "Ask": 0.7132},
    {"Tenor": "2M", "Settlement Date": datetime(2026, 11, 16), "Bid": 0.7124, "Ask": 0.7128},
]


def test_points_from_bid_ask_rows_sorted_with_columns():
    points, columns = points_from_rows(ROWS)
    assert columns == ["Tenor", "Settlement Date", "Bid", "Ask"]
    assert points == [(date(2026, 9, 21), pytest.approx(0.7132)), (date(2026, 10, 16), pytest.approx(0.7130)),
                      (date(2026, 11, 16), pytest.approx(0.7126))]


def test_points_prefer_mid_column_and_skip_bad_rows():
    rows = [{"Date": "2026-10-16", "Mid": 154.1, "Bid": 1.0, "Ask": 2.0},
            {"Date": "not a date", "Mid": 1.0},
            {"Date": "2026-11-16", "Mid": 0.0}]
    points, _ = points_from_rows(rows)
    assert points == [(date(2026, 10, 16), 154.1)]


def test_outright_exact_interp_from_spot_and_none_beyond():
    points, _ = points_from_rows(ROWS)
    assert outright_for_date(points, date(2026, 10, 16)) == (pytest.approx(0.7130), "EXACT")
    v, how = outright_for_date(points, date(2026, 10, 31))
    assert how == "INTERP" and 0.7126 < v < 0.7130
    v, how = outright_for_date(points, date(2026, 9, 16), spot=0.7140, spot_date=date(2026, 9, 14))
    assert how == "INTERP_FROM_SPOT" and 0.7132 < v < 0.7140
    assert outright_for_date(points, date(2026, 9, 16)) == (None, "")          # no spot given, before first tenor
    assert outright_for_date(points, date(2027, 1, 1)) == (None, "")           # never extrapolated
