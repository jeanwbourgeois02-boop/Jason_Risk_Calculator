"""ui/tabs/header.py: the P&L header shown on every tab (docs/BUILD_PLAN.md section 5).

Covers two 2026-09-17 fixes for "the headline ... doesn't work":

1. Every call into `_build_figures`/`_pnl_card` was already computing correctly on a
   database with zero official marks (Net/Gross USD delta, trade counts) -- the bug
   was that the *reason* for an unavailable P&L figure was only ever an HTML `title`
   tooltip, invisible until hovered, and generic ("today's LTD unavailable") rather
   than actionable. Fixed: a visible caption line, and a concrete "no official
   <mark_type> for <date> (<n> of <m> needed marks) -- run the Bloomberg pull"
   sentence built from `data.bloomberg.inventory.mark_inventory`.
2. Live-Bloomberg-PC follow-up: most of the book now prices, but a handful of trades
   never will (options with no strike yet, etc.), and the old `engine.pnl.ledger`
   /`scoped_period_pnl`-based aggregation "poisoned" an entire period to NaN the
   moment ANY one trade was unpriced -- so every headline card still showed nothing.
   Fixed: `_priced_single`/`_priced_diff` sum over priced trades only, with a visible
   "excludes N of M trades unpriced" caption (tooltip: breakdown by product/reason);
   `_priced_diff` additionally excludes any trade priced on only one of its two dates
   so an appearing/disappearing mark cannot fake a one-period jump.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui.tabs import header  # noqa: E402


def _vb_frame(rows):
    """rows: list of (trade_id, product, reason, pnl_usd) -> a value_book-shaped
    DataFrame carrying just the columns `_priced_single`/`_priced_diff`/
    `_unpriced_breakdown` read, for fast hand-built unit tests that do not need a
    real database or official marks."""
    return pd.DataFrame(rows, columns=["trade_id", "product", "reason", "pnl_usd"])


def _insert_instrument(conn, instrument_id, asset_class, base_ccy, quote_ccy, multiplier=1):
    conn.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,0,?,'9999-12-31')",
        (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, f"{instrument_id} Curncy"),
    )


def _insert_trade(conn, trade_id, instrument_id, product, trade_date, quantity, price):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, product, trade_id, trade_date, quantity, price,
         "acc", "cp", "HAHY7", "t", "d", ""),
    )


def _insert_legs(conn, rows):
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _insert_official_mark(conn, as_of, instrument_id, settle_date, mark_type, value, source):
    conn.execute(
        "INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
        (as_of, instrument_id, settle_date, mark_type, value, source, f"{as_of}T17:00:00-04:00"),
    )


def _db_with_one_open_fx_trade(as_of="2026-09-17"):
    """One open USDJPY spot-like FX_SPOT trade settling exactly on `as_of`, no marks --
    the minimal shape whose needed-mark set (SPOT + FWD_OUTRIGHT for USDJPY on `as_of`)
    is easy to reason about by hand."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_SPOT", as_of, 1_000_000, 147.0)
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, as_of, as_of, 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, as_of, as_of, 147.0, 1),
    ])
    conn.commit()
    return conn


# --------------------------------------------------------------------- _missing_marks_reason


def test_missing_marks_reason_is_empty_with_no_open_trades():
    conn = schema.connect()
    assert header._missing_marks_reason(conn, "2026-09-17") == ""


def test_missing_marks_reason_names_the_missing_mark_types_and_count():
    conn = _db_with_one_open_fx_trade()
    reason = header._missing_marks_reason(conn, "2026-09-17")
    assert "no official" in reason
    assert "SPOT" in reason and "FWD_OUTRIGHT" in reason
    assert "2026-09-17" in reason
    assert "2 of 2 needed marks" in reason
    assert "run the Bloomberg pull" in reason


def test_missing_marks_reason_is_empty_once_official_marks_are_written():
    conn = _db_with_one_open_fx_trade()
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 147.0, "BBG_BFXFORWARD")
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "FWD_OUTRIGHT", 147.0, "BBG_BFXFORWARD")
    conn.commit()
    assert header._missing_marks_reason(conn, "2026-09-17") == ""


