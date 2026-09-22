"""engine/ladder/usd_marks.py: USD-per-unit marks per (currency, value date) for the
cash ladder's USD-equivalent cells (user's cash-ladder spec, 2026-09-18)."""
import pytest

from data.ingest import schema
from engine.ladder import usd_marks
from engine.ladder.usd_marks import (BASIS_FLAT, BASIS_INTERPOLATED, BASIS_NO_CURVE, BASIS_OUTRIGHT,
                                     BASIS_SPOT, forward_usd_rates, spot_date)

AS_OF = "2026-09-17"  # a Thursday: spot date is Monday 21 Sep


def _mark(conn, pair, settle, mark_type, value, source="BBG_BFXFORWARD"):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, pair, settle, mark_type, value, source, "2026-09-17T17:00:00-04:00"))


def _entry(rate, inverted, pair):
    return {"rate": rate, "inverted": inverted, "pair": pair, "as_of_date": AS_OF,
            "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False}


@pytest.fixture
def conn():
    c = schema.connect(":memory:")
    for pair, base, quote in (("USDJPY", "USD", "JPY"), ("AUDUSD", "AUD", "USD"), ("USDNOK", "USD", "NOK")):
        c.execute("INSERT INTO instruments VALUES (?,?,?,?,1,0,?,'9999-12-31')", (pair, "FX", base, quote, pair + " Curncy"))
    _mark(c, "USDJPY", AS_OF, "SPOT", 150.0)
    _mark(c, "USDJPY", "2026-10-21", "FWD_OUTRIGHT", 149.0)          # 1M tenor row
    _mark(c, "USDJPY", "2026-11-23", "FWD_OUTRIGHT", 148.0)          # 2M tenor row
    _mark(c, "USDJPY", "2026-11-05", "FWD_OUTRIGHT", 148.5, "BBG_INTERP")  # official broken-date fallback
    _mark(c, "AUDUSD", AS_OF, "SPOT", 0.66)
    _mark(c, "AUDUSD", "2026-10-21", "FWD_OUTRIGHT", 0.659)
    c.commit()
    return c


def test_spot_date_skips_weekends():
    assert spot_date("2026-09-17") == "2026-09-21"  # Thu -> Mon
    assert spot_date("2026-09-18") == "2026-09-22"  # Fri -> Tue
    assert spot_date("2026-09-14") == "2026-09-16"  # Mon -> Wed


def test_spot_date_is_the_shared_rule_per_pair():
    """One spot-date rule for the app (engine.pnl.calendar.spot_date, 2026-09-22): T+1 for
    USDCAD, T+2 for everything else, rolled forward off a holiday; a caller with no pair
    keeps the T+2 rule."""
    from engine.pnl import calendar as pnl_calendar
    none = frozenset()
    assert spot_date("2026-09-17", "USDCAD", none) == "2026-09-18"   # Thu -> Fri (T+1)
    assert spot_date("2026-09-18", "USDCAD", none) == "2026-09-21"   # Fri -> Mon (T+1 over the weekend)
    assert spot_date("2026-09-17", "USDJPY", none) == "2026-09-21"   # T+2 pairs unchanged
    assert spot_date("2026-09-17", "EURUSD", none) == "2026-09-21"
    assert spot_date("2026-09-17", "", none) == "2026-09-21"
    # a holiday on the spot date itself rolls it forward; one in between does not count
    assert spot_date("2026-11-24", "USDJPY", {"2026-11-26"}) == "2026-11-27"
    assert spot_date("2026-11-24", "USDJPY", {"2026-11-25"}) == "2026-11-26"
    assert spot_date("2026-11-25", "USDCAD", {"2026-11-26"}) == "2026-11-27"
    for day, pair in (("2026-09-17", "USDCAD"), ("2026-09-17", "USDJPY"), ("2026-11-24", "USDJPY")):
        assert spot_date(day, pair) == pnl_calendar.spot_date(day, pair).isoformat()


def test_usdcad_leg_is_spot_at_t_plus_1_and_forward_at_t_plus_2(conn):
    """The Ladder's column and the Blotter's curve agree on the spot date: a USDCAD leg at
    T+2 is one day along the curve, not spot (reviewer finding m-6)."""
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,1,0,?,'9999-12-31')",
                 ("USDCAD", "FX", "USD", "CAD", "USDCAD Curncy"))
    _mark(conn, "USDCAD", AS_OF, "SPOT", 1.36)
    _mark(conn, "USDCAD", "2026-10-19", "FWD_OUTRIGHT", 1.35)  # 1M for a T+1 pair
    conn.commit()
    rates = {"CAD": _entry(1.36, True, "USDCAD"), "JPY": _entry(150.0, True, "USDJPY")}
    out = forward_usd_rates(conn, rates, [("CAD", "2026-09-18"), ("CAD", "2026-09-21"),
                                          ("JPY", "2026-09-21"), ("JPY", "2026-09-22")])
    assert out[("CAD", "2026-09-18")]["basis"] == BASIS_SPOT and out[("CAD", "2026-09-18")]["quoted"] == 1.36
    t2 = out[("CAD", "2026-09-21")]
    assert t2["basis"] == BASIS_INTERPOLATED
    # spot pillar (18 Sep, 1.36) -> 1M (19 Oct, 1.35): 21 Sep is 3/31 of the way
    assert t2["quoted"] == pytest.approx(1.36 - 0.01 * 3 / 31)
    assert t2["rate"] == pytest.approx(1 / t2["quoted"])
    assert out[("JPY", "2026-09-21")]["basis"] == BASIS_SPOT        # T+2 pairs unchanged
    assert out[("JPY", "2026-09-22")]["basis"] == BASIS_INTERPOLATED


