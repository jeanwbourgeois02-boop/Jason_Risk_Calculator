"""engine/pnl/ledger.py: realise on settlement, ltd, period P&L. Pure sqlite."""
import math

import pytest

from data.ingest import schema
from engine.pnl import ledger


def _insert_instruments(conn, rows):
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", rows)


def _insert_trade(conn, trade_id, instrument_id, product, trade_date, quantity, price,
                  strategy="HAHY7", theme=""):
    # 'XLSX' (2026-09-16, trades_official double-count fix): ledger's open-trade
    # queries now read trades_official, which excludes source='BNP' by design.
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, product, trade_id, trade_date, quantity, price,
         "acc", "cp", strategy, "t", "d", theme),
    )


def _insert_legs(conn, rows):
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _insert_marks(conn, rows):
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)


def _db():
    conn = schema.connect()
    _insert_instruments(conn, [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
    ])
    # a1: sold 1m AUD @0.65 settling 09-10 (settles before as_of 09-14); a2: open, settles 09-30
    # j1: bought 150m JPY for 1m USD settling 09-12 (settled); no spot for JPY pair on/before -> unrealisable
    _insert_trade(conn, "a1", "AUDUSD", "FX_FWD", "2026-08-10", -1e6, 0.65)
    _insert_trade(conn, "a2", "AUDUSD", "FX_FWD", "2026-09-14", 2e6, 0.70)
    _insert_trade(conn, "j1", "USDJPY", "FX_FWD", "2026-08-10", -1e6, 150.0)
    _insert_legs(conn, [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a2", 1, "FX_NEAR", "AUD", 2e6, "2026-09-14", "2026-09-30", 0.70, 1),
        ("a2", 2, "FX_NEAR", "USD", -1400000, "2026-09-14", "2026-09-30", 0.70, 1),
        ("j1", 1, "FX_NEAR", "USD", -1e6, "2026-08-10", "2026-09-12", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", 150e6, "2026-08-10", "2026-09-12", 150.0, 1),
    ])
    _insert_marks(conn, [
        ("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.62, "BBG_BFXFORWARD", "2026-09-09T15:00:00+00:00"),  # last before 09-10
        ("2026-09-14", "AUDUSD", "2026-09-30", "FWD_OUTRIGHT", 0.71, "BBG_BFXFORWARD", "2026-09-14T15:00:00+00:00"),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.71, "BBG_BFXFORWARD", "2026-09-14T15:00:00+00:00"),
    ])
    conn.commit()
    return conn


def test_realise_settled_freezes_once_with_prior_spot_and_reports_unrealisable():
    conn = _db()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["realised"] == 1 and [u["trade_id"] for u in res["unrealisable"]] == ["j1"]
    rows = ledger.realised_rows(conn, "2026-09-14")
    a1 = rows.iloc[0]
    assert a1["trade_id"] == "a1" and a1["spot_as_of_date"] == "2026-09-09"
    assert a1["pnl_usd"] == pytest.approx(-1e6 * 0.62 - (-650000)) == pytest.approx(30000)  # sold AUD, AUD fell
    assert "last before settlement" in a1["note"]
    # idempotent: second call realises nothing new and never re-prices a1 at the newer 0.71 spot
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 0
    assert ledger.realised_rows(conn, "2026-09-14").iloc[0]["pnl_usd"] == pytest.approx(30000)
    assert ledger.realised_rows(conn, "2026-09-10").empty  # earlier as_of excludes it


def test_ltd_and_periods_with_a_settled_and_an_open_trade():
    conn = _db()
    ledger.realise_settled(conn, "2026-09-14")
    # j1 unrealisable -> ltd is NaN today
    assert math.isnan(ledger.ltd(conn, "2026-09-14"))
    periods = ledger.period_pnl(conn, "2026-09-14")
    assert not periods["daily"]["available"]
    assert "missing mark" in periods["daily"]["reason"]


def test_ltd_available_when_every_open_and_settled_trade_prices():
    conn = schema.connect()
    _insert_instruments(conn, [("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31")])
    _insert_trade(conn, "a1", "AUDUSD", "FX_FWD", "2026-08-10", -1e6, 0.65)
    _insert_trade(conn, "a2", "AUDUSD", "FX_FWD", "2026-09-14", 2e6, 0.70)
    _insert_legs(conn, [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-09-10", 0.65, 1),
        ("a2", 1, "FX_NEAR", "AUD", 2e6, "2026-09-14", "2026-09-30", 0.70, 1),
        ("a2", 2, "FX_NEAR", "USD", -1400000, "2026-09-14", "2026-09-30", 0.70, 1),
    ])
    _insert_marks(conn, [
        ("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.62, "BBG_BFXFORWARD", "t"),
        ("2026-09-14", "AUDUSD", "2026-09-30", "FWD_OUTRIGHT", 0.71, "BBG_BFXFORWARD", "t"),
        ("2026-09-14", "AUDUSD", "2026-09-14", "SPOT", 0.71, "BBG_BFXFORWARD", "t"),
    ])
    conn.commit()
    ledger.realise_settled(conn, "2026-09-14")
    ltd_today = ledger.ltd(conn, "2026-09-14")
    realised = -1e6 * 0.62 - (-650000)          # 30000
    unrealised = 2e6 * 0.71 - 1400000            # 20000
    assert ltd_today == pytest.approx(realised + unrealised)
    periods = ledger.period_pnl(conn, "2026-09-14")
    assert periods["trading"]["available"] and periods["trading"]["value"] == pytest.approx(unrealised)
    # reference day 09-11 only sees a1 (already settled and realised by then); a2 trades
    # in on 09-14 so it drops out of the 09-11 book -> daily isolates a2's unrealised P&L
    assert periods["daily"]["available"] and periods["daily"]["value"] == pytest.approx(unrealised)


def test_first_trading_day_ltd_is_zero_not_unavailable():
    conn = schema.connect()
    assert ledger.ltd(conn, "2026-09-14") == 0.0
    periods = ledger.period_pnl(conn, "2026-09-14")
    for key in ("daily", "d5", "mtd", "ytd"):
        assert periods[key]["available"] and periods[key]["value"] == 0.0
    assert periods["trading"]["available"] and periods["trading"]["value"] == 0.0


def test_holiday_shifts_prev_business_day(tmp_path, monkeypatch):
    holidays_file = tmp_path / "holidays.txt"
    holidays_file.write_text("2026-09-11\n")  # Friday before as_of Monday 09-14
    monkeypatch.setattr("engine.pnl.aggregate._DEFAULT_HOLIDAYS_PATH", holidays_file)
    conn = schema.connect()
    periods = ledger.period_pnl(conn, "2026-09-14")
    assert periods["daily"]["ref_date"] == "2026-09-10"  # skips the holiday Friday


def test_period_pnl_by_groups_rows():
    conn = _db()
    ledger.realise_settled(conn, "2026-09-14")
    by_pair = ledger.period_pnl_by(conn, "2026-09-14", "instrument_id")
    assert "AUDUSD" in by_pair and "USDJPY" in by_pair
    # AUDUSD side is fully priced (a1 realised, a2 priced) -> available; USDJPY (j1) is not
    assert by_pair["AUDUSD"]["daily"]["available"]
    assert not by_pair["USDJPY"]["daily"]["available"]
    with pytest.raises(ValueError):
        ledger.period_pnl_by(conn, "2026-09-14", "not_a_key")


# --------------------------------------------------------------------------- IRS / options (2026-09-17)
def _irs_db():
    conn = schema.connect()
    _insert_instruments(conn, [
        ("IRSOIS-USD-1", "IRS", "USD", "USD", 1, 0, "IRSOIS-USD-1", "2026-09-10"),
        ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
        ("EURUSD091026C", "FX_OPTION", "EUR", "USD", 1, 0, "", "2026-09-10"),
    ])
    _insert_trade(conn, "s1", "IRSOIS-USD-1", "IRS", "2026-03-01", 10e6, 0.04)
    _insert_legs(conn, [
        ("s1", 1, "FIXED", "USD", -10e6, "2026-03-03", "2026-09-10", 0.04, 0),
        ("s1", 2, "FLOAT", "USD", 10e6, "2026-03-03", "2026-09-10", 0.0, 0),
    ])
    _insert_trade(conn, "o1", "EURUSD091026C", "FX_OPTION", "2026-03-01", 1e6, 0.0050)
    _insert_legs(conn, [("o1", 1, "NOTIONAL", "EUR", 1e6, "2026-03-01", "2026-09-10", 0.0050, 0)])
    conn.commit()
    return conn


def test_realise_settled_freezes_matured_swap_at_pv_plus_cashflows():
    """Item 52: a swap past maturity freezes at the last official PV_USD + CASHFLOW_USD on
    or before maturity (here PV is 0 on the maturity date, the coupons are the result)."""
    conn = _irs_db()
    _insert_marks(conn, [
        ("2026-09-09", "IRSOIS-USD-1", "2026-09-10", "PV_USD", 2_000.0, "QL_PRICER", "t"),
        ("2026-09-09", "IRSOIS-USD-1", "2026-09-10", "CASHFLOW_USD", 0.0, "QL_PRICER", "t"),
        ("2026-09-10", "IRSOIS-USD-1", "2026-09-10", "PV_USD", 0.0, "QL_PRICER", "t"),
        ("2026-09-10", "IRSOIS-USD-1", "2026-09-10", "CASHFLOW_USD", 2_100.0, "QL_PRICER", "t"),
    ])
    out = ledger.realise_settled(conn, "2026-09-14")
    assert any(u["trade_id"] == "o1" for u in out["unrealisable"])   # no PREMIUM on file
    row = conn.execute("SELECT product, mark_type, pnl_usd, spot_usd_per_local, note FROM realised_pnl "
                       "WHERE trade_id = 's1'").fetchone()
    assert row == ("IRS", "PV_USD", 2_100.0, 1.0, "")
    vb = ledger.value_book(conn, "2026-09-14")
    assert vb.loc[vb["trade_id"] == "s1", "pnl_usd"].iloc[0] == 2_100.0
    # a re-run never re-freezes
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 0


def test_realise_settled_swap_without_cashflow_mark_is_unrealisable():
    conn = _irs_db()
    _insert_marks(conn, [("2026-09-10", "IRSOIS-USD-1", "2026-09-10", "PV_USD", 0.0, "QL_PRICER", "t")])
    out = ledger.realise_settled(conn, "2026-09-14")
    reasons = {u["trade_id"]: u["reason"] for u in out["unrealisable"]}
    assert "CASHFLOW_USD" in reasons["s1"]
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 0


def test_realise_settled_freezes_expired_option_at_last_premium_and_spot():
    conn = _irs_db()
    _insert_marks(conn, [
        ("2026-09-09", "EURUSD091026C", "2026-09-10", "PREMIUM", 0.0080, "QL_OPTIONS_PRICER", "t"),
        ("2026-09-09", "EURUSD", "2026-09-09", "SPOT", 1.20, "BBG_BFXFORWARD", "t"),
    ])
    ledger.realise_settled(conn, "2026-09-14")
    row = conn.execute("SELECT mark_type, pnl_usd, spot_as_of_date, note FROM realised_pnl WHERE trade_id = 'o1'").fetchone()
    # 1m EUR * (0.0080 - 0.0050) = 3,000 EUR * 1.20
    assert row[0] == "PREMIUM" and row[1] == pytest.approx(3_600.0) and row[2] == "2026-09-09"
    assert "last before expiry" in row[3]
