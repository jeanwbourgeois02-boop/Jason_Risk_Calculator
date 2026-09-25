"""Tests for ui/tabs/market_data.py (C3 Market data tab, pair-organised rewrite)."""
from __future__ import annotations

import sqlite3

import pytest

from ui.tabs import market_data as md


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE instruments (instrument_id TEXT PRIMARY KEY, asset_class TEXT, base_ccy TEXT,
                                   quote_ccy TEXT, multiplier REAL, is_ndf INTEGER, bbg_ticker TEXT,
                                   expiry_date TEXT);
        CREATE TABLE trades (trade_id TEXT PRIMARY KEY, source TEXT, instrument_id TEXT, product TEXT,
                              package_id TEXT, trade_date TEXT, quantity REAL, price REAL, account TEXT,
                              counterparty TEXT, strategy TEXT, trader TEXT, description TEXT);
        CREATE TABLE trade_legs (trade_id TEXT, leg_no INTEGER, leg_type TEXT, ccy TEXT, amount REAL,
                                  start_date TEXT, settle_date TEXT, rate REAL, settles_cash INTEGER);
        CREATE TABLE marks (as_of_date TEXT, instrument_id TEXT, settle_date TEXT, mark_type TEXT,
                             value REAL, source TEXT, snapped_at TEXT,
                             PRIMARY KEY (as_of_date, instrument_id, settle_date, mark_type, source));
        CREATE VIEW marks_official AS
            SELECT * FROM marks WHERE
            (mark_type IN ('SPOT','FWD_OUTRIGHT') AND source='BBG_BFXFORWARD') OR
            (mark_type='FUTURE_PX' AND source='BBG_BDH') OR
            (mark_type IN ('DELTA','PREMIUM') AND source='MANUAL');
        """
    )
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("T1", "BNP", "EURUSD", "FX_FWD", "T1", "2026-08-01", 1_000_000, 1.10,
         "ACC", "CP", "HAHY7", "trader", "desc"),
        ("T2", "BNP", "EURUSD", "FX_FWD", "T2", "2026-07-01", 500_000, 1.08,
         "ACC", "CP", "HAHY7", "trader", "desc"),
        ("T3", "BNP", "USDJPY", "FX_FWD", "T3", "2026-08-01", 1_000_000, 150.0,
         "ACC", "CP", "HAHY7", "trader", "desc"),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("T1", 1, "FX_NEAR", "EUR", 1_000_000, "2026-08-01", "2026-09-18", 1.10, 1),
        ("T1", 2, "FX_NEAR", "USD", -1_100_000, "2026-08-01", "2026-09-18", 1.10, 1),
        ("T2", 1, "FX_NEAR", "EUR", 500_000, "2026-07-01", "2026-10-19", 1.08, 1),
        ("T2", 2, "FX_NEAR", "USD", -540_000, "2026-07-01", "2026-10-19", 1.08, 1),
        ("T3", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-01", "2026-09-18", 150.0, 1),
        ("T3", 2, "FX_NEAR", "JPY", -150_000_000, "2026-08-01", "2026-09-18", 150.0, 1),
    ])
    as_of = "2026-08-18"
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (as_of, "EURUSD", as_of, "SPOT", 1.1050, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "EURUSD", "2026-09-18", "FWD_OUTRIGHT", 1.1080, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "EURUSD", "2026-10-19", "FWD_OUTRIGHT", 1.1120, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "EURUSD", "2026-11-05", "FWD_OUTRIGHT", 1.1150, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "USDJPY", as_of, "SPOT", 149.00, "BNP_BVAL", as_of + "T15:00:00-04:00"),
        (as_of, "USDJPY", "2026-09-18", "FWD_OUTRIGHT", 148.00, "BNP_BVAL", as_of + "T15:00:00-04:00"),
    ])
    conn.commit()
    return conn


AS_OF = "2026-08-18"


def test_pair_options_lists_fx_pairs_and_defaults_to_most_open_trades():
    conn = _db()
    options, default = md.pair_options(conn, AS_OF)
    values = {o["value"] for o in options}
    assert values == {"EURUSD", "USDJPY"}
    # EURUSD has two open trades (T1, T2) vs USDJPY's one -> default EURUSD.
    assert default == "EURUSD"


def test_curve_table_rows_and_tenor_labels():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "EURUSD")
    assert list(df["settle_date"]) == ["2026-09-18", "2026-10-19", "2026-11-05"]
    tenors = dict(zip(df["settle_date"], df["tenor"]))
    assert tenors["2026-09-18"] == "1M"
    assert tenors["2026-10-19"] == "2M"
    assert tenors["2026-11-05"] == "broken"
    # BNP_BVAL is never official -> "reconciliation only" status, source label "BNP file".
    assert set(df["status"]) == {"reconciliation only"}
    assert set(df["source"]) == {"BNP file"}


def test_forward_points_non_jpy_pair_four_decimals():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "EURUSD")
    row = df[df["settle_date"] == "2026-09-18"].iloc[0]
    assert row["points"] == pytest.approx(round(1.1080 - 1.1050, 4))


def test_forward_points_jpy_pair_two_decimals():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "USDJPY")
    row = df[df["settle_date"] == "2026-09-18"].iloc[0]
    assert row["points"] == pytest.approx(round(148.00 - 149.00, 2))
    assert md.decimals_for_pair("USDJPY") == 2
    assert md.decimals_for_pair("EURUSD") == 4


def test_used_by_book_marker_counts_open_trades():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "EURUSD")
    used = dict(zip(df["settle_date"], df["used_by_book"]))
    assert used["2026-09-18"] == 1  # T1
    assert used["2026-10-19"] == 1  # T2
    assert used["2026-11-05"] == ""  # no trade settles here


def test_forward_curve_empty_state():
    conn = _db()
    df = md.forward_curve(conn, AS_OF, "GBPUSD")
    assert df.empty
    body = md.pair_body(conn, AS_OF, "GBPUSD")
    rendered = str(body)
    assert "No spot mark for GBPUSD" in rendered
    assert "No forward marks for GBPUSD on 2026-08-18" in rendered


def test_pair_body_renders_chart_when_curve_present():
    conn = _db()
    body = md.pair_body(conn, AS_OF, "EURUSD")
    rendered = str(body)
    assert md.CURVE_TABLE_ID in rendered
    assert md.CURVE_CHART_ID in rendered


def test_manual_form_prefill():
    form = md.manual_entry_form(default_pair="EURUSD")
    rendered = str(form)
    assert "EURUSD" in rendered
    assert md.MANUAL_INSTRUMENT_ID in rendered


def test_build_layout_has_expected_ids():
    layout = md.build_layout(default_date="2026-08-18")
    rendered = str(layout)
    for expected_id in (md.DATE_PICKER_ID, md.PAIR_DROPDOWN_ID, md.STATUS_ID, md.BODY_ID, md.PULL_NOW_ID,
                        md.PULL_NOW_STATUS_ID, md.PULL_REVISION_ID, md.REFRESH_ID, md.MANUAL_INSTRUMENT_ID):
        assert expected_id in rendered


def test_feed_headline_and_backfill_headline_and_top_bar_status():
    assert md.feed_headline(None).startswith("Bloomberg: no pull recorded yet")
    assert md.feed_headline(None).endswith("pulls only when you press Pull Bloomberg now")
    assert "not connected" in md.feed_headline({"connected": False, "reason": "no port"})
    text = md.feed_headline({"connected": True, "time": "t", "written": 3, "failed": 1})
    assert "3 marks written, 1 failed" in text
    status = {"connected": True, "time": "t", "written": 1, "failed": 0,
              "backfill": {"running": True, "remaining": 4}}
    assert "Backfill: 4 day(s) remaining" in md.top_bar_status(status)
    assert md.backfill_headline(None) is None


def test_pull_timings_line_lists_the_steps_slowest_first_and_is_absent_without_timings():
    timings = {"session": 1.2, "spot": 3.14, "forwards": 21.4, "futures": 0.9, "curves": 8.0, "vol": 0.8,
               "options": 12.0, "ledger": 0.4, "total": 48.3}
    status = {"connected": True, "time": "t", "written": 5, "failed": 0, "timings": timings}
    assert md.pull_timings_line(status) == (
        "Last pull took 48 s: forwards 21 s · options 12 s · curves 8.0 s · spot 3.1 s · session 1.2 s · "
        "futures 0.9 s · vol 0.8 s · ledger 0.4 s")
    # a value that is not a number is left out, never shown as one; "total" alone still reads
    assert md.pull_timings_line({"timings": {"spot": 2.0, "curves": None, "vol": "n/a"}}) == "Last pull by step: spot 2.0 s"
    assert md.pull_timings_line({"timings": {"total": 12}}) == "Last pull took 12 s"
    for absent in (None, {}, {"connected": True}, {"timings": None}, {"timings": {}}, {"timings": "48"}):
        assert md.pull_timings_line(absent) == ""

    # the tab's feed-status block: the status line alone, plus one compact line when there are timings
    assert md.status_block({"connected": True, "time": "t", "written": 5, "failed": 0}) == \
        md.top_bar_status({"connected": True, "time": "t", "written": 5, "failed": 0})
    line, timing_row = md.status_block(status)
    assert line == md.top_bar_status(status) and "last pull took 48 s" in line
    assert timing_row.id == md.PULL_TIMINGS_ID and timing_row.children == md.pull_timings_line(status)


def test_refresh_timer_follows_the_feed_interval_not_a_typed_in_number():
    from data.bloomberg import live
    assert md.REFRESH_MS == live.INTERVAL_SECONDS * 1000
    layout = md.build_layout(default_date="2026-08-18")
    timer = next(c for c in layout.children if getattr(c, "id", None) == md.REFRESH_ID)
    assert timer.interval == md.REFRESH_MS


def test_tenor_label_boundaries():
    assert md.tenor_label("2026-08-18", "2026-08-25") == "1W"
    assert md.tenor_label("2026-08-18", "2027-08-18") == "1Y"
    assert md.tenor_label("2026-08-18", "2026-08-20") == "broken"


# =========================================================================== whole-book checks (2026-09-21)
# "What is missing", "Marks that look wrong", "Past closes the header needs": real schema
# (data.ingest.schema.connect, in memory), so marks_official / trades_official are the app's own.
import datetime as dt  # noqa: E402

from data.ingest import schema  # noqa: E402

TODAY = "2026-09-21"      # a Monday; the previous business day is Friday 2026-09-18
PREV = "2026-09-18"


@pytest.fixture
def book_is_today(monkeypatch):
    from data.bloomberg import live
    monkeypatch.setattr(live, "book_today", lambda: dt.date(2026, 9, 21))


def _instrument(conn, instrument_id, asset_class, base, quote, multiplier=1, expiry="9999-12-31", ticker=None):
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,0,?,?)",
        (instrument_id, asset_class, base, quote, multiplier,
         f"{instrument_id} Curncy" if ticker is None else ticker, expiry))


def _trade(conn, trade_id, instrument_id, product, trade_date, quantity, price, legs):
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
        "account, counterparty, strategy, trader, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, product, trade_id, trade_date, quantity, price, "acc", "cp", "", "t", "d"))
    for n, (leg_type, ccy, amount, settle, cash) in enumerate(legs, start=1):
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (trade_id, n, leg_type, ccy, amount, trade_date, settle, price, cash))


def _mark(conn, as_of, instrument_id, settle, mark_type, value, source="BBG_BFXFORWARD", snapped_at=None):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, settle, mark_type, value, source, snapped_at or f"{as_of}T15:00:00-04:00"))


def _book():
    """Two USDJPY forwards on one value date, a EURSEK cross, a WTI crude future and a USDJPY option,
    all dealt 2026-09-01 and open on TODAY. No marks."""
    conn = schema.connect()
    _instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _instrument(conn, "EURSEK", "FX", "EUR", "SEK")
    # the cross's USD-conversion pairs, never traded: the pull creates these rows before it writes their SPOT
    _instrument(conn, "EURUSD", "FX", "EUR", "USD")
    _instrument(conn, "USDSEK", "FX", "USD", "SEK")
    _instrument(conn, "CLZ26 Comdty", "FUTURE", "NYMEX:CL", "USD", multiplier=1000, expiry="2026-12-18",
                ticker="CLZ26 Comdty")
    _instrument(conn, "USDJPY111926P-1", "FX_OPTION", "USD", "JPY", expiry="2026-11-19")
    _trade(conn, "T1", "USDJPY", "FX_FWD", "2026-09-01", 1_000_000, 147.0,
           [("FX_NEAR", "USD", 1_000_000, "2026-10-15", 1), ("FX_NEAR", "JPY", -147_000_000, "2026-10-15", 1)])
    _trade(conn, "T2", "USDJPY", "FX_FWD", "2026-09-01", -2_000_000, 147.5,
           [("FX_NEAR", "USD", -2_000_000, "2026-10-15", 1), ("FX_NEAR", "JPY", 295_000_000, "2026-10-15", 1)])
    _trade(conn, "T3", "EURSEK", "FX_FWD", "2026-09-01", 5_000_000, 11.2,
           [("FX_NEAR", "EUR", 5_000_000, "2026-11-02", 1), ("FX_NEAR", "SEK", -56_000_000, "2026-11-02", 1)])
    _trade(conn, "F1", "CLZ26 Comdty", "FUTURE", "2026-09-01", 2, 68.0,
           [("NOTIONAL", "USD", 136_000, "2026-12-18", 0)])
    _trade(conn, "O1", "USDJPY111926P-1", "FX_OPTION", "2026-09-01", 3_000_000, 0.01,
           [("NOTIONAL", "USD", 3_000_000, "2026-11-19", 0)])
    conn.commit()
    return conn


def _write_needed_marks(conn, as_of):
    """An official mark for every mark the header says the book needs on `as_of`, stamped
    the way the backfill stamps a close: 15:00 New York for an FX row, the settlement
    (`backfill.settle_stamp`, 17:00 New York) for a FUTURE_PX row, since 2026-09-22 a
    future's row on a past day counts as a close only at that stamp (a live press's PX_LAST
    is not a close and the backfill replaces it with PX_SETTLE)."""
    from data.bloomberg import backfill
    from ui.tabs import header
    for row in header.needed_marks(conn, as_of)[1]:
        is_future = row["mark_type"] == "FUTURE_PX"
        source = "BBG_BDH" if is_future else "BBG_BFXFORWARD"
        stamp = backfill.settle_stamp(dt.date.fromisoformat(as_of)) if is_future else None
        _mark(conn, as_of, row["instrument_id"], row["settle_date"], row["mark_type"], 1.0, source, snapped_at=stamp)
    conn.commit()


def _keyed(rows):
    return {(r["instrument_id"], r["mark_type"], r["settle_date"]): r for r in rows}


# --------------------------------------------------------------------------- panel 1: what is missing
def test_missing_rows_lists_every_unofficial_mark_with_trades_and_notional_blocked(book_is_today):
    conn = _book()
    needed, rows = md.missing_rows(conn, TODAY)
    assert needed == 8 and len(rows) == 8
    by_key = _keyed(rows)

    # sorted by trades blocked, largest first: USDJPY SPOT is read by both forwards and the option
    assert (rows[0]["instrument_id"], rows[0]["mark_type"]) == ("USDJPY", "SPOT")
    assert rows[0]["trades_blocked"] == 3 and rows[0]["notional_blocked"] == "USD 6,000,000"
    assert [r["trades_blocked"] for r in rows] == sorted((r["trades_blocked"] for r in rows), reverse=True)

    fwd = by_key[("USDJPY", "FWD_OUTRIGHT", "2026-10-15")]
    assert fwd["trades_blocked"] == 2 and fwd["notional_blocked"] == "USD 3,000,000"   # |USD leg|, gross
    assert by_key[("USDJPY", "FWD_OUTRIGHT", "2026-11-19")]["trades_blocked"] == 1     # the option's expiry
    assert by_key[("CLZ26 Comdty", "FUTURE_PX", "2026-12-18")]["notional_blocked"] == "USD 136,000"
    # a cross has no USD leg: the base amount under its own currency, never converted; its USD
    # conversion spots are blocked by the same trade
    for key in (("EURSEK", "FWD_OUTRIGHT", "2026-11-02"), ("EURSEK", "SPOT", TODAY),
                ("EURUSD", "SPOT", TODAY), ("USDSEK", "SPOT", TODAY)):
        assert by_key[key]["trades_blocked"] == 1 and by_key[key]["notional_blocked"] == "EUR 5,000,000"
    assert {r["on_file"] for r in rows} == {"nothing"}


def test_missing_rows_say_what_is_on_file_instead_and_repeat_bloombergs_reason(book_is_today):
    conn = _book()
    _mark(conn, TODAY, "USDJPY", TODAY, "SPOT", 147.5, source="MANUAL")
    conn.commit()
    status = {"connected": True, "as_of_date": TODAY, "as_of_marks": TODAY, "items": [
        {"instrument_id": "USDJPY", "mark_type": "FWD_OUTRIGHT", "settle_date": "2026-10-15",
         "status": "FAILED", "detail": "FWD_CURVE: no points"},
        "not a dict", {"instrument_id": "EURSEK"}]}
    by_key = _keyed(md.missing_rows(conn, TODAY, status)[1])
    assert by_key[("USDJPY", "SPOT", TODAY)]["on_file"] == "manual 147.5000 (not official)"
    assert by_key[("USDJPY", "FWD_OUTRIGHT", "2026-10-15")]["reason"] == "FWD_CURVE: no points"
    assert by_key[("EURUSD", "SPOT", TODAY)]["reason"] == "not requested by the last pull"
    assert md.MISSING_TABLE_ID in str(md.missing_panel(conn, TODAY, status))

    # a pull for another date: its reasons would land on the wrong date, so none is shown and the panel says why
    other = dict(status, as_of_date=PREV, as_of_marks=PREV)
    assert {r["reason"] for r in md.missing_rows(conn, TODAY, other)[1]} == {""}
    assert f"was for {PREV}, not {TODAY}" in str(md.missing_panel(conn, TODAY, other))
    assert "No Bloomberg pull is recorded yet" in str(md.missing_panel(conn, TODAY, None))
    assert "did not connect (no port)" in str(md.missing_panel(conn, TODAY, {"connected": False, "reason": "no port"}))


def test_missing_panel_is_one_sentence_when_every_needed_mark_is_official(book_is_today):
    conn = _book()
    _write_needed_marks(conn, TODAY)
    text = str(md.missing_panel(conn, TODAY))
    assert f"Every one of the 8 marks the book needs on {TODAY} is official." in text
    assert md.MISSING_TABLE_ID not in text
    assert f"needs no marks on {TODAY}" in str(md.missing_panel(schema.connect(), TODAY))


def test_missing_panel_on_a_past_date_uses_the_past_close_needs_and_the_backfill_sentence(book_is_today):
    conn = _book()
    needed, rows = md.missing_rows(conn, PREV)
    # no forward at the option's expiry on a past date: the backfill never writes it, by design
    assert needed == 7 and ("USDJPY", "FWD_OUTRIGHT", "2026-11-19") not in _keyed(rows)
    text = str(md.missing_panel(conn, PREV))
    assert f"{PREV} is a past date" in text and "backfill" in text


# --------------------------------------------------------------------------- panel 3: marks that look wrong
def _marked_book():
    conn = _book()
    for day, usdjpy, fwd in ((PREV, 145.0, 149.0), (TODAY, 150.0, 149.5)):
        _mark(conn, day, "USDJPY", day, "SPOT", usdjpy)
        _mark(conn, day, "USDJPY", "2026-10-15", "FWD_OUTRIGHT", fwd)
        _mark(conn, day, "EURUSD", day, "SPOT", 1.1)                      # exactly unchanged
        _mark(conn, day, "USDJPY", "2026-12-21", "FWD_OUTRIGHT", 148.0)   # a tenor row no open leg reads
    _mark(conn, TODAY, "EURSEK", "2026-11-02", "FWD_OUTRIGHT", 11.3)       # no mark for this date on PREV
    _mark(conn, TODAY, "CLZ26 Comdty", "2026-12-18", "FUTURE_PX", 68.56, source="BBG_BDH")
    _mark(conn, PREV, "CLZ26 Comdty", "2026-12-18", "FUTURE_PX", 68.0, source="BBG_BDH")
    conn.commit()
    return conn


def test_suspect_rows_flag_a_big_move_and_an_exactly_unchanged_mark_flagged_first():
    conn = _marked_book()
    found = md.suspect_rows(conn, TODAY, today="2026-09-25")   # a past as-of date: no snap-age check
    rows = found["rows"]
    assert found["prev_day"] == PREV and found["prev_has_marks"]
    by_key = _keyed(rows)
    assert ("USDJPY", "FWD_OUTRIGHT", "2026-12-21") not in by_key      # not read by any open leg
    assert len(rows) == 5

    assert [bool(r["flag"]) for r in rows] == [True, True, False, False, False]
    spot = rows[0]
    assert (spot["instrument_id"], spot["mark_type"]) == ("USDJPY", "SPOT")
    assert spot["change_pct"] == pytest.approx(3.448, abs=1e-3) and "moved 3.4 %, above 2.5 %" in spot["flag"]   # printed +3.45%
    assert spot["previous"] == 145.0 and spot["previous_date"] == PREV
    assert rows[1]["instrument_id"] == "EURUSD" and "exactly unchanged" in rows[1]["flag"]

    fwd = by_key[("USDJPY", "FWD_OUTRIGHT", "2026-10-15")]
    assert fwd["change_pct"] == pytest.approx(0.336, abs=1e-3) and fwd["flag"] == ""
    assert by_key[("CLZ26 Comdty", "FUTURE_PX", "2026-12-18")]["change_pct"] == pytest.approx(0.824, abs=1e-3)
    broken = by_key[("EURSEK", "FWD_OUTRIGHT", "2026-11-02")]          # never interpolated, and it says so
    assert broken["previous"] is None and broken["change_pct"] is None and broken["flag"] == ""
    assert f"no {PREV} mark for this settle date" in broken["note"] and "SPOT" in broken["note"]


def test_suspect_rows_flag_an_old_snap_only_on_the_live_date():
    from data.bloomberg.live import STALE_AFTER_SECONDS
    conn = _marked_book()
    snapped = dt.datetime.fromisoformat(f"{TODAY}T15:00:00-04:00")
    fresh = md.suspect_rows(conn, TODAY, today=TODAY, now=snapped + dt.timedelta(seconds=STALE_AFTER_SECONDS - 60))
    assert not any("snapped" in r["flag"] for r in fresh["rows"])
    old = md.suspect_rows(conn, TODAY, today=TODAY, now=snapped + dt.timedelta(seconds=STALE_AFTER_SECONDS + 60))
    assert all(f"older than {STALE_AFTER_SECONDS // 60} min" in r["flag"] for r in old["rows"])
    assert "moved 3.4 %" in old["rows"][0]["flag"]                      # a value flag still leads
    past = md.suspect_rows(conn, TODAY, today="2026-09-25", now=snapped + dt.timedelta(days=3))
    assert not any("snapped" in r["flag"] for r in past["rows"])


def test_suspect_panel_shows_flagged_rows_and_collapses_the_rest():
    conn = _marked_book()
    panel = md.suspect_panel(conn, TODAY, today="2026-09-25")
    text = str(panel)
    assert "2 of 5 marks flagged" in text and "above 2.5 %" in text
    assert md.SUSPECT_FLAGGED_TABLE_ID in text and "Show all 5 marks" in text
    details = [c for c in panel.children if type(c).__name__ == "Details"]
    assert len(details) == 1 and not getattr(details[0], "open", False)
    assert len(details[0].children[1].data) == 5


def test_suspect_panel_says_so_when_the_previous_day_has_no_marks_or_today_has_none():
    conn = _book()
    _mark(conn, TODAY, "USDJPY", TODAY, "SPOT", 150.0)
    conn.commit()
    text = str(md.suspect_panel(conn, TODAY, today="2026-09-25"))
    assert f"No official marks are on file for {PREV}, the previous business day" in text
    assert md.SUSPECT_FLAGGED_TABLE_ID not in text and "Show all 1 marks" in text
    assert "nothing to check" in str(md.suspect_panel(_book(), TODAY, today="2026-09-25"))


# --------------------------------------------------------------------------- panel 2: past closes the header needs
def test_header_reference_labels_cover_every_date_the_backfill_puts_first():
    from data.bloomberg.backfill import reference_dates
    labels = md.header_reference_labels(dt.date(2026, 9, 21))
    assert labels == {"2026-09-18": ["Daily", "Previous day"], "2026-09-17": ["Previous day"],
                      "2026-09-14": ["5d"], "2026-08-31": ["MTD"], "2025-12-31": ["YTD"]}
    assert {d.isoformat() for d in reference_dates(dt.date(2026, 9, 21))} == set(labels)


def test_past_close_rows_count_like_the_header_and_repeat_its_sentence(book_is_today):
    from ui.tabs import header
    conn = _book()   # dealt 2026-09-01: open on 09-18 / 09-17 / 09-14, not yet on 08-31 or 2025-12-31
    _write_needed_marks(conn, PREV)
    backfill = {"running": False, "days": {"2026-09-17": {"status": "NO_CLOSES"}}}
    rows = md.past_close_rows(conn, TODAY, backfill)
    assert [r["date"] for r in rows] == ["2026-09-18", "2026-09-17", "2026-09-14", "2026-08-31", "2025-12-31"]
    done, holiday, _d5, mtd, _ytd = rows
    assert (done["read_by"], done["needed"], done["present"], done["state"], done["why"]) == \
        ("Daily, Previous day", 7, 7, "complete", "")
    assert (holiday["needed"], holiday["present"], holiday["state"]) == (7, 0, "incomplete: 7 missing")
    assert holiday["why"] == header.past_close_explanation(backfill, "2026-09-17")
    assert "Bloomberg returned no closes for 2026-09-17" in holiday["why"]
    assert "7 of 7 needed marks" in header._missing_marks_reason(conn, "2026-09-17")   # the header's own count
    assert (mtd["needed"], mtd["state"]) == (0, "nothing needed")

    text = str(md.past_closes_panel(conn, TODAY, backfill))
    assert md.PAST_CLOSES_TABLE_ID in text and f"LTD of {TODAY}" in text


# --------------------------------------------------------------------------- wiring
def test_whole_book_panels_follow_the_headers_date_and_one_failure_stays_in_its_panel(book_is_today, monkeypatch):
    conn = _book()
    _missing, _suspect, past = md.whole_book_panels(conn, TODAY, None, header_as_of="2026-09-16")
    assert "LTD of 2026-09-16" in str(past)
    assert "LTD of 2026-09-21" in str(md.whole_book_panels(conn, TODAY)[2])   # no header date yet: the book date

    def _boom(*_a, **_k):
        raise ValueError("bad stored value")
    monkeypatch.setattr(md, "missing_panel", _boom)
    missing, suspect, past = md.whole_book_panels(conn, TODAY)
    assert "could not be built (ValueError: bad stored value)" in str(missing)
    assert md.SUSPECT_TITLE in str(suspect) and md.PAST_CLOSES_TABLE_ID in str(past)


def _ids(component, found):
    cid = getattr(component, "id", None)
    if isinstance(cid, str):
        found.add(cid)
    children = getattr(component, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None and not isinstance(child, (str, int, float)):
            _ids(child, found)
    return found


def test_every_input_and_output_of_this_tabs_callbacks_exists_in_the_app_layout(tmp_path):
    """The app runs with suppress_callback_exceptions: an Output missing from the layout is
    dropped without a word, and a missing Input stops the callback from ever firing."""
    import dash
    from ui import app as ui_app

    tab_ids = _ids(md.build_layout(default_date=TODAY), set())
    assert {md.MISSING_PANEL_ID, md.SUSPECT_PANEL_ID, md.PAST_CLOSES_PANEL_ID} <= tab_ids
    rendered = str(md.build_layout(default_date=TODAY))
    assert rendered.index(md.MISSING_PANEL_ID) < rendered.index(md.BODY_ID)   # above the per-pair section

    probe = dash.Dash(__name__, suppress_callback_exceptions=True)
    md.register_callbacks(probe, lambda: tmp_path / "unused.db")
    wanted = set()
    for key, spec in probe.callback_map.items():
        wanted |= {part.rsplit(".", 1)[0] for part in key.strip(".").split("...")}
        wanted |= {i["id"] for i in spec["inputs"]} | {s["id"] for s in spec["state"]}
    app_ids = _ids(ui_app.create_app(db_path=tmp_path / "layout.db").layout(), set())   # callable layout since 2026-09-22
    assert wanted <= app_ids, sorted(wanted - app_ids)


# =========================================================================== 2026-09-22: one pull action
def test_market_data_pull_now_is_the_top_bars_pull_not_a_pull_of_its_own(tmp_path, monkeypatch):
    """Both buttons ask the feed for one cycle (pull, backfill, marks snapshot). Until
    2026-09-22 this tab's button called pull_once directly, which ran neither."""
    from types import SimpleNamespace
    from ui.feed_controls import PullGuard
    called = []
    monkeypatch.setattr("data.bloomberg.live.pull_once", lambda *a, **k: called.append("pull_once") or {})
    triggered = []
    app = SimpleNamespace(bloomberg_feed=SimpleNamespace(trigger_now=lambda: triggered.append(True)))
    text, revision = md.pull_now_outcome(app, PullGuard(), lambda: tmp_path / "risk.db")
    assert triggered == [True] and called == []
    assert text == "Bloomberg: pull requested..." and revision
    # no feed on this machine: nothing asked, the not-connected message
    text, _ = md.pull_now_outcome(SimpleNamespace(bloomberg_feed=None), PullGuard(), lambda: tmp_path / "risk.db")
    assert called == [] and "not connected" in text.lower()