def test_forward_usd_rates_rolls_spot_off_a_holiday(conn, tmp_path, monkeypatch):
    """A config/holidays.txt holiday on the spot date moves it forward, so the day before
    the rolled date is still spot on the column."""
    from engine.pnl import calendar as pnl_calendar
    hol = tmp_path / "holidays.txt"
    hol.write_text("2026-09-21\n")
    monkeypatch.setattr(pnl_calendar, "_DEFAULT_HOLIDAYS_PATH", hol)
    rates = {"JPY": _entry(150.0, True, "USDJPY")}
    out = forward_usd_rates(conn, rates, [("JPY", "2026-09-21"), ("JPY", "2026-09-22"), ("JPY", "2026-09-23")])
    assert out[("JPY", "2026-09-22")]["basis"] == BASIS_SPOT      # rolled spot date: Tue 22 Sep
    assert out[("JPY", "2026-09-23")]["basis"] == BASIS_INTERPOLATED
    assert out[("JPY", "2026-09-21")]["basis"] == BASIS_SPOT      # before the spot date


def test_spot_on_or_before_spot_date_and_exact_outright(conn):
    rates = {"JPY": _entry(150.0, True, "USDJPY"), "AUD": _entry(0.66, False, "AUDUSD")}
    out = forward_usd_rates(conn, rates, [("JPY", AS_OF), ("JPY", "2026-09-21"), ("JPY", "2026-10-21"),
                                          ("AUD", "2026-10-21"), ("USD", "2026-10-21")])
    assert out[("JPY", AS_OF)]["basis"] == BASIS_SPOT and out[("JPY", AS_OF)]["rate"] == pytest.approx(1 / 150)
    assert out[("JPY", "2026-09-21")]["basis"] == BASIS_SPOT
    assert out[("JPY", "2026-10-21")] == {"rate": pytest.approx(1 / 149), "quoted": 149.0, "basis": BASIS_OUTRIGHT, "pair": "USDJPY"}
    assert out[("AUD", "2026-10-21")]["rate"] == pytest.approx(0.659)  # base of its pair: not inverted
    assert out[("USD", "2026-10-21")]["rate"] == 1.0


def test_interpolation_between_pillars_and_flat_beyond_last(conn):
    rates = {"JPY": _entry(150.0, True, "USDJPY")}
    out = forward_usd_rates(conn, rates, [("JPY", "2026-10-06"), ("JPY", "2026-11-05"), ("JPY", "2027-03-01")])
    # spot pillar (21 Sep, 150) -> 1M (21 Oct, 149): 6 Oct is 15/30 of the way
    mid = out[("JPY", "2026-10-06")]
    assert mid["basis"] == BASIS_INTERPOLATED and mid["quoted"] == pytest.approx(149.5)
    # the official BBG_INTERP broken-date row is an exact pillar, not re-interpolated
    assert out[("JPY", "2026-11-05")]["basis"] == BASIS_OUTRIGHT and out[("JPY", "2026-11-05")]["quoted"] == 148.5
    far = out[("JPY", "2027-03-01")]
    assert far["basis"] == BASIS_FLAT and far["quoted"] == 148.0


def test_no_forward_curve_falls_back_to_spot_and_says_so(conn):
    rates = {"NOK": _entry(10.5, True, "USDNOK")}
    out = forward_usd_rates(conn, rates, [("NOK", "2026-12-15")])
    assert out[("NOK", "2026-12-15")]["basis"] == BASIS_NO_CURVE
    assert out[("NOK", "2026-12-15")]["rate"] == pytest.approx(1 / 10.5)


def test_currency_without_spot_entry_gets_no_mark(conn):
    out = forward_usd_rates(conn, {}, [("SEK", "2026-12-15"), ("USD", "2026-12-15")])
    assert ("SEK", "2026-12-15") not in out
    assert out[("USD", "2026-12-15")]["rate"] == 1.0


def test_settled_sentinel_is_marked_at_spot(conn):
    from engine.ladder.exposure_adapter import SETTLED
    rates = {"JPY": _entry(150.0, True, "USDJPY")}
    out = forward_usd_rates(conn, rates, [("JPY", SETTLED)])
    assert out[("JPY", SETTLED)]["basis"] == BASIS_SPOT and out[("JPY", SETTLED)]["quoted"] == 150.0


def test_only_the_spot_entrys_own_day_curve_is_read(conn):
    """Forwards are read for the as_of_date the spot entry carries, never another day's."""
    rates = {"JPY": {**_entry(150.0, True, "USDJPY"), "as_of_date": "2026-09-16"}}
    out = forward_usd_rates(conn, rates, [("JPY", "2026-10-21")])
    assert out[("JPY", "2026-10-21")]["basis"] == BASIS_NO_CURVE
    assert usd_marks._FWD_SQL.count(":as_of") == 1
