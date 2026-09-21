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


def test_realise_settled_freezes_every_trade_of_a_closed_out_option_at_the_closing_fill():
    """An option bought and sold back in full needs no PREMIUM (user, 2026-09-21: "for options
    closed out theyre not live"). The purchase here was frozen at a PREMIUM while the sale's
    strike was not on file yet; once it is, both are frozen alike or the total would be wrong."""
    conn = _irs_db()
    _insert_instruments(conn, [("EURUSD091026C-2", "FX_OPTION", "EUR", "USD", 1, 0, "", "2026-09-10")])
    _insert_trade(conn, "o2", "EURUSD091026C-2", "FX_OPTION", "2026-04-01", -1e6, 0.0070)
    _insert_legs(conn, [("o2", 1, "NOTIONAL", "EUR", -1e6, "2026-04-01", "2026-09-10", 0.0070, 0)])
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES ('EURUSD091026C', 1.15, 'CALL', 'VANILLA')")
    _insert_marks(conn, [
        ("2026-09-10", "EURUSD091026C", "2026-09-10", "PREMIUM", 0.0200, "QL_OPTIONS_PRICER", "t"),
        ("2026-09-10", "EURUSD", "2026-09-10", "SPOT", 1.20, "BBG_BFXFORWARD", "t"),
    ])
    out = ledger.realise_settled(conn, "2026-09-14")
    assert out["realised"] == 1 and [u["trade_id"] for u in out["unrealisable"]] == ["s1", "o2"]   # the swap has no marks
    assert conn.execute("SELECT mark_type FROM realised_pnl WHERE trade_id = 'o1'").fetchone() == ("PREMIUM",)

    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES ('EURUSD091026C-2', 1.15, 'CALL', 'VANILLA')")
    ledger.realise_settled(conn, "2026-09-14")
    rows = dict(conn.execute("SELECT trade_id, pnl_usd FROM realised_pnl WHERE mark_type = 'CLOSE_OUT'").fetchall())
    # 1m EUR * (0.0070 - 0.0050) = 2,000 EUR * 1.20 on the purchase, nothing on the sale
    assert rows == {"o1": pytest.approx(2_400.0), "o2": pytest.approx(0.0)}
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 0   # a re-run never re-freezes


# --------------------------------------------------------------------------- 2026-09-18
# Root cause of the Bloomberg-PC "could not convert string to float: '<a date>'" that
# blanked every Blotter view and every headline: `_insert_realised` was a positional
# INSERT, and `realised_pnl`'s physical column order depends on the database's age
# (`product` / `mark_type` sit in the MIDDLE of a fresh table but are APPENDED by
# `schema._migrate_columns` to a table created before 2026-09-15).

_REALISED_DDL_2026_09_14 = """
CREATE TABLE realised_pnl (
  trade_id            TEXT PRIMARY KEY REFERENCES trades,
  instrument_id       TEXT NOT NULL,
  currency            TEXT NOT NULL,
  settle_date         TEXT NOT NULL,
  local_amount        REAL NOT NULL,
  usd_entry_amount    REAL NOT NULL,
  spot_usd_per_local  REAL NOT NULL,
  spot_as_of_date     TEXT NOT NULL,
  spot_source         TEXT NOT NULL,
  pnl_usd             REAL NOT NULL,
  frozen_at           TEXT NOT NULL,
  note                TEXT NOT NULL
);
"""


def _migrated_db():
    """`_db()` on a database whose `realised_pnl` was created with the original 12
    columns and then brought up to date by `create_schema` -- the dev database's and the
    Bloomberg PC's shape: `product` and `mark_type` are the LAST two columns."""
    conn = _db()
    conn.execute("DROP TABLE realised_pnl")
    conn.executescript(_REALISED_DDL_2026_09_14)
    schema.create_schema(conn)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(realised_pnl)")]
    assert columns[-2:] == ["product", "mark_type"] and columns[2] == "currency"
    return conn


def test_realise_settled_writes_every_value_to_its_own_column_on_a_migrated_database():
    conn = _migrated_db()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["realised"] == 1 and res["repaired"] == []
    row = conn.execute("SELECT typeof(pnl_usd), pnl_usd, typeof(local_amount), local_amount, currency, settle_date, "
                       "product, mark_type, spot_as_of_date, spot_source FROM realised_pnl WHERE trade_id = 'a1'").fetchone()
    assert row == ("real", pytest.approx(30000), "real", -1e6, "USD", "2026-09-10",
                   "FX_FWD", "SPOT", "2026-09-09", "BBG_BFXFORWARD")
    assert ledger.value_book(conn, "2026-09-14").set_index("trade_id").loc["a1", "pnl_usd"] == pytest.approx(30000)


