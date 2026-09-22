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


# =========================================================================== historical curve (2026-09-21)
# HistoricalDataRequest does not serve SETTLE_DT (a static reference field), so a past
# day's pillar dates are computed by market convention unless Bloomberg did send them.
def test_spot_date_matches_the_ladders_rule_and_handles_t_plus_one_and_holidays():
    from data.bloomberg.fwd_curve import spot_date_for
    from engine.ladder.usd_marks import spot_date
    for day in (date(2026, 9, 14), date(2026, 9, 17), date(2026, 9, 18)):      # Mon, Thu, Fri
        assert spot_date_for(day, "EURUSD").isoformat() == spot_date(day.isoformat())
    assert spot_date_for(date(2026, 9, 18), "USDTRY") == date(2026, 9, 21)     # T+1 pair, over a weekend
    # a holiday ON the spot date rolls it forward; one in between does not count against the lag
    assert spot_date_for(date(2026, 9, 14), "EURUSD", frozenset({"2026-09-16"})) == date(2026, 9, 17)
    assert spot_date_for(date(2026, 9, 14), "EURUSD", frozenset({"2026-09-15"})) == date(2026, 9, 16)


def test_spot_date_for_is_the_calendars_own_rule_with_the_holidays_it_is_given():
    """2026-09-22: one spot-date rule, engine.pnl.calendar.spot_date; spot_date_for keeps its
    signature for this module's callers, passes the holidays through (the empty default means
    none, never config/holidays.txt behind the caller's back) and holds no rule of its own."""
    from data.bloomberg import fwd_curve
    from engine.pnl.calendar import spot_date
    import engine.pnl.calendar as cal
    assert fwd_curve._SPOT_LAG_ONE_DAY is cal._SPOT_LAG_ONE_DAY              # one set, re-exported, not a copy
    for day, pair, holidays in ((date(2026, 9, 4), "USDCAD", frozenset()), (date(2026, 9, 4), "USDJPY", frozenset()),
                                (date(2026, 9, 14), "EURUSD", frozenset({"2026-09-16"})),
                                (date(2026, 12, 31), "USDTRY", frozenset({"2027-01-01"}))):
        assert fwd_curve.spot_date_for(day, pair, holidays) == spot_date(day, pair, holidays)
    assert fwd_curve.spot_date_for(date(2026, 9, 4), "USDCAD") == date(2026, 9, 7)      # Labor Day: no holidays given


def test_tenor_settle_dates_by_market_convention():
    from data.bloomberg.fwd_curve import tenor_settle_date
    spot = date(2026, 9, 16)                                                   # a Wednesday
    assert tenor_settle_date(spot, "SP") == spot
    assert tenor_settle_date(spot, "1W") == date(2026, 9, 23)
    assert tenor_settle_date(spot, "1M") == date(2026, 10, 16)
    assert tenor_settle_date(spot, "2M") == date(2026, 11, 16)
    assert tenor_settle_date(spot, "1Y") == date(2027, 9, 16)
    assert tenor_settle_date(date(2026, 8, 12), "3M") == date(2026, 11, 12)
    assert tenor_settle_date(date(2026, 7, 15), "1M") == date(2026, 8, 17)     # 08-15 is a Saturday: following
    assert tenor_settle_date(date(2026, 9, 17), "1W", frozenset({"2026-09-24"})) == date(2026, 9, 25)
    # modified following: 2026-10-31 is a Saturday, and rolling forward would leave October
    assert tenor_settle_date(date(2026, 7, 31), "3M") == date(2026, 10, 30)
    # end-of-month rule: a spot date on the last good day of its month lands on the last good day
    assert tenor_settle_date(date(2026, 9, 30), "1M") == date(2026, 10, 30)
    assert tenor_settle_date(date(2026, 2, 27), "1M") == date(2026, 3, 31)
    assert tenor_settle_date(spot, "TN") is None and tenor_settle_date(spot, "ON") is None


def test_historical_curve_converts_points_like_the_live_tenor_path_at_computed_dates():
    from data.bloomberg.fwd_curve import UNIT_POINTS, historical_curve
    from data.bloomberg.pull_marks import outright_from_points
    rows = {"SP": {"PX_LAST": 147.2}, "1W": {"PX_LAST": -12.0}, "1M": {"PX_LAST": -50.0}, "1Y": {"PX_LAST": -550.0}}
    curve = historical_curve(date(2026, 9, 14), rows, spot=147.0, scale=100.0, pair="USDJPY")
    assert curve["unit"] == UNIT_POINTS and curve["reason"] == "" and curve["own_dates"] == set()
    # spot date pillar is spot itself (the SP ticker's own value is not used); negative points are kept
    assert curve["points"] == [
        (date(2026, 9, 16), 147.0),
        (date(2026, 9, 23), pytest.approx(outright_from_points(147.0, -12.0, 100.0))),
        (date(2026, 10, 16), pytest.approx(146.5)),
        (date(2027, 9, 16), pytest.approx(141.5)),
    ]
    # interpolating these outrights IS the live path's interpolation in points
    from data.bloomberg.pull_marks import TenorPoint, interpolate_forward_points
    target = date(2026, 10, 1)
    live = outright_from_points(147.0, interpolate_forward_points(target, [
        TenorPoint("1W", date(2026, 9, 23), -12.0), TenorPoint("1M", date(2026, 10, 16), -50.0)]), 100.0)
    assert outright_for_date(curve["points"], target)[0] == pytest.approx(live)


def test_historical_curve_uses_bloombergs_own_settle_dates_when_they_come_back():
    from data.bloomberg.fwd_curve import UNIT_OUTRIGHT, historical_curve
    rows = {"1W": {"PX_LAST": 1.1712, "SETTLE_DT": "2026-09-24"},              # Bloomberg's own date wins
            "1M": {"PX_LAST": 1.1730},                                         # none sent: computed
            "3M": {"PX_LAST": 1.1765, "SETTLE_DT": date(2026, 12, 17)}}
    curve = historical_curve(date(2026, 9, 14), rows, spot=1.1700, pair="EURUSD")
    assert curve["unit"] == UNIT_OUTRIGHT                                      # same order of magnitude as spot
    assert curve["points"] == [(date(2026, 9, 24), 1.1712), (date(2026, 10, 16), 1.1730), (date(2026, 12, 17), 1.1765)]
    assert curve["own_dates"] == {date(2026, 9, 24), date(2026, 12, 17)}


def test_historical_curve_gives_a_reason_and_no_points_rather_than_a_guess():
    from data.bloomberg.fwd_curve import historical_curve
    day, points_rows = date(2026, 9, 14), {"1W": {"PX_LAST": 4.0}, "1M": {"PX_LAST": 18.0}}
    assert "no forward tenor prices" in historical_curve(day, {}, 1.17, pair="EURUSD")["reason"]
    assert "no SPOT close" in historical_curve(day, points_rows, None, 10000.0, "EURUSD")["reason"]
    no_scale = historical_curve(day, points_rows, 1.17, None, "EURUSD")
    # 2026-09-21: the divisor is FWD_POINTS_SCALE, else 10 ** FWD_SCALE; with neither, the reason names both
    assert no_scale["points"] == [] and "neither FWD_POINTS_SCALE nor FWD_SCALE" in no_scale["reason"]
