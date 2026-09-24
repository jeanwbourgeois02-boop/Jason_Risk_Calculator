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


def _trade(conn, contract_id, root_id, lots, trade_date="2026-09-01"):
    _instrument(conn, contract_id, root_id)
    tid = f"T{next(_ids)}"
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) "
        "VALUES (?, 'XLSX', ?, 'FUTURE', ?, ?, ?, 100, 'ACC', 'CP', '', 'JB', '')",
        (tid, contract_id, tid, trade_date, lots))
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
    _trade(conn, "CLZ28 Comdty", "NYMEX:CL", 1)
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