def _db_with_one_open_option(trade_date="2026-09-10", expiry="2026-11-19"):
    """One open USDJPY option and nothing else: a LIVE pull asks for the pair's SPOT and a
    forward at the expiry; a PAST close needs the SPOT only (the backfill never writes that
    forward for a past date, by design)."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES ('USDJPY111926P-1', 'FX_OPTION', 'USD', 'JPY', 1, 0, '', ?)", (expiry,))
    _insert_trade(conn, "o1", "USDJPY111926P-1", "FX_OPTION", trade_date, 1_000_000, 0.01)
    _insert_legs(conn, [("o1", 1, "NOTIONAL", "USD", 1_000_000, trade_date, expiry, 0.0, 0)])
    conn.commit()
    return conn


def test_missing_marks_reason_counts_a_past_date_with_the_past_close_needs(monkeypatch):
    import datetime as dt
    from data.bloomberg import live

    conn = _db_with_one_open_option()
    day = "2026-09-17"
    # seen as TODAY: the live request list, SPOT + the forward at the expiry (unchanged behaviour)
    monkeypatch.setattr(live, "book_today", lambda: dt.date(2026, 9, 17))
    assert header.needed_marks(conn, day)[0] == 2
    assert "2 of 2 needed marks" in header._missing_marks_reason(conn, day)

    # seen as a PAST date: the past-close list, SPOT only
    monkeypatch.setattr(live, "book_today", lambda: dt.date(2026, 9, 21))
    needed, missing = header.needed_marks(conn, day)
    assert needed == 1
    assert missing == [{"instrument_id": "USDJPY", "settle_date": day, "mark_type": "SPOT"}]
    reason = header._missing_marks_reason(conn, day)
    assert "no official SPOT for 2026-09-17" in reason and "1 of 1 needed marks" in reason
    assert "FWD_OUTRIGHT" not in reason

    # once the closing SPOT is on file the past date is complete: no "1 of 2" left over for good
    _insert_official_mark(conn, day, "USDJPY", day, "SPOT", 147.0, "BBG_BFXFORWARD")
    conn.commit()
    assert header.needed_marks(conn, day) == (1, [])
    assert header._missing_marks_reason(conn, day) == ""
    monkeypatch.setattr(live, "book_today", lambda: dt.date(2026, 9, 17))
    assert "1 of 2 needed marks" in header._missing_marks_reason(conn, day)  # today still wants the forward


def test_needed_marks_keeps_the_live_list_for_a_past_date_that_has_no_close_row(monkeypatch):
    import datetime as dt
    from data.bloomberg import live

    monkeypatch.setattr(live, "book_today", lambda: dt.date(2026, 9, 21))
    conn = _db_with_one_open_option()
    assert header.needed_marks(conn, "2026-09-19")[0] == 2  # a Saturday: close_completeness has no row for it


# --------------------------------------------------------------------- _reason_tag / _unpriced_breakdown


def test_reason_tag_extracts_the_missing_mark_type():
    assert header._reason_tag("no PREMIUM mark for USDJPY111926P-1 expiry 2026-11-19 on 2026-09-17") == "no PREMIUM"
    assert header._reason_tag("no FWD_OUTRIGHT mark for USDJPY settle 2026-10-01 on 2026-09-17") == "no FWD_OUTRIGHT"
    assert header._reason_tag("no FUTURE_PX mark for ESU6 Index expiry 2026-12-18 on 2026-09-17") == "no FUTURE_PX"


def test_reason_tag_spot_conversion_and_settled_and_fallback():
    assert header._reason_tag("no SPOT for USD conversion of BRL on 2026-09-17") == "no SPOT (USD conversion)"
    assert header._reason_tag(
        "settled trade t1: no official mark on or before its settlement 2026-08-01, so it cannot be frozen"
    ) == "no historical mark at settlement"
    assert header._reason_tag("") == "unpriced"
    assert header._reason_tag("something unrecognised entirely") == "unpriced"


def test_unpriced_breakdown_groups_by_product_and_reason():
    unpriced = _vb_frame([
        ("o1", "FX_OPTION", "no PREMIUM mark for A expiry B on C", float("nan")),
        ("o2", "FX_OPTION", "no PREMIUM mark for D expiry E on F", float("nan")),
        ("f1", "FX_FWD", "no FWD_OUTRIGHT mark for G settle H on I", float("nan")),
    ])
    breakdown = header._unpriced_breakdown(unpriced)
    assert "2 options: no PREMIUM" in breakdown
    assert "1 forward: no FWD_OUTRIGHT" in breakdown


def test_unpriced_breakdown_empty_is_blank():
    assert header._unpriced_breakdown(_vb_frame([])) == ""


# --------------------------------------------------------------------- _priced_single


def test_priced_single_sums_only_priced_trades_with_a_visible_label():
    df = _vb_frame([
        ("t1", "FX_SPOT", "", 100.0),
        ("t2", "FX_OPTION", "no PREMIUM mark for X expiry Y on Z", float("nan")),
    ])
    entry = header._priced_single(df, "root reason")
    assert entry["available"] is True
    assert entry["value"] == 100.0
    assert entry["excluded_summary"] == "excludes 1 of 2 trades unpriced"
    assert "1 option: no PREMIUM" in entry["excluded_detail"]


def test_priced_single_all_unpriced_is_na_with_root_reason():
    df = _vb_frame([("t1", "FX_OPTION", "no PREMIUM mark for X expiry Y on Z", float("nan"))])
    entry = header._priced_single(df, "root reason X")
    assert entry["available"] is False
    assert entry["reason"] == "root reason X"
    assert entry["value"] != entry["value"]  # NaN
    assert entry["excluded_summary"] == ""


def test_priced_single_fully_priced_has_no_label():
    df = _vb_frame([("t1", "FX_SPOT", "", 100.0), ("t2", "FUTURE", "", 25.0)])
    entry = header._priced_single(df, "unused")
    assert entry == {"value": 125.0, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}


def test_priced_single_empty_book_is_zero_available():
    entry = header._priced_single(_vb_frame([]), "unused")
    assert entry == {"value": 0.0, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}


# --------------------------------------------------------------------- _priced_diff


def test_priced_diff_excludes_trades_unpriced_on_either_date():
    df_today = _vb_frame([
        ("both_priced", "FX_SPOT", "", 100.0),
        ("blocked", "FX_FWD", "", 50.0),      # priced today, was unpriced on the ref date -> excluded
        ("new_trade", "FUTURE", "", 30.0),    # only in today's book (traded after ref) -> included in full
        ("still_unpriced", "FX_OPTION", "no PREMIUM mark for X expiry Y on Z", float("nan")),
    ])
    df_ref = _vb_frame([
        ("both_priced", "FX_SPOT", "", 40.0),
        ("blocked", "FX_FWD", "no FWD_OUTRIGHT mark for X settle Y on Z", float("nan")),
        ("still_unpriced", "FX_OPTION", "no PREMIUM mark for X expiry Y on Z", float("nan")),
    ])
    entry = header._priced_diff(df_today, df_ref, "root reason", "2026-09-16")
    assert entry["available"] is True
    # (100 - 40) for both_priced, + 30 for new_trade (no ref-side value to subtract);
    # "blocked" and "still_unpriced" contribute nothing either way.
    assert entry["value"] == pytest.approx(90.0)
    assert entry["excluded_summary"] == "excludes 2 of 4 trades unpriced"
    assert "priced now but unpriced on 2026-09-16" in entry["excluded_detail"]
    assert "no PREMIUM" in entry["excluded_detail"]


def test_priced_diff_new_trade_since_ref_is_not_excluded():
    """A trade that simply did not exist on the reference date must NOT be treated as
    "unpriced on either date" -- it is a new position entering the book, not a pricing
    artefact, so its full value flows through the diff untouched."""
    df_today = _vb_frame([("new", "FX_SPOT", "", 77.0)])
    df_ref = _vb_frame([])
    entry = header._priced_diff(df_today, df_ref, "unused", "2026-09-16")
    assert entry["available"] is True
    assert entry["value"] == 77.0
    assert entry["excluded_summary"] == ""


def test_priced_diff_unavailable_when_nothing_contributes():
    df_today = _vb_frame([("t1", "FX_OPTION", "no PREMIUM mark for X expiry Y on Z", float("nan"))])
    entry = header._priced_diff(df_today, _vb_frame([]), "root reason Q", "2026-09-16")
    assert entry["available"] is False
    assert entry["reason"] == "root reason Q"
    assert entry["value"] != entry["value"]  # NaN


def test_priced_diff_empty_today_is_zero_available():
    entry = header._priced_diff(_vb_frame([]), _vb_frame([]), "unused", "2026-09-16")
    assert entry == {"value": 0.0, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}


def test_priced_diff_fully_priced_both_dates_has_no_label():
    df_today = _vb_frame([("t1", "FX_SPOT", "", 100.0)])
    df_ref = _vb_frame([("t1", "FX_SPOT", "", 40.0)])
    entry = header._priced_diff(df_today, df_ref, "unused", "2026-09-16")
    assert entry == {"value": 60.0, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}


# --------------------------------------------------------------------- _pnl_card


def test_pnl_card_unavailable_shows_reason_as_visible_caption_and_tooltip():
    card = header._pnl_card("X", {"available": False, "reason": "no mark"})
    assert len(card.children) == 3
    value_div, caption_div = card.children[1], card.children[2]
    # Backward-compatible with the pre-existing tests/test_ui.py assertions: the muted
    # value stays "n/a" with the reason as a `title` tooltip ...
    assert value_div.children == "n/a"
    assert value_div.title == "no mark"
    # ... but the reason is now ALSO plain text on the card, not hover-only.
    assert caption_div.children == "no mark"
    assert "header-figure-caption" in caption_div.className


def test_pnl_card_unavailable_with_blank_reason_has_no_caption():
    card = header._pnl_card("X", {"available": False, "reason": ""})
    assert len(card.children) == 2


def test_pnl_card_available_unchanged():
    card = header._pnl_card("X", {"value": 5.0, "available": True})
    assert len(card.children) == 2


def test_pnl_card_available_with_partial_pricing_shows_visible_summary_and_detail_tooltip():
    card = header._pnl_card("X", {
        "value": 100.0, "available": True,
        "excluded_summary": "excludes 1 of 2 trades unpriced",
        "excluded_detail": "1 option: no PREMIUM",
    })
    assert len(card.children) == 3
    value_div, caption_div = card.children[1], card.children[2]
    assert value_div.children == "$100"  # a real number, not "n/a" -- this is a priced figure
    assert caption_div.children == "excludes 1 of 2 trades unpriced"
    assert caption_div.title == "1 option: no PREMIUM"


# --------------------------------------------------------------------- _build_figures


def test_build_figures_trades_card_is_always_present_with_zero_marks():
    conn = _db_with_one_open_fx_trade()
    cards = header._build_figures(conn, "2026-09-17")
    titles = [c.children[0].children for c in cards if getattr(c, "className", "") != "header-divider"]
    assert "Trades" in titles
    trades_card = next(c for c in cards if getattr(c, "children", None) and
                        getattr(c.children[0], "children", None) == "Trades")
    assert trades_card.children[1].children == "1"
    assert trades_card.children[2].children == "1 open, 0 settled"


def test_build_figures_ltd_reason_is_actionable_when_marks_are_missing():
    conn = _db_with_one_open_fx_trade()
    cards = header._build_figures(conn, "2026-09-17")
    ltd_card = cards[0]
    assert ltd_card.children[0].children == "LTD"
    assert ltd_card.children[1].children == "n/a"
    reason = ltd_card.children[2].children
    assert "run the Bloomberg pull" in reason
    assert "SPOT" in reason and "FWD_OUTRIGHT" in reason


def test_build_figures_ltd_populates_once_official_marks_exist():
    """Confirms the "genuinely missing data" half of the 2026-09-17 investigation: with
    the book's marks on file the LTD figure (and every period derived from it) is a
    real computed value, not a stuck 'Unavailable' -- there is no callback bug hiding
    behind the missing-marks case."""
    conn = _db_with_one_open_fx_trade()
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 147.0, "BBG_BFXFORWARD")
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    ltd_card = cards[0]
    assert ltd_card.children[1].children != "n/a"
    # Q * (m - f) = 1,000,000 * (148 - 147) = 1,000,000 JPY; converted at USDJPY spot 147
    # -> USD pnl uses S = 1/147 per usd_per_quote's inverted-pair lookup... the exact
    # figure is engine/pnl/valuation's to define; this only pins "it is a number".
    assert "$" in ltd_card.children[1].children


def test_build_figures_never_raises_on_a_completely_empty_database():
    conn = schema.connect()
    cards = header._build_figures(conn, "2026-09-17")
    assert cards  # never an empty/blank header


def _db_with_one_priced_and_one_unpriced_trade(as_of="2026-09-17"):
    """A priced FX_SPOT trade (official marks on file) plus an FX_OPTION with no
    PREMIUM mark -- the live-Bloomberg-PC scenario: most of the book prices, a few
    trades never will yet (a strike not typed in)."""
    conn = _db_with_one_open_fx_trade(as_of)
    _insert_official_mark(conn, as_of, "USDJPY", as_of, "SPOT", 147.0, "BBG_BFXFORWARD")
    _insert_official_mark(conn, as_of, "USDJPY", as_of, "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD")
    _insert_instrument(conn, "USDJPY111926P-1", "FX_OPTION", "USD", "JPY")
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("o1", "XLSX", "USDJPY111926P-1", "FX_OPTION", "o1", as_of, 1_000_000, 0.01,
         "acc", "cp", "HAHY7", "t", "d", ""),
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        ("o1", 1, "NOTIONAL", "USD", 1_000_000, as_of, "2026-11-19", 0.0, 0),
    )
    conn.commit()
    return conn


def test_build_figures_ltd_shows_partial_sum_and_label_when_some_trades_unpriced():
    """The live-Bloomberg-PC follow-up: LTD must be a real, computed number over the
    priced trade (not "n/a") with a visible "excludes N of M" caption and a tooltip
    naming which product/reason is missing -- per-trade arithmetic is untouched, this
    only pins the new aggregation."""
    conn = _db_with_one_priced_and_one_unpriced_trade()
    cards = header._build_figures(conn, "2026-09-17")
    ltd_card = cards[0]
    assert ltd_card.children[0].children == "LTD"
    assert ltd_card.children[1].children != "n/a"
    assert "$" in ltd_card.children[1].children
    caption = ltd_card.children[2]
    assert caption.children == "excludes 1 of 2 trades unpriced"
    assert "no PREMIUM" in caption.title
    assert "option" in caption.title


# ------------------------------------------------- reference-date gap (2026-09-18)


def test_priced_diff_unavailable_when_blocked_trades_outnumber_anchored_ones():
    """First day on the Bloomberg PC: today's book prices, yesterday's has no marks, so
    every trade is "priced now but unpriced on t-1". One settled trade anchored at both
    ends used to be enough to show "$0 -- excludes 771 of 772"; the difference is now
    unavailable and the reason names the REFERENCE date and the count."""
    df_today = _vb_frame([("a", "FX_FWD", "", 10.0), ("b", "FX_FWD", "", 20.0), ("c", "FX_FWD", "", 5.0)])
    df_ref = _vb_frame([("a", "FX_FWD", "", 4.0),
                        ("b", "FX_FWD", "no FWD_OUTRIGHT mark for X settle Y on Z", float("nan")),
                        ("c", "FX_FWD", "no FWD_OUTRIGHT mark for X settle Y on Z", float("nan"))])
    entry = header._priced_diff(df_today, df_ref, "root reason", "2026-09-16",
                                lambda n_blocked, n_open: f"Daily needs the 2026-09-16 close: {n_blocked} of {n_open}")
    assert entry["available"] is False
    assert entry["reason"] == "Daily needs the 2026-09-16 close: 2 of 3"
    assert entry["excluded_summary"] == ""


def test_priced_diff_minority_blocked_keeps_partial_figure():
    df_today = _vb_frame([("a", "FX_FWD", "", 10.0), ("b", "FX_FWD", "", 20.0), ("c", "FX_FWD", "", 5.0)])
    df_ref = _vb_frame([("a", "FX_FWD", "", 4.0), ("b", "FX_FWD", "", 1.0),
                        ("c", "FX_FWD", "no FWD_OUTRIGHT mark for X settle Y on Z", float("nan"))])
    entry = header._priced_diff(df_today, df_ref, "root reason", "2026-09-16", lambda *_: "unused")
    assert entry["available"] is True
    assert entry["value"] == pytest.approx((10 - 4) + (20 - 1))
    assert entry["excluded_summary"] == "excludes 1 of 3 trades unpriced"


def test_build_figures_daily_names_reference_date_when_yesterday_has_no_marks():
    """End to end: marks for today only. Daily's caption must talk about the
    reference date (t-1) and the backfill, not about today, which is fully priced."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", "2026-09-10", 1_000_000, 147.0)  # open on t-1 and today
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, "2026-09-10", "2026-09-30", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-09-10", "2026-09-30", 147.0, 1),
    ])
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 147.0, "BBG_BFXFORWARD")
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-30", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    by_title = {c.children[0].children: c for c in cards if getattr(c, "children", None) and c.children
                and hasattr(c.children[0], "children")}
    daily = by_title["Daily"]
    assert daily.children[1].children == "n/a"
    caption = daily.children[2].children
    assert caption.startswith("Daily needs the 2026-09-16 close: 1 of 1 trades open that day")
    assert "backfill" in caption
    assert "2026-09-17" not in caption
    # an in-memory database has no status file: the "nothing known" sentence, and never an
    # instruction to run something no screen can run
    assert "needed marks) — the backfill fills past closes by itself after each Bloomberg pull" in caption
    assert "run the" not in caption and "Market data tab" not in caption


