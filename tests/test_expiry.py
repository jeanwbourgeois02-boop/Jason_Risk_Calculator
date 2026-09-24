"""The roll calendar (engine/expiry, expiry-monitor lane, 2026-09-24): next event, business
days on the contract's own exchange calendar, alert levels."""
from __future__ import annotations

import dataclasses
import itertools

import pytest

from data.contracts import store_static_dates
from data.ingest import schema
from engine.expiry import AMBER_MAX_BUSINESS_DAYS, RED_MAX_BUSINESS_DAYS, expiry_schedule, levels, schedule

_ids = itertools.count(1)


@pytest.fixture
def conn():
    c = schema.connect(":memory:")
    yield c
    c.close()


def _instrument(conn, contract_id, base_ccy, asset_class="FUTURE", expiry="2026-12-31"):
    conn.execute(
        "INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
        "is_ndf, bbg_ticker, expiry_date) VALUES (?, ?, ?, 'USD', 1000, 0, '', ?)",
        (contract_id, asset_class, base_ccy, expiry))


def _trade(conn, contract_id, root_id, lots, trade_date="2026-09-01", product="FUTURE",
           asset_class="FUTURE", expiry="2026-12-31"):
    _instrument(conn, contract_id, root_id, asset_class=asset_class, expiry=expiry)
    tid = f"T{next(_ids)}"
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) "
        "VALUES (?, 'XLSX', ?, ?, ?, ?, ?, 100, 'ACC', 'CP', '', 'JB', '')",
        (tid, contract_id, product, tid, trade_date, lots))
    conn.commit()
    return tid


def _freeze(conn, trade_id, contract_id, settle_date, frozen_at="2026-09-01T17:00:00-04:00"):
    """A ledger row as engine/pnl/ledger.realise_settled writes one for a future."""
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, "
        "note) VALUES (?, ?, 'FUTURE', 'USD', ?, 0, 0, 'FUTURE_PX', 1, ?, 'BBG_BDH', 0, ?, '')",
        (trade_id, contract_id, settle_date, settle_date, frozen_at))
    conn.commit()


def _dates(conn, contract_id, last_trade, first_notice=""):
    store_static_dates(conn, [{"contract_id": contract_id, "last_trade_date": last_trade,
                               "first_notice_date": first_notice, "source": "BBG_BDP"}])


def _only(result):
    assert len(result["rows"]) == 1, result
    return result["rows"][0]


def test_physical_contract_two_business_days_from_first_notice_is_red(conn):
    _trade(conn, "HGZ26 Comdty", "COMEX:HG", 4)
    _dates(conn, "HGZ26 Comdty", "2026-12-29", "2026-11-20")
    row = _only(expiry_schedule(conn, "2026-11-18"))
    assert (row["next_event"], row["next_event_date"], row["business_days"]) == \
        ("first notice", "2026-11-20", 2)
    assert row["level"] == "RED"
    assert (row["dates_source"], row["estimated"], row["delivery"]) == ("BLOOMBERG", False, "physical")
    assert (row["root_id"], row["calendar"], row["lots"]) == ("COMEX:HG", "US", 4.0)
    assert row["beyond_calendar_coverage"] is False


def test_business_days_are_counted_on_the_contracts_own_calendar_across_a_holiday(conn):
    # 2026-11-26 is Thanksgiving on the US calendar and a business day on ICE Europe's.
    _trade(conn, "HGZ26 Comdty", "COMEX:HG", 4)
    _dates(conn, "HGZ26 Comdty", "2026-12-29", "2026-11-30")
    _trade(conn, "COF27 Comdty", "ICE:B", -3)
    _dates(conn, "COF27 Comdty", "2026-11-30")
    rows = {r["contract_id"]: r for r in expiry_schedule(conn, "2026-11-24")["rows"]}
    hg, brent = rows["HGZ26 Comdty"], rows["COF27 Comdty"]
    assert (hg["business_days"], hg["level"]) == (3, "RED")      # Wed, (Thu closed), Fri, Mon
    assert (brent["business_days"], brent["level"]) == (4, "AMBER")  # Wed, Thu, Fri, Mon


