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


# --------------------------------------------------------------------------- options (2026-09-17)
def _option_db():
    conn = schema.connect()
    _insert_instruments(conn, [
        ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
        ("EURUSD091026C", "FX_OPTION", "EUR", "USD", 1, 0, "", "2026-09-10"),
    ])
    _insert_trade(conn, "o1", "EURUSD091026C", "FX_OPTION", "2026-03-01", 1e6, 0.0050)
    _insert_legs(conn, [("o1", 1, "NOTIONAL", "EUR", 1e6, "2026-03-01", "2026-09-10", 0.0050, 0)])
    conn.commit()
    return conn


def test_realise_settled_freezes_expired_option_at_last_premium_and_spot():
    conn = _option_db()
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
    conn = _option_db()
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
    assert out["realised"] == 1 and [u["trade_id"] for u in out["unrealisable"]] == ["o2"]   # no strike for o2 yet
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


# --------------------------------------------------------------------------- re-freeze at the close (2026-09-22)
# User decision: a frozen row is what the official marks on file give for its date and mark
# type, or it is dropped and frozen again by the same rule in the same call (`purge_superseded`).
# A row whose inputs did not change is untouched and keeps its frozen_at.


def _a1(conn):
    return conn.execute("SELECT spot_usd_per_local, spot_as_of_date, pnl_usd, frozen_at FROM realised_pnl "
                        "WHERE trade_id = 'a1'").fetchone()


