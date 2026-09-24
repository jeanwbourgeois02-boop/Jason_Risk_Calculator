"""data/bloomberg/library.py: the Bloomberg library (user decision 2026-09-21).

What the trades on file need from Bloomberg for their P&L is kept in `bbg_library`. It
changes only when the trades change, a pull asks for what is in it and nothing else, and
the list it yields is the one the pull used to work out from the trades on every cycle.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from data.bloomberg import library, live
from data.ingest import schema

_SAMPLE_CSV = Path(__file__).resolve().parents[1] / "data" / "sample" / "blotter_sample.csv"
_INSTRUMENT = "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)"
_TRADE = "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
_LEG = "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)"


def _db(tmp_path):
    """A forward (AUDUSD), a cross (EURSEK), a future, an option still open (USDJPY) and an
    option long expired (EURUSD)."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.executemany(_INSTRUMENT, [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("EURSEK", "FX", "EUR", "SEK", 1, 0, "EURSEK Curncy", "9999-12-31"),
        ("ESZ6 Index", "FUTURE", "ES", "USD", 50, 0, "ESZ6 Index", "2026-12-18"),
        ("USDJPY111926P-1", "FX_OPTION", "USD", "JPY", 1, 0, "USDJPY111926P-1", "2026-11-19"),
        ("EURUSD030626C-2", "FX_OPTION", "EUR", "USD", 1, 0, "EURUSD030626C-2", "2026-03-06"),
    ])
    conn.executemany(_TRADE, [
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""),
        ("x1", "XLSX", "EURSEK", "FX_FWD", "x1", "2026-08-10", 1e6, 11.2, "acc", "cp", "", "t", "d", ""),
        ("f1", "XLSX", "ESZ6 Index", "FUTURE", "f1", "2026-08-10", 2, 6500.0, "acc", "cp", "", "t", "d", ""),
        ("o1", "XLSX", "USDJPY111926P-1", "FX_OPTION", "o1", "2026-08-19", 1e6, 0.14, "acc", "cp", "", "t", "d", ""),
        ("o2", "XLSX", "EURUSD030626C-2", "FX_OPTION", "o2", "2026-01-05", 1e6, 0.01, "acc", "cp", "", "t", "d", ""),
    ])
    conn.executemany(_LEG, [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-10-16", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-10-16", 0.65, 1),
        ("x1", 1, "FX_NEAR", "EUR", 1e6, "2026-08-10", "2026-10-30", 11.2, 1),
        ("x1", 2, "FX_NEAR", "SEK", -11.2e6, "2026-08-10", "2026-10-30", 11.2, 1),
        ("f1", 1, "NOTIONAL", "USD", 650000, "2026-08-10", "2026-12-18", 6500.0, 0),
        ("o1", 1, "NOTIONAL", "USD", 1e6, "2026-08-19", "2026-11-19", 0.14, 0),
        ("o2", 1, "NOTIONAL", "EUR", 1e6, "2026-01-05", "2026-03-06", 0.01, 0),
    ])
    conn.commit()
    return p, conn


def _requested(conn, as_of):
    return {(r.instrument_id, r.mark_type, r.settle_date, r.bbg_ticker) for r in live.build_requests(conn, as_of)}