# =========================================================================== 2026-09-22: a press with no Bloomberg re-prices the options
# What data.bloomberg.live.pull_once writes when the press found no Bloomberg (user: "pull bbg now
# should recalc options too, using log data if no bbg access"): connected / reason as before, plus
# the recalc block and its one sentence. Neither key exists on a connected pull.
RECALC_SUMMARY = ("no Bloomberg on this machine: options re-priced from the marks on file as of 2026-09-22, "
                  "11 priced, 1 skipped over 2 day(s)")
RECALC_STATUS = {
    "time": "2026-09-22T14:32:05", "connected": False, "reason": "blpapi is not installed on this computer",
    "requested": 0, "written": 0, "failed": 0, "items": [], "timings": {"options": 3.1, "total": 3.2},
    "recalc": {"as_of": "2026-09-22", "since": "2026-09-01", "priced": 11, "skipped": 1, "days": [
        {"day": "2026-09-18", "priced": 6, "skipped": []},
        {"day": "2026-09-22", "priced": 5, "skipped": [{"trade_id": "O3", "reason": "no vol smile on file for USDJPY"}]},
    ]},
    "recalc_summary": RECALC_SUMMARY,
}
STATUS_BEFORE_THE_CHANGE = {k: v for k, v in RECALC_STATUS.items() if k not in ("recalc", "recalc_summary")}
CONNECTED_STATUS = {"connected": True, "time": "2026-09-22T14:32:05", "written": 5, "failed": 0,
                    "timings": {"options": 3.1, "total": 3.2}}


