"""data/bloomberg/live.py: Bloomberg's contract dates of the commodity futures (2026-09-24,
commodity conversion Phase 1). One press asks FUT_LAST_TRADE_DT / FUT_NOTICE_FIRST for the
library's CONTRACT_DATES futures before any futures price, stores them in contract_static,
applies them to the futures, and only then asks the prices, under Bloomberg's expiry. A
future with no Bloomberg ticker is never asked for. No blpapi needed: fake fetches."""
import sys
import types
from datetime import date

import pytest

from data.bloomberg import live
from data.bloomberg import pull_marks as pm, fwd_curve
from data.ingest import schema

TODAY = date(2026, 9, 24)
ESTIMATE = "2026-11-30"            # last weekday of the contract month: data/contracts' estimate
BBG_LAST_TRADE = "2026-11-19"      # Bloomberg's FUT_LAST_TRADE_DT for CLZ6
BBG_FIRST_NOTICE = "2026-11-20"


def _db(tmp_path, extra_futures=()):
    """A CL December future on its estimated expiry, plus any `extra_futures`
    (instrument_id, bbg_ticker, expiry) with one long trade each."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    futures = [("CLZ26 Comdty", "CLZ6 Comdty", ESTIMATE)] + list(extra_futures)
    for n, (iid, ticker, expiry) in enumerate(futures):
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                     (iid, "FUTURE", "NYMEX:" + iid[:2], "USD", 1000.0, 0, ticker, expiry))
        tid = f"f{n}"
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", iid, "FUTURE", tid, "2026-09-10", 2, 70.0, "acc", "cp", "", "t", "d", ""))
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (tid, 1, "NOTIONAL", "USD", 2 * 1000 * 70.0, "2026-09-10", expiry, 70.0, 0))
    conn.commit()
    return p, conn


@pytest.fixture
def fake_apply(monkeypatch):
    """ingest-booking's apply_contract_dates (written in parallel): moves each future with a
    stored last trade date onto it, instrument and legs, and says what it did."""
    calls = []

    def apply_contract_dates(conn):
        from data.contracts import static_dates
        calls.append(True)
        out = {"checked": 0, "updated": [], "missing_dates": []}
        for iid, expiry in conn.execute("SELECT instrument_id, expiry_date FROM instruments "
                                        "WHERE asset_class IN ('FUTURE', 'CMDTY_OPTION')").fetchall():
            out["checked"] += 1
            got = static_dates(conn, iid)
            if got is None:
                out["missing_dates"].append(iid)
                continue
            if got["last_trade_date"] != expiry:
                with conn:
                    conn.execute("UPDATE instruments SET expiry_date = ? WHERE instrument_id = ?",
                                 (got["last_trade_date"], iid))
                    conn.execute("UPDATE trade_legs SET settle_date = ? WHERE trade_id IN "
                                 "(SELECT trade_id FROM trades WHERE instrument_id = ?)", (got["last_trade_date"], iid))
                out["updated"].append({"instrument_id": iid, "from": expiry, "to": got["last_trade_date"]})
        return out

    module = types.ModuleType("data.ingest.contract_dates")
    module.apply_contract_dates = apply_contract_dates
    monkeypatch.setitem(sys.modules, "data.ingest.contract_dates", module)
    return calls


def _fake_bloomberg(monkeypatch, dates=None, raise_on_dates=None):
    """Every fetch_reference call logged as (purpose, tickers, fields); contract dates from
    `dates` ({ticker: {field: value}}), PX_LAST 71.5 for every future ticker."""
    log = []
    dates = {"CLZ6 Comdty": {"FUT_LAST_TRADE_DT": date(2026, 11, 19), "FUT_NOTICE_FIRST": date(2026, 11, 20)}} \
        if dates is None else dates

    def fetch_reference(session, service, tickers, fields, overrides=None, diag=None, tag=None):
        purpose = (tag or {}).get("purpose")
        log.append((purpose, list(tickers), list(fields)))
        if purpose == live.CONTRACT_DATES:
            if raise_on_dates is not None:
                raise raise_on_dates
            if diag is not None:      # what fetch_reference records: Bloomberg's own words per ticker
                rec = diag.new_request("ReferenceDataRequest", tickers, fields, None, tag)
                rec["raw_response"] = [{"security": t, "fieldData": dates.get(t, {}), "fieldExceptions": [],
                                        "securityError": None if t in dates else {"message": "Unknown/Invalid Security"}}
                                       for t in tickers]
            return {t: dict(dates.get(t, {})) for t in tickers}
        return {t: {"PX_LAST": 71.5} for t in tickers}

    monkeypatch.setattr(pm, "fetch_reference", fetch_reference)
    monkeypatch.setattr(pm, "fetch_historical", lambda *a, **k: {})
    monkeypatch.setattr(pm, "_get_blpapi", lambda: object())
    monkeypatch.setattr(fwd_curve, "request_fwd_curves", lambda *a, **k: {})
    return log


def _pull(p):
    return live.pull_once(p, session_factory=lambda: (object(), object()), today=TODAY)


def test_dates_are_asked_first_only_for_the_contract_dates_futures_and_the_price_lands_at_bloombergs_expiry(
        tmp_path, monkeypatch, fake_apply):
    p, conn = _db(tmp_path)
    log = _fake_bloomberg(monkeypatch)
    status = _pull(p)
    assert status["connected"] is True
    # The dates request is the first request of the press, for the CONTRACT_DATES future only,
    # and the futures price comes after it.
    purposes = [purpose for purpose, _t, _f in log]
    assert purposes[0] == live.CONTRACT_DATES and "FUTURE_PX_LIVE" in purposes
    assert purposes.index(live.CONTRACT_DATES) < purposes.index("FUTURE_PX_LIVE")
    assert log[0][1] == ["CLZ6 Comdty"] and log[0][2] == ["FUT_LAST_TRADE_DT", "FUT_NOTICE_FIRST"]
    assert sum(1 for purpose in purposes if purpose == live.CONTRACT_DATES) == 1
    # Stored under BBG_BDP, through contract-master, and applied.
    from data.contracts import static_dates
    got = static_dates(conn, "CLZ26 Comdty")
    assert got["last_trade_date"] == BBG_LAST_TRADE and got["first_notice_date"] == BBG_FIRST_NOTICE
    assert got["source"] == "BBG_BDP"
    assert fake_apply == [True]
    # The price was asked for and written at Bloomberg's expiry, never at the estimate.
    fut = [i for i in status["items"] if i["mark_type"] == "FUTURE_PX"]
    assert [(i["instrument_id"], i["settle_date"], i["status"]) for i in fut] == [("CLZ26 Comdty", BBG_LAST_TRADE, "OK")]
    marks = conn.execute("SELECT settle_date, value FROM marks WHERE mark_type = 'FUTURE_PX'").fetchall()
    assert marks == [(BBG_LAST_TRADE, 71.5)]
    # The status block.
    block = status["contract_dates"]
    assert block["requested"] == 1 and block["stored"] == 1 and block["failed"] == []
    assert block["applied"]["updated"] == [{"instrument_id": "CLZ26 Comdty", "from": ESTIMATE, "to": BBG_LAST_TRADE}]
    assert block["summary"] == "1 contract date stored, 1 future moved to Bloomberg's expiry"
    assert live.read_status(p)["contract_dates"] == block


def test_a_second_press_does_not_ask_again_once_the_dates_are_stored(tmp_path, monkeypatch, fake_apply):
    p, conn = _db(tmp_path)
    log = _fake_bloomberg(monkeypatch)
    _pull(p)
    log.clear()
    status = _pull(p)
    assert all(purpose != live.CONTRACT_DATES for purpose, _t, _f in log)
    assert status["contract_dates"]["requested"] == 0 and status["contract_dates"]["summary"] == ""
    # The apply still runs (dates on file, future open): a future stored but not moved is moved.
    assert fake_apply == [True, True] and status["contract_dates"]["applied"]["updated"] == []


def test_a_ticker_bloomberg_rejects_or_answers_without_a_date_is_listed_and_the_pull_goes_on(
        tmp_path, monkeypatch, fake_apply):
    p, conn = _db(tmp_path, extra_futures=[("NGZ26 Comdty", "NGZ6 Comdty", "2026-11-30"),
                                           ("HOZ26 Comdty", "HOZ6 Comdty", "2026-11-30")])
    dates = {"CLZ6 Comdty": {"FUT_LAST_TRADE_DT": date(2026, 11, 19)},        # no first notice: stored as ''
             "NGZ6 Comdty": {"FUT_NOTICE_FIRST": date(2026, 11, 25)}}         # no last trade date
    log = _fake_bloomberg(monkeypatch, dates=dates)                           # HOZ6: security error
    status = _pull(p)
    assert status["connected"] is True
    assert sorted(log[0][1]) == ["CLZ6 Comdty", "HOZ6 Comdty", "NGZ6 Comdty"]
    block = status["contract_dates"]
    assert block["requested"] == 3 and block["stored"] == 1
    failed = {f["ticker"]: f["reason"] for f in block["failed"]}
    assert failed == {"HOZ6 Comdty": "Unknown/Invalid Security",
                      "NGZ6 Comdty": "Bloomberg returned no FUT_LAST_TRADE_DT"}
    assert block["summary"] == "1 contract date stored, 1 future moved to Bloomberg's expiry; 2 tickers gave no date"
    from data.contracts import static_dates
    assert static_dates(conn, "CLZ26 Comdty")["first_notice_date"] == ""
    assert static_dates(conn, "NGZ26 Comdty") is None
    # Every future was still priced, the two without dates at their estimate.
    fut = {(i["instrument_id"], i["settle_date"]): i["status"] for i in status["items"] if i["mark_type"] == "FUTURE_PX"}
    assert fut == {("CLZ26 Comdty", BBG_LAST_TRADE): "OK", ("NGZ26 Comdty", "2026-11-30"): "OK",
                   ("HOZ26 Comdty", "2026-11-30"): "OK"}


def test_a_dates_request_that_fails_lists_every_ticker_and_the_prices_are_still_asked(
        tmp_path, monkeypatch, fake_apply):
    p, conn = _db(tmp_path)
    log = _fake_bloomberg(monkeypatch, raise_on_dates=pm.BloombergRequestError(
        "ReferenceDataRequest", ["CLZ6 Comdty"], ["FUT_LAST_TRADE_DT"], "TIMEOUT", "no response"))
    status = _pull(p)
    block = status["contract_dates"]
    assert block["stored"] == 0 and [f["ticker"] for f in block["failed"]] == ["CLZ6 Comdty"]
    assert block["failed"][0]["reason"].startswith("request failed:")
    assert fake_apply == [True]                   # still applied: dates stored earlier would move
    assert "FUTURE_PX_LIVE" in [purpose for purpose, _t, _f in log]
    assert conn.execute("SELECT settle_date FROM marks WHERE mark_type = 'FUTURE_PX'").fetchall() == [(ESTIMATE,)]


def test_a_future_with_no_bloomberg_ticker_is_never_asked_and_is_listed_with_its_reason(
        tmp_path, monkeypatch, fake_apply):
    p, conn = _db(tmp_path, extra_futures=[("XXZ26 Comdty", "", "2026-12-31")])
    log = _fake_bloomberg(monkeypatch)
    status = _pull(p)
    asked = {t for _purpose, tickers, _f in log for t in tickers}
    assert "" not in asked and all("XX" not in t for t in asked)
    assert all(r.bbg_ticker for r in live.build_requests(conn, TODAY.isoformat()))
    assert [i["instrument_id"] for i in status["items"] if i["mark_type"] == "FUTURE_PX"] == ["CLZ26 Comdty"]
    [entry] = status["not_requestable"]
    assert entry["instrument_id"] == "XXZ26 Comdty" and entry["trade_ids"] == ["f1"]
    assert entry["reason"] == "no verified Bloomberg ticker for NYMEX:XX"      # the library's own reason
    assert any("XXZ26 Comdty not requested" in w for w in status["warnings"])
    assert status["contract_dates"]["requested"] == 1            # only CL's dates were asked for


def test_no_terminal_asks_nothing_and_applies_nothing(tmp_path, monkeypatch, fake_apply):
    p, conn = _db(tmp_path)
    log = _fake_bloomberg(monkeypatch)
    monkeypatch.setattr(live, "availability", lambda host, port: (False, "blpapi is not installed on this computer"))
    monkeypatch.setattr(live, "recalc_options_on_file", lambda db, today: {"as_of": today.isoformat(), "days": [],
                                                                           "priced": 0, "skipped": 0, "closed_out": 0})
    status = live.pull_once(p, today=TODAY)
    assert log == [] and fake_apply == [] and "contract_dates" not in status
    from data.contracts import static_dates
    assert static_dates(conn, "CLZ26 Comdty") is None


def test_a_book_with_no_commodity_future_neither_asks_nor_applies(tmp_path, fake_apply):
    conn = schema.connect(tmp_path / "risk.db")

    def no_session():
        raise AssertionError("no session is opened when the library lists no contract dates")

    block = live.contract_dates_step(conn, TODAY, no_session)
    assert block == {"requested": 0, "stored": 0, "failed": [], "applied": {}, "summary": ""}
    assert fake_apply == []


def test_contract_dates_summary_wording():
    assert live.contract_dates_summary({"requested": 2, "stored": 2, "failed": [],
                                        "applied": {"updated": [{}, {}]}}) == \
        "2 contract dates stored, 2 futures moved to Bloomberg's expiry"
    assert live.contract_dates_summary({"requested": 1, "stored": 1, "failed": [],
                                        "applied": {"error": "boom"}}) == \
        "1 contract date stored, 0 futures moved to Bloomberg's expiry; moving the futures stopped (boom)"


# --------------------------------------------------------------------------- options on futures (Phase 5)
OPTION_ID, OPTION_TICKER = "CLZ26C 75 Comdty", "CLZ6C 75 Comdty"
OPTION_ESTIMATE = "2026-11-30"
BBG_OPT_EXPIRE = "2026-11-17"


def _option_db(tmp_path):
    """The CL December future (its trade) and a long call on it, both on estimated dates."""
    p, conn = _db(tmp_path)
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                 (OPTION_ID, "CMDTY_OPTION", "NYMEX:CL", "USD", 1000.0, 0, OPTION_TICKER, OPTION_ESTIMATE))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("o1", "XLSX", OPTION_ID, "CMDTY_OPTION", "o1", "2026-09-10", 3, 1.25, "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 ("o1", 1, "NOTIONAL", "USD", 3 * 1000 * 1.25, "2026-09-10", OPTION_ESTIMATE, 1.25, 0))
    conn.commit()
    return p, conn


def test_an_option_on_a_future_has_its_dates_asked_with_the_option_fields_and_stored_as_its_last_trade(
        tmp_path, monkeypatch, fake_apply):
    """bbg-library lists an option on a future under CONTRACT_DATES with the options' own
    fields (OPT_EXPIRE_DT, LAST_TRADEABLE_DT): asked in a request of their own, the expiry
    stored as the option's last trade date with no first notice, and the option moved."""
    p, conn = _option_db(tmp_path)
    dates = {"CLZ6 Comdty": {"FUT_LAST_TRADE_DT": date(2026, 11, 19), "FUT_NOTICE_FIRST": date(2026, 11, 20)},
             OPTION_TICKER: {"OPT_EXPIRE_DT": None, "LAST_TRADEABLE_DT": date(2026, 11, 17)}}
    log = _fake_bloomberg(monkeypatch, dates=dates)
    status = _pull(p)
    asks = [(tickers, fields) for purpose, tickers, fields in log if purpose == live.CONTRACT_DATES]
    assert sorted(asks) == sorted([(["CLZ6 Comdty"], ["FUT_LAST_TRADE_DT", "FUT_NOTICE_FIRST"]),
                                   ([OPTION_TICKER], ["OPT_EXPIRE_DT", "LAST_TRADEABLE_DT"])])
    from data.contracts import static_dates
    got = static_dates(conn, OPTION_ID)
    assert got["last_trade_date"] == BBG_OPT_EXPIRE and got["first_notice_date"] == "" and got["source"] == "BBG_BDP"
    assert static_dates(conn, "CLZ26 Comdty")["first_notice_date"] == BBG_FIRST_NOTICE
    block = status["contract_dates"]
    assert block["requested"] == 2 and block["stored"] == 2 and block["failed"] == []
    assert block["summary"] == "2 contract dates stored, 1 future and 1 option on futures moved to Bloomberg's expiry"
    # the option's price asked after its dates, at Bloomberg's expiry
    assert conn.execute("SELECT settle_date FROM marks WHERE instrument_id = ? AND mark_type = 'FUTURE_PX'",
                        (OPTION_ID,)).fetchall() == [(BBG_OPT_EXPIRE,)]


