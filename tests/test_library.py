"""data/bloomberg/library.py: the Bloomberg library (user decision 2026-09-21).

What the trades on file need from Bloomberg for their P&L is kept in `bbg_library`. It
changes only when the trades change, a pull asks for what is in it and nothing else, and
the list it yields is the one the pull used to work out from the trades on every cycle.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from data.bloomberg import library, live
from data.ingest import schema

_SAMPLE_CSV = Path(__file__).resolve().parents[1] / "data" / "raw" / "new_sample_trades.csv"
_INSTRUMENT = "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)"
_TRADE = "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
_LEG = "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)"


def _db(tmp_path):
    """A forward (AUDUSD), a cross (EURSEK), a future, an option still open (USDJPY), an
    option long expired (EURUSD) and a USD swap."""
    p = tmp_path / "risk.db"
    conn = schema.connect(p)
    conn.executemany(_INSTRUMENT, [
        ("AUDUSD", "FX", "AUD", "USD", 1, 0, "AUDUSD Curncy", "9999-12-31"),
        ("EURSEK", "FX", "EUR", "SEK", 1, 0, "EURSEK Curncy", "9999-12-31"),
        ("ESZ6 Index", "FUTURE", "ES", "USD", 50, 0, "ESZ6 Index", "2026-12-18"),
        ("USDJPY111926P-1", "FX_OPTION", "USD", "JPY", 1, 0, "USDJPY111926P-1", "2026-11-19"),
        ("EURUSD030626C-2", "FX_OPTION", "EUR", "USD", 1, 0, "EURUSD030626C-2", "2026-03-06"),
        ("IRSOIS-USD-1", "IRS", "USD", "USD", 1, 0, "IRSOIS-USD-1", "2031-09-01"),
    ])
    conn.executemany(_TRADE, [
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", "2026-08-10", -1e6, 0.65, "acc", "cp", "", "t", "d", ""),
        ("x1", "XLSX", "EURSEK", "FX_FWD", "x1", "2026-08-10", 1e6, 11.2, "acc", "cp", "", "t", "d", ""),
        ("f1", "XLSX", "ESZ6 Index", "FUTURE", "f1", "2026-08-10", 2, 6500.0, "acc", "cp", "", "t", "d", ""),
        ("o1", "XLSX", "USDJPY111926P-1", "FX_OPTION", "o1", "2026-08-19", 1e6, 0.14, "acc", "cp", "", "t", "d", ""),
        ("o2", "XLSX", "EURUSD030626C-2", "FX_OPTION", "o2", "2026-01-05", 1e6, 0.01, "acc", "cp", "", "t", "d", ""),
        ("s1", "XLSX", "IRSOIS-USD-1", "IRS", "s1", "2026-08-28", 1e7, 0.035, "acc", "cp", "", "t", "d", ""),
    ])
    conn.executemany(_LEG, [
        ("a1", 1, "FX_NEAR", "AUD", -1e6, "2026-08-10", "2026-10-16", 0.65, 1),
        ("a1", 2, "FX_NEAR", "USD", 650000, "2026-08-10", "2026-10-16", 0.65, 1),
        ("x1", 1, "FX_NEAR", "EUR", 1e6, "2026-08-10", "2026-10-30", 11.2, 1),
        ("x1", 2, "FX_NEAR", "SEK", -11.2e6, "2026-08-10", "2026-10-30", 11.2, 1),
        ("f1", 1, "NOTIONAL", "USD", 650000, "2026-08-10", "2026-12-18", 6500.0, 0),
        ("o1", 1, "NOTIONAL", "USD", 1e6, "2026-08-19", "2026-11-19", 0.14, 0),
        ("o2", 1, "NOTIONAL", "EUR", 1e6, "2026-01-05", "2026-03-06", 0.01, 0),
        ("s1", 1, "FIXED", "USD", -1e7, "2026-09-01", "2031-09-01", 0.035, 1),
        ("s1", 2, "FLOAT", "USD", 1e7, "2026-09-01", "2031-09-01", 0.0, 1),
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
    # rates and vol read the same library: the swap's and the open option's currencies, the
    # open option's pair -- never the pair of the option that expired in March
    assert library.keys(conn, as_of, "OIS_CURVE") == ["JPY", "USD"]
    assert library.keys(conn, as_of, "FIXINGS") == ["USD"]
    assert library.keys(conn, as_of, "VOL_SMILE") == ["USDJPY"]
    # nothing is asked for after its date: the forwards have settled, the option has expired
    later = _requested(conn, "2026-11-20")
    assert later == {("ESZ6 Index", "FUTURE_PX", "2026-12-18", "ESZ6 Index")}
    assert library.keys(conn, "2026-11-20", "VOL_SMILE") == []
    assert library.keys(conn, "2026-11-20", "OIS_CURVE") == ["USD"]       # the swap runs to 2031


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
    assert any(t["used_for"] == "USD overnight fixings" for t in found.values())
    summary = library.summary(conn, "2026-09-21")
    assert summary["tickers"] == len(found) and summary["trades"] == 5 and summary["synced_at"]


@pytest.mark.skipif(not _SAMPLE_CSV.exists(), reason="data/raw/new_sample_trades.csv is not on this machine")
def test_sample_book_request_list_is_what_the_trades_themselves_call_for(tmp_path):
    """The library yields the list the pull used to work out from the trades on every
    cycle (the queries are still in live.py), on the real reference book, on every date
    checked -- and an upload brings the library up to date and says so."""
    from data.ingest.upload import import_blotter
    p = tmp_path / "risk.db"
    message = import_blotter(_SAMPLE_CSV.read_bytes(), _SAMPLE_CSV.name, p)
    assert "Bloomberg library:" in message and "Nothing was pulled" in message
    conn = schema.connect(p)
    assert not library.is_out_of_date(conn)                            # the upload synced it

    def from_the_trades(as_of):
        keys = set()
        for instrument_id, ticker, settle in conn.execute(live._OPEN_FX_SQL, {"as_of": as_of}):
            keys |= {(instrument_id, "SPOT", as_of, ticker), (instrument_id, "FWD_OUTRIGHT", settle, ticker)}
        for leg in live._cross_usd_legs(conn, as_of) + live._option_usd_legs(conn, as_of):
            keys.add((leg["instrument_id"], "SPOT", as_of, leg["bbg_ticker"]))
        for o in live._option_mark_rows(conn, as_of):
            keys |= {(o["instrument_id"], "SPOT", as_of, o["bbg_ticker"]),
                     (o["instrument_id"], "FWD_OUTRIGHT", o["expiry"], o["bbg_ticker"])}
        for instrument_id, ticker, settle in conn.execute(live._OPEN_FUTURE_SQL, {"as_of": as_of}):
            keys.add((instrument_id, "FUTURE_PX", settle, ticker))
        return keys

    for as_of in ("2026-08-24", "2026-09-18", "2026-09-23", "2026-10-15", "2026-11-25", "2027-01-15"):
        assert _requested(conn, as_of) == from_the_trades(as_of), as_of
    assert _requested(conn, "2026-09-18")

    # a second upload of the same file replaces the book: same library, nothing left over
    before = conn.execute("SELECT COUNT(*) FROM bbg_library").fetchone()[0]
    import_blotter(_SAMPLE_CSV.read_bytes(), _SAMPLE_CSV.name, p)
    assert conn.execute("SELECT COUNT(*) FROM bbg_library").fetchone()[0] == before