def test_the_pull_asks_for_what_each_trade_needs_and_nothing_else(tmp_path):
    p, conn = _db(tmp_path)
    as_of = "2026-09-21"
    assert _requested(conn, as_of) == {
        ("AUDUSD", "SPOT", as_of, "AUDUSD Curncy"), ("AUDUSD", "FWD_OUTRIGHT", "2026-10-16", "AUDUSD Curncy"),
        ("EURSEK", "SPOT", as_of, "EURSEK Curncy"), ("EURSEK", "FWD_OUTRIGHT", "2026-10-30", "EURSEK Curncy"),
        ("EURUSD", "SPOT", as_of, "EURUSD Curncy"), ("USDSEK", "SPOT", as_of, "USDSEK Curncy"),   # the cross's USD conversion
        ("USDJPY", "SPOT", as_of, "USDJPY Curncy"), ("USDJPY", "FWD_OUTRIGHT", "2026-11-19", "USDJPY Curncy"),
        ("ESZ6 Index", "FUTURE_PX", "2026-12-18", "ESZ6 Index"),
    }
    # rates and vol read the same library: the open option's currencies and pair -- never
    # the pair of the option that expired in March
    assert library.keys(conn, as_of, "OIS_CURVE") == ["JPY", "USD"]
    assert library.keys(conn, as_of, "VOL_SMILE") == ["USDJPY"]
    # nothing is asked for after its date: the forwards have settled, the option has expired
    later = _requested(conn, "2026-11-20")
    assert later == {("ESZ6 Index", "FUTURE_PX", "2026-12-18", "ESZ6 Index")}
    assert library.keys(conn, "2026-11-20", "VOL_SMILE") == []
    assert library.keys(conn, "2026-11-20", "OIS_CURVE") == []


def test_vol_step_pulls_open_options_pairs_only(tmp_path):
    """It used to pull the smile (45 tickers a pair) of every FX_OPTION instrument ever on
    file as long as the book held one option of any age."""
    p, conn = _db(tmp_path)
    asked = []

    class _Source:
        def get_vol_quotes(self, pairs):
            asked.append(list(pairs))
            raise RuntimeError("stop here")

    live._vol_step(conn, date(2026, 9, 21), "localhost", 8194, vol_source=_Source())
    assert asked == [["USDJPY"]]
    asked.clear()
    out = live._vol_step(conn, date(2026, 11, 20), "localhost", 8194, vol_source=_Source())
    assert asked == [] and out["skipped"]                              # no open option: Bloomberg is not asked


def test_library_changes_only_when_the_trades_change(tmp_path):
    p, conn = _db(tmp_path)
    assert library.is_out_of_date(conn)                                # trades were written, nothing synced yet
    first = library.sync(conn)
    assert first["added"] == first["total"] > 0 and first["removed"] == 0
    assert not library.is_out_of_date(conn)
    stamps = dict(conn.execute("SELECT trade_id || kind || key || settle_date, added_at FROM bbg_library"))

    # a pull's writes (marks, realised rows) are not trades: the library is left alone
    conn.execute("INSERT INTO marks VALUES ('2026-09-21','AUDUSD','2026-09-21','SPOT',0.66,'BBG_BFXFORWARD','t')")
    conn.commit()
    live.build_requests(conn, "2026-09-21")
    assert not library.is_out_of_date(conn)
    assert library.sync(conn) == {"added": 0, "removed": 0, "total": first["total"]}

    # a new trade (manual entry writes `trades` / `trade_legs` like this) marks it out of
    # date, and the next reader brings it up to date before it reads
    conn.execute(_TRADE, ("MANUAL-1", "MANUAL", "AUDUSD", "FX_FWD", "MANUAL-1", "2026-09-21", 5e5, 0.66,
                          "acc", "cp", "", "t", "d", ""))
    conn.executemany(_LEG, [("MANUAL-1", 1, "FX_NEAR", "AUD", 5e5, "2026-09-21", "2026-12-15", 0.66, 1),
                            ("MANUAL-1", 2, "FX_NEAR", "USD", -330000, "2026-09-21", "2026-12-15", 0.66, 1)])
    conn.commit()
    assert library.is_out_of_date(conn)
    assert ("AUDUSD", "FWD_OUTRIGHT", "2026-12-15", "AUDUSD Curncy") in _requested(conn, "2026-09-21")
    assert not library.is_out_of_date(conn)
    after = dict(conn.execute("SELECT trade_id || kind || key || settle_date, added_at FROM bbg_library"))
    assert all(after[k] == v for k, v in stamps.items())               # what was there keeps its added_at
    assert len(after) == len(stamps) + 2                               # the new trade's SPOT and FWD_OUTRIGHT

    # a trade that leaves the book takes its rows with it
    conn.execute("DELETE FROM trade_legs WHERE trade_id = 'MANUAL-1'")
    conn.execute("DELETE FROM trades WHERE trade_id = 'MANUAL-1'")
    conn.commit()
    assert library.sync(conn)["removed"] == 2
    assert conn.execute("SELECT COUNT(*) FROM bbg_library WHERE trade_id = 'MANUAL-1'").fetchone()[0] == 0