def test_not_connected_line_says_what_the_press_did_in_the_pulls_own_words_said_once():
    from types import SimpleNamespace
    from ui import feed_controls as fc
    line = fc.feed_headline(RECALC_STATUS, feed_running=True, say_on_request=False)
    assert line.startswith("Bloomberg: not connected — blpapi is not installed on this computer · options re-priced "
                           "from the marks on file as of 2026-09-22, 11 priced, 1 skipped over 2 day(s) · status as of ")
    assert "no Bloomberg on this machine" not in line and line.count("not connected") == 1   # not said twice
    # the fast poll after a press prints this very line when the cycle lands
    text, finished, landed = fc.poll_outcome(RECALC_STATUS, {"requested_at": "2026-09-22T14:32:00", "baseline": ""},
                                             feed=object())
    assert finished and landed and "11 priced, 1 skipped over 2 day(s)" in text
    # the summary whole, in its own words, for a line that has not said "not connected"
    assert fc.recalc_words(RECALC_STATUS, drop_head=False) == RECALC_SUMMARY
    assert fc.recalc_words({"recalc_summary": "  options re-priced: 3 priced "}) == "options re-priced: 3 priced"
    for absent in (None, {}, {"connected": False, "reason": "x", "recalc_summary": ""}, {"recalc_summary": 3},
                   STATUS_BEFORE_THE_CHANGE, CONNECTED_STATUS):
        assert fc.recalc_words(absent) == ""
    # without the block nothing changes, and say_recalc=False gives that same line
    before = fc.feed_headline(STATUS_BEFORE_THE_CHANGE, feed_running=True)
    assert "re-priced" not in before and "status as of" in before
    assert fc.feed_headline(RECALC_STATUS, feed_running=True, say_recalc=False) == before
    # a connected pull carries no recalc; a stray summary on one is not read into the connected line
    assert "re-priced" not in fc.feed_headline(CONNECTED_STATUS)
    assert "re-priced" not in fc.feed_headline(dict(CONNECTED_STATUS, recalc_summary=RECALC_SUMMARY))
    # no feed to wake: that press ran nothing, so the message does not claim the re-pricing
    app = SimpleNamespace(bloomberg_feed=None, bloomberg_feed_reason="the live feed is switched off (RISK_LIVE=0)")
    assert fc.not_connected_message(app, RECALC_STATUS) == \
        "Bloomberg is not connected on this machine: the live feed is switched off (RISK_LIVE=0)"
    assert fc.click_outcome(app, fc.PullGuard(), RECALC_STATUS) == (fc.not_connected_message(app, RECALC_STATUS), None)