def _refrozen_ids(res):
    return [e["trade_id"] for e in res["refrozen"]]


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
    assert res["realised"] == 1 and res["repaired"] == [] and res["kept"] == []
    assert res["refrozen"] == [{"trade_id": "a1", "product": "FX_FWD", "mark_type": "SPOT", "spot_as_of_date": "2026-09-09",
                                "pnl_from": pytest.approx(30000), "pnl_to": pytest.approx(20000),
                                "why": "SPOT 2026-09-09 mark 0.62 -> 0.63 (the marks of that date changed)"}]
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
    # the mark it was frozen from is gone: nothing to compare against, the row keeps its figure,
    # and says so under 'kept' (reviewer m-2, 2026-09-22: silent before)
    conn.execute("DELETE FROM marks WHERE as_of_date = '2026-09-09'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == [] and _a1(conn) == before
    assert res["kept"] == [{"trade_id": "a1", "product": "FX_FWD", "reason": "no official SPOT for AUDUSD on or before 2026-09-10"}]
    # a past-day call (the backfill's) never looks at a row whose value date it is not past
    _insert_marks(conn, [("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.64, "BBG_BFXFORWARD", "t")])
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-10")
    assert res["refrozen"] == [] and res["kept"] == [] and _a1(conn) == before
    assert _refrozen_ids(ledger.realise_settled(conn, "2026-09-14")) == ["a1"]


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
    assert res["realised"] == 1
    assert res["refrozen"] == [{"trade_id": "f1", "product": "FUTURE", "mark_type": "FUTURE_PX", "spot_as_of_date": "2026-09-18",
                                "pnl_from": pytest.approx(2 * 50 * 50.0), "pnl_to": pytest.approx(2 * 50 * 60.0),
                                "why": "FUTURE_PX 2026-09-18 mark 322500 -> 323000 (the marks of that date changed)"}]
    assert conn.execute("SELECT pnl_usd, spot_usd_per_local FROM realised_pnl WHERE trade_id = 'f1'").fetchone() == (
        pytest.approx(2 * 50 * 60.0), pytest.approx(50 * 6460.0))


def test_kept_names_every_row_the_rule_cannot_recompute_and_refrozen_is_sorted_by_trade_id():
    """Reviewer m-2 / M-2, 2026-09-22: a row the purge could not recompute was skipped in silence,
    and 'refrozen' was bare ids. Two frozen AUD rows, both from the 09-09 spot: with the spot
    gone both are kept with the reason and nothing is dropped; with the spot back at another
    value both are re-frozen, listed by trade_id, each with its own from / to figure."""
    conn = _db()
    _insert_trade(conn, "a0", "AUDUSD", "FX_FWD", "2026-08-10", 2e6, 0.60)
    _insert_legs(conn, [("a0", 1, "FX_NEAR", "AUD", 2e6, "2026-08-10", "2026-09-10", 0.60, 1),
                        ("a0", 2, "FX_NEAR", "USD", -1200000, "2026-08-10", "2026-09-10", 0.60, 1)])
    conn.commit()
    assert ledger.realise_settled(conn, "2026-09-14")["realised"] == 2
    conn.execute("DELETE FROM marks WHERE as_of_date = '2026-09-09'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["refrozen"] == [] and res["realised"] == 0
    assert res["kept"] == [{"trade_id": "a0", "product": "FX_FWD", "reason": "no official SPOT for AUDUSD on or before 2026-09-10"},
                           {"trade_id": "a1", "product": "FX_FWD", "reason": "no official SPOT for AUDUSD on or before 2026-09-10"}]
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 2
    _insert_marks(conn, [("2026-09-09", "AUDUSD", "2026-09-09", "SPOT", 0.63, "BBG_BFXFORWARD", "t")])
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-14")
    assert res["kept"] == [] and res["realised"] == 2
    assert [(e["trade_id"], e["pnl_from"], e["pnl_to"]) for e in res["refrozen"]] == [
        ("a0", pytest.approx(2e6 * (0.62 - 0.60)), pytest.approx(2e6 * (0.63 - 0.60))),
        ("a1", pytest.approx(30000), pytest.approx(20000))]
    assert {e["why"] for e in res["refrozen"]} == {"SPOT 2026-09-09 mark 0.62 -> 0.63 (the marks of that date changed)"}


# --------------------------------------------------------------------------- non-USD futures (2026-09-24)
# User decision "Spot of valuation date": a settled future freezes at the last official FUTURE_PX on
# or before expiry, its quote-currency P&L converted to USD at spot of that same date; a CNY
# contract is never frozen as dollars, and a USD one freezes exactly as before.


_ES_FILL, _ES_MARK = 6_400.75, 6_450.37   # not exact in binary, so a changed operation order would show


def _cny_future_db(usdcny=7.10):
    """c1: long 2 SHFE copper (CNY, 5 t per contract) at 80,000, expiry 09-15, marked 80,500 that day;
    f1: long 2 ES (USD, 50) at 6,400.75, expiry 09-18, marked 6,450.37 that day. USDCNY on 09-15 unless None."""
    conn = schema.connect()
    _insert_instruments(conn, [
        ("CUV6 Comdty", "FUTURE", "CU", "CNY", 5, 0, "CUV6 Comdty", "2026-09-15"),
        ("ESU6 Index", "FUTURE", "ES", "USD", 50, 0, "ESU6 Index", "2026-09-18"),
        ("USDCNY", "FX", "USD", "CNY", 1, 0, "USDCNY Curncy", "9999-12-31"),
    ])
    _insert_trade(conn, "c1", "CUV6 Comdty", "FUTURE", "2026-08-10", 2, 80_000.0)
    _insert_trade(conn, "f1", "ESU6 Index", "FUTURE", "2026-08-10", 2, _ES_FILL)
    _insert_legs(conn, [
        ("c1", 1, "NOTIONAL", "CNY", 2 * 5 * 80_000.0, "2026-08-10", "2026-09-15", 80_000.0, 0),
        ("f1", 1, "NOTIONAL", "USD", 2 * 50 * _ES_FILL, "2026-08-10", "2026-09-18", _ES_FILL, 0),
    ])
    _insert_marks(conn, [
        ("2026-09-15", "CUV6 Comdty", "2026-09-15", "FUTURE_PX", 80_500.0, "BBG_BDH", "t"),
        ("2026-09-18", "ESU6 Index", "2026-09-18", "FUTURE_PX", _ES_MARK, "BBG_BDH", "t"),
    ])
    if usdcny is not None:
        _insert_marks(conn, [("2026-09-15", "USDCNY", "2026-09-15", "SPOT", usdcny, "BBG_BFXFORWARD", "t")])
    conn.commit()
    return conn


_REALISED_FIELDS = ("currency, local_amount, usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, "
                    "spot_source, pnl_usd, note")


def _realised(conn, trade_id):
    return conn.execute(f"SELECT {_REALISED_FIELDS} FROM realised_pnl WHERE trade_id = ?", (trade_id,)).fetchone()


def _es_row_as_before():
    """What the ledger froze a USD future at before 2026-09-24, bit for bit: the old
    `_future_freeze`'s own expressions (combined = multiplier x m, entry = qty x multiplier x fill)."""
    qty, multiplier = 2.0, 50.0
    combined, entry = multiplier * _ES_MARK, qty * multiplier * _ES_FILL
    return ("USD", qty, entry, "FUTURE_PX", combined, "2026-09-18", "BBG_BDH", qty * combined - entry, "")


def test_a_cny_future_freezes_at_its_cny_pnl_converted_at_the_spot_of_its_marks_date():
    conn = _cny_future_db()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["realised"] == 2 and res["unrealisable"] == [] and res["refrozen"] == []
    s = 1 / 7.10
    c1 = _realised(conn, "c1")
    assert c1[0] == "CNY" and c1[1] == 2.0 and c1[3] == "FUTURE_PX" and c1[5:7] == ("2026-09-15", "BBG_BDH")
    assert c1[2] == pytest.approx(2 * 5 * 80_000.0 * s)          # usd_entry_amount: the entry in USD at S
    assert c1[4] == pytest.approx(5 * 80_500.0 * s)              # spot_usd_per_local: multiplier x mark x S
    assert c1[7] == pytest.approx(2 * 5 * (80_500.0 - 80_000.0) / 7.10) == pytest.approx(704.2253521)
    assert c1[7] == pytest.approx(c1[1] * c1[4] - c1[2], rel=1e-12)
    assert c1[8] == "CNY P&L converted at USDCNY spot of 2026-09-15"
    # the USD contract is frozen exactly as before 2026-09-24
    assert _realised(conn, "f1") == _es_row_as_before()
    # and nothing moves on the next call
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["realised"] == 0 and res["refrozen"] == [] and res["kept"] == []


def test_a_cny_future_with_no_usdcny_on_file_stays_unfrozen_with_the_reason_never_frozen_as_dollars():
    conn = _cny_future_db(usdcny=None)
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["realised"] == 1
    assert res["unrealisable"] == [{"trade_id": "c1", "reason": "no SPOT for USD conversion of CNY on 2026-09-15"}]
    assert _realised(conn, "c1") is None
    assert _realised(conn, "f1") == _es_row_as_before()
    # once the conversion spot lands, the next call freezes it
    _insert_marks(conn, [("2026-09-15", "USDCNY", "2026-09-15", "SPOT", 7.10, "BBG_BFXFORWARD", "t")])
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["realised"] == 1 and res["unrealisable"] == []
    assert _realised(conn, "c1")[7] == pytest.approx(5_000.0 / 7.10)


def test_a_cny_future_is_frozen_again_when_the_conversion_spot_of_its_marks_date_changes():
    """The backfill replaces the live USDCNY press of 09-15 with that day's 15:00 close: the
    FUTURE_PX is unchanged, the conversion is not, so the row is re-frozen and reported."""
    conn = _cny_future_db(usdcny=7.10)
    ledger.realise_settled(conn, "2026-09-21")
    es_before = conn.execute("SELECT * FROM realised_pnl WHERE trade_id = 'f1'").fetchone()
    conn.execute("UPDATE marks SET value = 7.12, snapped_at = '2026-09-15T15:00:00-04:00' "
                 "WHERE instrument_id = 'USDCNY' AND as_of_date = '2026-09-15'")
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["realised"] == 1 and res["kept"] == []
    assert res["refrozen"] == [{
        "trade_id": "c1", "product": "FUTURE", "mark_type": "FUTURE_PX", "spot_as_of_date": "2026-09-15",
        "pnl_from": pytest.approx(5_000.0 / 7.10), "pnl_to": pytest.approx(5_000.0 / 7.12),
        "why": (f"FUTURE_PX 2026-09-15 mark {5 * 80_500.0 / 7.10:.10g} -> {5 * 80_500.0 / 7.12:.10g}, "
                f"entry {2 * 5 * 80_000.0 / 7.10:.10g} -> {2 * 5 * 80_000.0 / 7.12:.10g} (the marks of that date changed)")}]
    c1 = _realised(conn, "c1")
    assert c1[0] == "CNY" and c1[7] == pytest.approx(5_000.0 / 7.12)
    # the USD future is untouched, frozen_at included
    assert conn.execute("SELECT * FROM realised_pnl WHERE trade_id = 'f1'").fetchone() == es_before
    res = ledger.realise_settled(conn, "2026-09-21")
    assert res["refrozen"] == [] and res["realised"] == 0


# --------------------------------------------------------------------------- Phase 2 removal (2026-09-24)
# Rates swaps and NDFs left the app (user decision 2026-09-24). The frozen figure of every product
# that remains must not move: the book below was frozen by the ledger at HEAD before the removal
# (e660974) and each row is pinned bit for bit, frozen_at aside. An older database's swap row, or an
# NDF row frozen at its fix, is left exactly as it is and named under 'kept'.

_PIN_INSTRUMENTS = [
    ("USDCNH", "FX", "USD", "CNH", 1, 0, "USDCNH Curncy", "9999-12-31"),
    ("EURGBP", "FX", "EUR", "GBP", 1, 0, "EURGBP Curncy", "9999-12-31"),
    ("GBPUSD", "FX", "GBP", "USD", 1, 0, "GBPUSD Curncy", "9999-12-31"),
    ("XAUUSD", "FX", "XAU", "USD", 1, 0, "XAUUSD Curncy", "9999-12-31"),
    ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
    ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
    ("USDCNY", "FX", "USD", "CNY", 1, 0, "USDCNY Curncy", "9999-12-31"),
    ("CUV6 Comdty", "FUTURE", "CU", "CNY", 5, 0, "CUV6 Comdty", "2026-09-15"),
    ("CLQ6 Comdty", "FUTURE", "CL", "USD", 1000, 0, "CLQ6 Comdty", "2026-07-21"),
    ("EURUSD091026C-1", "FX_OPTION", "EUR", "USD", 1, 0, "", "2026-09-10"),
    ("EURUSD090926P-2", "FX_OPTION", "EUR", "USD", 1, 0, "", "2026-09-09"),
    ("EURUSD090926P-3", "FX_OPTION", "EUR", "USD", 1, 0, "", "2026-09-09"),
]
# trade_id, instrument, product, trade_date, quantity, fill, legs (leg_type, ccy, amount, settle, settles_cash)
_PIN_TRADES = [
    ("h1", "USDCNH", "FX_FWD", "2026-07-20", 1.5e6, 7.1834,
     [("FX_NEAR", "USD", 1.5e6, "2026-08-19", 1), ("FX_NEAR", "CNH", -1.5e6 * 7.1834, "2026-08-19", 1)]),
    ("x1", "EURGBP", "FX_FWD", "2026-08-03", -2.3e6, 0.84713,
     [("FX_NEAR", "EUR", -2.3e6, "2026-09-10", 1), ("FX_NEAR", "GBP", 2.3e6 * 0.84713, "2026-09-10", 1)]),
    ("g1", "XAUUSD", "FX_FWD", "2026-08-05", 250.0, 3341.27,
     [("FX_NEAR", "XAU", 250.0, "2026-09-11", 1), ("FX_NEAR", "USD", -250.0 * 3341.27, "2026-09-11", 1)]),
    ("j1", "USDJPY", "FX_SPOT", "2026-09-09", -1e6, 147.382,
     [("FX_NEAR", "USD", -1e6, "2026-09-11", 1), ("FX_NEAR", "JPY", 1e6 * 147.382, "2026-09-11", 1)]),
    ("c1", "CUV6 Comdty", "FUTURE", "2026-08-10", -3, 80123.4, [("NOTIONAL", "CNY", -3 * 5 * 80123.4, "2026-09-15", 0)]),
    ("q1", "CLQ6 Comdty", "FUTURE", "2026-06-11", 4, 67.83, [("NOTIONAL", "USD", 4 * 1000 * 67.83, "2026-07-21", 0)]),
    ("o1", "EURUSD091026C-1", "FX_OPTION", "2026-06-01", 1.7e6, 0.00613, [("NOTIONAL", "EUR", 1.7e6, "2026-09-10", 0)]),
    ("o2", "EURUSD090926P-2", "FX_OPTION", "2026-06-02", 2e6, 0.00421, [("NOTIONAL", "EUR", 2e6, "2026-09-09", 0)]),
    ("o3", "EURUSD090926P-3", "FX_OPTION", "2026-07-15", -2e6, 0.00537, [("NOTIONAL", "EUR", -2e6, "2026-09-09", 0)]),
]
_PIN_MARKS = [
    ("2026-08-19", "USDCNH", "2026-08-19", "SPOT", 7.1592),
    ("2026-09-09", "EURGBP", "2026-09-09", "SPOT", 0.85291),            # last before the 09-10 value date
    ("2026-09-09", "GBPUSD", "2026-09-09", "SPOT", 1.33427),
    ("2026-09-11", "XAUUSD", "2026-09-11", "SPOT", 3372.64),
    ("2026-09-11", "USDJPY", "2026-09-11", "SPOT", 146.917),
    ("2026-09-15", "CUV6 Comdty", "2026-09-15", "FUTURE_PX", 79876.5),
    ("2026-09-15", "USDCNY", "2026-09-15", "SPOT", 7.1043),
    ("2026-07-20", "CLQ6 Comdty", "2026-07-21", "FUTURE_PX", 66.91),    # last before expiry
    ("2026-09-10", "EURUSD091026C-1", "2026-09-10", "PREMIUM", 0.00887),
    ("2026-09-10", "EURUSD", "2026-09-10", "SPOT", 1.17364),
    ("2026-07-14", "EURUSD", "2026-07-14", "SPOT", 1.16482),            # last before the 07-15 close-out
]
_PIN_SOURCE = {"SPOT": "BBG_BFXFORWARD", "FUTURE_PX": "BBG_BDH", "PREMIUM": "QL_OPTIONS_PRICER"}

# Every realised_pnl column but frozen_at, exactly as the ledger at e660974 wrote them.
_PIN_ROWS = [
    ('c1', 'CUV6 Comdty', 'FUTURE', 'CNY', '2026-09-15', -3.0, -169172.33224948272, 'FUTURE_PX', 56217.00941683206,
     '2026-09-15', 'BBG_BDH', 521.3039989865501, 'CNY P&L converted at USDCNY spot of 2026-09-15'),
    ('g1', 'XAUUSD', 'FX_FWD', 'USD', '2026-09-11', 250.0, 835317.5, 'SPOT', 3372.64, '2026-09-11', 'BBG_BFXFORWARD',
     7842.5, ''),
    ('h1', 'USDCNH', 'FX_FWD', 'CNH', '2026-08-19', 1500000.0, 1505070.3989272546, 'SPOT', 1.0, '2026-08-19',
     'BBG_BFXFORWARD', -5070.398927254602, ''),
    ('j1', 'USDJPY', 'FX_SPOT', 'JPY', '2026-09-11', -1000000.0, -1003165.0523765119, 'SPOT', 1.0, '2026-09-11',
     'BBG_BFXFORWARD', 3165.052376511856, ''),
    ('o1', 'EURUSD091026C-1', 'FX_OPTION', 'EUR', '2026-09-10', 1700000.0, 12230.50244, 'PREMIUM', 0.010410186799999999,
     '2026-09-10', 'QL_OPTIONS_PRICER', 5466.815119999996, 'premium dated 2026-09-10'),
    ('o2', 'EURUSD090926P-2', 'FX_OPTION', 'EUR', '2026-09-09', 2000000.0, 9807.7844, 'CLOSE_OUT', 0.0062550834,
     '2026-07-14', 'BBG_BFXFORWARD', 2702.3823999999986,
     'closed out 2026-07-15 at the closing fill 0.00537; spot dated 2026-07-14 (last before the close-out)'),
    ('o3', 'EURUSD090926P-3', 'FX_OPTION', 'EUR', '2026-09-09', -2000000.0, -12510.166799999999, 'CLOSE_OUT',
     0.0062550834, '2026-07-14', 'BBG_BFXFORWARD', 0.0,
     'closed out 2026-07-15 at the closing fill 0.00537; spot dated 2026-07-14 (last before the close-out)'),
    ('q1', 'CLQ6 Comdty', 'FUTURE', 'USD', '2026-07-21', 4.0, 271320.0, 'FUTURE_PX', 66910.0, '2026-07-20', 'BBG_BDH',
     -3680.0, 'settlement price dated 2026-07-20 (last before expiry)'),
    ('x1', 'EURGBP', 'FX_FWD', 'GBP', '2026-09-10', -2300000.0, -2599690.33373, 'SPOT', 1.1380122257, '2026-09-09',
     'BBG_BFXFORWARD', -17737.78538000025, 'spot dated 2026-09-09 (last before settlement)'),
]
_PIN_LTD = -6790.1304117564505


def _pin_db():
    conn = schema.connect()
    _insert_instruments(conn, _PIN_INSTRUMENTS)
    for trade_id, inst, product, trade_date, qty, fill, legs in _PIN_TRADES:
        _insert_trade(conn, trade_id, inst, product, trade_date, qty, fill, strategy="")
        _insert_legs(conn, [(trade_id, n, leg_type, ccy, amount, trade_date, settle, fill, cash)
                            for n, (leg_type, ccy, amount, settle, cash) in enumerate(legs, 1)])
    for inst in ("EURUSD090926P-2", "EURUSD090926P-3"):
        conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) "
                     "VALUES (?, 1.15, 'PUT', 'VANILLA')", (inst,))
    _insert_marks(conn, [(d, i, s, mt, v, _PIN_SOURCE[mt], f"{d}T15:00:00-04:00") for d, i, s, mt, v in _PIN_MARKS])
    conn.commit()
    return conn


def _pin_rows(conn):
    cols = [c for c in ledger._REALISED_COLUMNS if c != "frozen_at"]
    return conn.execute(f"SELECT {', '.join(cols)} FROM realised_pnl ORDER BY trade_id").fetchall()


def test_every_remaining_product_freezes_bit_for_bit_as_before_the_removal():
    """A USDCNH forward, a cross (EURGBP, converted through GBPUSD), XAUUSD, a USDJPY spot, a CNY
    future, a USD future frozen at the last price before expiry, an FX option at its PREMIUM and a
    closed-out option pair at CLOSE_OUT: every stored figure is the one HEAD wrote, exactly."""
    conn = _pin_db()
    res = ledger.realise_settled(conn, "2026-09-21")
    assert (res["realised"], res["unrealisable"], res["refrozen"], res["kept"]) == (9, [], [], [])
    assert _pin_rows(conn) == _PIN_ROWS
    assert ledger.ltd(conn, "2026-09-21") == _PIN_LTD
    res = ledger.realise_settled(conn, "2026-09-21")
    assert (res["realised"], res["refrozen"], res["kept"]) == (0, [], [])
    assert _pin_rows(conn) == _PIN_ROWS


def test_an_older_databases_swap_and_ndf_fix_rows_are_left_as_they_are_and_named_under_kept():
    """Rates swaps and NDFs left the app on 2026-09-24: the ledger freezes neither, and a row an
    older database still holds for one is never recomputed (by the FX rule it would move) and never
    dropped; it is named under 'kept' until the next upload replaces the book."""
    conn = _pin_db()
    _insert_instruments(conn, [
        ("IRSOIS-USD-1", "IRS", "USD", "USD", 1, 0, "IRSOIS-USD-1", "2026-09-10"),
        ("IRSOIS-USD-2", "IRS", "USD", "USD", 1, 0, "IRSOIS-USD-2", "2026-09-10"),
        ("USDBRL", "FX", "USD", "BRL", 1, 1, "USDBRL Curncy", "9999-12-31"),
    ])
    for trade_id, inst in (("s1", "IRSOIS-USD-1"), ("s2", "IRSOIS-USD-2")):
        _insert_trade(conn, trade_id, inst, "IRS", "2026-03-01", 10e6, 0.04)
        _insert_legs(conn, [(trade_id, 1, "FIXED", "USD", -10e6, "2026-03-03", "2026-09-10", 0.04, 0),
                            (trade_id, 2, "FLOAT", "USD", 10e6, "2026-03-03", "2026-09-10", 0.0, 0)])
    _insert_trade(conn, "b1", "USDBRL", "FX_FWD", "2026-08-14", 1e6, 5.20)
    _insert_legs(conn, [("b1", 1, "FX_NEAR", "USD", 1e6, "2026-08-14", "2026-09-16", 5.20, 0),
                        ("b1", 2, "FX_NEAR", "BRL", -5.2e6, "2026-08-14", "2026-09-16", 5.20, 0)])
    _insert_marks(conn, [
        ("2026-09-16", "USDBRL", "2026-09-16", "SPOT", 5.35, "BBG_BFXFORWARD", "t"),
        ("2026-09-10", "IRSOIS-USD-2", "2026-09-10", "PV_USD", 0.0, "QL_PRICER", "t"),
        ("2026-09-10", "IRSOIS-USD-2", "2026-09-10", "CASHFLOW_USD", 2_100.0, "QL_PRICER", "t"),
    ])
    old_rows = [
        ("s1", "IRSOIS-USD-1", "IRS", "USD", "2026-09-10", 2_100.0, 0.0, "PV_USD", 1.0, "2026-09-10", "QL_PRICER",
         2_100.0, "2026-09-11T17:00:00-04:00", ""),
        ("b1", "USDBRL", "FX_FWD", "BRL", "2026-09-16", 1e6, 1e6 * 5.20 / 5.22, "NDF_FIX", 1 / 5.22, "2026-09-14",
         "BBG_BDH", 1e6 * (5.22 - 5.20) / 5.22, "2026-09-15T17:00:00-04:00",
         "official fixing dated 2026-09-14 (NDF fixing), converted at the fixing"),
    ]
    conn.executemany(f"INSERT INTO realised_pnl ({', '.join(ledger._REALISED_COLUMNS)}) VALUES ({','.join('?' * 14)})",
                     old_rows)
    conn.commit()
    res = ledger.realise_settled(conn, "2026-09-21")
    reason = ("{} row frozen at {}: rates swaps and NDFs left the app on 2026-09-24, "
              "so it is left as frozen until the next upload replaces the book")
    assert res["kept"] == [{"trade_id": "b1", "product": "FX_FWD", "reason": reason.format("FX_FWD", "NDF_FIX")},
                           {"trade_id": "s1", "product": "IRS", "reason": reason.format("IRS", "PV_USD")}]
    assert res["refrozen"] == [] and res["unrealisable"] == []
    # the matured swap s2 is not frozen at its PV + cashflows any more, and nothing else moved
    assert res["realised"] == 9
    stored = conn.execute(f"SELECT {', '.join(ledger._REALISED_COLUMNS)} FROM realised_pnl "
                          "WHERE trade_id IN ('s1', 'b1', 's2') ORDER BY trade_id DESC").fetchall()
    assert stored == old_rows
    assert [r for r in _pin_rows(conn) if r[0] not in ("s1", "b1")] == _PIN_ROWS