def test_cash_settled_contract_uses_last_trade_even_with_a_first_notice_on_file(conn):
    _trade(conn, "COF27 Comdty", "ICE:B", 2)
    _dates(conn, "COF27 Comdty", "2026-11-30", "2026-11-02")
    row = _only(expiry_schedule(conn, "2026-10-01"))
    assert (row["delivery"], row["delivery_assumed"]) == ("cash", "cash")
    assert (row["next_event"], row["next_event_date"]) == ("last trade", "2026-11-30")
    assert row["level"] == "GREEN"


def test_an_estimated_date_is_flagged_and_says_the_real_date_may_be_earlier(conn):
    _trade(conn, "CLZ26 Comdty", "NYMEX:CL", 1)
    row = _only(expiry_schedule(conn, "2026-12-21"))
    assert (row["dates_source"], row["estimated"]) == ("ESTIMATED", True)
    assert (row["next_event"], row["last_trade_date"], row["first_notice_date"]) == \
        ("last trade", "2026-12-31", None)
    assert "estimated" in row["reason"] and "not Bloomberg's" in row["reason"]
    assert "may be earlier" in row["reason"]
    # Past the conservative alert date (first business day of Nov 2026), not past the estimate.
    assert (row["alert_date"], row["level"]) == ("2026-11-02", "RED")
    assert "may already have passed" in row["reason"]


def test_an_estimated_physical_contract_is_alerted_from_the_month_before(conn):
    _trade(conn, "CLX26 Comdty", "NYMEX:CL", 1)
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert (row["last_trade_date"], row["dates_source"]) == ("2026-11-30", "ESTIMATED")
    assert (row["alert_date"], row["alert_basis"]) == ("2026-10-01", "estimated: first business day of Oct 2026")
    assert (row["business_days"], row["level"]) == (5, "AMBER")    # not GREEN at 46 days
    assert "held early" in row["reason"] and "not on file" in row["reason"]


def test_a_january_contract_is_alerted_from_december_of_the_year_before(conn):
    _trade(conn, "CLF27 Comdty", "NYMEX:CL", 1)
    row = _only(expiry_schedule(conn, "2026-11-20"))
    assert row["alert_date"] == "2026-12-01" and row["alert_basis"].endswith("Dec 2026")


def test_bloomberg_dates_are_not_held_early(conn):
    _trade(conn, "CLX26 Comdty", "NYMEX:CL", 1)
    _dates(conn, "CLX26 Comdty", "2026-10-20", "2026-10-21")
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert (row["alert_date"], row["alert_basis"]) == ("2026-10-20", "last trade")
    assert (row["business_days"], row["level"]) == (18, "GREEN")
    assert "held early" not in row["reason"]


def test_an_estimated_cash_settled_contract_is_not_held_early(conn):
    _trade(conn, "COF27 Comdty", "ICE:B", 1)
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert row["estimated"] is True
    assert (row["alert_date"], row["alert_basis"]) == (row["last_trade_date"], "last trade")


def test_a_contract_still_held_past_its_last_trade_date_is_expired(conn):
    _trade(conn, "CLQ26 Comdty", "NYMEX:CL", -2, trade_date="2026-06-01")
    _dates(conn, "CLQ26 Comdty", "2026-07-21", "2026-07-22")
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert row["level"] == "EXPIRED"
    assert row["next_event"] == "last trade" and row["business_days"] < 0
    assert "still held" in row["reason"]
    res = expiry_schedule(conn, "2026-09-24")
    assert res["counts"] == {"EXPIRED": 1, "RED": 0, "AMBER": 0, "GREEN": 0}
    assert res["settled_expired"] == []   # not frozen by the ledger: a real gap, still alerted