def test_status_block_shows_the_recalc_sentence_once_and_every_day_with_each_skipped_reason():
    parts = md.status_block(RECALC_STATUS)
    assert isinstance(parts, list) and len(parts) == 3
    line, timing_row, block = parts
    assert line == md.top_bar_status(RECALC_STATUS, say_recalc=False) and "re-priced" not in line   # said in the block
    assert "not connected — blpapi is not installed" in line
    assert timing_row.id == md.PULL_TIMINGS_ID and "options 3.1 s" in timing_row.children
    assert block.id == md.RECALC_BLOCK_ID
    text = str(block)
    assert RECALC_SUMMARY in text and text.count("re-priced") == 1
    assert "2026-09-18: 6 priced, 0 skipped" in text and "2026-09-22: 5 priced, 1 skipped" in text
    assert "O3: no vol smile on file for USDJPY" in text
    assert "Re-pricing stopped" not in text
    details = next(c for c in block.children if type(c).__name__ == "Details")
    assert not getattr(details, "open", False) and details.children[0].children == "Day by day: 2 day(s)"
    days = details.children[1]
    assert days.id == md.RECALC_DAYS_ID and len(days.children) == 2
    clean, with_skip = days.children
    assert getattr(clean, "title", None) is None and len(clean.children) == 1
    assert with_skip.title == "O3: no vol smile on file for USDJPY"           # on hover ...
    assert with_skip.children[1].children[0].children == "O3: no vol smile on file for USDJPY"   # ... and listed under the day
    rows = md.recalc_day_rows(RECALC_STATUS["recalc"])
    assert [(r["day"], r["priced"], r["skipped"], r["reasons"]) for r in rows] == \
        [("2026-09-18", 6, 0, []), ("2026-09-22", 5, 1, ["O3: no vol smile on file for USDJPY"])]


def test_status_block_shows_the_recalc_error_and_reads_a_ragged_block_without_failing():
    stopped = dict(RECALC_STATUS,
                   recalc={**RECALC_STATUS["recalc"], "error": "OperationalError('database is locked')",
                           "days": [{"day": "2026-09-18", "priced": 2, "skipped": 3}, "not a dict",
                                    {"day": "2026-09-19", "priced": None, "skipped": [], "error": "ValueError('x')"},
                                    {"skipped": [{"trade_id": "O1"}, "junk"]}]},
                   recalc_summary="no Bloomberg on this machine: re-pricing the options from the marks on file stopped "
                                  "(OperationalError('database is locked')); 2 priced, 3 skipped before that")
    block = md.status_block(stopped)[-1]
    text = str(block)
    assert "Re-pricing stopped: OperationalError('database is locked')" in text
    bad = next(c for c in block.children if getattr(c, "className", "") == "status-line status-line--bad")
    assert bad.children.startswith("Re-pricing stopped")
    assert "2026-09-18: 2 priced, 3 skipped" in text
    assert "2026-09-19: 0 priced, 0 skipped · stopped: ValueError('x')" in text
    assert "?: 0 priced, 2 skipped" in text and "O1: no reason given" in text
    assert [r["day"] for r in md.recalc_day_rows(stopped["recalc"])] == ["2026-09-18", "2026-09-19", "?"]
    # an empty block still says what the status carries, and never raises
    assert md.recalc_block({"recalc": {"as_of": "2026-09-22", "days": []}}).id == md.RECALC_BLOCK_ID
    assert md.recalc_day_rows(None) == [] and md.recalc_day_rows({"days": "3"}) == []


def test_closed_out_options_are_counted_where_the_options_step_counts_are_shown():
    """2026-09-22 (user: "we dont need to price all options, as some of them might be closed
    out already"): the pricer leaves a closed-out option unpriced and the pull lists it under
    "closed_out", kept out of "skipped", with "N closed-out options not priced" appended to
    its sentence. The tab shows the count beside priced / skipped; the top bar's line
    carries the pull's sentence untouched."""
    from ui import feed_controls as fc
    # the connected pull's options block, on the tab's diagnostics panel
    lines = md._options_step_lines({"priced": 4, "skipped": [{"trade_id": "O3", "reason": "no vol smile"}],
                                    "closed_out": ["O1", "O2"]})
    assert lines == ["Options: 4 option(s) priced this cycle.", "Options: 2 closed-out options not priced.",
                     "Options O3: not priced -- no vol smile."]
    assert md._options_step_lines({"priced": 1, "skipped": [], "closed_out": ["O1"]})[1] == \
        "Options: 1 closed-out option not priced."
    # a press with no Bloomberg: the recalc block, day by day, and its sentence on the top bar
    summary = RECALC_SUMMARY + "; 2 closed-out options not priced"
    status = dict(RECALC_STATUS, recalc_summary=summary,
                  recalc={**RECALC_STATUS["recalc"], "closed_out": 2, "days": [
                      {"day": "2026-09-18", "priced": 6, "skipped": [], "closed_out": []},
                      {"day": "2026-09-22", "priced": 5, "skipped": [{"trade_id": "O3", "reason": "no vol smile on file for USDJPY"}],
                       "closed_out": ["O1", "O2"]}]})
    rows = md.recalc_day_rows(status["recalc"])
    assert [(r["day"], r["priced"], r["skipped"], r["closed_out"]) for r in rows] == \
        [("2026-09-18", 6, 0, 0), ("2026-09-22", 5, 1, 2)]
    text = str(md.status_block(status)[-1])
    assert "2026-09-18: 6 priced, 0 skipped" in text and "2026-09-18: 6 priced, 0 skipped," not in text
    assert "2026-09-22: 5 priced, 1 skipped, 2 closed-out options not priced" in text
    assert summary in text and text.count("closed-out") == 2      # the sentence once, the day once
    assert "2 closed-out options not priced" in fc.feed_headline(status, feed_running=True)
    # the count is read defensively: a list, a number, or nothing
    assert md.closed_out_count({"closed_out": ["O1"]}) == 1 and md.closed_out_count({"closed_out": 3}) == 3
    assert md.closed_out_count({"closed_out": "2"}) == 2 and md.closed_out_count({"closed_out": "junk"}) == 0
    assert md.closed_out_count({"closed_out": True}) == 0 and md.closed_out_count("not a dict") == 0
    assert md.closed_out_words(0) == ""


def test_a_status_file_without_the_closed_out_key_renders_unchanged():
    """An older status file, or a pull with nothing closed out: the key is absent or empty
    and the tab says nothing of it."""
    for block in ({"priced": 4, "skipped": []}, {"priced": 4, "skipped": [], "closed_out": []},
                  {"priced": 4, "skipped": [], "closed_out": None}):
        assert md._options_step_lines(block) == ["Options: 4 option(s) priced this cycle."]
    rows = md.recalc_day_rows(RECALC_STATUS["recalc"])
    assert [r["closed_out"] for r in rows] == [0, 0]
    text = str(md.status_block(RECALC_STATUS)[-1])
    assert "closed-out" not in text
    assert "2026-09-18: 6 priced, 0 skipped" in text and "2026-09-22: 5 priced, 1 skipped" in text
    assert md.closed_out_count(None) == 0 and md.closed_out_count({}) == 0


def test_status_block_without_a_recalc_block_renders_as_before_and_a_connected_pull_says_nothing_of_it():
    line, timing_row = md.status_block(STATUS_BEFORE_THE_CHANGE)
    assert line == md.top_bar_status(STATUS_BEFORE_THE_CHANGE) and "re-priced" not in line
    assert timing_row.id == md.PULL_TIMINGS_ID
    assert md.recalc_block(STATUS_BEFORE_THE_CHANGE) is None and md.recalc_block(None) is None
    assert md.recalc_block({"connected": False, "recalc": "not a dict"}) is None
    line, timing_row = md.status_block(CONNECTED_STATUS)
    assert line == md.top_bar_status(CONNECTED_STATUS) and "connected · last pull" in line
    rendered = str(md.status_block(CONNECTED_STATUS))
    assert "re-priced" not in rendered and "no Bloomberg" not in rendered and md.RECALC_BLOCK_ID not in rendered
    assert md.status_block({"connected": True, "time": "t", "written": 5, "failed": 0}) == \
        md.top_bar_status({"connected": True, "time": "t", "written": 5, "failed": 0})


# =========================================================================== 2026-09-22: what the ledger's re-freeze did
# What bbg-data writes on status["ledger"] (and on each backfill day's ledger block and the closing
# step's) when realise_settled drops a row frozen at a live press and freezes it again at the close.
LEDGER_STATUS = dict(CONNECTED_STATUS, ledger={
    "as_of_date": "2026-09-22", "realised": 2, "unrealisable": [],
    "refrozen": [
        {"trade_id": "F1", "product": "FX_FWD", "mark_type": "SPOT", "spot_as_of_date": "2026-09-19",
         "pnl_from": -1234.4, "pnl_to": 2000.6, "why": "the 2026-09-19 close landed"},
        "F9",                                                             # a bare string: its id alone
        {"trade_id": "O2", "product": "FX_OPTION", "pnl_from": None, "pnl_to": 15000, "why": ""},
    ],
    "kept": [{"trade_id": "F3", "product": "FX_FWD", "reason": "already frozen at the close"}, "F4"],
    "refrozen_count": 3, "refrozen_summary": "3 settled trades re-frozen at the close",
}, backfill={"running": False, "remaining": 0, "days": {"2026-09-19": {"status": "DONE", "missing_count": 0, "missing": [],
             "ledger": {"refrozen": [{"trade_id": "F1", "product": "FX_FWD", "pnl_from": -1234.4, "pnl_to": 2000.6,
                                      "why": "the 2026-09-19 close landed"}], "kept": [], "refrozen_count": 1,
                        "refrozen_summary": "1 settled trade re-frozen at the close"}}},
             "ledger": {"refrozen": [], "kept": [{"trade_id": "F5", "product": "FUTURE", "reason": "no close on file yet"}]}})