def _misalign_a1(conn):
    """Exactly what the old positional INSERT did to trade a1 on a migrated database."""
    conn.execute("INSERT INTO realised_pnl VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("a1", "AUDUSD", "FX_FWD", "USD", "2026-09-10", -1e6, -650000.0, "SPOT", 0.62,
                  "2026-09-09", "BBG_BFXFORWARD", 30000.0, "2026-09-14T17:00:00-04:00", ""))
    conn.commit()
    # pnl_usd received spot_as_of_date: a date string, kept as TEXT in a REAL column.
    assert conn.execute("SELECT typeof(pnl_usd), pnl_usd FROM realised_pnl").fetchone() == ("text", "2026-09-09")


def test_realise_settled_deletes_and_refreezes_the_rows_the_positional_insert_misaligned():
    conn = _migrated_db()
    _misalign_a1(conn)
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["repaired"] == ["a1"] and res["realised"] == 1
    assert conn.execute("SELECT typeof(pnl_usd), pnl_usd, spot_as_of_date FROM realised_pnl WHERE trade_id = 'a1'"
                        ).fetchone() == ("real", pytest.approx(30000), "2026-09-09")
    # a healthy row is never touched again
    again = ledger.realise_settled(conn, "2026-09-14")
    assert again["repaired"] == [] and again["realised"] == 0


def test_value_book_prices_a_trade_whose_realised_row_is_unreadable_as_not_yet_frozen():
    """Before any pull has repaired it: the misaligned row must not raise (it blanked the
    whole app), must not be read as a number, and the trade is valued exactly as a settled
    trade with no realised row -- the figure `realise_settled` then persists."""
    conn = _migrated_db()
    _misalign_a1(conn)
    vb = ledger.value_book(conn, "2026-09-14").set_index("trade_id")
    assert vb.loc["a1", "pnl_usd"] == pytest.approx(30000) and vb.loc["a1", "reason"] == ""
    assert "realised_pnl.pnl_usd is not a number ('2026-09-09')" in vb.loc["a1", "note"]
    assert vb.loc["a2", "pnl_usd"] == pytest.approx(2e6 * (0.71 - 0.70))  # every other trade prices
    ledger.realise_settled(conn, "2026-09-14")
    after = ledger.value_book(conn, "2026-09-14").set_index("trade_id")
    assert after.loc["a1", "pnl_usd"] == pytest.approx(vb.loc["a1", "pnl_usd"])


def test_one_trade_with_a_text_price_is_unrealisable_and_the_rest_is_still_realised():
    conn = _db()
    _insert_trade(conn, "a3", "AUDUSD", "FX_FWD", "2026-08-10", -1e6, 0.66)
    _insert_legs(conn, [
        ("a3", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-09-10", 0.66, 1),
        ("a3", 2, "FX_NEAR", "USD", 660000, "2026-08-10", "2026-09-10", 0.66, 1),
    ])
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 'a3'")
    conn.commit()
    assert conn.execute("SELECT typeof(price) FROM trades WHERE trade_id = 'a3'").fetchone()[0] == "text"
    res = ledger.realise_settled(conn, "2026-09-14")
    reasons = {u["trade_id"]: u["reason"] for u in res["unrealisable"]}
    assert res["realised"] == 1  # a1
    assert reasons["a3"] == "trade a3: trades.price is not a number ('24-Jul')"
    assert [r[0] for r in conn.execute("SELECT trade_id FROM realised_pnl")] == ["a1"]


def test_a_text_mark_makes_the_trades_that_need_it_unrealisable_and_names_the_mark():
    conn = _db()
    conn.execute("UPDATE marks SET value = '24-Jul' WHERE as_of_date = '2026-09-09' AND mark_type = 'SPOT'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    reasons = {u["trade_id"]: u["reason"] for u in res["unrealisable"]}
    assert res["realised"] == 0
    assert reasons["a1"] == "trade a1: marks.value (SPOT for AUDUSD on 2026-09-09) is not a number ('24-Jul')"