# ------------------------------------------------- why the past close is missing (2026-09-21)
# The caption ended "run the Bloomberg backfill (Market data tab)"; no screen has such a
# control (the backfill runs by itself after every feed cycle). It now ends with what the
# backfill itself published under status["backfill"]. Every key may be absent.

DAY = "2026-09-14"


def test_past_close_says_the_backfill_is_running_with_the_days_remaining():
    assert header.past_close_explanation({"running": True, "remaining": 4}, DAY) == \
        "the Bloomberg backfill is filling past closes now (4 days remaining)"
    assert header.past_close_explanation({"running": True, "remaining": 1}, DAY).endswith("now (1 day remaining)")
    assert header.past_close_explanation({"running": True}, DAY) == \
        "the Bloomberg backfill is filling past closes now"


def test_past_close_says_what_the_backfill_recorded_for_that_date():
    no_closes = {"running": False, "remaining": 0, "days": {DAY: {"status": "NO_CLOSES", "missing_count": 0,
                                                                   "missing": []}}}
    assert header.past_close_explanation(no_closes, DAY) == \
        f"Bloomberg returned no closes for {DAY} (a holiday, or no data for that date)"

    reasons = ["no forward-curve history for USDTWD", "no PX_SETTLE history for ESU6 Index on 2026-09-14",
               "2027-03-17 outside curve for EURSEK"]
    incomplete = {"days": {DAY: {"status": "INCOMPLETE", "missing_count": 37, "missing": reasons}},
                  "last_run": "2026-09-21T09:15:00"}
    text = header.past_close_explanation(incomplete, DAY)
    assert text == (f"the Bloomberg backfill reached {DAY} but could not fill 37 marks: "
                    f"{reasons[0]}; {reasons[1]} (and 35 more)")
    assert reasons[2] not in text                                    # the first reason or two, not the list
    # another date's entry says nothing about this one
    assert "none has reached this date yet" in header.past_close_explanation(incomplete, "2026-09-11")

    done = {"days": {DAY: {"status": "DONE", "missing_count": 0, "missing": []}}}
    assert "reports 2026-09-14 as filled" in header.past_close_explanation(done, DAY)
    bare = {"days": {DAY: {"status": "INCOMPLETE"}}}               # no count, no reasons: still a sentence
    assert "could not fill every mark (no reason recorded)" in header.past_close_explanation(bare, DAY)