def test_status_block_shows_what_the_ledger_refroze_with_money_the_tabs_way_and_the_kept_trades():
    parts = md.status_block(LEDGER_STATUS)
    assert isinstance(parts, list) and len(parts) == 3
    line, timing_row, block = parts
    assert line == md.top_bar_status(LEDGER_STATUS) and "re-frozen" not in line     # the block says it
    assert timing_row.id == md.PULL_TIMINGS_ID and block.id == md.LEDGER_BLOCK_ID
    text = str(block)
    # the pull's own sentence, then one line per trade, money as the tab writes it elsewhere
    assert "Ledger: 3 settled trades re-frozen at the close" in text
    assert "F1 FX_FWD: USD -1,234 -> USD 2,001 (the 2026-09-19 close landed)" in text
    assert "O2 FX_OPTION: n/a -> USD 15,000" in text and "O2 FX_OPTION: n/a -> USD 15,000 (" not in text
    assert "kept: F3 FX_FWD: already frozen at the close" in text and "kept: F4" in text
    # each backfill block under its own label, the day's and the closing step's
    assert "Backfill 2026-09-19 ledger: 1 settled trade re-frozen at the close" in text
    assert "Backfill closing step: 1 settled trade(s) kept as frozen" in text
    assert "kept: F5 FUTURE: no close on file yet" in text
    # the list is collapsed, the bare-string entry is its id alone, the mark and its date on hover
    details = [c for c in block.children if type(c).__name__ == "Details"]
    assert len(details) == 3 and not any(getattr(d, "open", False) for d in details)
    first = details[0]
    assert first.children[0].children == "Re-frozen: 3 trade(s), kept: 2"
    items = first.children[1].children
    assert [li.children for li in items][:2] == ["F1 FX_FWD: USD -1,234 -> USD 2,001 (the 2026-09-19 close landed)", "F9"]
    assert items[0].title == "SPOT 2026-09-19" and getattr(items[1], "title", None) is None
    assert details[1].children[0].children == "Re-frozen: 0 trade(s), kept: 1"       # the closing step
    assert details[2].children[0].children == "Re-frozen: 1 trade(s)"                # the backfill day
    rows = md.refrozen_rows(LEDGER_STATUS["ledger"])
    assert rows["count"] == 3 and rows["summary"] == "3 settled trades re-frozen at the close"
    assert [label for label, _ in md.ledger_blocks(LEDGER_STATUS)] == ["Ledger", "Backfill closing step", "Backfill 2026-09-19 ledger"]
    # a count without a sentence gets the pull's wording; a blank never shows as zero
    assert md.refrozen_rows({"refrozen": [{"trade_id": "F1"}]})["summary"] == "1 settled trade re-frozen at the close"
    assert md.refrozen_rows({"refrozen_count": "2"})["summary"] == "2 settled trades re-frozen at the close"
    assert md.usd_words(None) == "n/a" and md.usd_words("x") == "n/a" and md.usd_words(float("nan")) == "n/a"
    assert md.usd_words(0) == "USD 0" and md.usd_words(-2.5e6) == "USD -2,500,000"


def test_a_status_file_without_the_ledger_keys_renders_exactly_as_before():
    """An older status file (status["ledger"] holds only as_of_date / realised /
    unrealisable), or a press that re-froze nothing: nothing is added."""
    plain = dict(CONNECTED_STATUS, ledger={"as_of_date": "2026-09-22", "realised": 0, "unrealisable": []})
    assert str(md.status_block(plain)) == str(md.status_block(CONNECTED_STATUS))   # components compare by repr
    assert len(md.status_block(plain)) == 2 and md.ledger_block(plain) is None
    empty = dict(plain, ledger={**plain["ledger"], "refrozen": [], "kept": [], "refrozen_count": 0, "refrozen_summary": ""})
    assert md.ledger_block(empty) is None and md.ledger_blocks(empty) == []
    for absent in (None, {}, STATUS_BEFORE_THE_CHANGE, RECALC_STATUS, {"ledger": "not a dict"},
                   {"ledger": {"error": "x"}}, {"backfill": {"ledger": "junk", "days": "junk"}}):
        assert md.ledger_block(absent) is None
    assert md.LEDGER_BLOCK_ID not in str(md.status_block(RECALC_STATUS))
    assert md.status_block({"connected": True, "time": "t", "written": 5, "failed": 0}) == \
        md.top_bar_status({"connected": True, "time": "t", "written": 5, "failed": 0})
    # a ragged block never raises and says only what it can read
    ragged = md.refrozen_rows({"refrozen": "F1", "kept": [None, {"reason": "r"}], "refrozen_count": True})
    assert ragged == {"summary": "", "count": 0, "refrozen": [], "kept": ["None", "?: r"]}


