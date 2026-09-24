"""engine/lme: LME prompt dates, metals, curve tickers and the curve reader (lme-forwards
lane, commodity conversion plan Phase 5)."""
from __future__ import annotations

import datetime as dt

import pytest

import engine.calendars as cals
from data.ingest.schema import connect
from engine.lme import (
    cash_date,
    day_curve,
    forward_at,
    is_lme_instrument,
    is_valid_prompt,
    lme_curve_tickers,
    lme_root,
    lot_tonnes,
    monthly_prompt,
    prompt_structure,
    prompt_zone,
    settlement_price,
    third_wednesday,
    three_month_date,
)

D = dt.date


# ------------------------------------------------------------------ prompt calendar


def test_cash_date_is_t_plus_two_lme_business_days():
    assert cash_date("2026-09-24") == D(2026, 9, 28)          # Thu -> Mon, over the weekend
    assert cash_date(D(2026, 9, 21)) == D(2026, 9, 23)        # Mon -> Wed


def test_cash_date_across_a_uk_holiday():
    # Thu 27 Aug 2026: Fri 28, then Mon 31 Aug is the summer bank holiday -> Tue 1 Sep
    assert cash_date("2026-08-27") == D(2026, 9, 1)
    # Thu 2 Apr 2026: Good Friday and Easter Monday closed -> Tue 7, Wed 8 Apr
    assert cash_date("2026-04-02") == D(2026, 4, 8)
    # a US-only holiday is an LME business day (Labor Day, Mon 7 Sep 2026)
    assert cash_date("2026-09-03") == D(2026, 9, 7)


def test_three_month_date_plain_and_rolled_forward():
    assert three_month_date("2026-09-24") == D(2026, 12, 24)
    # 5 Sep 2026 is a Saturday -> next business day, Mon 7 Sep (same month)
    assert three_month_date("2026-06-05") == D(2026, 9, 7)


def test_three_month_date_rolls_back_at_month_end():
    # 29 Aug 2026 is a Saturday, Mon 31 Aug a bank holiday, Tue 1 Sep is the next month:
    # the previous business day, Fri 28 Aug
    assert three_month_date("2026-05-29") == D(2026, 8, 28)
    # 30 Nov + 3 months: no 30 Feb, so 28 Feb 2027 (a Sunday); 1 Mar is the next month
    # -> Fri 26 Feb 2027
    assert three_month_date("2026-11-30") == D(2027, 2, 26)


def test_third_wednesdays():
    assert third_wednesday(2026, 9) == D(2026, 9, 16)
    assert third_wednesday(2026, 10) == D(2026, 10, 21)       # the 1st is a Thursday
    assert third_wednesday(2026, 12) == D(2026, 12, 16)
    assert third_wednesday(2027, 4) == D(2027, 4, 21)
    assert third_wednesday(2027, 9) == D(2027, 9, 15)         # the 1st is a Wednesday
    for y in (2026, 2027, 2028):
        for m in range(1, 13):
            w = third_wednesday(y, m)
            assert w.weekday() == 2 and 15 <= w.day <= 21


def test_prompt_validity_in_each_zone():
    t = "2026-09-24"   # cash Mon 28 Sep, 3M Thu 24 Dec, 6M Wed 24 Mar 2027
    assert not is_valid_prompt("2026-09-25", t)                 # Tom: before cash
    assert prompt_zone("2026-09-25", t) == "BEFORE_CASH"
    # daily zone: any LME business day
    assert is_valid_prompt("2026-09-28", t)                     # cash itself
    assert is_valid_prompt("2026-10-15", t)
    assert is_valid_prompt("2026-12-24", t)                     # 3M itself
    assert not is_valid_prompt("2026-10-17", t)                 # Saturday
    assert not is_valid_prompt("2026-12-25", t)                 # Christmas
    assert prompt_zone("2026-10-15", t) == "DAILY"
    # weekly zone: Wednesdays only
    assert is_valid_prompt("2026-12-30", t)
    assert is_valid_prompt("2027-01-06", t)
    assert not is_valid_prompt("2027-01-07", t)                 # Thursday
    assert is_valid_prompt("2027-03-24", t)                     # a Wednesday on 6M
    assert prompt_zone("2027-01-06", t) == "WEEKLY"
    # monthly zone: third Wednesdays only
    assert is_valid_prompt("2027-04-21", t)
    assert not is_valid_prompt("2027-04-28", t)                 # a fourth Wednesday
    assert not is_valid_prompt("2027-04-22", t)
    assert is_valid_prompt("2028-12-20", t)
    assert prompt_zone("2027-04-21", t) == "MONTHLY"