def test_past_close_says_bloomberg_is_not_reachable_with_the_reason():
    why = "no Bloomberg API service on localhost:8194 ([WinError 10061] refused)"
    assert header.past_close_explanation({"running": False, "reason": why}, DAY) == \
        f"past closes come from Bloomberg, and the terminal is not reachable ({why})"
    # a run that raised is not called an unreachable terminal
    failed = header.past_close_explanation({"running": False, "remaining": 0,
                                            "reason": "auto-backfill failed: ValueError('x')"}, DAY)
    assert "its last run failed (auto-backfill failed: ValueError('x'))" in failed and "not reachable" not in failed
    # what was recorded for the date is still said when the terminal has since gone away
    both = header.past_close_explanation(
        {"running": False, "reason": why, "days": {DAY: {"status": "NO_CLOSES"}}}, DAY)
    assert both.startswith("Bloomberg returned no closes for 2026-09-14") and "not reachable" in both


def test_past_close_with_nothing_known_says_the_backfill_runs_by_itself():
    expected = "the backfill fills past closes by itself after each Bloomberg pull; none has reached this date yet"
    for nothing in (None, {}, {"running": False}, {"running": False, "remaining": 0, "reason": ""},
                    {"days": "not a dict"}, "not a dict"):
        assert header.past_close_explanation(nothing, DAY) == expected


