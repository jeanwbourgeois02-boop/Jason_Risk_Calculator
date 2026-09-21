"""Tests for ui/tabs/market_data.py (C3 Market data tab, pair-organised rewrite)."""
from __future__ import annotations

import sqlite3

import pandas as pd
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
            (mark_type IN ('PAR_RATE','PV_USD','DV01_USD') AND source='BBG_BDH') OR
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
    timings = {"session": 1.2, "spot": 3.14, "forwards": 21.4, "futures": 0.9, "rates": 8.0, "vol": 0.8,
               "options": 12.0, "ledger": 0.4, "total": 48.3}
    status = {"connected": True, "time": "t", "written": 5, "failed": 0, "timings": timings}
    assert md.pull_timings_line(status) == (
        "Last pull took 48 s: forwards 21 s · options 12 s · rates 8.0 s · spot 3.1 s · session 1.2 s · "
        "futures 0.9 s · vol 0.8 s · ledger 0.4 s")
    # a value that is not a number is left out, never shown as one; "total" alone still reads
    assert md.pull_timings_line({"timings": {"spot": 2.0, "rates": None, "vol": "n/a"}}) == "Last pull by step: spot 2.0 s"
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


def _instrument(conn, instrument_id, asset_class, base, quote, multiplier=1, expiry="9999-12-31"):
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,0,?,?)",
        (instrument_id, asset_class, base, quote, multiplier, f"{instrument_id} Curncy", expiry))


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
    """Two USDJPY forwards on one value date, a EURSEK cross, an ES future and a USDJPY option,
    all dealt 2026-09-01 and open on TODAY. No marks."""
    conn = schema.connect()
    _instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _instrument(conn, "EURSEK", "FX", "EUR", "SEK")
    # the cross's USD-conversion pairs, never traded: the pull creates these rows before it writes their SPOT
    _instrument(conn, "EURUSD", "FX", "EUR", "USD")
    _instrument(conn, "USDSEK", "FX", "USD", "SEK")
    _instrument(conn, "ESZ6 Index", "FUTURE", "ES", "USD", multiplier=50, expiry="2026-12-18")
    _instrument(conn, "USDJPY111926P-1", "FX_OPTION", "USD", "JPY", expiry="2026-11-19")
    _trade(conn, "T1", "USDJPY", "FX_FWD", "2026-09-01", 1_000_000, 147.0,
           [("FX_NEAR", "USD", 1_000_000, "2026-10-15", 1), ("FX_NEAR", "JPY", -147_000_000, "2026-10-15", 1)])
    _trade(conn, "T2", "USDJPY", "FX_FWD", "2026-09-01", -2_000_000, 147.5,
           [("FX_NEAR", "USD", -2_000_000, "2026-10-15", 1), ("FX_NEAR", "JPY", 295_000_000, "2026-10-15", 1)])
    _trade(conn, "T3", "EURSEK", "FX_FWD", "2026-09-01", 5_000_000, 11.2,
           [("FX_NEAR", "EUR", 5_000_000, "2026-11-02", 1), ("FX_NEAR", "SEK", -56_000_000, "2026-11-02", 1)])
    _trade(conn, "F1", "ESZ6 Index", "FUTURE", "2026-09-01", 2, 6000.0,
           [("NOTIONAL", "USD", 600_000, "2026-12-18", 0)])
    _trade(conn, "O1", "USDJPY111926P-1", "FX_OPTION", "2026-09-01", 3_000_000, 0.01,
           [("NOTIONAL", "USD", 3_000_000, "2026-11-19", 0)])
    conn.commit()
    return conn


def _write_needed_marks(conn, as_of):
    """An official mark for every mark the header says the book needs on `as_of`."""
    from ui.tabs import header
    for row in header.needed_marks(conn, as_of)[1]:
        source = "BBG_BDH" if row["mark_type"] == "FUTURE_PX" else "BBG_BFXFORWARD"
        _mark(conn, as_of, row["instrument_id"], row["settle_date"], row["mark_type"], 1.0, source)
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
    assert by_key[("ESZ6 Index", "FUTURE_PX", "2026-12-18")]["notional_blocked"] == "USD 600,000"
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
    _mark(conn, TODAY, "ESZ6 Index", "2026-12-18", "FUTURE_PX", 6100.0, source="BBG_BDH")
    _mark(conn, PREV, "ESZ6 Index", "2026-12-18", "FUTURE_PX", 6050.0, source="BBG_BDH")
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
    assert spot["change_pct"] == "+3.45 %" and "moved 3.4 %, above 2.5 %" in spot["flag"]
    assert spot["previous"] == "145.0000" and spot["previous_date"] == PREV
    assert rows[1]["instrument_id"] == "EURUSD" and "exactly unchanged" in rows[1]["flag"]

    fwd = by_key[("USDJPY", "FWD_OUTRIGHT", "2026-10-15")]
    assert fwd["change_pct"] == "+0.34 %" and fwd["flag"] == ""
    assert by_key[("ESZ6 Index", "FUTURE_PX", "2026-12-18")]["change_pct"] == "+0.83 %"
    broken = by_key[("EURSEK", "FWD_OUTRIGHT", "2026-11-02")]          # never interpolated, and it says so
    assert broken["previous"] == "" and broken["change_pct"] == "" and broken["flag"] == ""
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
    app_ids = _ids(ui_app.create_app(db_path=tmp_path / "layout.db").layout, set())
    assert wanted <= app_ids, sorted(wanted - app_ids)