def test_an_option_bloomberg_gives_no_expiry_for_is_listed_with_both_fields_named(tmp_path, monkeypatch, fake_apply):
    p, conn = _option_db(tmp_path)
    dates = {"CLZ6 Comdty": {"FUT_LAST_TRADE_DT": date(2026, 11, 19)}, OPTION_TICKER: {}}
    _fake_bloomberg(monkeypatch, dates=dates)
    block = _pull(p)["contract_dates"]
    assert block["stored"] == 1
    assert block["failed"] == [{"ticker": OPTION_TICKER,
                                "reason": "Bloomberg returned no OPT_EXPIRE_DT or LAST_TRADEABLE_DT"}]


def test_an_option_on_a_future_is_priced_live_at_bloombergs_mid(tmp_path, monkeypatch, fake_apply):
    """CMDTY_OPTION goes through the futures' price request like EQ_OPTION: PX_MID when
    Bloomberg has one, since the last trade of one strike can be hours old."""
    p, conn = _option_db(tmp_path)
    _fake_bloomberg(monkeypatch)

    def fetch_reference(session, service, tickers, fields, overrides=None, diag=None, tag=None):
        if (tag or {}).get("purpose") == live.CONTRACT_DATES:
            return {}
        assert "PX_MID" in fields
        return {t: ({"PX_LAST": 1.10, "PX_MID": 1.42} if t == OPTION_TICKER else {"PX_LAST": 71.5}) for t in tickers}

    monkeypatch.setattr(pm, "fetch_reference", fetch_reference)
    status = _pull(p)
    item = next(i for i in status["items"] if i["instrument_id"] == OPTION_ID)
    assert item["status"] == "OK" and item["value"] == 1.42 and item["detail"] == "live PX_MID"
    assert conn.execute("SELECT value, source FROM marks WHERE instrument_id = ? AND mark_type = 'FUTURE_PX'",
                        (OPTION_ID,)).fetchall() == [(1.42, "BBG_BDH")]
    fut = next(i for i in status["items"] if i["instrument_id"] == "CLZ26 Comdty")
    assert fut["value"] == 71.5 and fut["detail"] == "live PX_LAST"
    assert OPTION_ID in live._listed_option_ids(conn) and "CLZ26 Comdty" not in live._listed_option_ids(conn)