def test_a_contract_the_ledger_has_frozen_leaves_the_schedule_and_is_listed_once(conn):
    # The golden book's CLQ26: expired 2026-08-31 with no closing trade, frozen SETTLED.
    t1 = _trade(conn, "CLQ26 Comdty", "NYMEX:CL", 1, trade_date="2026-06-01")
    t2 = _trade(conn, "CLQ26 Comdty", "NYMEX:CL", 1, trade_date="2026-06-02")
    _dates(conn, "CLQ26 Comdty", "2026-08-31")
    _freeze(conn, t1, "CLQ26 Comdty", "2026-08-31", "2026-09-01T17:00:00-04:00")
    _freeze(conn, t2, "CLQ26 Comdty", "2026-08-31", "2026-09-02T17:00:00-04:00")
    res = expiry_schedule(conn, "2026-09-24")
    assert res["rows"] == [] and res["note"]
    assert res["counts"] == {"EXPIRED": 0, "RED": 0, "AMBER": 0, "GREEN": 0}
    [entry] = res["settled_expired"]
    assert (entry["contract_id"], entry["lots"], entry["last_trade_date"], entry["frozen_at"]) == \
        ("CLQ26 Comdty", 2.0, "2026-08-31", "2026-09-02T17:00:00-04:00")
    assert (entry["product"], entry["root_id"], entry["dates_source"], entry["estimated"]) == \
        ("FUTURE", "NYMEX:CL", "BLOOMBERG", False)
    assert "frozen all 2 trades" in entry["reason"] and "no alert" in entry["reason"]
    assert "level" not in entry


def test_a_contract_only_partly_frozen_stays_expired(conn):
    t1 = _trade(conn, "CLQ26 Comdty", "NYMEX:CL", 1, trade_date="2026-06-01")
    _trade(conn, "CLQ26 Comdty", "NYMEX:CL", 1, trade_date="2026-06-02")
    _dates(conn, "CLQ26 Comdty", "2026-08-31")
    _freeze(conn, t1, "CLQ26 Comdty", "2026-08-31")
    res = expiry_schedule(conn, "2026-09-24")
    assert _only(res)["level"] == "EXPIRED" and res["settled_expired"] == []


def test_a_freeze_dated_after_the_day_viewed_does_not_hide_the_contract(conn):
    # Viewing a past day before the settlement: the ledger's later freeze is not yet in force.
    t1 = _trade(conn, "CLQ26 Comdty", "NYMEX:CL", 1, trade_date="2026-06-01")
    _dates(conn, "CLQ26 Comdty", "2026-08-31")
    _freeze(conn, t1, "CLQ26 Comdty", "2026-08-31")
    res = expiry_schedule(conn, "2026-08-27")
    assert _only(res)["level"] == "RED" and res["settled_expired"] == []
    assert _only(expiry_schedule(conn, "2026-08-31"))["business_days"] == 0
    assert [e["contract_id"] for e in expiry_schedule(conn, "2026-09-01")["settled_expired"]] == ["CLQ26 Comdty"]


def test_a_frozen_contract_master_cannot_place_takes_the_ledger_settle_date(conn):
    t1 = _trade(conn, "ZZQ26 Comdty", "NYMEX:ZZNOPE", 1, trade_date="2026-06-01")
    _freeze(conn, t1, "ZZQ26 Comdty", "2026-08-28")
    res = expiry_schedule(conn, "2026-09-24")
    assert res["rows"] == []
    assert res["settled_expired"][0]["last_trade_date"] == "2026-08-28"


def test_rows_carry_their_product_and_a_product_joins_through_one_builder(conn, monkeypatch):
    _trade(conn, "CLZ26 Comdty", "NYMEX:CL", 1)
    _trade(conn, "WIDGET-1", "TEST:W", 25, product="TEST_PRODUCT", asset_class="TEST")
    assert [r["product"] for r in expiry_schedule(conn, "2026-09-24")["rows"]] == ["FUTURE"]

    seen = []

    def test_row(c, pos, as_of):
        seen.append((pos.product, pos.instrument_id, pos.lots))
        return {**schedule._unresolved_row(pos, pos.base_ccy, "test builder"), "level": "GREEN",
                "business_days": 60, "next_event_date": "2026-12-16"}

    monkeypatch.setitem(schedule._BUILDERS, "TEST_PRODUCT", test_row)
    rows = expiry_schedule(conn, "2026-09-24")["rows"]
    assert seen == [("TEST_PRODUCT", "WIDGET-1", 25.0)]
    assert {r["product"] for r in rows} == {"FUTURE", "TEST_PRODUCT"}