def _fake_lme_calendar(tmp_path, monkeypatch, closures):
    (tmp_path / "LME.txt").write_text(
        "# coverage: 2026-01-01 to 2029-12-31\n" + "\n".join(closures) + "\n", encoding="utf-8")
    monkeypatch.setattr(cals, "CALENDAR_DIR", tmp_path)


def test_a_holiday_wednesday_rolls_to_the_next_business_day(tmp_path, monkeypatch):
    # a made-up LME closure on Wed 6 Jan 2027 (weekly zone) and on the third Wednesday of
    # April 2027 (monthly zone)
    _fake_lme_calendar(tmp_path, monkeypatch, ["2027-01-06", "2027-04-21"])
    t = "2026-09-24"
    assert not is_valid_prompt("2027-01-06", t)
    assert is_valid_prompt("2027-01-07", t)
    assert monthly_prompt(2027, 4) == D(2027, 4, 22)
    assert is_valid_prompt("2027-04-22", t)
    assert not is_valid_prompt("2027-04-21", t)


def test_the_pillar_list():
    pillars = prompt_structure("2026-09-24")
    assert [p.kind for p in pillars[:3]] == ["CASH", "MONTHLY", "MONTHLY"]
    assert pillars[0].date == D(2026, 9, 28)
    assert sum(p.kind == "3M" for p in pillars) == 1
    assert next(p for p in pillars if p.kind == "3M").date == D(2026, 12, 24)
    monthly = [p for p in pillars if p.kind == "MONTHLY"]
    assert len(monthly) == 27
    # September's third Wednesday (16 Sep) is before cash: the monthlies start in October
    assert monthly[0].date == D(2026, 10, 21) and monthly[0].label == "2026-10"
    assert monthly[-1].date == D(2028, 12, 20) and monthly[-1].label == "2028-12"
    dates = [p.date for p in pillars]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


def test_the_pillar_list_leaves_a_monthly_on_the_3m_date_to_the_3m_pillar():
    # dealt Wed 16 Sep 2026: 3M = Wed 16 Dec 2026, December's third Wednesday
    pillars = prompt_structure("2026-09-16")
    on_3m = [p for p in pillars if p.date == D(2026, 12, 16)]
    assert [p.kind for p in on_3m] == ["3M"]
    assert sum(p.kind == "MONTHLY" for p in pillars) == 26


# ------------------------------------------------------------------ metals and tickers


def test_lme_root_names_codes_and_ids():
    assert lme_root("copper") == "LME:CA"
    assert lme_root("Copper") == "LME:CA"
    assert lme_root("CA") == "LME:CA"
    assert lme_root("LME:CA") == "LME:CA"
    assert lme_root("aluminum") == "LME:AH"
    assert lme_root("Zinc") == "LME:ZS"
    assert lme_root("lead") == "LME:PB"
    assert lme_root("nickel") == "LME:NI"
    assert lme_root("tin") == "LME:SN"
    assert lme_root("NASAAC") == "LME:NA"
    assert lme_root("cobalt") == "LME:CO"


def test_lot_sizes_in_tonnes():
    assert lot_tonnes("LME:CA") == 25
    assert lot_tonnes("aluminium") == 25
    assert lot_tonnes("nickel") == 6
    assert lot_tonnes("tin") == 5


def test_unknown_metal_raises_with_the_known_ones_named():
    with pytest.raises(ValueError) as err:
        lme_root("unobtainium")
    msg = str(err.value)
    for rid in ("LME:CA", "LME:AH", "LME:ZS", "LME:PB", "LME:NI", "LME:SN"):
        assert rid in msg
    assert "copper" in msg
    # an LME ferrous contract is a monthly cash-settled future, not a prompt-date forward
    with pytest.raises(ValueError):
        lme_root("LME:SC")
    assert not is_lme_instrument("LME:SC")
    assert is_lme_instrument("LME:CA")
    assert not is_lme_instrument("XAUUSD")