def test_no_missing_close_sentence_tells_the_user_to_run_the_backfill():
    blocks = (None, {"running": True, "remaining": 2}, {"running": False, "reason": "blpapi is not installed"},
              {"days": {DAY: {"status": "INCOMPLETE", "missing_count": 1, "missing": ["no curve"]}}})
    for block in blocks:
        text = header.past_close_explanation(block, DAY)
        assert "run the" not in text and "Market data tab" not in text


def test_backfill_status_reads_the_status_file_beside_the_database(tmp_path):
    import json
    from data.bloomberg.live import status_path
    from ui.app import connect_readonly

    def block_for(db_path):
        ro = connect_readonly(db_path)
        try:
            return header.backfill_status(ro)
        finally:
            ro.close()

    assert header.backfill_status(None) == {}
    assert header.backfill_status(schema.connect()) == {}                      # in-memory: no file to read
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    assert block_for(db) == {}                                                 # no status file yet
    status_path(db).write_text(json.dumps({"connected": True, "time": "t"}), encoding="utf-8")
    assert block_for(db) == {}                                                 # a file with no backfill key
    block = {"running": True, "remaining": 3}
    status_path(db).write_text(json.dumps({"connected": True, "backfill": block}), encoding="utf-8")
    assert block_for(db) == block
    status_path(db).write_text("{ half written", encoding="utf-8")
    assert block_for(db) == {}                                                 # unreadable: nothing known, no raise