# --- options on futures (CMDTY_OPTION, Phase 5) -------------------------------------------

def _option(conn, option_id, root_id, lots, trade_date="2026-09-01", expiry="2026-12-31"):
    """An option trade; ``expiry`` is its instruments.expiry_date, which contract-master's
    option_for reads as the broker's own date (SYMBOL) when earlier than the estimate."""
    return _trade(conn, option_id, root_id, lots, trade_date=trade_date, product="CMDTY_OPTION",
                  asset_class="CMDTY_OPTION", expiry=expiry)


def test_an_estimated_option_expiry_is_held_early_and_says_it_is_estimated(conn):
    _option(conn, "CLZ26C 70 Comdty", "NYMEX:CL", 3)
    row = _only(expiry_schedule(conn, "2026-10-20"))
    assert (row["product"], row["contract_id"], row["lots"]) == ("CMDTY_OPTION", "CLZ26C 70 Comdty", 3.0)
    assert (row["next_event"], row["dates_source"], row["estimated"]) == ("option expiry", "ESTIMATED", True)
    # the estimate is the underlying's own estimated last trade date, never earlier than the real expiry
    assert row["last_trade_date"] == row["next_event_date"] == "2026-12-31"
    # held early like a future: first business day of the month before the option's contract month
    assert (row["alert_date"], row["alert_basis"]) == ("2026-11-02", "estimated: first business day of Nov 2026")
    assert (row["business_days"], row["level"]) == (9, "AMBER")   # Oct 21-23, 26-30, Nov 2 on US
    assert "not Bloomberg's" in row["reason"] and "held early" in row["reason"]
    assert (row["option_type"], row["strike"], row["underlying_id"]) == ("CALL", 70.0, "CLZ26 Comdty")


def test_an_option_with_bloombergs_date_counts_to_its_own_expiry(conn):
    _option(conn, "CLZ26C 70 Comdty", "NYMEX:CL", -2)
    _dates(conn, "CLZ26C 70 Comdty", "2026-11-17")
    row = _only(expiry_schedule(conn, "2026-11-12"))
    assert (row["dates_source"], row["estimated"]) == ("BLOOMBERG", False)
    assert (row["next_event_date"], row["alert_date"], row["alert_basis"]) == \
        ("2026-11-17", "2026-11-17", "option expiry")
    assert (row["business_days"], row["level"]) == (3, "RED")
    assert "held early" not in row["reason"]
    assert _only(expiry_schedule(conn, "2026-11-18"))["level"] == "EXPIRED"


def test_a_physical_underlying_is_named_in_the_option_reason(conn):
    _option(conn, "CLZ26P 65 Comdty", "NYMEX:CL", 1)
    _dates(conn, "CLZ26P 65 Comdty", "2026-11-17")
    _dates(conn, "CLZ26 Comdty", "2026-11-19", "2026-11-20")
    row = _only(expiry_schedule(conn, "2026-10-01"))
    assert (row["delivery_assumed"], row["underlying_event"], row["underlying_event_date"]) == \
        ("physical", "last trade", "2026-11-19")
    assert "becomes the future CLZ26 Comdty, physically delivered" in row["reason"]
    assert "last trade is 2026-11-19" in row["reason"]
    assert row["underlying_estimated"] is False


def test_a_cash_settled_underlying_is_not_called_physical_and_a_two_month_lead_is_held_earlier(conn):
    from data.contracts import option_contract
    estimate = option_contract("ICE:B", 1, 2027, "C", 80)
    # booked at the estimate itself (no earlier date on file), so the estimated path is exercised
    _option(conn, estimate.contract_id, "ICE:B", 1, expiry=estimate.last_trade_date.isoformat())
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert (row["dates_source"], row["estimated"]) == ("ESTIMATED", True)
    assert row["delivery_assumed"] == "cash" and "physically delivered" not in row["reason"]
    # ICE Brent options expire two months ahead (option_lead_months 2): alerted from Nov 2026
    assert row["alert_basis"] == "estimated: first business day of Nov 2026"


