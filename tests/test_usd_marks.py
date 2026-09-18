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