def test_reference_reason_keeps_its_head_and_the_needed_marks_detail(tmp_path):
    """The pasted 5d sentence, end to end from a status file: same head, same
    "(N of M needed marks)", and the backfill's own report where "run the Bloomberg
    backfill (Market data tab)" used to be."""
    import json
    from data.bloomberg.live import status_path
    from ui.app import connect_readonly

    db = tmp_path / "risk.db"
    conn = schema.connect(db)
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", "2026-09-01", 1_000_000, 147.0)
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, "2026-09-01", "2026-09-30", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-09-01", "2026-09-30", 147.0, 1),
    ])
    conn.commit()
    conn.close()
    status_path(db).write_text(json.dumps({"connected": True, "backfill": {
        "running": False, "remaining": 0, "last_run": "2026-09-21T09:15:00",
        "days": {DAY: {"status": "INCOMPLETE", "missing_count": 2,
                       "missing": ["no forward-curve history for USDJPY on 2026-09-14"]}}}}), encoding="utf-8")

    ro = connect_readonly(db)
    try:
        sentence = header._reference_reason(ro, DAY, "5d")(576, 577)
    finally:
        ro.close()
    assert sentence.startswith("5d needs the 2026-09-14 close: 576 of 577 trades open that day have no official "
                               "mark dated 2026-09-14 (no official ")
    assert " needed marks) — the Bloomberg backfill reached 2026-09-14 but could not fill 2 marks: " \
           "no forward-curve history for USDJPY on 2026-09-14 (and 1 more))" in sentence
    assert "run the" not in sentence and "Market data tab" not in sentence
    # a block the caller already read is used as given (one status read for all the cards)
    given = header._reference_reason(schema.connect(), DAY, "MTD", {"running": True, "remaining": 6})(3, 4)
    assert given == ("MTD needs the 2026-09-14 close: 3 of 4 trades open that day have no official mark dated "
                     "2026-09-14 — the Bloomberg backfill is filling past closes now (6 days remaining)")