def test_an_option_booked_with_an_earlier_expiry_alerts_on_that_date_not_estimated(conn):
    from data.contracts import option_contract
    estimate = option_contract("ICE:B", 1, 2027, "C", 80)
    assert estimate.last_trade_date.isoformat() > "2026-11-25"
    _option(conn, estimate.contract_id, "ICE:B", 1, expiry="2026-11-25")
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert (row["dates_source"], row["estimated"]) == ("SYMBOL", False)
    assert (row["last_trade_date"], row["next_event_date"], row["alert_date"], row["alert_basis"]) == \
        ("2026-11-25", "2026-11-25", "2026-11-25", "option expiry")
    assert row["next_event"] == "option expiry"
    assert "not Bloomberg's" not in row["reason"] and "held early" not in row["reason"]


def test_an_option_contract_master_cannot_read_is_listed_red(conn):
    _option(conn, "NOT AN OPTION", "NYMEX:CL", 1)
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert (row["product"], row["level"], row["business_days"]) == ("CMDTY_OPTION", "RED", None)


def test_an_option_the_ledger_has_frozen_leaves_the_schedule(conn):
    t = _option(conn, "CLZ26C 70 Comdty", "NYMEX:CL", 1)
    _dates(conn, "CLZ26C 70 Comdty", "2026-11-17")
    _freeze(conn, t, "CLZ26C 70 Comdty", "2026-11-17")
    res = expiry_schedule(conn, "2026-11-20")
    assert res["rows"] == []
    [entry] = res["settled_expired"]
    assert (entry["product"], entry["contract_id"], entry["last_trade_date"]) == \
        ("CMDTY_OPTION", "CLZ26C 70 Comdty", "2026-11-17")


# --- LME forwards (LME_FWD, Phase 5) ------------------------------------------------------

def _lme(conn, root_id, tonnes, prompt, trade_date="2026-08-03"):
    tid = _trade(conn, root_id, root_id, tonnes, trade_date=trade_date, product="LME_FWD",
                 asset_class="LME_FWD")
    for n, (ccy, amount) in enumerate(((root_id, tonnes), ("USD", -tonnes * 9000.0)), start=1):
        conn.execute("INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, "
                     "rate, settles_cash) VALUES (?, ?, 'FX_NEAR', ?, ?, ?, ?, 9000, 1)",
                     (tid, n, ccy, amount, trade_date, prompt))
    conn.commit()
    return tid


def test_an_lme_prompt_is_alerted_from_its_cash_date_across_a_uk_holiday(conn):
    from engine import calendars, lme
    # 2026-08-31 is the summer bank holiday in London (a New York business day): the 2026-09-02
    # prompt becomes the cash date on Friday 2026-08-28, not Monday 2026-08-31.
    _lme(conn, "LME:CA", 50, "2026-09-02")
    row = _only(expiry_schedule(conn, "2026-08-26"))
    assert (row["product"], row["contract_id"], row["instrument_id"]) == \
        ("LME_FWD", "LME:CA 2026-09-02", "LME:CA")
    assert (row["next_event"], row["next_event_date"], row["prompt_date"]) == \
        ("LME prompt", "2026-09-02", "2026-09-02")
    assert row["alert_date"] == "2026-08-28" and lme.cash_date("2026-08-28").isoformat() == "2026-09-02"
    assert calendars.business_days_between("US", "2026-08-28", "2026-09-02") == 3   # the US would say Monday
    assert (row["calendar"], row["business_days"], row["level"]) == ("LME", 2, "RED")
    assert (row["tonnes"], row["lots"]) == (50.0, 2.0)
    assert (row["dates_source"], row["estimated"]) == ("TICKET", False)
    assert "cash date" in row["reason"] and "2026-08-28" in row["reason"]
    # on the cash date and between it and the prompt: RED; after the prompt: EXPIRED
    assert _only(expiry_schedule(conn, "2026-08-28"))["level"] == "RED"
    assert _only(expiry_schedule(conn, "2026-09-01"))["level"] == "RED"
    assert _only(expiry_schedule(conn, "2026-09-03"))["level"] == "EXPIRED"