def test_a_read_only_connection_still_sees_the_whole_library(tmp_path):
    """Every tab callback reads through a read-only handle: it cannot write the library,
    so it works the same rows out in memory rather than read one that is behind the book."""
    p, conn = _db(tmp_path)
    conn.close()
    ro = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)
    try:
        assert library.is_out_of_date(ro)
        assert ("USDJPY", "SPOT") in {(r["key"], r["kind"]) for r in library.needed_on(ro, "2026-09-21")}
        assert ro.execute("SELECT COUNT(*) FROM bbg_library").fetchone()[0] == 0
    finally:
        ro.close()


def test_tickers_lists_the_securities_a_pull_asks_for(tmp_path):
    p, conn = _db(tmp_path)
    found = {(t["ticker"], t["field"]): t for t in library.tickers(conn, "2026-09-21")}
    assert found[("AUDUSD Curncy", "PX_LAST")]["trades"] == 1
    assert ("AUDUSD Curncy", "FWD_CURVE") in found and ("ESZ6 Index", "PX_LAST") in found
    assert found[("USDSEK Curncy", "PX_LAST")]["used_for"] == "USDSEK spot (USD conversion)"
    assert any(t["used_for"].startswith("USDJPY vol") for t in found.values())
    assert not any("EURUSD vol" in t["used_for"] for t in found.values())          # the expired option
    assert any(t["used_for"].startswith("USD OIS curve") for t in found.values())
    summary = library.summary(conn, "2026-09-21")
    assert summary["tickers"] == len(found) and summary["trades"] == 4 and summary["synced_at"]


# =========================================================================== 2026-09-24: the macro needs retired
# Commodity conversion Phase 2 (user approval 2026-09-24): rates / IRS, NDFs and the equity
# index leave the app. A swap, an NDF currency and a listed index option no longer call for
# an OIS curve or fixings, a 1M NDF outright or a fixing, an index level or a dividend yield.
_RETIRED_KINDS = {"NDF_1M", "NDF_FIX", "FIXINGS", "DIV_YIELD"}


def _macro_db(tmp_path):
    """A USD swap, a USDKRW forward (an NDF currency) and a listed SPX put: what the macro
    book held and the library no longer lists for it."""
    p = tmp_path / "macro.db"
    conn = schema.connect(p)
    conn.executemany(_INSTRUMENT, [
        ("IRSOIS-USD-1", "IRS", "USD", "USD", 1, 0, "IRSOIS-USD-1", "2031-09-01"),
        ("USDKRW", "FX", "USD", "KRW", 1, 1, "USDKRW Curncy", "9999-12-31"),
        ("SPX-P7615", "EQ_OPTION", "SPX", "USD", 100, 0, "SPX Index", "2026-10-16"),
    ])
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type) VALUES (?,?,?)",
                 ("SPX-P7615", 7615.0, "PUT"))
    conn.executemany(_TRADE, [
        ("s1", "XLSX", "IRSOIS-USD-1", "IRS", "s1", "2026-08-28", 1e7, 0.035, "acc", "cp", "", "t", "d", ""),
        ("k1", "XLSX", "USDKRW", "FX_FWD", "k1", "2026-08-10", 1e6, 1390.0, "acc", "cp", "", "t", "d", ""),
        ("e1", "XLSX", "SPX-P7615", "EQ_OPTION", "e1", "2026-09-01", 2, 41.5, "acc", "cp", "", "t", "d", ""),
    ])
    conn.executemany(_LEG, [
        ("s1", 1, "FIXED", "USD", -1e7, "2026-09-01", "2031-09-01", 0.035, 1),
        ("s1", 2, "FLOAT", "USD", 1e7, "2026-09-01", "2031-09-01", 0.0, 1),
        ("k1", 1, "FX_NEAR", "USD", 1e6, "2026-08-10", "2026-09-30", 1390.0, 0),
        ("k1", 2, "FX_NEAR", "KRW", -1390e6, "2026-08-10", "2026-09-30", 1390.0, 0),
        ("e1", 1, "NOTIONAL", "USD", 200, "2026-09-01", "2026-10-16", 41.5, 0),
    ])
    conn.commit()
    return p, conn


