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
        ("2026-04-01", "EURUSD", "2026-04-01", "SPOT", 1.10, "BBG_BFXFORWARD", "t"),   # the close-out date's spot
    ])
    out = ledger.realise_settled(conn, "2026-09-14")
    assert out["realised"] == 1 and [u["trade_id"] for u in out["unrealisable"]] == ["s1", "o2"]   # the swap has no marks
    assert conn.execute("SELECT mark_type FROM realised_pnl WHERE trade_id = 'o1'").fetchone() == ("PREMIUM",)

    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES ('EURUSD091026C-2', 1.15, 'CALL', 'VANILLA')")
    ledger.realise_settled(conn, "2026-09-14")
    rows = dict(conn.execute("SELECT trade_id, pnl_usd FROM realised_pnl WHERE mark_type = 'CLOSE_OUT'").fetchall())
    # 1m EUR * (0.0070 - 0.0050) = 2,000 EUR on the purchase, nothing on the sale, in dollars at the
    # close-out date's 1.10 (user, 2026-09-21: "of course you freeze the usd converstion"), not expiry's 1.20
    assert rows == {"o1": pytest.approx(2_200.0), "o2": pytest.approx(0.0)}
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 0   # a re-run never re-freezes

    # a row frozen at the expiry date's spot (the rule of a few hours on 2026-09-21) is brought into line
    conn.execute("UPDATE realised_pnl SET pnl_usd = 2400.0, spot_as_of_date = '2026-09-10' WHERE trade_id = 'o1'")
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 1
    assert conn.execute("SELECT pnl_usd, spot_as_of_date FROM realised_pnl WHERE trade_id = 'o1'").fetchone() == (
        pytest.approx(2_200.0), "2026-04-01")


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


# --------------------------------------------------------------------------- NDF fixing (2026-09-22)
# User: "the exit price is the fix on that day, as pulled from bbg"; "each ndf has a unique fix".
# The freeze of an NDF ticket reads the official NDF_FIX dated its fixing date exactly, never the
# last fix on or before it; with none, the last official SPOT on or before the fixing date. A row
# frozen at a spot is dropped and frozen again once the exact-day fix lands ('refrozen'), and a
# row frozen at a fix of another day is dropped and frozen again by the same rule.