def test_lme_positions_are_one_row_per_prompt_and_a_flat_prompt_is_not_a_row(conn):
    _lme(conn, "LME:CA", 50, "2026-12-16")
    _lme(conn, "LME:CA", -25, "2026-12-16")
    _lme(conn, "LME:CA", 75, "2027-01-20")
    _lme(conn, "LME:CA", -75, "2027-01-20")     # flat prompt
    _lme(conn, "LME:NI", -12, "2026-12-16")     # nickel: 6 t lots
    rows = {r["contract_id"]: r for r in expiry_schedule(conn, "2026-09-24")["rows"]}
    assert set(rows) == {"LME:CA 2026-12-16", "LME:NI 2026-12-16"}
    assert (rows["LME:CA 2026-12-16"]["tonnes"], rows["LME:CA 2026-12-16"]["lots"]) == (25.0, 1.0)
    assert (rows["LME:NI 2026-12-16"]["tonnes"], rows["LME:NI 2026-12-16"]["lots"]) == (-12.0, -2.0)
    assert all(r["level"] == "GREEN" for r in rows.values())


def test_a_frozen_lme_prompt_moves_to_settled_expired(conn):
    t1 = _lme(conn, "LME:CA", 50, "2026-09-02")
    t2 = _lme(conn, "LME:CA", 25, "2026-12-16")
    _freeze(conn, t1, "LME:CA", "2026-09-02")
    res = expiry_schedule(conn, "2026-09-24")
    assert [r["contract_id"] for r in res["rows"]] == ["LME:CA 2026-12-16"]
    [entry] = res["settled_expired"]
    assert (entry["product"], entry["contract_id"], entry["tonnes"], entry["lots"], entry["prompt_date"]) == \
        ("LME_FWD", "LME:CA 2026-09-02", 50.0, 2.0, "2026-09-02")
    assert "no alert" in entry["reason"]
    # a prompt past its date and not frozen stays EXPIRED: a real gap
    _freeze(conn, t2, "LME:CA", "2026-12-16")
    _lme(conn, "LME:ZS", 25, "2026-09-16")
    rows = expiry_schedule(conn, "2026-09-24")["rows"]
    assert [(r["contract_id"], r["level"]) for r in rows] == [("LME:ZS 2026-09-16", "EXPIRED"),
                                                              ("LME:CA 2026-12-16", "GREEN")]


def test_an_lme_forward_with_no_prompt_or_unknown_metal_is_listed_red(conn):
    _trade(conn, "LME:CA", "LME:CA", 25, product="LME_FWD", asset_class="LME_FWD")  # no legs
    _lme(conn, "LME:XX", 10, "2026-12-16")
    rows = expiry_schedule(conn, "2026-09-24")["rows"]
    assert len(rows) == 2 and all(r["level"] == "RED" and r["business_days"] is None for r in rows)
    assert all("cannot be placed" in r["reason"] for r in rows)


def test_futures_rows_carry_no_phase_5_keys(conn):
    _trade(conn, "CLZ26 Comdty", "NYMEX:CL", 1)
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert set(row) == {
        "product", "root_id", "name", "sector", "exchange", "calendar", "delivery", "delivery_assumed",
        "contract_id", "lots", "last_trade_date", "first_notice_date", "dates_source", "estimated",
        "next_event", "next_event_date", "alert_date", "alert_basis", "business_days", "level", "reason",
        "beyond_calendar_coverage"}


def test_a_first_notice_after_the_last_trade_date_leaves_last_trade_as_the_event(conn):
    _trade(conn, "CLZ26 Comdty", "NYMEX:CL", 5)
    _dates(conn, "CLZ26 Comdty", "2026-11-19", "2026-11-20")
    row = _only(expiry_schedule(conn, "2026-11-02"))
    assert (row["next_event"], row["next_event_date"], row["business_days"]) == ("last trade", "2026-11-19", 13)
    assert row["level"] == "GREEN"