def test_blotter_strip_missing_close_sentence_says_the_same_thing(tmp_path):
    """`ui/tabs/blotter_pricing.py::_reference_missing_reason` carried the same "run the
    Bloomberg backfill (Market data tab)"; it now ends with the header's explanation."""
    import json
    from data.bloomberg.live import status_path
    from ui.app import connect_readonly
    from ui.tabs import blotter_pricing

    unpriced = _vb_frame([("T2", "FX_FWD", "no FWD_OUTRIGHT mark for T2", float("nan"))])
    nothing_known = blotter_pricing._reference_missing_reason(unpriced, DAY, 2, 3)
    assert nothing_known == ("needs the 2026-09-14 close: 2 of 3 trades open that day have no official mark there "
                             "(1 forward: no FWD_OUTRIGHT) -- the backfill fills past closes by itself after each "
                             "Bloomberg pull; none has reached this date yet")

    db = tmp_path / "risk.db"
    schema.connect(db).close()
    status_path(db).write_text(json.dumps({"backfill": {"running": True, "remaining": 9}}), encoding="utf-8")
    ro = connect_readonly(db)
    try:
        running = blotter_pricing._reference_missing_reason(unpriced, DAY, 2, 3, ro)
    finally:
        ro.close()
    assert running.endswith(" -- the Bloomberg backfill is filling past closes now (9 days remaining)")
    assert "run the" not in running and "Market data tab" not in running


def test_market_data_tab_has_no_control_that_runs_the_backfill():
    """Why no sentence may send the user there to run it: the tab's only Bloomberg buttons
    are "Pull now" and "Check Bloomberg connection"."""
    from ui.tabs import market_data

    def buttons(node, found):
        if type(node).__name__ == "Button":
            found.append(str(node.children))
        children = getattr(node, "children", None)
        for child in (children if isinstance(children, (list, tuple)) else [children]):
            if hasattr(child, "children"):
                buttons(child, found)
        return found

    labels = buttons(market_data.build_layout(default_date="2026-09-21"), [])
    assert "Pull now" in labels
    assert not [label for label in labels if "backfill" in label.lower()]


def test_priced_day_sums_priced_rows_and_counts_excluded():
    df = _vb_frame([("a", "FX_FWD", "", 10.0), ("b", "FX_OPTION", "no PREMIUM mark for X expiry Y on Z", float("nan"))])
    assert header._priced_day(df) == (10.0, 1, 2)
    assert header._priced_day(_vb_frame([("b", "FX_OPTION", "no PREMIUM mark", float("nan"))])) == (None, 1, 1)
    assert header._priced_day(_vb_frame([])) == (0.0, 0, 0)


def test_build_chart_plots_partially_priced_days_with_hover_note():
    conn = _db_with_one_priced_and_one_unpriced_trade("2026-09-17")
    graph = header._build_chart(conn, "2026-09-17")
    trace = graph.figure["data"][0]
    assert trace["y"][-1] is not None  # the priced trade's sum, not a gap
    assert trace["text"][-1] == "excludes 1 of 2 trades unpriced"
    assert "%{text}" in trace["hovertemplate"]


# --------------------------------------------------------------------- one bad stored value
# 2026-09-18, Bloomberg PC: "none of the top headlines of the app work" -- one stored value
# that was not a number raised out of `value_book`, so `_build_figures` raised and the
# whole header was one "headline could not be computed (... could not convert string to
# float: '<a date>')" card, naming neither the trade nor the column.


def _card(cards, title):
    return next(c for c in cards if getattr(c, "className", "") == "header-figure"
                and c.children[0].children == title)