def test_the_retired_macro_needs_are_no_longer_listed(tmp_path):
    p, conn = _macro_db(tmp_path)
    found = library.rows(conn)
    assert not [r for r in found if r["kind"] in _RETIRED_KINDS]
    assert not [r for r in found if r["role"] == "UNDERLYING"]
    assert not [r for r in found if r["trade_id"] == "s1"]                        # a swap needs nothing
    # an NDF currency's forward is an ordinary forward: its spot and its leg's outright
    assert {(r["kind"], r["key"], r["settle_date"]) for r in found if r["trade_id"] == "k1"} == {
        ("SPOT", "USDKRW", library.SENTINEL), ("FWD_OUTRIGHT", "USDKRW", "2026-09-30")}
    # a listed option keeps Bloomberg's own price of it, nothing else (Phase 5's path)
    assert [(r["kind"], r["key"], r["bbg_ticker"]) for r in found if r["trade_id"] == "e1"] == [
        ("FUTURE_PX", "SPX-P7615", "SPX US 10/16/26 P7615 Index")]
    as_of = "2026-09-21"
    assert library.keys(conn, as_of, "OIS_CURVE") == [] and library.keys(conn, as_of, "FIXINGS") == []
    assert library.keys(conn, as_of, "DIV_YIELD") == []
    assert library.history_inputs_needed(conn, as_of) == []
    listed = library.tickers(conn, as_of)
    assert {t["ticker"] for t in listed} == {"USDKRW Curncy", "SPX US 10/16/26 P7615 Index"}
    assert _requested(conn, as_of) == {
        ("USDKRW", "SPOT", as_of, "USDKRW Curncy"), ("USDKRW", "FWD_OUTRIGHT", "2026-09-30", "USDKRW Curncy"),
        ("SPX-P7615", "FUTURE_PX", "2026-10-16", "SPX US 10/16/26 P7615 Index")}
    # a past close: the same marks, and no fixing-date need
    assert {r["kind"] for r in library.needed_in_range(conn, "2026-09-01", "2026-09-30")} == {
        "SPOT", "FWD_OUTRIGHT", "FUTURE_PX"}
    assert "SPX Index" not in {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    assert library.MARK_KINDS == ("SPOT", "FWD_OUTRIGHT", "FUTURE_PX")
    assert library.LIVE_ONLY_KINDS == (library.CONTRACT_DATES,)
    assert library.SET_KINDS == ("OIS_CURVE", "VOL_SMILE")


def test_a_library_synced_by_older_code_drops_the_retired_rows_at_its_next_read(tmp_path):
    """The stored code version counts (seen 2026-09-22, when a new kind reached no existing
    database until its next upload): a library an older code wrote, holding the macro rows,
    is out of date and a read resyncs it without them."""
    p, conn = _macro_db(tmp_path)
    library.sync(conn)
    assert not library.is_out_of_date(conn)
    assert conn.execute("SELECT code_version FROM bbg_library_state").fetchone() == (library.LIBRARY_VERSION,)
    assert library.LIBRARY_VERSION not in ("2026-09-22.2", "2026-09-24.1")
    old = [("s1", "OIS_CURVE", "USD", library.SENTINEL, "", library.ROLE_PAIR, "IRS", "2026-08-28", "2031-09-01"),
           ("s1", "FIXINGS", "USD", library.SENTINEL, "", library.ROLE_PAIR, "IRS", "2026-08-28", "2031-09-01"),
           ("k1", "NDF_1M", "USDKRW", library.SENTINEL, "KWN+1M Curncy", library.ROLE_PAIR, "FX_FWD", "2026-08-10",
            "2026-09-30"),
           ("k1", "NDF_FIX", "USDKRW", "2026-09-28", "KOBRUSD Index", library.ROLE_PAIR, "FX_FWD", "2026-09-28",
            "2026-09-28"),
           ("e1", "SPOT", "SPX Index", library.SENTINEL, "SPX Index", "UNDERLYING", "EQ_OPTION", "2026-09-01",
            "2026-10-16"),
           ("e1", "DIV_YIELD", "SPX Index", library.SENTINEL, "SPX Index", library.ROLE_PAIR, "EQ_OPTION",
            "2026-09-01", "2026-10-16")]
    conn.executemany("INSERT INTO bbg_library (trade_id, kind, key, settle_date, bbg_ticker, role, product, "
                     "needed_from, needed_until, added_at) VALUES (?,?,?,?,?,?,?,?,?,'x')", old)
    conn.execute("UPDATE bbg_library_state SET dirty = 0, code_version = '2026-09-24.1'")
    conn.commit()
    assert library.is_out_of_date(conn)
    found = library.rows(conn)                                                    # a read resyncs it
    assert not [r for r in found if r["kind"] in _RETIRED_KINDS or r["role"] == "UNDERLYING"]
    assert not [r for r in found if r["trade_id"] == "s1"]
    assert conn.execute("SELECT dirty, code_version FROM bbg_library_state").fetchone() == (0, library.LIBRARY_VERSION)
    assert conn.execute("SELECT COUNT(*) FROM bbg_library WHERE kind IN ('NDF_1M','NDF_FIX','FIXINGS','DIV_YIELD') "
                        "OR role = 'UNDERLYING' OR trade_id = 's1'").fetchone() == (0,)


def test_the_sample_book_lists_only_what_its_trades_call_for(tmp_path):
    """The synthetic sample (commodity futures in five currencies, FX forwards, one spot,
    FX options): an upload brings the library up to date, every mark a pull asks for is in
    it, a non-USD future brings its currency's USD conversion spot, and a second upload of
    the same file leaves the same library."""
    from data.ingest.upload import import_blotter
    p = tmp_path / "risk.db"
    message = import_blotter(_SAMPLE_CSV.read_bytes(), _SAMPLE_CSV.name, p)
    assert "Bloomberg library:" in message
    conn = schema.connect(p)
    assert not library.is_out_of_date(conn)                            # the upload synced it
    found = library.rows(conn)
    assert found
    assert {r["kind"] for r in found} <= {"SPOT", "FWD_OUTRIGHT", "FUTURE_PX", library.CONTRACT_DATES,
                                          "OIS_CURVE", "VOL_SMILE"}
    assert {r["product"] for r in found} >= {"FUTURE", "FX_FWD", "FX_OPTION"}
    conversions = {r["key"] for r in found if r["product"] == "FUTURE" and r["role"] == library.ROLE_CONVERSION}
    assert conversions >= {"USDCNY", "EURUSD", "GBPUSD", "USDJPY"}
    asked_any = False
    for as_of in ("2026-08-24", "2026-09-18", "2026-09-23", "2026-10-15", "2026-11-25", "2027-01-15"):
        listed = {(r["key"], r["kind"]) for r in library.needed_on(conn, as_of)}
        asked = _requested(conn, as_of)
        asked_any = asked_any or bool(asked)
        assert {(a[0], a[1]) for a in asked} <= listed, as_of
    assert asked_any
    before = conn.execute("SELECT COUNT(*) FROM bbg_library").fetchone()[0]
    import_blotter(_SAMPLE_CSV.read_bytes(), _SAMPLE_CSV.name, p)
    assert conn.execute("SELECT COUNT(*) FROM bbg_library").fetchone()[0] == before