def test_unknown_delivery_is_treated_as_physical_and_says_so(conn, monkeypatch):
    # Pin the root's delivery blank: contract-master fills the column, so the file is not relied on.
    real = schedule.contract_for

    def blank_delivery(root_id, contract_id, conn=None):
        c = real(root_id, contract_id, conn)
        return dataclasses.replace(c, root=dataclasses.replace(c.root, delivery=""))

    monkeypatch.setattr(schedule, "contract_for", blank_delivery)
    _trade(conn, "GCZ26 Comdty", "COMEX:GC", 1)
    _dates(conn, "GCZ26 Comdty", "2026-12-29", "2026-11-30")
    row = _only(expiry_schedule(conn, "2026-11-20"))
    assert (row["delivery"], row["delivery_assumed"]) == ("", "physical")
    assert row["next_event"] == "first notice"
    assert "treated as physically delivered" in row["reason"]


def test_a_flat_contract_is_not_listed_and_later_trades_do_not_count(conn):
    _trade(conn, "CLZ26 Comdty", "NYMEX:CL", 5)
    _trade(conn, "CLZ26 Comdty", "NYMEX:CL", -5)
    _trade(conn, "CLF27 Comdty", "NYMEX:CL", 3, trade_date="2026-10-01")
    res = expiry_schedule(conn, "2026-09-24")
    assert res["rows"] == [] and res["note"]
    assert [r["contract_id"] for r in expiry_schedule(conn, "2026-10-01")["rows"]] == ["CLF27 Comdty"]


def test_a_count_reaching_past_calendar_coverage_is_flagged(conn):
    _trade(conn, "CLZ29 Comdty", "NYMEX:CL", 1)  # US coverage ends 2028-12-31
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert row["beyond_calendar_coverage"] is True
    assert "coverage" in row["reason"]


def test_equity_index_futures_are_ignored(conn):
    _trade(conn, "ESZ6 Index", "ES", 2)
    res = expiry_schedule(conn, "2026-09-24")
    assert res["rows"] == [] and "No commodity futures position" in res["note"]
    assert res["counts"] == {"EXPIRED": 0, "RED": 0, "AMBER": 0, "GREEN": 0}


def test_a_held_contract_the_master_cannot_place_is_listed_red(conn):
    _trade(conn, "ZZQ26 Comdty", "NYMEX:ZZNOPE", 1)
    row = _only(expiry_schedule(conn, "2026-09-24"))
    assert row["level"] == "RED" and row["business_days"] is None
    assert "dates are unknown" in row["reason"]


def test_rows_are_worst_first_and_thresholds_are_reported(conn):
    _trade(conn, "CLQ26 Comdty", "NYMEX:CL", 1, trade_date="2026-06-01")
    _dates(conn, "CLQ26 Comdty", "2026-07-21")
    _trade(conn, "HGZ26 Comdty", "COMEX:HG", 1)
    _dates(conn, "HGZ26 Comdty", "2026-12-29", "2026-11-20")
    res = expiry_schedule(conn, "2026-11-18")
    assert [r["level"] for r in res["rows"]] == ["EXPIRED", "RED"]
    assert res["thresholds"] == {"RED": RED_MAX_BUSINESS_DAYS, "AMBER": AMBER_MAX_BUSINESS_DAYS}


def test_level_thresholds():
    assert levels.level_for(0, False) == "RED"
    assert levels.level_for(RED_MAX_BUSINESS_DAYS, False) == "RED"
    assert levels.level_for(RED_MAX_BUSINESS_DAYS + 1, False) == "AMBER"
    assert levels.level_for(AMBER_MAX_BUSINESS_DAYS, False) == "AMBER"
    assert levels.level_for(AMBER_MAX_BUSINESS_DAYS + 1, False) == "GREEN"
    assert levels.level_for(50, True) == "EXPIRED"
    assert levels.level_for(None, False) == "RED"