# =========================================================================== 2026-09-24: commodity conversion, Phase 2
# The macro trader's products left the app (user approval 2026-09-24): the tab no longer shows the
# rates step or the swap marks in the manual form; the pull's OIS step is status["curves"].
# Added: the futures' contract dates, the futures never asked for, and the library's gaps.
def test_the_manual_form_offers_no_swap_marks_and_says_a_manual_mark_is_never_official():
    offered = {o["value"] for o in md.MANUAL_MARK_TYPE_OPTIONS}
    assert offered == {"SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "DELTA", "PREMIUM"}
    text = str(md.manual_entry_form("EURUSD"))
    assert "never official" in text and "PAR_RATE" not in text and "DV01" not in text


def test_the_rates_step_of_the_status_file_is_not_rendered():
    status = {"connected": True, "time": "t", "written": 1, "failed": 0,
              "rates": {"currencies": {"USD": {"quotes": 12, "fixings": 30}}, "failed": [{"trade_id": "S1", "error": "x"}]},
              "options": {"priced": 2, "skipped": []}}
    text = str(md.diagnostics_panel(status, {}))
    assert "Rates" not in text and "fixing" not in text and "S1" not in text
    assert "Options: 2 option(s) priced this cycle." in text
    assert not hasattr(md, "_rates_step_lines")
    assert "Rates" not in str(md.status_block(status))
    # the old block's name, and the gone dividends block, are read by nothing
    assert "OIS curves" not in str(md.status_block(dict(status, dividends={"SPX Index": 0.013})))


def test_the_curves_block_says_in_brief_which_currencies_have_a_curve_and_why_not():
    status = dict(CONNECTED_STATUS, curves={
        "as_of_date": "2026-09-24", "bootstrapped": 2, "seconds": {"bloomberg": 2.1, "bootstrap": 0.4},
        "currencies": {"USD": {"quotes": 18, "nodes": 18, "error": ""},
                       "EUR": {"quotes": 15, "nodes": 1, "error": ""},
                       "SEK": {"quotes": 0, "nodes": 0, "error": "SEK has no OIS index in scope"},
                       "JPY": {"quotes": 9, "nodes": 0, "error": ""}}})
    parts = md.status_block(status)
    block = next(p for p in parts[1:] if getattr(p, "id", None) == md.CURVES_BLOCK_ID)
    assert block.children[0].children == "OIS curves: EUR 1 node, USD 18 nodes; 2 currencies without a curve"
    details = block.children[1]
    assert not getattr(details, "open", False) and details.children[0].children == "Without a curve: 2"
    assert [li.children for li in details.children[1].children] == [
        "JPY: 9 quote(s), no curve built", "SEK: SEK has no OIS index in scope"]
    skipped = md.curves_block({"curves": {"currencies": {}, "bootstrapped": 0, "skipped": "no FX_OPTION needs an OIS curve"}})
    assert str(skipped.children[0].children) == "OIS curves: not pulled, no FX_OPTION needs an OIS curve"
    errored = md.curves_block({"curves": {"currencies": {}, "error": "RatesBloombergSource unavailable: x"}})
    assert errored.children[0].children == "OIS curves: RatesBloombergSource unavailable: x"
    for absent in (None, {}, CONNECTED_STATUS, {"curves": "junk"}, {"curves": {"currencies": {}}}):
        assert md.curves_block(absent) is None


def test_missing_rows_link_a_non_usd_futures_conversion_spot_to_its_trades(book_is_today):
    conn = _book()
    _instrument(conn, "COZ26 Comdty", "FUTURE", "ICE:CO", "EUR", multiplier=1000, expiry="2026-10-30",
                ticker="COZ26 Comdty")
    _trade(conn, "F3", "COZ26 Comdty", "FUTURE", "2026-09-01", -4, 60.0,
           [("NOTIONAL", "EUR", -240_000, "2026-10-30", 0)])
    conn.commit()
    blocked = md.blocked_by_mark(conn, TODAY)
    assert blocked[("EURUSD", "SPOT", TODAY)]["trades"] == {"T3", "F3"}
    assert blocked[("COZ26 Comdty", "FUTURE_PX", "2026-10-30")]["notional"] == {"EUR": 240_000}
    by_key = _keyed(md.missing_rows(conn, TODAY)[1])
    spot = by_key[("EURUSD", "SPOT", TODAY)]
    assert spot["trades_blocked"] == 2 and spot["notional_blocked"] == "EUR 5,240,000"
    # a USD future reads no conversion spot
    assert not any(k[0] == "USDUSD" for k in blocked)


CONTRACT_DATES_STATUS = dict(CONNECTED_STATUS, contract_dates={
    "requested": 3, "stored": 2, "applied": {"checked": 5, "updated": ["CLZ26 Comdty"], "missing_dates": []},
    "failed": [{"ticker": "COZ6 Comdty", "reason": "Unknown/Invalid security"}, "HGZ6 Comdty", {"ticker": "NGZ6 Comdty"}],
    "summary": "2 contract dates stored, 1 future moved to Bloomberg's expiry; 3 tickers gave no date"},
    not_requestable=[
        {"instrument_id": "LAZ26 Comdty", "settle_date": "2026-12-16", "trade_ids": ["910000020", "910000021"],
         "reason": "no verified Bloomberg ticker for LME:LA"},
        {"instrument_id": "SCZ26 Comdty", "settle_date": "", "trade_ids": [], "reason": ""},
        "RBZ26 Comdty"])


def test_status_block_shows_the_contract_dates_sentence_and_the_failures_collapsed():
    parts = md.status_block(CONTRACT_DATES_STATUS)
    assert [getattr(p, "id", None) for p in parts[1:]] == [md.PULL_TIMINGS_ID, md.CONTRACT_DATES_BLOCK_ID,
                                                           md.NOT_REQUESTABLE_ID]
    block = parts[2]
    assert block.children[0].children == ("Contract dates: 2 contract dates stored, 1 future moved to Bloomberg's "
                                          "expiry; 3 tickers gave no date")
    details = block.children[1]
    assert type(details).__name__ == "Details" and not getattr(details, "open", False)
    assert details.children[0].children == "Tickers that gave no contract date: 3"
    assert [li.children for li in details.children[1].children] == [
        "COZ6 Comdty: Unknown/Invalid security", "HGZ6 Comdty", "NGZ6 Comdty: no reason given"]
    # the block's error is said, in red; nothing to say gives nothing
    errored = md.contract_dates_block({"contract_dates": {"failed": [], "summary": "", "error": "library not read"}})
    assert "Contract dates: library not read" in str(errored)
    for absent in (None, {}, CONNECTED_STATUS, {"contract_dates": "junk"},
                   {"contract_dates": {"requested": 0, "stored": 0, "failed": [], "applied": {}, "summary": ""}}):
        assert md.contract_dates_block(absent) is None
    assert md.CONTRACT_DATES_BLOCK_ID not in str(md.status_block(CONNECTED_STATUS))


def test_status_block_lists_the_futures_never_asked_for_with_their_reason_and_trades_on_hover():
    block = md.not_requestable_block(CONTRACT_DATES_STATUS)
    assert block.id == md.NOT_REQUESTABLE_ID
    assert block.children[0].children.startswith("Not asked of Bloomberg: 3 futures with no verified Bloomberg ticker")
    items = block.children[1].children
    assert items[0].children == "LAZ26 Comdty (2026-12-16, 2 trades): no verified Bloomberg ticker for LME:LA"
    assert items[0].title == "910000020, 910000021"
    assert items[1].children == "SCZ26 Comdty: no reason given" and getattr(items[1], "title", None) is None
    assert items[2].children == "RBZ26 Comdty"
    for absent in (None, {}, {"not_requestable": []}, {"not_requestable": "junk"}):
        assert md.not_requestable_block(absent) is None
    assert md.status_block(CONNECTED_STATUS)[-1].id == md.PULL_TIMINGS_ID


def _commodity_book():
    """The fixture book plus a future of a root with no verified ticker (bbg_ticker ''), as the
    contract master writes one."""
    conn = _book()
    _instrument(conn, "LAZ26 Comdty", "FUTURE", "LME:LA", "USD", multiplier=25, expiry="2026-12-16", ticker="")
    _trade(conn, "F2", "LAZ26 Comdty", "FUTURE", "2026-09-01", 3, 2600.0,
           [("NOTIONAL", "USD", 195_000, "2026-12-16", 0)])
    conn.commit()
    return conn


def test_library_lists_a_need_with_no_ticker_as_a_gap_not_as_a_ticker(book_is_today):
    conn = _commodity_book()
    asked, gaps = md.library_rows(conn, TODAY)
    assert "" not in {r["ticker"] for r in asked} and all(r["requestable"] for r in asked)
    assert gaps and {r["ticker"] for r in gaps} == {md.LIBRARY_GAP_TICKER} and all(r["flag"] == "gap" for r in gaps)
    assert any("LAZ26 Comdty" in r["used_for"] and "no verified Bloomberg ticker for LME:LA" in r["used_for"]
               for r in gaps)
    dates = [r for r in asked if r["field"] == "FUT_LAST_TRADE_DT, FUT_NOTICE_FIRST"]
    assert [r["ticker"] for r in dates] == ["CLZ26 Comdty"]
    panel = md.library_panel(conn, TODAY)
    summary = panel.children[0].children
    assert f"{len(asked)} ticker(s)" in summary and f"{len(gaps)} need(s) with no Bloomberg ticker, not asked" in summary
    table = next(c for c in panel.children if getattr(c, "id", None) == md.LIBRARY_TABLE_ID)
    assert table.data[:len(gaps)] == gaps                                        # the gaps first, flagged
    assert "gaps" in panel.children[0].title                     # the definitions on hover of the summary line


def test_contract_dates_panel_shows_on_file_and_missing_with_the_reason(book_is_today):
    from data.contracts.static import store_static_dates
    conn = _commodity_book()
    rows = md.contract_date_rows(conn, TODAY)
    by_id = {r["contract_id"]: r for r in rows}
    assert set(by_id) == {"CLZ26 Comdty", "LAZ26 Comdty"}
    assert by_id["CLZ26 Comdty"]["status"] == "missing" and by_id["CLZ26 Comdty"]["why"].startswith("not on file yet")
    la = by_id["LAZ26 Comdty"]
    assert la["status"] == "missing" and la["bbg_ticker"] == "no ticker" and "LME:LA" in la["why"]
    summary = md.contract_dates_panel(conn, TODAY).children[0].children
    assert summary == (f"Contract dates · 0 of 2 contract(s) on file on {TODAY} · 2 missing, "
                       "1 with no Bloomberg ticker to ask with")

    store_static_dates(conn, [{"contract_id": "CLZ26 Comdty", "last_trade_date": "2026-11-19",
                               "first_notice_date": "2026-11-20", "source": "BBG_BDP"}])
    rows = md.contract_date_rows(conn, TODAY)
    assert [r["contract_id"] for r in rows] == ["LAZ26 Comdty", "CLZ26 Comdty"]     # missing first
    cl = rows[1]
    assert (cl["status"], cl["last_trade_date"], cl["first_notice_date"], cl["source"], cl["why"], cl["flag"]) == \
        ("on file", "2026-11-19", "2026-11-20", "BBG_BDP", "", "")
    panel = md.contract_dates_panel(conn, TODAY)
    assert "1 of 2 contract(s) on file" in panel.children[0].children
    assert md.CONTRACT_DATES_TABLE_ID in str(panel)
    # no commodity future open: one quiet line, no table
    empty = md.contract_dates_panel(schema.connect(), TODAY)
    assert "none needed" in str(empty) and md.CONTRACT_DATES_TABLE_ID not in str(empty)
    assert type(empty).__name__ == "Div" and "section" not in (empty.className or "")
    # below the FX pair section since 2026-09-25, no longer among the health panels
    assert md.CONTRACT_DATES_TITLE in str(md.reference_panels(conn, TODAY))
    assert md.CONTRACT_DATES_TITLE not in str(md.whole_book_panels(conn, TODAY)[0])


def test_completeness_strip_hover_counts_the_needs_with_no_ticker_apart():
    import pandas as pd
    df = pd.DataFrame([
        {"as_of_date": PREV, "needed": 8, "present": 8, "complete": True, "not_requestable": [
            {"instrument_id": "LAZ26 Comdty", "settle_date": "2026-12-16", "mark_type": "FUTURE_PX", "reason": "r"}]},
        {"as_of_date": TODAY, "needed": 8, "present": 5, "complete": False, "not_requestable": []}])
    squares = md.completeness_strip(df).children
    assert squares[0].title == (f"{PREV}: 8/8 needed marks on file as official closes; 1 more with no Bloomberg ticker, "
                                "never asked for and not counted (LAZ26 Comdty)")
    assert squares[1].title == f"{TODAY}: 5/8 needed marks on file as official closes"
    older = pd.DataFrame([{"as_of_date": TODAY, "needed": 1, "present": 1, "complete": True}])   # no column at all
    assert md.completeness_strip(older).children[0].title == f"{TODAY}: 1/1 needed marks on file as official closes"


# =========================================================================== 2026-09-24: Phase 3, futures curves
def _futures_book():
    """Two commodity roots in two sectors, open on TODAY: WTI (energy) long 2 CLZ26 with a price
    today and on PREV, CLX26 and CLF27 on file with no position (CLF27 priced today only); LME
    aluminium (metals) long 3 LAZ26, whose root has no verified ticker (bbg_ticker ''), so it is
    never asked for and has no price."""
    conn = schema.connect()
    _instrument(conn, "CLZ26 Comdty", "FUTURE", "NYMEX:CL", "USD", multiplier=1000, expiry="2026-11-19",
                ticker="CLZ6 Comdty")
    _instrument(conn, "CLX26 Comdty", "FUTURE", "NYMEX:CL", "USD", multiplier=1000, expiry="2026-10-20",
                ticker="CLX6 Comdty")
    _instrument(conn, "CLF27 Comdty", "FUTURE", "NYMEX:CL", "USD", multiplier=1000, expiry="2026-12-18",
                ticker="CLF7 Comdty")
    _instrument(conn, "LAZ26 Comdty", "FUTURE", "LME:AH", "USD", multiplier=25, expiry="2026-12-16", ticker="")
    _trade(conn, "F1", "CLZ26 Comdty", "FUTURE", "2026-09-01", 2, 68.0,
           [("NOTIONAL", "USD", 136_000, "2026-11-19", 0)])
    _trade(conn, "F2", "LAZ26 Comdty", "FUTURE", "2026-09-01", 3, 2600.0,
           [("NOTIONAL", "USD", 195_000, "2026-12-16", 0)])
    _mark(conn, PREV, "CLZ26 Comdty", "2026-11-19", "FUTURE_PX", 67.5, "BBG_BDH", f"{PREV}T17:00:00-04:00")
    _mark(conn, TODAY, "CLZ26 Comdty", "2026-11-19", "FUTURE_PX", 68.25, "BBG_BDH", f"{TODAY}T14:32:05-04:00")
    _mark(conn, TODAY, "CLF27 Comdty", "2026-12-18", "FUTURE_PX", 67.9, "BBG_BDH")
    conn.commit()
    return conn


def test_futures_curves_one_block_per_root_by_sector_a_price_and_a_missing_one_with_its_reason(book_is_today):
    blocks = md.futures_curve_rows(_futures_book(), TODAY)
    assert [(b["sector"], b["root_id"]) for b in blocks] == [("energy", "NYMEX:CL"), ("metals", "LME:AH")]
    wti, alu = blocks
    assert wti["exchange"] == "NYMEX" and wti["currency"] == "USD" and wti["open"] == 1 and wti["missing"] == 1

    z = wti["rows"][0]
    assert (z["contract_id"], z["month"], z["expiry"], z["lots"]) == ("CLZ26 Comdty", "2026-12", "2026-11-19 (est.)", "+2")
    assert (z["price"], z["source"], z["snapped_at"]) == ("68.2500", "Bloomberg (BDH)", f"{TODAY} 14:32:05-04:00")
    assert z["why"] == "" and z["flag"] == ""

    la = alu["rows"][0]
    assert (la["contract_id"], la["lots"], la["price"], la["flag"]) == ("LAZ26 Comdty", "+3", md.MISSING_PRICE, "missing")
    assert la["why"] == "no verified Bloomberg ticker for LME:AH"
    assert la["change"] == "n/a" and la["previous"] == "no earlier close on file"

    # a contract no trade needs: never asked, and it says so; never blank
    x = next(r for r in wti["rows"] if r["contract_id"] == "CLX26 Comdty")
    assert x["price"] == md.MISSING_PRICE and x["lots"] == "no position"
    assert x["why"] == f"no trade on file needs it on {TODAY}, so no pull asks Bloomberg for it"


def test_futures_curves_change_is_against_the_latest_earlier_official_close(book_is_today):
    conn = _futures_book()
    z = md.futures_curve_rows(conn, TODAY)[0]["rows"][0]
    assert z["previous"] == f"67.5000 ({PREV})" and z["change"] == "+0.7500"
    f = next(r for r in md.futures_curve_rows(conn, TODAY)[0]["rows"] if r["contract_id"] == "CLF27 Comdty")
    assert f["price"] == "67.9000" and f["previous"] == "no earlier close on file" and f["change"] == "n/a"
    # a MANUAL row is shown as what is on file instead, never as the price
    _mark(conn, TODAY, "CLX26 Comdty", "2026-10-20", "FUTURE_PX", 66.0, "MANUAL")
    conn.commit()
    x = next(r for r in md.futures_curve_rows(conn, TODAY)[0]["rows"] if r["contract_id"] == "CLX26 Comdty")
    assert x["price"] == md.MISSING_PRICE and x["why"].endswith("on file instead: manual 66.0000 (not official)")
    assert md._change_words(2610.5, 2600.0) == "+10.50" and md._change_words(0.5, 0.75) == "-0.250000"


def test_futures_curves_list_open_positions_first_then_by_expiry(book_is_today):
    rows = md.futures_curve_rows(_futures_book(), TODAY)[0]["rows"]
    # CLX26 expires before CLZ26 but has no position: the open contract leads
    assert [r["contract_id"] for r in rows] == ["CLZ26 Comdty", "CLX26 Comdty", "CLF27 Comdty"]
    panel = md.futures_curves_panel(_futures_book(), TODAY)
    text = str(panel)
    assert md.FUTURES_CURVES_TITLE in text and "Energy" in text and "Metals" in text
    assert text.index("Energy") < text.index("Metals")
    assert md.FUTURES_CURVE_TABLE_ID_PREFIX + "nymex-cl" in text and "Graph" in text   # WTI has prices: a chart
    alu = next(b for b in md.futures_curve_rows(_futures_book(), TODAY) if b["root_id"] == "LME:AH")
    assert md._futures_curve_chart(alu) is None                                           # no price, no chart


def test_futures_curves_section_is_absent_with_no_commodity_future_open(book_is_today):
    fx_only = schema.connect()
    _instrument(fx_only, "USDJPY", "FX", "USD", "JPY")
    _trade(fx_only, "T1", "USDJPY", "FX_FWD", "2026-09-01", 1_000_000, 147.0,
           [("FX_NEAR", "USD", 1_000_000, "2026-10-15", 1), ("FX_NEAR", "JPY", -147_000_000, "2026-10-15", 1)])
    fx_only.commit()
    assert md.futures_curve_rows(fx_only, TODAY) == [] and md.futures_curves_panel(fx_only, TODAY) is None
    # an expired future is not open either
    assert md.futures_curves_panel(_futures_book(), "2026-12-17") is None
    assert md.FUTURES_CURVES_PANEL_ID in str(md.build_layout(default_date=TODAY))


def test_missing_rows_count_a_listed_options_conversion_spot_and_price(book_is_today):
    conn = _book()
    _instrument(conn, "SX5E 12/18/26 C5000 Index", "EQ_OPTION", "SX5E", "EUR", multiplier=10,
                expiry="2026-12-18", ticker="")
    _trade(conn, "L1", "SX5E 12/18/26 C5000 Index", "EQ_OPTION", "2026-09-01", 5, 120.0, [])
    conn.commit()
    blocked = md.blocked_by_mark(conn, TODAY)
    assert blocked[("EURUSD", "SPOT", TODAY)]["trades"] == {"T3", "L1"}
    assert blocked[("SX5E 12/18/26 C5000 Index", "FUTURE_PX", "2026-12-18")]["trades"] == {"L1"}
    by_key = _keyed(md.missing_rows(conn, TODAY)[1])
    spot = by_key[("EURUSD", "SPOT", TODAY)]
    assert spot["trades_blocked"] == 2 and spot["notional_blocked"] == "EUR 5,000,000"   # no amount made up for L1


# =========================================================================== 2026-09-24: Phase 5, LME and options on futures
LME_STATUS = dict(CONNECTED_STATUS, as_of_date=TODAY, as_of_marks=TODAY, lme={
    "roots": ["LME:CA"], "written": 3, "interp_written": 1,
    "missing": [{"root_id": "LME:CA", "ticker": "LMCADS03 Comdty", "settle_date": "2026-12-21",
                 "reason": "Unknown/Invalid security"},
                {"root_id": "LME:CA", "ticker": "", "settle_date": "2026-11-11", "reason": "beyond the last pillar"}],
    "reasons": ["LME:CA: 3M pillar not quoted"],
    "summary": "LME curves: 3 marks written for 1 metal, 1 interpolated; 2 pillars or prompts without a mark"},
    options={"priced": 4, "skipped": [], "futures_options_priced": 2,
             "futures_options_summary": "2 options on futures priced"},
    backfill={"options": {PREV: {"priced": 3, "skipped": [{"trade_id": "O9", "reason": "no vol"}], "note": "",
                                 "closed_out": [], "futures_options_priced": 1},
                          "2026-09-17": {"priced": None, "skipped": [], "note": "no option open that day"}}})


def test_status_block_shows_the_lme_line_with_its_missing_pillars_collapsed():
    block = md.lme_block(LME_STATUS)
    assert block.id == md.LME_BLOCK_ID
    assert block.children[0].children == LME_STATUS["lme"]["summary"]
    details = block.children[1]
    assert type(details).__name__ == "Details" and details.children[0].children == \
        "LME pillars or prompts without a mark: 2"
    assert [li.children for li in details.children[1].children] == [
        "LME:CA LMCADS03 Comdty 2026-12-21: Unknown/Invalid security",
        "LME:CA open prompt 2026-11-11: beyond the last pillar", "LME:CA: 3M pillar not quoted"]
    errored = md.lme_block({"lme": {"roots": [], "missing": [], "summary": "", "error": "session lost"}})
    assert "LME curves: session lost" in str(errored)
    for absent in (None, {}, CONNECTED_STATUS, {"lme": "junk"},
                   {"lme": {"roots": [], "written": 0, "interp_written": 0, "missing": [], "reasons": [], "summary": ""}}):
        assert md.lme_block(absent) is None
    ids = [getattr(p, "id", None) for p in md.status_block(LME_STATUS)[1:]]
    assert ids == [md.PULL_TIMINGS_ID, md.LME_BLOCK_ID, md.FUTURES_OPTIONS_LINE_ID, md.BACKFILL_OPTIONS_ID]


def test_status_block_shows_the_options_on_futures_line_and_the_backfills_count_per_day():
    assert md.futures_options_block(LME_STATUS).children == "Options on futures: 2 options on futures priced"
    counted = md.futures_options_block({"options": {"futures_options_priced": 1}})
    assert counted.children == "Options on futures: 1 option on futures priced"
    for absent in (None, {}, {"options": {"skipped": "no option trades to price"}},
                   {"options": {"futures_options_priced": 0}}):
        assert md.futures_options_block(absent) is None

    days = md.backfill_options_block(LME_STATUS)
    assert days.id == md.BACKFILL_OPTIONS_ID and days.children[0].children == "Past-close option pricing: 2 day(s)"
    assert [li.children for li in days.children[1].children] == [
        f"{PREV}: 3 options priced (1 on futures), 1 skipped",
        "2026-09-17: not priced (no option open that day)"]
    # a status file from before Phase 5: no futures count, nothing made up
    older = {"backfill": {"options": {PREV: {"priced": 2, "skipped": [], "note": ""}}}}
    assert md.backfill_options_block(older).children[1].children[0].children == f"{PREV}: 2 options priced, 0 skipped"
    assert md.backfill_options_block(CONNECTED_STATUS) is None


def _lme_book():
    """An LME copper book on TODAY: long 50 t (2 lots) to the October monthly prompt 2026-10-21,
    short 25 t to 2026-11-04 (between pillars). Cash, October and the 11-04 prompt (interpolated)
    are on file today, the 3M is not; cash, October and the 3M of PREV are."""
    conn = schema.connect()
    _instrument(conn, "LME:CA", "LME_FWD", "LME:CA", "USD", ticker="LMCADY Comdty")
    for trade_id, tonnes, price, prompt in (("L1", 50, 9450.0, "2026-10-21"), ("L2", -25, 9480.0, "2026-11-04")):
        _trade(conn, trade_id, "LME:CA", "LME_FWD", "2026-09-01", tonnes, price,
               [("FX_NEAR", "LME:CA", tonnes, prompt, 0), ("FX_NEAR", "USD", -tonnes * price, prompt, 1)])
    _mark(conn, TODAY, "LME:CA", TODAY, "SPOT", 9500.0, snapped_at=f"{TODAY}T11:05:00-04:00")
    _mark(conn, TODAY, "LME:CA", "2026-10-21", "FWD_OUTRIGHT", 9520.0)
    _mark(conn, TODAY, "LME:CA", "2026-11-04", "FWD_OUTRIGHT", 9530.0, "BBG_INTERP")
    _mark(conn, PREV, "LME:CA", PREV, "SPOT", 9400.0)
    _mark(conn, PREV, "LME:CA", "2026-10-21", "FWD_OUTRIGHT", 9510.0)
    _mark(conn, PREV, "LME:CA", "2026-12-18", "FWD_OUTRIGHT", 9600.0)
    conn.commit()
    return conn


def test_futures_curves_show_an_lme_metals_curve_under_metals_with_sources_and_the_missing_3m(book_is_today):
    blocks = md.futures_curve_rows(_lme_book(), TODAY, LME_STATUS)
    assert [(b["sector"], b["root_id"], b.get("lme")) for b in blocks] == [("metals", "LME:CA", True)]
    block = blocks[0]
    assert block["open"] == 2 and block["missing"] == 2
    rows = {r["pillar"]: r for r in block["rows"]}
    # by date: the November monthly pillar is asked because it brackets the 11-04 prompt
    assert [(r["pillar"], r["expiry"]) for r in block["rows"]] == [
        ("cash", "2026-09-23"), ("monthly prompt (an open prompt)", "2026-10-21"), ("open prompt", "2026-11-04"),
        ("monthly prompt", "2026-11-18"), ("3M", "2026-12-21")]
    november = rows["monthly prompt"]
    assert (november["ticker"], november["price"], november["lots"]) == ("LPX6 Comdty", md.MISSING_PRICE, "no position")
    assert november["why"] == "the last pull wrote no mark for it"

    cash = rows["cash"]
    assert (cash["ticker"], cash["expiry"], cash["lots"], cash["price"], cash["source"]) == \
        ("LMCADY Comdty", "2026-09-23", "no position", "9,500.00", "Bloomberg")
    assert cash["snapped_at"] == f"{TODAY} 11:05:00-04:00"
    assert cash["previous"] == f"9,400.00 ({PREV})" and cash["change"] == "+100.00"

    october = rows["monthly prompt (an open prompt)"]
    assert (october["ticker"], october["lots"], october["change"]) == ("LPV6 Comdty", "+2", "+10.00")
    prompt = rows["open prompt"]
    assert (prompt["expiry"], prompt["lots"], prompt["source"]) == ("2026-11-04", "-1", "Interpolated")
    assert prompt["ticker"] == "none: an open prompt, read off the curve"

    three_m = rows["3M"]
    assert (three_m["price"], three_m["flag"], three_m["why"]) == (md.MISSING_PRICE, "missing", "Unknown/Invalid security")
    assert three_m["previous"] == f"9,600.00 ({PREV})" and three_m["change"] == "n/a"   # the 3M of PREV, on its own date

    # no pull for the date: says so, never blank
    assert md.futures_curve_rows(_lme_book(), TODAY)[0]["rows"][-1]["why"] == \
        f"not pulled for {TODAY} yet: the next Pull Bloomberg now asks for it"
    panel = str(md.futures_curves_panel(_lme_book(), TODAY, LME_STATUS))
    assert "Metals" in panel and md.FUTURES_CURVE_TABLE_ID_PREFIX + "lme-lme-ca" in panel and "LME curve" in panel


def test_the_library_says_in_plain_words_what_each_need_is_for(book_is_today):
    kinds = {r["what"]: r for r in md.library_kind_rows(_lme_book(), TODAY)}
    assert {"LME curve (cash, 3M, monthlies)", "LME cash price",
            "LME prompt outright (read off the LME curve)"} <= set(kinds)
    assert kinds["LME curve (cash, 3M, monthlies)"]["items"] == 1 and kinds["LME curve (cash, 3M, monthlies)"]["trades"] == 2
    words = md.library_need_words
    assert words({"kind": "FUTURE_PX", "role": "UNDERLYING", "product": "CMDTY_OPTION"}) == "option's underlying future"
    assert words({"kind": "FUTURE_PX", "role": "PAIR", "product": "CMDTY_OPTION"}) == "option price"
    assert words({"kind": "CONTRACT_DATES", "role": "PAIR", "product": "CMDTY_OPTION"}) == "option expiry date"
    assert words({"kind": "SPOT", "role": "CONVERSION", "product": "CMDTY_OPTION"}) == "USD conversion spot"
    assert words({"kind": "OIS_CURVE", "role": "PAIR", "product": "CMDTY_OPTION"}) == "OIS discount curve (option Greeks)"
    assert md.LIBRARY_KINDS_TABLE_ID in str(md.library_panel(_lme_book(), TODAY))


def test_an_lme_curve_short_of_its_3m_is_missing_with_trades_blocked_and_in_the_strips_hover(book_is_today):
    conn = _lme_book()
    by_key = _keyed(md.missing_rows(conn, TODAY, LME_STATUS)[1])
    curve = by_key[("LME:CA", "LME_CURVE", TODAY)]
    assert curve["on_file"] == "3M not on file" and curve["trades_blocked"] == 2
    assert curve["notional_blocked"] == "USD 709,500"                        # |USD legs| 472,500 + 237,000
    assert curve["reason"] == ("LMCADS03 Comdty 2026-12-21: Unknown/Invalid security; "
                               "open prompt 2026-11-11: beyond the last pillar")
    assert md.blocked_by_mark(conn, TODAY)[("LME:CA", "FWD_OUTRIGHT", "2026-11-04")]["trades"] == {"L2"}
    import pandas as pd
    df = pd.DataFrame([{"as_of_date": PREV, "needed": 4, "present": 3, "complete": False, "not_requestable": [],
                        "missing": [{"instrument_id": "LME:CA", "settle_date": PREV, "mark_type": "LME_CURVE",
                                     "detail": "cash, 3M not on file"}]}])
    assert md.completeness_strip(df).children[0].title == (
        f"{PREV}: 3/4 needed marks on file as official closes; LME curve LME:CA: cash, 3M not on file")


def test_an_option_on_a_future_blocks_its_underlying_futures_price(book_is_today):
    conn = _book()
    _instrument(conn, "CLZ26C 70 Comdty", "CMDTY_OPTION", "NYMEX:CL", "USD", multiplier=1000, expiry="2026-11-17",
                ticker="CLZ6C 70 Comdty")
    _trade(conn, "P1", "CLZ26C 70 Comdty", "CMDTY_OPTION", "2026-09-01", 2, 1.5,
           [("NOTIONAL", "USD", 2000, "2026-11-17", 0)])
    conn.commit()
    blocked = md.blocked_by_mark(conn, TODAY)
    assert blocked[("CLZ26C 70 Comdty", "FUTURE_PX", "2026-11-17")]["trades"] == {"P1"}
    underlying = [k for k, v in blocked.items() if k[0] == "CLZ26 Comdty" and k[1] == "FUTURE_PX"]
    assert underlying and all("P1" in blocked[k]["trades"] for k in underlying)


# =========================================================================== 2026-09-25: screens redesign, Phase A
# The tab is "Data"; data health first, the futures curves before the FX pair; definitions on hover
# of each title, reasons gathered in one "Data issues (N)" drawer; a section with nothing to report
# is one quiet line. Nothing is dropped (hard rule 2).
def _walk(component):
    yield component
    children = getattr(component, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None and not isinstance(child, (str, int, float)):
            yield from _walk(child)


def test_the_tab_is_data_and_reads_health_futures_fx_library_completeness_manual_then_the_bloomberg_check():
    layout = md.build_layout(default_date=TODAY)
    title = layout.children[0]
    assert title.children[0] == "Data" and "never asks Bloomberg" in title.title      # definitions on hover
    rendered = str(layout)
    order = [md.ISSUES_ID, md.MISSING_PANEL_ID, md.SUSPECT_PANEL_ID, md.FUTURES_CURVES_PANEL_ID,
             md.PAIR_DROPDOWN_ID, md.BODY_ID, md.REFERENCE_PANEL_ID, "market-data-completeness-container",
             md.PAST_CLOSES_PANEL_ID, md.MANUAL_INSTRUMENT_ID, md.BBG_CHECK_BUTTON_ID]
    positions = [rendered.index(f"'{i}'") for i in order]
    assert positions == sorted(positions), order
    # the pair dropdown sits with the FX section, not in the top bar; the Pull now control is unchanged
    toolbar = next(c for c in _walk(layout) if getattr(c, "id", None) == md.TOOLBAR_ID)
    assert md.PAIR_DROPDOWN_ID not in str(toolbar) and md.PULL_NOW_ID in str(toolbar) and md.STATUS_ID in str(toolbar)
    # no definitions paragraph left anywhere in the static layout; the manual rule is on hover
    assert "section-kicker" not in rendered and "never official" in rendered
    assert not any(type(c).__name__ == "P" for c in _walk(md.manual_entry_form()))
    assert "FX & cash" in str(md.diagnostics_panel(None, {})) and "the ladder" not in str(md.diagnostics_panel(None, {}))


def test_sections_with_nothing_to_report_are_one_quiet_line_with_their_definitions_on_hover(book_is_today):
    conn = _book()
    _write_needed_marks(conn, TODAY)
    for panel in (md.missing_panel(conn, TODAY), md.upload_issues_panel(conn),
                  md.suspect_panel(_book(), TODAY, today="2026-09-25"),
                  md.contract_dates_panel(schema.connect(), TODAY), md.library_panel(schema.connect(), TODAY)):
        assert type(panel).__name__ == "Div" and "section" not in (panel.className or "").split(), str(panel)[:200]
        assert "quiet-line" in panel.className and "section-kicker" not in str(panel)
        heading = panel.children[0]
        assert "about-title" in heading.className and heading.title                  # definitions on hover
    assert "Notional blocked" in md.missing_panel(conn, TODAY).children[0].title
    # a panel with something to report is a card, its definitions on the title, no paragraph above the table
    card = md.missing_panel(_book(), TODAY)
    assert "section" in card.className and "section-kicker" not in str(card)
    assert "Trades blocked = open trades" in card.children[0].title


def test_the_reasons_given_on_hover_are_gathered_for_the_data_issues_drawer(book_is_today):
    issues = []
    panel = md.missing_panel(_book(), TODAY, None, issues=issues)
    note = "No Bloomberg pull is recorded yet, so there is no reason from Bloomberg to show."
    assert issues == [(md.MISSING_TITLE, note)]
    marker = next(c for c in _walk(panel) if "marker" in (getattr(c, "className", "") or ""))
    assert marker.title == note and marker.children == "no Bloomberg reason"
    # every reason blank (no pull): the reason column is left out; the marker says why
    table = next(c for c in _walk(panel) if getattr(c, "id", None) == md.MISSING_TABLE_ID)
    assert "reason" not in [col["id"] for col in table.columns]

    conn = _book()
    _mark(conn, TODAY, "USDJPY", TODAY, "SPOT", 150.0)
    conn.commit()
    md.suspect_panel(conn, TODAY, today="2026-09-25", issues=issues)
    assert issues[-1][0] == md.SUSPECT_TITLE and f"No official marks are on file for {PREV}" in issues[-1][1]

    md.futures_curves_panel(_futures_book(), TODAY, issues=issues)
    labelled = dict(issues)
    assert labelled["LAZ26 Comdty price"] == "no verified Bloomberg ticker for LME:AH"
    assert labelled["CLX26 Comdty price"] == f"no trade on file needs it on {TODAY}, so no pull asks Bloomberg for it"
    drawer = md.issues_drawer(issues)
    assert drawer.children[0].children == f"Data issues ({len(issues)})" and not drawer.open


def test_a_futures_table_shows_why_missing_only_when_a_price_is_missing_and_on_hover_of_the_price(book_is_today):
    blocks = {b["root_id"]: b for b in md.futures_curve_rows(_futures_book(), TODAY)}
    wti = md._futures_block(blocks["NYMEX:CL"])
    table = wti.children[1]
    assert "why" in [c["id"] for c in table.columns]
    rows = {r["contract_id"]: tip for r, tip in zip(table.data, table.tooltip_data)}
    assert rows["CLX26 Comdty"]["price"]["value"].startswith("no trade on file needs it") and rows["CLZ26 Comdty"] == {}
    priced = dict(blocks["NYMEX:CL"], rows=[r for r in blocks["NYMEX:CL"]["rows"] if not r["flag"]], missing=0)
    assert "why" not in [c["id"] for c in md._futures_block(priced).children[1].columns]
    # the definitions on hover of the section title, not a paragraph
    panel = md.futures_curves_panel(_futures_book(), TODAY)
    assert "section-kicker" not in str(panel) and "nothing is estimated here" in panel.children[0].title


def test_the_body_callback_fills_the_drawer_the_reference_block_and_a_quiet_futures_line(book_is_today, tmp_path):
    import dash
    path = tmp_path / "risk.db"
    for source in (_futures_book(), _book()):
        dest = sqlite3.connect(str(path))
        source.backup(dest)
        dest.close()
        probe = dash.Dash(__name__, suppress_callback_exceptions=True)
        md.register_callbacks(probe, lambda: path)
        key = next(k for k in probe.callback_map if f"{md.BODY_ID}.children" in k)
        out = probe.callback_map[key]["callback"].__wrapped__(TODAY, None, 0, None, None, None, TODAY)
        assert len(out) == 10
        futures, reference, drawer = out[7], out[8], out[9]
        assert md.LIBRARY_TITLE in str(reference) and md.CONTRACT_DATES_TITLE in str(reference)
        assert type(drawer).__name__ == "Details" and drawer.children[0].children.startswith("Data issues (")
        assert md.FUTURES_CURVES_TITLE in str(futures)
    # a book with no commodity future: the section is one quiet line, never a blank
    fx_only = schema.connect()
    _instrument(fx_only, "USDJPY", "FX", "USD", "JPY")
    _trade(fx_only, "T1", "USDJPY", "FX_FWD", "2026-09-01", 1_000_000, 147.0,
           [("FX_NEAR", "USD", 1_000_000, "2026-10-15", 1), ("FX_NEAR", "JPY", -147_000_000, "2026-10-15", 1)])
    fx_only.commit()
    path.unlink()
    dest = sqlite3.connect(str(path))
    fx_only.backup(dest)
    dest.close()
    probe = dash.Dash(__name__, suppress_callback_exceptions=True)
    md.register_callbacks(probe, lambda: path)
    key = next(k for k in probe.callback_map if f"{md.BODY_ID}.children" in k)
    futures = probe.callback_map[key]["callback"].__wrapped__(TODAY, "USDJPY", 0, None, None, None, TODAY)[7]
    assert "quiet-line" in futures.className and f"no commodity future is open on {TODAY}" in str(futures)