def test_the_ticker_list_for_copper():
    rows = lme_curve_tickers("LME:CA", "2026-09-24")
    assert len(rows) == 29
    cash, first_monthly = rows[0], rows[1]
    assert cash == {"ticker": "LMCADY Comdty", "pillar_date": "2026-09-28", "kind": "CASH",
                    "mark_type": "SPOT", "settle_date": "2026-09-24"}
    assert first_monthly == {"ticker": "LPV6 Comdty", "pillar_date": "2026-10-21", "kind": "MONTHLY",
                             "mark_type": "FWD_OUTRIGHT", "settle_date": "2026-10-21"}
    three_m = next(r for r in rows if r["kind"] == "3M")
    assert three_m == {"ticker": "LMCADS03 Comdty", "pillar_date": "2026-12-24", "kind": "3M",
                       "mark_type": "FWD_OUTRIGHT", "settle_date": "2026-12-24"}
    assert rows[-1]["ticker"] == "LPZ8 Comdty" and rows[-1]["pillar_date"] == "2028-12-20"
    assert len({r["ticker"] for r in rows}) == len(rows)
    assert [r["pillar_date"] for r in rows] == sorted(r["pillar_date"] for r in rows)
    with pytest.raises(ValueError, match="LME:CA"):
        lme_curve_tickers("platinum", "2026-09-24")


# ------------------------------------------------------------------ curve reader


@pytest.fixture()
def conn():
    c = connect(":memory:")
    c.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
              "is_ndf, bbg_ticker, expiry_date) VALUES ('LME:CA', 'FUTURE', 'LME:CA', 'USD', 1, 0, "
              "'LMCADY Comdty', '9999-12-31')")
    yield c
    c.close()


def _mark(conn, as_of, settle, mark_type, value, source="BBG_BFXFORWARD"):
    conn.execute("INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                 "snapped_at) VALUES (?, 'LME:CA', ?, ?, ?, ?, ?)",
                 (as_of, settle, mark_type, value, source, f"{as_of}T15:00:00-04:00"))


def test_day_curve_and_forward_at(conn):
    day = "2026-09-24"
    _mark(conn, day, day, "SPOT", 10000.0)                       # cash, placed at 28 Sep
    _mark(conn, day, "2026-10-21", "FWD_OUTRIGHT", 10023.0)
    _mark(conn, day, "2026-12-24", "FWD_OUTRIGHT", 10087.0)
    curve = day_curve(conn, "copper", day)
    assert [d for d, _, _ in curve] == ["2026-09-28", "2026-10-21", "2026-12-24"]
    assert forward_at(conn, "LME:CA", "2026-10-21", day) == (10023.0, "BBG_BFXFORWARD")
    v, src = forward_at(conn, "LME:CA", "2026-10-09", day)       # 11 of 23 days on
    assert v == pytest.approx(10000.0 + 23.0 * 11 / 23)
    assert "between" in src
    v, src = forward_at(conn, "LME:CA", "2026-09-25", day)       # Tom: the cash price
    assert v == 10000.0 and "before the cash date" in src
    assert forward_at(conn, "LME:CA", "2027-01-20", day) is None  # beyond the curve: never extrapolated
    assert forward_at(conn, "LME:CA", "2026-10-21", "2026-09-23") is None  # no curve that day
    # a non-official source is never read
    _mark(conn, day, "2027-01-20", "FWD_OUTRIGHT", 10100.0, source="MANUAL")
    assert forward_at(conn, "LME:CA", "2027-01-20", day) is None


def test_settlement_price_is_the_last_cash_on_or_before_the_prompt(conn):
    _mark(conn, "2026-10-19", "2026-10-19", "SPOT", 9950.0)
    _mark(conn, "2026-10-20", "2026-10-20", "SPOT", 9960.0)
    _mark(conn, "2026-10-22", "2026-10-22", "SPOT", 9990.0)
    assert settlement_price(conn, "LME:CA", "2026-10-21") == (9960.0, "2026-10-20", "BBG_BFXFORWARD")
    assert settlement_price(conn, "LME:CA", "2026-10-22")[0] == 9990.0
    assert settlement_price(conn, "LME:CA", "2026-10-01") is None


def test_a_stored_value_that_is_not_a_number_is_a_data_error(conn):
    _mark(conn, "2026-09-24", "2026-09-24", "SPOT", "n/a")
    with pytest.raises(ValueError, match="not a number"):
        day_curve(conn, "LME:CA", "2026-09-24")