def _ndf_db():
    """b1: an NDF, bought 1m USD against BRL at 5.20, value Wed 2026-09-16, fixing Mon 09-14;
    j1: a deliverable USDJPY forward on the same dates. Spots on the fixing date and the value
    date; no fix on file yet."""
    conn = schema.connect()
    _insert_instruments(conn, [
        ("USDBRL", "FX", "USD", "BRL", 1, 1, "USDBRL Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
    ])
    for trade_id, pair, fill in (("b1", "USDBRL", 5.20), ("j1", "USDJPY", 150.0)):
        _insert_trade(conn, trade_id, pair, "FX_FWD", "2026-08-14", 1e6, fill)
        _insert_legs(conn, [
            (trade_id, 1, "FX_NEAR", "USD", 1e6, "2026-08-14", "2026-09-16", fill, 0),
            (trade_id, 2, "FX_NEAR", pair[3:], -1e6 * fill, "2026-08-14", "2026-09-16", fill, 0),
        ])
    _insert_marks(conn, [
        ("2026-09-14", "USDBRL", "2026-09-14", "SPOT", 5.25, "BBG_BFXFORWARD", "t"),
        ("2026-09-16", "USDBRL", "2026-09-16", "SPOT", 5.35, "BBG_BFXFORWARD", "t"),
        ("2026-09-14", "USDJPY", "2026-09-14", "SPOT", 151.0, "BBG_BFXFORWARD", "t"),
        ("2026-09-16", "USDJPY", "2026-09-16", "SPOT", 152.0, "BBG_BFXFORWARD", "t"),
    ])
    conn.commit()
    return conn


def _brl_fix(conn, day, value):
    _insert_marks(conn, [(day, "USDBRL", day, "NDF_FIX", value, "BBG_BDH", "t")])
    conn.commit()


def _frozen_row(conn, trade_id):
    return conn.execute("SELECT mark_type, spot_as_of_date, pnl_usd, note FROM realised_pnl WHERE trade_id = ?",
                        (trade_id,)).fetchone()


def test_ndf_freezes_at_the_fix_of_its_fixing_date_exactly_never_a_neighbouring_days():
    conn = _ndf_db()
    _brl_fix(conn, "2026-09-11", 5.30)   # the Friday before: not this ticket's fix
    _brl_fix(conn, "2026-09-15", 5.40)   # the day after: not this ticket's fix either
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["realised"] == 2 and res["refrozen"] == [] and res["unrealisable"] == []
    b1 = _frozen_row(conn, "b1")
    assert b1[:2] == ("SPOT", "2026-09-14") and b1[2] == pytest.approx(1e6 * (5.25 - 5.20) / 5.25)
    assert b1[3] == "spot dated 2026-09-14 (NDF fixing), converted at that spot"
    # the fixing date's own fix lands: the spot-frozen row is dropped and the ticket frozen at the fix,
    # the P&L converted at the fix itself (user decision 2026-09-22), spot_usd_per_local = 1 / FIX
    _brl_fix(conn, "2026-09-14", 5.22)
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == ["b1"] and res["realised"] == 1
    b1 = _frozen_row(conn, "b1")
    assert b1[:2] == ("NDF_FIX", "2026-09-14") and b1[2] == pytest.approx(1e6 * (5.22 - 5.20) / 5.22)
    assert b1[3] == "official fixing dated 2026-09-14 (NDF fixing), converted at the fixing"
    assert conn.execute("SELECT spot_usd_per_local, spot_source FROM realised_pnl WHERE trade_id = 'b1'").fetchone() == (
        pytest.approx(1 / 5.22), "BBG_BDH")
    # and stays there: a further call drops and freezes nothing
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == [] and res["realised"] == 0 and _frozen_row(conn, "b1") == b1


def test_a_row_frozen_at_a_fix_of_another_day_is_dropped_and_frozen_again():
    conn = _ndf_db()
    _brl_fix(conn, "2026-09-11", 5.30)
    ledger.realise_settled(conn, "2026-09-21")
    # what the rule of a few hours on 2026-09-22 wrote: the last fix on or before the fixing date
    conn.execute("UPDATE realised_pnl SET mark_type = 'NDF_FIX', spot_as_of_date = '2026-09-11', pnl_usd = 1.0, "
                 "note = 'official fixing dated 2026-09-11 (last before fixing)' WHERE trade_id = 'b1'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == ["b1"] and res["realised"] == 1
    b1 = _frozen_row(conn, "b1")
    assert b1[:2] == ("SPOT", "2026-09-14") and b1[2] == pytest.approx(1e6 * (5.25 - 5.20) / 5.25)
    assert b1[3] == "spot dated 2026-09-14 (NDF fixing), converted at that spot"


def test_a_deliverable_pairs_row_is_never_touched_by_the_ndf_guard():
    conn = _ndf_db()
    ledger.realise_settled(conn, "2026-09-21")
    j1 = conn.execute("SELECT * FROM realised_pnl WHERE trade_id = 'j1'").fetchone()
    assert _frozen_row(conn, "j1")[:2] == ("SPOT", "2026-09-16")
    _brl_fix(conn, "2026-09-14", 5.22)
    _insert_marks(conn, [("2026-09-16", "USDJPY", "2026-09-16", "NDF_FIX", 999.0, "BBG_BDH", "t")])   # a deliverable pair never reads one
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == ["b1"] and res["realised"] == 1
    assert conn.execute("SELECT * FROM realised_pnl WHERE trade_id = 'j1'").fetchone() == j1


def test_a_past_day_call_never_drops_a_row_it_cannot_freeze_again():
    """Reviewer W1, 2026-09-22: the purges had no as_of gate while the refreeze covers
    settle_date < as_of only, so realise_settled(<past day>) from the backfill dropped a row
    and froze nothing (refrozen=['b1'], realised=0, no row). A row is dropped only by a call
    whose as_of is past the value date, the one that re-freezes it."""
    conn = _ndf_db()
    ledger.realise_settled(conn, "2026-09-21")
    before = _frozen_row(conn, "b1")
    assert before[:2] == ("SPOT", "2026-09-14")
    _brl_fix(conn, "2026-09-14", 5.22)                                  # lands in a backfill span ending 09-15
    res = ledger.realise_settled(conn, "2026-09-15")                    # b1's value date 09-16 is not before as_of
    assert res["refrozen"] == [] and res["realised"] == 0 and _frozen_row(conn, "b1") == before
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == ["b1"] and res["realised"] == 1
    assert _frozen_row(conn, "b1")[:2] == ("NDF_FIX", "2026-09-14")


def test_the_frozen_row_is_the_blotters_own_estimate_when_the_fixing_date_has_no_close():
    """Reviewer W2, 2026-09-22: closes either side of the fixing date and none on it -- the
    Blotter showed the near-marks estimate (between the closes) from the fixing on while the
    ledger froze at the earlier close alone. The ledger now records the Blotter's figure: the
    same number, the estimate named."""
    conn = _ndf_db()
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDBRL'")
    _insert_marks(conn, [
        ("2026-09-11", "USDBRL", "2026-09-11", "SPOT", 5.20, "BBG_BFXFORWARD", "t"),
        ("2026-09-15", "USDBRL", "2026-09-15", "SPOT", 5.40, "BBG_BFXFORWARD", "t"),
    ])
    conn.commit()
    est = 5.20 + 0.75 * (5.40 - 5.20)      # 3 of the 4 calendar days from 09-11 to 09-15
    shown = ledger.value_book(conn, "2026-09-15").set_index("trade_id").loc["b1"]
    assert shown["status"] == "OPEN" and shown["mark"] == pytest.approx(est)
    assert shown["pnl_usd"] == pytest.approx(1e6 * (est - 5.20) / est)
    assert ledger.realise_settled(conn, "2026-09-21")["realised"] == 2
    b1 = conn.execute("SELECT mark_type, spot_as_of_date, spot_source, pnl_usd, note FROM realised_pnl "
                      "WHERE trade_id = 'b1'").fetchone()
    assert b1[:3] == ("SPOT", "2026-09-14", "INTERP: SPOT between the 2026-09-11 and 2026-09-15 closes")
    assert b1[3] == shown["pnl_usd"]       # the very number the Blotter showed, not 1e6 * (5.20 - 5.20) / 5.20
    assert b1[4] == ("spot dated 2026-09-14 (NDF fixing), converted at that spot; no official fixing on file: "
                     "at the spot of 2026-09-14 instead (INTERP: SPOT between the 2026-09-11 and 2026-09-15 closes)")
    assert ledger.value_book(conn, "2026-09-21").set_index("trade_id").loc["b1", "pnl_usd"] == shown["pnl_usd"]


# --------------------------------------------------------------------------- re-freeze at the close (2026-09-22)
# User decision: a frozen row is what the official marks on file give for its date and mark
# type, or it is dropped and frozen again by the same rule in the same call (`purge_superseded`).
# A row whose inputs did not change is untouched and keeps its frozen_at.


def _a1(conn):
    return conn.execute("SELECT spot_usd_per_local, spot_as_of_date, pnl_usd, frozen_at FROM realised_pnl "
                        "WHERE trade_id = 'a1'").fetchone()


def test_a_row_frozen_at_a_live_press_is_frozen_again_once_the_close_replaces_that_mark():
    conn = _db()
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 1
    before = _a1(conn)
    assert before[:3] == (0.62, "2026-09-09", pytest.approx(30000))
    # the backfill lands 09-09's 15:00 close under the same key: the live 0.62 becomes 0.63
    conn.execute("UPDATE marks SET value = 0.63, snapped_at = '2026-09-09T15:00:00-04:00' "
                 "WHERE as_of_date = '2026-09-09' AND instrument_id = 'AUDUSD' AND mark_type = 'SPOT'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == ["a1"] and res["realised"] == 1 and res["repaired"] == []
    after = _a1(conn)
    assert after[:3] == (0.63, "2026-09-09", pytest.approx(-1e6 * 0.63 + 650000))
    assert ledger.value_book(conn, "2026-09-14").set_index("trade_id").loc["a1", "pnl_usd"] == pytest.approx(20000)
    # and stays there: the next call finds the row is what the marks give and leaves it, frozen_at included
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == [] and res["realised"] == 0 and _a1(conn) == after


def test_an_unchanged_row_is_untouched_and_a_row_the_rule_cannot_recompute_keeps_its_figure():
    conn = _db()
    ledger.realise_settled(conn, "2026-09-14")
    before = _a1(conn)
    # a NEWER spot never moves a settled trade (the freeze reads on or before settlement)
    _insert_marks(conn, [("2026-09-11", "AUDUSD", "2026-09-11", "SPOT", 0.70, "BBG_BFXFORWARD", "t")])
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == [] and res["realised"] == 0 and _a1(conn) == before
    # the mark it was frozen from is gone: nothing to compare against, the row keeps its figure
    conn.execute("DELETE FROM marks WHERE as_of_date = '2026-09-09'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == [] and _a1(conn) == before
    # a past-day call (the backfill's) never looks at a row whose value date it is not past
    _insert_marks(conn, [("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.64, "BBG_BFXFORWARD", "t")])
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-10")
    assert res["refrozen"] == [] and _a1(conn) == before
    assert ledger.realise_settled(conn, "2026-09-14")["refrozen"] == ["a1"]


def test_a_future_frozen_at_a_live_px_last_is_frozen_again_at_that_days_settlement_price():
    conn = schema.connect()
    _insert_instruments(conn, [("ESU6 Index", "FUTURE", "ES", "USD", 50, 0, "ESU6 Index", "2026-09-18")])
    _insert_trade(conn, "f1", "ESU6 Index", "FUTURE", "2026-08-10", 2, 6400.0)
    _insert_legs(conn, [("f1", 1, "NOTIONAL", "USD", 2 * 50 * 6400.0, "2026-08-10", "2026-09-18", 6400.0, 0)])
    _insert_marks(conn, [("2026-09-18", "ESU6 Index", "2026-09-18", "FUTURE_PX", 6450.0, "BBG_BDH", "2026-09-18T14:12:00-04:00")])
    conn.commit()
    assert ledger.realise_settled(conn, "2026-09-21")["realised"] == 1
    assert conn.execute("SELECT pnl_usd FROM realised_pnl WHERE trade_id = 'f1'").fetchone()[0] == pytest.approx(2 * 50 * 50.0)
    conn.execute("UPDATE marks SET value = 6460.0, snapped_at = '2026-09-18T16:00:00-04:00' WHERE instrument_id = 'ESU6 Index'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == ["f1"] and res["realised"] == 1
    assert conn.execute("SELECT pnl_usd, spot_usd_per_local FROM realised_pnl WHERE trade_id = 'f1'").fetchone() == (
        pytest.approx(2 * 50 * 60.0), pytest.approx(50 * 6460.0))


def test_a_matured_swap_is_frozen_again_when_its_maturity_pv_is_re_run():
    conn = _irs_db()
    _insert_marks(conn, [
        ("2026-09-10", "IRSOIS-USD-1", "2026-09-10", "PV_USD", 0.0, "QL_PRICER", "t"),
        ("2026-09-10", "IRSOIS-USD-1", "2026-09-10", "CASHFLOW_USD", 2_100.0, "QL_PRICER", "t"),
    ])
    ledger.realise_settled(conn, "2026-09-14")
    conn.execute("UPDATE marks SET value = 2_150.0 WHERE mark_type = 'CASHFLOW_USD'")   # engine/rates re-ran the day
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == ["s1"]
    assert conn.execute("SELECT pnl_usd, local_amount FROM realised_pnl WHERE trade_id = 's1'").fetchone() == (2_150.0, 2_150.0)