def _db_two_priced_forwards(as_of="2026-09-17"):
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_instrument(conn, "EURUSD", "FX", "EUR", "USD")
    _insert_trade(conn, "j1", "USDJPY", "FX_FWD", "2026-08-03", 1_000_000, 147.0)
    _insert_trade(conn, "e1", "EURUSD", "FX_FWD", "2026-08-03", 2_000_000, 1.10)
    _insert_legs(conn, [
        ("j1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-03", "2026-10-20", 147.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-08-03", "2026-10-20", 147.0, 1),
        ("e1", 1, "FX_NEAR", "EUR", 2_000_000, "2026-08-03", "2026-10-20", 1.10, 1),
        ("e1", 2, "FX_NEAR", "USD", -2_200_000, "2026-08-03", "2026-10-20", 1.10, 1),
    ])
    _insert_official_mark(conn, as_of, "USDJPY", as_of, "SPOT", 149.0, "BBG_BFXFORWARD")
    _insert_official_mark(conn, as_of, "USDJPY", "2026-10-20", "FWD_OUTRIGHT", 148.0, "BBG_INTERP")
    _insert_official_mark(conn, as_of, "EURUSD", as_of, "SPOT", 1.11, "BBG_BFXFORWARD")
    _insert_official_mark(conn, as_of, "EURUSD", "2026-10-20", "FWD_OUTRIGHT", 1.12, "BBG_BFXFORWARD")
    conn.commit()
    return conn


def test_build_figures_survives_a_text_price_and_says_which_trade_and_column():
    conn = _db_two_priced_forwards()
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 'j1'")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    ltd = _card(cards, "LTD")
    assert ltd.children[1].children == "$40,000"  # e1 alone: 2,000,000 x (1.12 - 1.10); j1 contributes nothing
    caption = ltd.children[2]
    assert caption.children == "excludes 1 of 2 trades unpriced (1 with a stored value is not a number)"
    assert "trade j1: trades.price is not a number ('24-Jul')" in caption.title
    assert _card(cards, "Trades").children[1].children == "2"


def test_build_figures_survives_the_misaligned_realised_row_of_the_2026_09_18_incident():
    """`realised_pnl.pnl_usd` holding a date (engine/pnl/ledger.py's old positional INSERT
    on a migrated table): the header used to be one error card. The settled trade is valued
    as not yet frozen and every card is a figure."""
    conn = _db_two_priced_forwards()
    _insert_trade(conn, "old", "EURUSD", "FX_FWD", "2026-06-01", 1_000_000, 1.08)
    _insert_legs(conn, [
        ("old", 1, "FX_NEAR", "EUR", 1_000_000, "2026-06-01", "2026-07-24", 1.08, 1),
        ("old", 2, "FX_NEAR", "USD", -1_080_000, "2026-06-01", "2026-07-24", 1.08, 1),
    ])
    _insert_official_mark(conn, "2026-07-24", "EURUSD", "2026-07-24", "SPOT", 1.09, "BBG_BFXFORWARD")
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
        "VALUES ('old','EURUSD','FX_FWD','USD','2026-07-24','2026-07-24',1000000,'SPOT',1080000,'SPOT','1.09',"
        "'2026-07-24','BBG_BFXFORWARD','10000.0')")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    ltd = _card(cards, "LTD")
    # j1 1,000,000/149 JPY->USD + e1 40,000 + old 1,000,000 x (1.09 - 1.08) = 10,000
    assert ltd.children[1].children == f"${1_000_000 / 149.0 + 40_000 + 10_000:,.0f}"
    assert len(ltd.children) == 2  # fully priced: no "excludes" caption


def test_a_failing_usd_delta_no_longer_takes_the_pnl_cards_with_it():
    conn = _db_two_priced_forwards()
    conn.execute("UPDATE trade_legs SET amount = '24-Jul' WHERE trade_id = 'j1' AND leg_no = 1")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    assert _card(cards, "LTD").children[1].children != "n/a"
    net = _card(cards, "Net USD delta")
    assert net.children[1].children == "n/a"  # the ladder raised on the text amount: only these two cards say so
    reason = net.children[2].children
    assert "USD delta could not be computed" in reason
    assert "trade_legs.amount" in reason and "'24-Jul'" in reason and "trade_id j1 leg_no 1" in reason


def test_failure_reason_names_table_column_row_and_value():
    conn = _db_two_priced_forwards()
    conn.execute("UPDATE marks SET value = '24-Jul' WHERE instrument_id = 'USDJPY' AND mark_type = 'SPOT'")
    conn.commit()
    text = header._failure_reason("headline could not be computed", ValueError("could not convert string to float: '24-Jul'"), conn)
    assert text.startswith("headline could not be computed (ValueError: could not convert string to float: '24-Jul')")
    assert "marks.value: 1 value that is not a number -- '24-Jul'" in text
    assert "instrument_id USDJPY" in text and "mark_type SPOT" in text
    # nothing bad on file: just the exception, no empty "Stored values" sentence
    clean = header._failure_reason("x", ValueError("y"), _db_two_priced_forwards())
    assert clean == "x (ValueError: y)"
